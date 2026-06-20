import asyncio,logging,sys,io,time
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
sys.stdout = io.StringIO()
sys.stderr = sys.stdout
from datetime import date
from app.core.database import async_session_factory
from app.services.deep_scorer import deep_analyze
from sqlalchemy import text
async def t():
    t0=time.time()
    async with async_session_factory() as s:
        r=await deep_analyze(s,scan_date=date(2026,6,4),session_date=date(2026,6,4))
    async with async_session_factory() as s:
        n = await s.execute(text("SELECT COUNT(*) FROM signal_history WHERE relative_position IS NOT NULL"))
        cnt = n.scalar()
    open("/tmp/phase44a_result.txt","w").write(f"OK: {len(r)} stocks in {time.time()-t0:.0f}s, signal_history enriched: {cnt}")
asyncio.run(t())
