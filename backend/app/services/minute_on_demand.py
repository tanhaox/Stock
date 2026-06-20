"""分时数据按需计算插件 — minute_on_demand.

设计理念:
  - 不预存储大量分时数据
  - 按需从 Tushare 拉取，实时计算
  - LRU 缓存近期数据，避免重复调用

调用场景:
  - N/M 形态检测
  - 三线对比
  - DNA 表情提取
  - AlphaFlow 验证
  - 锁死区间分析

作者: P0级基建 v1.0
日期: 2026-06-14
"""

import asyncio
import logging
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.services.tushare_common import call_tushare

logger = logging.getLogger("minute_on_demand")

# ── 常量 ──
PERIODS = ["5min", "15min", "30min", "60min"]
DEFAULT_LOOKBACK_DAYS = 5
MAX_CACHE_SIZE = 100  # LRU 缓存最大条目数
CACHE_TTL_SECONDS = 3600  # 1小时过期

# ── LRU 缓存 ──
_cache: OrderedDict[str, list[dict]] = OrderedDict()
_cache_times: dict[str, datetime] = {}


def _make_cache_key(ts_code: str, period: str, trade_date: date | None) -> str:
    """生成缓存键."""
    d = trade_date.strftime("%Y%m%d") if trade_date else "latest"
    return f"{ts_code}:{period}:{d}"


def _get_cached(cache_key: str) -> list[dict] | None:
    """获取缓存（检查 TTL）."""
    if cache_key not in _cache:
        return None

    # 检查 TTL
    if cache_key in _cache_times:
        age = (datetime.now() - _cache_times[cache_key]).total_seconds()
        if age > CACHE_TTL_SECONDS:
            # 过期，删除
            _cache.pop(cache_key, None)
            _cache_times.pop(cache_key, None)
            return None

    # 移到末尾（LRU）
    _cache.move_to_end(cache_key)
    return _cache.get(cache_key)


def _set_cache(cache_key: str, data: list[dict]):
    """设置缓存（LRU 淘汰）."""
    _cache[cache_key] = data
    _cache_times[cache_key] = datetime.now()
    _cache.move_to_end(cache_key)

    # LRU 淘汰
    while len(_cache) > MAX_CACHE_SIZE:
        oldest_key = next(iter(_cache))
        _cache.pop(oldest_key)
        _cache_times.pop(oldest_key, None)


# ═══════════════════════════════════════════════════════════
#  Tushare 分时数据拉取
# ═══════════════════════════════════════════════════════════

async def _fetch_raw_bars(
    ts_code: str,
    trade_date: date,
    freq: str = "5min",
) -> list[dict]:
    """从 Tushare 拉取分时数据.

    注意: Tushare 单日查询返回空，需要查询 start 到 start+1 天。

    Args:
        ts_code: 股票代码
        trade_date: 交易日期
        freq: 周期 '1min'/'5min'

    Returns:
        原始分时数据列表
    """
    # Tushare 单日查询有 bug，需要查 start 到 start+1
    start_str = trade_date.strftime("%Y%m%d")
    end_str = (trade_date + timedelta(days=1)).strftime("%Y%m%d")

    try:
        rows = await call_tushare(
            "stk_mins",
            {
                "ts_code": ts_code,
                "freq": freq,
                "start_date": start_str,
                "end_date": end_str,
            },
            "ts_code,trade_time,open,high,low,close,volume,amount"
        )
    except Exception as e:
        logger.warning(f"stk_mins failed for {ts_code} @ {start_str}: {e}")
        return []

    if not rows:
        return []

    # 过滤只保留目标日期的数据
    target_date_str = trade_date.strftime("%Y-%m-%d")

    # 转换为标准格式
    bars = []
    for row in rows:
        try:
            tt = row.get("trade_time", "")
            if not tt:
                continue

            # 处理 datetime 对象或字符串
            if hasattr(tt, 'strftime'):
                # datetime 对象：提取日期字符串
                date_str = tt.strftime("%Y-%m-%d")
                # 移除时区信息
                if tt.tzinfo is not None:
                    tt = tt.replace(tzinfo=None)
            else:
                # 字符串：直接取前10个字符
                date_str = str(tt)[:10]

            # 只保留目标日期的数据
            if date_str != target_date_str:
                continue

            bars.append({
                "ts_code": ts_code,
                "trade_time": tt if hasattr(tt, 'strftime') else tt,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "volume": int(row.get("volume", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
            })
        except Exception:
            continue

    return bars


# ═══════════════════════════════════════════════════════════
#  数据库分时数据回退
# ═══════════════════════════════════════════════════════════

async def _fetch_from_db(
    ts_code: str,
    trade_date: date,
) -> list[dict]:
    """从本地 min_kline 表获取分时数据.

    当 Tushare stk_mins 接口返回空时使用本地已同步数据。
    min_kline 表已同步 74 只活跃股（主要是 AlphaFlow 池）。

    Args:
        ts_code: 股票代码
        trade_date: 交易日期

    Returns:
        原始分时数据列表（5分钟线）
    """
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as session:
        date_str = trade_date.strftime("%Y-%m-%d")
        stmt = text("""
            SELECT ts_code, trade_time, open, high, low, close, volume, amount
            FROM min_kline
            WHERE ts_code = :ts_code
              AND trade_time >= CAST(:start_date AS date)
              AND trade_time < CAST(:start_date AS date) + interval '1 day'
            ORDER BY trade_time
        """).bindparams(ts_code=ts_code, start_date=date_str)
        result = await session.execute(stmt)
        rows = result.fetchall()

    if not rows:
        return []

    bars = []
    for row in rows:
        tt = row[1]
        # 移除时区信息
        if hasattr(tt, 'tzinfo') and tt.tzinfo is not None:
            tt = tt.replace(tzinfo=None)
        bars.append({
            "ts_code": row[0],
            "trade_time": tt,
            "open": float(row[2] or 0),
            "high": float(row[3] or 0),
            "low": float(row[4] or 0),
            "close": float(row[5] or 0),
            "volume": int(row[6] or 0),
            "amount": float(row[7] or 0),
        })

    return bars


# ═══════════════════════════════════════════════════════════
#  分时聚合计算
# ═══════════════════════════════════════════════════════════

def resample_to_period(bars: list[dict], period: str) -> list[dict]:
    """将分时K线聚合到指定周期.

    Args:
        bars: 原始分时K线列表
        period: '5min'/'15min'/'30min'/'60min'

    Returns:
        聚合后的K线列表
    """
    if not bars:
        return []

    df = pd.DataFrame(bars)
    # 移除时区信息（统一为 naive datetime）
    if pd.api.types.is_datetime64_any_dtype(df["trade_time"]):
        try:
            if df["trade_time"].dt.tz is not None:
                df["trade_time"] = df["trade_time"].dt.tz_convert(None)
        except Exception:
            pass
    else:
        try:
            df["trade_time"] = pd.to_datetime(df["trade_time"], utc=True).dt.tz_convert(None)
        except Exception:
            df["trade_time"] = pd.to_datetime(df["trade_time"])
    df = df.sort_values("trade_time")

    freq_map = {
        "5min": "5T",
        "15min": "15T",
        "30min": "30T",
        "60min": "60T",
    }
    freq = freq_map.get(period, "5T")

    # 按周期重采样
    grouped = df.set_index("trade_time").resample(freq)

    result = []
    for ts, group in grouped:
        if group.empty:
            continue
        result.append({
            "period": period,
            "trade_time": ts,
            "open": float(group["open"].iloc[0]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group["close"].iloc[-1]),
            "volume": int(group["volume"].sum()),
            "amount": float(group["amount"].sum()),
        })

    return result


def compute_pct_chg(bars: list[dict]) -> list[dict]:
    """计算涨跌幅."""
    if len(bars) < 2:
        for bar in bars:
            bar["pct_chg"] = 0.0
        return bars

    for i, bar in enumerate(bars):
        if i == 0:
            bar["pct_chg"] = 0.0
        else:
            prev_close = bars[i - 1]["close"]
            if prev_close > 0:
                bar["pct_chg"] = round((bar["close"] - prev_close) / prev_close * 100, 4)
            else:
                bar["pct_chg"] = 0.0

    return bars


# ═══════════════════════════════════════════════════════════
#  主入口: 按需获取分时数据
# ═══════════════════════════════════════════════════════════

async def get_minute_bars(
    ts_code: str,
    period: str = "5min",
    days: int = 5,
    trade_date: date | None = None,
    use_cache: bool = True,
) -> list[dict]:
    """按需获取分时数据，优先从本地数据库获取，失败则调用 Tushare.

    流程:
        1. 检查缓存
        2. 尝试从本地 min_kline 表获取（已同步的 74 只活跃股）
        3. 如果数据库为空，调用 Tushare stk_mins 接口
        4. 聚合到指定周期
        5. 缓存近期数据
        6. 返回结果

    Args:
        ts_code: 股票代码
        period: 周期 '5min'/'15min'/'30min'/'60min'
        days: 回看天数
        trade_date: 指定日期，默认今天
        use_cache: 是否使用缓存

    Returns:
        [{trade_time, open, high, low, close, volume, pct_chg}, ...]
    """
    if trade_date is None:
        trade_date = date.today()

    cache_key = _make_cache_key(ts_code, period, trade_date)

    # 1. 检查缓存
    if use_cache:
        cached = _get_cached(cache_key)
        if cached is not None:
            return cached

    # 2. 收集多天数据（优先数据库，失败则 Tushare）
    all_bars = []
    for i in range(days):
        d = trade_date - timedelta(days=i)

        # 2.1 优先从数据库获取（已有 min_kline 数据）
        bars = await _fetch_from_db(ts_code, d)

        # 2.2 数据库为空则尝试 Tushare
        if not bars:
            bars = await _fetch_raw_bars(ts_code, d)

        all_bars.extend(bars)

    if not all_bars:
        return []

    # 统一所有数据的 trade_time 类型（统一转为 datetime）
    for bar in all_bars:
        tt = bar.get("trade_time") or bar.get("time") or ""
        if isinstance(tt, str):
            # 字符串转为 datetime
            try:
                dt = datetime.fromisoformat(tt.replace(" ", "T"))
                if dt.tzinfo is not None:
                    dt = dt.replace(tzinfo=None)
                bar["trade_time"] = dt
            except Exception:
                bar["trade_time"] = datetime.now()
        elif hasattr(tt, 'tzinfo') and tt.tzinfo is not None:
            # 有时区的 datetime 移除时区
            bar["trade_time"] = tt.replace(tzinfo=None)

    # 排序
    all_bars.sort(key=lambda x: x.get("trade_time") or datetime.min)
    if period != "5min":
        agg_bars = resample_to_period(all_bars, period)
    else:
        agg_bars = all_bars

    # 4. 计算涨跌幅
    agg_bars = compute_pct_chg(agg_bars)

    # 5. 缓存（限制条数）
    if use_cache and len(agg_bars) <= 500:
        _set_cache(cache_key, agg_bars)

    return agg_bars


# ═══════════════════════════════════════════════════════════
#  批量获取: 多只股票
# ═══════════════════════════════════════════════════════════

async def get_minute_bars_batch(
    ts_codes: list[str],
    period: str = "5min",
    days: int = 5,
    trade_date: date | None = None,
    max_concurrent: int = 3,
) -> dict[str, list[dict]]:
    """批量获取多只股票的分时数据.

    内部控制并发数，避免触发 Tushare 限流。

    Args:
        ts_codes: 股票代码列表
        period: 周期
        days: 回看天数
        trade_date: 指定日期
        max_concurrent: 最大并发数

    Returns:
        {ts_code: bars, ...}
    """
    if not ts_codes:
        return {}

    sem = asyncio.Semaphore(max_concurrent)

    async def _fetch_one(code: str) -> tuple[str, list[dict]]:
        async with sem:
            bars = await get_minute_bars(code, period, days, trade_date)
            return code, bars

    tasks = [_fetch_one(code) for code in ts_codes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    result = {}
    for item in results:
        if isinstance(item, Exception):
            logger.warning(f"Batch fetch failed: {item}")
            continue
        code, bars = item
        result[code] = bars

    return result


# ═══════════════════════════════════════════════════════════
#  形态数据: 用于 N/M 检测
# ═══════════════════════════════════════════════════════════

async def get_intraday_bars(
    ts_code: str,
    trade_date: date | None = None,
) -> dict:
    """获取日内分时数据 + 预处理（用于 N/M 形态检测）.

    Args:
        ts_code: 股票代码
        trade_date: 交易日期

    Returns:
        {
            bars_1min: [...],    # 原始1分钟
            bars_5min: [...],    # 聚合5分钟
            morning: {...},      # 上午统计
            afternoon: {...},    # 下午统计
            pattern: 'N'/'M'/None,  # 检测到的形态
        }
    """
    if trade_date is None:
        trade_date = date.today()

    # 获取1分钟数据
    bars_1min = await _fetch_raw_bars(ts_code, trade_date, freq="1min")
    if not bars_1min:
        # 尝试从数据库获取5分钟数据（min_kline 只有5分钟线）
        bars_5min = await _fetch_from_db(ts_code, trade_date)
        if not bars_5min:
            return {
                "bars_1min": [],
                "bars_5min": [],
                "morning": {},
                "afternoon": {},
                "pattern": None,
                "pattern_confidence": 0,
                "pattern_detail": "数据不足",
            }

        # 上下午分割
        half = len(bars_5min) // 2
        morning = bars_5min[:half] if half > 0 else []
        afternoon = bars_5min[half:] if half < len(bars_5min) else []

        # 计算上下午统计
        def _calc_stats(bars: list[dict]) -> dict:
            if not bars:
                return {}
            opens = [b["open"] for b in bars]
            highs = [b["high"] for b in bars]
            lows = [b["low"] for b in bars]
            closes = [b["close"] for b in bars]
            volumes = [b["volume"] for b in bars]

            return {
                "open": opens[0],
                "high": max(highs),
                "low": min(lows),
                "close": closes[-1],
                "volume": sum(volumes),
                "pct_chg": round((closes[-1] - opens[0]) / opens[0] * 100, 2) if opens[0] > 0 else 0,
            }

        morning_stats = _calc_stats(morning)
        afternoon_stats = _calc_stats(afternoon)

        # N/M 形态检测
        pattern, confidence, detail = _detect_nm_pattern(morning_stats, afternoon_stats)

        return {
            "bars_1min": [],
            "bars_5min": bars_5min,
            "morning": morning_stats,
            "afternoon": afternoon_stats,
            "pattern": pattern,
            "pattern_confidence": confidence,
            "pattern_detail": detail,
        }

    # 聚合到5分钟
    bars_5min = resample_to_period(bars_1min, "5min")
    bars_5min = compute_pct_chg(bars_5min)

    # 上下午分割
    half = len(bars_5min) // 2
    morning = bars_5min[:half] if half > 0 else []
    afternoon = bars_5min[half:] if half < len(bars_5min) else []

    # 计算上下午统计
    def _calc_stats(bars: list[dict]) -> dict:
        if not bars:
            return {}
        opens = [b["open"] for b in bars]
        highs = [b["high"] for b in bars]
        lows = [b["low"] for b in bars]
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]

        return {
            "open": opens[0],
            "high": max(highs),
            "low": min(lows),
            "close": closes[-1],
            "volume": sum(volumes),
            "pct_chg": round((closes[-1] - opens[0]) / opens[0] * 100, 2) if opens[0] > 0 else 0,
        }

    morning_stats = _calc_stats(morning)
    afternoon_stats = _calc_stats(afternoon)

    # N/M 形态检测
    pattern, confidence, detail = _detect_nm_pattern(morning_stats, afternoon_stats)

    return {
        "bars_1min": bars_1min,
        "bars_5min": bars_5min,
        "morning": morning_stats,
        "afternoon": afternoon_stats,
        "pattern": pattern,
        "pattern_confidence": confidence,
        "pattern_detail": detail,
    }


def _detect_nm_pattern(
    morning: dict,
    afternoon: dict,
) -> tuple[str | None, float, str]:
    """检测 N/M 形态.

    Args:
        morning: 上午统计
        afternoon: 下午统计

    Returns:
        (pattern, confidence, detail)
    """
    if not morning or not afternoon:
        return None, 0, "数据不足"

    m_open = morning.get("open", 0)
    m_high = morning.get("high", 0)
    m_low = morning.get("low", 0)
    m_close = morning.get("close", 0)

    a_open = afternoon.get("open", 0)
    a_high = afternoon.get("high", 0)
    a_low = afternoon.get("low", 0)
    a_close = afternoon.get("close", 0)

    # 计算涨跌
    morning_drop = (m_open - m_low) / m_open if m_open > 0 else 0  # 早盘下跌幅度
    morning_rise = (m_high - m_open) / m_open if m_open > 0 else 0   # 早盘上涨幅度
    afternoon_rise = (a_high - a_open) / a_open if a_open > 0 else 0  # 午盘上涨幅度
    afternoon_drop = (a_open - a_low) / a_open if a_open > 0 else 0  # 午盘下跌幅度

    # N型特征: 早盘下跌后午盘反弹
    if morning_drop > 0.01 and afternoon_rise > morning_drop * 0.5:
        confidence = min(0.9, morning_drop * 10)
        detail = f"N型吸筹: 早盘-{morning_drop*100:.1f}%, 午盘+{afternoon_rise*100:.1f}%"
        return "N", confidence, detail

    # M型特征: 早盘上涨后午盘下跌
    if morning_rise > 0.01 and afternoon_drop > morning_rise * 0.5:
        confidence = min(0.9, morning_rise * 10)
        detail = f"M型出货: 早盘+{morning_rise*100:.1f}%, 午盘-{afternoon_drop*100:.1f}%"
        return "M", confidence, detail

    return None, 0, "无明显形态"


# ═══════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════

def clear_cache():
    """清空缓存."""
    global _cache, _cache_times
    _cache.clear()
    _cache_times.clear()
    logger.info("Cache cleared")


def get_cache_stats() -> dict:
    """获取缓存统计."""
    return {
        "size": len(_cache),
        "max_size": MAX_CACHE_SIZE,
        "keys": list(_cache.keys())[:10],  # 只显示前10个
    }