#!/usr/bin/env python3
"""Build sector_trend — 板块 5/10/20日涨跌幅 + 排名 + 方向 + 生命周期 (Phase 26c).

数据源: sw_sector_index (Phase 26a).
用法:
  PYTHONPATH=. python scripts/build_sector_trend.py         # 全量
  PYTHONPATH=. python scripts/build_sector_trend.py --today  # 仅今天
"""
import asyncio, logging, sys, numpy as np
from datetime import date, timedelta
from app.core.database import async_session_factory
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("sector_trend")


def _slope(series: list) -> float:
    """Linear regression slope on 1..n → return slope per index step."""
    n = len(series)
    if n < 3: return 0.0
    x = np.arange(n, dtype=np.float64)
    y = np.array(series, dtype=np.float64)
    mask = np.isfinite(y)
    if mask.sum() < 3: return 0.0
    try:
        A = np.vstack([x[mask], np.ones(mask.sum())]).T
        m, _ = np.linalg.lstsq(A, y[mask], rcond=None)[0]
        return float(m)
    except Exception:
        return 0.0


def _direction(slope: float, close: float) -> str:
    """10-day slope relative to price → direction."""
    if close <= 0: return "震荡"
    rel = slope / close * 100
    if rel > 0.3: return "上升"
    elif rel < -0.3: return "下降"
    return "震荡"


def _lifecycle(p5: float|None, p10: float|None, p20: float|None, vr: float|None) -> str:
    """量价关系 → lifecycle (relaxed thresholds for A-share distribution)."""
    if any(v is None for v in [p5, p10, p20, vr]): return "正常"
    if p5 > 3 and vr > 2.0: return "高潮"
    if p5 < -2 and vr > 1.5 and (p20 or 0) > 0: return "分化"
    if (p10 or 0) > 2 and p5 > (p10 or 0) * 0.3 and vr > 1.5: return "发酵"
    if (p20 or 0) > 1 and vr > 1.2 and p5 < (p20 or 0): return "萌芽"
    if (p10 or 0) < -1 and vr < 0.8: return "退潮"
    return "正常"


async def build_today():
    """Incremental: only compute latest trading day."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(trade_date) FROM sw_sector_index"))
        latest = r.scalar()
        if not latest: return
        await _build_dates(s, [latest])


async def build_full():
    """Full rebuild: all dates from 2020-01-01."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM sw_sector_index "
            "WHERE trade_date >= '2020-01-01' ORDER BY trade_date"))
        dates = [row[0] for row in r.fetchall()]
    if not dates:
        logger.error("No dates in sw_sector_index"); return
    logger.info(f"Building sector_trend for {len(dates)} dates ({dates[0]} ~ {dates[-1]})")
    batch_size = 120
    total = 0
    for i in range(0, len(dates), batch_size):
        batch = dates[i:i+batch_size]
        async with async_session_factory() as s:
            n = await _build_dates(s, batch)
        total += n
        logger.info(f"  [{i+len(batch)}/{len(dates)}] +{n} rows")
    logger.info(f"Done: {total} rows total")


async def _build_dates(session, target_dates: list) -> int:
    """Build trend for specific dates — two-pass: compute ranks first, then insert."""
    if not target_dates: return 0

    LOOKBACK_DAYS = 45  # ~32 trading days for 20d lookback
    dmin = min(target_dates) - timedelta(days=LOOKBACK_DAYS + 10)
    dmax = max(target_dates)

    r = await session.execute(text("""
        SELECT index_code, trade_date, close, vol
        FROM sw_sector_index
        WHERE trade_date BETWEEN :d1 AND :d2
        ORDER BY index_code, trade_date
    """), {"d1": dmin, "d2": dmax})
    raw_rows = r.fetchall()

    # Build per-sector sorted list of (date, close, vol)
    sector_ts: dict[str, list] = {}
    for row in raw_rows:
        code = row[0]; td = row[1]; cl = float(row[2] or 0); vl = float(row[3] or 0)
        sector_ts.setdefault(code, []).append((td, cl, vl))

    inserted = 0
    for td in target_dates:
        # ── Pass 1: compute all metrics, collect for ranking ──
        day_rows: list[dict] = []  # [{code, pct_5d, pct_10d, pct_20d, vol_ratio, direction, lifecycle}, ...]

        for code, rows in sector_ts.items():
            # Find index of td in this sector's rows
            t_idx = None
            for j, (d, _, _) in enumerate(rows):
                if d == td:
                    t_idx = j
                    break
            if t_idx is None:
                continue

            close_now = rows[t_idx][1]
            if close_now <= 0:
                continue

            # Extract closes by trading-day offset (rows[t_idx] is today, rows[t_idx-5] is 5 days ago)
            def _close(offset_days: int):
                idx = t_idx - offset_days
                if idx < 0: return np.nan
                return rows[idx][1]

            pct_5d  = round((close_now - _close(5))  / _close(5)  * 100, 2) if _close(5)  > 0 else None
            pct_10d = round((close_now - _close(10)) / _close(10) * 100, 2) if _close(10) > 0 else None
            pct_20d = round((close_now - _close(20)) / _close(20) * 100, 2) if _close(20) > 0 else None

            # vol_ratio: avg vol of last 5d / avg vol of last 20d (trading days, not calendar)
            vols = [rows[j][2] for j in range(max(0, t_idx-20), t_idx) if rows[j][2] > 0]
            v5 = np.mean(vols[-5:]) if len(vols) >= 5 else 0.0
            v20 = np.mean(vols) if len(vols) >= 10 else 0.0
            vol_ratio = round(float(v5 / v20), 3) if v20 > 0 else None

            # direction: slope of last 10 trading closes
            recent_closes = [rows[j][1] for j in range(max(0, t_idx-10), t_idx+1) if rows[j][1] > 0]
            dir_slope = _slope(recent_closes) if len(recent_closes) >= 5 else 0.0
            direction = _direction(dir_slope, close_now)

            lifecycle = _lifecycle(pct_5d, pct_10d, pct_20d, vol_ratio)

            day_rows.append({
                "code": code, "pct_5d": pct_5d, "pct_10d": pct_10d,
                "pct_20d": pct_20d, "vol_ratio": vol_ratio,
                "direction": direction, "lifecycle": lifecycle,
            })

        if not day_rows:
            continue

        # ── Pass 2: compute ranks ──
        for col_key, rank_key in [("pct_5d", "rank_5d"), ("pct_20d", "rank_20d")]:
            valid = [(i, d[col_key]) for i, d in enumerate(day_rows) if d[col_key] is not None]
            valid.sort(key=lambda x: -x[1])  # descending
            for rank, (i, _) in enumerate(valid, 1):
                day_rows[i][rank_key] = rank

        # ── Pass 3: batch INSERT with ranks ──
        for d in day_rows:
            await session.execute(text("""
                INSERT INTO sector_trend
                    (sector_code, trade_date, pct_5d, pct_10d, pct_20d,
                     rank_5d, rank_20d, direction, lifecycle, vol_ratio)
                VALUES (:c, :d, :p5, :p10, :p20, :r5, :r20, :dir, :lc, :vr)
                ON CONFLICT (sector_code, trade_date) DO UPDATE SET
                    pct_5d=EXCLUDED.pct_5d, pct_10d=EXCLUDED.pct_10d,
                    pct_20d=EXCLUDED.pct_20d, rank_5d=EXCLUDED.rank_5d,
                    rank_20d=EXCLUDED.rank_20d, direction=EXCLUDED.direction,
                    lifecycle=EXCLUDED.lifecycle, vol_ratio=EXCLUDED.vol_ratio
            """), {
                "c": d["code"], "d": td,
                "p5": d["pct_5d"], "p10": d["pct_10d"], "p20": d["pct_20d"],
                "r5": d.get("rank_5d"), "r20": d.get("rank_20d"),
                "dir": d["direction"], "lc": d["lifecycle"], "vr": d["vol_ratio"],
            })
            inserted += 1

        await session.commit()

    return inserted


async def main():
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS sector_trend (
                sector_code VARCHAR(20) NOT NULL, trade_date DATE NOT NULL,
                pct_5d FLOAT, pct_10d FLOAT, pct_20d FLOAT,
                rank_5d INT, rank_20d INT,
                direction VARCHAR(8), lifecycle VARCHAR(8), vol_ratio FLOAT,
                PRIMARY KEY (sector_code, trade_date)
            )"""))
        await s.commit()
    logger.info("Table sector_trend ready")

    if "--today" in sys.argv:
        await build_today()
    else:
        await build_full()


if __name__ == "__main__":
    asyncio.run(main())
