"""DeepSeek 反哺 API."""
import json
from datetime import date as date_type
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.schemas.feedback import SubmitRawRequest, SubmitRequest, BatchScoreRequest
from sqlalchemy import text
from app.core.config import settings
from app.core.database import async_session_factory
from app.core.auth import get_current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])

@router.post("/parse")
async def parse_feedback(req: SubmitRawRequest):
    from app.services.feedback_parser import parse_feedback_text
    result = await parse_feedback_text(req.raw_response)
    return {"status": "success", "data": result}

@router.post("/submit-raw")
async def submit_raw_feedback(req: SubmitRawRequest, user_id: str = Depends(get_current_user)):
    import asyncio
    import logging
    logger = logging.getLogger("feedback")

    td = date_type.fromisoformat(req.trade_date) if isinstance(req.trade_date, str) else req.trade_date

    # 立即存储原始文本(快速响应)
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO stock_deep_feedback (ts_code, trade_date, user_id, source_type, raw_response,
                suggested_score, confidence_score, business_stage, profit_quality,
                hidden_risks, catalysts, data_corrections, capability_gaps, profit_attribution, generated_at)
            VALUES (:ts, :td, :uid, :st, :raw, NULL, NULL, NULL, NULL,
                    '[]', '[]', '[]', '[]', '[]', NOW())
            ON CONFLICT (ts_code, trade_date, user_id) DO UPDATE SET
                raw_response=EXCLUDED.raw_response, generated_at=NOW()
        """), {
            "ts": req.ts_code, "td": td, "uid": user_id,
            "st": req.source_type or "browser_extension", "raw": req.raw_response[:50000],
        })
        await s.commit()

    # 后台异步解析(不阻塞响应)
    async def _parse_later():
        try:
            from app.services.llm_deep_analyzer import process_and_store_deepseek_response
            stored = await process_and_store_deepseek_response(
                req.ts_code, td, req.raw_response, req.source_type or "browser_extension"
            )
            if stored.get("status") == "success":
                logger.info(f"DeepSeek feedback parsed for {req.ts_code}: "
                           f"{stored.get('positive',0)}pos + {stored.get('negative',0)}neg signals")
                return

            # 未检测到JSON → 方法2: feedback_parser文本解析
            from app.services.feedback_parser import parse_feedback_text
            parsed = await parse_feedback_text(req.raw_response)
            async with async_session_factory() as s:
                await s.execute(text("""
                    UPDATE stock_deep_feedback SET
                        suggested_score=:ss, confidence_score=:cs,
                        business_stage=:bs, profit_quality=:pq,
                        hidden_risks=CAST(:hr AS jsonb), catalysts=CAST(:ct AS jsonb),
                        data_corrections=CAST(:dc AS jsonb), capability_gaps=CAST(:cg AS jsonb),
                        profit_attribution=CAST(:pa AS jsonb)
                    WHERE ts_code=:ts AND trade_date=CAST(:td AS date) AND user_id=:uid
                """), {
                    "ts": req.ts_code, "td": td, "uid": req.source_type or "browser_extension",
                    "ss": parsed.get("suggested_score"), "cs": parsed.get("confidence_score"),
                    "bs": parsed.get("business_stage"), "pq": parsed.get("profit_quality"),
                    "hr": json.dumps(parsed.get("hidden_risks") or []),
                    "ct": json.dumps(parsed.get("catalysts") or []),
                    "dc": json.dumps(parsed.get("data_corrections") or []),
                    "cg": json.dumps(parsed.get("capability_gaps") or []),
                    "pa": json.dumps(parsed.get("profit_attribution") or []),
                })
                await s.commit()
            logger.info(f"Async parse complete for {req.ts_code}")
        except Exception as e:
            logger.warning(f"Async parse failed for {req.ts_code}: {e}")

    asyncio.create_task(_parse_later())

    return {"status": "success", "message": "反馈已记录(后台解析中)"}

@router.post("/submit")
async def submit_feedback(req: SubmitRequest, user_id: str = Depends(get_current_user)):
    td = date_type.fromisoformat(req.trade_date) if isinstance(req.trade_date, str) else req.trade_date
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO stock_deep_feedback (ts_code, trade_date, user_id, source_type, raw_response,
                business_stage, profit_quality, recurring_profit_pct, suggested_score, confidence_score,
                data_freshness, profit_attribution, hidden_risks, catalysts, data_corrections,
                capability_gaps, system_score_before, generated_at)
            VALUES (:ts, :td, :uid, 'user_paste', :raw, :bs, :pq, :rpp, :ss, :cs, :df,
                    CAST(:pa AS jsonb), CAST(:hr AS jsonb), CAST(:ct AS jsonb), CAST(:dc AS jsonb),
                    CAST(:cg AS jsonb), :sb, NOW())
            ON CONFLICT (ts_code, trade_date, user_id) DO UPDATE SET
                raw_response=EXCLUDED.raw_response, suggested_score=EXCLUDED.suggested_score,
                confidence_score=EXCLUDED.confidence_score, business_stage=EXCLUDED.business_stage,
                profit_quality=EXCLUDED.profit_quality, hidden_risks=EXCLUDED.hidden_risks,
                catalysts=EXCLUDED.catalysts, generated_at=NOW()
        """), {
            "uid": user_id,
            "ts": req.ts_code, "td": td, "raw": req.raw_response[:50000],
            "bs": req.business_stage, "pq": req.profit_quality, "rpp": req.recurring_profit_pct,
            "ss": req.suggested_score, "cs": req.confidence_score, "df": req.data_freshness,
            "pa": json.dumps(req.profit_attribution), "hr": json.dumps(req.hidden_risks),
            "ct": json.dumps(req.catalysts), "dc": json.dumps(req.data_corrections),
            "cg": json.dumps(req.capability_gaps), "sb": req.system_score_before,
        })
        await s.commit()
    return {"status": "success", "message": "反馈已记录"}

@router.get("/list")
async def list_feedback(ts_code: str | None = None, trade_date: str | None = None, limit: int = 20):
    conditions = ["1=1"]; params = {"lim": limit}
    if ts_code: conditions.append("ts_code=:ts"); params["ts"] = ts_code
    if trade_date: conditions.append("trade_date=CAST(:td AS date)"); params["td"] = trade_date
    async with async_session_factory() as s:
        r = await s.execute(text(f"SELECT ts_code,trade_date,suggested_score,confidence_score,business_stage,profit_quality,applied,validation_status,system_score_before,system_score_after FROM stock_deep_feedback WHERE {' AND '.join(conditions)} ORDER BY generated_at DESC LIMIT :lim"), params)
        data = [{"ts_code":row[0],"trade_date":str(row[1]),"suggested_score":row[2],"confidence_score":row[3],"business_stage":row[4],"profit_quality":row[5],"applied":row[6],"validation_status":row[7],"system_score_before":row[8],"system_score_after":row[9]} for row in r.fetchall()]
    return {"status":"success","data":data,"count":len(data)}


@router.get("/check-batch")
async def check_feedback_batch(symbols: str = "", trade_date: str | None = None):
    """批量检查反哺记录，返回详情+原始文本供前端展示."""
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"status": "success", "data": {}}
    from datetime import date as dt_date, timedelta
    td = dt_date.fromisoformat(trade_date) if trade_date else dt_date.today()
    td_start = td - timedelta(days=1)
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, suggested_score, hidden_risks, catalysts, business_stage, profit_quality, raw_response "
            "FROM stock_deep_feedback "
            "WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :ds AND :de"
        ), {"syms": syms, "ds": td_start, "de": td})
        feedback_map = {}
        for row in r.fetchall():
            feedback_map[row[0]] = {
                "received": True,
                "suggested_score": float(row[1]) if row[1] else None,
                "hidden_risks": row[2] if row[2] else [],
                "catalysts": row[3] if row[3] else [],
                "business_stage": row[4] or "",
                "profit_quality": row[5] or "",
                "raw_response": (row[6] or "")[:3000],
            }
    return {"status": "success", "data": {s: feedback_map.get(s, {"received": False}) for s in syms}}


@router.post("/batch-score")
async def batch_score_stocks(req: BatchScoreRequest):
    """对多只股票的反哺文本进行横向对比评分(短期+中期各0-10分)."""
    if not req.symbol_texts:
        return {"status": "error", "detail": "无数据"}

    # 如果前端传的是空文本，从数据库补充
    symbols = list(req.symbol_texts.keys())
    texts = dict(req.symbol_texts)
    async with async_session_factory() as s:
        for sym in symbols:
            if not texts.get(sym):
                r = await s.execute(text(
                    "SELECT raw_response FROM stock_deep_feedback WHERE ts_code=:s AND trade_date=CURRENT_DATE ORDER BY generated_at DESC LIMIT 1"
                ), {"s": sym})
                row = r.fetchone()
                if row:
                    texts[sym] = row[0] or ""

    # 构建提示词 — 发送全文 + JSON信号
    stocks_text = []
    for sym in symbols:
        txt = texts.get(sym, "")
        if not txt:
            continue
        # 尝试从 parsed JSON 信号中提取结构化摘要
        async with async_session_factory() as s2:
            r2 = await s2.execute(text(
                "SELECT hidden_risks, catalysts FROM stock_deep_feedback WHERE ts_code=:s AND trade_date=CURRENT_DATE ORDER BY generated_at DESC LIMIT 1"
            ), {"s": sym})
            row2 = r2.fetchone()
        signal_text = ""
        if row2:
            risks = json.loads(row2[0]) if isinstance(row2[0], str) else (row2[0] or [])
            cats = json.loads(row2[1]) if isinstance(row2[1], str) else (row2[1] or [])
            if risks or cats:
                parts = []
                for r in (risks or []):
                    if isinstance(r, dict):
                        parts.append(f"  ⚠ {r.get('description','')} (置信度:{r.get('confidence','')})")
                for c in (cats or []):
                    if isinstance(c, dict):
                        parts.append(f"  ✓ {c.get('description','')} (置信度:{c.get('confidence','')})")
                if parts:
                    signal_text = "\n[结构化信号摘要]\n" + "\n".join(parts) + "\n"
        stocks_text.append(f"【{sym}】\n{signal_text}\n{txt}\n")

    if not stocks_text:
        return {"status": "error", "detail": "无可用的反哺文本"}

    all_text = "\n---\n".join(stocks_text)

    # ★ v7.0.32: 附加 22 字段硬指标 (技术指标 + 筹码分布, 让 LLM 看到真实数字)
    tech_lines = []
    try:
        async with async_session_factory() as s:
            # 一次查所有 symbols 的 22 字段
            placeholders = ", ".join([f":s{i}" for i in range(len(symbols[:20]))])
            params = {f"s{i}": sym for i, sym in enumerate(symbols[:20])}
            r = await s.execute(text(f"""
                SELECT symbol, a.composite_score, a.macd_dif, a.macd_dea, a.macd_bar,
                       a.kdj_k, a.kdj_d, a.kdj_j,
                       a.rsi_6, a.rsi_12, a.rsi_24,
                       a.boll_upper, a.boll_mid, a.boll_lower, a.boll_width, a.boll_pos,
                       a.cci,
                       a.cost_5pct, a.cost_50pct, a.cost_95pct, a.weight_avg, a.winner_rate,
                       a.cost_spread, a.price_vs_cost
                FROM analysis_scores a
                WHERE a.symbol IN ({placeholders})
                  AND a.scan_date = (SELECT MAX(scan_date) FROM analysis_scores WHERE symbol = a.symbol)
            """), params)
            for row in r.fetchall():
                sym = row[0]
                cs = row[1]
                macd_dif, macd_dea, macd_bar = row[2], row[3], row[4]
                kdj_k, kdj_d, kdj_j = row[5], row[6], row[7]
                rsi6, rsi12, rsi24 = row[8], row[9], row[10]
                boll_up, boll_mid, boll_low, boll_w, boll_pos = row[11], row[12], row[13], row[14], row[15]
                cci = row[16]
                cost5, cost50, cost95, wavg, wr = row[17], row[18], row[19], row[20], row[21]
                spread, pvc = row[22], row[23]

                def _f(v, p=2):
                    if v is None: return "—"
                    return f"{v:+.{p}f}" if p and v < 0 else f"{v:.{p}f}"

                lines = [
                    f"  综合分: {cs:.0f}",
                    f"  MACD: DIF={_f(macd_dif)} DEA={_f(macd_dea)} BAR={_f(macd_bar)}",
                    f"  KDJ: K={_f(kdj_k, 1)} D={_f(kdj_d, 1)} J={_f(kdj_j, 1)}",
                    f"  RSI: 6={_f(rsi6, 0)} 12={_f(rsi12, 0)} 24={_f(rsi24, 0)}",
                    f"  BOLL: 上={_f(boll_up)} 中={_f(boll_mid)} 下={_f(boll_low)} pos={_f(boll_pos, 2)}",
                    f"  CCI: {_f(cci, 1)}",
                    f"  筹码: 5%={_f(cost5)} 50%={_f(cost50)} 95%={_f(cost95)} 主力={_f(wavg)} 获利={_f(wr, 1)}% spread={_f(spread)} 现价vs成本={_f(pvc, 1)}%",
                ]
                tech_lines.append(f"{sym}:\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Batch score tech/chip 22 fields fetch failed: {e}")

    tech_note = ""
    if tech_lines:
        tech_note = "\n\n[★ v7.0.32 硬指标(技术+筹码) — 评分必须基于此]\n" + "\n\n".join(tech_lines)

    # ★ 附加筹码吸收率数据 (硬指标, 横向对比时让LLM看到)
    chip_lines = []
    try:
        from app.services.chip_analyzer import analyze_chip_absorption
        for sym in symbols[:20]:
            try:
                cr = await analyze_chip_absorption(sym)
                if cr and "absorption" in cr and "error" not in cr.get("absorption", {}):
                    ab = cr["absorption"]
                    chip_lines.append(
                        f"  {sym}: 吸收率{ab['ar_ratio']*100:.0f}% "
                        f"({ab['verdict']}, {ab['trend']})"
                    )
            except Exception as e:
                logger.warning(f"Chip data fetch failed for {sym}: {e}")
    except Exception as e:
        logger.warning(f"Batch score chip fetch failed: {e}")
        pass

    chip_note = ""
    if chip_lines:
        chip_note = "\n\n[筹码吸收率(系统硬指标)]\n" + "\n".join(chip_lines)

    # ★ v4.6: 注入宏观环境（Tier 1，来自 macro_data）
    macro_note = "宏观数据暂不可用"
    try:
        from app.services.macro_data import score_macro_impact, get_macro_snapshot
        adj, detail = await score_macro_impact()
        snap = await get_macro_snapshot()
        m2 = snap.get("m2_yoy", {}).get("value", "?")
        shibor = snap.get("shibor_3m", {}).get("value", "?")
        pmi = snap.get("pmi", {}).get("value", "?")
        cpi = snap.get("cpi_yoy", {}).get("value", "?")
        macro_note = (
            f"宏观环境(M2={m2}%, SHIBOR3M={shibor}%, PMI={pmi}, CPI={cpi}%)。"
            f"综合评分{adj:+.1f}分(-5~+5)。"
            f"{'偏多,利率敏感型股票可加分' if adj>0.5 else '偏空,防御型股票优先' if adj<-0.5 else '中性,按个股自身逻辑评分'}。"
        )
    except Exception:
        pass

    prompt = f"""你是A股量化基金经理。请以**盈亏视角**冷静点评每只股票，拒绝模棱两可，拒绝讨好。

当前宏观环境:
{macro_note}

评分铁律（严苛——5分为中性，多数股票应在3-7分之间）:
- 短期(1-4周): 看技术面+资金面+催化剂。**v7.0.32 技术指标 5 维作为硬指标**: MACD空头/KDJ超买(>80)/RSI超买(>70)/BOLL上轨外(>0.9)/CCI超买(>100) 任一触发必须打≤4分
- 中期(1-3月): 看基本面+行业趋势+估值。**筹码分布作为硬指标**: 现价相对主力成本 price_vs_cost>+20% 必须打≤4分(高估), winner_rate>85% 警戒(高位出货)
- 筹码: 参考[筹码吸收率]+v7.0.32 筹码5维 — 吸收率>60%=上方抛压轻, <40%=套牢盘重, 50%<获利盘<70%为吸筹黄金区

**v7.0.32 硬指标(必须严格基于此评分)**:
- MACD 多空: DIF>DEA=多头(加分), DIF<DEA=空头(减分)
- KDJ: J<20 超卖(短线加分), J>80 超买(短线减分)
- RSI24: <30 超卖(加分), >70 超买(减分)
- BOLL: boll_pos<0.1 下轨外(强反弹加分), >0.9 上轨外(强风险减分)
- CCI: <-100 超卖(加分), >100 超买(减分)
- 成本贴近: price_vs_cost<5% 加分(主力成本贴近), >20% 减分
- 获利盘: <30% 加分(底部吸筹), >85% 减分(高位风险)

**禁止**: 不得因股票代码熟悉就默认高分。不得写"建议关注/适当参与"等废话。直接给结论。

{all_text}
{tech_note}
{chip_note}

请只输出JSON，每只股票: short(0-10), mid(0-10), short_note(≤15字,盈亏视角操作建议), mid_note(≤15字,盈亏视角操作建议), support(短期支撑位), resistance(短期压力位)

格式:
{{"603855.SH":{{"short":5.5,"mid":6.0,"short_note":"J值38偏低,游资主导,短线回避","mid_note":"核心逻辑OK但估值偏高,等回调","support":"19.00","resistance":"22.50"}}}}"""

    from app.services.deepseek import call_deepseek
    result = await call_deepseek(prompt, max_tokens=4096, model=settings.DEEPSEEK_PRO_MODEL)

    import re
    json_match = re.search(r'\{.*\}', result, re.DOTALL)
    if not json_match:
        return {"status": "error", "detail": "LLM未返回有效评分", "raw": result[:500]}

    try:
        scores = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return {"status": "error", "detail": "JSON解析失败", "raw": result[:500]}

    # 系统计算支撑/压力位(纯技术，覆盖LLM可能未返回的值)
    from app.services.ma_scorer import calc_support_resistance
    for sym in symbols:
        try:
            sr = await calc_support_resistance(sym)
            if sr and sym in scores:
                s = scores[sym]
                if not s.get("support") and sr.get("support"):
                    s["support"] = str(sr["support"])
                if not s.get("resistance") and sr.get("resistance"):
                    s["resistance"] = str(sr["resistance"])
        except Exception:
            pass

    return {"status": "success", "scores": scores}
