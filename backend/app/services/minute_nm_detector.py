"""分钟线 N/M 形态检测器 — 长尾理论核心引擎.

N型 = 两低夹一高 = 吸筹形态 (Accumulation)
  - 开盘后先跌(震仓) → 反弹(主力承接) → 再跌但低点抬高(试盘) → 收盘拉回
  - 本质: 日内有两波下跌都被接住, 且第二波更浅, 说明抛压衰竭

M型 = 两高夹一低 = 出货形态 (Distribution)
  - 开盘后先涨(诱多) → 回落(出货) → 再涨但高点降低(二次诱多) → 收盘砸回
  - 本质: 日内有两波拉升都无以为继, 且第二波更弱, 说明买力衰竭

算法:
  对每日 5 分钟线:
    1. 计算日内 VWAP + 分时段量价
    2. 找显著摆点 (局部极值, 振幅 > 阈值)
    3. 匹配 N/M 序列模板
    4. 跨日聚合 → N_score / M_score

板块联盟:
  同行业多只股票同时出 N → 板块资金真流入 → 提升信号置信度
  同行业多只股票同时出 M → 板块资金真流出 → 降低信号置信度
"""

import logging, numpy as np
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("minute_nm")

# ── 阈值配置 ──
SWING_THRESHOLD = 0.012      # 摆点最小振幅 1.2%
RECOVERY_THRESHOLD = 0.005   # 恢复最小幅度 0.5%
MIN_BARS_PER_DAY = 30        # 每天至少 30 根 5 分钟线
LOOKBACK_DAYS = 15           # 回看天数
HALF_WINDOW = 5              # 摆点检测窗口 (左右各 5 根 ≈ 25 分钟)


def find_swing_points(prices: np.ndarray, window: int = HALF_WINDOW) -> list[dict]:
    """找局部极值点 (左右各 window 根K线内的最高/最低)."""
    n = len(prices)
    if n < window * 2 + 1:
        return []

    swings = []
    for i in range(window, n - window):
        left_mean = float(np.mean(prices[i - window:i]))
        right_mean = float(np.mean(prices[i + 1:i + window + 1]))
        neighbor_mean = (left_mean + right_mean) / 2

        if prices[i] == np.max(prices[i - window:i + window + 1]):
            amplitude = (prices[i] - neighbor_mean) / max(abs(neighbor_mean), 0.01)
            if amplitude > SWING_THRESHOLD:
                swings.append({
                    "idx": i, "price": float(prices[i]), "type": "high",
                    "amplitude_pct": round(float(amplitude * 100), 2),
                })
        if prices[i] == np.min(prices[i - window:i + window + 1]):
            amplitude = (neighbor_mean - prices[i]) / max(abs(neighbor_mean), 0.01)
            if amplitude > SWING_THRESHOLD:
                swings.append({
                    "idx": i, "price": float(prices[i]), "type": "low",
                    "amplitude_pct": round(float(amplitude * 100), 2),
                })

    return swings


def _merge_swings(swings: list[dict], prices: np.ndarray) -> list[dict]:
    """合并过近的同类摆点, 保留更显著的."""
    if len(swings) < 2:
        return swings

    merged = [swings[0]]
    for s in swings[1:]:
        last = merged[-1]
        # 同类且距离 < 5 根K线 → 保留价格更极端的
        if s["type"] == last["type"] and s["idx"] - last["idx"] < 5:
            if s["type"] == "high" and s["price"] > last["price"]:
                merged[-1] = s
            elif s["type"] == "low" and s["price"] < last["price"]:
                merged[-1] = s
        else:
            merged.append(s)

    return merged


def classify_day_shape(bars_5min: list[dict]) -> dict:
    """对单日 5 分钟线进行 N/M 分类 — 分时段对比法.

    核心方法:
      N型 (吸筹): 上下午各一次下跌都被接住, 下午跌得更浅 (抛压衰竭)
      M型 (出货): 上下午各一次拉升都无以为继, 下午涨得更弱 (买力衰竭)

    不依赖精确摆点匹配, 用分时段极值统计 — 对真实噪声数据更稳健.
    """
    if len(bars_5min) < MIN_BARS_PER_DAY:
        return _empty_result("数据不足")

    closes = np.array([b["close"] for b in bars_5min])
    highs = np.array([b["high"] for b in bars_5min])
    lows = np.array([b["low"] for b in bars_5min])
    vols = np.array([b.get("vol", 0) for b in bars_5min])
    n = len(closes)

    day_open = float(bars_5min[0]["open"])
    day_close = float(bars_5min[-1]["close"])
    day_high = float(np.max(highs))
    day_low = float(np.min(lows))
    day_range = (day_high - day_low) / max(day_low, 0.01) * 100

    # 日内振幅太小 → 无形态
    if day_range < 1.5:
        return _empty_result(f"振幅过小({day_range:.1f}%)", day_range, 1.0)

    # VWAP + 量
    typical = (highs + lows + closes) / 3
    vwap = float(np.sum(typical * vols) / max(np.sum(vols), 1))
    vwap_pos = day_close / max(vwap, 0.01)

    # ── 分时段 ──
    mid = n // 2
    morning_c = closes[:mid]
    afternoon_c = closes[mid:]
    morning_h = highs[:mid]
    afternoon_h = highs[mid:]
    morning_l = lows[:mid]
    afternoon_l = lows[mid:]

    # 上午相对开盘的最大涨/跌幅
    morning_max_gain = (float(np.max(morning_h)) - day_open) / max(day_open, 0.01)
    morning_max_loss = (day_open - float(np.min(morning_l))) / max(day_open, 0.01)

    # 下午相对中午收盘的最大涨/跌幅
    midday_close = float(closes[mid - 1])
    afternoon_max_gain = (float(np.max(afternoon_h)) - midday_close) / max(midday_close, 0.01)
    afternoon_max_loss = (midday_close - float(np.min(afternoon_l))) / max(midday_close, 0.01)

    # ── N型检测: 两次下跌, 第二次更浅 (低点抬高) ──
    n_score = 0.0
    n_points = 0

    # N型条件1: 上午有显著下跌 > 1.2%
    if morning_max_loss > SWING_THRESHOLD:
        n_points += 1
        n_score += 0.20

    # N型条件2: 下午下跌幅度 < 上午下跌幅度 × 0.7 (抛压减小)
    if afternoon_max_loss > 0.003 and afternoon_max_loss < morning_max_loss * 0.70:
        n_points += 1
        n_score += 0.25
    elif afternoon_max_loss < morning_max_loss * 0.50:
        n_points += 1
        n_score += 0.30  # 下午几乎没跌, 强N

    # N型条件3: 上午低点 < 下午低点 (higher low)
    am_low = float(np.min(morning_l))
    pm_low = float(np.min(afternoon_l))
    if pm_low > am_low * (1 + RECOVERY_THRESHOLD):
        n_points += 1
        n_score += 0.25

    # N型条件4: 收盘 > 开盘 (真吸筹最终推高价格)
    if day_close > day_open * 1.003:
        n_points += 1
        n_score += 0.15

    # N型条件5: 收盘 > VWAP
    if vwap_pos > 1.005:
        n_points += 1
        n_score += 0.10

    # 额外加分: 全天最终收在高位
    close_position = (day_close - day_low) / max(day_high - day_low, 0.01)
    if close_position > 0.65:
        n_score += 0.05

    n_score = round(min(1.0, n_score), 3)

    # ── M型检测: 两次拉升, 第二次更低 (高点降低) ──
    m_score = 0.0
    m_points = 0

    # M型条件1: 上午有显著拉升 > 1.2%
    if morning_max_gain > SWING_THRESHOLD:
        m_points += 1
        m_score += 0.20

    # M型条件2: 下午拉升幅度 < 上午拉升幅度 × 0.7 (买力减弱)
    if afternoon_max_gain > 0.003 and afternoon_max_gain < morning_max_gain * 0.70:
        m_points += 1
        m_score += 0.25
    elif afternoon_max_gain < morning_max_gain * 0.50:
        m_points += 1
        m_score += 0.30

    # M型条件3: 上午高点 > 下午高点 (lower high)
    am_high = float(np.max(morning_h))
    pm_high = float(np.max(afternoon_h))
    if pm_high < am_high * (1 - RECOVERY_THRESHOLD):
        m_points += 1
        m_score += 0.25

    # M型条件4: 收盘 < 开盘
    if day_close < day_open * 0.997:
        m_points += 1
        m_score += 0.15

    # M型条件5: 收盘 < VWAP
    if vwap_pos < 0.995:
        m_points += 1
        m_score += 0.10

    if close_position < 0.35:
        m_score += 0.05

    m_score = round(min(1.0, m_score), 3)

    # ── 综合判定 ──
    if n_score >= 0.35 and n_score > m_score + 0.15:
        shape = "N"
    elif m_score >= 0.35 and m_score > n_score + 0.15:
        shape = "M"
    elif n_score >= 0.20 and n_score > m_score:
        shape = "weak_n"
    elif m_score >= 0.20 and m_score > n_score:
        shape = "weak_m"
    else:
        shape = "neutral"

    return {
        "shape": shape,
        "n_score": n_score,
        "m_score": m_score,
        "n_confirmations": n_points,
        "m_confirmations": m_points,
        "day_range_pct": round(day_range, 2),
        "vwap_position": round(vwap_pos, 3),
        "close_position": round(close_position, 2),
        "am_dip_pct": round(morning_max_loss * 100, 2),
        "pm_dip_pct": round(afternoon_max_loss * 100, 2),
        "am_spike_pct": round(morning_max_gain * 100, 2),
        "pm_spike_pct": round(afternoon_max_gain * 100, 2),
        "details": _describe_shape(shape, n_score, m_score, n_points, m_points),
    }


def _empty_result(details="", day_range=0, vwap_pos=1.0):
    return {"shape": "neutral", "n_score": 0, "m_score": 0,
            "n_confirmations": 0, "m_confirmations": 0,
            "day_range_pct": round(day_range, 2), "vwap_position": round(vwap_pos, 3),
            "close_position": 0.5, "am_dip_pct": 0, "pm_dip_pct": 0,
            "am_spike_pct": 0, "pm_spike_pct": 0, "details": details}


def _describe_shape(shape, n_s, m_s, nc, mc):
    """人类可读的形态描述."""
    if shape == "N":
        return f"N型吸筹 (强度{n_s:.0%}, {nc}处确认) — 抛压衰竭, 主力承接"
    elif shape == "weak_n":
        return f"偏N型 (强度{n_s:.0%}, {nc}处确认) — 有承接迹象但不够强"
    elif shape == "M":
        return f"M型出货 (强度{m_s:.0%}, {mc}处确认) — 买力衰竭, 主力减仓"
    elif shape == "weak_m":
        return f"偏M型 (强度{m_s:.0%}, {mc}处确认) — 有出货迹象但不够强"
    else:
        return "中性 — 无明显日内形态"


def detect_nm_pattern(bars_5min: list[dict]) -> dict:
    """多日 5 分钟线 N/M 检测 — 按天分组, 逐日分类, 跨日聚合.

    Args:
        bars_5min: 多日 5 分钟线 [{time, open, close, high, low, vol}, ...]
                   需已按时间排序, time 格式 "2026-05-30 09:30:00"

    Returns:
        {
            n_days: int,          # N型天数
            m_days: int,          # M型天数
            neutral_days: int,    # 中性天数
            n_ratio: float,       # N型占比
            m_ratio: float,       # M型占比
            dominant_shape: str,  # 主导形态
            daily_shapes: [...],  # 每日详细结果
            nm_score: float,      # -1(M) ~ +1(N) 综合分
            confidence: str,      # high/medium/low
            verdict: str,         # 综合判定文本
        }
    """
    if len(bars_5min) < 100:
        return {"n_days": 0, "m_days": 0, "neutral_days": 0,
                "n_ratio": 0, "m_ratio": 0, "dominant_shape": "no_data",
                "daily_shapes": [], "nm_score": 0, "confidence": "low",
                "verdict": "分钟数据不足, 无法判定"}

    # 按天分组
    by_day = defaultdict(list)
    for b in bars_5min:
        d = b.get("time", "")[:10] if "time" in b else b.get("trade_time", "")[:10]
        if d:
            by_day[d].append(b)

    days = sorted(by_day.keys())
    if len(days) < 3:
        return {"n_days": 0, "m_days": 0, "neutral_days": len(days),
                "n_ratio": 0, "m_ratio": 0, "dominant_shape": "insufficient",
                "daily_shapes": [], "nm_score": 0, "confidence": "low",
                "verdict": f"仅{len(days)}天数据, 需≥3天"}

    daily_results = []
    n_count = 0
    m_count = 0
    weak_n_count = 0
    weak_m_count = 0

    for d in days:
        day_result = classify_day_shape(by_day[d])
        day_result["date"] = d
        daily_results.append(day_result)

        if day_result["shape"] == "N":
            n_count += 1
        elif day_result["shape"] == "M":
            m_count += 1
        elif day_result["shape"] == "weak_n":
            weak_n_count += 1
        elif day_result["shape"] == "weak_m":
            weak_m_count += 1

    total = len(days)
    # Weighted: strong N = 1.0, weak N = 0.5
    n_equiv = n_count + weak_n_count * 0.5
    m_equiv = m_count + weak_m_count * 0.5
    neutral_equiv = total - n_equiv - m_equiv

    n_ratio = round(n_equiv / total, 3)
    m_ratio = round(m_equiv / total, 3)

    # 最近 5 天加权 (越近越重要)
    recent_weights = np.exp(np.linspace(-0.5, 0, min(5, len(daily_results))))
    recent_n = 0.0
    recent_m = 0.0
    for i, dr in enumerate(daily_results[-5:]):
        if dr["shape"] == "N":
            recent_n += recent_weights[i]
        elif dr["shape"] == "M":
            recent_m += recent_weights[i]
        elif dr["shape"] == "weak_n":
            recent_n += recent_weights[i] * 0.5
        elif dr["shape"] == "weak_m":
            recent_m += recent_weights[i] * 0.5

    recent_total = float(np.sum(recent_weights))
    recent_n_ratio = recent_n / max(recent_total, 0.01)
    recent_m_ratio = recent_m / max(recent_total, 0.01)

    # ── 跨日综合评分 ──
    # 全周期 60% + 最近 40%
    nm_score_raw = (n_ratio - m_ratio) * 0.6 + (recent_n_ratio - recent_m_ratio) * 0.4
    nm_score = round(max(-1.0, min(1.0, nm_score_raw)), 3)

    # 主导形态
    if nm_score >= 0.25:
        dominant = "N_dominant"
    elif nm_score <= -0.25:
        dominant = "M_dominant"
    elif nm_score > 0.05:
        dominant = "N_leaning"
    elif nm_score < -0.05:
        dominant = "M_leaning"
    else:
        dominant = "neutral"

    # 置信度
    if total >= 10 and abs(nm_score) >= 0.30:
        confidence = "high"
    elif total >= 5 and abs(nm_score) >= 0.15:
        confidence = "medium"
    else:
        confidence = "low"

    # 综合判定
    verdict_map = {
        "N_dominant": f"主导吸筹 — {round(n_ratio*100)}%天数呈N型, 主力持续承接",
        "N_leaning": f"偏吸筹 — N型倾向({nm_score:+.2f}), 关注后续确认",
        "M_dominant": f"主导出货 — {round(m_ratio*100)}%天数呈M型, 建议规避",
        "M_leaning": f"偏出货 — M型倾向({nm_score:+.2f}), 需警惕",
        "neutral": "形态中性 — 无明显吸筹/出货倾向",
    }

    return {
        "n_days": n_count,
        "m_days": m_count,
        "weak_n_days": weak_n_count,
        "weak_m_days": weak_m_count,
        "neutral_days": total - n_count - m_count - weak_n_count - weak_m_count,
        "total_days": total,
        "n_ratio": n_ratio,
        "m_ratio": m_ratio,
        "recent_n_ratio": round(recent_n_ratio, 3),
        "recent_m_ratio": round(recent_m_ratio, 3),
        "dominant_shape": dominant,
        "daily_shapes": daily_results,
        "nm_score": nm_score,
        "confidence": confidence,
        "verdict": verdict_map.get(dominant, "数据不足"),
    }


# ── 分钟线数据下载 ──
#   fetch_5min_bars 已迁移至 app/services/minute_data.py
#   本地直接复用 detect_nm_pattern 即可
