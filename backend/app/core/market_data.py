"""统一基准数据 + 超额收益计算 (P0 系统级).

消除 3 处独立实现的超额收益计算不一致性。
核心函数:
  get_benchmark_closes()    — 带缓存的 700001.TI 日线加载 (替代 14 处分散 SQL)
  compute_excess_return()   — 统一超额收益公式 (交易日计数, 替代 3 处实现)
"""
import logging
from datetime import date
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("market_data")

# ══════════════════════════════════════════════════════════════════════
# 模块级缓存 (历史数据不可变, 缓存一次永久有效)
# ══════════════════════════════════════════════════════════════════════

_benchmark_closes: dict[date, float] = {}
_cache_loaded: bool = False


async def get_benchmark_closes(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    session=None,
) -> dict[date, float]:
    """加载 700001.TI (同花顺全A等权) 日线收盘价, 模块级缓存.

    历史日线数据不可变, 加载一次后永久缓存。
    支持按日期范围过滤。

    Args:
        start_date: 起始日期 (含)
        end_date: 截止日期 (含)
        session: 可选的共享 session

    Returns:
        {trade_date: close_price} dict
    """
    global _benchmark_closes, _cache_loaded

    # 懒加载
    if not _cache_loaded:
        try:
            async def _load(s):
                r = await s.execute(text(
                    "SELECT trade_date, close FROM daily_kline "
                    "WHERE ts_code = '700001.TI' ORDER BY trade_date"
                ))
                return {row[0]: float(row[1]) for row in r.fetchall() if row[1]}

            if session:
                _benchmark_closes = await _load(session)
            else:
                async with async_session_factory() as s:
                    _benchmark_closes = await _load(s)

            _cache_loaded = True
            logger.info(f"Benchmark cache loaded: {len(_benchmark_closes)} dates")
        except Exception as e:
            logger.warning(f"Benchmark cache load failed: {e}")
            return {}

    # 日期过滤
    if start_date or end_date:
        return {
            d: v for d, v in _benchmark_closes.items()
            if (not start_date or d >= start_date) and (not end_date or d <= end_date)
        }
    return dict(_benchmark_closes)


async def refresh_benchmark_cache():
    """强制刷新基准缓存 (数据源更新后调用)."""
    global _benchmark_closes, _cache_loaded
    _cache_loaded = False
    _benchmark_closes.clear()
    await get_benchmark_closes()


# ══════════════════════════════════════════════════════════════════════
# 统一超额收益计算
# ══════════════════════════════════════════════════════════════════════

def compute_excess_return(
    stock_close_today: float,
    stock_close_future: float,
    trade_date: date,
    horizon: int,
    benchmark_closes: Optional[dict[date, float]] = None,
    epsilon: float = 0.01,
) -> float:
    """统一的超额收益计算 (交易日计数, vs 700001.TI).

    公式:
      stock_ret = (stock_close_future - stock_close_today) / stock_close_today * 100
      market_t0 = benchmark 在 trade_date 之后的第一个交易日收盘价
      market_tN = benchmark 在 trade_date 之后的第 horizon 个交易日收盘价
      market_ret = (market_tN - market_t0) / market_t0 * 100
      excess_return = stock_ret - market_ret

    Args:
        stock_close_today: 股票当日收盘价
        stock_close_future: 股票 T+N 日收盘价
        trade_date: 基准日
        horizon: 时间窗口 (2/5/10/20, 交易日数)
        benchmark_closes: 可选, 预加载的基准数据。None 则返回 0.0 (调用方自行处理)
        epsilon: 最小价格阈值 (防止除零)

    Returns:
        超额收益 (%), 保留 4 位小数。数据不足时返回 0.0。

    Examples:
        >>> closes = {date(2024,6,3): 5000, date(2024,6,4): 5050, ...}
        >>> compute_excess_return(100, 103, date(2024,6,3), 5, closes)
        3.0 - (benchmark_ret) = 2.15
    """
    if not benchmark_closes:
        return 0.0

    stock_close_today = max(stock_close_today, epsilon)
    stock_ret = (stock_close_future - stock_close_today) / stock_close_today * 100

    # 找 trade_date 之后的交易日 (严格 >, 因为 trade_date 当天的收盘价反映的是当天的交易,
    # 而我们要的是"从今天收盘到未来"的基准收益, 基准 T+0 应该是下一个交易日)
    future_dates = sorted(d for d in benchmark_closes if d > trade_date)

    if len(future_dates) < horizon:
        return 0.0  # 数据不足

    c0 = benchmark_closes.get(future_dates[0], 0)
    cN = benchmark_closes.get(future_dates[horizon - 1], 0)

    if c0 <= 0 or cN <= 0:
        return 0.0

    market_ret = (cN - c0) / c0 * 100
    return round(stock_ret - market_ret, 4)


def compute_excess_return_or_fallback(
    stock_close_today: float,
    stock_close_future: float,
    trade_date: date,
    horizon: int,
    benchmark_closes: Optional[dict[date, float]] = None,
) -> float:
    """超额收益计算 — 数据不足时返回纯股票收益 (兼容 predictive_features 行为).

    注意: 此变体仅用于向后兼容。新代码应使用 compute_excess_return。
    """
    stock_close_today_safe = max(stock_close_today, 0.01)
    stock_ret = (stock_close_future - stock_close_today_safe) / stock_close_today_safe * 100

    if not benchmark_closes:
        return round(stock_ret, 4)

    future_dates = sorted(d for d in benchmark_closes if d > trade_date)

    if len(future_dates) < horizon:
        return round(stock_ret, 4)

    c0 = benchmark_closes.get(future_dates[0], 0)
    cN = benchmark_closes.get(future_dates[horizon - 1], 0)

    if c0 <= 0 or cN <= 0:
        return round(stock_ret, 4)

    market_ret = (cN - c0) / c0 * 100
    return round(stock_ret - market_ret, 4)
