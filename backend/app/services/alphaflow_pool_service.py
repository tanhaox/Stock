"""AlphaFlow pool management service — goose detection + query + assembly (v4.3)."""
import numpy as np
import logging
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow_pool_service")

TIMING_FACTORS = {
    "趋势上涨": 1.15, "结构行情": 1.08, "震荡整理": 1.00,
    "维稳行情": 0.95, "缩量博弈": 0.85, "弱势探底": 0.72, "恐慌杀跌": 0.60,
}
_INDEX_NAMES = {
    "000001.SH": "上证指数", "000300.SH": "沪深300", "000016.SH": "上证50",
    "000905.SH": "中证500", "000852.SH": "中证1000", "399001.SZ": "深证成指",
    "399006.SZ": "创业板指", "399005.SZ": "中小100",
}


def _timing_note(regime: str, factor: float) -> str:
    if factor >= 1.1: return f"{regime} — timing favorable, weight x{factor}"
    elif factor >= 0.95: return f"{regime} — timing neutral, weight x{factor}"
    elif factor >= 0.75: return f"{regime} — timing weak, weight x{factor}"
    else: return f"{regime} — timing strict, weight x{factor}"


async def get_pool_with_maintenance(tier_filter: str = "", limit: int = 50, fast: bool = True) -> dict:
    """Full pool pipeline: goose detection + query + name lookup + timing scoring + structure maintenance.

    Args:
        fast: If True (default), skip goose detection (100 per-stock DB queries) and
              structure maintenance (50 per-stock trend+sxqs checks). Use for quick page loads.
              Set to False during /scan when you need full accuracy.
    """
    # ── Goose detection (skip in fast mode) ──
    goosed_now = 0
    goosed_codes = []
    if not fast:
        try:
            from app.services.alphaflow_features import compute_wave_features
            async with async_session_factory() as s2:
                r2 = await s2.execute(text("SELECT ts_code FROM alphaflow_pool ORDER BY last_updated DESC LIMIT 100"))
                recent_codes = [row[0] for row in r2.fetchall()]
            for code in recent_codes:
                try:
                    async with async_session_factory() as s3:
                        r_f = await s3.execute(text(
                            "SELECT close,open,volume,high,low FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 220"
                        ), {"c": code})
                        rows_f = list(reversed(r_f.fetchall()))
                    if len(rows_f) < 80: continue
                    cs = np.array([float(rw[0] or 0) for rw in rows_f])
                    os_z = np.array([float(rw[1] or cs[i]) for i, rw in enumerate(rows_f)])
                    vs = np.array([float(rw[2] or 0) for rw in rows_f])
                    hs = np.array([float(rw[3] or cs[i]) for i, rw in enumerate(rows_f)])
                    ls = np.array([float(rw[4] or cs[i]) for i, rw in enumerate(rows_f)])
                    wf = compute_wave_features(cs, os_z, hs, ls, vs)
                    if wf is None: continue
                    if wf[27] > 100:
                        async with async_session_factory() as s4:
                            await s4.execute(text(
                                "INSERT INTO goose_archive (ts_code, first_seen, last_prob, gain_from_first_lock, first_lock_avg, waves_completed) "
                                "VALUES (:c, CURRENT_DATE, 0, :g, :f, :w) ON CONFLICT (ts_code) DO UPDATE SET gain_from_first_lock=:g"
                            ), {"c": code, "g": round(wf[27], 1), "f": round(wf[26], 2), "w": int(wf[8] or 0)})
                            await s4.execute(text("DELETE FROM alphaflow_pool WHERE ts_code=:c"), {"c": code})
                            await s4.commit()
                        goosed_now += 1; goosed_codes.append(code)
                except Exception as e: logger.debug(f"Goose check failed for {code}: {e}")
        except Exception as e: logger.warning(f"Goose detection failed: {e}", exc_info=True)
        if goosed_now: logger.info(f"Goose: {goosed_now} archived")

    # ── Market timing ──
    market_regime, market_factor, market_risk = "unknown", 1.0, "unknown"
    try:
        from app.services.market_gate import get_market_state
        ms = await get_market_state()
        market_regime = ms.get("regime", "unknown"); market_risk = ms.get("risk", "unknown")
        market_factor = TIMING_FACTORS.get(market_regime, 1.0)
        if market_risk == "high": market_factor = max(0.50, market_factor - 0.05)
        market_factor = round(market_factor, 2)
    except Exception as e: logger.warning(f"Market state query failed: {e}")

    # ── Model check ──
    xgb_failed = False
    try:
        from app.services.alphaflow_pool import _load_xgb_model
        if await _load_xgb_model() is None: xgb_failed = True
    except Exception: xgb_failed = True

    # ── Pool query ──
    async with async_session_factory() as s:
        r = await s.execute(text(f"""
            SELECT ts_code, first_seen, last_updated, current_prob, prob_trend,
                   tier, days_in_pool, COALESCE(micro_score,0),
                   COALESCE(strategy_group, '') as strategy_group,
                   COALESCE(strategy_label, '') as strategy_label
            FROM alphaflow_pool WHERE 1=1 {tier_filter}
            ORDER BY current_prob DESC LIMIT :lim
        """), {"lim": limit})
        rows = r.fetchall()

    # ── Name lookup ──
    codes = [row[0] for row in rows]
    name_map = {}
    if codes:
        async with async_session_factory() as s2:
            r2 = await s2.execute(text("SELECT symbol, name FROM stock_name_cache WHERE symbol = ANY(:c)"), {"c": codes})
            name_map = {row[0]: row[1] for row in r2.fetchall()}
        for c in codes:
            if c not in name_map and c in _INDEX_NAMES: name_map[c] = _INDEX_NAMES[c]

    # ── Data assembly + timing scoring + lockup filtering ──
    data = []
    for row in rows:
        code, sg, sl = row[0], row[8] or "", row[9] or ""
        micro_score = row[7] or 0  # lockup days

        # v4.6: filter out early-lockup stocks (lockup <20d, no proven cycle history)
        # micro_score=0: already trading/broken out, always show
        # micro_score>=20: spring compressed enough, show
        # micro_score>0 AND <20: early lockup, hide unless veteran
        if not sg and 0 < micro_score < 20:
            continue  # early lockup, wait for compression

        entry = {"ts_code": code, "name": name_map.get(code, code),
                 "first_seen": str(row[1]), "last_updated": str(row[2]),
                 "prob": round(float(row[3] or 0), 3), "trend": round(float(row[4] or 0), 3),
                 "tier": row[5], "days_in_pool": row[6] or 1, "micro_score": micro_score}
        if sg: entry["strategy_group"] = sg; entry["strategy_label"] = sl; entry["is_veteran"] = True; entry["tier"] = "veteran"
        entry["raw_prob"] = entry["prob"]
        entry["timing_score"] = round(entry["prob"] * market_factor, 3)
        entry["market_factor"] = round(market_factor, 2)
        data.append(entry)

    # ── Lock quality gate + compression boost ──
    # Quality gate runs on ALL veterans (reads relative_strength from lock_detail).
    # Compression boost only runs on top-20 (expensive cycle math).
    try:
        from app.services.lock_detail_service import get_full_lock_detail
        all_veterans = [d for d in data if d.get("is_veteran")][:50]  # quality-gate top-50 for perf
        quality_checked = 0
        for d in all_veterans:
            try:
                ld = await get_full_lock_detail(d["ts_code"])
                rs = ld.get("relative_strength", 0)
                d["lock_quality"] = "strong" if (rs or 0) > 0 else "weak"
                d["lock_rs"] = round(rs or 0, 1)
                quality_checked += 1

                # Compression boost (top-20 only, cycle history math is expensive)
                if quality_checked <= 20:
                    cycles = ld.get("lock_cycles", [])
                    ld_current = ld.get("lock_days", 0)
                    if ld.get("in_lock") and ld_current > 0 and len(cycles) >= 3:
                        past = [c for c in cycles if c.get("breakout_pct", 0) > 0 or c != cycles[-1]]
                        if past and len(past) >= 2:
                            avg_lock = sum(c["days"] for c in past) / len(past)
                            if avg_lock > 0 and ld_current > avg_lock:
                                compression = ld_current / avg_lock
                                boost = min(0.45, (compression - 1.0) * 0.35)
                                d["timing_score"] = round(d["timing_score"] * (1 + boost), 3)
                                d["compression_boost"] = round(boost, 3)
                                d["avg_lock_days"] = round(avg_lock, 0)
            except Exception as inner_e:
                d.setdefault("lock_quality", "unknown")
                logger.warning(f"Lock detail {d['ts_code']}: {inner_e}")
        boosted = sum(1 for d in all_veterans[:20] if d.get('compression_boost',0) > 0)
        logger.info(f"Lock quality: {quality_checked}/{len(all_veterans)} veterans checked, {boosted} boosted")
    except Exception as e:
        logger.warning(f"Lock quality gate failed: {e}")

    data.sort(key=lambda x: x["timing_score"], reverse=True)

    # ── v4.7: Batch AlphaFlow signal (lock state + TG + Big Fairy) ──
    removed = 0
    try:
        codes = [d["ts_code"] for d in data]
        if not codes:
            return {"status": "success", "data": [], "count": 0, "tiers": {}, "total": 0,
                    "removed": 0, "goosed": goosed_now, "goosed_codes": goosed_codes,
                    "market": {"regime": market_regime, "risk": market_risk,
                               "factor": market_factor, "note": _timing_note(market_regime, market_factor)},
                    "model_status": "degraded" if xgb_failed else "ok"}

        # ── Step A: Batch load kline (120d) for lock state + Big Fairy ──
        kline_by_code = {}
        async with async_session_factory() as s_k:
            r_k = await s_k.execute(text("""
                SELECT ts_code, trade_date, close, high, low, volume FROM daily_kline
                WHERE ts_code = ANY(:codes) ORDER BY ts_code, trade_date
            """), {"codes": codes})
            for row in r_k.fetchall():
                kline_by_code.setdefault(row[0], []).append(
                    (str(row[1]), float(row[2] or 0), float(row[3] or 0), float(row[4] or 0), float(row[5] or 0)))

        # ── Step B: Lock state + Big Fairy with recency tracking ──
        from app.services.lock_detector import detect_lock_simple
        from app.services.big_fairy import _big_fairy_from_arrays
        from datetime import date as dt_date
        today = dt_date.today()

        lock_state = {}
        bf_computed = 0
        bf_sell_offset = {}
        for code in codes:
            rows = kline_by_code.get(code, [])
            if len(rows) < 20:
                lock_state[code] = "unknown"
                continue
            dates = [r[0] for r in rows]
            cs = np.array([r[1] for r in rows])
            hs = np.array([r[2] if r[2] else cs[i] for i, r in enumerate(rows)])
            ls = np.array([r[3] if r[3] else cs[i] for i, r in enumerate(rows)])
            vs = np.array([r[4] if r[4] else 0 for i, r in enumerate(rows)])

            lock = detect_lock_simple(cs, hs, ls)
            lock_state[code] = lock["state"]

            # BF recency: scan backward bar-by-bar to find last sell date
            try:
                bf_current = _big_fairy_from_arrays(cs, hs, ls, vs, code)
                bf_score_current = bf_current["score"] if bf_current else 0
                last_date = dt_date.fromisoformat(dates[-1]) if dates else today

                bf_offset = None
                # Check windows: today, -5 bars, -10 bars
                for bar_back, label in [(0, 0), (5, 5), (10, 10)]:
                    end = len(cs) - bar_back
                    if end < 60:
                        continue
                    bf_w = _big_fairy_from_arrays(cs[:end], hs[:end], ls[:end], vs[:end], code)
                    if bf_w and bf_w["score"] >= 2:
                        bar_date = dt_date.fromisoformat(dates[end - 1])
                        bf_offset = (today - bar_date).days
                        break  # take the most recent
                bf_sell_offset[code] = bf_offset

                # Store Big Fairy in entry dict
                d = next((x for x in data if x["ts_code"] == code), None)
                if d and bf_current:
                    d["big_fairy"] = {
                        "score": bf_score_current,  # show today's REAL score
                        "signal": bf_current["signal"],
                        "bearish": bf_score_current >= 2,
                        "dimensions": bf_current["dimensions"],
                        "rsi14": bf_current.get("rsi14"), "j": bf_current.get("j"),
                        "macd_hist": bf_current.get("macd_hist"), "details": bf_current.get("details", {}),
                        "sell_offset": bf_offset,  # used for signal competition, not display
                    }
                    bf_computed += 1
            except Exception:
                pass

        # ── Step C: Batch load TG scan_results (10d) with buy recency ──
        tg_map = {}
        async with async_session_factory() as s_tg:
            r_tg = await s_tg.execute(text("""
                SELECT symbol,
                       MAX(CASE WHEN level IN ('L2','L3') AND COALESCE(buy_strength,0) > 0 THEN 1 ELSE 0 END)::int as has_buy,
                       MAX(COALESCE(buy_strength,0)) as max_strength,
                       MAX(level) as max_level,
                       MAX(CASE WHEN level IN ('L2','L3') THEN trigger_path END) as best_trigger,
                       MAX(scan_date) as last_scan,
                       MAX(CASE WHEN level IN ('L2','L3') AND COALESCE(buy_strength,0) > 0 THEN scan_date END) as last_buy_date
                FROM scan_results
                WHERE symbol = ANY(:codes) AND scan_date >= CURRENT_DATE - 10
                GROUP BY symbol
            """), {"codes": codes})
            from datetime import date as dt_date
            today = dt_date.today()
            for row in r_tg.fetchall():
                last_buy = row[5]
                buy_offset = (today - last_buy).days if last_buy else None
                tg_map[row[0]] = {
                    "has_buy": bool(row[1]),
                    "level": row[3],
                    "buy_strength": float(row[2] or 0),
                    "trigger": row[4] or "",
                    "last_scan": str(row[5]),
                    "buy_offset": buy_offset,  # days since last buy (None=no buy)
                }

        # ── Step D: AlphaFlow信号 ──
        # 锁死中 → 观察（不管TG/BF）
        # 主升浪 + TG → 买入
        # 主升浪 + 大神仙空 → 卖出
        # 两者都有 → 最新信号胜
        for d in data:
            code = d["ts_code"]
            state = lock_state.get(code, "locked")
            tg = tg_map.get(code)
            bf = d.get("big_fairy", {})

            tg_buy_offset = tg.get("buy_offset") if tg else None
            bf_sell_offset = bf.get("sell_offset") if bf else None
            tg_active = tg_buy_offset is not None
            bf_active = bf_sell_offset is not None

            if state == "locked":
                signal, label = "watch", "锁死中"
            elif state == "breakout_up":
                if tg_active and bf_active:
                    # 两个信号都活跃 → 最新的胜出
                    if bf_sell_offset <= tg_buy_offset:
                        signal, label = "sell", f"大神仙空(新)"
                    else:
                        signal, label = "buy", "主升浪+TG"
                elif tg_active:
                    signal, label = "buy", "主升浪+TG"
                elif bf_active:
                    signal, label = "sell", f"大神仙空({bf.get('score',2)})"
                else:
                    signal, label = "watch", "主升浪待确认"
            elif state == "breakout_down":
                signal, label = "sell", "破位下跌"
            else:
                signal, label = "watch", "待确认"

            d["sxqs"] = {
                "signal": signal,
                "label": label,
                "tg_level": tg.get("level") if tg else None,
                "tg_has_buy": tg_active,
                "tg_buy_offset": tg_buy_offset,
                "bf_sell_offset": bf_sell_offset,
                "lock_state": state,
            }

        logger.info(f"v4.7 signal: {len(data)} stocks, lock={sum(1 for v in lock_state.values() if v=='locked')}, "
                    f"BF={bf_computed}, TG={len(tg_map)}, "
                    f"buy={sum(1 for d in data if d['sxqs']['signal']=='buy')}, "
                    f"sell={sum(1 for d in data if d['sxqs']['signal']=='sell')}")
    except Exception as e:
        logger.warning(f"Signal computation failed: {e}", exc_info=True)

    # ── Tier stats ──
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT tier, COUNT(*) FROM alphaflow_pool GROUP BY tier"))
        tiers = {row[0]: row[1] for row in r.fetchall()}
        r2 = await s.execute(text("SELECT COUNT(*) FROM alphaflow_pool"))
        total = r2.scalar() or 0

    return {
        "status": "success", "data": data, "count": len(data),
        "tiers": tiers, "total": total, "removed": removed,
        "goosed": goosed_now, "goosed_codes": goosed_codes,
        "market": {"regime": market_regime, "risk": market_risk,
                    "factor": market_factor, "note": _timing_note(market_regime, market_factor)},
        "model_status": "degraded" if xgb_failed else "ok",
    }
