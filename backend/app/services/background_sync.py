"""Backward-compatible re-export from scheduler modules (Phase 7).

All business logic has been extracted to app/scheduler/{daily,weekly,monthly}_tasks.py.
This file re-exports the same names for any code still depending on them.
"""
from app.scheduler.scheduler_loop import scheduler_loop, start_scheduler, run_all_daily_tasks
from app.scheduler.daily_tasks import (
    task_refresh_fundamental, task_sync_toplist, task_sync_commodity_futures,
    task_sync_margin_trading, task_daily_backtest, task_shadow_training,
    task_self_learning, task_increment_holding_days, task_sync_min_kline,
    task_cleanup_old_data, task_system_health, task_holdings_sector_warning,
)

# Legacy names for CLI compatibility
import asyncio
from datetime import date, datetime, timedelta

refresh_fundamental_snapshot = task_refresh_fundamental
run_daily_backtest = task_daily_backtest


async def daily_task():
    """Legacy wrapper — delegates to run_all_daily_tasks()."""
    return await run_all_daily_tasks()


# CLI entry
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        print("Running daily tasks once...")
        result = asyncio.run(run_all_daily_tasks())
        print(f"Done.")
    else:
        print("Starting scheduler (16:00 daily)...")
        asyncio.run(scheduler_loop())
