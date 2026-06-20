"""历史数据回填 — 逐月 TG扫描 + 深度分析，扩展训练数据窗口.

用法: python scripts/backfill_history.py
      python scripts/backfill_history.py --months 202601,202602,202603
"""
import asyncio, sys, time, logging
from datetime import date, datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
logger = logging.getLogger('backfill')

from app.core.database import async_session_factory
from sqlalchemy import text

# 进度追踪（供外部查询）
_backfill_progress: dict = {"running": False, "total": 0, "done": 0, "current": "", "pct": 0,
                             "results": [], "started_at": "", "elapsed_min": 0}


async def get_monthly_trading_days(year: int, month: int) -> list[str]:
    """从本地 daily_kline 获取指定月份所有交易日（不调Tushare，避免Token依赖）."""
    from datetime import date as dt_date
    month_start = dt_date(year, month, 1)
    if month == 12:
        month_end = dt_date(year + 1, 1, 1)
    else:
        month_end = dt_date(year, month + 1, 1)

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= :d1 AND trade_date < :d2 ORDER BY trade_date"
        ), {"d1": month_start, "d2": month_end})
        return [row[0].isoformat() for row in r.fetchall()]


async def backfill_single_date(trade_date_str: str) -> dict:
    """对单个历史日期执行完整 TG扫描 + 深度分析."""
    td = date.fromisoformat(trade_date_str)
    logger.info(f"开始回填 {trade_date_str}")

    # 1. TG扫描（跳过下载，用已有K线，指定历史日期）
    async with async_session_factory() as s:
        from app.services.tg_engine import scan_all_stocks
        results_df, sd = await scan_all_stocks(
            s, progress_callback=None, skip_download=True,
            scan_date_override=trade_date_str
        )
        if results_df is None or (hasattr(results_df, 'empty') and results_df.empty):
            return {"date": trade_date_str, "status": "no_signals", "tg_count": 0}

        tg_count = len(results_df) if results_df is not None else 0
        symbols = results_df["symbol"].unique().tolist() if tg_count > 0 else []

    # 2. 深度分析（deep_analyze 自动从 scan_results 读股票列表）
    try:
        async with async_session_factory() as s2:
            from app.services.deep_scorer import deep_analyze
            await deep_analyze(s2, scan_date=td, min_composite_score=0)
            r = await s2.execute(text(
                "SELECT COUNT(*) FROM analysis_scores WHERE scan_date = :d"
            ), {"d": td})
            scored = r.scalar() or 0
    except Exception as e:
        logger.error(f"深度分析失败 {trade_date_str}: {e}", exc_info=True)
        # TG扫描已成功，即使深度分析失败也保留TG结果
        scored = 0

    logger.info(f"回填完成 {trade_date_str}: TG={tg_count}只, 评分={scored}只")
    return {"date": trade_date_str, "status": "success", "tg_count": tg_count, "scored": scored}


async def run_backfill(month_specs: list[str] | None = None):
    """执行历史回填."""
    global _backfill_progress
    _backfill_progress = {"running": True, "total": 0, "done": 0, "current": "", "pct": 0,
                           "results": [], "started_at": datetime.now().isoformat(), "elapsed_min": 0}
    start_time = time.time()

    # 确定目标日期
    target_dates = []
    if month_specs:
        for spec in month_specs:
            y, m = int(spec[:4]), int(spec[4:])
            days = await get_monthly_trading_days(y, m)
            if days:
                target_dates.append(days[len(days) // 2])  # 月中交易日
    else:
        # 默认: 2025-11 ~ 2026-05
        for year, month in [(2025, 11), (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5)]:
            days = await get_monthly_trading_days(year, month)
            if days:
                target_dates.append(days[len(days) // 2])

    # 检查已有数据
    date_objs = [date.fromisoformat(d) for d in target_dates]
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT scan_date, COUNT(*) FROM scan_results WHERE scan_date = ANY(:ds) GROUP BY scan_date"
        ), {"ds": date_objs})
        existing = set()
        for row in r.fetchall():
            d = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
            if row[1] >= 100:  # 至少100只股票才算有效
                existing.add(d)

    pending = [d for d in target_dates if d not in existing]
    total = len(pending)
    _backfill_progress["total"] = total

    print(f"\n{'='*60}")
    print(f"  历史数据回填")
    print(f"  目标月份: {len(target_dates)} | 已有: {len(existing)} | 待回填: {total}")
    print(f"  待回填日期: {pending}")
    print(f"{'='*60}\n")

    if not pending:
        _backfill_progress["running"] = False
        _backfill_progress["pct"] = 100
        print("所有日期已有数据，无需回填")
        return

    for i, d in enumerate(pending):
        _backfill_progress["current"] = d
        _backfill_progress["done"] = i
        _backfill_progress["pct"] = round(i / total * 100)
        _backfill_progress["elapsed_min"] = round((time.time() - start_time) / 60, 1)

        pct = _backfill_progress["pct"]
        elapsed = _backfill_progress["elapsed_min"]
        print(f"\n[回填进度 {pct}%] {i+1}/{total} - {d} (已耗时 {elapsed}分)", flush=True)

        try:
            result = await backfill_single_date(d)
            _backfill_progress["results"].append(result)
            print(f"  [OK] {d}: TG={result.get('tg_count',0)} 评分={result.get('scored',0)}", flush=True)
        except Exception as e:
            logger.error(f"回填失败 {d}: {e}", exc_info=True)
            _backfill_progress["results"].append({"date": d, "status": "error", "error": str(e)})
            print(f"  [FAIL] {d}: {e}", flush=True)

        if i > 0:
            avg_time = (time.time() - start_time) / (i + 1)
            remaining = avg_time * (total - i - 1)
            print(f"  [ETA] 预计剩余: {round(remaining/60, 1)}分钟", flush=True)

    _backfill_progress["done"] = total
    _backfill_progress["pct"] = 100
    _backfill_progress["running"] = False
    _backfill_progress["elapsed_min"] = round((time.time() - start_time) / 60, 1)

    results = _backfill_progress["results"]
    success = sum(1 for r in results if r.get("status") == "success")
    total_tg = sum(r.get("tg_count", 0) for r in results)
    total_scored = sum(r.get("scored", 0) for r in results)

    print(f"\n{'='*60}")
    print(f"  回填完成!")
    print(f"  耗时: {_backfill_progress['elapsed_min']} 分钟")
    print(f"  成功: {success}/{total}")
    print(f"  TG信号合计: {total_tg} 只")
    print(f"  评分合计: {total_scored} 只")
    for r in results:
        icon = "[OK]" if r.get("status") == "success" else "[FAIL]"
        print(f"  {icon} {r['date']}: {r.get('status')} TG={r.get('tg_count','?')} scored={r.get('scored','?')}")
    print(f"{'='*60}\n")


def get_progress() -> dict:
    """供外部查询进度."""
    return dict(_backfill_progress)


if __name__ == "__main__":
    months = None
    if len(sys.argv) > 1 and sys.argv[1] == "--months":
        months = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    asyncio.run(run_backfill(months))
