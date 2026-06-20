#!/usr/bin/env python3
"""Sync sector_min_kline — 34 指数级别 5 分钟 K 线 (Phase 26d).

覆盖: 上证/创业板/沪深300/中证1000 + 32 个 SW 一级行业指数.
数据源: Tushare idx_mins API.
用法:
  PYTHONPATH=. python scripts/sync_sector_min_kline.py         # 回填 30 天
  PYTHONPATH=. python scripts/sync_sector_min_kline.py --today  # 仅今天
"""
import asyncio, logging, sys
from datetime import date, datetime, timedelta
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("sector_mins")

FREQ = '5min'
BATCH_SIZE = 10  # concurrent calls per batch
DAYS_BACK = 30   # initial backfill

# 4 个大盘指数 + 80 个 SSE 行业/主题指数 (idx_mins 仅支持 000xxx.SH)
MARKET_CODES = [
    "000001.SH",  # 上证综指
    "399006.SZ",  # 创业板指
    "000300.SH",  # 沪深300
    "000852.SH",  # 中证1000
]

SSE_INDUSTRY_CODES = [
    # ── 一级行业指数 ──
    "000032.SH",  # 上证能源
    "000033.SH",  # 上证材料
    "000034.SH",  # 上证工业
    "000035.SH",  # 上证可选
    "000036.SH",  # 上证消费
    "000037.SH",  # 上证医药
    "000039.SH",  # 上证信息
    "000040.SH",  # 上证电信
    "000041.SH",  # 上证公用
    # ── 主题/行业指数 ──
    "000004.SH",  # 工业指数
    "000005.SH",  # 商业指数
    "000006.SH",  # 地产指数
    "000007.SH",  # 公用指数
    "000008.SH",  # 综合指数
    "000018.SH",  # 180金融
    "000038.SH",  # 上证金融
    "000042.SH",  # 上证资源
    "000048.SH",  # 上证消费80
    "000049.SH",  # 上证医药卫生
    "000050.SH",  # 50等权
    "000819.SH",  # 有色金属
    "000820.SH",  # 煤炭指数
    "000823.SH",  # 800有色
    "000828.SH",  # 300非银
    "000840.SH",  # 浙江国资
    "000841.SH",  # 800医药
    "000842.SH",  # 800消费
    "000843.SH",  # 800信息
    "000844.SH",  # 300医药
    "000845.SH",  # 300消费
    "000846.SH",  # 300信息
    "000847.SH",  # 300能源
    "000848.SH",  # 300材料
    "000849.SH",  # 300工业
    "000902.SH",  # 中证流通
    "000922.SH",  # 中证红利
    "000933.SH",  # 中证医药
    "000935.SH",  # 中证信息
    "000936.SH",  # 中证电信
    "000938.SH",  # 中证可选
    "000939.SH",  # 中证消费
    "000940.SH",  # 中证医药100
    "000941.SH",  # 中证能源
    "000942.SH",  # 中证材料
    "000943.SH",  # 中证金融
    "000944.SH",  # 中证工业
    "000945.SH",  # 中证公用
    "000951.SH",  # 300银行
    "000952.SH",  # 300地产
    "000953.SH",  # 300运输
    "000954.SH",  # 300医药卫生
    "000955.SH",  # 300信息科技
    "000958.SH",  # 创业成长
    "000959.SH",  # 创业价值
    "000960.SH",  # 创业医药
    "000961.SH",  # 创业信息
    "000962.SH",  # 创业工业
    "000963.SH",  # 创业消费
    "000964.SH",  # 创业材料
    "000965.SH",  # 创业能源
]

SECTOR_CODES = MARKET_CODES + SSE_INDUSTRY_CODES  # 84 个指数

# 以下 33 个代码经 2026-06-04 idx_mins 直接测试返回 0 行，永久跳过
SKIP_CODES = {
    "000820.SH",  # 煤炭指数(沪)
    "000828.SH",  # 300非银
    "000840.SH",  # 浙江国资
    "000841.SH",  # 800医药
    "000842.SH",  # 800消费
    "000843.SH",  # 800信息
    "000844.SH",  # 300医药
    "000845.SH",  # 300消费
    "000846.SH",  # 300信息
    "000848.SH",  # 300材料
    "000902.SH",  # 中证流通
    "000922.SH",  # 中证红利
    "000936.SH",  # 中证电信
    "000938.SH",  # 中证可选
    "000939.SH",  # 中证消费
    "000940.SH",  # 中证医药100
    "000941.SH",  # 中证能源
    "000942.SH",  # 中证材料
    "000943.SH",  # 中证金融
    "000944.SH",  # 中证工业
    "000945.SH",  # 中证公用
    "000951.SH",  # 300银行
    "000952.SH",  # 300地产
    "000953.SH",  # 300运输
    "000954.SH",  # 300医药卫生
    "000955.SH",  # 300信息科技
    "000958.SH",  # 创业成长
    "000959.SH",  # 创业价值
    "000960.SH",  # 创业医药
    "000961.SH",  # 创业信息
    "000962.SH",  # 创业工业
    "000963.SH",  # 创业消费
    "000964.SH",  # 创业材料
    "000965.SH",  # 创业能源
}


async def sync_one_date(code: str, start: str, end: str) -> int:
    """Fetch and insert 5-min bars for one sector on one date range. Returns count."""
    try:
        rows = await call_tushare(
            'idx_mins',
            {'ts_code': code, 'freq': FREQ, 'start_date': start, 'end_date': end},
            'ts_code,trade_time,open,high,low,close,vol,amount'
        )
    except Exception as e:
        logger.warning(f"  {code} Tushare fail: {e}")
        return 0

    if not rows:
        return 0

    count = 0
    async with async_session_factory() as s:
        for item in rows:
            try:
                tt = item.get("trade_time", "")
                # Parse ISO timestamp or 'YYYY-MM-DD HH:MM:SS'
                if isinstance(tt, str) and len(tt) >= 16:
                    tt_dt = datetime.fromisoformat(tt)
                else:
                    continue

                await s.execute(text("""
                    INSERT INTO sector_min_kline
                        (sector_code, trade_time, open, high, low, close, vol, amount)
                    VALUES (:c, :t, :o, :h, :l, :cl, :v, :a)
                    ON CONFLICT (sector_code, trade_time) DO NOTHING
                """), {
                    "c": code, "t": tt_dt,
                    "o": float(item.get("open", 0) or 0),
                    "h": float(item.get("high", 0) or 0),
                    "l": float(item.get("low", 0) or 0),
                    "cl": float(item.get("close", 0) or 0),
                    "v": float(item.get("vol", 0) or 0),
                    "a": float(item.get("amount", 0) or 0),
                })
                count += 1
            except Exception:
                pass
        await s.commit()
    return count


async def get_trading_days(days_back: int) -> list[str]:
    """Get recent trading days from index_daily."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM index_daily "
            "WHERE ts_code='000001.SH' ORDER BY trade_date DESC LIMIT :n"
        ), {"n": days_back})
        return [row[0].strftime("%Y%m%d") for row in r.fetchall() if row[0]]


async def build_table():
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS sector_min_kline (
                sector_code VARCHAR(20) NOT NULL,
                trade_time TIMESTAMP NOT NULL,
                open FLOAT, high FLOAT, low FLOAT, close FLOAT,
                vol FLOAT, amount FLOAT,
                PRIMARY KEY (sector_code, trade_time)
            )"""))
        await s.commit()
    logger.info("Table sector_min_kline ready")


async def sync_full():
    """Full backfill: fetch 30 trading days for all sectors."""
    tdays = await get_trading_days(DAYS_BACK)
    if not tdays:
        logger.error("No trading days found in index_daily")
        return

    start_date = tdays[-1]  # earliest
    end_date = tdays[0]     # latest (most recent)
    logger.info(f"Full backfill: {start_date} ~ {end_date} ({len(tdays)} days) × {len(SECTOR_CODES)} sectors")

    total = 0
    for i in range(0, len(SECTOR_CODES), BATCH_SIZE):
        batch = [c for c in SECTOR_CODES[i:i + BATCH_SIZE] if c not in SKIP_CODES]
        tasks = [sync_one_date(code, start_date, end_date) for code in batch]
        results = await asyncio.gather(*tasks)
        for code, n in zip(batch, results):
            logger.info(f"  {code}: {n} bars")
            total += n
        if i + BATCH_SIZE < len(SECTOR_CODES):
            await asyncio.sleep(1.2)  # rate limit

    logger.info(f"\nTotal: {total} bars across {len(SECTOR_CODES)} sectors")


async def sync_today():
    """Incremental: fetch today only."""
    today = date.today().strftime("%Y%m%d")
    logger.info(f"Today sync: {today} × {len(SECTOR_CODES)} sectors")
    total = 0
    for i in range(0, len(SECTOR_CODES), BATCH_SIZE):
        batch = [c for c in SECTOR_CODES[i:i + BATCH_SIZE] if c not in SKIP_CODES]
        tasks = [sync_one_date(code, today, today) for code in batch]
        results = await asyncio.gather(*tasks)
        for code, n in zip(batch, results):
            if n > 0:
                logger.info(f"  {code}: +{n} bars")
            total += n
    if total > 0:
        logger.info(f"Today: +{total} bars")


async def main():
    await build_table()
    if "--today" in sys.argv:
        await sync_today()
    else:
        await sync_full()


if __name__ == "__main__":
    asyncio.run(main())
