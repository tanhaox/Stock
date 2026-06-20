"""Final results API."""
import json
from datetime import date
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db, async_session_factory
from app.core.name_resolver import get_stock_name
import logging
import numpy as np
logger = logging.getLogger("result")


def _sanitize_numpy(obj):
    """递归清洗所有 numpy 类型 → Python 原生类型，防止 jsonable_encoder 500."""
    if isinstance(obj, (np.bool_, np.bool)):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, dict):
        return {k: _sanitize_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_numpy(v) for v in obj]
    return obj


def _safe_int(val, default=40):
    """安全地将 FastAPI Query 或 int 转为 int."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default

router = APIRouter(prefix="/result", tags=["result"])


def _market_warning(regime: str, risk: str, count: int, hot_count: int = 0, relaxed: bool = False) -> str | None:
    """根据市场状态生成前端警告."""
    if count == 0:
        return f"当前{regime}(风险:{risk}), 无一股票通过严选标准。建议空仓观望。"
    if relaxed and count <= 8:
        return f"当前{regime}, 严选仅{hot_count}只, 已降级补入{count}只。⚠ 降级股来自放宽门槛, 风险较高。"
    if count <= 5 and regime in ("弱势探底", "缩量博弈"):
        return f"当前{regime}, 仅{count}只通过, 谨慎操作。"
    return None


@router.get("/final")
async def get_final_results(
    min_score: int = Query(40),
    limit: int = Query(20),
    symbols: str = Query(default=""),
    date: str = Query(default=""),  # ★ 历史回溯日期 (仅当 symbols 也提供时生效)
    db: AsyncSession = Depends(get_db),
):
    # ★ sanitize Query params to plain ints (FastAPI Query can leak into SQL, direct Python calls pass int)
    min_score_int = _safe_int(min_score, 40)
    limit_int = _safe_int(limit, 20)
    syms = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else []

    # ★ 确定 scan_date: 当传 symbols 时用这些股票最新的 scan_date,
    #    不信任外部传入的 date (LLM 反馈日期 ≠ TG 扫描日期)
    if syms:
        logger.info(f"Curated request: {len(syms)} symbols, first 3: {syms[:3]}")
        date_r = await db.execute(text(
            "SELECT MAX(scan_date) FROM analysis_scores WHERE symbol = ANY(:syms)"
        ), {"syms": syms})
        scan_date = date_r.scalar()
        logger.info(f"Curated scan_date resolved: {scan_date}")
    elif date:
        from datetime import date as _dt
        scan_date = _dt.fromisoformat(date)
    else:
        date_r = await db.execute(text("SELECT MAX(scan_date) FROM analysis_scores"))
        scan_date = date_r.scalar()
    if not scan_date:
        return {"status": "success", "data": [], "count": 0}

    base_select = """a.symbol, COALESCE(nc.name, s.name, a.name, a.symbol) as name,
               a.tech_score, a.kline_score, a.fund_score,
               a.sector_bonus, a.composite_score, a.market_correction,
               s.level, s.tg_momentum, s.close_price, a.archetype,
               a.weight_snapshot, a.adjustment_reasons,
               a.dimension_scores, a.win_probability, a.downside_risk,
               a.details,
               -- v7.0.32 新增 22 字段 (技术指标 + 筹码)
               a.macd_dif, a.macd_dea, a.macd_bar,
               a.kdj_k, a.kdj_d, a.kdj_j,
               a.rsi_6, a.rsi_12, a.rsi_24,
               a.boll_upper, a.boll_mid, a.boll_lower, a.boll_width, a.boll_pos,
               a.cci,
               a.cost_5pct, a.cost_50pct, a.cost_95pct, a.weight_avg, a.winner_rate,
               a.cost_spread, a.price_vs_cost,
               -- v7.0.34: OBV 主力能量潮
               a.obv_value, a.obv_ma20,
               fb.suggested_score as llm_score,
               fb.hidden_risks, fb.catalysts,
               COALESCE(s.resonance_type,'daily_only') as resonance_type,
               COALESCE(s.weekly_tg_momentum,0) as weekly_tg_momentum"""

    # 方案 B 排序: 共振优先 (weekly_resonance → 0, daily_only → 1, weekly_driven → 2)
    _resonance_order = "CASE COALESCE(s.resonance_type,'daily_only') WHEN 'weekly_resonance' THEN 0 WHEN 'daily_only' THEN 1 WHEN 'weekly_driven' THEN 2 ELSE 1 END"

    if syms:
        # ★ 当传 symbols 时, 取每只股票最新的 analysis_scores (不限制单一 scan_date)
        #    LLM 分析日期 ≠ TG 扫描日期, 两者可能差 1-2 天
        result = await db.execute(text(f"""
            SELECT {base_select}
            FROM (
                SELECT DISTINCT ON (symbol) *
                FROM analysis_scores
                WHERE symbol = ANY(:syms)
                ORDER BY symbol, scan_date DESC
            ) a
            LEFT JOIN scan_results s ON s.symbol = a.symbol AND s.scan_date = a.scan_date
            LEFT JOIN stock_name_cache nc ON nc.symbol = a.symbol
            LEFT JOIN LATERAL (
                SELECT suggested_score, hidden_risks, catalysts
                FROM stock_deep_feedback
                WHERE ts_code = a.symbol
                ORDER BY generated_at DESC LIMIT 1
            ) fb ON true
            ORDER BY {_resonance_order}, COALESCE(fb.suggested_score, a.composite_score) DESC
        """), {"syms": syms})
    else:
        result = await db.execute(text(f"""
            SELECT {base_select}
            FROM analysis_scores a
            LEFT JOIN scan_results s ON s.symbol = a.symbol AND s.scan_date = a.scan_date
            LEFT JOIN stock_name_cache nc ON nc.symbol = a.symbol
            LEFT JOIN LATERAL (
                SELECT suggested_score, hidden_risks, catalysts
                FROM stock_deep_feedback
                WHERE ts_code = a.symbol
                ORDER BY generated_at DESC LIMIT 1
            ) fb ON true
            WHERE a.scan_date = :d AND a.composite_score >= :ms
            ORDER BY {_resonance_order}, COALESCE(fb.suggested_score, a.composite_score) DESC LIMIT :lim
        """), {"d": scan_date, "ms": min_score_int, "lim": limit_int})

    rows = result.fetchall()
    data = []
    for r in rows:
        # Parse details JSON (position 17 in base_select = a.details)
        raw_details = r[17] if len(r) > 17 and r[17] else {}
        if isinstance(raw_details, str):
            try: raw_details = json.loads(raw_details)
            except Exception: raw_details = {}
        details = raw_details if isinstance(raw_details, dict) else {}
        predicted_return = details.get("predicted_return")
        predicted_win_prob = details.get("predicted_win_prob")
        # ── v7.0.10: 读取 v2 best_horizon (从 details JSON 解析) ──
        best_horizon = details.get("best_horizon")
        best_strategy = details.get("best_strategy")
        v2_advice = details.get("v2_advice")
        v2_net = details.get("v2_net")
        v2_active = details.get("v2_active", False)

        d = {"symbol": r[0], "name": r[1], "tech_score": float(r[2] or 0),
             "kline_score": float(r[3] or 0), "fund_score": float(r[4] or 0),
             "sector_bonus": float(r[5] or 0), "composite_score": float(r[6] or 0),
             "market_correction": r[7], "level": r[8], "tg_momentum": float(r[9] or 0),
             "close_price": float(r[10] or 0), "archetype": r[11] or "unknown",
             "weight_snapshot": r[12] if len(r) > 12 and r[12] else {},
             "adjustment_reasons": r[13] if len(r) > 13 and r[13] else [],
             "dimension_scores": r[14] if len(r) > 14 and r[14] else {},
             "win_probability": float(r[15]) if len(r) > 15 and r[15] is not None else None,
             "downside_risk": float(r[16]) if len(r) > 16 and r[16] is not None else None,
             "predicted_return": round(float(predicted_return), 2) if predicted_return is not None else None,
             "predicted_win_prob": round(float(predicted_win_prob), 3) if predicted_win_prob is not None else None,
             # ── v7.0.10: v2 持仓期字段 (Step 3 修 Bug A) ──
             "v2_active": bool(v2_active),
             "best_horizon": best_horizon,
             "best_strategy": best_strategy,
             "v2_advice": v2_advice,
             "v2_net": round(float(v2_net), 4) if v2_net is not None else None,
             # ★ v7.0.32: 新增 22 字段 (技术指标 + 筹码)
             "macd_dif": float(r[18]) if len(r) > 18 and r[18] is not None else None,
             "macd_dea": float(r[19]) if len(r) > 19 and r[19] is not None else None,
             "macd_bar": float(r[20]) if len(r) > 20 and r[20] is not None else None,
             "kdj_k": float(r[21]) if len(r) > 21 and r[21] is not None else None,
             "kdj_d": float(r[22]) if len(r) > 22 and r[22] is not None else None,
             "kdj_j": float(r[23]) if len(r) > 23 and r[23] is not None else None,
             "rsi_6": float(r[24]) if len(r) > 24 and r[24] is not None else None,
             "rsi_12": float(r[25]) if len(r) > 25 and r[25] is not None else None,
             "rsi_24": float(r[26]) if len(r) > 26 and r[26] is not None else None,
             "boll_upper": float(r[27]) if len(r) > 27 and r[27] is not None else None,
             "boll_mid": float(r[28]) if len(r) > 28 and r[28] is not None else None,
             "boll_lower": float(r[29]) if len(r) > 29 and r[29] is not None else None,
             "boll_width": float(r[30]) if len(r) > 30 and r[30] is not None else None,
             "boll_pos": float(r[31]) if len(r) > 31 and r[31] is not None else None,
             "cci": float(r[32]) if len(r) > 32 and r[32] is not None else None,
             "cost_5pct": float(r[33]) if len(r) > 33 and r[33] is not None else None,
             "cost_50pct": float(r[34]) if len(r) > 34 and r[34] is not None else None,
             "cost_95pct": float(r[35]) if len(r) > 35 and r[35] is not None else None,
             "weight_avg": float(r[36]) if len(r) > 36 and r[36] is not None else None,
             "winner_rate": float(r[37]) if len(r) > 37 and r[37] is not None else None,
             "cost_spread": float(r[38]) if len(r) > 38 and r[38] is not None else None,
             "price_vs_cost": float(r[39]) if len(r) > 39 and r[39] is not None else None,
             # v7.0.34: OBV 主力能量潮
             "obv_value": float(r[40]) if len(r) > 40 and r[40] is not None else None,
             "obv_ma20": float(r[41]) if len(r) > 41 and r[41] is not None else None,
             "llm_score": float(r[42]) if len(r) > 42 and r[42] is not None else None,
             "hidden_risks": r[43] if len(r) > 43 and r[43] else [],
             "catalysts": r[44] if len(r) > 44 and r[44] else [],
             "resonance_type": r[45] if len(r) > 45 and r[45] else "daily_only",
             "weekly_tg_momentum": float(r[46]) if len(r) > 46 and r[46] is not None else 0,
             # ★ Phase 26e: 三层相对强弱字段
             "relative_position": details.get("relative_position") if isinstance(details, dict) else None,
             "sector_direction": details.get("sector_direction") if isinstance(details, dict) else None,
             "sector_lifecycle": details.get("sector_lifecycle") if isinstance(details, dict) else None,
             "sector_rank_5d": details.get("sector_rank_5d") if isinstance(details, dict) else None,
             "market_5d": details.get("market_5d") if isinstance(details, dict) else None,
             "news_signal": details.get("news_signal") if isinstance(details, dict) else None,
             "rank_score": details.get("rank_score") if isinstance(details, dict) else None,
             # ★ v4.9: 三层 Regime 系数
             "market_coef": details.get("market_coef") if isinstance(details, dict) else None,
             "sector_coef": details.get("sector_coef") if isinstance(details, dict) else None,
             "final_regime_coef": details.get("final_regime_coef") if isinstance(details, dict) else None,
             "regime_signal": details.get("regime_signal") if isinstance(details, dict) else None,
             "regime_signal_cn": details.get("regime_signal_cn") if isinstance(details, dict) else None,
             }
        data.append(d)

    # ★ 名称兜底: 如果后端 SQL COALESCE 仍返回 symbol (老扫描/stock_basic 没数据),
    #   用 name_resolver.get_stock_name() 4 层兜底 (stock_basic → cache → scan_results → fallback)
    need_name_fix = [(d["symbol"], d["name"]) for d in data
                     if not d["name"] or d["name"] == d["symbol"]
                     or (d["name"] and (d["name"].endswith(".SH") or d["name"].endswith(".SZ") or d["name"].endswith(".BJ")))]
    if need_name_fix:
        logger.info(f"[result] {len(need_name_fix)} stocks need name fallback (symbol-only)")
        for sym, _ in need_name_fix:
            try:
                real_name = await get_stock_name(sym)
                if real_name and real_name != sym:
                    for d in data:
                        if d["symbol"] == sym:
                            d["name"] = real_name
                            break
            except Exception as e:
                logger.warning(f"Name fallback failed for {sym}: {e}")

    # S3 风险分类: 按原型内 composite_score 分位 (与 analysis.py 一致)
    arch_scores: dict[str, list[float]] = {}
    for d in data:
        arch_scores.setdefault(d["archetype"], []).append(d["composite_score"])
    for d in data:
        scores = sorted(arch_scores[d["archetype"]])
        # 使用 <= 避免并列分数全部标记为 dead
        pct = (sum(1 for s in scores if s <= d["composite_score"]) / len(scores)) * 100 if len(scores) >= 3 else 50
        if pct < 5:    d["risk_label"] = "dead"
        elif pct < 15:  d["risk_label"] = "danger"
        elif pct < 30:  d["risk_label"] = "warn"
        else:           d["risk_label"] = ""
    # S3 过滤: 死蛇踢出推荐 (S3流水线第3步 — 砍人)
    recommended = [d for d in data if d["risk_label"] != "dead"]
    filtered_dead = len(data) - len(recommended)

    # ★ Phase 35: 三策略分层 — 热点/温区/冰点 按板块排名打标签
    try:
        codes = [d["symbol"] for d in recommended]
        if codes:
            async with async_session_factory() as hs:
                r = await hs.execute(text("""
                    SELECT ssm.ts_code, ssm.sw_code, ssm.sw_name,
                           st.direction, st.lifecycle, st.rank_5d, st.pct_5d, st.pct_20d
                    FROM stock_sector_map ssm
                    LEFT JOIN sector_trend st ON ssm.sw_code = st.sector_code
                        AND st.trade_date = (SELECT MAX(trade_date) FROM sector_trend)
                    WHERE ssm.ts_code = ANY(:codes) AND ssm.sw_code IS NOT NULL
                      AND ssm.source != 'default'
                """), {"codes": codes})
                stock_sector = {}
                for row in r.fetchall():
                    stock_sector[row[0]] = {
                        "sw_code": row[1], "sw_name": row[2],
                        "direction": row[3] or "震荡", "lifecycle": row[4] or "正常",
                        "rank_5d": row[5] or 16,
                        "pct_5d": float(row[6] or 0), "pct_20d": float(row[7] or 0),
                    }

                # ★ Phase 37a: 加载 stock_tags 用于多级分组兜底
                r_tags = await hs.execute(text(
                    "SELECT ts_code, board, market_cap_tier FROM stock_tags WHERE ts_code = ANY(:c)"
                ), {"c": codes})
                stock_tags_map = {row[0]: {"board": row[1] or "主板", "cap": row[2] or "小盘"}
                                  for row in r_tags.fetchall()}

            # Three-tier classification
            for d in recommended:
                si = stock_sector.get(d["symbol"], {})
                rank = si.get("rank_5d", 16) or 16
                lifecycle = si.get("lifecycle", "正常")
                direction = si.get("direction", "震荡")

                if rank <= 8:
                    tier = "hot"
                    strategy_label = "超短策略" + ("(发酵)" if lifecycle == "发酵" else "")
                elif rank <= 24:
                    tier = "warm"
                    strategy_label = "短中策略"
                else:
                    tier = "cold"
                    strategy_label = "中线策略"

                d["sector_tier"] = tier
                d["strategy_label"] = strategy_label
                d["sector_rank"] = rank

            # ★ Phase 37a: 多级分组兜底 — SW行业 → board+cap → board → 全局
            by_sw: dict[str, list[dict]] = {}
            by_board: dict[str, list[dict]] = {}
            by_cap: dict[str, list[dict]] = {}
            for d in recommended:
                sym = d["symbol"]
                sw = stock_sector.get(sym, {}).get("sw_code", "unknown")
                board = stock_tags_map.get(sym, {}).get("board", "主板")
                cap = stock_tags_map.get(sym, {}).get("cap", "小盘")
                if sw != "unknown":
                    by_sw.setdefault(sw, []).append(d)
                by_board.setdefault(board, []).append(d)
                by_cap.setdefault(f"{board}_{cap}", []).append(d)

            # ── First pass: assign peer ranks per group ──
            all_group_lists = list(by_sw.values()) + list(by_board.values()) + list(by_cap.values())
            for group_list in all_group_lists:
                if len(group_list) < 2:
                    continue
                sorted_g = sorted(group_list, key=lambda x: -(x.get("composite_score", 0)))
                total = len(sorted_g)
                for idx, d in enumerate(sorted_g):
                    if d.get("peer_rank"):
                        continue  # already assigned by a more specific group
                    sym = d["symbol"]
                    sw = stock_sector.get(sym, {}).get("sw_code", "unknown")
                    board = stock_tags_map.get(sym, {}).get("board", "主板")
                    cap = stock_tags_map.get(sym, {}).get("cap", "小盘")
                    if sw != "unknown" and len(by_sw.get(sw, [])) >= 2:
                        gk = f"SW:{sw[-7:]}"
                    elif len(by_cap.get(f"{board}_{cap}", [])) >= 2:
                        gk = f"{board}_{cap}"
                    elif len(by_board.get(board, [])) >= 2:
                        gk = board
                    else:
                        gk = "全局"
                    d["peer_rank"] = f"{gk}#{idx+1}/{total}"
                    d["peer_rank_num"] = idx + 1

            # Fallback: stocks not in any multi-stock group → solo
            for d in recommended:
                if not d.get("peer_rank"):
                    d["peer_rank"] = "全局#1/1"
                    d["peer_rank_num"] = 1

            # ── Second pass: score labels + spread adjustment ──
            for d in recommended:
                idx = d.get("peer_rank_num", 0)
                total_in_group = int(d.get("peer_rank", "").split("/")[1]) if "/" in d.get("peer_rank", "") else len(recommended)
                group_key = d.get("peer_rank", "全局").split("#")[0]

                # ★ Phase 38b/68: 同组排名标签 (不再覆写 composite_score, 归一化去重已在 deep_scorer 完成)
                if total_in_group >= 2:
                    rank_0 = idx - 1  # convert 1-based peer_rank_num to 0-based
                    if rank_0 == 0:
                        d["score_label"] = f"🏆 {group_key}龙头#1/{total_in_group}"
                    elif rank_0 == 1:
                        d["score_label"] = f"🥈 {group_key}#2/{total_in_group}"
                    elif rank_0 == 2:
                        d["score_label"] = f"🥉 {group_key}#3/{total_in_group}"
                    else:
                        d["score_label"] = ""

            # ★ Phase 39: 综合推荐指数 (0-100)
            for d in recommended:
                cs = d.get("composite_score", 50)
                peer_rank_num = d.get("peer_rank_num", 1)
                total = int(d.get("peer_rank", "").split("/")[1]) if "/" in d.get("peer_rank", "") else 1
                tier = d.get("sector_tier", "warm")
                pred_r = d.get("predicted_return") or 0
                sector_rank = d.get("sector_rank", 16) or 16
                resonance = d.get("resonance_type", "")
                tl_on = 1 if d.get("tl_on_toplist") == 1 else 0  # may come from details

                # Sub-scores (0-100)
                score_cs = min(cs, 100)
                score_peer = 100 - ((peer_rank_num - 1) / max(total - 1, 1)) * 100
                score_strategy = {"hot": 70, "warm": 90, "cold": 60}.get(tier, 80)
                score_pred = min(100, max(0, 50 + pred_r * 5)) if pred_r != 0 else 50
                score_sector = 100 if sector_rank <= 8 else (60 if sector_rank <= 24 else 30)

                rec_index = round(
                    0.35 * score_cs + 0.25 * score_peer + 0.20 * score_strategy
                    + 0.10 * score_pred + 0.10 * score_sector
                )
                # Extra bonuses
                if resonance == "weekly_resonance":
                    rec_index = round(rec_index * 1.05)
                if tl_on:
                    rec_index = round(rec_index * 1.03)
                if peer_rank_num == 1 and total >= 3:
                    rec_index = round(rec_index * 1.05)

                d["rec_index"] = round(min(100, max(0, rec_index)))
                d["rec_index_detail"] = f"评分{score_cs:.0f}%×0.35+排名{score_peer:.0f}%×0.25+策略{score_strategy}%×0.20+预测×0.10+板块×0.10"

            # Sort: tier → peer_rank_num ASC (龙头优先) → composite_score DESC
            tier_order = {"hot": 0, "warm": 1, "cold": 2}
            recommended.sort(key=lambda x: (
                tier_order.get(x.get("sector_tier", "cold"), 2),
                x.get("peer_rank_num", 99),
                -(x.get("composite_score", 0))
            ))
    except Exception as e:
        logger.warning(f"Sector tier classification skipped: {e}")

    # 30交易日累计推送次数
    if data:
        from datetime import date as _dt
        syms = [d["symbol"] for d in data]
        cutoff = scan_date
        if scan_date:
            push_r2 = await db.execute(text("""
                SELECT trade_date FROM (
                    SELECT DISTINCT trade_date FROM daily_kline
                    WHERE trade_date <= :d ORDER BY trade_date DESC LIMIT 31
                ) sub ORDER BY trade_date LIMIT 1
            """), {"d": scan_date})
            row30 = push_r2.fetchone()
            if row30: cutoff = row30[0]
        push_r = await db.execute(text("""
            SELECT symbol, COUNT(DISTINCT scan_date) FROM analysis_scores
            WHERE symbol = ANY(:syms) AND scan_date >= :ms AND composite_score >= :ms2
            GROUP BY symbol
        """), {"syms": syms, "ms": cutoff, "ms2": min_score_int})
        push_counts = {row[0]: row[1] for row in push_r.fetchall()}
        for d in data:
            d["monthly_pushes"] = push_counts.get(d["symbol"], 1)

    has_feedback = any(
        d.get("llm_score") is not None or d.get("hidden_risks") or d.get("catalysts")
        for d in data
    )


    # ★ Phase C: 板块感知门控 → 委托 recommendation_gating (v4.3)
    gate_config = {}
    gate_filtered = 0
    regime_hard_cap = None
    drill_data = {}
    force_empty = False
    hot_passed = 0
    relaxed_any = False
    stock_industry = {}
    watchlist = []
    regime = "unknown"
    risk = "unknown"
    try:
        from app.services.market_gate import get_gate_config, get_market_state
        from app.services.recommendation_gating import collect_hot_sectors, apply_market_gate, apply_drill_corrections
        gate_config = await get_gate_config()

        # ★ Phase 31: 如果自适应阈值可用且数据充足，覆盖硬编码阈值
        adaptive = gate_config.get("adaptive", {})
        if adaptive.get("status") == "adaptive" and adaptive.get("min_score"):
            min_score_int = max(adaptive["min_score"], min_score_int)
            # floor 25 分防纯噪声

        ms = await get_market_state()
        regime = ms.get("regime", "unknown")
        risk = ms.get("risk", "normal")
        force_empty = gate_config.get("force_empty", False)

        # ★ 精选反哺路径: 用户主动选的股票全部展示，不砍
        is_curated = len(syms) > 0
        if is_curated:
            for d in recommended:
                d["hot_sector"] = False
                d["sector_name"] = ""
            if recommended and not force_empty:
                drill_data = await apply_drill_corrections(recommended, scan_date, regime)
            gate_filtered = 0
        else:
            hot_sectors, hot_individuals = await collect_hot_sectors()
            codes = [d['symbol'] for d in recommended]
            if codes:
                async with async_session_factory() as hs:
                    r = await hs.execute(text("SELECT ts_code, ths_name FROM ths_member WHERE ts_code = ANY(:c) AND out_date IS NULL"), {'c': codes})
                    stock_industry = {row[0]: row[1] or '' for row in r.fetchall()}
            gate_result = await apply_market_gate(recommended, data, regime, risk, hot_sectors, hot_individuals, stock_industry)
            recommended = gate_result.get("passed", recommended)
            gate_filtered = gate_result.get("gate_filtered", 0)
            regime_hard_cap = gate_result.get("regime_hard_cap")
            hot_passed = gate_result.get("hot_passed", 0)
            relaxed_any = any(d.get("gate_relaxed") for d in recommended)
            watchlist = [d for d in data if d not in recommended][:20] if gate_filtered > 0 else []
            for w in watchlist:
                w["filtered_reason"] = w.get("filtered_reason", "gate filtered")
            if recommended and not force_empty:
                drill_data = await apply_drill_corrections(recommended, scan_date, regime)
    except Exception as e:
        logger.exception(f"Gate/drill failed: {e}")
        regime = "unknown"; risk = "unknown"

    if force_empty:
        recommended = []
        gate_filtered = len(data)

    market_themes = []
    try:
        from app.services.sector_heat_engine import cross_validate_with_toplist
        cv_results = await cross_validate_with_toplist()
        confirmed = [r for r in cv_results if r["verdict"] == "confirmed"][:3]
        outflow = sorted([r for r in cv_results if r["net_flow"] < 0], key=lambda x: x["net_flow"])[:3]
        market_themes = [{"type": "main_line", "label": "今日主线", "sectors": confirmed},
                         {"type": "outflow", "label": "资金流出", "sectors": outflow}]
    except Exception:
        pass

    return _sanitize_numpy({
        "status": "success", "data": recommended, "count": len(recommended),
        "scan_date": str(scan_date), "has_feedback": has_feedback,
        "s3_filtered": filtered_dead, "gate": gate_config,
        "gate_filtered": gate_filtered, "timing_cap": regime_hard_cap,
        "watchlist": watchlist, "drill_data": drill_data,
        "market_themes": market_themes,
        "market_warning": (
            {"action": "empty",
             "message": "市场极度危险，系统建议完全空仓。恐慌杀跌 + 近期胜率<25% + 上涨家数<20%。",
             "regime": regime, "risk": risk}
            if force_empty
            else _market_warning(regime, risk, len(recommended), hot_passed, relaxed_any)
        )
    })

@router.get("/fusion")
async def get_fusion_board(limit: int = Query(50), db: AsyncSession = Depends(get_db)):
    try:
        r = await db.execute(text("""
            SELECT sds.symbol, sds.tg_score, sds.dim11_score, sds.ambush_score,
                   COALESCE(sds.tg_score,0)*0.5+COALESCE(sds.dim11_score,0)*0.3+COALESCE(sds.ambush_score,0)*0.2 as fusion,
                   COALESCE(sr.name, '') as name
            FROM strategy_daily_score sds
            LEFT JOIN scan_results sr ON sr.symbol=sds.symbol AND sr.scan_date=(SELECT MAX(scan_date) FROM scan_results)
            WHERE sds.trade_date=(SELECT MAX(trade_date) FROM strategy_daily_score)
            ORDER BY fusion DESC LIMIT :lim
        """), {"lim": limit})
        data = [{"symbol": r[0], "tg_score": float(r[1] or 0), "dim11_score": float(r[2] or 0),
                 "ambush_score": float(r[3] or 0), "fusion_score": round(float(r[4]), 1), "name": r[5]}
                for r in r.fetchall()]
    except Exception:
        # 降级：从 analysis_scores 取数据
        r = await db.execute(text("""
            SELECT symbol, name, composite_score, archetype
            FROM analysis_scores
            WHERE scan_date=(SELECT MAX(scan_date) FROM analysis_scores)
            ORDER BY composite_score DESC LIMIT :lim
        """), {"lim": limit})
        data = [{"symbol": r[0], "name": r[1], "fusion_score": float(r[2] or 0), "archetype": r[3] or "unknown"}
                for r in r.fetchall()]
    return {"status": "success", "data": data, "count": len(data)}

@router.get("/history")
async def get_analysis_history(limit: int = Query(3), db: AsyncSession = Depends(get_db)):
    """返回用户最近的分析历史(按日期分组，用于快捷入口按钮)."""
    r = await db.execute(text("""
        SELECT trade_date, array_agg(DISTINCT ts_code ORDER BY ts_code) as symbols
        FROM stock_deep_feedback
        GROUP BY trade_date
        ORDER BY trade_date DESC
        LIMIT :lim
    """), {"lim": limit})
    rows = r.fetchall()
    data = []
    for row in rows:
        d = row[0]
        syms = row[1] if row[1] else []
        data.append({
            "date": str(d),
            "label": f"{d.month}月{d.day}日分析",
            "symbols": syms,
            "count": len(syms),
        })
    return {"status": "success", "data": data}

