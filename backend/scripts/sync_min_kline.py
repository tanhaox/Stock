"""同步分钟K线数据 — AlphaFlow池 + 持仓股票.

v2.3 (2026-06-02):
  - 扩展到池中股票 + 持仓股票
  - 5分钟线, 60天范围 (筹码吸收分析需要)
  - 并发 5 只, 尊重 Tushare API 限制
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare


async def sync_pool_min_kline():
    """为 AlphaFlow 池中股票 + 持仓同步 60 天 5 分钟线."""
    symbols = set()

    # 池中股票
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT ts_code FROM alphaflow_pool"))
        for row in r.fetchall():
            symbols.add(row[0])

        # 持仓股票
        r = await s.execute(text("SELECT DISTINCT symbol FROM holdings"))
        for row in r.fetchall():
            symbols.add(row[0])

    if not symbols:
        print("无目标股票")
        return

    symbols = list(symbols)
    print(f"目标股票: {len(symbols)} 只")

    today = date.today()
    start = (today - timedelta(days=65)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")

    sem = asyncio.Semaphore(5)
    synced = 0
    failed = 0

    async def _sync_one(sym):
        nonlocal synced, failed
        async with sem:
            try:
                rows = await call_tushare("stk_mins", {
                    "ts_code": sym, "freq": "5min",
                    "start_date": start, "end_date": end,
                }, "ts_code,trade_time,open,high,low,close,volume,amount")
                if not rows:
                    failed += 1
                    return

                inserted = 0
                async with async_session_factory() as s:
                    for r in rows:
                        tt = r.get("trade_time", "")
                        if not tt:
                            continue
                        await s.execute(text("""
                            INSERT INTO min_kline (ts_code, trade_time, open, high, low, close, volume, amount)
                            VALUES (:ts, CAST(:tt AS timestamp), :o, :h, :l, :c, :v, :a)
                            ON CONFLICT (ts_code, trade_time) DO UPDATE SET
                                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                                close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount
                        """), {
                            "ts": r["ts_code"], "tt": tt,
                            "o": float(r.get("open", 0) or 0), "h": float(r.get("high", 0) or 0),
                            "l": float(r.get("low", 0) or 0), "c": float(r.get("close", 0) or 0),
                            "v": float(r.get("volume", 0) or 0), "a": float(r.get("amount", 0) or 0),
                        })
                        inserted += 1
                    await s.commit()
                synced += 1
                if synced % 20 == 0:
                    cover = await _check_coverage()
                    print(f"  进度: {synced}/{len(symbols)} 同步, {failed} 失败, DB覆盖: {cover}")
            except Exception as e:
                failed += 1
                if failed <= 5:
                    print(f"  {sym}: {e}")

    tasks = [_sync_one(sym) for sym in symbols]
    await asyncio.gather(*tasks)

    cover = await _check_coverage()
    print(f"\n完成: {synced} 同步, {failed} 失败")
    print(f"min_kline 覆盖: {cover}")


async def _check_coverage():
    """返回 min_kline 覆盖统计."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT COUNT(*), COUNT(DISTINCT ts_code), MIN(trade_time), MAX(trade_time)
            FROM min_kline
        """))
        row = r.fetchone()
        return f"{row[0]}行/{row[1]}股"


if __name__ == "__main__":
    asyncio.run(sync_pool_min_kline())
