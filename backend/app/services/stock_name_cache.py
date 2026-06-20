"""股票名称缓存 — 从 Tushare stock_basic 获取."""
import logging
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)
_cache = {}
_loaded = False

async def _ensure_cache():
    global _cache, _loaded
    if _loaded: return
    try:
        from app.services.tushare_common import call_tushare
        rows = await call_tushare("stock_basic", {"list_status": "L"}, "ts_code,symbol,name")
        for r in rows:
            _cache[r["ts_code"]] = r.get("name", r["ts_code"])
        _loaded = True
    except Exception as e:
        logger.warning("stock_name_cache load failed: %s", e)

def get_stock_name(ts_code: str) -> str:
    return _cache.get(ts_code, ts_code)
