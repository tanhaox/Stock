"""潜伏猎手 API."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db, async_session_factory

router = APIRouter(prefix="/ambush-signals", tags=["ambush"])


@router.get("")
async def get_ambush_signals(db: AsyncSession = Depends(get_db), limit: int = Query(20)):
    r = await db.execute(text(
        "SELECT symbol,name,scan_date,limit_up_date,limit_up_gain,max_drawdown,vol_shrink_ratio,launch_vol_ratio,composite_score "
        "FROM ambush_signals WHERE scan_date=(SELECT MAX(scan_date) FROM ambush_signals) "
        "ORDER BY composite_score DESC LIMIT :lim"
    ), {"lim": limit})
    data = [{
        "symbol": row[0], "name": row[1], "scan_date": str(row[2]), "limit_up_date": str(row[3]),
        "limit_up_gain": float(row[4] or 0), "max_drawdown": float(row[5] or 0),
        "vol_shrink_ratio": float(row[6] or 0), "launch_vol_ratio": float(row[7] or 0),
        "composite_score": float(row[8] or 0),
    } for row in r.fetchall()]
    return {"status": "success", "data": data, "count": len(data)}


@router.post("/trigger")
async def trigger_ambush_scan():
    """手动触发潜伏猎手 — 独立运行不依赖TG扫描, 使用会话日期去重."""
    from app.services.ambush_scanner import run_ambush_scan
    from datetime import date as dt_date

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT MAX(scan_date) FROM analysis_scores"))
        session_date = r.scalar() or dt_date.today()
        result = await run_ambush_scan(session=s, scan_date=session_date)
    return {"status": "success", "session_date": str(session_date), **result}
