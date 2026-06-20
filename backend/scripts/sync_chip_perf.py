"""每日同步 Tushare cyq_perf 全市场筹码数据到本地 daily_chip_perf 表.

策略:
  - 每日 16:00 后运行一次 (Tushare T+1 更新)
  - 批量模式: 一次 API 调用拉取全市场 (trade_date 参数)
  - 如果批量模式返回数据不足, 逐股回退

用法:
  python -m scripts.sync_chip_perf                    # 同步最新
  python -m scripts.sync_chip_perf --backfill 20260101  # 回填历史
  python -m scripts.sync_chip_perf --backfill-all       # 回填全部历史
"""

import asyncio, logging, sys
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare

logger = logging.getLogger("sync_chip_perf")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def get_latest_perf_date() -> date | None:
    """获取本地筹码最新日期."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(trade_date) FROM daily_chip_perf"))
        d = r.scalar()
        return d if d else None


async def sync_day(trade_date: str) -> int:
    """同步指定日期的全市场 cyq_perf.

    Returns:
        插入的行数
    """
    today_dt = date(int(trade_date[:4]), int(trade_date[4:6]), int(trade_date[6:8]))

    # 获取需要同步的股票列表 (有日线数据的)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ts_code FROM daily_kline
            WHERE ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%'
               OR ts_code LIKE '4%' OR ts_code LIKE '8%' OR ts_code LIKE '9%'
        """))
        symbols = [row[0] for row in r.fetchall()]

    if not symbols:
        logger.warning("No stocks in daily_kline")
        return 0

    logger.info(f"Syncing cyq_perf for {trade_date}, {len(symbols)} stocks")

    # 批量模式: 一次调用
    try:
        rows = await call_tushare("cyq_perf", {"trade_date": trade_date},
            "ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")
    except Exception as e:
        logger.error(f"Bulk cyq_perf failed for {trade_date}: {e}")
        rows = []

    if not rows:
        logger.warning(f"cyq_perf returned 0 rows for {trade_date}")
        return 0

    # 只保留本地有 K 线的股票
    local_set = set(symbols)
    rows = [r for r in rows if r.get("ts_code", "") in local_set]
    if not rows:
        logger.warning(f"No matching stocks in local DB for {trade_date}")
        return 0

    inserted = 0
    async with async_session_factory() as s:
        for r in rows:
            ts = r.get("ts_code", "")
            td = r.get("trade_date", "")
            if not ts or not td:
                continue
            try:
                td_dt = date(int(td[:4]), int(td[4:6]), int(td[6:8]))
            except (ValueError, IndexError):
                continue

            def _f(key):
                v = r.get(key)
                if v is None or v == "":
                    return None
                return float(v)

            await s.execute(text("""
                INSERT INTO daily_chip_perf (ts_code, trade_date, his_low, his_high,
                    cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                    weight_avg, winner_rate)
                VALUES (:ts, :td, :hl, :hh, :c5, :c15, :c50, :c85, :c95, :wa, :wr)
                ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                    his_low=EXCLUDED.his_low, his_high=EXCLUDED.his_high,
                    cost_5pct=EXCLUDED.cost_5pct, cost_15pct=EXCLUDED.cost_15pct,
                    cost_50pct=EXCLUDED.cost_50pct, cost_85pct=EXCLUDED.cost_85pct,
                    cost_95pct=EXCLUDED.cost_95pct, weight_avg=EXCLUDED.weight_avg,
                    winner_rate=EXCLUDED.winner_rate
            """), {
                "ts": ts, "td": td_dt,
                "hl": _f("his_low"), "hh": _f("his_high"),
                "c5": _f("cost_5pct"), "c15": _f("cost_15pct"),
                "c50": _f("cost_50pct"), "c85": _f("cost_85pct"),
                "c95": _f("cost_95pct"), "wa": _f("weight_avg"),
                "wr": _f("winner_rate"),
            })
            inserted += 1

        await s.commit()

    logger.info(f"Synced {inserted} cyq_perf rows for {trade_date}")
    return inserted


async def backfill(start_date: str, end_date: str = None):
    """回填历史筹码数据."""
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")

    # 获取交易日历
    try:
        cal = await call_tushare("trade_cal", {
            "exchange": "SSE", "start_date": start_date, "end_date": end_date, "is_open": "1"
        }, "cal_date")
        trading_days = sorted([r["cal_date"] for r in cal if r.get("cal_date")])
    except Exception:
        logger.warning("trade_cal failed, using weekday fallback")
        from datetime import timedelta as td
        sd = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
        ed = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))
        d = sd
        trading_days = []
        while d <= ed:
            if d.weekday() < 5:
                trading_days.append(d.strftime("%Y%m%d"))
            d += td(days=1)

    total = len(trading_days)
    total_inserted = 0
    for i, td in enumerate(trading_days):
        logger.info(f"[{i+1}/{total}] Syncing {td}...")
        n = await sync_day(td)
        total_inserted += n
        if n == 0:
            # 如果返回 0，可能是 API 限流或该日期真的没数据
            # 等一等再继续
            await asyncio.sleep(1)

    logger.info(f"Backfill complete: {total_inserted} rows across {total} days")


async def main():
    if "--backfill" in sys.argv:
        idx = sys.argv.index("--backfill")
        start = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "20240101"
        end = None
        if "--end" in sys.argv:
            end_idx = sys.argv.index("--end")
            end = sys.argv[end_idx + 1] if end_idx + 1 < len(sys.argv) else None
        await backfill(start, end)
    elif "--backfill-all" in sys.argv:
        await backfill("20180101")  # Tushare cyq_perf 最早 2018
    else:
        # 增量: 同步最新的交易日
        latest = await get_latest_perf_date()
        if latest is None:
            logger.info("No existing chip data, syncing last 5 days...")
            start = date.today() - timedelta(days=5)
            end = date.today()
            from datetime import timedelta as td
            d = start
            while d <= end:
                if d.weekday() < 5:
                    await sync_day(d.strftime("%Y%m%d"))
                d += td(days=1)
        else:
            logger.info(f"Latest chip data: {latest}")
            # 同步最新缺失的交易日 (从 latest+1 到今天)
            # 修复 v7.0.32: 不能只同步今天, 因为今天可能没收盘
            from datetime import timedelta as td
            d = latest + td(days=1)
            today = date.today()
            synced = 0
            while d <= today:
                if d.weekday() < 5:  # 工作日
                    n = await sync_day(d.strftime("%Y%m%d"))
                    if n > 0:
                        synced += 1
                d += td(days=1)
            if synced == 0:
                # 没新数据, 再回头试一次今天
                if today.weekday() < 5 and latest < today:
                    await sync_day(today.strftime("%Y%m%d"))
            else:
                logger.info("Already up to date")


if __name__ == "__main__":
    asyncio.run(main())
