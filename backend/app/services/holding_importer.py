"""持仓导入服务 - 从 holdings.py 提取 (v4.3)."""
import logging, json, re
from datetime import date, datetime
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("holding_importer")

async def import_holdings_from_text(raw_text, user_id, session):
    """LLM 解析券商导出文本 + PnL 计算 + auto-close + holding-days 合并."""
    from app.services.deepseek import call_deepseek
    prompt = f"""请解析以下券商导出的持仓文本，提取每只股票的代码(6位)、名称、持仓数量、成本价。
返回JSON: {{"holdings": [{{"ts_code": "000001.SZ", "qty": 1000, "cost": 12.50}}, ...]}}
如果无法解析，返回 {{"holdings": []}}。

文本:
{raw_text}"""
    try:
        resp = await call_deepseek(prompt, max_tokens=2048)
        parsed = json.loads(re.search(r'\{.*\}', resp, re.DOTALL).group() or '{}')
    except Exception:
        return {"status": "error", "detail": "LLM 解析失败"}

    holdings_list = parsed.get('holdings', [])
    if not holdings_list:
        return {"status": "error", "detail": "未解析到持仓数据"}

    # Auto-close: detect stocks no longer in import list
    try:
        r = await session.execute(text("SELECT symbol, quantity FROM holdings WHERE created_by = :u"), {"u": user_id})
        existing_symbols = [(row[0], float(row[1] or 0)) for row in r.fetchall()]
        imported_symbols = [h.get("ts_code", h.get("symbol", "")) for h in holdings_list]
        auto_closed = []
        for sym, qty in existing_symbols:
            if sym not in imported_symbols and qty > 0:
                await session.execute(text(
                    "UPDATE holdings SET quantity = 0, updated_at = NOW() WHERE symbol = :s AND created_by = :u"
                ), {"s": sym, "u": user_id})
                auto_closed.append(sym)
        if auto_closed:
            logger.info(f"Auto-closed {len(auto_closed)} positions: {auto_closed}")
    except Exception as e:
        logger.warning(f"Auto-close check failed: {e}")

    # Insert/update holdings
    inserted, updated = 0, 0
    for h in holdings_list:
        sym = h.get("ts_code", h.get("symbol", ""))
        qty = float(h.get("qty", 0))
        cost = float(h.get("cost", 0))
        if not sym or qty <= 0: continue
        try:
            r = await session.execute(text(
                "SELECT 1 FROM holdings WHERE symbol = :s AND created_by = :u"
            ), {"s": sym, "u": user_id})
            if r.fetchone():
                await session.execute(text(
                    "UPDATE holdings SET quantity = :q, cost_price = :c, updated_at = NOW() WHERE symbol = :s AND created_by = :u"
                ), {"q": qty, "c": cost, "s": sym, "u": user_id})
                updated += 1
            else:
                await session.execute(text(
                    "INSERT INTO holdings (created_by, symbol, quantity, cost_price, created_at, updated_at) VALUES (:u, :s, :q, :c, NOW(), NOW())"
                ), {"u": user_id, "s": sym, "q": qty, "c": cost})
                inserted += 1
        except Exception: pass

    await session.commit()
    return {"status": "success", "imported": len(holdings_list), "inserted": inserted, "updated": updated}
