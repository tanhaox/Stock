"""LLM 深度分析 API — 提示词生成 + 缓存 + 候选股列表."""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.schemas.llm import PromptRequest, RetryRequest
from sqlalchemy import text
from app.core.config import settings
from app.core.database import async_session_factory

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/candidates")
async def get_llm_candidates(limit: int = Query(default=15, le=30)):
    from app.services.llm_deep_analyzer import get_candidates_for_llm
    data = await get_candidates_for_llm(limit)
    return {"status": "success", "data": data, "count": len(data)}


@router.post("/generate-prompt")
async def generate_prompts(req: PromptRequest):
    """生成提示词并缓存到数据库(刷新不丢失)."""
    if len(req.symbols) > 20:
        req.symbols = req.symbols[:20]
    from app.services.llm_deep_analyzer import generate_prompts
    results = await generate_prompts(req.symbols)

    # 缓存到数据库(含上下文)
    from datetime import date
    import json
    async with async_session_factory() as s:
        for r in results:
            ctx = {"name": r["name"], "composite_score": r["context"].get("composite_score"),
                   "archetype": r["context"].get("archetype"), "level": r["context"].get("level"),
                   "tg_momentum": r["context"].get("tg_momentum")}
            await s.execute(text("""
                INSERT INTO prompt_cache (symbol, prompt_date, prompt_text, context_json)
                VALUES (:sym, :d, :txt, CAST(:ctx AS jsonb))
                ON CONFLICT (symbol, prompt_date) DO UPDATE SET prompt_text=:txt, context_json=CAST(:ctx AS jsonb)
            """), {"sym": r["symbol"], "d": date.today(), "txt": r["prompt"], "ctx": json.dumps(ctx)})
        await s.commit()

    return {"status": "success", "data": results, "count": len(results)}


@router.get("/prompts")
async def get_cached_prompts(symbols: str = ""):
    """读取已缓存的提示词(含上下文，刷新不丢失)."""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"status": "success", "data": [], "count": 0}
    from datetime import date
    import json
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT symbol, prompt_text, context_json FROM prompt_cache WHERE symbol = ANY(:syms) AND prompt_date = :d"
        ), {"syms": syms, "d": date.today()})
        rows = {row[0]: (row[1], row[2]) for row in r.fetchall()}
        # 补充名称
        r2 = await s.execute(text(
            "SELECT DISTINCT ON (symbol) symbol, name FROM scan_results WHERE symbol = ANY(:syms) ORDER BY symbol, scan_date DESC"
        ), {"syms": syms})
        names = {row[0]: row[1] for row in r2.fetchall()}
    data = []
    for sym in syms:
        if sym in rows:
            ctx = json.loads(rows[sym][1]) if rows[sym][1] else {}
            name = ctx.get("name", "")
            if not name or name == sym:
                # 缓存无名称时从 scan_results 补充
                name = names.get(sym, sym)
            data.append({"symbol": sym, "prompt": rows[sym][0], "name": name, "context": ctx})
    return {"status": "success", "data": data, "count": len(data)}


@router.post("/auto-analyze")
async def auto_analyze_stocks(req: PromptRequest):
    """一键自动分析: 生成提示词 → DeepSeek API → 解析信号 → 存储反哺 → 批量对比.

    SSE 流式返回: 每完成一只股票推送进度事件, 前端实时显示 X/20 已接收.
    """
    import asyncio
    import logging
    import json
    from datetime import date as dt_date
    from fastapi.responses import StreamingResponse
    from app.services.llm_deep_analyzer import generate_prompts, process_and_store_deepseek_response
    from app.services.deepseek import call_deepseek

    log = logging.getLogger("llm_auto")

    if not req.symbols:
        return {"status": "error", "detail": "无股票"}
    symbols = req.symbols[:20]
    today = dt_date.today()
    total = len(symbols)

    # Step 1: 批量生成提示词
    prompts_data = await generate_prompts(symbols)
    log.info(f"Auto-analyze: {len(prompts_data)} prompts generated")

    sem = asyncio.Semaphore(5)  # 5并发稳定, 避免DeepSeek速率限制导致集体超时

    async def _analyze_one(pd: dict) -> dict:
        sym = pd["symbol"]
        name = pd["name"]
        try:
            async with sem:
                log.info(f"Auto-analyze [{sym}]: calling DeepSeek API...")
                raw = await call_deepseek(pd["prompt"], max_tokens=8192, model=settings.DEEPSEEK_PRO_MODEL)
            if raw.startswith("[LLM"):
                return {"symbol": sym, "name": name, "status": "error", "error": raw}
            stored = await process_and_store_deepseek_response(sym, today, raw, "auto-analyze")
            log.info(f"Auto-analyze [{sym}]: stored {stored.get('positive',0)}pos + {stored.get('negative',0)}neg")
            return {
                "symbol": sym, "name": name, "status": "success",
                "positive_signals": stored.get("positive_signals", []),
                "negative_signals": stored.get("negative_signals", []),
            }
        except Exception as e:
            log.warning(f"Auto-analyze [{sym}] failed: {e}")
            return {"symbol": sym, "name": name, "status": "error", "error": str(e)}

    # ── SSE 流式返回 ──
    async def event_stream():
        completed = 0
        results = []

        # 并发执行但逐个 yield 结果
        tasks = [asyncio.create_task(_analyze_one(p)) for p in prompts_data]

        for task in asyncio.as_completed(tasks):
            r = await task
            results.append(r)
            completed += 1
            yield f"data: {json.dumps({'type': 'progress', 'completed': completed, 'total': total, 'result': r}, ensure_ascii=False)}\n\n"

        # Step 3: 批量横向对比
        batch_scores = {}
        success_symbols = [r["symbol"] for r in results if r["status"] == "success"]
        if len(success_symbols) >= 2:
            try:
                from app.api.feedback import batch_score_stocks, BatchScoreRequest
                symbol_texts = {}
                for r in results:
                    if r["status"] == "success":
                        pos_desc = "; ".join(s.get("description","")[:80] for s in r.get("positive_signals",[]))
                        neg_desc = "; ".join(s.get("description","")[:80] for s in r.get("negative_signals",[]))
                        symbol_texts[r["symbol"]] = f"正面: {pos_desc}\n负面: {neg_desc}"
                fake_req = BatchScoreRequest(symbol_texts=symbol_texts)
                batch_result = await batch_score_stocks(fake_req)
                if batch_result.get("status") == "success":
                    batch_scores = batch_result.get("scores", {})
            except Exception as e:
                log.warning(f"Batch comparison failed: {e}")

        n_success = len(success_symbols)
        yield f"data: {json.dumps({'type': 'done', 'individual': results, 'batch_scores': batch_scores, 'summary': f'{n_success}/{total} stocks analyzed'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/retry-one")
async def retry_one_stock(req: RetryRequest):
    """重试单只股票的 LLM 分析 — 不依赖 prompt_cache, 实时生成提示词.

    用于批量分析中个别失败时的快速重试。
    返回与 auto-analyze 单只相同格式的 result。
    """
    import logging
    from datetime import date as dt_date
    from app.services.llm_deep_analyzer import generate_prompts, process_and_store_deepseek_response
    from app.services.deepseek import call_deepseek

    symbol = req.symbol.strip()
    if not symbol:
        return {"status": "error", "detail": "符号为空"}

    log = logging.getLogger("llm_retry")

    # 生成提示词
    prompts_data = await generate_prompts([symbol])
    if not prompts_data:
        return {"status": "error", "detail": "无法生成提示词"}

    pd = prompts_data[0]
    name = pd["name"]

    try:
        log.info(f"Retry [{symbol}]: calling DeepSeek...")
        raw = await call_deepseek(pd["prompt"], max_tokens=8192, model=settings.DEEPSEEK_MODEL)
    except Exception as e:
        log.warning(f"Retry [{symbol}] API call failed: {e}")
        return {"status": "error", "symbol": symbol, "name": name, "error": f"API调用失败: {e}"}

    if raw.startswith("[LLM"):
        return {"status": "error", "symbol": symbol, "name": name, "error": raw}

    try:
        stored = await process_and_store_deepseek_response(symbol, dt_date.today(), raw, "auto-analyze")
        log.info(f"Retry [{symbol}]: {stored.get('positive',0)}pos + {stored.get('negative',0)}neg")
        return {
            "status": "success",
            "symbol": symbol,
            "name": name,
            "positive_signals": stored.get("positive_signals", []),
            "negative_signals": stored.get("negative_signals", []),
        }
    except Exception as e:
        log.warning(f"Retry [{symbol}] parse failed: {e}")
        return {"status": "error", "symbol": symbol, "name": name, "error": f"解析失败: {e}"}
