"""均线趋势质量评分模块 (MA Trend Score).

基于 8-21-55-144-250 EMA 五线体系，量化：
  1. 乖离率 (30分) — 价格相对均线位置
  2. 均线排列 (30分) — 多头/空头/缠绕
  3. 趋势强度 (20分) — EMA21斜率 + RSI14
  4. 筹码形态 (20分) — 获利盘比例 + 成本集中度 (chip_daily为空则降级)

总分 0-100，融入11维指纹体系作为第12维。
"""
import logging
import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# 五线参数
MA_PERIODS = [8, 21, 55, 144, 250]
# 乖离率评分的四条关键均线
DEVIATION_LINES = [21, 55, 144, 250]
# 最小K线数
MIN_KLINES = 250

NEUTRAL_10 = 5.0


async def load_kline_for_ma(ts_code: str, trade_date: date) -> Optional[pd.DataFrame]:
    """加载指定股票在 trade_date 之前的历史K线(无未来数据)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, high, low, close, volume
            FROM daily_kline
            WHERE ts_code=:s AND trade_date <= :d
            ORDER BY trade_date
        """), {"s": ts_code, "d": trade_date})
        rows = r.fetchall()
    if len(rows) < MIN_KLINES:
        return None
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


async def load_chip_data(ts_code: str, trade_date: date) -> Optional[dict]:
    """加载筹码分布数据(无未来数据)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT profit_ratio, concentration
            FROM chip_daily
            WHERE ts_code=:s AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 1
        """), {"s": ts_code, "d": trade_date})
        row = r.fetchone()
        if row:
            return {
                "profit_ratio": float(row[0]) if row[0] is not None else None,
                "concentration": float(row[1]) if row[1] is not None else None,
            }
    return None


# ── 均线计算 ─────────────────────────────────────

def calc_emas(df: pd.DataFrame) -> dict[int, pd.Series]:
    """计算五条 EMA."""
    emas = {}
    for p in MA_PERIODS:
        emas[p] = df["Close"].ewm(span=p, adjust=False).mean()
    return emas


# ── 3.1 乖离率评分 (30分) ─────────────────────────

def deviation_score(df: pd.DataFrame, emas: dict[int, pd.Series]) -> float:
    """计算乖离率得分.

    对21/55/144/250四条均线分别计算 (close - ema) / ema * 100
    每条线满分+5，四线满分20，映射到30分制: score = sum(scores) / 20 * 30
    """
    close = df["Close"].iloc[-1]
    total = 0.0
    valid = 0
    for p in DEVIATION_LINES:
        ema_val = emas[p].iloc[-1]
        if pd.isna(ema_val) or ema_val <= 0:
            continue
        valid += 1
        deviation = (close - ema_val) / ema_val * 100

        if -3 <= deviation <= 3:
            total += 0       # 均线附近，中性
        elif 3 < deviation <= 8:
            total += 5       # 温和上涨
        elif 8 < deviation <= 15:
            total += 2       # 强势但接近超买
        elif deviation > 15:
            total += 0       # 严重乖离，不加分
        elif -8 <= deviation < -3:
            total -= 3       # 轻度破位
        else:  # < -8
            total -= 5       # 严重破位

    if valid == 0:
        return 0.0
    return max(0.0, min(30.0, total / 20.0 * 30.0))


# ── 3.2 均线排列评分 (30分) ────────────────────────

def alignment_score(emas: dict[int, pd.Series]) -> float:
    """检查 8-21-55-144-250 五线排列.

    完美多头(5线依次向上) → +30
    4线多头 → +20
    3线多头 → +10
    缠绕 → 0
    3线空头 → -10
    4线空头 → -20
    完美空头 → -30
    """
    last_vals = {}
    for p in MA_PERIODS:
        v = emas[p].iloc[-1]
        if pd.isna(v):
            return 0.0
        last_vals[p] = v

    pairs = list(zip(MA_PERIODS, MA_PERIODS[1:]))
    bullish = sum(1 for s, l in pairs if last_vals[s] > last_vals[l])
    bearish = sum(1 for s, l in pairs if last_vals[s] < last_vals[l])

    if bullish == 4:    # 完美多头 (4对全部短期>长期)
        return 30.0
    elif bullish == 3:
        return 20.0
    elif bullish == 2:
        return 10.0
    elif bearish == 4:   # 完美空头
        return -30.0
    elif bearish == 3:
        return -20.0
    elif bearish == 2:
        return -10.0
    else:
        return 0.0       # 缠绕


# ── 3.3 趋势强度评分 (20分) ────────────────────────

def trend_strength_score(df: pd.DataFrame, emas: dict[int, pd.Series]) -> float:
    """EMA21 斜率 + RSI14 + 成交量确认."""
    score = 0.0

    # EMA21 5日斜率
    ema21 = emas[21]
    if len(ema21) >= 6:
        slope_5d = (ema21.iloc[-1] - ema21.iloc[-6]) / ema21.iloc[-6] * 100 / 5
        if slope_5d > 0.2:
            score += 10
        elif slope_5d > 0.05:
            score += 5
        elif slope_5d > -0.05:
            score += 0
        elif slope_5d > -0.2:
            score -= 5
        else:
            score -= 10

    # RSI14
    close = df["Close"]
    if len(close) >= 15:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean().replace(0, 1)
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
        rsi_val = rsi.iloc[-1]
        if pd.notna(rsi_val):
            if 80 <= rsi_val <= 100:
                score -= 5       # 极度超买
            elif 70 <= rsi_val < 80:
                score -= 3       # 超买风险
            elif 30 <= rsi_val < 70:
                score += 0       # 正常区间
            elif rsi_val < 30:
                score -= 2       # 超卖，反弹预期弱

    # 成交量确认: 突破均线时放量加分, 缩量突破减分
    if "Volume" in df.columns and len(close) >= 22:
        vol = df["Volume"]
        vol_ma20 = vol.rolling(20).mean()
        vol_ratio = vol.iloc[-1] / (vol_ma20.iloc[-1] + 1)
        close_ma20 = close.rolling(20).mean()
        above_ma = close.iloc[-1] > close_ma20.iloc[-1]
        if above_ma and vol_ratio > 1.5:       # 放量站上均线
            score += 2
        elif above_ma and vol_ratio > 1.0:     # 温和放量
            score += 1
        elif not above_ma and vol_ratio > 2.0: # 放量跌破均线(出货)
            score -= 2

    return max(-15.0, min(20.0, score))


# ── 3.4 筹码形态评分 (20分) ────────────────────────

async def chip_score_async(ts_code: str, trade_date: date, df: pd.DataFrame | None = None) -> tuple[float, bool]:
    """筹码形态评分.

    优先使用 chip_daily 表数据(获利盘比例+集中度)。
    若 chip_daily 为空，使用日K线价格位置作为代理估算:
      - 收盘价在60日价格区间的位置 → 近似获利盘比例
      - 60日振幅倒数 → 近似筹码集中度
    代理评分权重减半(10分)，其余10分分配给排列分。

    Returns:
        (score, has_real_chip_data)
    """
    chip = await load_chip_data(ts_code, trade_date)
    if chip is not None:
        # 真实筹码数据
        score = 0.0
        profit_ratio = chip.get("profit_ratio")
        concentration = chip.get("concentration")
        if profit_ratio is not None:
            if profit_ratio > 80:    score += 10
            elif profit_ratio > 60:  score += 5
            elif profit_ratio < 20:  score -= 10
        if concentration is not None:
            if concentration < 10:   score += 5
            elif concentration > 30: score -= 5
        return max(-15.0, min(20.0, score)), True

    # 降级：基于K线的代理估算
    if df is None or len(df) < 60:
        return 0.0, False

    close = df["Close"]
    high_60 = df["High"].rolling(60).max().iloc[-1]
    low_60 = df["Low"].rolling(60).min().iloc[-1]
    price_range = high_60 - low_60

    score = 0.0
    if price_range > 0:
        # 价格位置 = (close - low) / (high - low) * 100, 值越高越接近顶部
        price_position = (close.iloc[-1] - low_60) / price_range * 100
        if price_position > 80:
            score -= 3    # 接近60日高位，获利盘多但回调风险大
        elif price_position > 60:
            score += 2    # 偏高位但未到极端
        elif price_position > 20:
            score += 3    # 中间位置，健康
        elif price_position >= 0:
            score += 1    # 接近低位，可能有支撑

        # 振幅倒数代理集中度: 振幅小=筹码集中
        amplitude_60 = (df["High"].rolling(60).max().iloc[-1] / df["Low"].rolling(60).min().iloc[-1] - 1) * 100
        if amplitude_60 < 20:
            score += 2    # 60日振幅<20%, 筹码较集中
        elif amplitude_60 > 50:
            score -= 2    # 振幅>50%, 筹码分散

    # 代理评分上限10分(减半)
    return max(-8.0, min(10.0, score)), False


# ── 综合评分 ─────────────────────────────────────

async def calc_ma_score(ts_code: str, trade_date: Optional[date] = None) -> Optional[dict]:
    """计算均线趋势质量综合分.

    Returns:
        None 如果K线不足250根
        {"score": 0-100, "deviation": float, "alignment": float, "trend": float,
         "chip": float, "has_chip": bool, "details": str}
    """
    if trade_date is None:
        trade_date = date.today()

    df = await load_kline_for_ma(ts_code, trade_date)
    if df is None:
        return None

    emas = calc_emas(df)

    dev = deviation_score(df, emas)
    ali = alignment_score(emas)
    trend = trend_strength_score(df, emas)
    chip_val, has_real_chip = await chip_score_async(ts_code, trade_date, df)

    # 权重分配：真实筹码全部计入; 代理估算时排列40+筹码代理10
    if has_real_chip:
        total = dev + max(0, ali) + max(0, trend) + max(0, chip_val)
    else:
        total = dev + max(0, ali) * (40.0 / 30.0) + max(0, trend) + max(0, chip_val)
    score = max(0.0, min(100.0, total))

    details_parts = []
    if not has_real_chip:
        details_parts.append("筹码代理(K线估算),排列分权重提升至40")
    if ali < 0:
        details_parts.append(f"空头排列({ali:.0f}分),趋势破坏风险")

    return {
        "score": round(score, 1),
        "deviation": round(dev, 1),
        "alignment": round(ali, 1),
        "trend": round(trend, 1),
        "chip": round(chip_val, 1) if has_real_chip else None,
        "has_chip": has_real_chip,
        "details": "; ".join(details_parts) if details_parts else "ok",
    }


async def calc_support_resistance(ts_code: str, trade_date: date | None = None) -> dict | None:
    """纯技术计算支撑/压力位——不依赖LLM.

    支撑位: 近期低点, MA21, MA55 (取最近的两个)
    压力位: 近期高点, MA21, MA55 (取最近的两个)
    """
    if trade_date is None:
        trade_date = date.today()

    df = await load_kline_for_ma(ts_code, trade_date)
    if df is None or len(df) < 20:
        return None

    close = df["Close"]
    emas = calc_emas(df)
    last_close = close.iloc[-1]

    # 支撑/压力位候选
    low_20 = df["Low"].iloc[-20:].min()
    low_60 = df["Low"].iloc[-60:].min()
    high_20 = df["High"].iloc[-20:].max()
    high_60 = df["High"].iloc[-60:].max()
    ma21_v = emas[21].iloc[-1]
    ma55_v = emas[55].iloc[-1]

    supports = [v for v in [low_20, low_60, ma21_v, ma55_v] if v < last_close]
    resistances = [v for v in [high_20, high_60, ma21_v, ma55_v] if v > last_close]

    # 筹码分布：VWAP + 最大量价格
    vol = df["Volume"].iloc[-60:]
    close_60 = df["Close"].iloc[-60:]
    if len(vol) > 0 and vol.sum() > 0:
        vwap_60 = (close_60 * vol).sum() / vol.sum()
        max_vol_idx = vol.idxmax()
        peak_vol_price = df["Close"].iloc[max_vol_idx]
        for v in [vwap_60, peak_vol_price]:
            if v < last_close:
                supports.append(v)
            elif v > last_close:
                resistances.append(v)

    supports = sorted(set(round(v, 2) for v in supports), reverse=True)
    resistances = sorted(set(round(v, 2) for v in resistances))

    return {
        "support": round(supports[0], 2) if supports else None,
        "support2": round(supports[1], 2) if len(supports) > 1 else None,
        "resistance": round(resistances[0], 2) if resistances else None,
        "resistance2": round(resistances[1], 2) if len(resistances) > 1 else None,
        "close": round(float(last_close), 2),
    }
