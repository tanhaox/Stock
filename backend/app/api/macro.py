"""宏观数据 API — /api/macro"""
from fastapi import APIRouter
from app.services.macro_data import get_macro_snapshot, generate_morning_brief, score_macro_impact

router = APIRouter(prefix="/macro", tags=["macro"])


@router.post("/sync")
async def macro_sync():
    """手动触发宏观数据同步."""
    from app.services.macro_data import sync_macro_cache
    result = await sync_macro_cache()
    return {"status": "success", "data": result}


@router.get("/snapshot")
async def macro_snapshot():
    data = await get_macro_snapshot()
    return {"status": "success", "data": data}


@router.get("/brief")
async def macro_brief():
    brief = await generate_morning_brief()
    return {"status": "success", **brief}
