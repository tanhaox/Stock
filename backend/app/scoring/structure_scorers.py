"""结构形态评分函数 — 从 deep_scorer.py 拆分 (v4.3)."""
import numpy as np

def score_ma_trend(kline_df) -> dict:
    """均线趋势评分 -10~+10 — 多空排列 + 偏离度."""
    if kline_df is None or len(kline_df) < 60:
        return {"score": 0.0, "details": "K线不足"}
    c = kline_df["Close"]
    ma5 = c.rolling(5).mean().iloc[-1]
    ma20 = c.rolling(20).mean().iloc[-1]
    ma60 = c.rolling(60).mean().iloc[-1] if len(c) >= 60 else ma20
    close = c.iloc[-1]

    score = 0.0
    if close > ma5 > ma20 > ma60: score += 4.5
    elif close > ma5 > ma20: score += 3.0
    elif close > ma20: score += 1.5
    elif close > ma60: score += 0.5
    else: score -= 2.0

    if ma20 > 0:
        bias = (close - ma20) / ma20 * 100
        if -5 < bias < -2: score += 2.5       # 温和回调，好的买点
        elif -2 <= bias <= 2: score += 1.0    # 均线附近
        elif bias > 10: score -= 2.0          # 过度乖离
        elif bias < -15: score -= 2.0         # 深度超跌

    return {"score": round(float(np.clip(score, -10, 10)), 1)}


def score_pattern_signal(patterns: str) -> dict:
    """形态信号评分 0-10 — 看涨形态加分，看跌减分."""
    if not patterns:
        return None  # 无形态数据，不参与评分
    parts = [p.strip() for p in patterns.split(",") if p.strip()]
    if not parts:
        return None
    BULL = {'three_red_soldiers','golden_spider','bullish_artillery',
            'morning_star','double_firecracker','air_refueling',
            'single_yang_unbroken','dawn_appearance','golden_needle_bottom'}
    BEAR = {'three_black_crows','evening_star','hanging_man',
            'decapitation','dark_cloud_cover','pouring_rain'}
    score = 0.0  # pattern base
    for p in parts:
        if p in BULL: score += 0.8
        elif p in BEAR: score -= 1.5
    return {"score": round(float(np.clip(score, -10, 10)), 1)}


def score_multi_box(kline_df) -> dict:
    """多箱体结构维度 — 自适应窗口 + 量价配合 + 均线协同, -10~+10 评分.

    核心理念: 负分 = 有明显结构缺陷, 不被推荐; 正分 = 结构优势.
    输出包含第一支撑/压力位, 突破/破位信号.
    """
    import numpy as np
    if kline_df is None or len(kline_df) < 60:
        return None
    closes = kline_df["Close"].values; opens = kline_df["Open"].values
    highs = kline_df["High"].values; lows = kline_df["Low"].values
    volumes = kline_df["Volume"].values if "Volume" in kline_df.columns else np.ones_like(closes)
    n = len(closes)

    # ── ATR(14): 个股的有意义波动单位 ──
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
    atr14 = np.zeros(n)
    atr14[13] = np.mean(tr[1:14])
    for i in range(14, n):
        atr14[i] = (atr14[i-1]*13 + tr[i])/14
    atr = float(atr14[-1]) if atr14[-1] > 0 else closes[-1]*0.02

    # ── 波段高低点检测 (120日内) ──
    lookback = min(120, n)
    swings_high, swings_low = [], []
    for i in range(max(10, n-lookback+10), n-5):
        if closes[i] <= 0: continue
        half = max(3, int(atr/closes[i]*50))
        if i-half < 0 or i+half >= n: continue
        if highs[i] == max(highs[i-half:i+half+1]) and highs[i]-lows[i] > atr*1.5:
            swings_high.append(i)
        if lows[i] == min(lows[i-half:i+half+1]) and highs[i]-lows[i] > atr*1.5:
            swings_low.append(i)

    # 平均摆动周期
    avg_cycle = 20  # default
    periods = []
    for j in range(1, len(swings_high)):
        periods.append(swings_high[j]-swings_high[j-1])
    for j in range(1, len(swings_low)):
        periods.append(swings_low[j]-swings_low[j-1])
    if periods:
        avg_cycle = int(np.median(periods))
    avg_cycle = max(5, min(40, avg_cycle))

    # ── 自适应窗口 ──
    windows = [
        max(5, int(avg_cycle*0.8)),     # 短箱
        max(8, int(avg_cycle*1.6)),     # 中箱
        max(12, int(avg_cycle*3.0)),    # 长箱
        max(20, int(avg_cycle*5.0)),    # 大箱
    ]
    windows = list(dict.fromkeys(windows))  # 去重
    windows = [w for w in windows if w <= n][:4]

    # ── 每层箱体检测 ──
    avg_vol_20 = float(np.mean(volumes[-20:])) if len(volumes)>=20 else 1.0
    boxes = []
    for w in windows:
        win_high = float(np.max(highs[-w:]))
        win_low = float(np.min(lows[-w:]))
        if win_low <= 0: continue
        height_pct = (win_high-win_low)/win_low*100
        if height_pct < 2 or height_pct > 40: continue

        # 触碰计数
        touches_top = sum(1 for i in range(n-w, n) if highs[i] >= win_high*0.99)
        touches_bot = sum(1 for i in range(n-w, n) if lows[i] <= win_low*1.01)
        if touches_top < 1 or touches_bot < 1: continue

        # 量价配合
        vol_quality = 0
        top_idxs = [i for i in range(n-w, n) if highs[i] >= win_high*0.99][-2:]
        bot_idxs = [i for i in range(n-w, n) if lows[i] <= win_low*1.01][-2:]
        for idx in top_idxs:
            if volumes[idx] < avg_vol_20*0.8: vol_quality -= 0.5  # 箱顶缩量=自然消化
            elif volumes[idx] > avg_vol_20*1.5: vol_quality += 0.5  # 强压力确认
        for idx in bot_idxs:
            if volumes[idx] < avg_vol_20*0.8: vol_quality += 1.0  # 箱底缩量=吸筹
            elif volumes[idx] > avg_vol_20*1.5: vol_quality -= 1.0  # 箱底放量=出货嫌疑

        boxes.append({"window": w, "top": win_high, "bottom": win_low,
                       "height_pct": round(height_pct,1), "touches_top": touches_top,
                       "touches_bot": touches_bot, "vol_quality": round(vol_quality,2)})

    if not boxes:
        return {"score": 0, "detail": "无有效箱体", "boxes": [], "first_support": None, "first_resistance": None}

    # ── 支撑/压力矩阵 ──
    supports = sorted(set(b["bottom"] for b in boxes), reverse=True)  # 从高到低
    resistances = sorted(set(b["top"] for b in boxes))  # 从低到高
    price = float(closes[-1])
    first_support = next((s for s in supports if s < price), min(supports) if supports else None)
    first_resistance = next((r for r in resistances if r > price), max(resistances) if resistances else None)
    strong_support = min(b["bottom"] for b in boxes if b["touches_bot"]>=2) if any(b["touches_bot"]>=2 for b in boxes) else None
    strong_resistance = max(b["top"] for b in boxes if b["touches_top"]>=2) if any(b["touches_top"]>=2 for b in boxes) else None

    # ── 当前位置: 多少层箱体确认当前价在箱底附近 ──
    at_bottom = sum(1 for b in boxes if (price-b["bottom"])/b["bottom"] < 0.05)
    at_top = sum(1 for b in boxes if (b["top"]-price)/price < 0.05)
    valid_boxes = [b for b in boxes if b["touches_top"]>=2 and b["touches_bot"]>=2]

    # ── 均线协同 ──
    ma20 = float(np.mean(closes[-20:])) if n>=20 else price
    ma_above = price > ma20

    # ── 突破/破位检测 ──
    breakout = None
    if first_resistance and price > first_resistance:
        vol_ratio = volumes[-1]/avg_vol_20 if avg_vol_20 > 0 else 1
        if vol_ratio > 1.5 and ma_above: breakout = "up"
        elif vol_ratio < 0.8: breakout = "fake_up"
    if first_support and price < first_support:
        vol_ratio = volumes[-1]/avg_vol_20 if avg_vol_20 > 0 else 1
        if vol_ratio > 1.5 and not ma_above: breakout = "down"
        elif vol_ratio < 0.8: breakout = "fake_down"

    # ── 评分 (-10 ~ +10) ──
    score = 0.0
    total_vq = sum(b["vol_quality"] for b in boxes) / max(1, len(boxes))

    # 正分: 结构优势
    if len(valid_boxes) >= 3:
        score += 3.0
    elif len(valid_boxes) >= 2:
        score += 1.5
    elif len(valid_boxes) >= 1:
        score += 0.5

    if at_bottom >= 2:
        score += 3.0  # 多层箱底确认 → 强支撑
    elif at_bottom >= 1:
        score += 1.5

    if total_vq > 0.5: score += 2.0  # 量价配合良好
    elif total_vq > 0: score += 1.0

    if ma_above and at_bottom >= 1: score += 1.0  # 均线+箱底共振

    # 压缩加分(短箱高度<8%) → 蓄力
    if boxes and boxes[0]["height_pct"] < 8 and at_bottom >= 1:
        score += 1.5

    if breakout == "up": score += 2.0
    elif breakout == "fake_up": score -= 1.5

    # 负分: 结构缺陷
    if at_top >= 2:
        score -= 3.0  # 多层箱顶 → 强压力
    elif at_top >= 1:
        score -= 1.5

    if total_vq < -1.0: score -= 2.5  # 量价背离严重
    elif total_vq < -0.5: score -= 1.5

    if not ma_above and at_top >= 1: score -= 1.5  # 均线压制+箱顶

    if breakout == "down": score -= 4.0  # 放量破位 → 大幅减分
    elif breakout == "fake_down": score -= 1.0

    # 无结构惩罚
    if len(valid_boxes) == 0:
        score -= 1.0

    score = round(float(np.clip(score, -10, 10)), 1)

    # 将 -10~+10 映射到 0-10 供 composite 使用(乘数保持一致)
    # composite 层期望 0-10 的分数, 这里做线性映射
    mapped_score = round((score + 10) / 2, 1)  # -10→0, 0→5, +10→10

    return {"score": mapped_score, "raw_score": score,
            "detail": f"boxes={len(valid_boxes)}v/{len(boxes)}t at_bot={at_bottom} at_top={at_top} vq={total_vq:.1f} brk={breakout or 'none'}",
            "boxes": boxes, "first_support": first_support, "first_resistance": first_resistance,
            "strong_support": strong_support, "strong_resistance": strong_resistance,
            "breakout": breakout, "volume_quality": round(total_vq, 2),
            "at_bottom": at_bottom, "at_top": at_top}


