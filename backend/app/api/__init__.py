from fastapi import APIRouter

from app.api.scan import router as scan_router
from app.api.result import router as result_router
from app.api.feedback import router as feedback_router
from app.api.analysis import router as analysis_router
from app.api.llm_analysis import router as llm_router

router = APIRouter()
router.include_router(scan_router)
router.include_router(result_router)
router.include_router(feedback_router)
router.include_router(analysis_router)
router.include_router(llm_router)

@router.get("/health")
async def health_check():
    from app.core.database import async_session_factory
    from sqlalchemy import text
    try:
        async with async_session_factory() as s:
            await s.execute(text("SELECT 1"))
        return {"status": "ok", "version": "0.1.0", "checks": {"database": {"status": "ok"}}}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
from app.api.holdings import router as holdings_router
router.include_router(holdings_router)
from app.api.learning import router as learning_router
router.include_router(learning_router)
from app.api.ambush import router as ambush_router
router.include_router(ambush_router)
from app.api.settings import router as settings_router
router.include_router(settings_router)
from app.api.decisions import router as decisions_router
router.include_router(decisions_router)
from app.api.alphaflow import router as alphaflow_router
router.include_router(alphaflow_router)
from app.api.comprehensive import router as comprehensive_router
router.include_router(comprehensive_router)
from app.api.drill import router as drill_router
router.include_router(drill_router)
from app.api.sanxian import router as sanxian_router
router.include_router(sanxian_router)
