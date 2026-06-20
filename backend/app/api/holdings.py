"""持仓管理 API v2.0 — 完整资产管理.

v2.0 (2026-06-01):
  - 导入=替换: 未再出现的股票自动清仓, 记录盈亏到 closed_positions
  - 资本账户: 初始本金 + 入金/出金记录
  - 汇总: 本金 + 入金 - 出金 + 清仓盈亏 + 持仓浮盈 = 账户净值
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from app.schemas.holdings import HoldingAdd, HoldingImport, HoldingAnalyze, CapitalOp, CloseRequest
from sqlalchemy import text
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.auth import get_current_user
from datetime import date as dt_date

router = APIRouter(prefix="/holdings", tags=["holdings"])

# ═══════════════════════════════════════════════════════════
# 用户ID转换
# ═══════════════════════════════════════════════════════════

def _to_uuid(user_id: str) -> str:
    """web-{uuid} → uuid, 其他 → MD5 hash 稳定 UUID."""
    import hashlib
    import uuid as _uuid
    if user_id.startswith('web-'):
        inner = user_id[4:]
        try: _uuid.UUID(inner); return inner
        except ValueError: pass
    try: _uuid.UUID(user_id); return user_id
    except ValueError: pass
    return str(_uuid.UUID(hashlib.md5(user_id.encode()).hexdigest()))


async def _ensure_user(uid: str):
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO users (id, username, display_name, hashed_password, is_active, plan_type, created_at, updated_at)
            VALUES (:uid, :un, 'Web User', '', true, 'free', NOW(), NOW())
            ON CONFLICT (id) DO NOTHING
        """), {"uid": uid, "un": f"web_{uid[:8]}"})
        await s.commit()



# ═══════════════════════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════════════════════

@router.post("")
async def add_holding(req: HoldingAdd, user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    await _ensure_user(uid)
    price = req.current_price or req.cost_price
    mv = req.quantity * price
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO holdings (id, created_by, symbol, name, quantity, available, frozen,
                cost_price, current_price, floating_pnl, pnl_pct, daily_pnl, market_value,
                weight_pct, market, holding_days)
            VALUES (gen_random_uuid(), :uid, :sym, :name, :qty, :qty, 0,
                :cost, :price, 0, 0, 0, :mv, 0, :mkt, 0)
            ON CONFLICT (symbol, created_by) DO UPDATE SET
                name=EXCLUDED.name, quantity=EXCLUDED.quantity,
                cost_price=EXCLUDED.cost_price, current_price=EXCLUDED.current_price,
                market_value=EXCLUDED.market_value, updated_at=NOW()
        """), {
            "uid": uid, "sym": req.symbol, "name": req.name or req.symbol,
            "qty": req.quantity, "cost": req.cost_price, "price": price,
            "mv": mv, "mkt": "上海A股" if req.symbol.endswith(".SH") else "深圳A股",
        })
        await s.commit()
    return {"status": "success", "message": f"已添加 {req.symbol}"}


@router.put("/{symbol}")
async def update_price(symbol: str, current_price: float, user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT quantity, cost_price FROM holdings WHERE symbol=:s AND created_by = :uid"
        ), {"s": symbol, "uid": uid})
        row = r.fetchone()
        if not row: return {"status": "error", "detail": "未找到该持仓"}
        qty = float(row[0] or 0); cost = float(row[1] or 0)
        mv = qty * current_price
        pnl = round((current_price - cost) * qty, 2) if cost > 0 else 0
        pnl_pct = round((current_price - cost) / cost * 100, 2) if cost > 0 else 0
        await s.execute(text("""
            UPDATE holdings SET current_price=:p, market_value=:mv, floating_pnl=:pnl, pnl_pct=:pct, updated_at=NOW()
            WHERE symbol=:s
        """), {"p": current_price, "mv": mv, "pnl": pnl, "pct": pnl_pct, "s": symbol})
        await s.commit()
    return {"status": "success", "message": f"已更新 {symbol} 现价至 {current_price}"}


@router.post("/{symbol}/close")
async def close_position(symbol: str, req: CloseRequest, user_id: str = Depends(get_current_user)):
    """手动清仓: 用户输入卖出价, 记录盈亏到 closed_positions, 从 holdings 删除."""
    uid = _to_uuid(user_id)
    sell_price = req.sell_price

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT name, quantity, cost_price, current_price, holding_days FROM holdings WHERE symbol=:s AND created_by = :uid"
        ), {"s": symbol, "uid": uid})
        row = r.fetchone()
        if not row:
            return {"status": "error", "detail": f"未找到持仓: {symbol}"}

        name, qty, cost, cur_price, days = row[0], row[1], float(row[2] or 0), float(row[3] or 0), row[4] or 0
        realized = round((sell_price - cost) * qty, 2)
        realized_pct = round((sell_price - cost) / cost * 100, 2) if cost > 0 else 0

        # 写入清仓记录
        await s.execute(text("""
            INSERT INTO closed_positions
            (id, created_by, symbol, name, quantity, buy_price, sell_price,
             holding_pnl, pnl_pct, holding_days, buy_reason, buy_date, close_date,
             t_trade_count, updated_at)
            VALUES (gen_random_uuid(), :uid, :sym, :name, :qty, :buy, :sell,
                    :pnl, :pct, :days, 'manual_close', CURRENT_DATE, CURRENT_DATE,
                    0, NOW())
        """), {
            "uid": uid, "sym": symbol, "name": name, "qty": qty,
            "buy": cost, "sell": sell_price, "pnl": realized, "pct": realized_pct, "days": days,
        })
        # 删除持仓
        await s.execute(text("DELETE FROM holdings WHERE symbol = :s AND created_by = :uid"), {"s": symbol, "uid": uid})
        await s.commit()

    return {
        "status": "success",
        "message": f"已清仓 {symbol}",
        "data": {
            "symbol": symbol, "name": name, "quantity": qty,
            "buy_price": round(cost, 2), "sell_price": round(sell_price, 2),
            "realized_pnl": round(realized, 2), "pnl_pct": round(realized_pct, 2),
        },
    }


@router.get("")
async def list_holdings(user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT h.symbol, h.name, h.quantity, h.cost_price, h.current_price,
                   h.floating_pnl, h.pnl_pct, h.market_value, h.weight_pct,
                   h.holding_days, COALESCE(h.pending_close, false) as pending_close,
                   a.composite_score, a.details, a.archetype,
                   (SELECT COUNT(*) FROM recommendation_tracking rt
                    WHERE rt.symbol = h.symbol AND was_profitable_2d = true
                      AND rt.scan_date >= CURRENT_DATE - 30) as recent_wins
            FROM holdings h
            LEFT JOIN LATERAL (
                SELECT composite_score, details, archetype
                FROM analysis_scores
                WHERE symbol = h.symbol
                ORDER BY scan_date DESC LIMIT 1
            ) a ON true
            WHERE h.created_by = :uid
            ORDER BY h.pending_close ASC, h.market_value DESC
        """), {"uid": uid})
        import json
        data = []
        for row in r.fetchall():
            raw_details = row[12] if len(row) > 12 and row[12] else {}
            if isinstance(raw_details, str):
                try: raw_details = json.loads(raw_details)
                except Exception: raw_details = {}
            details = raw_details if isinstance(raw_details, dict) else {}

            d = {
                "symbol": row[0], "name": row[1], "quantity": row[2],
                "cost_price": float(row[3] or 0), "current_price": float(row[4] or 0),
                "floating_pnl": float(row[5] or 0), "pnl_pct": float(row[6] or 0),
                "market_value": float(row[7] or 0), "weight_pct": float(row[8] or 0),
                "holding_days": row[9],
                "pending_close": bool(row[10]) if len(row) > 10 else False,
                "rec_index": float(row[11]) if len(row) > 11 and row[11] is not None else None,
                "archetype": row[13] if len(row) > 13 and row[13] else None,
                "recent_wins": int(row[14]) if len(row) > 14 and row[14] is not None else 0,
                "news_signal": details.get("news_signal"),
            }
            data.append(d)
    return {"status": "success", "data": data}


@router.get("/summary")
async def holdings_summary(user_id: str = Depends(get_current_user)):
    """汇总: 仅当前用户持仓."""
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT COUNT(*), COALESCE(SUM(market_value),0), COALESCE(SUM(floating_pnl),0),
                   COALESCE(AVG(pnl_pct),0)
            FROM holdings WHERE created_by = :uid
        """), {"uid": uid})
        row = r.fetchone()
    return {"status": "success", "data": {
        "count": row[0], "total_value": float(row[1]),
        "total_pnl": float(row[2]), "avg_pnl_pct": float(row[3]),
    }}


@router.get("/alerts")
async def holdings_alerts(user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol,name,floating_pnl,pnl_pct FROM holdings
            WHERE created_by = :uid AND pnl_pct < -5 ORDER BY pnl_pct ASC
        """), {"uid": uid})
        data = [{"symbol": row[0], "name": row[1], "pnl": float(row[2] or 0),
                 "pnl_pct": float(row[3] or 0)} for row in r.fetchall()]
    return {"status": "success", "data": data}


# ═══════════════════════════════════════════════════════════
# 资本账户
# ═══════════════════════════════════════════════════════════

@router.get("/capital")
async def get_capital(user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT type, amount, note, created_at FROM capital_account
            WHERE created_by = :uid ORDER BY created_at
        """), {"uid": uid})
        records = [{"type": row[0], "amount": float(row[1] or 0),
                     "note": row[2], "date": str(row[3])} for row in r.fetchall()]
        total_in = sum(rec["amount"] for rec in records if rec["amount"] > 0)
        total_out = sum(abs(rec["amount"]) for rec in records if rec["amount"] < 0)
    return {
        "status": "success",
        "data": {"records": records, "total_invested": round(total_in, 2),
                 "total_withdrawn": round(total_out, 2),
                 "net_capital": round(total_in - total_out, 2)},
    }


@router.post("/capital")
async def set_capital(req: CapitalOp, user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    await _ensure_user(uid)
    op_type = "deposit" if req.amount > 0 else "withdraw"
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO capital_account (created_by, type, amount, note)
            VALUES (:uid, :tp, :amt, :note)
        """), {"uid": uid, "tp": op_type, "amt": round(req.amount, 2), "note": req.note})
        await s.commit()
    return {"status": "success", "message": f"{'入金' if req.amount>0 else '出金'} ¥{abs(req.amount):.2f}"}


# ═══════════════════════════════════════════════════════════
# 清仓历史
# ═══════════════════════════════════════════════════════════

@router.get("/closed")
async def list_closed(user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol, name, quantity, buy_price, sell_price,
                   holding_pnl, pnl_pct, holding_days, buy_reason, close_date
            FROM closed_positions WHERE created_by = :uid
            ORDER BY close_date DESC
        """), {"uid": uid})
        data = [{
            "symbol": row[0], "name": row[1], "quantity": row[2],
            "buy_price": float(row[3] or 0), "sell_price": float(row[4] or 0),
            "realized_pnl": float(row[5] or 0), "pnl_pct": float(row[6] or 0),
            "holding_days": row[7], "buy_reason": row[8], "close_date": str(row[9]),
        } for row in r.fetchall()]
        total_realized = sum(d["realized_pnl"] for d in data)
    return {"status": "success", "data": data, "total_realized_pnl": round(total_realized, 2),
            "count": len(data)}


# ═══════════════════════════════════════════════════════════
# 完整账户视图
# ═══════════════════════════════════════════════════════════

@router.get("/account")
async def full_account(user_id: str = Depends(get_current_user)):
    """完整账户: 本金 + 入金 - 出金 + 清仓盈亏 + 持仓浮盈 = 净值."""
    uid = _to_uuid(user_id)

    async with async_session_factory() as s:
        # 资本
        r = await s.execute(text("""
            SELECT COALESCE(SUM(amount), 0) FROM capital_account
            WHERE created_by = :uid
        """), {"uid": uid})
        net_capital = float(r.scalar() or 0)

        # 持仓
        r = await s.execute(text("""
            SELECT COUNT(*), COALESCE(SUM(market_value),0), COALESCE(SUM(floating_pnl),0)
            FROM holdings WHERE created_by = :uid
        """), {"uid": uid})
        h_row = r.fetchone()
        h_count = h_row[0] or 0
        h_value = float(h_row[1] or 0)
        h_pnl = float(h_row[2] or 0)  # 浮盈

        # 清仓
        r = await s.execute(text("""
            SELECT COALESCE(SUM(holding_pnl), 0), COUNT(*) FROM closed_positions
            WHERE created_by = :uid
        """), {"uid": uid})
        cp_row = r.fetchone()
        closed_pnl = float(cp_row[0] or 0)
        closed_count = cp_row[1] or 0

    # 账户净值 = 本金 + 已实现盈亏 + 未实现盈亏
    net_value = net_capital + closed_pnl + h_pnl
    # 可用现金 = 本金 - 持仓投入 + 已实现盈亏
    cash_remaining = net_capital + closed_pnl

    return {
        "status": "success",
        "data": {
            "net_capital": round(net_capital, 2),
            "cash_remaining": round(cash_remaining, 2),
            "holdings_count": h_count,
            "holdings_value": round(h_value, 2),
            "holdings_unrealized_pnl": round(h_pnl, 2),
            "closed_count": closed_count,
            "closed_realized_pnl": round(closed_pnl, 2),
            "net_account_value": round(net_value, 2),
            "total_return_pct": round((net_value - net_capital) / max(abs(net_capital), 1) * 100, 1) if net_capital > 0 else 0,
        },
    }


# ═══════════════════════════════════════════════════════════
# 导入持仓 — v2.0: 替换式导入, 自动清仓
# ═══════════════════════════════════════════════════════════

@router.post("/import")
async def import_holdings(req: HoldingImport, user_id: str = Depends(get_current_user)):
    """Import holdings - delegated to holding_importer (v4.3)."""
    async with async_session_factory() as session:
        from app.services.holding_importer import import_holdings_from_text
        result = await import_holdings_from_text(req.raw_text, user_id, session)
        return result


# ═══════════════════════════════════════════════════════════
# 持仓分析 (保留)
# ═══════════════════════════════════════════════════════════

@router.post("/analyze")
async def analyze_holding(req: HoldingAnalyze):
    import json
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT symbol, name, quantity, cost_price, current_price, floating_pnl, pnl_pct, market_value, holding_days FROM holdings WHERE symbol=:s"
        ), {"s": req.symbol})
        row = r.fetchone()
        if not row: return {"status": "error", "detail": "未找到该持仓"}
        holding = {
            "symbol": row[0], "name": row[1], "quantity": row[2],
            "cost_price": float(row[3] or 0), "current_price": float(row[4] or 0),
            "floating_pnl": float(row[5] or 0), "pnl_pct": float(row[6] or 0),
            "market_value": float(row[7] or 0), "holding_days": row[8],
        }
        r = await s.execute(text(
            "SELECT a.composite_score, a.tech_score, a.kline_score, a.fund_score, a.fundamental_adjustment, a.archetype, s.level "
            "FROM analysis_scores a LEFT JOIN scan_results s ON a.symbol=s.symbol AND a.scan_date=s.scan_date "
            "WHERE a.symbol=:s AND a.scan_date=(SELECT MAX(scan_date) FROM analysis_scores)"
        ), {"s": req.symbol})
        score_row = r.fetchone()
        scoring = {}
        if score_row:
            scoring = {"composite_score": float(score_row[0] or 0), "tech_score": float(score_row[1] or 0),
                       "kline_score": float(score_row[2] or 0), "fund_score": float(score_row[3] or 0),
                       "fundamental_adjustment": float(score_row[4] or 0), "archetype": score_row[5] or "?", "level": score_row[6] or "?"}

    prompt = f"""你是一位拥有20年A股实战经验的资深股票交易员。请根据以下信息，给出该持仓的详细后期操作策略。

【持仓数据】
股票: {holding['symbol']} {holding['name']}
持有数量: {holding['quantity']}股
成本价: {holding['cost_price']:.2f}元
现价: {holding['current_price']:.2f}元
浮动盈亏: {holding['floating_pnl']:.0f}元 ({holding['pnl_pct']:.1f}%)
市值: {holding['market_value']:.0f}元
持有天数: {holding['holding_days']}天

【系统27维评分】
{json.dumps(scoring, ensure_ascii=False) if scoring else '暂无评分数据'}

【DeepSeek深度分析】
{req.raw_text[:6000]}

请从实战交易角度，给出以下三个策略(假设资金无限，不考虑仓位限制)：
1. 止损/割肉策略 2. T+0/做T策略 3. 止盈/持有策略
请用结构化格式输出，每个策略给出明确的价位和操作逻辑。"""

    from app.services.deepseek import call_deepseek
    result = await call_deepseek(prompt, max_tokens=8192, model=settings.DEEPSEEK_MODEL)
    return {"status": "success", "strategy": result, "holding": holding, "scoring": scoring}


@router.post("/analyze/summary")
async def summarize_strategy(req: HoldingAnalyze):
    prompt = f"""请将以下股票交易策略提炼为两行关键点位，不要标题，不要代码，格式严格如下：
止损: (价位+理由) | 支撑: (价位区间) | 压力: (价位区间)
建议: (一句话操作建议)
策略原文:
{req.raw_text[:4000]}"""
    from app.services.deepseek import call_deepseek
    result = await call_deepseek(prompt, max_tokens=4096, model=settings.DEEPSEEK_MODEL)
    return {"status": "success", "summary": result}


@router.get("/big-fairy")
async def holdings_big_fairy(user_id: str = Depends(get_current_user)):
    """获取所有持仓的大神仙空头信号 — 批量计算."""
    from app.services.big_fairy import compute_big_fairy
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT symbol FROM holdings WHERE created_by = :uid"
        ), {"uid": uid})
        symbols = [row[0] for row in r.fetchall()]
    if not symbols:
        return {"status": "success", "data": {}}
    # Batch compute using single session for efficiency
    results = {}
    async with async_session_factory() as bf_s:
        for sym in symbols[:30]:  # Cap at 30 holdings
            try:
                bf = await compute_big_fairy(sym, session=bf_s)
                if bf:
                    results[sym] = {
                        "score": bf["score"],
                        "signal": bf["signal"],
                        "bearish": bf["bearish"],
                        "dimensions": bf["dimensions"],
                        "rsi14": bf.get("rsi14"),
                        "j": bf.get("j"),
                    }
            except Exception:
                pass
    return {"status": "success", "data": results}


# ═══════════════════════════════════════════════════════════
# 日内异动 & 退出信号 & T+0
# ═══════════════════════════════════════════════════════════

@router.get("/intraday/{symbol}")
async def get_intraday_analysis(symbol: str):
    from app.services.intraday_analyzer import analyze_intraday_move
    from datetime import date, timedelta

    # 更新全部持仓股价
    from app.services.realtime_quote import get_batch_realtime_quotes
    async with async_session_factory() as s:
        holdings = await s.execute(text("SELECT symbol, quantity FROM holdings"))
        rows = holdings.fetchall()
        quotes = await get_batch_realtime_quotes([h[0] for h in rows])
        for h in rows:
            sym, qty = h[0], h[1]
            q = quotes.get(sym, {})
            price = q.get("price")
            if price and price > 0:
                await s.execute(text(
                    "UPDATE holdings SET current_price=:p,market_value=:mv,updated_at=NOW() WHERE symbol=:s"
                ), {"p": price, "mv": qty * price, "s": sym})
        await s.commit()

    result = await analyze_intraday_move(symbol)
    if result is not None: return {"status": "success", "data": result}

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT open, high, low, close FROM daily_kline WHERE ts_code=:s ORDER BY trade_date DESC LIMIT 1"
        ), {"s": symbol})
        row = r.fetchone()
        if row:
            o, h, l, c = float(row[0] or 0), float(row[1] or 0), float(row[2] or 0), float(row[3] or 0)
            if o > 0:
                gain = (c - o) / o * 100
                retrace = (h - c) / max(h - o, 0.01) if h > o else 0
                return {"status": "success", "data": {
                    "max_gain_pct": round((h - o) / o * 100, 1), "day_gain_pct": round(gain, 1),
                    "retrace_ratio": round(retrace, 2), "volume_profile": "日线(无分钟数据)",
                    "big_order_bias": "无分钟数据",
                    "verdict": "涨幅大+回撤小(偏强)" if gain > 2 and retrace < 0.5 else "正常波动",
                    "peak_price": round(h, 2), "current_price": round(c, 2),
                }}
    return {"status": "error", "detail": "暂无K线数据"}


@router.get("/exit-signals")
async def get_exit_signals(symbol: str = "", lookback: int = 10):
    from datetime import date as dt_date, timedelta
    from app.services.exit_signal_detector import detect_exit_signals, get_recommendation_exit_signals
    if symbol:
        entry_date = (dt_date.today() - timedelta(days=lookback)).isoformat()
        signals = await detect_exit_signals(symbol, entry_date)
        return {"status": "success", "data": {"symbol": symbol, "signals": signals}}
    results = await get_recommendation_exit_signals(lookback)
    return {"status": "success", "data": results, "count": len(results)}


@router.post("/exit-signals/batch")
async def batch_exit_signals(symbols: list[str]):
    from datetime import date as dt_date, timedelta
    today = dt_date.today()
    from app.services.exit_signal_detector import detect_exit_signals
    results = {}
    for sym in symbols:
        entry = (today - timedelta(days=10)).isoformat()
        sigs = await detect_exit_signals(sym, entry)
        if sigs: results[sym] = sigs
    return {"status": "success", "data": results, "count": len(results)}


# ═══════════════════════════════════════════════════════════
# P3: 持仓策略自动生成 + 板块集中度
# ═══════════════════════════════════════════════════════════

@router.get("/auto-strategy")
async def auto_holding_strategy(user_id: str = Depends(get_current_user)):
    uid = _to_uuid(user_id)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol, name, quantity, cost_price, current_price,
                   floating_pnl, pnl_pct, market_value, holding_days
            FROM holdings WHERE created_by = :uid
            ORDER BY market_value DESC
        """), {"uid": uid})
        holdings = [{"symbol": row[0], "name": row[1], "quantity": row[2],
                      "cost_price": float(row[3] or 0), "current_price": float(row[4] or 0),
                      "floating_pnl": float(row[5] or 0), "pnl_pct": float(row[6] or 0),
                      "market_value": float(row[7] or 0), "holding_days": row[8]}
                    for row in r.fetchall()]

    if not holdings: return {"status": "error", "detail": "无持仓数据"}

    # v4.3: Delegate to holding_strategy service
    from app.services.holding_strategy import generate_holding_strategies
    result = await generate_holding_strategies(holdings, uid)
    return result


@router.get("/t-mode-suggestion/{symbol}")
async def get_t_mode_suggestion(symbol: str):
    import numpy as np, httpx, os
    from datetime import date as dt_date, timedelta
    from dotenv import load_dotenv
    load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
    TOKEN = os.getenv('TUSHARE_TOKEN')
    try:
        end_dt = dt_date.today().strftime('%Y-%m-%d')
        start_dt = (dt_date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post('https://api.tushare.pro', json={
                'api_name': 'stk_mins', 'token': TOKEN,
                'params': {'ts_code': symbol, 'freq': '5min',
                           'start_date': f'{start_dt} 09:00:00', 'end_date': f'{end_dt} 15:00:00'},
                'fields': 'ts_code,trade_time,open,close,high,low,vol,amount'
            })
            data = resp.json()
            if data.get('code') != 0: return {"status": "error", "detail": "分钟数据不可用"}
        items = data.get('data', {}).get('items', []) or []
        recent_items = [b for b in items if b[1][:10] >= (dt_date.today() - timedelta(days=30)).strftime('%Y-%m-%d')]
        if len(recent_items) < 50: return {"status": "error", "detail": "分钟数据不足"}
        r_lows = [float(b[5]) for b in recent_items]; r_highs = [float(b[4]) for b in recent_items]
        floor = round(float(np.percentile(r_lows, 5)), 2)
        ceiling = round(float(np.percentile(r_highs, 95)), 2)
        vw_sum = sum(float(b[3]) * float(b[6]) for b in recent_items)
        vw_denom = sum(float(b[6]) for b in recent_items)
        vwap = round(vw_sum / vw_denom, 2) if vw_denom > 0 else 0
        grid_width = round(ceiling - floor, 2)
        grid_pct = round(grid_width / vwap * 100, 1) if vwap > 0 else 0
        if 5 <= grid_pct <= 20:
            n_grids = max(3, min(8, int(grid_pct / 1.5)))
            grid_step = round(grid_width / n_grids, 2)
            suggestion = f"适合T模式: {n_grids}格网格, 每格{grid_step:.2f}元, 区间{floor}-{ceiling}"
        else:
            suggestion = f"振幅{grid_pct:.1f}%, {'偏窄不适合T' if grid_pct < 5 else '偏宽风险大'}"
        return {"status": "success", "data": {
            "symbol": symbol, "floor": floor, "ceiling": ceiling, "vwap": vwap,
            "grid_width": grid_width, "grid_pct": grid_pct, "suggestion": suggestion,
        }}
    except Exception as e:
        return {"status": "error", "detail": str(e)}
