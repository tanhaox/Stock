"""Phase 47: 触发 6/5 深度评分 (L1 过滤后)"""
import asyncio, logging, sys, io, time
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
sys.stdout = io.StringIO()
sys.stderr = sys.stdout

from datetime import date
from app.core.database import async_session_factory
from app.services.deep_scorer import deep_analyze
from sqlalchemy import text

async def t():
    t0 = time.time()
    async with async_session_factory() as s:
        # Phase 47: L1 过滤已在 deep_analyze() 内部完成
        results = await deep_analyze(s, scan_date=date(2026, 6, 5), session_date=date(2026, 6, 5))

    async with async_session_factory() as s:
        # Verify
        r = await s.execute(text("SELECT COUNT(*) FROM analysis_scores WHERE scan_date='2026-06-05'"))
        cnt = r.scalar()
        r = await s.execute(text(
            "SELECT level, COUNT(*) FROM analysis_scores WHERE scan_date='2026-06-05' GROUP BY level ORDER BY level"
        ))
        levels = {row[0]: row[1] for row in r.fetchall()}

    elapsed = time.time() - t0
    with open("/tmp/phase47_result.txt", "w", encoding="utf-8") as f:
        f.write(f"Phase 47 OK: {len(results)} stocks scored in {elapsed:.0f}s\n")
        f.write(f"analysis_scores rows: {cnt}\n")
        f.write(f"levels: {levels}\n")
        f.write(f"L1 in results: {sum(1 for r in results if r.get('level','')=='L1')}\n")

asyncio.run(t())
