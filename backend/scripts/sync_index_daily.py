#!/usr/bin/env python3
"""Sync 4 major index daily data via idx_mins API (5min bars → daily OHLCV).

Tushare `daily` API 不返回指数代码, 改用 idx_mins 拉取 5 分钟线再合成日线.
同时写入 daily_kline (大盘指数行) 和 index_daily (只用 close)。

Usage:
  PYTHONPATH=. python scripts/sync_index_daily.py         # 回填 5 天
  PYTHONPATH=. python scripts/sync_index_daily.py --today  # 仅今天
"""
import asyncio, logging, sys
from datetime import date, datetime, timedelta
from collections import defaultdict
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("sync_index")

INDEX_CODES = ['000001.SH', '000300.SH', '000852.SH', '399006.SZ', '700001.TI']

# .TI 后缀的代码通过 ths_daily API 直接获取日线（不需要分钟线合成）
TI_CODES = {'700001.TI'}


async def _sync_ti_index(code: str, start_date: str, end_date: str) -> int:
    """Fetch daily OHLCV from ths_daily API for .TI codes."""
    try:
        rows = await call_tushare('ths_daily', {
            'ts_code': code, 'start_date': start_date, 'end_date': end_date,
        }, 'ts_code,trade_date,open,high,low,close,vol,amount')
    except Exception as e:
        logger.warning(f"  {code} ths_daily fail: {e}")
        return 0

    if not rows:
        return 0

    inserted = 0
    async with async_session_factory() as s:
        for row in rows:
            td_str = str(row.get('trade_date', ''))
            if len(td_str) == 8 and td_str.isdigit():
                td = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
            else:
                continue
            o = float(row.get('open', 0) or 0)
            h = float(row.get('high', 0) or 0)
            l = float(row.get('low', 0) or 0)
            c = float(row.get('close', 0) or 0)
            v = float(row.get('vol', 0) or 0)
            a = float(row.get('amount', 0) or 0)

            await s.execute(text("""
                INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, volume, amount)
                VALUES (:c, :d, :o, :h, :l, :cl, :v, :a)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high,
                    low=EXCLUDED.low, close=EXCLUDED.close,
                    volume=EXCLUDED.volume, amount=EXCLUDED.amount
            """), {"c": code, "d": td, "o": o, "h": h, "l": l, "cl": c, "v": v, "a": a})

            await s.execute(text("""
                INSERT INTO index_daily (ts_code, trade_date, close)
                VALUES (:c, :d, :cl)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET close = EXCLUDED.close
            """), {"c": code, "d": td, "cl": c})
            inserted += 1
        await s.commit()
    return inserted


async def sync_one_index(code: str, start_date: str, end_date: str) -> int:
    """Fetch 5-min bars via idx_mins, aggregate to daily OHLCV, write daily_kline + index_daily."""
    try:
        bars = await call_tushare('idx_mins', {
            'ts_code': code, 'freq': '5min',
            'start_date': start_date, 'end_date': end_date,
        }, 'ts_code,trade_time,open,high,low,close,vol,amount')
    except Exception as e:
        logger.warning(f"  {code} Tushare fail: {e}")
        return 0

    if not bars:
        return 0

    # 按日期分组
    daily: dict[str, list[dict]] = defaultdict(list)
    for b in bars:
        tt = str(b.get('trade_time', ''))
        # idx_mins returns 'YYYYMMDD HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS'
        d_str = tt[:10].replace('-', '').replace('/', '')
        if len(d_str) >= 8 and d_str[:8].isdigit():
            daily[d_str[:8]].append(b)

    inserted = 0
    async with async_session_factory() as s:
        for d_str, day_bars in daily.items():
            if len(day_bars) < 8:
                continue  # 至少 8 根 5 分钟 K 线才算有效交易日

            # 按时间升序排列 (idx_mins 返回倒序 15:00→09:30)
            day_bars.sort(key=lambda x: x['trade_time'])
            o = float(day_bars[0]['open'])
            c = float(day_bars[-1]['close'])
            h = max(float(b['high']) for b in day_bars)
            l = min(float(b['low']) for b in day_bars)
            v = sum(float(b.get('vol', 0) or 0) for b in day_bars)
            a = sum(float(b.get('amount', 0) or 0) for b in day_bars)
            # Parse date from YYYYMMDD
            td = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))

            # ── 写入 daily_kline (大盘指数行) ──
            await s.execute(text("""
                INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, volume, amount)
                VALUES (:c, :d, :o, :h, :l, :cl, :v, :a)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                    open=EXCLUDED.open, high=EXCLUDED.high,
                    low=EXCLUDED.low, close=EXCLUDED.close,
                    volume=EXCLUDED.volume, amount=EXCLUDED.amount
            """), {"c": code, "d": td, "o": o, "h": h, "l": l, "cl": c, "v": v, "a": a})

            # ── 写入 index_daily ──
            await s.execute(text("""
                INSERT INTO index_daily (ts_code, trade_date, close)
                VALUES (:c, :d, :cl)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET close = EXCLUDED.close
            """), {"c": code, "d": td, "cl": c})

            inserted += 1
        await s.commit()

    return inserted


async def main():
    if "--today" in sys.argv:
        today = date.today().strftime('%Y%m%d')
        start = today
        end = today
    else:
        start = (date.today() - timedelta(days=5)).strftime('%Y%m%d')
        end = date.today().strftime('%Y%m%d')

    logger.info(f"Sync index daily: {start} ~ {end}")
    total = 0
    for code in INDEX_CODES:
        if code in TI_CODES:
            n = await _sync_ti_index(code, start, end)
        else:
            n = await sync_one_index(code, start, end)
        logger.info(f"  {code}: {n} days")
        total += n
        await asyncio.sleep(0.6)  # QPS control

    logger.info(f"Total: {total} days across {len(INDEX_CODES)} indexes")


if __name__ == "__main__":
    asyncio.run(main())
