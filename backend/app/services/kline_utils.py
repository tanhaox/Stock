"""统一的前复权 K 线工具函数 (P0 系统级除权).

所有模块应使用此模块的函数获取除权安全的 K 线数据，
替代直接查询 daily_kline 原始表。

核心:
  get_adjusted_kline()     — 获取前复权 K 线 (DataFrame)
  get_ex_rights_dates()    — 从 adj_factor 列精确识别除权日
  is_ex_rights_date()      — 判断单个日期是否为除权日
  iter_non_exrights_chunks() — 按除权日切分连续 K 线段
"""
import logging
import numpy as np
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("kline_utils")


async def get_adjusted_kline(
    symbol: str,
    start_date=None,
    end_date=None,
    session=None,
) -> list[dict]:
    """获取前复权 K 线 (list-of-dict 格式, 兼容现有代码).

    daily_kline 中的 open/high/low/close 在 v2.8+ 均已是前复权价格
    (tg_engine.py 拉取时使用 adj='qfq' 参数)。
    此函数同时返回 adj_factor 列供除权日识别。

    Args:
        symbol: 股票代码
        start_date: 起始日期 (date 或 str)
        end_date: 截止日期 (date 或 str)
        session: 可选, 外部传入的数据库 session

    Returns:
        [{trade_date, open, high, low, close, volume, amount, adj_factor}, ...]
        按 trade_date 升序
    """
    if start_date is None:
        start_date = date.today() - timedelta(days=365)
    if end_date is None:
        end_date = date.today()

    sql = """
        SELECT trade_date, open, high, low, close, volume, amount,
               COALESCE(adj_factor, 1.0) AS adj_factor
        FROM daily_kline
        WHERE ts_code = :sym
          AND trade_date BETWEEN :sd AND :ed
        ORDER BY trade_date
    """

    async def _query(s):
        r = await s.execute(text(sql), {"sym": symbol, "sd": start_date, "ed": end_date})
        return [{"trade_date": row[0], "open": float(row[1] or 0), "high": float(row[2] or 0),
                 "low": float(row[3] or 0), "close": float(row[4] or 0),
                 "volume": float(row[5] or 0), "amount": float(row[6] or 0),
                 "adj_factor": float(row[7] or 1.0)}
                for row in r.fetchall() if float(row[4] or 0) > 0]

    if session:
        return await _query(session)
    async with async_session_factory() as s:
        return await _query(s)


def get_ex_rights_dates(
    kline_rows: list[dict],
    threshold: float = 0.01,
) -> list[date]:
    """从 adj_factor 列精确识别除权日.

    除权日的 adj_factor 与前一交易日不同 (差值 > threshold).
    此方法比阈值猜测法 (15%/18%/20%) 精确 100%。

    Args:
        kline_rows: 从 get_adjusted_kline() 获取的 K 线列表 (含 adj_factor)
        threshold: adj_factor 变化阈值, 默认 0.01 (1%)

    Returns:
        除权日期列表
    """
    if len(kline_rows) < 2:
        return []

    ex_dates = []
    for i in range(1, len(kline_rows)):
        prev_af = kline_rows[i - 1].get("adj_factor", 1.0) or 1.0
        curr_af = kline_rows[i].get("adj_factor", 1.0) or 1.0
        if abs(curr_af - prev_af) > threshold:
            ex_dates.append(kline_rows[i]["trade_date"])

    return ex_dates


def is_ex_rights_date(row: dict, prev_row: dict | None, threshold: float = 0.01) -> bool:
    """判断单个交易日是否为除权日."""
    if prev_row is None:
        return False
    prev_af = prev_row.get("adj_factor", 1.0) or 1.0
    curr_af = row.get("adj_factor", 1.0) or 1.0
    return abs(curr_af - prev_af) > threshold


def iter_non_exrights_chunks(
    kline_rows: list[dict],
    min_chunk_size: int = 20,
) -> list[list[dict]]:
    """按除权日切分 K 线为连续段, 每段内部无除权.

    用于需要连续价格序列的计算 (ATR, MA, 收益率等).
    自动跳过除权日前后受影响的 bar。

    Args:
        kline_rows: 含 adj_factor 的 K 线列表
        min_chunk_size: 最小段长度, 短于此的段被丢弃

    Returns:
        多个连续 K 线段组成的列表
    """
    ex_dates = set(get_ex_rights_dates(kline_rows))

    chunks = []
    current_chunk = []

    for i, row in enumerate(kline_rows):
        td = row["trade_date"]

        # 除权日及其后 2 天跳过 (让指标有时间恢复)
        is_affected = False
        for d in ex_dates:
            if isinstance(td, date) and isinstance(d, date):
                delta = (td - d).days
            else:
                delta = 999
            if 0 <= delta <= 2:
                is_affected = True
                break

        if is_affected:
            if len(current_chunk) >= min_chunk_size:
                chunks.append(current_chunk)
            current_chunk = []
        else:
            current_chunk.append(row)

    if len(current_chunk) >= min_chunk_size:
        chunks.append(current_chunk)

    return chunks


async def get_kline_as_dataframe(
    symbol: str,
    start_date=None,
    end_date=None,
    session=None,
) -> pd.DataFrame:
    """获取前复权 K 线为 DataFrame 格式 (供 pandas 分析)."""
    rows = await get_adjusted_kline(symbol, start_date, end_date, session)
    if not rows:
        return pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "adj_factor"])
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.set_index("trade_date", inplace=True)
    return df
