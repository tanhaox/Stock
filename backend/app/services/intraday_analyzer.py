"""盘中异动分析 — 分钟K线形态判定出货/吸筹."""
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory


async def load_minute_kline(ts_code: str, trade_date: date) -> Optional[pd.DataFrame]:
    """加载最近交易日的分钟K线(不指定具体日期，取最新的)."""
    async with async_session_factory() as s:
        # 先取最近有数据的交易日
        r = await s.execute(text("""
            SELECT trade_time::date FROM min_kline
            WHERE ts_code=:s AND trade_time::date <= :d
            ORDER BY trade_time::date DESC LIMIT 1
        """), {"s": ts_code, "d": trade_date})
        row = r.fetchone()
        if not row:
            return None
        latest_date = row[0]

        r = await s.execute(text("""
            SELECT trade_time, open, high, low, close, volume
            FROM min_kline
            WHERE ts_code=:s AND trade_time::date = :d
            ORDER BY trade_time
        """), {"s": ts_code, "d": latest_date})
        rows = r.fetchall()
    if len(rows) < 30:
        return None
    df = pd.DataFrame(rows, columns=["Time", "Open", "High", "Low", "Close", "Volume"])
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


async def load_daily_moneyflow(ts_code: str, trade_date: date) -> Optional[dict]:
    """加载当日资金流向."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT buy_elg_amount, sell_elg_amount, buy_lg_amount, sell_lg_amount,
                   buy_md_amount, sell_md_amount, buy_sm_amount, sell_sm_amount
            FROM moneyflow WHERE ts_code=:s AND trade_date=:d
        """), {"s": ts_code, "d": trade_date})
        row = r.fetchone()
        if not row:
            return None
        return {
            "big_net": float(row[0] or 0) - float(row[1] or 0),     # 超大单净
            "large_net": float(row[2] or 0) - float(row[3] or 0),   # 大单净
            "mid_net": float(row[4] or 0) - float(row[5] or 0),     # 中单净
            "small_net": float(row[6] or 0) - float(row[7] or 0),   # 小单净
        }


async def analyze_intraday_move(ts_code: str, trade_date: date | None = None) -> Optional[dict]:
    """分析日内异动: 判定2%+涨幅是出货还是吸筹.

    Returns:
        {
            "max_gain_pct": float,        # 最大涨幅%
            "retrace_ratio": float,       # 回撤比(0-1), >0.5=出货嫌疑
            "volume_profile": str,        # 量价形态描述
            "big_order_bias": str,        # 大单偏向
            "verdict": str,               # 综合判定
        }
    """
    if trade_date is None:
        trade_date = date.today()

    df = await load_minute_kline(ts_code, trade_date)
    if df is None:
        return None

    mf = await load_daily_moneyflow(ts_code, trade_date)

    close = df["Close"]
    vol = df["Volume"]
    n = len(df)
    if n < 30:
        return None

    # 找当日最大涨幅段
    open_price = df["Open"].iloc[0]
    peak_price = df["High"].max()
    peak_idx = df["High"].idxmax()
    current_price = close.iloc[-1]
    day_gain = (current_price - open_price) / open_price * 100
    max_gain = (peak_price - open_price) / open_price * 100

    # 只分析涨幅>2%的情况
    if max_gain < 2.0:
        return {"verdict": "涨幅不足2%,无需判定", "max_gain_pct": round(max_gain, 1)}

    # 回撤比: (峰值-现价)/(峰值-开盘) — 值越大说明涨幅回吐越多,出货嫌疑越大
    if peak_price > open_price:
        retrace_ratio = (peak_price - current_price) / (peak_price - open_price)
    else:
        retrace_ratio = 0

    # 量价形态: 上涨段量 vs 盘整段量
    # 上涨段: 从开盘到峰值; 盘整段: 峰值到收盘
    rise_vol = vol.iloc[:peak_idx + 1].mean() if peak_idx > 0 else 0
    rest_vol = vol.iloc[peak_idx + 1:].mean() if peak_idx < n - 1 else 0
    avg_vol = vol.mean()

    # 判断量价形态
    if rest_vol > 0:
        vol_ratio = rise_vol / rest_vol
    else:
        vol_ratio = 999

    if vol_ratio > 1.5 and retrace_ratio < 0.3:
        vol_profile = "放量上涨+缩量盘整(筹码稳定)"
    elif vol_ratio < 0.7 and retrace_ratio > 0.5:
        vol_profile = "缩量拉升+放量回落(出货特征)"
    elif vol_ratio > 1.2 and retrace_ratio < 0.5:
        vol_profile = "温和放量+小幅回撤(偏吸筹)"
    elif retrace_ratio > 0.6:
        vol_profile = "高位放量回落(出货嫌疑)"
    else:
        vol_profile = "量价中性"

    # 大单偏向
    if mf:
        big_total = mf["big_net"] + mf["large_net"]
        if big_total > 1000000:     # >100万净买入
            big_bias = "大单净买入(吸筹)"
        elif big_total < -1000000:  # >100万净卖出
            big_bias = "大单净卖出(出货)"
        else:
            big_bias = "大单中性"
    else:
        big_bias = "无资金数据"

    # 综合判定
    score = 0
    if retrace_ratio > 0.5:
        score -= 2
    elif retrace_ratio < 0.3:
        score += 1
    if vol_ratio < 0.7:
        score -= 1
    elif vol_ratio > 1.5:
        score += 1
    if "净买入" in big_bias:
        score += 2
    elif "净卖出" in big_bias:
        score -= 2

    if score >= 3:
        verdict = "主力吸筹 — 涨幅稳定+大单流入,可关注后续"
    elif score >= 0:
        verdict = "偏中性 — 暂无明显出货迹象"
    elif score >= -2:
        verdict = "出货嫌疑 — 涨幅回吐+大单流出,需警惕"
    else:
        verdict = "明确出货 — 拉高减仓特征明显"

    return {
        "max_gain_pct": round(max_gain, 1),
        "day_gain_pct": round(day_gain, 1),
        "retrace_ratio": round(retrace_ratio, 2),
        "volume_profile": vol_profile,
        "big_order_bias": big_bias,
        "verdict": verdict,
        "peak_price": round(float(peak_price), 2),
        "current_price": round(float(current_price), 2),
    }
