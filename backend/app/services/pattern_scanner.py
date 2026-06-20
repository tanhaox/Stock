"""形态识别扫描器 — 经典K线形态量化检测.

识别形态:
  - 红三兵 (three_red_soldiers): 连续3阳线, 放量上攻
  - 金蜘蛛 (golden_spider): 均线粘合+突破
  - 多方炮 (bullish_artillery): 阳-阴-阳 夹击
  - 早晨之星 (morning_star): 底部反转星线

所有判断基于 trade_date 及之前数据, 无未来函数.
"""
import pandas as pd
import numpy as np
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory


async def load_recent_klines(ts_code: str, trade_date: date, days: int = 60) -> Optional[pd.DataFrame]:
    """加载 trade_date 之前(含)的K线."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_kline WHERE ts_code=:s AND trade_date <= :d
            ORDER BY trade_date
        """), {"s": ts_code, "d": trade_date})
        rows = r.fetchall()
    if len(rows) < 20:
        return None
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── 辅助函数 ─────────────────────────────────────

def _body(row):
    return abs(row["Close"] - row["Open"])

def _is_bullish(row):
    return row["Close"] > row["Open"]

def _upper_shadow(row):
    return row["High"] - max(row["Close"], row["Open"])

def _lower_shadow(row):
    return min(row["Close"], row["Open"]) - row["Low"]


# ── 红三兵 ───────────────────────────────────────

def detect_three_red_soldiers(df: pd.DataFrame) -> Optional[dict]:
    """检测红三兵: 连续3根递增阳线, 量价配合.

    条件:
    1. 最近3根均为阳线
    2. 收盘价依次升高
    3. 每根实体 > 前一根实体的 0.7 倍(逐步放大)
    4. 成交量温和放大(量比1.0-3.0)
    """
    if len(df) < 4:  # 需要前1根作对比
        return None
    c = df.iloc[-3:]  # 最近3根
    if not all(_is_bullish(row) for _, row in c.iterrows()):
        return None
    closes = c["Close"].values
    if not (closes[0] < closes[1] < closes[2]):
        return None
    bodies = [_body(row) for _, row in c.iterrows()]
    if bodies[1] < bodies[0] * 0.7 or bodies[2] < bodies[1] * 0.7:
        return None
    vol = c["Volume"].values
    vol_ma = df["Volume"].iloc[-10:-1].mean()
    vol_ratio = vol[-1] / max(vol_ma, 1)
    if vol_ratio < 1.0:
        return None

    body_growth = (bodies[2] / max(bodies[0], 0.001) - 1)
    score = 8.0 if body_growth > 0.3 and vol_ratio > 1.3 else 5.0
    confidence = min(1.0, 0.5 + body_growth * 0.3 + (vol_ratio - 1.0) * 0.1)
    return {
        "pattern_type": "three_red_soldiers",
        "pattern_score": round(score, 1),
        "confidence": round(confidence, 2),
        "details": {"body_growth_pct": round(body_growth * 100, 1), "vol_ratio": round(vol_ratio, 2)},
    }


# ── 金蜘蛛 ───────────────────────────────────────

def detect_golden_spider(df: pd.DataFrame) -> Optional[dict]:
    """检测金蜘蛛: 5/10/20均线粘合后放量突破.

    条件:
    1. 5/10/20 SMA 最大差值 < 2%
    2. 收盘价站上三条均线
    3. 成交量放大(量比 > 1.5)
    """
    if len(df) < 25:
        return None
    close = df["Close"]
    sma5 = close.rolling(5).mean()
    sma10 = close.rolling(10).mean()
    sma20 = close.rolling(20).mean()

    last = len(df) - 1
    s5, s10, s20 = sma5.iloc[last], sma10.iloc[last], sma20.iloc[last]
    if pd.isna(s5) or pd.isna(s10) or pd.isna(s20) or s20 <= 0:
        return None

    max_sma = max(s5, s10, s20)
    min_sma = min(s5, s10, s20)
    stickiness = (max_sma - min_sma) / s20 * 100  # 粘合度(%)

    if stickiness > 2.0:
        return None

    last_close = close.iloc[last]
    if not (last_close > s5 and last_close > s10 and last_close > s20):
        return None

    vol = df["Volume"].iloc[last]
    vol_ma = df["Volume"].iloc[-20:-1].mean()
    vol_ratio = vol / max(vol_ma, 1)
    if vol_ratio < 1.5:
        return None

    score = 9.0 if stickiness < 1.0 and vol_ratio > 2.0 else 6.0
    confidence = min(1.0, 0.5 + (2.0 - stickiness) * 0.2 + (vol_ratio - 1.5) * 0.1)
    return {
        "pattern_type": "golden_spider",
        "pattern_score": round(score, 1),
        "confidence": round(confidence, 2),
        "details": {"stickiness_pct": round(stickiness, 2), "vol_ratio": round(vol_ratio, 2)},
    }


# ── 多方炮 ───────────────────────────────────────

def detect_bullish_artillery(df: pd.DataFrame) -> Optional[dict]:
    """检测多方炮: 阳-阴-阳 三K线组合, 中间阴线被前后阳线夹击.

    条件:
    1. 最近3根K线为 阳-阴-阳
    2. 中间阴线实体完全在首阳实体范围内(或幅度很小)
    3. 第三阳收盘 > 首阳收盘
    4. 阴线缩量, 阳线放量
    """
    if len(df) < 5:
        return None
    r1, r2, r3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if not (_is_bullish(r1) and not _is_bullish(r2) and _is_bullish(r3)):
        return None

    body1, body2, body3 = _body(r1), _body(r2), _body(r3)
    if body1 <= 0:
        return None

    # 阴线实体 < 首阳实体
    if body2 > body1 * 1.2:
        return None

    # 第三阳收盘 > 首阳收盘
    if r3["Close"] <= r1["Close"]:
        return None

    # 量价: 阴线缩量, 阳线放量
    vol1, vol2, vol3 = r1["Volume"], r2["Volume"], r3["Volume"]
    if vol2 > vol1 * 1.1 or vol3 < vol1 * 1.1:
        return None

    score = 9.0 if body3 > body1 and r3["Close"] > r1["High"] else 7.0
    confidence = min(1.0, 0.6 + (r3["Close"] / r1["Close"] - 1.0) * 2)
    return {
        "pattern_type": "bullish_artillery",
        "pattern_score": round(score, 1),
        "confidence": round(confidence, 2),
        "details": {"breakout_pct": round((r3["Close"] / r1["Close"] - 1) * 100, 1)},
    }


# ── 早晨之星 ─────────────────────────────────────

def detect_morning_star(df: pd.DataFrame) -> Optional[dict]:
    """检测早晨之星: 大阴线+星线+大阳线 底部反转.

    条件:
    1. 首根大阴线(实体 > 2% 且 > 前5日平均实体的 1.5 倍)
    2. 中间星线(实体 < 首阴实体的 30%, 带上下影)
    3. 第三根大阳线(收盘进入首阴实体内或上方)
    """
    if len(df) < 10:
        return None
    r1, r2, r3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]

    if not (not _is_bullish(r1) and _is_bullish(r3)):
        return None

    body1, body2, body3 = _body(r1), _body(r2), _body(r3)
    avg_body_5 = df.iloc[-8:-3].apply(_body, axis=1).mean()
    if avg_body_5 <= 0:
        return None

    # 首阴足够大
    close1 = r1["Close"]
    if body1 / close1 < 0.02 or body1 < avg_body_5 * 1.3:
        return None

    # 星线: 小实体 + 有影线
    if body2 > body1 * 0.35:
        return None
    if _upper_shadow(r2) < body2 * 0.3 or _lower_shadow(r2) < body2 * 0.3:
        return None

    # 第三阳进入首阴实体
    if r3["Close"] < r1["Close"]:
        return None

    score = 8.0 if r3["Close"] > r1["Open"] else 6.0
    confidence = min(1.0, 0.5 + body1 / close1 * 10 + body3 / body1 * 0.2)
    return {
        "pattern_type": "morning_star",
        "pattern_score": round(score, 1),
        "confidence": round(confidence, 2),
        "details": {"reversal_strength": round((r3["Close"] / r1["Close"] - 1) * 100, 1)},
    }


# ── 涨停双响炮 ───────────────────────────────────

def detect_double_firecracker(df: pd.DataFrame) -> Optional[dict]:
    """涨停双响炮: 一个月内两次涨停, 中间缩量回调不破首次涨停最低价."""
    if len(df) < 30:
        return None
    close = df["Close"]; vol = df["Volume"]
    limit_up_days = []
    for i in range(1, len(df)):
        chg = (close.iloc[i] - close.iloc[i-1]) / close.iloc[i-1] * 100
        if chg >= 9.5:
            limit_up_days.append(i)
    if len(limit_up_days) < 2:
        return None
    recent = limit_up_days[-2:]
    if recent[1] >= len(df) - 3 and recent[1] - recent[0] <= 20:
        between_low = close.iloc[recent[0]:recent[1]].min()
        first_low = df.iloc[recent[0]-1:recent[0]+1]["Low"].min()
        between_vol = vol.iloc[recent[0]:recent[1]].mean()
        before_vol = vol.iloc[max(0, recent[0]-5):recent[0]].mean()
        if between_low >= first_low * 0.98 and before_vol > 0 and between_vol < before_vol * 0.7:
            return {"pattern_type": "double_firecracker", "pattern_score": 9.5, "confidence": 0.85,
                    "details": {"limit_ups": 2, "days_between": recent[1] - recent[0]}}
    return None


# ── 空中加油 ─────────────────────────────────────

def detect_air_refueling(df: pd.DataFrame) -> Optional[dict]:
    """空中加油: 涨停后巨量阴线, 缩量回调不破启动低点, 近2日企稳."""
    if len(df) < 20:
        return None
    close = df["Close"]; vol = df["Volume"]
    vol_ma20 = vol.rolling(20).mean()
    start_idx = -1
    for i in range(len(df) - 5, max(len(df) - 16, 0), -1):
        chg = (close.iloc[i] - close.iloc[i-1]) / close.iloc[i-1] * 100
        if chg >= 7.0 and vol.iloc[i] / max(vol_ma20.iloc[i], 1) > 1.5:
            start_idx = i; break
    if start_idx < 0 or start_idx + 1 >= len(df):
        return None
    start_low = df.iloc[start_idx-1:start_idx+1]["Low"].min()
    r_break = df.iloc[start_idx + 1]
    if _is_bullish(r_break) or r_break["Open"] <= df.iloc[start_idx]["Close"]:
        return None
    if r_break["Volume"] / max(vol_ma20.iloc[start_idx + 1], 1) < 2.0:
        return None
    if df.iloc[start_idx+2:]["Low"].min() < start_low * 0.98:
        return None
    if df.iloc[start_idx+2:-2]["Volume"].mean() > r_break["Volume"] * 0.6:
        return None
    body_pct = _body(df.iloc[-1]) / max(df.iloc[-1]["Close"], 0.01) * 100
    if body_pct > 2.0:
        return None
    score = 9.0 if df.iloc[start_idx+2:]["Low"].min() > start_low * 0.995 else 7.5
    return {"pattern_type": "air_refueling", "pattern_score": round(score, 1), "confidence": 0.80,
            "details": {"start_chg_pct": round((close.iloc[start_idx] / close.iloc[start_idx-1] - 1) * 100, 1)}}


# ── 单阳不破 ─────────────────────────────────────

def detect_single_yang_unbroken(df: pd.DataFrame) -> Optional[dict]:
    """单阳不破: 放量大阳线后7-12日盘整不破最低价, 当前接近阳线收盘."""
    if len(df) < 15:
        return None
    close = df["Close"]; vol = df["Volume"]
    vol_ma20 = vol.rolling(20).mean()
    for i in range(len(df) - 7, max(len(df) - 14, 0), -1):
        chg = (close.iloc[i] - close.iloc[i-1]) / close.iloc[i-1] * 100
        if chg >= 5.0 and vol.iloc[i] / max(vol_ma20.iloc[i], 1) > 1.5:
            yang_low = df.iloc[i-1:i+1]["Low"].min()
            if df.iloc[i+1:]["Low"].min() < yang_low * 0.98:
                continue
            if df.iloc[i+1:]["Volume"].mean() > vol.iloc[i] * 0.5:
                continue
            if abs(close.iloc[-1] - close.iloc[i]) / close.iloc[i] * 100 < 3.0:
                days = len(df) - 1 - i
                return {"pattern_type": "single_yang_unbroken", "pattern_score": round(8.5 if days >= 10 else 7.0, 1),
                        "confidence": min(1.0, 0.6 + days * 0.03), "details": {"consolidation_days": days}}
    return None


# ── 曙光初现 ─────────────────────────────────────

def detect_dawn_appearance(df: pd.DataFrame) -> Optional[dict]:
    """曙光初现: 大阴线次日低开高走, 阳线深入阴线实体50%以上."""
    if len(df) < 10:
        return None
    r1, r2 = df.iloc[-2], df.iloc[-1]
    if _is_bullish(r1) or not _is_bullish(r2):
        return None
    body1 = _body(r1)
    if body1 / r1["Close"] < 0.02:
        return None
    if r2["Open"] >= r1["Low"]:
        return None
    mid_point = (r1["Open"] + r1["Close"]) / 2
    if r2["Close"] <= mid_point:
        return None
    if r2["Volume"] < df["Volume"].iloc[-10:-1].mean():
        return None
    pen = (r2["Close"] - mid_point) / body1 * 100 if body1 > 0 else 0
    return {"pattern_type": "dawn_appearance", "pattern_score": round(8.0 if r2["Close"] > r1["Open"] else 6.0, 1),
            "confidence": min(1.0, 0.5 + pen / 100), "details": {"penetration_pct": round(pen, 1)}}


# ── 金针探底 ─────────────────────────────────────

def detect_golden_needle_bottom(df: pd.DataFrame) -> Optional[dict]:
    """金针探底: 20日低位附近长下影线, 影线>实体3倍, 放量承接."""
    if len(df) < 20:
        return None
    r = df.iloc[-1]
    if r["Low"] > df["Close"].iloc[-20:].min() * 1.03:
        return None
    body = _body(r); lower = _lower_shadow(r)
    if body <= 0 or lower < body * 3.0:
        return None
    price_range = r["High"] - r["Low"]
    if price_range <= 0 or (r["Close"] - r["Low"]) / price_range < 0.5:
        return None
    if r["Volume"] < df["Volume"].iloc[-10:-1].mean() * 1.1:
        return None
    sr = lower / body
    return {"pattern_type": "golden_needle_bottom", "pattern_score": round(8.0 if sr > 5.0 else 6.0, 1),
            "confidence": min(1.0, 0.5 + sr / 10), "details": {"shadow_ratio": round(sr, 1)}}


# ── 避坑形态 ─────────────────────────────────────

def detect_three_black_crows(df: pd.DataFrame) -> Optional[dict]:
    """三只乌鸦: 连续3根实体阴线, 收盘渐低, 顶部反转信号."""
    if len(df) < 5:
        return None
    c = df.iloc[-3:]
    if any(_is_bullish(row) for _, row in c.iterrows()):
        return None
    closes = c["Close"].values
    if not (closes[0] > closes[1] > closes[2]):
        return None
    bodies = [_body(row) for _, row in c.iterrows()]
    if bodies[1] < bodies[0] * 0.5 or bodies[2] < bodies[1] * 0.5:
        return None
    avg_body_10 = df.iloc[-13:-3].apply(_body, axis=1).mean()
    if avg_body_10 > 0 and sum(bodies) / 3 < avg_body_10:
        return None
    return {"pattern_type": "three_black_crows", "pattern_score": 8.0, "confidence": 0.80,
            "details": {"body_sum": round(sum(bodies) / max(df.iloc[-1]["Close"], 0.01) * 100, 1)}}


def detect_evening_star(df: pd.DataFrame) -> Optional[dict]:
    """黄昏之星: 大阳线+星线+大阴线, 阴线深入阳线实体, 顶部反转."""
    if len(df) < 5:
        return None
    r1, r2, r3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
    if not (_is_bullish(r1) and not _is_bullish(r3)):
        return None
    body1 = _body(r1)
    if body1 / r1["Close"] < 0.02:
        return None
    body2 = _body(r2)
    if body2 > body1 * 0.35:
        return None
    if _upper_shadow(r2) < body2 * 0.3 or _lower_shadow(r2) < body2 * 0.3:
        return None
    if r3["Close"] > r1["Close"]:
        return None
    score = 8.5 if r3["Close"] < r1["Open"] else 7.0
    return {"pattern_type": "evening_star", "pattern_score": round(score, 1), "confidence": 0.80,
            "details": {"penetration": round((r1["Close"] - r3["Close"]) / r1["Close"] * 100, 1)}}


def detect_hanging_man(df: pd.DataFrame) -> Optional[dict]:
    """吊颈线: 高位出现长下影小实体, 下影>实体2倍, 见顶警告."""
    if len(df) < 20:
        return None
    r = df.iloc[-1]
    close = df["Close"]
    high_20 = close.iloc[-20:].max()
    if r["High"] < high_20 * 0.95:
        return None
    body = _body(r); lower = _lower_shadow(r); upper = _upper_shadow(r)
    if body <= 0 or lower < body * 2.0:
        return None
    if upper > body * 0.5:
        return None
    price_range = r["High"] - r["Low"]
    if price_range <= 0 or (r["Close"] - r["Low"]) / price_range < 0.3:
        return None
    return {"pattern_type": "hanging_man", "pattern_score": 7.0, "confidence": 0.70,
            "details": {"shadow_ratio": round(lower / body, 1)}}


def detect_decapitation(df: pd.DataFrame) -> Optional[dict]:
    """断头铡刀: 一根大阴线同时跌破5/10/20日均线, 强烈卖出信号."""
    if len(df) < 25:
        return None
    r = df.iloc[-1]
    if _is_bullish(r):
        return None
    chg = (r["Close"] - df["Close"].iloc[-2]) / df["Close"].iloc[-2] * 100
    if chg > -3.0:
        return None
    close = df["Close"]
    sma5 = close.rolling(5).mean().iloc[-2]
    sma10 = close.rolling(10).mean().iloc[-2]
    sma20 = close.rolling(20).mean().iloc[-2]
    prev_c = close.iloc[-2]
    # 昨日收盘站在均线上方, 今日跌破
    if not (prev_c > sma5 and prev_c > sma10 and prev_c > sma20):
        return None
    if not (r["Close"] < sma5 and r["Close"] < sma10 and r["Close"] < sma20):
        return None
    return {"pattern_type": "decapitation", "pattern_score": 9.5, "confidence": 0.90,
            "details": {"drop_pct": round(abs(chg), 1)}}


def detect_dark_cloud_cover(df: pd.DataFrame) -> Optional[dict]:
    """乌云盖顶: 阳线后高开低走, 阴线收盘深入阳线实体, 但未破中点."""
    if len(df) < 5:
        return None
    r1, r2 = df.iloc[-2], df.iloc[-1]
    if not _is_bullish(r1) or _is_bullish(r2):
        return None
    body1 = _body(r1)
    if body1 / r1["Close"] < 0.015:
        return None
    if r2["Open"] <= r1["High"]:
        return None
    mid = (r1["Open"] + r1["Close"]) / 2
    if r2["Close"] > mid or r2["Close"] < r1["Close"]:
        return None
    pen = (mid - r2["Close"]) / body1 * 100 if body1 > 0 else 0
    return {"pattern_type": "dark_cloud_cover", "pattern_score": round(7.0 if pen > 20 else 5.5, 1),
            "confidence": 0.70, "details": {"penetration_pct": round(pen, 1)}}


def detect_pouring_rain(df: pd.DataFrame) -> Optional[dict]:
    """倾盆大雨: 阳线后高开低走, 阴线收盘低于阳线实体中点, 强烈见顶."""
    if len(df) < 5:
        return None
    r1, r2 = df.iloc[-2], df.iloc[-1]
    if not _is_bullish(r1) or _is_bullish(r2):
        return None
    body1 = _body(r1)
    if body1 / r1["Close"] < 0.015:
        return None
    if r2["Open"] <= r1["High"]:
        return None
    mid = (r1["Open"] + r1["Close"]) / 2
    if r2["Close"] >= mid:
        return None
    return {"pattern_type": "pouring_rain", "pattern_score": 8.5, "confidence": 0.80,
            "details": {"drop": round((mid - r2["Close"]) / r1["Close"] * 100, 1)}}


# ── 主扫描函数 ───────────────────────────────────

async def scan_single_stock(ts_code: str, trade_date: date) -> list[dict]:
    """对单只股票进行形态扫描."""
    df = await load_recent_klines(ts_code, trade_date)
    if df is None:
        return []

    detectors = [
        detect_three_red_soldiers,
        detect_golden_spider,
        detect_bullish_artillery,
        detect_morning_star,
        detect_double_firecracker,
        detect_air_refueling,
        detect_single_yang_unbroken,
        detect_dawn_appearance,
        detect_golden_needle_bottom,
        detect_three_black_crows,
        detect_evening_star,
        detect_hanging_man,
        detect_decapitation,
        detect_dark_cloud_cover,
        detect_pouring_rain,
    ]

    results = []
    for detector in detectors:
        result = detector(df)
        if result:
            results.append(result)

    return results
