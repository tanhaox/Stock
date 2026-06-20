"""大神仙趋势 — 卖出方向指标 (v1.0).

基于四大经典技术指标的熊市信号组合:
  KDJ 死叉 (K < D, J < 20 为恐慌)
  MACD 柱转负 (dif < dea)
  均线空头 (MA5 < MA10 < MA20)
  RSI14 转弱 (< 50)

综合评分 0-4 分:
  0-1 分 = 正常 (无空头信号)
  2-3 分 = 偏空 (2+ 维度共振空头)
  4 分   = 强空 (全维度共振高点见顶)

用法:
  AlphaFlow: lockup结束 + TG买信号有效 → 查大神仙空 → 确认趋势是否终结
  持仓: 大神仙空 ≥ 3 分 → 退出信号
"""
import numpy as np
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory
import logging

logger = logging.getLogger("big_fairy")


def _big_fairy_from_arrays(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                           volumes: np.ndarray = None, symbol: str = "") -> Optional[dict]:
    """Pure numpy computation from pre-loaded arrays (no DB I/O)."""
    n = len(closes)
    if n < 60:
        return None

    result = {"symbol": symbol, "n_bars": n}

    # ═══ 1. KDJ (9,3,3) ═══
    kdj_n = min(9, n - 1)
    h9 = np.array([highs[max(0, i - kdj_n + 1):i + 1].max() for i in range(n)])
    l9 = np.array([lows[max(0, i - kdj_n + 1):i + 1].min() for i in range(n)])
    rsv = (closes - l9) / np.maximum(h9 - l9, 0.0001) * 100

    k = np.zeros(n); d = np.zeros(n)
    k[0] = d[0] = 50
    for i in range(1, n):
        k[i] = 2.0 / 3.0 * k[i - 1] + 1.0 / 3.0 * rsv[i]
        d[i] = 2.0 / 3.0 * d[i - 1] + 1.0 / 3.0 * k[i]
    j_val = 3 * k - 2 * d

    kdj_dead = bool(k[-1] < d[-1])
    j_overbought = bool(j_val[-1] > 80)

    result["k"] = round(float(k[-1]), 1)
    result["d"] = round(float(d[-1]), 1)
    result["j"] = round(float(j_val[-1]), 1)

    # ═══ 2. MACD (12-26-9) ═══
    def _ema(series, span):
        alpha = 2.0 / (span + 1)
        out = np.zeros_like(series)
        out[0] = series[:span].mean() if len(series) >= span else series[0]
        for i in range(1, len(series)):
            out[i] = alpha * series[i] + (1 - alpha) * out[i - 1]
        return out

    dif = _ema(closes, 12) - _ema(closes, 26)
    dea = _ema(dif, 9)
    macd_hist = 2 * (dif - dea)

    result["macd_hist"] = round(float(macd_hist[-1]), 4)
    result["dif"] = round(float(dif[-1]), 4)

    # ═══ 3. MA 均线排列 ═══
    ma5 = float(closes[-5:].mean())
    ma10 = float(closes[-10:].mean()) if n >= 10 else ma5
    ma20 = float(closes[-20:].mean()) if n >= 20 else ma10

    ma_bear = bool(closes[-1] < ma20)

    result["close"] = round(float(closes[-1]), 2)
    result["ma5"] = round(ma5, 2)
    result["ma10"] = round(ma10, 2)
    result["ma20"] = round(ma20, 2)

    # ═══ 4. RSI14 ═══
    gains = np.maximum(np.diff(closes, prepend=closes[0]), 0)
    losses = np.maximum(-np.diff(closes, prepend=closes[0]), 0)

    def _rma(s, p):
        alpha = 2.0 / (p + 1)
        out = np.zeros_like(s)
        out[0] = s[max(0, p - 30):p].mean()
        for i in range(1, len(s)):
            out[i] = alpha * s[i] + (1 - alpha) * out[i - 1]
        return out

    avg_gain = _rma(gains, 14)
    avg_loss = _rma(losses, 14)
    rs = avg_gain / np.maximum(avg_loss, 0.0001)
    rsi = 100 - 100 / (1 + rs)

    rsi_weak = bool(rsi[-1] < 50)

    result["rsi14"] = round(float(rsi[-1]), 1)

    # ═══ 5. 量价关系 ═══
    if volumes is not None and len(volumes) >= 21:
        vol_ma5 = float(volumes[-6:-1].mean()) if n >= 6 else volumes[-1]
        vol_ma20 = float(volumes[-21:-1].mean()) if n >= 21 else vol_ma5
        vol_ratio_5 = volumes[-1] / vol_ma5 if vol_ma5 > 0 else 1.0
        vol_ratio_20 = volumes[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
        vol_5d_trend = vol_ma5 / vol_ma20 if vol_ma20 > 0 else 1.0
    else:
        vol_ma5 = vol_ma20 = volumes[-1] if volumes is not None and len(volumes) > 0 else 0.0
        vol_ratio_5 = vol_ratio_20 = vol_5d_trend = 1.0

    # ═══ 6. 短期动量 ═══
    chg_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if n >= 2 and closes[-2] > 0 else 0
    chg_5d = (closes[-1] - closes[-6]) / closes[-6] * 100 if n >= 6 and closes[-6] > 0 else 0
    chg_10d = (closes[-1] - closes[-11]) / closes[-11] * 100 if n >= 11 and closes[-11] > 0 else 0

    # ═══ 综合打分 v2.0（6维度：KDJ + MACD + MA + RSI + 量价 + 动量）═══
    score = 0
    dimensions = []

    # ── 超买回落前置判断 ──
    # J currently overbought (>80) AND declining from recent peak
    j_overbought_now = bool(j_val[-1] > 80)
    j_turning = bool(j_val[-1] < j_val[-3]) if n >= 3 else False
    # RSI currently elevated (>70) AND declining
    rsi_elevated = bool(rsi[-1] > 70)
    rsi_turning = bool(rsi[-1] < rsi[-3]) if n >= 3 else False

    # ── 维度1: KDJ ──
    kdj_dead = bool(k[-1] < d[-1])
    kdj_oversold = bool(k[-1] < 20 or d[-1] < 20 or j_val[-1] < 0)
    kdj_extreme = bool(j_val[-1] > 100)  # J值破百，无条件警告
    if kdj_dead and j_overbought:
        score += 1
        dimensions.append("KDJ高位死叉")
    elif kdj_dead:
        score += 1
        dimensions.append("KDJ死叉")
    elif kdj_oversold:
        score += 1
        dimensions.append("KDJ超卖钝化")
    elif kdj_extreme:
        score += 1
        dimensions.append("KDJ极端超买")
    elif j_overbought_now and j_turning:
        score += 1
        dimensions.append("KDJ超买回落")

    # ── 维度2: MACD ──
    if macd_hist[-1] < 0:
        score += 1
        dimensions.append("MACD柱转负")
    elif dif[-1] < dea[-1]:
        score += 1
        dimensions.append("MACD死叉")

    # ── 维度3: MA 均线 ──
    ma_close_bear = bool(closes[-1] < ma20)
    ma_cross_bear = bool(ma5 < ma10)
    ma_below_ma5 = bool(closes[-1] < ma5)  # 跌穿短线支撑
    if ma_close_bear and ma_cross_bear:
        score += 1
        dimensions.append("均线空头排列")
    elif ma_cross_bear:
        score += 1
        dimensions.append("短均死叉")
    elif ma_close_bear:
        score += 1
        dimensions.append("跌破MA20")
    elif ma_below_ma5 and chg_1d < -1:  # 跌穿MA5需伴随实际下跌
        score += 1
        dimensions.append("跌穿MA5")

    # ── 维度4: RSI ──
    if rsi[-1] < 40:
        score += 1
        dimensions.append("RSI弱势")
    elif rsi[-1] < 50:
        score += 1
        dimensions.append("RSI转弱")
    elif rsi_elevated and rsi_turning:
        score += 1
        dimensions.append("RSI高位回落")

    # ── 维度5: 量价关系 ──
    vol_spike_down = bool(vol_ratio_5 > 1.3 and chg_1d < -1.5)
    vol_shrink = bool(vol_5d_trend < 0.65)
    vol_stall = bool(vol_ratio_5 > 1.5 and abs(chg_1d) < 0.8)
    if vol_spike_down:
        score += 1
        dimensions.append("放量下跌")
    elif vol_stall:
        score += 1
        dimensions.append("放量滞涨")
    elif vol_shrink:
        score += 1
        dimensions.append("持续缩量")

    # ── 维度6: 短期动量 ──
    big_red = bool(chg_1d < -5)
    weak_short = bool(chg_5d < -3)
    exhaustion = bool(chg_5d > 3 and chg_1d < -1.5)  # 连涨后回落
    if big_red:
        score += 1
        dimensions.append("单日大阴")
    elif weak_short:
        score += 1
        dimensions.append("短期转弱")
    elif exhaustion:
        score += 1
        dimensions.append("涨势衰竭")

    # ── 维度7: 复合超买（多指标共振见顶）──
    j_extreme = bool(j_val[-1] > 95)
    rsi_ob = bool(rsi[-1] > 75)
    price_extended = bool(closes[-1] > ma20 * 1.12) if ma20 > 0 else False
    rally_big = bool(chg_10d > 8)
    ob_count = sum([j_extreme, rsi_ob, price_extended, rally_big])
    j_was_extreme = bool(np.any(j_val[-6:] > 105))
    if ob_count >= 3 and (j_turning or rsi_turning):
        score += 1
        dimensions.append("多指标超买")
    elif ob_count >= 2 and j_was_extreme and j_turning:
        score += 1
        dimensions.append("极端超买回归")

    if score >= 3:
        signal = "strong_sell"
    elif score >= 2:
        signal = "sell"
    elif score >= 1:
        signal = "weak"
    else:
        signal = "normal"

    result["score"] = score
    result["bearish"] = score >= 2
    result["signal"] = signal
    result["dimensions"] = dimensions
    result["details"] = {
        "kdj": f"K={k[-1]:.1f} D={d[-1]:.1f} J={j_val[-1]:.1f} ({'死叉' if kdj_dead else '金叉'})",
        "macd": f"DIF={dif[-1]:.3f} DEA={dea[-1]:.3f} hist={macd_hist[-1]:.3f} ({'空' if macd_hist[-1] < 0 else '多'})",
        "ma": f"MA5={ma5:.2f} MA10={ma10:.2f} MA20={ma20:.2f} ({'空头' if closes[-1] < ma20 else '多头'})",
        "rsi": f"RSI14={rsi[-1]:.1f} ({'弱' if rsi[-1] < 50 else '强'})",
    }

    return result


async def compute_big_fairy(symbol: str, lookback: int = 120, session=None) -> Optional[dict]:
    """计算大神仙空头评分.

    Returns:
        {score, bearish, dimensions, details, signal, close, ma20, ma5, j, k, d, macd_hist, rsi14}
        score: 0-4 空头强度
        bearish: score >= 2
        signal: "strong_sell" / "sell" / "weak" / "normal"
    """
    own_s = session is None

    if own_s:
        s_ctx = async_session_factory()
        await s_ctx.__aenter__()
    else:
        s_ctx = session

    try:
        r = await s_ctx.execute(text(
            "SELECT close, high, low FROM daily_kline "
            "WHERE ts_code = :c ORDER BY trade_date DESC LIMIT :lim"
        ), {"c": symbol, "lim": lookback + 60})
        rows = list(reversed(r.fetchall()))

        if len(rows) < 60:
            return None

        closes = np.array([float(r[0] or 0) for r in rows])
        highs = np.array([float(rows[i][1] or closes[i]) for i in range(len(rows))])
        lows = np.array([float(rows[i][2] or closes[i]) for i in range(len(rows))])
        return _big_fairy_from_arrays(closes, highs, lows, symbol)

    finally:
        if own_s:
            await s_ctx.__aexit__(None, None, None)


async def batch_compute_big_fairy(symbols: list[str]) -> dict[str, dict]:
    """批量计算大神仙空头评分."""
    results = {}
    for i, sym in enumerate(symbols):
        try:
            bf = await compute_big_fairy(sym)
            if bf:
                results[sym] = bf
        except Exception as e:
            logger.debug(f"Big Fairy {sym}: {e}")
        if (i + 1) % 50 == 0:
            logger.info(f"Big Fairy batch: {i + 1}/{len(symbols)}")
    return results


async def get_big_fairy_for_holdings() -> dict[str, dict]:
    """获取持仓股票的大神仙信号 (P0-持仓系统集成)."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT symbol FROM holdings"))
        symbols = [row[0] for row in r.fetchall()]
    if not symbols:
        return {}
    return await batch_compute_big_fairy(symbols[:50])
