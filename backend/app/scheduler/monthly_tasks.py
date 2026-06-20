"""Monthly/quarterly background tasks — extracted from background_sync.py (Phase 7)."""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("scheduler.monthly")


async def task_monthly_archetype_snapshot():
    """5th trading day of month: Archetype profile snapshot."""
    from app.services.tushare_common import call_tushare
    today = date.today()
    month_start = today.replace(day=1).strftime("%Y%m%d")
    cal = await call_tushare("trade_cal", {"exchange": "SSE", "start_date": month_start,
                                             "end_date": today.strftime("%Y%m%d"), "is_open": "1"}, "cal_date")
    if not cal or len(cal) < 5: return {"status": "skipped"}
    trading_days = sorted([r["cal_date"] for r in cal])
    if len(trading_days) >= 5 and trading_days[4] == today.strftime("%Y%m%d"):
        logger.info("Monthly archetype snapshot...")
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT archetype, sample_count, is_trainable FROM archetype_profiles "
                "WHERE effective_date = (SELECT MAX(effective_date) FROM archetype_profiles)"
            ))
            current = [(row[0], row[1], row[2]) for row in r.fetchall()]
            for arch, cnt, trainable in current:
                await s.execute(text("""INSERT INTO archetype_profiles
                    (id, archetype, label, description, sample_count, is_trainable, effective_date, created_at, updated_at)
                    VALUES (gen_random_uuid(), :a, :l, :d, :c, :t, :e, NOW(), NOW())"""),
                    {"a": arch, "l": arch, "d": f"{arch} ({today} snapshot)", "c": cnt, "t": trainable, "e": today})
            await s.commit()
        logger.info(f"Snapshot: {len(current)} archs ({today})")
    return {"status": "done", "archs": len(cal or [])}


async def task_quarterly_delisted_sync():
    """First 3 days of (1,4,7,10): Sync delisted stocks."""
    from app.services.shadow_trainer import sync_delisted_stocks
    today = date.today()
    if today.day <= 3 and today.month in (1, 4, 7, 10):
        logger.info("Quarterly delisted sync...")
        result = await sync_delisted_stocks()
        logger.info(f"Delisted: {result}")
        return result
    return {"status": "skipped", "reason": "not_quarterly_start"}


async def task_sync_stock_tags():
    """Monthly (1st of month): Rebuild stock_tags classification."""
    from scripts.sync_stock_tags import main
    await main()
    return {"status": "done"}


async def task_build_stock_sector_map():
    """Monthly (1st of month): Rebuild stock_sector_map."""
    from scripts.build_stock_sector_map import main
    await main()
    return {"status": "done"}
