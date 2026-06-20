#!/usr/bin/env python3
"""Sync stock_tags — 股票板块/风险/市值/上市时长标签 (Phase 26b).

数据源: Tushare stock_basic + 本地 daily_kline.
用法: PYTHONPATH=. python scripts/sync_stock_tags.py
"""
import asyncio, logging
from datetime import date, datetime
from collections import Counter
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("sync_stock_tags")


def classify_board(code: str) -> str:
    """按代码前缀判定板块."""
    if code.startswith("60"):
        return "主板"
    elif code.startswith("00") or code.startswith("001"):
        return "主板"
    elif code.startswith("30"):
        return "创业板"
    elif code.startswith("688"):
        return "科创板"
    elif code.startswith("8"):
        return "北交所"
    return "未知"


def classify_risk(name: str) -> str:
    """从股票名称判定风险状态."""
    if not name:
        return "正常"
    if "*ST" in name:
        return "*ST"
    if "ST" in name:
        return "ST"
    if "PT" in name:
        return "PT"
    return "正常"


def classify_market_cap(avg_close: float) -> str:
    """按近 20 日均 close 粗略分档 (后续 Phase 26c 用 daily_basic.total_mv 修正)."""
    if avg_close >= 500:
        return "大盘"
    elif avg_close >= 100:
        return "中盘"
    elif avg_close >= 30:
        return "小盘"
    else:
        return "微盘"


def classify_ipo_age(list_date_str: str, today: date) -> str:
    """按上市日期分档."""
    if not list_date_str:
        return "未知"
    try:
        ld = datetime.strptime(str(list_date_str)[:10], "%Y%m%d").date()
    except ValueError:
        return "未知"
    years = (today - ld).days / 365.25
    if years < 1:
        return "次新(<1年)"
    elif years < 3:
        return "次新(1-3年)"
    else:
        return "成熟"


async def main():
    today = date.today()
    logger.info(f"=== stock_tags sync {today} ===")

    # ── Step 0: Ensure table exists ──
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_tags (
                ts_code VARCHAR(20) PRIMARY KEY,
                name VARCHAR(50),
                board VARCHAR(10),
                risk_status VARCHAR(10),
                market_cap_tier VARCHAR(10),
                ipo_age VARCHAR(10),
                updated_at TIMESTAMPTZ
            )
        """))
        await s.commit()
    logger.info("Table stock_tags ready")

    # ── Step 1: Fetch all listed stocks from Tushare ──
    logger.info("Fetching stock_basic (L listed)...")
    rows = await call_tushare(
        'stock_basic',
        {'list_status': 'L'},
        'ts_code,name,list_date'
    )
    if not rows:
        logger.error("No data from stock_basic")
        return
    logger.info(f"  Got {len(rows)} stocks")

    # ── Step 2: Batch-fetch 20-day avg close from daily_kline ──
    codes = [r["ts_code"] for r in rows if r.get("ts_code")]
    # Only compute for stocks with >= 20 days of data
    cap_map: dict[str, float] = {}
    BATCH_SIZE = 500
    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i:i + BATCH_SIZE]
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, AVG(close) as avg_close
                FROM (
                    SELECT ts_code, close,
                           ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) as rn
                    FROM daily_kline
                    WHERE ts_code = ANY(:codes)
                ) sub
                WHERE rn <= 20
                GROUP BY ts_code
            """), {"codes": batch})
            for row in r.fetchall():
                cap_map[row[0]] = float(row[1] or 0)

    # ── Step 3: Classify & insert ──
    distributions: dict[str, Counter] = {
        "board": Counter(), "risk": Counter(),
        "cap_tier": Counter(), "ipo_age": Counter(),
    }
    inserted = 0

    async with async_session_factory() as s:
        for item in rows:
            code = item.get("ts_code", "")
            if not code:
                continue
            name_val = item.get("name", "")
            list_date = item.get("list_date", "")

            board = classify_board(code)
            risk = classify_risk(name_val)
            avg_c = cap_map.get(code, 0)
            cap_tier = classify_market_cap(avg_c)
            ipo = classify_ipo_age(list_date, today)

            distributions["board"][board] += 1
            distributions["risk"][risk] += 1
            distributions["cap_tier"][cap_tier] += 1
            distributions["ipo_age"][ipo] += 1

            await s.execute(text("""
                INSERT INTO stock_tags (ts_code, name, board, risk_status, market_cap_tier, ipo_age, updated_at)
                VALUES (:c, :n, :b, :r, :m, :i, NOW())
                ON CONFLICT (ts_code) DO UPDATE SET
                    name=EXCLUDED.name, board=EXCLUDED.board,
                    risk_status=EXCLUDED.risk_status,
                    market_cap_tier=EXCLUDED.market_cap_tier,
                    ipo_age=EXCLUDED.ipo_age, updated_at=NOW()
            """), {
                "c": code, "n": name_val, "b": board, "r": risk,
                "m": cap_tier, "i": ipo,
            })
            inserted += 1

        await s.commit()

    logger.info(f"\nSynced {inserted} stocks")
    for col in ["board", "risk", "cap_tier", "ipo_age"]:
        logger.info(f"  {col}: {dict(distributions[col].most_common())}")


if __name__ == "__main__":
    asyncio.run(main())
