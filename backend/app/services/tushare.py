"""Tushare K线数据服务."""
import pandas as pd
from datetime import date, datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.tushare_common import call_tushare

async def fetch_daily_data(session: AsyncSession, ts_code: str, days: int = 120, local_only: bool = True) -> pd.DataFrame | None:
    end = datetime.now().date(); start = end - timedelta(days=days * 2)
    result = await session.execute(text("""SELECT trade_date,open,high,low,close,volume FROM daily_kline WHERE ts_code=:c AND trade_date>=:s AND trade_date<=:e ORDER BY trade_date"""), {"c": ts_code, "s": start, "e": end})
    rows = result.fetchall()
    if not rows or len(rows) < 20: return None
    df = pd.DataFrame(rows, columns=["Date","Open","High","Low","Close","Volume"])
    for col in ["Open","High","Low","Close","Volume"]: df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.tail(days)

async def get_stock_list(token: str | None = None) -> pd.DataFrame:
    from app.core.database import async_session_factory
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT ts_code FROM daily_kline"))
        codes = [row[0] for row in r.fetchall()]
    data = [{"ts_code": c, "name": c, "industry": ""} for c in codes]
    return pd.DataFrame(data)
