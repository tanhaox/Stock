"""基本面评分函数 — 从 deep_scorer.py 拆分 (v4.3)."""
import logging
import time
import numpy as np
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

_sector_alpha_cache: dict[str, float] = {}

def score_sector_alpha(kline_df, sector_change_pct: float = 0) -> dict:
    """申万行业相对强度 -10~+10.

    个股5日涨幅 vs 行业指数5日涨幅 = Alpha.
    跑赢同行→高分, 跑输同行→低分.
    """
    if kline_df is None or len(kline_df) < 5:
        return None
    c = kline_df["Close"]
    stock_ret = float((c.iloc[-1] / c.iloc[-5] - 1) * 100) if len(c) >= 5 else 0
    alpha = stock_ret - sector_change_pct
    if alpha > 5:        score = 8.0
    elif alpha > 2:      score = 5.0
    elif alpha > 0:      score = 2.0
    elif alpha > -2:     score = -2.0
    elif alpha > -5:     score = -5.0
    else:                score = -8.0
    return {"score": round(score, 1), "details": f"Alpha={alpha:+.1f}%"}


def score_toplist_sector(symbol: str, sector_toplist_flow: dict[str, float],
                          industry_map: dict[str, str] = None) -> dict:
    """龙虎榜板块资金热度评分 -5~+5 (v4.3).

    该股所属行业在龙虎榜上的净买卖额:
      净买入 > 5000万 → +2 分
      净买入 > 2亿   → +4 分
      净卖出 > 5000万 → -2 分
      净卖出 > 2亿   → -4 分
      无数据 → 0 分

    Args:
        symbol: 股票代码
        sector_toplist_flow: {行业名: 净买卖额} 预加载字典
        industry_map: {ts_code: 行业名} 可选映射

    Returns:
        {"score": -5~+5, "detail": str}
    """
    if not sector_toplist_flow:
        return {"score": 0, "detail": "无龙虎榜数据"}

    # 查找该股所属行业
    sector_name = None
    if industry_map:
        sector_name = industry_map.get(symbol, "")
    if not sector_name:
        return {"score": 0, "detail": "无行业分类"}

    # 模糊匹配: 龙虎榜行业名可能包含或包含于 ths_member 行业名
    net_flow = 0.0
    for flow_sector, flow_val in sector_toplist_flow.items():
        if flow_sector and (flow_sector in (sector_name or "") or (sector_name or "") in flow_sector):
            net_flow = flow_val
            break

    if abs(net_flow) < 100:  # 几乎无资金
        return {"score": 0, "detail": f"{sector_name} 龙虎榜无显著资金"}

    # 分档评分
    if net_flow > 200_000_000:  # > 2亿
        score = 5
    elif net_flow > 100_000_000:  # > 1亿
        score = 3
    elif net_flow > 50_000_000:   # > 5000万
        score = 2
    elif net_flow < -200_000_000:
        score = -5
    elif net_flow < -100_000_000:
        score = -3
    elif net_flow < -50_000_000:
        score = -2
    else:
        score = 0

    direction = "净买入" if net_flow > 0 else "净卖出"
    amt_str = f"{abs(net_flow)/1e8:.1f}亿" if abs(net_flow) >= 1e6 else f"{abs(net_flow)/1e4:.0f}万"
    return {
        "score": score,
        "detail": f"龙虎榜 {sector_name} {direction} {amt_str} → {score:+d}",
    }


def score_market_relative(kline_df, market_change_pct: float = 0) -> dict:
    """大盘相对强度 -10~+10.

    个股5日涨幅 vs 上证指数5日涨幅 = 超额收益(Beta-adjusted).
    连大盘都跑不赢的推荐→负分, 独立走强的→正分.
    """
    if kline_df is None or len(kline_df) < 5:
        return None
    c = kline_df["Close"]
    stock_ret = float((c.iloc[-1] / c.iloc[-5] - 1) * 100) if len(c) >= 5 else 0
    excess = stock_ret - market_change_pct
    if excess > 5:        score = 8.0
    elif excess > 2:      score = 5.0
    elif excess > 0:      score = 2.0
    elif excess > -2:     score = -2.0
    elif excess > -5:     score = -5.0
    else:                 score = -8.0
    return {"score": round(score, 1), "details": f"Excess={excess:+.1f}% vs SH"}


def score_fund_flow(kline_df):
    """资金面评分 0-10 — 量价关系与资金效率."""
    if kline_df is None or len(kline_df) < 20:
        return None  # 数据不足，不参与评分
    c = kline_df["Close"]
    v = kline_df.get("Volume", c * 1e6)

    # 1. 涨跌量比 (0-5分)
    up_vol = v[c.diff() > 0].sum()
    down_vol = v[c.diff() < 0].sum()
    ratio = up_vol / (down_vol + 1)
    # 使用对数尺度拉大区分度
    import math
    log_ratio = math.log(max(0.3, min(3.0, ratio)))
    flow_score = log_ratio * 4.0  # ratio=1→0, ratio=2→2.8, ratio=0.5→-2.8

    # 2. 量价配合度 (0-3分) — 放量上涨+缩量下跌=健康
    recent_n = min(10, len(c) // 2)
    up_days = c.diff().iloc[-recent_n:] > 0
    vol_recent = v.iloc[-recent_n:]
    vol_up_avg = vol_recent[up_days.values].mean() if up_days.any() else 0
    vol_down_avg = vol_recent[(~up_days).values].mean() if (~up_days).any() else vol_up_avg
    health_ratio = vol_up_avg / max(vol_down_avg, 1)
    if health_ratio > 1.8: health_score = 3.0
    elif health_ratio > 1.3: health_score = 2.0
    elif health_ratio > 1.0: health_score = 1.0
    elif health_ratio > 0.7: health_score = -1.0
    else: health_score = -3.0

    # 3. Turnover activity
    avg_vol = v.iloc[-20:].mean(); price = c.iloc[-1]
    turnover = avg_vol / (price * 1e6) if price > 0 else 0
    if 0.02 < turnover < 0.15: turn_score = 2.0
    elif 0.01 < turnover <= 0.02: turn_score = 1.0
    elif 0.005 < turnover <= 0.01: turn_score = 0.0
    elif turnover <= 0.005: turn_score = -2.0
    else: turn_score = -1.0

    total = flow_score + health_score + turn_score
    return {"score": round(float(np.clip(total, -10, 10)), 1)}


FUNDA_RULES = [
    ("roe", 15, 5, 5, -5, "gte", "excellent", "poor"),
    ("revenue_yoy", 20, 4, 0, -4, "gte", "excellent", "poor"),
    ("profit_yoy", 20, 4, 0, -4, "gte", "excellent", "poor"),
    ("debt_to_assets", 40, 3, 70, -3, "lte", "low_risk", "high_risk"),
    ("current_ratio", 2.0, 2, 1.0, -2, "gte", "good", "poor"),
    ("ocflow_net", 0, 2, 0, -2, "gt_pos", "positive", "negative"),
]

# ── 新增评分维度 ──────────────────────────────────

def score_valuation(pb: float | None, pe: float | None) -> dict:
    """估值评分 -10~+10 — PB + PE 综合位置."""
    score = 0.0  # 中性起点
    if pb is not None and pb > 0:
        if pb < 1.0: score += 3.0       # 破净，深度价值
        elif pb < 2.0: score += 2.0
        elif pb < 4.0: score += 0.5
        elif pb < 8.0: score -= 0.5
        elif pb < 15.0: score -= 1.5
        else: score -= 3.0              # PB极高，警惕
    if pe is not None and pe > 0:
        if pe < 10: score += 2.5        # 低PE价值
        elif pe < 20: score += 1.0
        elif pe < 40: score += 0.0
        elif pe < 80: score -= 1.0
        else: score -= 2.5              # 超高PE
    if pe is not None and pe < 0:
        score -= 2.0                     # 亏损
    return {"score": round(float(np.clip(score, -10, 10)), 1)}


_funda_cache: dict = {}
_funda_cache_ts: dict[str, float] = {}

async def get_fundamental_score(symbol: str):
    from app.core.database import async_session_factory as _s
    async with _s() as s:
        result = await s.execute(text(
            "SELECT roe,revenue_yoy,profit_yoy,debt_to_assets,current_ratio,ocflow_net,pb,pe_ttm "
            "FROM stock_fundamental_snapshot WHERE symbol=:sym"
        ), {"sym": symbol})
        row = result.fetchone()
    if not row:
        return 0, {"source": "no_data"}, None, None
    values = {
        "roe": float(row[0]) if row[0] is not None else None,
        "revenue_yoy": float(row[1]) if row[1] is not None else None,
        "profit_yoy": float(row[2]) if row[2] is not None else None,
        "debt_to_assets": float(row[3]) if row[3] is not None else None,
        "current_ratio": float(row[4]) if row[4] is not None else None,
        "ocflow_net": float(row[5]) if row[5] is not None else None,
    }
    pb = float(row[6]) if row[6] is not None else None
    pe = float(row[7]) if row[7] is not None else None
    score = 0
    detail = {}
    for key, gt, gp, bt, bp, cmp, gl, bl in FUNDA_RULES:
        v = values.get(key)
        if v is None:
            continue
        if cmp == "gte":
            ok, bad = v >= gt, v <= bt
        elif cmp == "lte":
            ok, bad = v <= gt, v >= bt
        else:
            ok, bad = v > gt, v < bt
        if ok:
            score += gp
            detail[key] = {"value": v, "score": gp, "level": gl}
        elif bad:
            score += bp
            detail[key] = {"value": v, "score": bp, "level": bl}
        else:
            detail[key] = {"value": v, "score": 0, "level": "neutral"}
    return max(-20, min(20, score)), detail, pb, pe


async def preload_fundamental_scores(symbols: list[str]):
    from app.core.database import async_session_factory as _s
    async with _s() as s:
        result = await s.execute(text(
            "SELECT symbol,roe,revenue_yoy,profit_yoy,debt_to_assets,current_ratio,ocflow_net,pb,pe_ttm "
            "FROM stock_fundamental_snapshot WHERE symbol=ANY(:syms)"
        ), {"syms": symbols})
        rows = {row[0]: row for row in result.fetchall()}
    for sym in symbols:
        row = rows.get(sym)
        if not row:
            _funda_cache[sym] = (0, {"source": "no_data"}, None, None)
            _funda_cache_ts[sym] = time.time()
            continue
        score = 0
        detail = {}
        for i, (key, gt, gp, bt, bp, cmp, gl, bl) in enumerate(FUNDA_RULES):
            v = float(row[i + 1]) if row[i + 1] is not None else None
            if v is None:
                continue
            if cmp == "gte":
                ok, bad = v >= gt, v <= bt
            elif cmp == "lte":
                ok, bad = v <= gt, v >= bt
            else:
                ok, bad = v > gt, v < bt
            if ok:
                score += gp
                detail[key] = {"value": v, "score": gp, "level": gl}
            elif bad:
                score += bp
                detail[key] = {"value": v, "score": bp, "level": bl}
        pb = float(row[7]) if row[7] is not None else None
        pe = float(row[8]) if row[8] is not None else None
        _funda_cache[sym] = (max(-20, min(20, score)), detail, pb, pe)
        _funda_cache_ts[sym] = time.time()


async def get_fundamental_score_cached(symbol: str):
    import time
    now = time.time()
    if symbol in _funda_cache and (now - _funda_cache_ts.get(symbol, 0)) < 3600:
        entry = _funda_cache[symbol]
        if len(entry) == 4:
            return entry  # (score, detail, pb, pe)
        return (entry[0], entry[1], None, None)
    result = await get_fundamental_score(symbol)
    _funda_cache[symbol] = result
    _funda_cache_ts[symbol] = now
    return result


