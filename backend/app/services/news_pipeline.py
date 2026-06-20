"""News pipeline service - extracted from scan.py (v4.3)."""
import asyncio
import json
import logging
from datetime import date

logger = logging.getLogger("news_pipeline")


async def run_news_pipeline(force: bool = False, progress_callback=None) -> dict:
    """Crawl -> extract events -> LLM analyze -> rescore pipeline."""
    result = {"status": "ok", "steps": []}

    # Phase 51b: 包装适配 — progress_cb(phase, current, total, extra), 本文件用 (phase, msg)
    async def _cb(phase: str, msg: str = ""):
        if progress_callback:
            await progress_callback(phase, 0, 1, msg)

    # Step 1: Crawl
    try:
        from app.services.news_crawler import crawl_all_sources
        crawl = await crawl_all_sources()
        result["steps"].append({"step": "crawl", "status": crawl.get("status", "ok")})
    except Exception as e:
        logger.warning(f"News crawl failed: {e}")
        return {"status": "error", "detail": f"crawl: {e}"}

    # Step 2: Analyze with LLM
    try:
        from app.services.event_detector import analyze_all_sources
        analysis = await analyze_all_sources(hours_back=48, progress_cb=progress_callback)
        result["steps"].append({"step": "analyze",
                                 "categories": len(analysis.get("analyzed_categories", []))})
        result["analysis"] = analysis
    except Exception as e:
        logger.warning(f"News analysis failed: {e}")
        result["steps"].append({"step": "analyze", "status": "error"})

    # Step 3: 关键词匹配 + 聚合 (Phase 48/49)
    try:
        await _cb("match", "关键词匹配+聚合...")
        from scripts.build_news_signals import build_news_signals
        sig_result = await build_news_signals()
        result["steps"].append({"step": "match",
            "signals": sig_result.get("matched", 0),
            "aggregated": sig_result.get("aggregated", 0)})
    except Exception as e:
        logger.warning(f"News matching failed: {e}")
        result["steps"].append({"step": "match", "status": "error"})

    # Step 4: 验证 (Phase 50, 仅在已有足够 T+2 数据时有效)
    try:
        await _cb("verify", "验证信号命中率...")
        from scripts.verify_news_signals import verify_news_signals
        verify_result = await verify_news_signals(lookback_days=30, min_age_days=5)
        result["steps"].append({"step": "verify",
            "active": verify_result.get("activated", 0),
            "total": verify_result.get("upserted", 0)})
    except Exception as e:
        logger.warning(f"News verify failed: {e}")
        result["steps"].append({"step": "verify", "status": "error"})

    return result
