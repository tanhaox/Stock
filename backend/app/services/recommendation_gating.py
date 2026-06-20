"""推荐门控服务 - 从 result.py 提取 (v4.3)."""
import logging
from datetime import date
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("recommendation_gating")

SW_NAME_MAP = {
    '801010.SI': '农林牧渔', '801030.SI': '基础化工', '801040.SI': '钢铁',
    '801050.SI': '有色金属', '801080.SI': '电子', '801110.SI': '家用电器',
    '801120.SI': '食品饮料', '801130.SI': '纺织服饰', '801140.SI': '轻工制造',
    '801150.SI': '医药生物', '801160.SI': '公用事业', '801170.SI': '交通运输',
    '801180.SI': '房地产', '801200.SI': '商贸零售', '801210.SI': '社会服务',
    '801230.SI': '综合', '801710.SI': '建筑材料', '801720.SI': '建筑装饰',
    '801730.SI': '电力设备', '801740.SI': '国防军工', '801750.SI': '计算机',
    '801760.SI': '传媒', '801770.SI': '通信', '801780.SI': '银行',
    '801790.SI': '非银金融', '801880.SI': '汽车', '801890.SI': '机械设备',
    '801950.SI': '煤炭', '801960.SI': '石油石化',
}

NOISE_WORDS = ("同花顺","昨日","减持","业绩","京津冀","粤港澳",
    "陆股通","沪股通","打板","首板","连板","低估值",
    "低动量","保守","小盘","全A")


async def collect_hot_sectors() -> tuple[set, set]:
    """收集热点板块: 龙虎榜+SW行业排名+新闻事件."""
    hot_sectors: set[str] = set()
    hot_individuals: set[str] = set()

    # 龙虎榜: 近3天净买入>0的股票 → 查其行业
    try:
        async with async_session_factory() as hs:
            r = await hs.execute(text("""
                SELECT tl.ts_code, tm.ths_name
                FROM toplist_daily tl
                LEFT JOIN ths_member tm ON tm.ts_code = tl.ts_code AND tm.out_date IS NULL
                WHERE tl.trade_date >= CURRENT_DATE - 3 AND tl.l_net > 0
            """))
            for row in r.fetchall():
                if row[1]: hot_sectors.add(row[1])
                hot_individuals.add(row[0])
    except Exception:
        pass

    # SW 行业排名 Top-8 (5日涨幅排序)
    try:
        from app.services.sector_heat_engine import get_sector_rankings
        rankings = await get_sector_rankings()
        for code, rk in sorted(rankings.items(), key=lambda x: x[1].get('pct_5d', 0), reverse=True)[:8]:
            name = SW_NAME_MAP.get(code, code)
            if rk.get('pct_5d', 0) > 0:
                hot_sectors.add(name)
                hot_sectors.add(code)
    except Exception:
        pass

    # 新闻: 从 stock_events 提取利好行业
    try:
        async with async_session_factory() as hs:
            r = await hs.execute(text("""
                SELECT category FROM stock_events
                WHERE created_at >= CURRENT_DATE - 3
                GROUP BY category HAVING COUNT(*) >= 2
            """))
            for row in r.fetchall():
                if row[0]: hot_sectors.add(row[0])
    except Exception:
        pass

    # 过滤噪音
    hot_sectors = {s for s in hot_sectors if s and len(s) >= 2 and not any(w in s for w in NOISE_WORDS)}
    return hot_sectors, hot_individuals


def match_stock_to_sector(stock_symbol: str, stock_industry: dict, hot_sectors: set,
                          hot_individuals: set) -> tuple[bool, str]:
    """判定股票是否属于热点板块."""
    if stock_symbol in hot_individuals:
        return True, 'toplist'
    ind = stock_industry.get(stock_symbol, '')
    if not ind:
        return False, ''
    for hk in hot_sectors:
        if not hk: continue
        if hk in ind or ind in hk: return True, ind
        if len(hk) >= 2 and hk[:2] in ind: return True, ind
    return False, ''


async def apply_market_gate(recommended: list[dict], data: list[dict], regime: str, risk: str,
                             hot_sectors: set, hot_individuals: set,
                             stock_industry: dict) -> dict:
    """执行分级门控: TIMING_CAPS + 热点/非热点双阈值 + 安全阀."""
    TIMING_CAPS = {
        "趋势上涨": (0.28, 120, 38, 0.22, 120, 34),
        "结构行情": (0.30, 100, 40, 0.25, 100, 36),
        "震荡整理": (0.32, 80, 42, 0.26, 80, 38),
        "维稳行情": (0.35, 60, 42, 0.28, 60, 38),
        "缩量博弈": (0.40, 30, 48, 0.30, 45, 44),
        "弱势探底": (0.48, 12, 54, 0.35, 30, 48),
        "恐慌杀跌": (0.55, 6, 56, 0.40, 15, 50),
    }
    cold_minp, cold_n, cold_sc, hot_minp, hot_n, hot_sc = TIMING_CAPS.get(
        regime, (0.35, 80, 40, 0.28, 80, 36))

    if risk == "high":
        cold_minp = min(0.60, cold_minp + 0.08)
        cold_n = min(10, cold_n)
        cold_sc = max(50, cold_sc)
        hot_minp = min(0.50, hot_minp + 0.05)
        hot_n = max(8, min(35, hot_n))
        hot_sc = max(45, hot_sc)

    before = len(recommended)
    passed = []
    hot_passed = 0
    cold_passed = 0

    for d in recommended:
        is_hot, sector = match_stock_to_sector(
            d['symbol'], stock_industry, hot_sectors, hot_individuals)

        min_p, max_n_use, min_s = (hot_minp, hot_n, hot_sc) if is_hot else (cold_minp, cold_n, cold_sc)

        sc = d.get("composite_score", 0)
        if sc < min_s:
            d["filtered_reason"] = f"sc{sc:.0f}<{min_s}"
            continue

        wp = d.get("win_probability")
        if not is_hot and wp is not None and wp < min_p:
            d["filtered_reason"] = f"wp{wp:.0%}<{min_p:.0%}"
            continue

        d["hot_sector"] = is_hot
        d["sector_name"] = sector
        passed.append(d)
        if is_hot:
            hot_passed += 1
        else:
            cold_passed += 1
            if cold_passed > cold_n:
                passed.remove(d)
                cold_passed -= 1
                continue

    gate_filtered = before - len(recommended)
    recommended[:] = passed
    gate_filtered = before - len(recommended)

    # 安全阀: 结果<5放宽阈值
    if len(recommended) < 5:
        relaxed = []
        relaxed_sc = max(40, cold_sc - 10)
        relaxed_wp = max(0.30, cold_minp - 0.12)
        for d in data:
            if d in recommended: continue
            sc = d.get("composite_score", 0)
            wp = d.get("win_probability")
            if sc >= relaxed_sc and (wp is None or wp >= relaxed_wp):
                d["hot_sector"] = False
                d["sector_name"] = ""
                d["gate_relaxed"] = True
                d["risk_label"] = "warn"
                relaxed.append(d)
                if len(relaxed) + len(recommended) >= 10:
                    break
        recommended.extend(relaxed)
        gate_filtered -= len(relaxed)

    regime_hard_cap = {
        "regime": regime, "risk": risk,
        "hot": {"min_sc": hot_sc, "min_prob": hot_minp, "max": hot_n},
        "cold": {"min_sc": cold_sc, "min_prob": cold_minp, "max": cold_n},
        "hot_count": hot_passed, "cold_count": cold_passed,
        "hot_sectors": list(hot_sectors)[:20],
    }

    return {"gate_filtered": gate_filtered, "regime_hard_cap": regime_hard_cap,
            "hot_passed": hot_passed, "cold_passed": cold_passed,
            "passed": passed}


async def apply_drill_corrections(recommended: list[dict], scan_date, regime: str) -> dict:
    """执行历史深度复盘 + composite_score 修正."""
    drill_data: dict = {}
    if not recommended:
        return drill_data
    try:
        from app.services.stock_historical_drill import drill_stocks
        top_syms = [d["symbol"] for d in recommended[:20]]
        drill_data = await drill_stocks(
            symbols=top_syms, current_date=scan_date,
            market_regime=regime, force_refresh=False,
        )
        for d in recommended:
            dr = drill_data.get(d["symbol"], {})
            d["drill_summary"] = dr.get("drill_summary", "")
            d["drill_signal_effectiveness"] = dr.get("signal_effectiveness", {})
            d["drill_chip_simulation"] = dr.get("chip_simulation", {})
            d["drill_critical_position"] = dr.get("critical_position", {})
            d["drill_pattern_matching"] = dr.get("pattern_matching", {})

            se = dr.get("signal_effectiveness", {})
            if se.get("status") == "success":
                wr = se.get("win_rate_5d", 0.5)
                if wr >= 0.6:
                    d["composite_score"] = round(min(100, d["composite_score"] + 3), 1)
                elif wr < 0.35 and se.get("history_count", 0) >= 5:
                    d["composite_score"] = round(max(0, d["composite_score"] - 4), 1)

            cs = dr.get("chip_simulation", {})
            if cs.get("trend") == "accelerating":
                d["composite_score"] = round(min(100, d["composite_score"] + 2), 1)

            res = dr.get("resonance", {})
            d["drill_resonance"] = res
            if res and res.get("status") != "insufficient":
                idx_r = res.get("index_resonance", {})
                if idx_r.get("independence_rate", 0) >= 0.40:
                    d["composite_score"] = round(min(100, d["composite_score"] + 2), 1)
                if idx_r.get("pseudo_strength_rate", 0) >= 0.30:
                    d["composite_score"] = round(max(0, d["composite_score"] - 3), 1)
                sec_r = res.get("sector_resonance", {})
                if sec_r.get("lead_rate", 0) >= 0.40:
                    d["composite_score"] = round(min(100, d["composite_score"] + 2), 1)
                chip_r = res.get("chip_resonance", {})
                high_wr = chip_r.get("high_absorption_win_rate", 0)
                low_wr = chip_r.get("low_absorption_win_rate", 0)
                cur_ar = cs.get("current_ar", 0)
                if high_wr >= 0.70 and cur_ar >= 0.60:
                    d["composite_score"] = round(min(100, d["composite_score"] + 3), 1)
                elif low_wr <= 0.30 and cur_ar > 0 and cur_ar < 0.40:
                    d["composite_score"] = round(max(0, d["composite_score"] - 4), 1)

            mb = dr.get("micro_behavior", {})
            d["drill_micro_behavior"] = mb
            if mb and mb.get("status") != "unavailable":
                rise = mb.get("fast_rise", {})
                if isinstance(rise, dict):
                    cs2 = rise.get("current_status", {})
                    if cs2.get("is_any_trigger_active"):
                        d["composite_score"] = round(min(100, d["composite_score"] + 2), 1)
                fall = mb.get("fast_fall", {})
                if isinstance(fall, dict):
                    cs3 = fall.get("current_status", {})
                    if cs3.get("is_any_trigger_active"):
                        d["composite_score"] = round(max(0, d["composite_score"] - 3), 1)
    except Exception as e:
        logger.warning(f"Drill integration failed (non-fatal): {e}")
    return drill_data
