#!/usr/bin/env python3
"""Fix & expand sw_sector_index: ALTER TABLE + full Tushare sw_daily sync for 28 SW sectors.

Problem: 15,159 rows but pct_chg all =0, missing open/high/low/vol/amount/pe/pb/name.
Solution: ALTER TABLE -> call_tushare(sw_daily) per sector -> INSERT with date conversion.

Usage: PYTHONPATH=. python scripts/sync_sw_sector.py
"""
import asyncio, logging
from datetime import date
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("sync_sw")

SW_SECTORS = [
    ("801010.SI", "农林牧渔"), ("801020.SI", "采掘"), ("801030.SI", "化工"),
    ("801040.SI", "钢铁"), ("801050.SI", "有色金属"), ("801080.SI", "电子"),
    ("801110.SI", "家用电器"), ("801120.SI", "食品饮料"), ("801130.SI", "纺织服装"),
    ("801140.SI", "轻工制造"), ("801150.SI", "医药生物"), ("801160.SI", "公用事业"),
    ("801170.SI", "交通运输"), ("801180.SI", "房地产"), ("801200.SI", "商业贸易"),
    ("801210.SI", "休闲服务"), ("801230.SI", "综合"), ("801710.SI", "建筑材料"),
    ("801720.SI", "建筑装饰"), ("801730.SI", "电气设备"), ("801740.SI", "国防军工"),
    ("801750.SI", "计算机"), ("801760.SI", "传媒"), ("801770.SI", "通信"),
    ("801780.SI", "银行"), ("801790.SI", "非银金融"), ("801880.SI", "汽车"),
    ("801890.SI", "机械设备"),
]


def _to_date(s: str):
    """Convert Tushare 'YYYYMMDD' string to Python date."""
    if isinstance(s, date):
        return s
    if len(s) == 8:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return date.today()


async def main():
    # ── Step 1: ALTER TABLE ──
    alter_cols = [
        "ADD COLUMN IF NOT EXISTS open FLOAT",
        "ADD COLUMN IF NOT EXISTS high FLOAT",
        "ADD COLUMN IF NOT EXISTS low FLOAT",
        "ADD COLUMN IF NOT EXISTS vol FLOAT",
        "ADD COLUMN IF NOT EXISTS amount FLOAT",
        "ADD COLUMN IF NOT EXISTS pe FLOAT",
        "ADD COLUMN IF NOT EXISTS pb FLOAT",
        "ADD COLUMN IF NOT EXISTS name VARCHAR(50)",
    ]
    async with async_session_factory() as s:
        for col in alter_cols:
            try:
                await s.execute(text(f"ALTER TABLE sw_sector_index {col}"))
            except Exception as e:
                logger.warning(f"ALTER skipped: {e}")
        await s.commit()
    logger.info("Schema ready")

    # ── Step 2: Fetch & insert per sector ──
    today = date.today().strftime('%Y%m%d')
    total_inserted = 0

    for code, name in SW_SECTORS:
        logger.info(f"Fetching {code} ({name})...")
        try:
            rows = await call_tushare(
                'sw_daily',
                {'ts_code': code, 'start_date': '20200101', 'end_date': today},
                'ts_code,trade_date,open,high,low,close,vol,amount,pe,pb,pct_change'
            )
        except Exception as e:
            logger.warning(f"  Tushare fail: {e}")
            continue

        if not rows:
            logger.warning(f"  No data returned")
            continue

        inserted = 0
        async with async_session_factory() as s:
            for item in rows:
                try:
                    td = _to_date(item.get("trade_date", ""))
                    await s.execute(text("""
                        INSERT INTO sw_sector_index
                            (index_code,trade_date,open,high,low,close,
                             vol,amount,pe,pb,pct_chg,name)
                        VALUES (:c,:d,:o,:h,:l,:cl,:v,:a,:pe,:pb,:pct,:nm)
                        ON CONFLICT (index_code,trade_date) DO UPDATE SET
                            open=EXCLUDED.open,high=EXCLUDED.high,
                            low=EXCLUDED.low,close=EXCLUDED.close,
                            vol=EXCLUDED.vol,amount=EXCLUDED.amount,
                            pe=EXCLUDED.pe,pb=EXCLUDED.pb,
                            pct_chg=EXCLUDED.pct_chg,name=EXCLUDED.name
                    """), {
                        "c": code, "d": td,
                        "o": float(item.get("open", 0) or 0),
                        "h": float(item.get("high", 0) or 0),
                        "l": float(item.get("low", 0) or 0),
                        "cl": float(item.get("close", 0) or 0),
                        "v": float(item.get("vol", 0) or 0),
                        "a": float(item.get("amount", 0) or 0),
                        "pe": float(item.get("pe", 0) or 0),
                        "pb": float(item.get("pb", 0) or 0),
                        "pct": round(float(item.get("pct_change", 0) or 0), 2),
                        "nm": name,
                    })
                    inserted += 1
                except Exception:
                    pass
            await s.commit()

        logger.info(f"  {code}: {inserted} rows ({name})")
        total_inserted += inserted
        await asyncio.sleep(0.4)  # QPS control

    logger.info(f"\nTotal: {total_inserted} rows across {len(SW_SECTORS)} sectors")


if __name__ == "__main__":
    asyncio.run(main())
