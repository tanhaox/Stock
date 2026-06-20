"""板块联盟对比框架 — 长尾理论的放大器.

核心认知:
  单只股票的 N/M 形态可能被庄家做出来,
  但同一板块/概念的 5-10 只股票同时出现 N 型 → 板块真金白银在流入
  同一板块同时出现 M 型 → 板块资金在集体出逃

算法:
  1. 对 TG 信号股(同一天), 按行业/概念分组
  2. 每组 ≥ 3 只时, 下载各股 15 天 5 分钟线
  3. 逐只检测 N/M 形态
  4. 计算板块共识分: N 型占比 - M 型占比
  5. 用板块共识修正个股 NM 得分
"""

import asyncio, logging, numpy as np
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("sector_alliance")

# ── 阈值 ──
MIN_STOCKS_FOR_ALLIANCE = 3     # 板块至少 3 只信号股才计算联盟
ALLIANCE_BOOST_FACTOR = 0.35    # 板块共识对个股得分的修正幅度
ALLIANCE_CONCURRENCY = 3        # 并发下载数


async def _get_industry_map(symbols: list[str]) -> dict[str, str]:
    """获取股票→行业映射 (优先同花顺, 回退 Tushare)."""
    if not symbols:
        return {}

    async with async_session_factory() as s:
        # 优先 ths_member
        r = await s.execute(text("""
            SELECT DISTINCT ON (ts_code) ts_code, ths_name
            FROM ths_member WHERE ts_code = ANY(:syms) AND out_date IS NULL
        """), {"syms": symbols})
        mapping = {row[0]: row[1] for row in r.fetchall() if row[1]}

        # 回退 scan_results.industry
        missing = [s for s in symbols if s not in mapping]
        if missing:
            r2 = await s.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, industry
                FROM scan_results WHERE symbol = ANY(:syms) AND industry != ''
                ORDER BY symbol, scan_date DESC
            """), {"syms": missing})
            for row in r2.fetchall():
                if row[0] not in mapping and row[1]:
                    mapping[row[0]] = row[1]

        # 最终回退: 从代码推断
        for s in symbols:
            if s not in mapping:
                code = s.replace('.SZ', '').replace('.SH', '').replace('.BJ', '')
                if code.startswith('300') or code.startswith('301') or code.startswith('688'):
                    mapping[s] = '创业板'
                elif code.startswith('002') or code.startswith('003'):
                    mapping[s] = '中小板'
                else:
                    mapping[s] = '主板'

    return mapping


def _group_by_sector(symbols: list[str], industry_map: dict[str, str]) -> dict[str, list[str]]:
    """按行业分组 (同一行业 ≥ MIN_STOCKS_FOR_ALLIANCE 的才保留)."""
    groups = defaultdict(list)
    for sym in symbols:
        ind = industry_map.get(sym, "未知")
        groups[ind].append(sym)

    # 只保留足够大的组
    return {k: v for k, v in groups.items() if len(v) >= MIN_STOCKS_FOR_ALLIANCE}


async def analyze_sector_alliance(
    signal_stocks: list[dict],
    scan_date: date,
    concurrency: int = ALLIANCE_CONCURRENCY,
) -> dict:
    """对一批 TG 信号股进行板块联盟分析.

    Args:
        signal_stocks: [{symbol, name, composite_score, ...}, ...]
        scan_date: 扫描日期

    Returns:
        {
            sectors: {行业名: {consensus_nm, stock_count, individual_scores, boost_factor}},
            stock_adjustments: {symbol: {original_nm, sector_nm, adjustment, ...}},
            summary: str,
        }
    """
    if len(signal_stocks) < MIN_STOCKS_FOR_ALLIANCE:
        return {"sectors": {}, "stock_adjustments": {},
                "summary": f"信号股不足{MIN_STOCKS_FOR_ALLIANCE}只, 跳过联盟分析"}

    symbols = [s["symbol"] for s in signal_stocks]
    industry_map = await _get_industry_map(symbols)
    groups = _group_by_sector(symbols, industry_map)

    if not groups:
        return {"sectors": {}, "stock_adjustments": {},
                "summary": "无满足条件的板块联盟"}

    logger.info(f"Sector alliance: {len(groups)} sectors, "
                f"{sum(len(v) for v in groups.values())} stocks")

    # ── 并发下载分钟线 + N/M检测 ──
    from app.services.minute_data import fetch_5min_bars
    from app.services.minute_nm_detector import detect_nm_pattern

    sem = asyncio.Semaphore(concurrency)

    async def _analyze_one(symbol: str) -> dict:
        async with sem:
            bars = await fetch_5min_bars(symbol, lookback_days=LOOKBACK_DAYS)
            if len(bars) < 100:
                return {"symbol": symbol, "error": "分钟数据不足"}
            nm = detect_nm_pattern(bars)
            nm["symbol"] = symbol
            return nm

    # 收集所有需要分析的股票 (所有板块的并集)
    all_analyze = set()
    for stocks in groups.values():
        all_analyze.update(stocks)

    tasks = [_analyze_one(sym) for sym in all_analyze]
    results = await asyncio.gather(*tasks)

    # symbol → nm_result
    nm_map = {r["symbol"]: r for r in results if "error" not in r}
    logger.info(f"NM detection complete: {len(nm_map)}/{len(all_analyze)} stocks analyzed")

    # ── 逐板块计算共识 ──
    sectors = {}
    stock_adjustments = {}

    for sector_name, sector_symbols in groups.items():
        sector_nm_scores = []
        individual_scores = {}

        for sym in sector_symbols:
            if sym not in nm_map:
                continue
            nm = nm_map[sym]
            sector_nm_scores.append(nm["nm_score"])
            individual_scores[sym] = {
                "nm_score": nm["nm_score"],
                "dominant_shape": nm["dominant_shape"],
                "n_ratio": nm["n_ratio"],
                "m_ratio": nm["m_ratio"],
                "verdict": nm["verdict"],
            }

        if len(sector_nm_scores) < MIN_STOCKS_FOR_ALLIANCE:
            continue

        # 板块共识分: 均值 (抗异常值)
        consensus_nm = round(float(np.mean(sector_nm_scores)), 3)

        # 板块内一致性: 同号的比例
        same_sign = sum(1 for s in sector_nm_scores if s * consensus_nm > 0)
        consistency = same_sign / len(sector_nm_scores)

        # 板块效应强度: |共识| × 一致性
        sector_strength = abs(consensus_nm) * consistency

        # boost 系数: 板块共识对个股的修正幅度
        if consensus_nm > 0.15 and consistency > 0.60:
            boost_factor = ALLIANCE_BOOST_FACTOR * sector_strength
            sector_verdict = "N型联盟 — 板块资金真流入"
        elif consensus_nm < -0.15 and consistency > 0.60:
            boost_factor = -ALLIANCE_BOOST_FACTOR * sector_strength
            sector_verdict = "M型联盟 — 板块资金真流出"
        elif abs(consensus_nm) < 0.10:
            boost_factor = 0
            sector_verdict = "板块中性 — 信号分化, 无联盟效应"
        else:
            boost_factor = consensus_nm * ALLIANCE_BOOST_FACTOR * 0.3
            sector_verdict = "板块弱趋向 — 一致性不足以判定联盟"

        sectors[sector_name] = {
            "stock_count": len(sector_symbols),
            "analyzed_count": len(sector_nm_scores),
            "consensus_nm": consensus_nm,
            "consistency": round(consistency, 2),
            "sector_strength": round(sector_strength, 3),
            "boost_factor": round(boost_factor, 3),
            "verdict": sector_verdict,
            "individual_scores": individual_scores,
        }

        # ── 修正个股得分 ──
        for sym in sector_symbols:
            if sym not in nm_map:
                continue
            original_nm = nm_map[sym]["nm_score"]
            # 修正: 个股分向板块共识方向调整
            # 如果个股 N 但板块也 N → 加分 (联盟确认)
            # 如果个股 N 但板块 M → 减分 (孤例, 可能是假信号)
            if consensus_nm * original_nm > 0:
                # 同方向 → 加分
                adjustment = abs(boost_factor) * 0.6
            elif consensus_nm * original_nm < 0:
                # 反方向 → 减分 (个股被板块拖累)
                adjustment = -abs(boost_factor) * 1.2
            else:
                # 个股中性 → 微调向板块方向
                adjustment = boost_factor * 0.3

            adjusted_nm = round(max(-1.0, min(1.0, original_nm + adjustment)), 3)

            stock_adjustments[sym] = {
                "original_nm": original_nm,
                "sector_nm": consensus_nm,
                "sector_name": sector_name,
                "adjustment": round(adjustment, 3),
                "adjusted_nm": adjusted_nm,
                "sector_verdict": sector_verdict,
            }

    # ── 汇总 ──
    n_alliances = sum(1 for v in sectors.values() if v["consensus_nm"] > 0.15)
    m_alliances = sum(1 for v in sectors.values() if v["consensus_nm"] < -0.15)
    total_analyzed = sum(v["analyzed_count"] for v in sectors.values())

    summary_parts = []
    if n_alliances:
        summary_parts.append(f"{n_alliances}个板块呈N型联盟(真流入)")
    if m_alliances:
        summary_parts.append(f"{m_alliances}个板块呈M型联盟(真流出)")
    if not summary_parts:
        summary_parts.append("无显著板块联盟")
    summary_parts.append(f"共分析{total_analyzed}只信号股")

    return {
        "scan_date": str(scan_date),
        "sectors": sectors,
        "stock_adjustments": stock_adjustments,
        "summary": " | ".join(summary_parts),
        "n_alliance_sectors": n_alliances,
        "m_alliance_sectors": m_alliances,
        "total_analyzed": total_analyzed,
    }


# ── 常量 ──
LOOKBACK_DAYS = 15
