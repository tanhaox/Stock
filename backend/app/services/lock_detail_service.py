"""Lock detail service — full lock cycle analysis + minute levels + T-mode (v4.4)."""
import numpy as np
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

AMP_LOCK_SHORT = 15.0   # short-window lock threshold
AMP_LOCK_LONG = 17.0    # long-window lock threshold


def _detect_lock_cycles(highs: np.ndarray, lows: np.ndarray) -> list[dict]:
    """Sliding-window detection: find all historical lock cycles.

    Each cycle = contiguous period where short(15-20d amp <= 15%) AND long(20-40d amp <= 17%).
    Returns list sorted by end-index (oldest → newest).
    """
    n = len(highs)
    if n < 30:
        return []

    cycles = []
    in_lock = False
    lock_start = 0

    for i in range(29, n):  # scan from bar 30 onward
        # short-window amp
        s15_end = i
        s15_start = max(0, i - 15)
        h15, l15 = float(np.max(highs[s15_start:s15_end + 1])), float(np.min(lows[s15_start:s15_end + 1]))
        amp15 = (h15 - l15) / l15 * 100 if l15 > 0 else 100

        s20_start = max(0, i - 20)
        h20s, l20s = float(np.max(highs[s20_start:s15_end + 1])), float(np.min(lows[s20_start:s15_end + 1]))
        amp20s = (h20s - l20s) / l20s * 100 if l20s > 0 else 100

        # long-window amp
        l40_start = max(0, i - 40)
        h40, l40 = float(np.max(highs[l40_start:s15_end + 1])), float(np.min(lows[l40_start:s15_end + 1]))
        amp40 = (h40 - l40) / l40 * 100 if l40 > 0 else 100

        l20_start = max(0, i - 20)
        h20l, l20l = float(np.max(highs[l20_start:s15_end + 1])), float(np.min(lows[l20_start:s15_end + 1]))
        amp20l = (h20l - l20l) / l20l * 100 if l20l > 0 else 100

        is_lock = (amp15 <= AMP_LOCK_SHORT and amp20s <= AMP_LOCK_SHORT
                   and amp40 <= AMP_LOCK_LONG and amp20l <= AMP_LOCK_LONG)

        if is_lock and not in_lock:
            in_lock = True
            lock_start = i - 14  # approximate start
        elif not is_lock and in_lock:
            in_lock = False
            end_i = i - 1
            start_i = lock_start
            if end_i - start_i >= 5:  # at least 5 days
                h = float(max(highs[start_i:end_i + 1]))
                l = float(min(lows[start_i:end_i + 1]))
                # Find post-breakout rally: scan up to 40 bars after lock end
                rally_peak = l
                rally_days = 0
                for j in range(end_i + 1, min(n, end_i + 41)):
                    if highs[j] > rally_peak:
                        rally_peak = highs[j]
                        rally_days = j - end_i
                    if rally_peak > l and highs[j] < rally_peak * 0.85:
                        break
                rally_pct = round((rally_peak - l) / l * 100, 1) if l > 0 else 0
                cycles.append({
                    "n": len(cycles) + 1,
                    "low": round(l, 2),
                    "high": round(h, 2),
                    "days": end_i - start_i + 1,
                    "amp": round((h - l) / l * 100, 1) if l > 0 else 0,
                    "breakout_pct": rally_pct,
                    "breakout_days": rally_days,
                })

    # current ongoing lock
    if in_lock:
        start_i = lock_start
        end_i = n - 1
        if end_i - start_i >= 5:
            h = float(np.max(highs[start_i:end_i + 1]))
            l = float(np.min(lows[start_i:end_i + 1]))
            cycles.append({
                "n": len(cycles) + 1,
                "low": round(l, 2),
                "high": round(h, 2),
                "days": end_i - start_i + 1,
                "amp": round((h - l) / l * 100, 1) if l > 0 else 0,
            })

    return cycles


def _compute_amp_trend(highs: np.ndarray, lows: np.ndarray) -> dict:
    """Compute recent amplitude trend (last 15d vs previous 15d)."""
    n = len(highs)
    if n < 30:
        return {"recent_amp": 0, "converging": False}

    # recent 15d
    h15 = float(np.max(highs[-15:]))
    l15 = float(np.min(lows[-15:]))
    recent_amp = round((h15 - l15) / l15 * 100, 1) if l15 > 0 else 0

    # previous 15d (bar -30 to -15)
    h30 = float(np.max(highs[-30:-15]))
    l30 = float(np.min(lows[-30:-15]))
    prev_amp = round((h30 - l30) / l30 * 100, 1) if l30 > 0 else 0

    converging = recent_amp < prev_amp and recent_amp < 20

    return {"recent_amp": recent_amp, "converging": converging}


async def get_full_lock_detail(symbol: str) -> dict:
    """Full lock detail: cycles + amp trend + minute levels + volume ref + T-mode."""
    from app.services.lock_detector import detect_lock_simple

    code = symbol.strip().upper()
    result = {"symbol": code}

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, close, high, low, volume, amount FROM daily_kline "
            "WHERE ts_code = :c ORDER BY trade_date"
        ), {"c": code})
        rows = list(r.fetchall())

    if len(rows) < 100:
        result["error"] = "data insufficient"
        return result

    cs = np.array([float(rw[1] or 0) for rw in rows])
    hs = np.array([float(rw[2] or cs[i]) for i, rw in enumerate(rows)])
    ls = np.array([float(rw[3] or cs[i]) for i, rw in enumerate(rows)])
    vs = np.array([float(rw[4] or 0) for rw in rows])
    dates = [rw[0] for rw in rows]
    n_total = len(cs)

    n = len(cs)
    if n < 30:
        result["error"] = "data insufficient (post-ex-rights)"
        return result

    # ── Index data ──
    idx_code = '399006.SZ' if (code.startswith('300') or code.startswith('301') or code.startswith('688')) else '700001.TI'
    idx_cs = np.zeros(n)
    try:
        async with async_session_factory() as s2:
            r2 = await s2.execute(text(
                "SELECT close FROM daily_kline WHERE ts_code = :c ORDER BY trade_date"
            ), {"c": idx_code})
            idx_rows = [float(rw[0] or 0) for rw in r2.fetchall()]
        if len(idx_rows) >= n:
            idx_cs = np.array(idx_rows[-n:])
    except Exception:
        pass

    # ── Basic lock detection (current state) ──
    lock = detect_lock_simple(cs, hs, ls, idx_cs)
    result.update({
        "in_lock": bool(lock["in_lock"]),
        "amplitude_30d": lock.get("amplitude_30d", 0),
        "lock_days": lock.get("lock_days", 0),
        "market_return": lock.get("market_return", 0.0),
        "relative_strength": lock.get("relative_strength", 0.0),
        "verdict": lock.get("verdict", ""),
        "current_price": round(float(cs[-1]), 2),
    })

    # ── Lock cycles (historical) ──
    lock_cycles = _detect_lock_cycles(hs, ls)
    result["lock_cycles"] = lock_cycles
    if lock_cycles:
        current = lock_cycles[-1]
        result["current_cycle"] = current
    else:
        result["current_cycle"] = None

    # ── Amp trend ──
    result["amp_trend"] = _compute_amp_trend(hs, ls)

    # ── Minute levels (from min_kline) ──
    try:
        async with async_session_factory() as s3:
            r3 = await s3.execute(text(
                "SELECT low, high, amount, volume FROM min_kline "
                "WHERE ts_code = :c AND trade_date >= :cut "
                "ORDER BY trade_date, trade_time"
            ), {"c": code, "cut": dates[-1] - timedelta(days=30) if dates else date.today() - timedelta(days=30)})
            min_rows = r3.fetchall()
        if min_rows and len(min_rows) >= 50:
            m_lows = [float(rw[0] or 0) for rw in min_rows]
            m_highs = [float(rw[1] or 0) for rw in min_rows]
            amounts_vol = [(float(rw[2] or 0), float(rw[3] or 0)) for rw in min_rows]

            floor = float(np.percentile(m_lows, 5))
            ceiling = float(np.percentile(m_highs, 95))
            grid_width = round((ceiling - floor) / floor * 100, 2) if floor > 0 else 0

            # VWAP from last 5 trading days' minute data
            total_amt = sum(a for a, _ in amounts_vol[-240 * 5:])
            total_vol = sum(v for _, v in amounts_vol[-240 * 5:])
            vwap = round(total_amt / total_vol, 2) if total_vol > 0 else round(float(cs[-1]), 2)

            result["minute_levels"] = {
                "floor": round(floor, 2),
                "ceiling": round(ceiling, 2),
                "vwap": vwap,
                "grid_width": f"{grid_width}%",
            }
        else:
            result["minute_levels"] = None
    except Exception as e:
        logger.debug(f"Minute levels unavailable for {code}: {e}")
        result["minute_levels"] = None

    # ── Volume trend (enhanced with reference) ──
    if n >= 60:
        vol_first = float(np.mean(vs[-60:-30]))
        vol_second = float(np.mean(vs[-30:]))
        vol_trend_pct = (vol_second / vol_first - 1) * 100 if vol_first > 0 else 0

        # Build historical volume cycle reference
        vol_windows = []
        for i in range(60, n - 30, 10):
            v1 = float(np.mean(vs[i - 60:i - 30]))
            v2 = float(np.mean(vs[i - 30:i]))
            vol_windows.append((v2 / v1 - 1) * 100 if v1 > 0 else 0)
        vol_windows.sort()

        ref = {}
        if vol_windows:
            ref["cycles_count"] = len(vol_windows)
            ref["min"] = round(min(vol_windows), 1)
            ref["max"] = round(max(vol_windows), 1)
            ref["median"] = round(float(np.median(vol_windows)), 1)

            # percentile of current volume change
            below = sum(1 for v in vol_windows if v < vol_trend_pct)
            ref["current_percentile"] = round(below / len(vol_windows) * 100, 0)

            if vol_trend_pct < -40:
                ref["current_label"] = "极度萎缩"
            elif vol_trend_pct < -20:
                ref["current_label"] = "显著萎缩"
            elif vol_trend_pct < -5:
                ref["current_label"] = "温和萎缩"
            elif vol_trend_pct < 10:
                ref["current_label"] = "正常"
            elif vol_trend_pct < 30:
                ref["current_label"] = "温和放量"
            elif vol_trend_pct < 60:
                ref["current_label"] = "显著放量"
            else:
                ref["current_label"] = "异常放量"

        result["volume_trend"] = {
            "first_half": round(vol_first, 0),
            "second_half": round(vol_second, 0),
            "change_pct": round(vol_trend_pct, 1),
            "verdict": "shrinking" if vol_trend_pct < -10 else ("expanding" if vol_trend_pct > 10 else "stable"),
            "reference": ref,
        }

    # ── T-mode suggestion ──
    amp = result.get("amp_trend", {}).get("recent_amp", 0)
    in_lock = result.get("in_lock", False)
    converging = result.get("amp_trend", {}).get("converging", False)
    vol_change = result.get("volume_trend", {}).get("change_pct", 0) if result.get("volume_trend") else 0

    t_suitable = in_lock and amp > 0 and amp < 20 and converging and vol_change < 10
    t_grid_count = max(2, min(8, int(amp / 3))) if amp > 0 else 3
    t_grid_step = round(amp / t_grid_count, 1) if amp > 0 else 0

    result["t_mode"] = {
        "suitable": t_suitable,
        "grid_count": t_grid_count,
        "grid_step": t_grid_step,
    }

    return result
