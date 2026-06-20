#!/usr/bin/env python3
"""Verify recommendations — backfill real T+2/T+5/T+15 returns from daily_kline (Phase 29).

No Tushare API calls — pure local daily_kline lookup.
Usage:
  PYTHONPATH=. python scripts/verify_recommendations.py --init   # backfill 60 days
  PYTHONPATH=. python scripts/verify_recommendations.py --daily  # incremental last 5 days
"""
import asyncio, logging, sys
from datetime import date, timedelta
from app.core.database import async_session_factory
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("verify_recs")


async def verify_one(session, sym: str, scan_date, entry_price: float) -> bool:
    """Backfill T+2/T+5/T+15 returns for one recommendation from daily_kline.
    Phase 57: only updates horizons that aren't already verified.
    Returns True if at least one horizon was updated."""
    # Check which horizons are already verified to avoid redundant writes
    r_check = await session.execute(text("""
        SELECT verified_2d, verified_5d, verified_15d FROM recommendation_tracking
        WHERE scan_date = :sd AND symbol = :sym
    """), {"sd": scan_date, "sym": sym})
    existing = r_check.fetchone()
    if not existing:
        return False

    v2_done = existing[0] is True
    v5_done = existing[1] is True
    v15_done = existing[2] is True

    if v2_done and v5_done and v15_done:
        return False  # all done, skip

    r = await session.execute(text("""
        SELECT trade_date, close FROM daily_kline
        WHERE ts_code = :sym AND trade_date > :sd
        ORDER BY trade_date
    """), {"sym": sym, "sd": scan_date})
    future_rows = r.fetchall()
    if not future_rows:
        return False

    updates: dict = {}

    # T+2: 2nd bar (index 1)
    if not v2_done and len(future_rows) >= 2:
        t2_close = float(future_rows[1][1] or 0)
        if t2_close > 0 and entry_price > 0:
            ret2 = round((t2_close - entry_price) / entry_price * 100, 2)
            updates["return_2d"] = ret2
            updates["was_profitable_2d"] = ret2 > 0
            updates["verified_2d"] = True

    # T+5: 5th bar (index 4)
    if not v5_done and len(future_rows) >= 5:
        t5_close = float(future_rows[4][1] or 0)
        if t5_close > 0 and entry_price > 0:
            ret5 = round((t5_close - entry_price) / entry_price * 100, 2)
            updates["return_5d"] = ret5
            updates["was_profitable_5d"] = ret5 > 0
            updates["verified_5d"] = True

    # T+15: 15th bar (index 14)
    if not v15_done and len(future_rows) >= 15:
        t15_close = float(future_rows[14][1] or 0)
        if t15_close > 0 and entry_price > 0:
            ret15 = round((t15_close - entry_price) / entry_price * 100, 2)
            updates["return_15d"] = ret15
            updates["was_profitable_15d"] = ret15 > 0
            updates["verified_15d"] = True

    if updates:
        sets = ", ".join(f"{k} = :{k}" for k in updates)
        await session.execute(text(
            f"UPDATE recommendation_tracking SET {sets}, updated_at = NOW() "
            f"WHERE scan_date = :sd AND symbol = :sym"
        ), {**updates, "sd": scan_date, "sym": sym})
        return True
    return False


async def main(daily: bool = False):
    if daily:
        cutoff = date.today() - timedelta(days=5)
    else:
        cutoff = date.today() - timedelta(days=60)

    mode = "daily (5d)" if daily else "init (60d)"
    logger.info(f"=== Verify recommendations: {mode} ===")

    async with async_session_factory() as s:
        # Phase 57: 扩展验证窗口 — 同时处理未验证T+2 和 未验证T+5/T+15 的记录
        rows_r = await s.execute(text("""
            SELECT scan_date, symbol, close_price
            FROM recommendation_tracking
            WHERE scan_date >= :cut AND close_price > 0
              AND (verified_2d IS NOT TRUE
                   OR verified_5d IS NOT TRUE
                   OR verified_15d IS NOT TRUE)
            ORDER BY scan_date
        """), {"cut": cutoff})
        records = [(row[0], row[1], float(row[2] or 0)) for row in rows_r.fetchall()]

    if not records:
        logger.info("No unverified records to process")
        return

    logger.info(f"Processing {len(records)} unverified recommendations...")
    updated = 0
    async with async_session_factory() as s:
        for scan_date, sym, entry_price in records:
            try:
                if await verify_one(s, sym, scan_date, entry_price):
                    updated += 1
            except Exception:
                pass
        await s.commit()

    logger.info(f"Updated {updated}/{len(records)} records")

    # ── Summary stats ──
    async with async_session_factory() as s:
        for h in ["2d", "5d", "15d"]:
            r = await s.execute(text(
                f"SELECT COUNT(*), AVG(return_{h}), "
                f"SUM(CASE WHEN was_profitable_{h} THEN 1 ELSE 0 END)::float / NULLIF(COUNT(*),0) "
                f"FROM recommendation_tracking WHERE verified_{h} = true"
            ))
            row = r.fetchone()
            if row and row[0]:
                logger.info(
                    f"  T+{h}: {row[0]} verified, avg_return={float(row[1] or 0):+.1f}%, "
                    f"wr={float(row[2] or 0)*100:.1f}%"
                )
            else:
                logger.info(f"  T+{h}: no verified data")


if __name__ == "__main__":
    daily_mode = "--daily" in sys.argv
    asyncio.run(main(daily=daily_mode))
