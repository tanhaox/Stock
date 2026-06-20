"""技术面评分函数 — 从 deep_scorer.py 拆分 (v4.3)."""
import numpy as np
import pandas as pd
from app.services.tdx_functions import MA, EMA, REF, HHV, LLV, STD, CROSS, calc_rsi

def _sma(arr, p):
    """Simple Moving Average helper."""
    if len(arr) < p:
        return np.full_like(arr, np.nan)
    r = np.full_like(arr, np.nan)
    cs = np.cumsum(np.insert(arr, 0, 0))
    r[p-1:] = (cs[p:] - cs[:-p]) / p
    return r


def score_technical(kline_df):
    """技术面评分 -10~+10 — 超卖反弹=正分, 过热追高=负分."""
    if kline_df is None or len(kline_df) < 20:
        return None  # 数据不足，不参与评分
    c = kline_df["Close"]
    v = kline_df.get("Volume", c * 1e6)
    h = kline_df["High"]
    l = kline_df["Low"]

    # 1. RSI (0-30→3.5分, 30-50→2.5分, 50-70→1.5分, >70→0.5分)
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, 1)
    rsi = 100 - 100 / (1 + rs)
    # 修正: avg_loss=0时RSI应为100(纯上涨，无下跌)
    rsi = rsi.where(avg_loss > 0, 100.0)
    last_rsi = rsi.iloc[-1]
    if last_rsi < 25: rsi_score = 4.0
    elif last_rsi < 35: rsi_score = 3.0
    elif last_rsi < 45: rsi_score = 1.5
    elif last_rsi < 55: rsi_score = 0.0
    elif last_rsi < 65: rsi_score = -1.0
    elif last_rsi < 75: rsi_score = -2.5
    else: rsi_score = -4.0

    # 2. MACD 趋势+背离 (0-2.5分)
    diff = EMA(c, 12) - EMA(c, 26)
    dea = EMA(diff, 9)
    macd = 2 * (diff - dea)
    macd_score = 0.0
    if macd.iloc[-1] > 0:
        macd_score += 1.5
        if macd.iloc[-1] > macd.iloc[-2]: macd_score += 1.0
        if macd.iloc[-1] > 0 and macd.iloc[-3] < 0: macd_score += 1.5  # 金叉
    else:
        macd_score -= 1.5
        if macd.iloc[-1] < macd.iloc[-2]: macd_score -= 1.0  # 加速下跌
        if macd.iloc[-1] < 0 and macd.iloc[-3] > 0: macd_score -= 1.5  # 死叉

    # 3. 量比 (0-2分)
    vol5 = v.rolling(5).mean()
    vol20 = v.rolling(20).mean()
    vr = vol5.iloc[-1] / vol20.iloc[-1] if vol20.iloc[-1] > 0 else 1
    if 1.5 < vr <= 2.5: vol_score = 2.5
    elif 1.0 < vr <= 1.5: vol_score = 1.5
    elif 0.8 <= vr <= 1.0: vol_score = 0.0
    elif 0.5 <= vr < 0.8: vol_score = -1.0
    elif vr > 4.0: vol_score = -2.5
    elif vr < 0.3: vol_score = -3.0
    else: vol_score = -1.5

    # 4. 布林带位置 (0-2分)
    ma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    bb_pos = (c.iloc[-1] - ma20.iloc[-1]) / std20.iloc[-1] if std20.iloc[-1] > 0 else 0
    if -2 < bb_pos < -1: bb_score = 2.5
    elif -1 <= bb_pos < -0.3: bb_score = 1.5
    elif -0.3 <= bb_pos <= 0.3: bb_score = 0.0
    elif 0.3 < bb_pos <= 1: bb_score = -1.0
    elif 1 < bb_pos <= 2: bb_score = -2.0
    else: bb_score = -3.0

    total = rsi_score + macd_score + vol_score + bb_score
    return {"score": round(float(np.clip(total, -10, 10)), 1), "detail": f"RSI={last_rsi:.0f} MACD={macd_score:.1f} VR={vr:.2f} BB={bb_pos:.2f}"}


def score_kline_game(kline_df):
    """K线博弈评分 -10~+10 — 多空力量对比."""
    if kline_df is None or len(kline_df) < 20:
        return None
    c = kline_df["Close"]; o = kline_df["Open"]
    h = kline_df["High"]; l = kline_df["Low"]

    recent = min(20, len(kline_df))
    bull_days = (c.iloc[-recent:] > o.iloc[-recent:]).sum()
    bull_ratio = bull_days / recent
    if bull_ratio >= 0.70: bull_score = 3.0
    elif bull_ratio >= 0.55: bull_score = 2.0
    elif bull_ratio >= 0.40: bull_score = 1.0
    elif bull_ratio >= 0.25: bull_score = -1.0
    else: bull_score = -3.0

    body = (c - o).abs(); spread = (h - l).replace(0, 0.01)
    body_ratio = (body / spread).iloc[-5:].mean()
    if body_ratio > 0.7: br_score = 2.5
    elif body_ratio > 0.5: br_score = 2.0
    elif body_ratio > 0.3: br_score = 1.0
    elif body_ratio > 0.15: br_score = -1.5
    else: br_score = -3.0

    high_20 = h.iloc[-20:].max(); low_20 = l.iloc[-20:].min()
    close_last = c.iloc[-1]
    if close_last >= high_20 * 0.98: break_score = 2.5
    elif close_last >= high_20 * 0.93: break_score = 1.5
    elif close_last >= high_20 * 0.85: break_score = 0.5
    elif close_last <= low_20 * 1.05: break_score = -3.5
    else: break_score = 0.0

    streak = 0
    for i in range(len(c) - 1, -1, -1):
        if c.iloc[i] > o.iloc[i]: streak += 1
        else: break
    streak_score = min(2.0, streak * 0.5)

    total = bull_score + br_score + break_score + streak_score
    return {"score": round(float(np.clip(total, -10, 10)), 1)}


def score_vol_ratio(kline_df):
    """量比评分 0-10 — 量比=当日成交量/20日均量，衡量资金关注度.

    1.0-2.0为黄金区间(温和放量)，>3.0警惕对倒，<0.5无人气.
    """
    if kline_df is None or len(kline_df) < 20:
        return None
    v = kline_df.get("Volume", None)
    if v is None or len(v) < 20:
        return None
    avg_vol_20 = v.rolling(20).mean().iloc[-1]
    if avg_vol_20 <= 0:
        return None
    vol_ratio = float(v.iloc[-1] / avg_vol_20)
    # 量比→分数映射: 1.0-2.0最优, 极端值降分
    if 1.5 < vol_ratio <= 2.5:   score = 7.0   # 健康放量，资金关注
    elif 0.8 <= vol_ratio <= 1.5: score = 2.0   # 正常活跃
    elif 2.5 < vol_ratio <= 4.0:  score = -1.0  # 过度放量
    elif 0.5 <= vol_ratio < 0.8:  score = -2.0  # 偏低迷
    elif vol_ratio > 4.0:         score = -5.0  # 极端放量
    else:                         score = -6.0  # <0.5，无人气
    return {"score": round(score, 1), "details": f"量比={vol_ratio:.1f}"}


def score_arbr(kline_df):
    """ARBR情绪指标 0-10 — AR人气+BR意愿+交叉信号.

    金叉(AR上穿BR)=底部反转, 死叉(AR下穿BR)=高位风险.
    """
    if kline_df is None or len(kline_df) < 30:
        return None
    o = kline_df["Open"]; h = kline_df["High"]
    l = kline_df["Low"]; c = kline_df["Close"]
    pc = c.shift(1)
    n = 26
    h_o = (h - o).clip(lower=0).rolling(n).sum()
    o_l = (o - l).clip(lower=0).rolling(n).sum()
    ar = float((h_o / o_l.replace(0, 1e-9) * 100).iloc[-1])
    h_pc = (h - pc).clip(lower=0).rolling(n).sum()
    pc_l = (pc - l).clip(lower=0).rolling(n).sum()
    br = float((h_pc / pc_l.replace(0, 1e-9) * 100).iloc[-1])
    # 近3日交叉检测
    ar_s = (h_o / o_l.replace(0, 1e-9) * 100).iloc[-4:]
    br_s = (h_pc / pc_l.replace(0, 1e-9) * 100).iloc[-4:]
    gold = any(ar_s.iloc[i] <= br_s.iloc[i] and ar_s.iloc[i+1] > br_s.iloc[i+1] for i in range(3))
    dead = any(ar_s.iloc[i] >= br_s.iloc[i] and ar_s.iloc[i+1] < br_s.iloc[i+1] for i in range(3))
    score = 0.0
    if br > ar and 80 <= br <= 200:     score = 6.0
    elif br > ar and br > 200:          score = 2.0
    elif ar > br and ar > 200:          score = -5.0
    elif ar < 50 and br < 60:           score = 3.0
    if gold and ar < 120:              score += 2.0
    if gold and ar < 70:               score += 1.5
    if dead and br > 200:              score -= 3.0
    if dead and br > 300:              score -= 3.0
    return {"score": round(float(np.clip(score, -10, 10)), 1), "details": f"AR={ar:.0f} BR={br:.0f}"}



def score_bbi(kline_df) -> dict:
    """BBI指标 -10~+10 — (MA3+MA6+MA12+MA24)/4 多空分界线.

    价格在BBI上方且BBI上行 → 多头趋势; 下方且下行 → 空头.
    偏离度 + 斜率 + 交叉信号 → 综合评分.
    """
    import numpy as np
    if kline_df is None or len(kline_df) < 30:
        return None
    closes = kline_df["Close"].values
    n = len(closes)

    bbi = (_sma(closes, 3) + _sma(closes, 6) + _sma(closes, 12) + _sma(closes, 24)) / 4

    # 偏离度: (价格-BBI)/BBI * 100
    dev = np.full(n, 0.0)
    mask = ~np.isnan(bbi) & (bbi > 0)
    dev[mask] = (closes[mask] - bbi[mask]) / bbi[mask] * 100
    latest_dev = float(dev[-1]) if not np.isnan(dev[-1]) else 0

    # BBI斜率 (近10日)
    if n >= 10 and not np.isnan(bbi[-1]) and not np.isnan(bbi[-10]) and bbi[-10] > 0:
        slope = (bbi[-1] - bbi[-10]) / bbi[-10] * 100
    else:
        slope = 0

    # 交叉信号: 近5日是否有金叉/死叉
    cross_signal = 0
    for i in range(max(0, n-5), n):
        if np.isnan(bbi[i]) or np.isnan(bbi[i-1]) if i > 0 else True:
            continue
        if i > 0 and closes[i] > bbi[i] and closes[i-1] <= bbi[i-1]:
            cross_signal += 1  # 金叉
        elif i > 0 and closes[i] < bbi[i] and closes[i-1] >= bbi[i-1]:
            cross_signal -= 1  # 死叉

    # 综合评分 — v2: 方向修正
    # 偏离方向: close > BBI → 多头但可能追高; close < BBI → 空头但可能超跌
    # 低价在 BBI 下方 = 便宜的买入点 → 给高分; 高溢价在 BBI 上方 = 追高风险 → 低调分
    # 核心: 偏离越大越危险(无论方向), 但向下偏离(买入机会)比向上偏离(追高风险)扣分少
    abs_dev = abs(latest_dev)
    if latest_dev < 0:  # 价格在 BBI 下方 → 可能存在买入机会
        dev_score = np.clip(5.0 + abs_dev * 0.5, 3, 9)  # 向下偏离加分但封顶 9
    else:  # 价格在 BBI 上方 → 偏强但追高有风险
        dev_score = np.clip(5.0 - latest_dev * 0.8, 0, 8)
    slope_bonus = np.clip(slope * 0.5, -2, 2)
    cross_bonus = np.clip(cross_signal * 0.5, -1, 1)
    score = dev_score + slope_bonus + cross_bonus

    return {"score": round(float(np.clip(score, 0, 10)), 1),
            "detail": f"BBI dev={latest_dev:.1f}% slope={slope:.1f}% cross={cross_signal}",
            "bbi_deviation": round(float(latest_dev), 2)}


def score_trend_deviation(kline_df) -> dict:
    """趋势偏离度 -10~+10 — 同花顺10层递归自适应信号线.

    核心: (MA3+MA7+MA13+MA27)/4 趋势基线 + (H+L+2O+6C)/10 加权价格
    → 10层递归微调 ±2% → 偏离度 + 方向稳定性 → 综合评分.
    """
    import numpy as np
    if kline_df is None or len(kline_df) < 30:
        return None
    closes = kline_df["Close"].values; opens = kline_df["Open"].values
    highs = kline_df["High"].values; lows = kline_df["Low"].values
    n = len(closes)

    ma3 = _sma(closes, 3); ma7 = _sma(closes, 7)
    ma13 = _sma(closes, 13); ma27 = _sma(closes, 27)
    x1 = (ma3 + ma7 + ma13 + ma27) / 4

    alpha = 2 / 6.0
    x2 = np.full_like(closes, closes[0])
    for i in range(1, n): x2[i] = alpha * closes[i] + (1 - alpha) * x2[i-1]
    x3 = np.where(~np.isnan(x1), x1, x2)  # 趋势基线

    x4 = (highs + lows + 2 * opens + 6 * closes) / 10  # 加权价格

    # 弱势/强势K线形态
    x5 = np.zeros(n, dtype=bool); x6 = np.zeros(n, dtype=bool)
    for i in range(1, n):
        c, o, h, l = closes[i], opens[i], highs[i], lows[i]
        rc = closes[i-1]; rh = highs[i-1]; rl = lows[i-1]
        x5[i] = (c < o) or (c < rh and c > o) or (c >= o and h-c >= c-o and c/rc < 1.02) or (c == o and h-c >= c-l and c/rc < 1.05)
        x6[i] = (c > o and c/rc > 0.94) or (c > rl and c < o) or (c <= o and c-l >= o-c and c/rc > 0.98) or (c == o and c-l >= h-c and c/rc > 0.95)

    # 10层递归
    signal = x4.copy()
    for _ in range(10):
        new_sig = signal.copy()
        for i in range(1, n):
            if np.isnan(x3[i]) or np.isnan(signal[i]) or np.isnan(x3[i-1]) or np.isnan(signal[i-1]):
                continue
            if signal[i] > x3[i] and signal[i-1] <= x3[i-1] and x5[i]:
                new_sig[i] = x3[i] * 0.98
            elif signal[i] < x3[i] and signal[i-1] >= x3[i-1] and x6[i]:
                new_sig[i] = x3[i] * 1.02
        signal = new_sig

    # 偏离度
    dev = np.full(n, 0.0)
    m = ~np.isnan(x3) & ~np.isnan(signal) & (x3 > 0)
    dev[m] = (signal[m] / x3[m] - 1) * 100

    # 稳定性: 近30天内方向切换次数
    w = min(30, n); changes = 0; prev = 0
    for i in range(n - w, n):
        d = 1 if signal[i] >= x3[i] else -1
        if prev != 0 and d != prev: changes += 1
        prev = d
    stability = max(0, 10 - changes)
    latest = float(dev[-1]) if not np.isnan(dev[-1]) else 0

    if abs(latest) < 1:
        score = 0.0
    else:
        dev_s = np.clip(5.0 + latest * 0.5, 0, 10)
        score = dev_s * 0.6 + stability * 0.4
    if latest < 0: score -= 3.0  # bearish deviation penalty in -10/+10 system

    return {"score": round(float(np.clip(score, 0, 10)), 1),
            "detail": f"dev={latest:.1f}% stb={stability}",
            "deviation_pct": round(float(latest), 2), "stability": stability}


def score_downside_risk(kline_df) -> dict:
    """下跌风险评分 -10~+10 — 高风险=负分, 低风险=正分.

    检测: RSI超买/超卖、波动率突增、量价背离、连续阴线、均线破位.
    """
    import numpy as np
    if kline_df is None or len(kline_df) < 20:
        return {"score": 0, "detail": "insufficient_data"}

    c = kline_df["Close"].values
    h = kline_df["High"].values
    l = kline_df["Low"].values
    v = kline_df["Volume"].values
    n = len(c)
    score = 0.0
    reasons = []

    # 1. RSI 超买检测 (14日)
    rsi14 = float(calc_rsi(pd.Series(c), 14).iloc[-1])
    if rsi14 > 75:
        score -= (rsi14 - 75) * 0.25
        reasons.append(f"RSI={rsi14:.0f}(超买)")
    elif rsi14 > 65:
        score -= (rsi14 - 65) * 0.15
    elif rsi14 < 25:
        score += (25 - rsi14) * 0.2  # 超卖反而风险低(反弹机会)
        reasons.append(f"RSI={rsi14:.0f}(超卖)")

    # 2. 波动率突增 (5日 vs 20日)
    safe_c = np.where(c[-21:-1] == 0, 0.01, c[-21:-1])
    rets = np.diff(c[-21:]) / safe_c
    vol_5d = np.std(rets[-5:]) if len(rets) >= 5 else 0
    vol_20d = np.std(rets) if len(rets) >= 10 else 0.001
    if vol_20d > 0 and vol_5d > vol_20d * 2:
        score -= 2.5
        reasons.append("波动率突增")

    # 3. 量价背离: 价格上涨但成交量萎缩
    if n >= 5:
        price_up = c[-1] > c[-5]
        vol_down = v[-3:].mean() < v[-8:-3].mean() * 0.7 if n >= 8 else False
        if price_up and vol_down:
            score -= 2.0
            reasons.append("量价背离(涨缩量)")

    # 4. 连续阴线
    bearish_streak = 0
    for i in range(n-1, max(0, n-10), -1):
        if c[i] < c[i-1]:
            bearish_streak += 1
        else:
            break
    if bearish_streak >= 4:
        score -= bearish_streak * 0.6
        reasons.append(f"{bearish_streak}连阴")
    elif bearish_streak >= 2:
        score -= bearish_streak * 0.25

    # 5. 均线破位: 收盘在 MA20 之下且 MA5 < MA20
    ma5 = np.mean(c[-5:]) if n >= 5 else c[-1]
    ma20 = np.mean(c[-20:]) if n >= 20 else c[-1]
    if c[-1] < ma20 and ma5 < ma20:
        score -= 2.0
        reasons.append("均线破位(MA5<MA20)")

    # 6. 近期是否存在跌停或大幅高开低走
    if n >= 2:
        o_today, c_today, l_today = c[-2], c[-1], l[-1]  # simplified
        daily_ret = (c[-1] - c[-2]) / c[-2] if c[-2] > 0 else 0
        if daily_ret < -0.07:  # -7%+
            score -= 3.0
            reasons.append(f"近暴跌{daily_ret*100:.0f}%")

    score = float(np.clip(score, -10, 10))
    return {"score": round(score, 1), "detail": "; ".join(reasons) if reasons else "正常",
            "risk_level": "high" if score < -5 else ("elevated" if score < -2 else "normal")}


def score_weekly_resonance(resonance_type: str, weekly_tg_momentum: float = 0) -> dict:
    """周线双周期共振评分 — 日线+周线信号叠加的额外加分.

    方案 B 核心逻辑:
      - 日线买入 AND 周线买入 → "weekly_resonance" → 满分 1.0 (共振最强)
      - 日线买入 AND NOT 周线买入 → "daily_only" → 0.0 (不加分, 正常日线驱动)
      - NOT 日线买入 AND 周线买入 → "weekly_driven" → 0.6 (新发现, 优先级较低)
      - 字段不存在 (旧数据兼容) → 0.0

    Args:
        resonance_type: scan_results.resonance_type
        weekly_tg_momentum: 周线 TG 动量值

    Returns:
        {"score": 0.0 ~ 1.0, "detail": str}
    """
    if not resonance_type:
        return {"score": 0.0, "detail": "无周线数据"}

    if resonance_type == "weekly_resonance":
        # 日线+周线双周期共振 → 额外增信
        boost = min(0.3, abs(weekly_tg_momentum) / 100) if weekly_tg_momentum else 0
        score = 1.0 + boost
        return {
            "score": min(1.0, score),
            "detail": f"周线共振⭐ (日线+周线双确认, 周线动量:{weekly_tg_momentum})",
        }
    elif resonance_type == "weekly_driven":
        # 仅周线买入, 日线无信号 → 0.6 分
        return {
            "score": 0.6,
            "detail": f"周线驱动📅 (仅周线信号, 日线待确认, 动量:{weekly_tg_momentum})",
        }
    else:
        # daily_only 或未知 → 不加分
        return {"score": 0.0, "detail": "仅日线驱动"}


