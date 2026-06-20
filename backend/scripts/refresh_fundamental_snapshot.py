#!/usr/bin/env python3
"""refresh_fundamental_snapshot.py — 基本面快照刷新."""
import asyncio, sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from sqlalchemy import text
from app.core.database import async_session_factory

async def refresh_snapshot():
    async with async_session_factory() as s:
        cutoff = date.today() - timedelta(days=30)
        r = await s.execute(text("""SELECT DISTINCT symbol FROM scan_results WHERE scan_date>=:c UNION SELECT DISTINCT symbol FROM ambush_signals WHERE scan_date>=:c"""), {"c": cutoff})
        symbols = [row[0] for row in r.fetchall()]
    if not symbols: return {"status": "empty", "count": 0}
    inserted = 0
    today = date.today()
    for sym in symbols:
        try:
            async with async_session_factory() as s:
                # 财报滞后保护: 年报(end_date month=12) → 120天, 中报(month=6) → 60天, 季报(month=3,9) → 30天
                # 滞后天数未经过的财报不可用（防止未来函数）
                r = await s.execute(text("""
                    SELECT roe, or_yoy, profit_dedt, debt_to_assets, current_ratio, goodwill, end_date
                    FROM fina_indicator
                    WHERE ts_code=:s
                      AND end_date < CURRENT_DATE - CASE
                        WHEN EXTRACT(MONTH FROM end_date) = 12 THEN 120
                        WHEN EXTRACT(MONTH FROM end_date) = 6 THEN 60
                        ELSE 30
                      END
                    ORDER BY CASE WHEN EXTRACT(MONTH FROM end_date)=12 THEN 0 ELSE 1 END, end_date DESC
                    LIMIT 1
                """), {"s": sym})
                fi = r.fetchone()
                r = await s.execute(text("SELECT trade_date,pb,pe_ttm FROM daily_basic WHERE ts_code=:s ORDER BY trade_date DESC LIMIT 1"), {"s": sym})
                db = r.fetchone()
                if not fi and not db: continue
                td = db[0] if db else date.today()
                roe = float(fi[0]) if fi and fi[0] else None
                revenue_yoy = float(fi[1]) if fi and fi[1] else None
                profit_dedt_cur = float(fi[2]) if fi and fi[2] else None
                debt = float(fi[3]) if fi and fi[3] else None
                cr = float(fi[4]) if fi and fi[4] else None
                pb = float(db[1]) if db and db[1] else None
                pe_ttm = float(db[2]) if db and db[2] else None
                profit_yoy = None
                if profit_dedt_cur is not None:
                    r2 = await s.execute(text("SELECT profit_dedt FROM fina_indicator WHERE ts_code=:s AND EXTRACT(YEAR FROM end_date)=EXTRACT(YEAR FROM CURRENT_DATE)-1 AND EXTRACT(MONTH FROM end_date)=12 LIMIT 1"), {"s": sym})
                    prev = r2.fetchone()
                    if prev and prev[0] and prev[0] != 0: profit_yoy = round((profit_dedt_cur-prev[0])/abs(prev[0])*100,2)
                ocflow_net = None
                try:
                    r3 = await s.execute(text("SELECT n_cashflow_act FROM cashflow WHERE ts_code=:s ORDER BY CASE WHEN EXTRACT(MONTH FROM end_date)=12 THEN 0 ELSE 1 END,end_date DESC LIMIT 1"), {"s": sym})
                    ocf = r3.fetchone()
                    if ocf and ocf[0]: ocflow_net = float(ocf[0])
                except: pass
                await s.execute(text("""INSERT INTO stock_fundamental_snapshot (symbol,trade_date,roe,revenue_yoy,profit_yoy,debt_to_assets,current_ratio,ocflow_net,pb,pe_ttm,updated_at) VALUES(:s,:td,:roe,:ry,:py,:da,:cr,:ocf,:pb,:pe,NOW()) ON CONFLICT(symbol) DO UPDATE SET trade_date=EXCLUDED.trade_date,roe=EXCLUDED.roe,revenue_yoy=EXCLUDED.revenue_yoy,profit_yoy=EXCLUDED.profit_yoy,debt_to_assets=EXCLUDED.debt_to_assets,current_ratio=EXCLUDED.current_ratio,ocflow_net=EXCLUDED.ocflow_net,pb=EXCLUDED.pb,pe_ttm=EXCLUDED.pe_ttm,updated_at=NOW()"""), {
                    "s": sym, "td": td, "roe": roe, "ry": revenue_yoy, "py": profit_yoy,
                    "da": debt, "cr": cr, "ocf": ocflow_net, "pb": pb, "pe": pe_ttm,
                })
                await s.commit()
                inserted += 1
        except Exception: pass
    return {"status": "success", "inserted": inserted}

if __name__ == "__main__":
    asyncio.run(refresh_snapshot()); print("Done")
