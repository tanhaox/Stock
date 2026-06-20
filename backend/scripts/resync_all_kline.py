#!/usr/bin/env python3
"""全量重新拉取 daily_kline (前复权) — v2: adj_factor API 手动复权.

Tushare daily API 的 adj='qfq' 参数在某些版本不可用。
改用 adj_factor API 获取复权因子，手动计算前复权价格后写入 daily_kline。

公式: close_adj = close_raw × (adj_factor[t] / adj_factor[latest_date])

用法:
  PYTHONPATH=. python scripts/resync_all_kline.py --symbols 002594.SZ
  PYTHONPATH=. python scripts/resync_all_kline.py --symbols 002594.SZ,300750.SZ
  PYTHONPATH=. python scripts/resync_all_kline.py --top 200
"""
import asyncio, sys, argparse, logging
from pathlib import Path
from datetime import date as dt_date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.tushare_common import call_tushare
from app.core.database import async_session_factory
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("resync_kline")


async def fetch_and_apply_qfq(sym: str, sem: asyncio.Semaphore) -> dict:
    """拉取原始日线 + 复权因子, 计算前复权价格, 写入 daily_kline."""
    async with sem:
        try:
            today = dt_date.today()
            start_str = "20180101"

            # Step 1: 拉取原始日线
            raw_rows = await call_tushare("daily", {
                "ts_code": sym,
                "start_date": start_str,
                "end_date": today.strftime("%Y%m%d"),
            }, "ts_code,trade_date,open,high,low,close,vol,amount")
            if not raw_rows:
                return {"symbol": sym, "status": "empty"}

            # Step 2: 拉取复权因子
            adj_rows = await call_tushare("adj_factor", {
                "ts_code": sym,
            }, "ts_code,trade_date,adj_factor")
            adj_map: dict[str, float] = {}
            if adj_rows:
                for r in adj_rows:
                    adj_map[r.get("trade_date", "")] = float(r.get("adj_factor", 1.0) or 1.0)

            # Forward-fill adj_factor: each date gets the adj_factor of the most recent change date ≤ that date
            adj_dates = sorted(adj_map.keys())
            all_dates = sorted(set(r.get("trade_date", "") for r in raw_rows if len(r.get("trade_date", "")) == 8))
            filled_adj: dict[str, float] = {}
            adj_idx = 0
            for d in all_dates:
                while adj_idx + 1 < len(adj_dates) and adj_dates[adj_idx + 1] <= d:
                    adj_idx += 1
                if adj_idx < len(adj_dates) and adj_dates[adj_idx] <= d:
                    filled_adj[d] = adj_map[adj_dates[adj_idx]]
                else:
                    filled_adj[d] = 1.0

            # 最新复权因子 (基准)
            latest_af = adj_map.get(adj_dates[-1], 1.0) if adj_dates else 1.0

            # Step 3: 前复权计算 + 写入
            inserted = 0
            async with async_session_factory() as s:
                for r in raw_rows:
                    td_str = r.get("trade_date", "")
                    if len(td_str) == 8:
                        td = dt_date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
                    else:
                        continue

                    # 当前日期的复权因子 (forward-filled)
                    af = filled_adj.get(td_str, 1.0)
                    # 前复权系数: adj_factor[t] / adj_factor[latest]
                    # 表示要将 t 日的价格乘以这个系数, 使其与最新价格可比
                    qfq_ratio = af / max(latest_af, 0.0001)

                    await s.execute(text("""
                        INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, volume, amount, adj_factor)
                        VALUES (:ts, :td, :o, :h, :l, :c, :v, :a, :af)
                        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                            open=EXCLUDED.open, high=EXCLUDED.high,
                            low=EXCLUDED.low, close=EXCLUDED.close,
                            volume=EXCLUDED.volume, amount=EXCLUDED.amount,
                            adj_factor=EXCLUDED.adj_factor
                    """), {
                        "ts": r["ts_code"], "td": td,
                        "o": round(float(r.get("open", 0) or 0) * qfq_ratio, 3),
                        "h": round(float(r.get("high", 0) or 0) * qfq_ratio, 3),
                        "l": round(float(r.get("low", 0) or 0) * qfq_ratio, 3),
                        "c": round(float(r.get("close", 0) or 0) * qfq_ratio, 3),
                        "v": float(r.get("vol", 0) or 0),
                        "a": float(r.get("amount", 0) or 0),
                        "af": af,
                    })
                    inserted += 1
                await s.commit()

            return {"symbol": sym, "status": "success", "rows": inserted}
        except Exception as e:
            return {"symbol": sym, "status": "error", "detail": str(e)[:200]}


async def resync_all(symbols: list[str]):
    sem = asyncio.Semaphore(8)
    logger.info(f"Resyncing {len(symbols)} stocks with adj_factor-based qfq...")
    tasks = [fetch_and_apply_qfq(sym, sem) for sym in symbols]
    results = await asyncio.gather(*tasks)

    success = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] != "success"]
    total_rows = sum(r.get("rows", 0) for r in success)

    logger.info(f"Done: {len(success)} success ({total_rows} rows), {len(failed)} failed")
    if failed:
        for f in failed[:5]:
            logger.warning(f"  {f['symbol']}: {f['status']} - {f.get('detail','')}")

    return results


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=str, default=None)
    parser.add_argument("--top", type=int, default=0)
    args = parser.parse_args()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    elif args.top > 0:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT DISTINCT ts_code FROM daily_kline WHERE (ts_code LIKE '60%' OR ts_code LIKE '00%' OR ts_code LIKE '30%') LIMIT :n"
            ), {"n": args.top})
            symbols = [row[0] for row in r.fetchall()]
    else:
        print("ERROR: --symbols or --top required")
        sys.exit(1)

    print(f"Target: {len(symbols)} stocks")
    await resync_all(symbols)


if __name__ == "__main__":
    asyncio.run(main())
