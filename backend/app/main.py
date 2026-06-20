import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stock_analyst")

_background_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _background_task
    logger.info("Stock Analyst starting up")
    from app.core.database import engine
    from app.models.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database verified")

    # ★ v4.3: 创建缺失索引
    try:
        from app.models.data_models import ensure_indexes
        await ensure_indexes()
        logger.info("Database indexes verified")
    except Exception as e:
        logger.warning(f"Database index creation failed: {e}")
        pass

    # 加载原型偏移校准缓存
    try:
        from app.services.archetype_param_resolver import _load_overrides_from_db
        await _load_overrides_from_db()
    except Exception as e:
        logger.warning(f"Startup archetype load failed: {e}")
        pass  # 表可能不存在, 静默降级

    # 启动后台调度器(每日 16:00 自动回测+快照刷新)
    import asyncio as _asyncio
    async def _run_scheduler():
        try:
            from app.scheduler.scheduler_loop import scheduler_loop
            await scheduler_loop()
        except Exception as e:
            logger.error(f"Background scheduler failed: {e}", exc_info=True)

    _background_task = _asyncio.create_task(_run_scheduler())
    logger.info("Background scheduler started (16:00 daily)")

    yield

    if _background_task:
        _background_task.cancel()
    try:
        from app.core.redis_client import close_redis
        await close_redis()
    except Exception:
        pass
    logger.info("Stock Analyst shutting down")

app = FastAPI(title="Stock Analyst", version="0.1.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True, allow_methods=["GET","POST","PUT","DELETE"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")

# DNA 系统路由 (全新, 与现有系统并行)
from app.api.dna import router as dna_router
app.include_router(dna_router, prefix="/api")

# 宏观数据路由 (M-5: 替代新闻爬虫宏观分析)
from app.api.macro import router as macro_router
app.include_router(macro_router, prefix="/api")

# Admin 路由 (v7.0.34: 数据维护手动触发, 如 exclusion_list 刷新)
from app.api.admin import router as admin_router
app.include_router(admin_router, prefix="/api")

@app.get("/api/")
async def api_index():
    return {"name": "Stock Analyst API", "version": "0.1.0"}
