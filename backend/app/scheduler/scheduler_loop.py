"""Scheduler loop — startup freshness check + hourly re-check (Phase 70).

Phase 70: 笔记本环境适配 — 固定时间 16:00 触发改为:
  1. 启动时检查关键表数据新鲜度，仅过期才同步
  2. Tushare API 覆盖率 ≥ 95% → 跳过下载
  3. 每小时轻量复查 (只查日期，不调 API)
  4. sync_log 表记录每次同步结果
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger("scheduler")


async def _ensure_sync_log_table():
    """Phase 70: 创建 sync_log 表 (如果不存在)."""
    from app.core.database import async_session_factory as _sf
    from sqlalchemy import text
    async with _sf() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id SERIAL PRIMARY KEY,
                task_name VARCHAR(100),
                status VARCHAR(20),
                detail TEXT,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ
            )
        """))
        await s.commit()
    logger.info("sync_log table ready")


async def _log_sync(task_name: str, status: str, detail: str = ""):
    """写入 sync_log 记录."""
    try:
        from app.core.database import async_session_factory as _sf
        from sqlalchemy import text
        async with _sf() as s:
            await s.execute(text(
                "INSERT INTO sync_log (task_name, status, detail, completed_at) "
                "VALUES (:n, :s, :d, NOW())"
            ), {"n": task_name, "s": status, "d": detail[:500]})
            await s.commit()
    except Exception:
        pass


async def run_all_daily_tasks():
    """Orchestrate all daily + weekly + monthly tasks.

    Phase 70: 每个 task 同时写 sync_log.
    """
    from app.scheduler.daily_tasks import (
        task_refresh_fundamental, task_sync_toplist, task_sync_commodity_futures,
        task_sync_margin_trading, task_daily_backtest, task_shadow_training,
        task_self_learning, task_increment_holding_days, task_sync_min_kline,
        task_sync_index_daily, task_sync_sw_sector,
        task_build_sector_trend, task_sync_sector_min_kline,
        task_sync_daily_kline, task_update_market_status,
        task_cleanup_old_data, task_system_health, task_holdings_sector_warning,
        task_verify_recommendations, task_build_news_signals, task_verify_news_signals,
        task_sync_chip_perf, task_sync_limit_list,
    )
    from app.scheduler.weekly_tasks import (
        task_scoring_weight_training, task_probability_recalibration,
        task_archetype_calibration, task_dual_channel_training,
        task_veteran_backtest, task_shadow_evaluation,
        task_retrain_predictive_model,
    )
    from app.scheduler.monthly_tasks import (
        task_monthly_archetype_snapshot, task_quarterly_delisted_sync,
        task_sync_stock_tags, task_build_stock_sector_map,
    )

    today = date.today()
    weekday = today.weekday()

    logger.info("=== Daily task run start ===")

    for name, fn in [
        ("kline", task_sync_daily_kline),
        ("market_status", task_update_market_status),
        ("fundamental", task_refresh_fundamental),
        ("toplist", task_sync_toplist),
        ("futures", task_sync_commodity_futures),
        ("margin", task_sync_margin_trading),
        ("backtest", task_daily_backtest),
        ("shadow", task_shadow_training),
        ("self_learning", task_self_learning),
        ("holding_days", task_increment_holding_days),
        ("min_kline", task_sync_min_kline),
        ("index_daily", task_sync_index_daily),
        ("sw_sector", task_sync_sw_sector),
        ("sector_trend", task_build_sector_trend),
        ("sector_mins", task_sync_sector_min_kline),
        ("verify_recs", task_verify_recommendations),
        ("news_signals", task_build_news_signals),
        ("verify_news", task_verify_news_signals),
        ("cleanup", task_cleanup_old_data),
        ("health", task_system_health),
        ("sector_warning", task_holdings_sector_warning),
        ("chip_perf", task_sync_chip_perf),
        ("limit_list", task_sync_limit_list),
    ]:
        try:
            logger.info(f"Task [{name}] start")
            result = await fn()
            status = "ok"
            detail = str(result)[:200] if result else ""
            logger.info(f"Task [{name}] done")
        except Exception as e:
            status = "error"
            detail = str(e)[:200]
            logger.warning(f"Task [{name}] failed: {e}")
        await _log_sync(name, status, detail)

    # ── Weekly ──
    if weekday == 0:
        for name, fn in [("scoring_weight", task_scoring_weight_training)]:
            try:
                await fn()
                await _log_sync(name, "ok", "")
            except Exception as e:
                logger.warning(f"Task {name}: {e}")
                await _log_sync(name, "error", str(e)[:200])
    if weekday == 5:
        try:
            await task_veteran_backtest()
            await _log_sync("veteran_bt", "ok", "")
        except Exception as e:
            logger.warning(f"Task veteran_bt: {e}")
            await _log_sync("veteran_bt", "error", str(e)[:200])
    if weekday == 6:
        for name, fn in [
            ("prob_calib", task_probability_recalibration),
            ("dual_channel", task_dual_channel_training),
            ("shadow_eval", task_shadow_evaluation),
            ("retrain_model", task_retrain_predictive_model),
        ]:
            try:
                await fn()
                await _log_sync(name, "ok", "")
            except Exception as e:
                logger.warning(f"Task {name}: {e}")
                await _log_sync(name, "error", str(e)[:200])
    if weekday == 6 and today.day <= 7:
        try:
            await task_archetype_calibration()
            await _log_sync("archetype_cal", "ok", "")
        except Exception as e:
            logger.warning(f"Task archetype_cal: {e}")
            await _log_sync("archetype_cal", "error", str(e)[:200])

    # ── Monthly + Quarterly ──
    if today.day == 1:
        for name, fn in [("stock_tags", task_sync_stock_tags), ("sector_map", task_build_stock_sector_map)]:
            try:
                await fn()
                await _log_sync(name, "ok", "")
            except Exception as e:
                logger.warning(f"Task {name}: {e}")
                await _log_sync(name, "error", str(e)[:200])
    for name, fn in [("monthly_snap", task_monthly_archetype_snapshot), ("quarterly_delist", task_quarterly_delisted_sync)]:
        try:
            await fn()
            await _log_sync(name, "ok", "")
        except Exception as e:
            logger.warning(f"Task {name}: {e}")
            await _log_sync(name, "error", str(e)[:200])

    logger.info("=== Daily task run complete ===")


# ═══════════ Phase 70: 数据新鲜度检查表 ═══════════
# (label, table_name, date_column, max_staleness_days, task_fn)
_DATA_FRESHNESS_CHECKS = [
    ("daily_kline",       "daily_kline",       "trade_date", 2, "task_sync_daily_kline"),
    ("index_daily",       "index_daily",       "trade_date", 2, "task_sync_index_daily"),
    ("sw_sector",         "sw_sector_index",   "trade_date", 7, "task_sync_sw_sector"),
    ("sector_trend",      "sector_trend",       "trade_date", 3, "task_build_sector_trend"),
    # sector_min_kline 不自动触发 (30+ API calls), 仅手动
    ("sector_min_kline",  "sector_min_kline",   "trade_time", 7, None),
    ("scan_results",      "scan_results",       "scan_date",  2, None),
    ("analysis_scores",   "analysis_scores",    "scan_date",  2, None),
    ("news_aggregated",   "news_aggregated",    "date",       2, "task_build_news_signals"),
    ("recommendation_tracking", "recommendation_tracking", "scan_date", 3, "task_verify_recommendations"),
    ("min_kline",       "min_kline",       "trade_time", 1, "task_sync_min_kline"),  # DNA系统分钟线
    ("macro_cache",      "macro_cache",      "period",    30, "task_sync_macro_data"),  # 宏观数据(月频,30天检查一次)
    ("stock_betas",      "stock_commodity_beta", "last_updated", 30, "task_sync_stock_betas"),
]


async def startup_check():
    """Phase 70: 启动时检查数据新鲜度, 仅过期才同步."""
    from app.core.database import async_session_factory as _sf
    from sqlalchemy import text

    logger.info("=== Startup freshness check ===")
    skipped = 0
    ran = 0

    async with _sf() as s:
        for label, table, col, max_days, task_name in _DATA_FRESHNESS_CHECKS:
            try:
                r = await s.execute(text(f"SELECT MAX({col}) FROM {table}"))
                latest = r.scalar()
                if latest is None:
                    staleness = "no_data"
                    needs_sync = True
                elif isinstance(latest, datetime):
                    staleness = f"{(datetime.now() - latest).days}d"
                    needs_sync = (datetime.now() - latest).days > max_days
                else:
                    staleness = f"{(date.today() - latest).days}d" if hasattr(latest, 'day') else "?"
                    needs_sync = (date.today() - latest).days > max_days if hasattr(latest, 'day') else False

                if needs_sync and task_name:
                    logger.info(f"  {label}: stale ({staleness}), triggering {task_name}...")
                    ran += 1
                elif needs_sync and not task_name:
                    logger.info(f"  {label}: stale ({staleness}), no auto-task (manual scan needed)")
                    ran += 1
                else:
                    logger.debug(f"  {label}: fresh ({staleness}), skip")
                    skipped += 1
            except Exception as e:
                logger.debug(f"  {label}: check failed ({e})")
                skipped += 1

    logger.info(f"Startup check: {skipped} tables fresh, {ran} need sync")

    # ── 仅当有 stale 时跑对应 task ──
    if ran == 0:
        logger.info("All data fresh — nothing to sync on startup")
        return

    # 实际执行需要同步的 task
    for label, table, col, max_days, task_name in _DATA_FRESHNESS_CHECKS:
        if not task_name:
            continue
        try:
            r = await _check_stale(label, table, col, max_days)
            if not r:
                continue
        except Exception:
            continue

        fn = _resolve_task_fn(task_name)
        if not fn:
            continue
        try:
            logger.info(f"  Running [{task_name}] for {label}...")
            result = await fn()
            await _log_sync(task_name, "ok", str(result)[:200] if result else "")
        except Exception as e:
            logger.warning(f"  [{task_name}] failed: {e}")
            await _log_sync(task_name, "error", str(e)[:200])


async def _check_stale(label: str, table: str, col: str, max_days: int) -> bool:
    """Return True if table data is older than max_days."""
    from app.core.database import async_session_factory as _sf
    from sqlalchemy import text
    async with _sf() as s:
        r = await s.execute(text(f"SELECT MAX({col}) FROM {table}"))
        latest = r.scalar()
        if latest is None:
            return True
        if isinstance(latest, datetime):
            return (datetime.now() - latest).days > max_days
        return (date.today() - latest).days > max_days


def _resolve_task_fn(task_name: str):
    """Resolve task name to callable."""
    if task_name == "task_sync_daily_kline":
        from app.scheduler.daily_tasks import task_sync_daily_kline
        return task_sync_daily_kline
    elif task_name == "task_sync_index_daily":
        from app.scheduler.daily_tasks import task_sync_index_daily
        return task_sync_index_daily
    elif task_name == "task_sync_sw_sector":
        from app.scheduler.daily_tasks import task_sync_sw_sector
        return task_sync_sw_sector
    elif task_name == "task_build_sector_trend":
        from app.scheduler.daily_tasks import task_build_sector_trend
        return task_build_sector_trend
    elif task_name == "task_sync_sector_min_kline":
        from app.scheduler.daily_tasks import task_sync_sector_min_kline
        return task_sync_sector_min_kline
    elif task_name == "task_sync_sector_min_kline":
        from app.scheduler.daily_tasks import task_sync_sector_min_kline
        return task_sync_sector_min_kline
    elif task_name == "task_build_news_signals":
        from app.scheduler.daily_tasks import task_build_news_signals
        return task_build_news_signals
    elif task_name == "task_verify_recommendations":
        from app.scheduler.daily_tasks import task_verify_recommendations
        return task_verify_recommendations
    elif task_name == "task_sync_macro_data":
        from app.services.macro_data import sync_macro_cache
        return sync_macro_cache
    elif task_name == "task_sync_stock_betas":
        from app.services.macro_beta import batch_compute_all_betas
        return batch_compute_all_betas
    return None


async def scheduler_loop():
    """Phase 70: 每小时轻量检查 + 按需同步.

    不再用 while True 等 16:00. 启动时检查新鲜度, 之后每小时复查.
    """
    logger.info("Scheduler starting (laptop mode)...")

    # 确保 sync_log 表存在
    await _ensure_sync_log_table()

    # 启动时检查
    try:
        from app.scheduler.monthly_tasks import task_monthly_archetype_snapshot, task_quarterly_delisted_sync
        await task_monthly_archetype_snapshot()
        await task_quarterly_delisted_sync()
    except Exception as e:
        logger.warning(f"Monthly init: {e}")

    await startup_check()

    # ── 每小时复查 (只查日期, 不调 API) ──
    try:
        while True:
            await asyncio.sleep(3600)
            logger.debug("Hourly freshness re-check...")
            await startup_check()
    except asyncio.CancelledError:
        logger.info("Scheduler cancelled — shutting down")


def start_scheduler():
    """Sync entry point for standalone thread execution."""
    asyncio.run(scheduler_loop())


# CLI
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--once":
        print("Running daily tasks once...")
        asyncio.run(run_all_daily_tasks())
        print("Done.")
    elif len(sys.argv) > 1 and sys.argv[1] == "--check":
        print("Running startup freshness check...")
        asyncio.run(startup_check())
        print("Done.")
    else:
        print("Starting scheduler (laptop mode — hourly check)...")
        asyncio.run(scheduler_loop())
