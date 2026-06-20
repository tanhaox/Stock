"""龙虎榜席位明细同步 + 历史回填.

数据源: Tushare top_inst 接口 (席位级别 buy/sell/net_buy)
存储表: toplist_detail
"""
import asyncio, sys, logging
from datetime import date, timedelta
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("toplist_sync")


async def sync_one_day(trade_date: str) -> int:
    """同步单日龙虎榜席位明细. 返回插入条数."""
    try:
        rows = await call_tushare('top_inst', {'trade_date': trade_date, 'limit': '1000'}, '')
    except Exception as e:
        logger.warning(f"Tushare top_inst {trade_date} failed: {e}")
        return 0

    if not rows or not isinstance(rows, list):
        return 0

    inserted = 0
    async with async_session_factory() as s:
        for r in rows:
            td_raw = r.get('trade_date', '')
            # 统一转为日期对象
            if isinstance(td_raw, str) and len(td_raw) == 8:
                td_obj = date(int(td_raw[:4]), int(td_raw[4:6]), int(td_raw[6:8]))
            elif isinstance(td_raw, date):
                td_obj = td_raw
            else:
                continue
            try:
                await s.execute(text("""
                    INSERT INTO toplist_detail (trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason, market)
                    VALUES (:d, :c, :e, :b, :br, :s, :sr, :nb, :si, :r, :mkt)
                    ON CONFLICT (trade_date, ts_code, exalter, side) DO NOTHING
                """), {
                    "d": td_obj, "c": r.get('ts_code', ''),
                    "e": r.get('exalter', ''),
                    "b": float(r.get('buy', 0) or 0),
                    "br": float(r.get('buy_rate', 0) or 0),
                    "s": float(r.get('sell', 0) or 0),
                    "sr": float(r.get('sell_rate', 0) or 0),
                    "nb": float(r.get('net_buy', 0) or 0),
                    "si": int(r.get('side', 0) or 0),
                    "r": r.get('reason', '')[:500],
                    "mkt": "创业板" if (r.get('ts_code','')[:3] in ('300','301','688')) else ("中小板" if (r.get('ts_code','')[:3] in ('002','003')) else "主板"),
                })
                inserted += 1
            except Exception:
                pass
        await s.commit()

    return inserted


async def backfill(start_date: str = "20260101", end_date: str = None):
    """回填历史龙虎榜数据."""
    if end_date is None:
        end_date = date.today().strftime("%Y%m%d")

    # 获取交易日历
    try:
        cal = await call_tushare('trade_cal', {
            'exchange': 'SSE', 'start_date': start_date,
            'end_date': end_date, 'is_open': '1',
        }, '')
    except Exception:
        logger.error("Failed to get trade calendar, using all dates")
        cal = None

    dates = []
    if cal and isinstance(cal, list):
        dates = [r['cal_date'] for r in cal if r.get('is_open') == 1]
    else:
        # Fallback: generate weekday dates
        import datetime
        start = datetime.date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
        end = datetime.date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))
        d = start
        while d <= end:
            if d.weekday() < 5:
                dates.append(d.strftime("%Y%m%d"))
            d += datetime.timedelta(days=1)

    logger.info(f"Backfilling {len(dates)} trading days ({dates[0]} ~ {dates[-1]})")

    total = 0
    for i, d in enumerate(dates):
        # 跳过已同步的日期
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT 1 FROM toplist_detail WHERE trade_date = :d LIMIT 1"), {"d": d[:4] + '-' + d[4:6] + '-' + d[6:8]})
            if r.fetchone():
                continue

        cnt = await sync_one_day(d)
        total += cnt
        if cnt > 0:
            logger.info(f"  [{i+1}/{len(dates)}] {d}: {cnt} records")
        await asyncio.sleep(0.3)  # Tushare rate limit

    logger.info(f"Backfill complete: {total} total records")


if __name__ == "__main__":
    asyncio.run(backfill("20260101"))
