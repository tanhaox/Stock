#!/usr/bin/env python3
"""Sync Tushare limit_list (daily limit-up/limit-down/broken-board) to local DB.

Usage:
  PYTHONPATH=. python -m scripts.sync_limit_list          # sync latest day
  PYTHONPATH=. python -m scripts.sync_limit_list --init   # sync last 60 days
"""
import asyncio, logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("sync_limit_list")


async def ensure_table():
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_limit_list (
                ts_code VARCHAR(12) NOT NULL,
                trade_date DATE NOT NULL,
                limit_type VARCHAR(1),       -- U=涨停 D=跌停 Z=炸板
                pct_chg DOUBLE PRECISION,
                open_times DOUBLE PRECISION,
                up_stat VARCHAR(20),
                limit_times INT,
                PRIMARY KEY (ts_code, trade_date)
            )
        """))
        await s.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_limit_list_date ON daily_limit_list (trade_date)"
        ))
        await s.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_limit_list_code ON daily_limit_list (ts_code)"
        ))
        await s.commit()


async def sync_day(trade_date: str) -> int:
    await ensure_table()

    rows = await call_tushare("limit_list", {"trade_date": trade_date},
        "ts_code,trade_date,limit,pct_chg,open_times,up_stat,limit_times")

    if not rows:
        logger.warning(f"limit_list returned 0 rows for {trade_date}")
        return 0

    inserted = 0
    async with async_session_factory() as s:
        for r in rows:
            ts = r.get("ts_code", "")
            td_str = r.get("trade_date", "")
            if not ts or not td_str:
                continue
            try:
                td = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
            except (ValueError, IndexError):
                continue

            await s.execute(text("""
                INSERT INTO daily_limit_list (ts_code, trade_date, limit_type, pct_chg, open_times, up_stat, limit_times)
                VALUES (:ts, :td, :lt, :pct, :ot, :us, :lti)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                    limit_type=EXCLUDED.limit_type, pct_chg=EXCLUDED.pct_chg,
                    open_times=EXCLUDED.open_times, up_stat=EXCLUDED.up_stat,
                    limit_times=EXCLUDED.limit_times
            """), {
                "ts": ts, "td": td,
                "lt": r.get("limit", ""),
                "pct": float(r.get("pct_chg", 0) or 0),
                "ot": float(r.get("open_times", 0) or 0),
                "us": r.get("up_stat", ""),
                "lti": int(r.get("limit_times", 0) or 0),
            })
            inserted += 1
        await s.commit()

    logger.info(f"Synced {inserted} limit records for {trade_date}")
    return inserted


async def backfill():
    await ensure_table()
    today = date.today()
    for d_offset in range(60, 0, -1):
        d = today - timedelta(days=d_offset)
        if d.weekday() >= 5:
            continue  # skip weekends
        await sync_day(d.strftime("%Y%m%d"))
        await asyncio.sleep(1)


async def main():
    import sys
    if "--init" in sys.argv:
        await backfill()
    else:
        today = date.today()
        if today.weekday() < 5:
            await sync_day(today.strftime("%Y%m%d"))
        else:
            logger.info("Today is not a trading day, skip")


if __name__ == "__main__":
    asyncio.run(main())
