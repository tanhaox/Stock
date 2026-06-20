"""个股历史深度复盘 API (v4.3).

提供两个端点:
  POST /api/drill/analyze — 批量复盘精选股票
  GET  /api/drill/report/{symbol} — 单股复盘报告
"""
from datetime import date
from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.schemas.drill import DrillRequest

router = APIRouter(prefix="/drill", tags=["drill"])


@router.post("/analyze")
async def analyze_stocks(req: DrillRequest):
    """对精选股票列表执行历史深度复盘.

    输入: { "symbols": ["000001.SZ", ...], "force_refresh": false }
    返回: {symbol: {signal_effectiveness, pattern_matching, ...}}
    """
    if not req.symbols:
        return {"status": "error", "detail": "symbols 不能为空"}
    if len(req.symbols) > 50:
        return {"status": "error", "detail": f"最多50只股票, 收到 {len(req.symbols)}"}

    from app.services.stock_historical_drill import drill_stocks
    from app.services.market_gate import get_market_state

    # 获取当前市场体制
    market_regime = "unknown"
    try:
        ms = await get_market_state()
        market_regime = ms.get("regime", "unknown")
    except Exception:
        pass

    results = await drill_stocks(
        symbols=req.symbols,
        current_date=date.today(),
        market_regime=market_regime,
        force_refresh=req.force_refresh,
    )

    count_ok = sum(1 for r in results.values() if r.get("status") == "ok")
    count_insufficient = sum(1 for r in results.values() if r.get("status") == "insufficient_data")
    count_error = sum(1 for r in results.values() if r.get("status") == "error")

    return {
        "status": "success",
        "data": results,
        "summary": {
            "total": len(results), "ok": count_ok,
            "insufficient": count_insufficient, "error": count_error,
        },
    }


@router.get("/report/{symbol}")
async def get_drill_report(
    symbol: str,
    force_refresh: bool = Query(False),
):
    """获取单只股票的最新深度复盘报告."""
    from app.services.stock_historical_drill import drill_stocks
    from app.services.market_gate import get_market_state

    market_regime = "unknown"
    try:
        ms = await get_market_state()
        market_regime = ms.get("regime", "unknown")
    except Exception:
        pass

    results = await drill_stocks(
        symbols=[symbol],
        current_date=date.today(),
        market_regime=market_regime,
        force_refresh=force_refresh,
    )

    if symbol not in results:
        return {"status": "error", "detail": f"复盘失败: {symbol}"}

    return {"status": "success", "data": results[symbol]}


@router.get("/micro-behavior/active-signals")
async def get_active_behavior_signals(
    symbols: str = Query("", description="逗号分隔的股票列表, 空则取当日推荐"),
):
    """★ v4.3: 扫描股票，返回当前满足历史操盘触发条件的股票列表。

    若未提供 symbols，自动从最新 analysis_scores 取 Top 20。
    """
    from app.services.micro_behavior_analyzer import scan_active_signals
    from sqlalchemy import text
    from app.core.database import async_session_factory as _asf

    syms = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else []

    if not syms:
        async with _asf() as s:
            r = await s.execute(text("""
                SELECT symbol FROM analysis_scores
                WHERE scan_date = (SELECT MAX(scan_date) FROM analysis_scores)
                ORDER BY composite_score DESC LIMIT 20
            """))
            syms = [row[0] for row in r.fetchall()]

    if not syms:
        return {"status": "error", "detail": "无可用股票"}

    results = await scan_active_signals(syms)
    return {"status": "success", "data": results, "count": len(results)}
