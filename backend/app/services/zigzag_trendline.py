"""趋势线指标 (ZigZag TrendLine) — 同花顺趋势自动线复刻 v1.0.

同花顺"趋势自动线"本质是 ZigZag(5%) 折线:
  - 线上时跟随新的高低点延伸
  - 转向(反转 5%)时旧线消失、新线出现
  - 特征: 上升趋势线开始 → 较长的上涨过程；趋势线消失 → 趋势反转

本模块提供两个版本:
  1. _zigzag_trendline(): 纯 NumPy 版，用于批量计算
  2. compute_zigzag_signal(): 带 DB 查询版，用于 API 调用

Returns:
    {trend_active, direction, zig_value, days_active, turn_date, turn_price, signal}
    signal: "trend_up" / "trend_down" / "none"
"""

import numpy as np
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 同花顺标准 ZigZag 参数 (可配置)
ZIG_DEVIATION = 0.03   # 3% 反转确认 (匹配同花顺趋势线)
ZIG_DEPTH = 12         # 最少12根K线
ZIG_BACKSTEP = 3       # 回看3根确认


def _zigzag_trendline(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> dict:
    """ZigZag(5%) 趋势线 — 纯 NumPy 实现.

    Args:
        closes, highs, lows: 价格数组 (最近的在最后)

    Returns:
        {active, direction, zig_value, days_active, turn_date (index), signal}
        active: 趋势线是否显示
        direction: "up" / "down"
        zig_value: 当前趋势线的坐标值
        signal: "trend_up" (上升趋势中) / "trend_down" (下降趋势中) / "none" (无趋势)
    """
    n = len(closes)
    if n < ZIG_DEPTH + 5:
        return {"active": False, "direction": "none", "zig_value": float(closes[-1]),
                "days_active": 0, "signal": "none"}

    direction = 1  # 1=上升, -1=下降
    extreme_val = closes[0]; extreme_idx = 0
    last_turn_val = closes[0]; last_turn_idx = 0
    last_turn_dir = 0  # 1=顶部, -1=底部

    for i in range(1, n):
        if direction == 1:
            if highs[i] > extreme_val:
                extreme_val = highs[i]; extreme_idx = i
            if closes[i] < extreme_val * (1 - ZIG_DEVIATION):
                direction = -1
                last_turn_val = extreme_val; last_turn_idx = extreme_idx
                last_turn_dir = 1
                extreme_val = lows[i]; extreme_idx = i
        else:
            if lows[i] < extreme_val:
                extreme_val = lows[i]; extreme_idx = i
            if closes[i] > extreme_val * (1 + ZIG_DEVIATION):
                direction = 1
                last_turn_val = extreme_val; last_turn_idx = i
                last_turn_dir = -1
                extreme_val = highs[i]; extreme_idx = i

    # 当前趋势线状态
    days_active = n - 1 - last_turn_idx
    active = days_active >= ZIG_BACKSTEP  # 至少需要 3 天确认

    if direction == 1:  # 上升趋势
        zig_value = float(last_turn_val) if last_turn_dir == -1 else float(closes[last_turn_idx])
        signal = "trend_up" if active else "none"
    else:  # 下降趋势
        zig_value = float(last_turn_val) if last_turn_dir == 1 else float(closes[last_turn_idx])
        signal = "trend_down" if active else "none"

    return {
        "active": active,
        "direction": "up" if direction == 1 else "down",
        "zig_value": round(zig_value, 2),
        "days_active": days_active,
        "turn_price": round(float(last_turn_val), 2),
        "current_price": round(float(closes[-1]), 2),
        "signal": signal,
    }


async def compute_zigzag_signal(symbol: str) -> Optional[dict]:
    """计算单只股票的 ZigZag 趋势线信号 (API 版).

    Returns:
        {active, direction, zig_value, days_active, signal, symbol}
    """
    from sqlalchemy import text
    from app.core.database import async_session_factory

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT close, high, low FROM daily_kline "
            "WHERE ts_code = :c ORDER BY trade_date"
        ), {"c": symbol})
        rows = r.fetchall()

    if len(rows) < 30:
        return None

    closes = np.array([float(rw[0] or 0) for rw in rows])
    highs = np.array([float(rw[1] or closes[i]) for i, rw in enumerate(rows)])
    lows = np.array([float(rw[2] or closes[i]) for i, rw in enumerate(rows)])

    result = _zigzag_trendline(closes, highs, lows)
    result["symbol"] = symbol
    return result


def batch_zigzag_from_arrays(kline_dict: dict[str, tuple]) -> dict[str, dict]:
    """批量计算 ZigZag 趋势线 — 纯内存运算, 无 DB I/O.

    Args:
        kline_dict: {symbol: (closes_array, highs_array, lows_array)}

    Returns:
        {symbol: _zigzag_trendline_result}
    """
    results = {}
    for sym, (cs, hs, ls) in kline_dict.items():
        try:
            results[sym] = _zigzag_trendline(cs, hs, ls)
        except Exception:
            pass
    return results
