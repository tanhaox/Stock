"""多周期验证器 — 周线/月线跨周期MACD + 位置判断 + 背离检测.

P1 v1.0 (2026-05-31):
  - 周线 MACD 方向 + 金叉/死叉检测
  - 月线位置: 当前价格在月线 MA20 上方/下方
  - 跨周期背离: 日线创新低但周线 MACD 不再创新低 = 底背离加分
  - 大周期压力位: 月线级别下降趋势线/前高
"""
import logging, numpy as np
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tdx_functions import EMA

logger = logging.getLogger("multi_timeframe")


def _calc_macd(closes, fast=12, slow=26, signal=9):
    """MACD 计算."""
    c_series = pd.Series(closes)
    ema_fast = EMA(c_series, fast).values
    ema_slow = EMA(c_series, slow).values
    diff = ema_fast - ema_slow
    dea = EMA(pd.Series(diff), signal).values
    macd = 2 * (diff - dea)
    return diff, dea, macd


async def verify_multi_timeframe(symbol: str) -> dict:
    """跨周期验证 — 日线信号在周线/月线视角下的可信度.

    Returns:
        {
            weekly: {macd_direction, golden_dead_cross, position_vs_ma20, ...},
            monthly: {position_vs_ma20, long_term_trend, overhead_resistance, ...},
            divergence: {type, strength, description},
            verdict: 'confirm'|'caution'|'warn'|'reject',
            adjustment: -5~+5  (composite_score 修正量),
        }
    """
    async with async_session_factory() as s:
        # 日线 (最近 250 天)
        r = await s.execute(text("""
            SELECT trade_date, close, high, low
            FROM daily_kline WHERE ts_code = :s
            ORDER BY trade_date DESC LIMIT 250
        """), {"s": symbol})
        daily = list(reversed(r.fetchall()))

    if len(daily) < 120:
        return {"verdict": "insufficient", "adjustment": 0,
                "reason": f"日线不足({len(daily)}天, 需≥120)"}

    d_closes = np.array([float(r[1] or 0) for r in daily])
    d_highs = np.array([float(r[2] or d_closes[i]) for i, r in enumerate(daily)])
    d_lows = np.array([float(r[3] or d_closes[i]) for i, r in enumerate(daily)])

    n = len(d_closes)

    # ── 周线构造 (每 5 个交易日聚合为 1 根周线) ──
    w_closes, w_highs, w_lows = [], [], []
    for i in range(0, n, 5):
        chunk_c = d_closes[i:min(i+5, n)]
        chunk_h = d_highs[i:min(i+5, n)]
        chunk_l = d_lows[i:min(i+5, n)]
        if len(chunk_c) >= 3:
            w_closes.append(float(chunk_c[-1]))
            w_highs.append(float(np.max(chunk_h)))
            w_lows.append(float(np.min(chunk_l)))

    if len(w_closes) < 20:
        return {"verdict": "insufficient", "adjustment": 0,
                "reason": f"周线不足({len(w_closes)}周, 需≥20)"}

    w_closes = np.array(w_closes)

    # 周线 MACD
    w_diff, w_dea, w_macd = _calc_macd(w_closes, 12, 26, 9)
    w_macd_now = float(w_macd[-1])
    w_macd_prev = float(w_macd[-2]) if len(w_macd) >= 2 else 0
    w_diff_now = float(w_diff[-1])
    w_dea_now = float(w_dea[-1])

    # 周线 MA20
    w_ma20 = float(np.mean(w_closes[-20:])) if len(w_closes) >= 20 else float(np.mean(w_closes))
    w_position = (w_closes[-1] - w_ma20) / max(w_ma20, 0.01) * 100

    # 周线金叉/死叉 (近 3 周内)
    w_golden_cross = False
    w_dead_cross = False
    for offset in [0, 1, 2]:
        idx = len(w_diff) - 1 - offset
        if idx < 1: continue
        if w_diff[idx-1] <= w_dea[idx-1] and w_diff[idx] > w_dea[idx]:
            w_golden_cross = True
        if w_diff[idx-1] >= w_dea[idx-1] and w_diff[idx] < w_dea[idx]:
            w_dead_cross = True

    # 周线判定
    if w_macd_now > 0 and w_macd_now > w_macd_prev and w_position > 0:
        weekly_verdict = "bullish"
    elif w_macd_now > 0 and w_position > -3:
        weekly_verdict = "neutral_positive"
    elif w_macd_now < 0 and w_macd_now < w_macd_prev:
        weekly_verdict = "bearish"
    elif w_macd_now < 0:
        weekly_verdict = "neutral_negative"
    else:
        weekly_verdict = "neutral"

    weekly = {
        "macd_now": round(w_macd_now, 4),
        "macd_direction": "up" if w_macd_now > w_macd_prev else "down",
        "golden_cross": w_golden_cross,
        "dead_cross": w_dead_cross,
        "position_vs_ma20": round(w_position, 1),
        "verdict": weekly_verdict,
        "bars": len(w_closes),
    }

    # ── 月线构造 (每 20 个交易日聚合) ──
    m_closes = []
    for i in range(0, n, 20):
        chunk = d_closes[i:min(i+20, n)]
        if len(chunk) >= 10:
            m_closes.append(float(chunk[-1]))

    monthly = {}
    if len(m_closes) >= 6:
        m_closes = np.array(m_closes)
        m_ma20 = float(np.mean(m_closes[-6:]))
        m_position = (m_closes[-1] - m_ma20) / max(m_ma20, 0.01) * 100

        # 月线趋势: 近 6 月斜率
        m_slope = float(np.polyfit(range(len(m_closes[-6:])), m_closes[-6:], 1)[0]) if len(m_closes) >= 6 else 0
        m_trend = "上升" if m_slope > 0.02 else ("下降" if m_slope < -0.02 else "横盘")

        # 月线压力位: 近 12 个月的最高点
        m_high_12m = float(np.max(m_closes[-12:])) if len(m_closes) >= 12 else float(np.max(m_closes))
        m_dist_to_high = (m_high_12m - m_closes[-1]) / max(m_closes[-1], 0.01) * 100

        # 月线支撑: 近 12 个月的最低点
        m_low_12m = float(np.min(m_closes[-12:])) if len(m_closes) >= 12 else float(np.min(m_closes))
        m_dist_to_low = (m_closes[-1] - m_low_12m) / max(m_low_12m, 0.01) * 100

        monthly = {
            "position_vs_ma20": round(m_position, 1),
            "long_term_trend": m_trend,
            "overhead_resistance": round(m_high_12m, 2),
            "dist_to_high_pct": round(m_dist_to_high, 1),
            "major_support": round(m_low_12m, 2),
            "dist_to_low_pct": round(m_dist_to_low, 1),
            "bars": len(m_closes),
        }
    else:
        monthly = {"verdict": "insufficient", "bars": len(m_closes)}

    # ── 跨周期背离检测 ──
    divergence = {"type": "none", "strength": 0, "description": ""}

    # 底背离: 日线近 20 日低点低于前 40 日的低点, 但周线 MACD 不创新低
    if n >= 60:
        recent_low = float(np.min(d_lows[-20:]))
        earlier_low = float(np.min(d_lows[-60:-20]))
        w_macd_4w_ago = float(np.mean(w_macd[-5:-1])) if len(w_macd) >= 5 else w_macd_now
        w_macd_8w_ago = float(np.mean(w_macd[-9:-5])) if len(w_macd) >= 9 else w_macd_4w_ago

        if recent_low < earlier_low * 0.98 and w_macd_now > w_macd_8w_ago:
            divergence = {
                "type": "bullish_divergence",
                "strength": 2.5,
                "description": "日线新低但周线MACD拒绝新低 — 底背离, 大资金在吸筹",
            }
        # 顶背离: 日线近 20 日高点高于前 40 日, 但周线 MACD 不创新高
        recent_high = float(np.max(d_highs[-20:]))
        earlier_high = float(np.max(d_highs[-60:-20]))
        if recent_high > earlier_high * 1.02 and w_macd_now < w_macd_8w_ago:
            divergence = {
                "type": "bearish_divergence",
                "strength": -3.0,
                "description": "日线新高但周线MACD不再跟随 — 顶背离, 大资金在出货",
            }

    # ── 综合判定 ──
    adjustment = 0.0
    details = []

    # 周线贡献
    if weekly["verdict"] == "bullish":
        adjustment += 3.0
        details.append("周线多头共振")
    elif weekly["verdict"] == "neutral_positive":
        adjustment += 1.0
        details.append("周线偏多")
    elif weekly["verdict"] == "bearish":
        adjustment -= 4.0
        details.append("⚠ 周线空头 — 日线信号可能是诱多")
    elif weekly["verdict"] == "neutral_negative":
        adjustment -= 1.5
        details.append("周线偏弱")

    # 周线金叉/死叉
    if w_golden_cross:
        adjustment += 2.0
        details.append("周线MACD金叉")
    if w_dead_cross:
        adjustment -= 3.0
        details.append("⚠ 周线MACD死叉")

    # 月线贡献
    if monthly.get("verdict") != "insufficient":
        if monthly.get("long_term_trend") == "下降":
            adjustment -= 2.0
            details.append(f"月线下降趋势, 压力位¥{monthly.get('overhead_resistance',0):.2f}")
        elif monthly.get("long_term_trend") == "上升":
            adjustment += 1.5
            details.append("月线上升趋势")

        dist_high = monthly.get("dist_to_high_pct", 100)
        if dist_high < 10:
            adjustment -= 1.5
            details.append(f"距月线压力仅{dist_high:.0f}%, 空间有限")

    # 背离贡献
    if divergence["type"] == "bullish_divergence":
        adjustment += divergence["strength"]
        details.append(divergence["description"])
    elif divergence["type"] == "bearish_divergence":
        adjustment += divergence["strength"]
        details.append(divergence["description"])

    adjustment = round(max(-8, min(8, adjustment)), 1)

    if adjustment >= 4:
        verdict = "confirm"
    elif adjustment >= 1:
        verdict = "caution"
    elif adjustment >= -3:
        verdict = "warn"
    else:
        verdict = "reject"

    return {
        "weekly": _sanitize_dict(weekly),
        "monthly": _sanitize_dict(monthly),
        "divergence": divergence,
        "verdict": verdict,
        "adjustment": adjustment,
        "reason": "; ".join(details) if details else "多周期一致",
    }


def _sanitize_dict(d: dict) -> dict:
    """递归转换 numpy 值为原生 Python 类型."""
    import numpy as np
    result = {}
    for k, v in d.items():
        if isinstance(v, (np.floating, np.integer)):
            result[k] = float(v) if isinstance(v, np.floating) else int(v)
        elif isinstance(v, np.bool_):
            result[k] = bool(v)
        elif isinstance(v, np.ndarray):
            result[k] = v.tolist()
        elif isinstance(v, dict):
            result[k] = _sanitize_dict(v)
        else:
            result[k] = v
    return result
