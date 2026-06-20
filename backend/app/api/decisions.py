"""用户决策 API — 记录买入/观察/放弃."""
from datetime import date as date_type
from fastapi import APIRouter
from pydantic import BaseModel
from app.schemas.decisions import DecisionRequest
from sqlalchemy import text
from app.core.database import async_session_factory

router = APIRouter(prefix="/user-decisions", tags=["decisions"])


@router.post("")
async def record_decision(req: DecisionRequest):
    if req.action not in ("buy", "watch", "pass"):
        return {"status": "error", "detail": "action must be buy/watch/pass"}
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO user_decisions (symbol, action, decision_date, decision_reason, source_prompt, feedback_id)
            VALUES (:sym, :act, CURRENT_DATE, :reason, :prompt, CAST(:fid AS uuid))
        """), {
            "sym": req.symbol, "act": req.action,
            "reason": req.decision_reason[:2000], "prompt": req.source_prompt[:10000],
            "fid": req.feedback_id,
        })
        await s.commit()
    return {"status": "success", "message": f"已记录: {req.symbol} → {req.action}"}


@router.get("/list")
async def list_decisions(action: str | None = None, limit: int = 20):
    async with async_session_factory() as s:
        where = ""
        params = {"lim": limit}
        if action and action in ("buy", "watch", "pass"):
            where = "WHERE action = :act"
            params["act"] = action
        r = await s.execute(text(f"""
            SELECT symbol, action, decision_date, decision_reason, feedback_id, created_at
            FROM user_decisions {where}
            ORDER BY created_at DESC LIMIT :lim
        """), params)
        data = [{"symbol": row[0], "action": row[1], "decision_date": str(row[2]),
                 "decision_reason": row[3], "feedback_id": str(row[4]) if row[4] else None,
                 "created_at": str(row[5])}
                for row in r.fetchall()]
    return {"status": "success", "data": data, "count": len(data)}


@router.get("/stats")
async def decision_stats():
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT action, COUNT(*) FROM user_decisions
            WHERE decision_date >= CURRENT_DATE - 30
            GROUP BY action
        """))
        stats = {row[0]: row[1] for row in r.fetchall()}
    return {"status": "success", "data": stats}
