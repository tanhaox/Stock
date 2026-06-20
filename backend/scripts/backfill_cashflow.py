#!/usr/bin/env python3
"""backfill_cashflow.py — 填充 cashflow 表."""
import asyncio, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.tushare_common import call_tushare
from app.core.database import async_session_factory
from sqlalchemy import text

async def backfill():
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT ts_code FROM daily_kline"))
        syms = [row[0] for row in r.fetchall()]
    print(f"Stocks: {len(syms)}")
    ok = 0
    for i, sym in enumerate(syms):
        try:
            rows = await call_tushare("cashflow", {"ts_code": sym, "start_date": "20230101", "end_date": date.today().strftime("%Y%m%d")}, "ts_code,end_date,n_cashflow_act")
            if rows:
                async with async_session_factory() as s:
                    for r in rows:
                        ed = r.get("end_date","")
                        if not ed: continue
                        try: ed_dt = date(int(ed[:4]), int(ed[4:6]), int(ed[6:8]))
                        except: continue
                        await s.execute(text("INSERT INTO cashflow (ts_code,end_date,n_cashflow_act) VALUES(:ts,:ed,:v) ON CONFLICT DO NOTHING"), {"ts": r["ts_code"], "ed": ed_dt, "v": float(r.get("n_cashflow_act",0) or 0)})
                    await s.commit()
                    ok += 1
        except: pass
        await asyncio.sleep(0.3)
        if (i+1) % 200 == 0: print(f"  {i+1}/{len(syms)} ok={ok}")
    print(f"Done: {ok}/{len(syms)}")

if __name__ == "__main__":
    asyncio.run(backfill())
