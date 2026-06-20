"""Async Redis 客户端 — 扫描状态共享 (支持多 worker).

提供 get_redis() / close_redis() 用于管理连接池.
Redis 不可用时自动降级为 None (调用方应回退到内存模式).
"""
import logging
from app.core.config import settings

logger = logging.getLogger(__name__)

_redis = None


async def get_redis():
    """获取 Redis 连接 (首次调用时创建). 失败返回 None."""
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=3,
        )
        await _redis.ping()
        logger.info(f"Redis connected: {settings.REDIS_URL}")
        return _redis
    except Exception as e:
        logger.debug(f"Redis unavailable (will use in-memory fallback): {e}")
        _redis = None
        return None


async def close_redis():
    """关闭 Redis 连接."""
    global _redis
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
        _redis = None
