"""分析结果 API — v2.3 简化 + 手动加股走完整管线."""
from datetime import date
import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from app.schemas.analysis import AddStockRequest
from sqlalchemy import text
import logging
logger = logging.getLogger("analysis")
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db, async_session_factory
from app.core.name_resolver import get_stock_name

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post("/trigger")
async def trigger_analysis():
    """手动触发深度评分(对最新扫描结果运行)."""
    from app.services.deep_scorer import deep_analyze
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(scan_date) FROM scan_results"))
        tg_scan_date = r.scalar()
        if not tg_scan_date:
            return {"status": "error", "detail": "没有扫描结果"}

        # ★ 去重: 该 scan_date 已有分析结果则跳过
        r2 = await s.execute(text(
            "SELECT COUNT(*) FROM analysis_scores WHERE scan_date = :d"
        ), {"d": tg_scan_date})
        existing = r2.scalar()
        if existing > 0:
            sq_min = 0.60; wp_min = 0.45; ts_min = 3
            return {
                "status": "success", "count": existing, "recommended": 0,
                "gate": {"min_sq": sq_min, "min_wp": wp_min, "min_trend": ts_min},
                "session_date": str(tg_scan_date), "tg_scan": str(tg_scan_date),
                "skipped": True, "reason": f"已分析过 ({existing} 只)"
            }

        results = await deep_analyze(s, scan_date=tg_scan_date, session_date=tg_scan_date)
    sq_min = 0.60; wp_min = 0.45; ts_min = 3
    passed = [r for r in results if
              r.get("signal_quality", 0) >= sq_min
              and r.get("win_probability", 0) >= wp_min
              and r.get("trend_score", 0) >= ts_min]
    return {
        "status": "success", "count": len(results), "recommended": len(passed),
        "gate": {"min_sq": sq_min, "min_wp": wp_min, "min_trend": ts_min},
        "session_date": str(tg_scan_date), "tg_scan": str(tg_scan_date),
    }

@router.get("/results")
async def get_analysis_results(db: AsyncSession = Depends(get_db), limit: int = Query(50), manual_only: str = Query(default="")):
    manual_filter = "AND a.archetype = 'manual'" if manual_only == 'true' else "AND a.archetype != 'manual'"
    r = await db.execute(text(f"""
        SELECT a.symbol, COALESCE(nc.name, s.name, a.name, a.symbol) as name,
               a.tech_score, a.kline_score, a.fund_score,
               a.sector_bonus, a.composite_score, a.fundamental_adjustment,
               a.archetype, a.adjustment_reasons, s.level, a.market_correction,
               COALESCE(pat.patterns, '') as patterns,
               COALESCE(amb.ambush_score, 0) as ambush_score,
               a.win_probability, a.downside_risk, a.signal_quality,
               COALESCE(a.trend_score, 0) as trend_score,
               COALESCE(a.entry_score, 0) as entry_score,
               COALESCE(s.market, '主板') as market,
               a.details,
               -- v7.0.32: 新增技术/筹码字段
               a.macd_dif, a.macd_dea, a.kdj_j, a.rsi_24, a.boll_pos, a.cci,
               a.cost_50pct, a.weight_avg, a.winner_rate, a.cost_spread
        FROM analysis_scores a
        LEFT JOIN scan_results s ON a.symbol=s.symbol AND a.scan_date=s.scan_date
        LEFT JOIN stock_name_cache nc ON nc.symbol = a.symbol
        LEFT JOIN (
            SELECT ts_code, STRING_AGG(pattern_type, ',') as patterns
            FROM pattern_signals
            WHERE trade_date = (SELECT MAX(scan_date) FROM analysis_scores)
            GROUP BY ts_code
        ) pat ON a.symbol = pat.ts_code
        LEFT JOIN (
            SELECT symbol, MAX(composite_score) as ambush_score
            FROM ambush_signals
            WHERE scan_date = (SELECT MAX(scan_date) FROM analysis_scores)
            GROUP BY symbol
        ) amb ON a.symbol = amb.symbol
        WHERE a.scan_date = (SELECT MAX(scan_date) FROM analysis_scores) {manual_filter}
        ORDER BY a.composite_score DESC LIMIT :lim
    """), {"lim": limit})
    data = []
    for row in r.fetchall():
        # SQL 实际顺序:
        #   [0]symbol [1]name [2]tech [3]kline [4]fund [5]sector_bonus [6]composite
        #   [7]fund_adj [8]archetype [9]adjustment [10]level [11]market_correction
        #   [12]patterns [13]ambush [14]win_prob [15]downside [16]signal_quality
        #   [17]trend [18]entry [19]market [20]details
        #   [21]macd_dif [22]macd_dea [23]kdj_j [24]rsi_24 [25]boll_pos
        #   [26]cci [27]cost_50pct [28]weight_avg [29]winner_rate [30]cost_spread
        raw_details = row[20] if len(row) > 20 and row[20] else {}
        if isinstance(raw_details, str):
            try: raw_details = json.loads(raw_details)
            except Exception: raw_details = {}
        details = raw_details if isinstance(raw_details, dict) else {}

        data.append({
        "symbol": row[0], "name": row[1],
        "tech_score": float(row[2] or 0), "kline_score": float(row[3] or 0),
        "fund_score": float(row[4] or 0), "sector_bonus": float(row[5] or 0),
        "composite_score": float(row[6] or 0), "fundamental_adjustment": float(row[7] or 0),
        "archetype": row[8] or "unknown",
        "adjustment_reasons": row[9] if row[9] else [],
        "level": row[10],
        "market_correction": row[11] or "",
        "patterns": row[12] or "",
        "ambush_score": float(row[13] or 0),
        "win_probability": float(row[14]) if len(row) > 14 and row[14] is not None else None,
        "downside_risk": float(row[15]) if len(row) > 15 and row[15] is not None else None,
        "signal_quality": float(row[16]) if len(row) > 16 and row[16] is not None else None,
        "trend_score": int(row[17]) if len(row) > 17 and row[17] is not None else 0,
        "entry_score": int(row[18]) if len(row) > 18 and row[18] is not None else 0,
        "market": row[19] if len(row) > 19 else "主板",
        "news_signal": details.get("news_signal"),
        # v7.0.32: 新增技术/筹码字段 (row index 21-30)
        "macd_dif": float(row[21]) if len(row) > 21 and row[21] is not None else None,
        "macd_dea": float(row[22]) if len(row) > 22 and row[22] is not None else None,
        "kdj_j": float(row[23]) if len(row) > 23 and row[23] is not None else None,
        "rsi_24": float(row[24]) if len(row) > 24 and row[24] is not None else None,
        "boll_pos": float(row[25]) if len(row) > 25 and row[25] is not None else None,
        "cci": float(row[26]) if len(row) > 26 and row[26] is not None else None,
        "cost_50pct": float(row[27]) if len(row) > 27 and row[27] is not None else None,
        "weight_avg": float(row[28]) if len(row) > 28 and row[28] is not None else None,
        "winner_rate": float(row[29]) if len(row) > 29 and row[29] is not None else None,
        "cost_spread": float(row[30]) if len(row) > 30 and row[30] is not None else None,
    })

    # ★ 名称兜底: 如果后端 SQL COALESCE 仍返回 symbol (老扫描/stock_basic 没数据),
    #   用 name_resolver.get_stock_name() 4 层兜底 (stock_basic → cache → scan_results → fallback)
    need_name_fix = [(d["symbol"], d["name"]) for d in data
                     if not d["name"] or d["name"] == d["symbol"] or d["name"].endswith(".SH") or d["name"].endswith(".SZ") or d["name"].endswith(".BJ")]
    if need_name_fix:
        logger.info(f"[analysis] {len(need_name_fix)} stocks need name fallback (symbol-only)")
        for sym, _ in need_name_fix:
            try:
                real_name = await get_stock_name(sym)
                if real_name and real_name != sym:
                    # 找到对应 d 改 name
                    for d in data:
                        if d["symbol"] == sym:
                            d["name"] = real_name
                            break
            except Exception as e:
                logger.warning(f"Name fallback failed for {sym}: {e}")

    for d in data:
        sq = d.get("signal_quality")
        if sq is not None:
            if sq >= 0.7: d["risk_label"] = ""
            elif sq >= 0.5: d["risk_label"] = "warn"
            elif sq >= 0.3: d["risk_label"] = "danger"
            else: d["risk_label"] = "dead"
        else:
            d["risk_label"] = ""

    # ── Big Fairy 批量计算 ──
    if data:
        try:
            import numpy as np
            from app.services.big_fairy import _big_fairy_from_arrays
            syms = [d["symbol"] for d in data]
            kline_by_code = {}
            async with async_session_factory() as s_bf:
                r_bf = await s_bf.execute(text(
                    "SELECT ts_code, close, high, low, volume FROM daily_kline "
                    "WHERE ts_code = ANY(:codes) ORDER BY ts_code, trade_date"
                ), {"codes": syms})
                for row in r_bf.fetchall():
                    kline_by_code.setdefault(row[0], []).append(
                        (float(row[1] or 0), float(row[2] or 0), float(row[3] or 0), float(row[4] or 0)))
            for d in data:
                rows_bf = kline_by_code.get(d["symbol"], [])
                if len(rows_bf) < 60: continue
                cs = np.array([r[0] for r in rows_bf])
                hs = np.array([r[1] if r[1] else cs[i] for i, r in enumerate(rows_bf)])
                ls = np.array([r[2] if r[2] else cs[i] for i, r in enumerate(rows_bf)])
                vs = np.array([r[3] if r[3] else 0 for i, r in enumerate(rows_bf)])
                bf = _big_fairy_from_arrays(cs, hs, ls, vs, d["symbol"])
                if bf:
                    d["big_fairy"] = {
                        "score": bf["score"], "signal": bf["signal"],
                        "bearish": bf["bearish"], "dimensions": bf["dimensions"],
                    }
        except Exception as e:
            logger.warning(f"Big Fairy batch failed: {e}")

    # ── v4.7: 大神仙空过滤 — BF≥2 直接剔除，不给推荐 ──
    bf_filtered = 0
    if data:
        filtered = []
        for d in data:
            bf = d.get("big_fairy", {})
            bf_score = bf.get("score", 0)
            if bf_score >= 3:
                # 强空直接剔除
                bf_filtered += 1
                continue
            if bf_score >= 2:
                # 偏空：composite_score 打六折
                d["composite_score"] = round(d["composite_score"] * 0.6, 1)
                d["bf_penalty"] = True
            filtered.append(d)
        if bf_filtered:
            data = filtered
            logger.info(f"Big Fairy filter: removed {bf_filtered} stocks (BF>=3)")
        penalized = sum(1 for d in data if d.get("bf_penalty"))
        if penalized:
            logger.info(f"Big Fairy penalty: {penalized} stocks (BF>=2, composite=0.6x)")

    if data:
        from datetime import date as _dt
        syms = [d["symbol"] for d in data]
        r2 = await db.execute(text("SELECT MAX(scan_date) FROM analysis_scores"))
        sd = r2.scalar()
        cutoff = sd
        if sd:
            r3 = await db.execute(text("""
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date FROM daily_kline
                    WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT 31
                ) sub ORDER BY trade_date LIMIT 1
            """), {"d": sd})
            row30 = r3.fetchone()
            if row30: cutoff = row30[0]
        push_r = await db.execute(text("""
            SELECT symbol, COUNT(DISTINCT scan_date) FROM analysis_scores
            WHERE symbol = ANY(:syms) AND scan_date >= :ms AND composite_score >= 40
            GROUP BY symbol
        """), {"syms": syms, "ms": cutoff})
        push_counts = {row[0]: row[1] for row in push_r.fetchall()}
        for d in data:
            d["monthly_pushes"] = push_counts.get(d["symbol"], 1)

    return {"status": "success", "data": data, "count": len(data)}


@router.post("/add-stock")
async def add_stock_manual(req: AddStockRequest):
    """手动添加: 下载K线 → scan_results占位 → deep_analyze完整管线."""
    from app.services.deep_scorer import deep_analyze
    from app.services.tushare import fetch_daily_data
    from app.services.tushare_common import call_tushare

    sym = req.symbol.strip().upper()
    if sym.isdigit() and len(sym) == 6:
        if sym.startswith('6'): sym += '.SH'
        elif sym.startswith(('0', '3')): sym += '.SZ'
        elif sym.startswith(('8', '4')): sym += '.BJ'
    if not sym or not (sym.endswith('.SH') or sym.endswith('.SZ') or sym.endswith('.BJ')):
        return {"status": "error", "detail": "格式错误，如: 600660.SH"}

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(scan_date) FROM scan_results"))
        scan_date = r.scalar()
        if not scan_date:
            scan_date = date.today()

    async with async_session_factory() as s:
        kline_df = await fetch_daily_data(s, sym, days=120, local_only=True)
        if kline_df is None or len(kline_df) < 20:
            rows = await call_tushare('daily',
                {'ts_code': sym, 'start_date': '20260101', 'end_date': date.today().strftime('%Y%m%d')},
                'ts_code,trade_date,open,high,low,close,vol')
            if not rows:
                return {"status": "error", "detail": f"无法获取 {sym} 的K线数据"}
            for r2 in rows:
                await s.execute(text(
                    'INSERT INTO daily_kline (ts_code,trade_date,open,high,low,close,volume) '
                    'VALUES(:ts,:td,:o,:h,:l,:c,:v) ON CONFLICT (ts_code,trade_date) DO NOTHING'),
                    {'ts': r2['ts_code'], 'td': date(int(r2['trade_date'][:4]), int(r2['trade_date'][4:6]), int(r2['trade_date'][6:8])),
                     'o': float(r2.get('open',0)or 0), 'h': float(r2.get('high',0)or 0),
                     'l': float(r2.get('low',0)or 0), 'c': float(r2.get('close',0)or 0),
                     'v': float(r2.get('vol',0)or 0)})
            await s.commit()

    name = sym
    try:
        r2 = await call_tushare('stock_basic', {'ts_code': sym, 'list_status': 'L'}, 'ts_code,name')
        if r2: name = r2[0].get('name', sym)
    except Exception as e:
        logger.debug(f"stock_basic name lookup failed for {sym}: {e}")
        pass
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO scan_results (scan_date, symbol, name, level, composite_score, market)
            VALUES (:d, :s, :n, 'ADD', 0, :mkt)
            ON CONFLICT (scan_date, symbol) DO NOTHING
        """), {"d": scan_date, "s": sym, "n": name,
               "mkt": "主板" if sym.endswith(".SH") or sym.startswith(("0","6")) else ("创业板" if sym.startswith("300") or sym.startswith("301") or sym.startswith("688") else "主板")})
        await s.commit()

    async with async_session_factory() as s:
        results = await deep_analyze(s, scan_date=scan_date, session_date=scan_date)

    stock_result = next((r for r in (results or []) if r["symbol"] == sym), None)
    if stock_result:
        return {"status": "success", "data": {
            "symbol": sym, "name": name,
            "composite_score": stock_result["composite_score"],
            "tech_score": stock_result["tech_score"],
            "kline_score": stock_result["kline_score"],
            "fund_score": stock_result["fund_score"],
        }}
    return {"status": "error", "detail": f"{sym} 已下载K线但未通过评分过滤"}


@router.delete("/manual-stock")
async def delete_manual_stock(symbol: str = Query(...)):
    async with async_session_factory() as s:
        await s.execute(text(
            "DELETE FROM analysis_scores WHERE symbol=:s AND archetype='manual'"
        ), {"s": symbol})
        await s.commit()
    return {"status": "success", "message": f"{symbol} 已删除"}
