"""信号质量评分器 — 识别假信号, 输出 signal_quality (0~1).

反训练 = 系统杀毒软件:
  signal_history = 病毒库
  特征提取 = 特征码扫描
  XGBoost = 启发式引擎
  signal_quality = 实时防护评分
"""
import json
import logging
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

ARCHETYPES = [
    "主板_large_bluechip", "主板_small_speculative", "主板_growth_tech",
    "主板_value_defensive", "主板_cyclical_resource",
    "创业板_large_bluechip", "创业板_small_speculative", "创业板_growth_tech",
    "创业板_value_defensive", "创业板_cyclical_resource",
]
MARKETS = ["主板", "中小板", "创业板"]

# ── 标签定义 ──

OUTCOME_LABELS = {
    "strong_win": "T+2收益>5%",
    "weak_win": "T+2收益0~5%",
    "flat": "T+2收益-2%~0%",
    "weak_loss": "T+2收益-5%~-2%",
    "strong_loss": "T+2收益<-5%",
}

DECEPTION_TYPES = {
    "quick_crash": "T+1跌>3%",
    "slow_fade": "T+2跌>2%, 非急跌",
    "flatline": "T+2收益-2%~2%, 窄幅横盘",
    "normal": "正常(非欺骗)",
    "breakout": "T+2涨>5%",
}


def _classify_outcome(ret_t2: float | None) -> str:
    if ret_t2 is None: return "unknown"
    if ret_t2 > 5: return "strong_win"
    if ret_t2 > 0: return "weak_win"
    if ret_t2 > -2: return "flat"
    if ret_t2 > -5: return "weak_loss"
    return "strong_loss"


def _classify_deception(ret_t1: float | None, ret_t2: float | None,
                         push_count: int, price_width_pct: float | None) -> str:
    if ret_t2 and ret_t2 > 5:
        return "breakout"
    if ret_t1 and ret_t1 < -3:
        return "quick_crash"
    if ret_t2 and ret_t2 < -2:
        return "slow_fade"
    if push_count >= 3 and price_width_pct and price_width_pct < 10:
        return "flatline"
    return "normal"


async def backfill_signal_history():
    """回填历史推荐数据到 signal_history."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT MIN(scan_date), MAX(scan_date) FROM analysis_scores WHERE composite_score >= 40"
        ))
        lo, hi = r.fetchone()
        if not lo: return 0

    logger.info(f"Backfilling signal_history: {lo} ~ {hi}")

    # 加载交易日
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= :d ORDER BY trade_date"
        ), {"d": lo - timedelta(days=30)})
        tdays = [row[0] for row in r.fetchall()]

    # 批量加载推荐记录 (Phase 44: 含 enrichment details)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol, scan_date, composite_score, archetype, market_correction, details
            FROM analysis_scores WHERE composite_score >= 40
            ORDER BY symbol, scan_date
        """))
        all_recs = [(row[0], row[1], float(row[2] or 0), row[3] or 'unknown', row[4] or '主板',
                     row[5]) for row in r.fetchall()]

    # Parse enrichment details per-record (Phase 44)
    enrichment: dict[tuple, dict] = {}
    for sym, sd, _, _, _, det in all_recs:
        det_dict = {}
        if isinstance(det, str):
            try: det_dict = json.loads(det)
            except Exception: pass
        elif isinstance(det, dict):
            det_dict = det
        enrichment[(sym, sd)] = {
            "relative_position": det_dict.get("relative_position"),
            "sector_direction": det_dict.get("sector_direction"),
            "sector_lifecycle": det_dict.get("sector_lifecycle"),
            "sector_rank_5d": det_dict.get("sector_rank_5d"),
            "market_5d": det_dict.get("market_5d"),
            "predicted_return": det_dict.get("predicted_return"),
            "predicted_win_prob": det_dict.get("predicted_win_prob"),
        }

    # 计算30日推送次数
    push_30d = {}
    for i, (sym, sd, _, _, _, _) in enumerate(all_recs):
        cutoff = sd - timedelta(days=30)
        cnt = sum(1 for j in range(max(0, i-50), i)
                  if all_recs[j][0] == sym and all_recs[j][1] >= cutoff)
        push_30d[(sym, sd)] = cnt + 1  # +1 = 包含本次

    # 批量加载价格数据
    async with async_session_factory() as s:
        symbols = list(set(r[0] for r in all_recs))
        r = await s.execute(text("""
            SELECT ts_code, trade_date, close, volume FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :d1 AND :d2
            ORDER BY ts_code, trade_date
        """), {"syms": symbols, "d1": lo - timedelta(days=60), "d2": hi + timedelta(days=30)})
        klines = defaultdict(list)
        for row in r.fetchall():
            klines[row[0]].append({"date": row[1], "close": float(row[2]), "vol": float(row[3])})

    inserted = 0
    async with async_session_factory() as s:
        for sym, sd, score, arch, mkt, _ in all_recs:
            key = (sym, sd)
            if key in push_30d and push_30d[key] <= 0:
                # Already inserted
                continue
            r_check = await s.execute(text(
                "SELECT 1 FROM signal_history WHERE symbol=:s AND scan_date=:d LIMIT 1"
            ), {"s": sym, "d": sd})
            if r_check.fetchone():
                continue

            # 价格区间 (推荐前20日)
            bars = klines.get(sym, [])
            recent = [b for b in bars if b["date"] <= sd][-20:]
            price_high = max(b["close"] for b in recent) if recent else None
            price_low = min(b["close"] for b in recent) if recent else None
            price_width = ((price_high - price_low) / price_low * 100
                           if price_high and price_low and price_low > 0 else None)

            # T+N 收益
            ret_t1, ret_t2, ret_t3, ret_t5 = None, None, None, None
            max_gain, max_loss = None, None
            entry_price = None
            for b in bars:
                if b["date"] == sd:
                    entry_price = b["close"]
                    break

            if entry_price:
                future = [b for b in bars if b["date"] > sd]
                for i, offset in enumerate([1, 2, 3, 5]):
                    if i < len(future):
                        rp = (future[min(i, len(future)-1)]["close"] - entry_price) / entry_price * 100
                        if offset == 1: ret_t1 = round(rp, 2)
                        elif offset == 2: ret_t2 = round(rp, 2)
                        elif offset == 3: ret_t3 = round(rp, 2)
                        elif offset == 5: ret_t5 = round(rp, 2)
                if future:
                    max_gain = round(max((b["close"] - entry_price) / entry_price * 100 for b in future), 2)
                    max_loss = round(min((b["close"] - entry_price) / entry_price * 100 for b in future), 2)

            outcome = _classify_outcome(ret_t2)
            deception = _classify_deception(ret_t1, ret_t2, push_30d.get(key, 1), price_width)

            try:
                await s.execute(text("""
                INSERT INTO signal_history (symbol, scan_date, composite_score, archetype, market,
                    push_count_30d, price_zone_high, price_zone_low, price_zone_width_pct,
                    ret_t1, ret_t2, ret_t3, ret_t5, max_gain_pct, max_loss_pct,
                    outcome_label, deception_type,
                    relative_position, sector_direction, sector_lifecycle,
                    sector_rank_5d, market_5d, predicted_return, predicted_win_prob,
                    strategy_label)
                VALUES (:s, :d, :sc, :a, :m, :p, :ph, :pl, :pw,
                    :r1, :r2, :r3, :r5, :mg, :ml, :o, :dt,
                    :rp, :sdir, :slc, :sr5, :m5d, :pret, :pwp,
                    :strat)
                """), {
                    "s": sym, "d": sd, "sc": score, "a": arch, "m": mkt,
                    "p": push_30d.get(key, 1), "ph": price_high, "pl": price_low, "pw": price_width,
                    "r1": ret_t1, "r2": ret_t2, "r3": ret_t3, "r5": ret_t5,
                    "mg": max_gain, "ml": max_loss, "o": outcome, "dt": deception,
                    "strat": "S2",  # Phase 61: 默认 S2 (T+5 收益率)——后续可从 strategy_map 推导
                    "rp": enrichment.get((sym, sd), {}).get("relative_position"),
                    "sdir": enrichment.get((sym, sd), {}).get("sector_direction"),
                    "slc": enrichment.get((sym, sd), {}).get("sector_lifecycle"),
                    "sr5": enrichment.get((sym, sd), {}).get("sector_rank_5d"),
                    "m5d": enrichment.get((sym, sd), {}).get("market_5d"),
                    "pret": enrichment.get((sym, sd), {}).get("predicted_return"),
                    "pwp": enrichment.get((sym, sd), {}).get("predicted_win_prob"),
                })
            except Exception as insert_err:
                logger.debug(f"signal_history insert skipped (table may not exist): {insert_err}")
                await s.rollback()
                break  # 表不存在，后续循环也会失败，直接退出
            else:
                inserted += 1

        await s.commit()

    logger.info(f"Backfilled {inserted} records into signal_history")
    return inserted


async def get_deception_stats() -> dict:
    """获取欺骗信号统计."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT outcome_label, deception_type, COUNT(*),
                   ROUND(AVG(ret_t2)::numeric, 2), ROUND(AVG(push_count_30d)::numeric, 1)
            FROM signal_history GROUP BY 1, 2 ORDER BY 1, 2
        """))
        stats = {}
        for row in r.fetchall():
            key = f"{row[0]}|{row[1]}"
            stats[key] = {"count": row[2], "avg_ret": float(row[3]), "avg_push": float(row[4])}
        return stats


async def score_signal_quality(symbol: str, scan_date: date,
                                composite_score: float, archetype: str,
                                push_count: int = 1, price_zone_width: float = None) -> dict:
    """预测信号质量 0~1 (1=高质量天使信号, 0=护法拦截).

    优先使用 XGBoost 模型预测, 回退到规则引擎.
    特征向量与 dual_channel_trainer.py 严格对齐.
    """
    try:
        import os, numpy as np
        model_dir = os.path.join(os.path.dirname(__file__), "..", "..", "models")
        guardian_path = os.path.join(model_dir, "guardian_model.json")

        if os.path.exists(guardian_path):
            import xgboost as xgb
            from app.services.dual_channel_trainer import (
                extract_behavioral_features, _compute_trajectory_features,
                MARKET_INDEX,
            )

            guardian = xgb.XGBClassifier()
            guardian.load_model(guardian_path)

            # ★ 加载天使模型 (T+2 收益预测)
            angel = None
            angel_path = os.path.join(model_dir, "angel_model.json")
            if os.path.exists(angel_path):
                angel = xgb.XGBRegressor()
                angel.load_model(angel_path)

            # ── 加载该股的 K 线、历史信号、大盘指数 ──
            prev_ret = 0.0
            stock_deception_rate = 0.0
            stock_signal_count_norm = 0.0
            stock_avg_ret_norm = 0.5
            pre_bars = []
            prev_scores_list = []
            prev_dates_list = []
            prev_vols_list = []
            idx_bars = []

            async with async_session_factory() as s:
                # K线数据 (信号日前25根)
                r = await s.execute(text("""
                    SELECT trade_date, close, volume, open, high, low
                    FROM daily_kline
                    WHERE ts_code = :sym AND trade_date <= :d
                    ORDER BY trade_date DESC LIMIT 25
                """), {"sym": symbol, "d": scan_date})
                krows = r.fetchall()
                pre_bars = [
                    {"date": row[0], "close": float(row[1]), "vol": float(row[2]),
                     "open": float(row[3]), "high": float(row[4]), "low": float(row[5])}
                    for row in reversed(krows)
                ]

                # 信号日成交量
                signal_vol = pre_bars[-1]["vol"] if pre_bars else 0.0

                # 上一笔收益 + 历史信号(用于轨迹特征)
                r = await s.execute(text("""
                    SELECT scan_date, composite_score, ret_t2
                    FROM signal_history
                    WHERE symbol = :s AND scan_date < :d
                    ORDER BY scan_date ASC
                """), {"s": symbol, "d": scan_date})
                hist_rows = r.fetchall()
                for hd, hscore, hret in hist_rows:
                    prev_scores_list.append(float(hscore or 0))
                    prev_dates_list.append(hd)
                    prev_vols_list.append(0.0)  # vol not available in signal_history, use 0
                    if hret is not None:
                        prev_ret = float(hret)  # 最后一笔

                # 个股欺骗史
                total_hist = len(hist_rows)
                if total_hist > 0:
                    deceptive_hist = sum(1 for _, _, hr in hist_rows
                                         if hr is not None and hr <= 0)
                    stock_deception_rate = deceptive_hist / total_hist
                    stock_signal_count_norm = min(total_hist, 20) / 20
                    rets = [float(hr) for _, _, hr in hist_rows if hr is not None]
                    if rets:
                        stock_avg_ret_norm = max(-20, min(20, float(np.mean(rets)))) / 20 + 0.5

                # 大盘指数 K线
                mkt = "创业板" if (archetype or "").startswith("创业板") else "主板"
                idx_code = MARKET_INDEX.get(mkt, "700001.TI")
                r = await s.execute(text("""
                    SELECT trade_date, close, volume, open, high, low
                    FROM daily_kline
                    WHERE ts_code = :sym AND trade_date <= :d
                    ORDER BY trade_date ASC
                """), {"sym": idx_code, "d": scan_date})
                for row in r.fetchall():
                    idx_bars.append({
                        "date": row[0], "close": float(row[1]), "vol": float(row[2]),
                        "open": float(row[3]), "high": float(row[4]), "low": float(row[5]),
                    })

            # ── 构建43维特征向量 ──
            push_f = float(push_count)
            score_norm = composite_score / 100
            pz_norm = float(price_zone_width or 10) / 50
            push_ratio = push_f / 10
            prev_ret_norm = max(-20, min(20, prev_ret)) / 20 + 0.5

            meta_feats = [
                score_norm, push_f, pz_norm, push_ratio,
                1.0 if prev_ret > 0 else 0.0, prev_ret_norm,
                score_norm * push_ratio, score_norm ** 2,
                1.0 if push_f > 5 else 0.0,
                1.0 if pz_norm < 0.1 else 0.0,
                0.0, 0.5,  # consec_pushes, days_gap (unknown for single prediction)
            ]

            behav_feats = extract_behavioral_features(pre_bars)

            history_feats = [stock_deception_rate, stock_signal_count_norm, stock_avg_ret_norm]

            # 轨迹特征: 构造临时的 stock_* dict 格式
            stock_scores_tmp = {symbol: prev_scores_list}
            stock_dates_tmp = {symbol: prev_dates_list}
            stock_volumes_tmp = {symbol: prev_vols_list}
            traj_feats = _compute_trajectory_features(
                symbol, scan_date, composite_score, push_f,
                stock_scores_tmp, stock_dates_tmp, stock_volumes_tmp,
                idx_bars,
            )

            feats = meta_feats + behav_feats + history_feats + traj_feats

            for a in ARCHETYPES:
                feats.append(1.0 if archetype == a else 0.0)
            for m in MARKETS:
                feats.append(1.0 if (archetype or "").startswith(m) else 0.0)

            X = np.array([feats], dtype=np.float32)
            deception_prob = float(guardian.predict_proba(X)[0, 1])

            # ★ 天使模型: 预期T+2收益 → 与护法欺骗概率组合
            angel_pred = 0.0
            if angel is not None:
                angel_pred = float(angel.predict(X)[0])
                # 组合: 正收益预期 × (1 - 欺骗概率)
                quality = round((1.0 + angel_pred / 100.0) * (1.0 - deception_prob), 2)
            else:
                quality = round(1.0 - deception_prob, 2)
            quality = max(0.05, min(1.0, quality))

            return {
                "quality": quality,
                "confidence": "model",
                "reason": f"XGBoost: 欺骗概率{deception_prob:.0%}"
                          + (f", 预期收益{angel_pred:+.1f}%" if angel else ""),
                "deceptive_rate": round(deception_prob, 2),
                "angel_return_prediction": round(angel_pred, 2),
            }
    except Exception as e:
        logger.debug(f"XGBoost prediction failed, using rules: {e}")
    # 查该股历史欺骗记录
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE deception_type != 'normal' AND deception_type != 'breakout'),
                   COUNT(*) FILTER (WHERE deception_type = 'flatline'),
                   COUNT(*) FILTER (WHERE deception_type = 'quick_crash')
            FROM signal_history
            WHERE symbol = :s AND scan_date < :d
        """), {"s": symbol, "d": scan_date})
        row = r.fetchone()
        if not row or row[0] == 0:
            # 无历史 → 默认中性
            return {"quality": 0.60, "confidence": "low", "reason": "无历史数据"}

        total, deceptive, flatlines, crashes = row
        deceptive_rate = deceptive / total if total > 0 else 0

        # 查同原型欺骗率
        r2 = await s.execute(text("""
            SELECT COUNT(*) FILTER (WHERE deception_type != 'normal' AND deception_type != 'breakout'),
                   COUNT(*)::float
            FROM signal_history WHERE archetype = :a
        """), {"a": archetype})
        row2 = r2.fetchone()
        arch_deceptive_rate = row2[0] / row2[1] if row2 and row2[1] > 0 else 0.3

        # 综合评分: 个股历史 + 原型统计
        quality = 1.0 - (deceptive_rate * 0.6 + arch_deceptive_rate * 0.4)
        quality = round(max(0.1, min(1.0, quality)), 2)

        confidence = "high" if total >= 5 else ("medium" if total >= 2 else "low")
        reason_parts = []
        if flatlines > 0: reason_parts.append(f"{flatlines}次横盘假信号")
        if crashes > 0: reason_parts.append(f"{crashes}次急跌假信号")
        reason = "; ".join(reason_parts) if reason_parts else f"历史欺骗率{deceptive_rate:.0%}"

        return {"quality": quality, "confidence": confidence,
                "reason": reason, "deceptive_rate": round(deceptive_rate, 2)}


# ═══════════════════════════════════════════════════════════════
# 分钟线 N/M 反哺验证 — 长尾理论
# ═══════════════════════════════════════════════════════════════

async def verify_signals_with_minute_bars(
    signal_stocks: list[dict],
    scan_date: date,
    top_n: int = 30,
) -> dict:
    """对 TG 信号股进行分钟线 N/M 验证 + 板块联盟分析.

    这是 3.4 分钟线反哺 TG 层的核心入口。

    Args:
        signal_stocks: [{symbol, name, composite_score, archetype, ...}, ...]
        scan_date: 扫描日期
        top_n: 只验证前 N 只 (按 composite_score 排序)

    Returns:
        {
            nm_results: {symbol: nm_detail},
            alliance: sector_alliance_result,
            quality_adjustments: {symbol: {old_quality, new_quality, adjustment}},
            summary: str,
        }
    """
    import asyncio, numpy as np
    from datetime import date as dt_date

    # 取前 N 只
    ranked = sorted(signal_stocks, key=lambda x: x.get("composite_score", 0), reverse=True)
    verify_list = ranked[:top_n]
    logger.info(f"Minute verification: {len(verify_list)} stocks (top {top_n})")

    if len(verify_list) < 3:
        return {"nm_results": {}, "alliance": None, "quality_adjustments": {},
                "summary": "信号股不足 3 只, 跳过分钟线验证"}

    # ── Phase 1: 逐只 N/M 检测 (并发, 使用 minute_on_demand) ──
    from app.services.minute_on_demand import get_minute_bars
    from app.services.minute_nm_detector import detect_nm_pattern

    sem = asyncio.Semaphore(3)

    async def _detect_one(stock: dict) -> dict:
        async with sem:
            sym = stock["symbol"]
            bars = await get_minute_bars(sym, period='5min', days=15)
            if len(bars) < 100:
                return {"symbol": sym, "error": "分钟数据不足", "nm_score": 0}
            nm = detect_nm_pattern(bars)
            nm["symbol"] = sym
            nm["name"] = stock.get("name", sym)
            nm["composite_score"] = stock.get("composite_score", 0)
            return nm

    tasks = [_detect_one(s) for s in verify_list]
    raw_results = await asyncio.gather(*tasks)

    nm_results = {}
    for r in raw_results:
        sym = r.pop("symbol", None) or r.get("symbol")
        if sym:
            nm_results[sym] = r

    n_stocks = sum(1 for r in nm_results.values() if r.get("nm_score", 0) > 0.1)
    m_stocks = sum(1 for r in nm_results.values() if r.get("nm_score", 0) < -0.1)
    logger.info(f"NM detection: {len(nm_results)} analyzed, {n_stocks} N-leaning, {m_stocks} M-leaning")

    # ── Phase 2: 板块联盟分析 ──
    from app.services.sector_alliance import analyze_sector_alliance

    alliance = await analyze_sector_alliance(verify_list, scan_date)

    # ── Phase 3: 综合调整 ──
    quality_adjustments = {}

    for stock in verify_list:
        sym = stock["symbol"]
        nm = nm_results.get(sym, {})
        adj = alliance.get("stock_adjustments", {}).get(sym, {})

        nm_score = nm.get("nm_score", 0)
        # 基础调整: NM分直接映射到质量调整
        # nm_score ∈ [-1, +1] → adjustment ∈ [-0.25, +0.25]
        nm_adjustment = nm_score * 0.25

        # 联盟调整
        alliance_adjustment = adj.get("adjustment", 0) * 0.30 if adj else 0

        # 综合调整
        total_adjustment = round(nm_adjustment + alliance_adjustment, 3)

        # 置信度 (分钟数据多 = 置信度高)
        nm_confidence = nm.get("confidence", "low")
        confidence_mult = {"high": 1.0, "medium": 0.6, "low": 0.2}.get(nm_confidence, 0.2)

        quality_adjustments[sym] = {
            "nm_score": nm_score,
            "nm_verdict": nm.get("verdict", "未分析"),
            "nm_confidence": nm_confidence,
            "dominant_shape": nm.get("dominant_shape", "unknown"),
            "n_days": nm.get("n_days", 0),
            "m_days": nm.get("m_days", 0),
            "sector_name": adj.get("sector_name", ""),
            "sector_nm": adj.get("sector_nm", 0),
            "nm_adjustment": nm_adjustment,
            "alliance_adjustment": alliance_adjustment,
            "total_adjustment": total_adjustment,
            "confidence_mult": confidence_mult,
        }

    n_boosted = sum(1 for v in quality_adjustments.values() if v["total_adjustment"] > 0.05)
    m_penalized = sum(1 for v in quality_adjustments.values() if v["total_adjustment"] < -0.05)

    return {
        "scan_date": str(scan_date),
        "nm_results": nm_results,
        "alliance": alliance,
        "quality_adjustments": quality_adjustments,
        "summary": (
            f"分钟线验证 {len(nm_results)} 只: "
            f"N型{n_stocks}只 M型{m_stocks}只 | "
            f"加分{n_boosted}只 减分{m_penalized}只 | "
            f"{alliance.get('summary', '')}"
        ),
        "statistics": {
            "verified_count": len(nm_results),
            "n_type_count": n_stocks,
            "m_type_count": m_stocks,
            "boosted_count": n_boosted,
            "penalized_count": m_penalized,
            "alliance_sectors": alliance.get("n_alliance_sectors", 0) + alliance.get("m_alliance_sectors", 0),
        },
    }


def apply_minute_adjustment(quality: float, adjustment: dict) -> dict:
    """将分钟线验证结果应用到信号质量分."""
    total_adj = adjustment.get("total_adjustment", 0)
    confidence = adjustment.get("confidence_mult", 0.5)

    effective_adj = total_adj * confidence
    new_quality = round(max(0.05, min(1.0, quality + effective_adj)), 2)

    parts = []
    nm_verdict = adjustment.get("nm_verdict", "")
    if nm_verdict:
        parts.append(f"分钟线: {nm_verdict[:40]}")
    sector_name = adjustment.get("sector_name", "")
    sector_nm = adjustment.get("sector_nm", 0)
    if sector_name:
        direction = "N型联盟" if sector_nm > 0.1 else ("M型联盟" if sector_nm < -0.1 else "板块中性")
        parts.append(f"板块[{sector_name}]: {direction}")

    return {
        "quality": new_quality,
        "original_quality": quality,
        "adjustment_amount": round(effective_adj, 3),
        "reason": " | ".join(parts) if parts else "分钟线验证无显著形态",
        "nm_score": adjustment.get("nm_score", 0),
        "dominant_shape": adjustment.get("dominant_shape", ""),
    }


# ═══════════════════════════════════════════════════════════════
# 分钟线防伪防火墙 — 自动运行, 事后惩戒
# ═══════════════════════════════════════════════════════════════

# M型惩罚系数: composite_score 下调幅度
M_PENALTY_FACTOR = 0.35      # M型: composite × (1 - m_score × 0.35)
STRONG_M_PENALTY = 0.50      # 强M (主导出货): composite × (1 - 0.50)
N_BOOST_FACTOR = 0.10        # N型确认: composite × (1 + n_score × 0.10)
ALLIANCE_BOOST = 0.08        # 板块联盟额外加成
BLOCK_SIZE = 5               # 每批下载 5 只, 控制 API 压力
TOP_N_DEFENSE = 25           # 只验证前 25 只高分信号


# ═══════════════════════════════════════════════════════════
# v4.9: 快速分钟线防伪 — TG 扫描后立即执行
# ═══════════════════════════════════════════════════════════

async def quick_nm_scan(scan_date: str, progress_callback=None, scored_stocks: list = None) -> dict:
    """TG 扫描后快速分钟线防伪 (v4.9 流程改造).

    扫描评分通过的股票列表，更新 scan_results.nm_verdict 字段。
    不调整分数，只标记 N/M 型。

    Args:
        scan_date: 扫描日期
        progress_callback: 进度回调函数
        scored_stocks: deep_score 返回的高分股票列表（dict list，包含 symbol）

    Returns:
        {"nm_verdicts": {symbol: verdict}, "m_count": N, "n_count": N}
    """
    import asyncio
    from datetime import datetime

    if isinstance(scan_date, str):
        scan_dt = datetime.strptime(scan_date, "%Y-%m-%d").date()
    else:
        scan_dt = scan_date

    # v4.9: 优先使用传入的高分股票列表，否则从数据库查询
    if scored_stocks:
        rows = [(s["symbol"], s.get("name", ""), s.get("level", "")) for s in scored_stocks if s.get("symbol")]
    else:
        # 降级：从数据库查询评分 >= 70 的股票
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT DISTINCT sr.symbol, sr.name, sr.level
                FROM scan_results sr
                INNER JOIN analysis_scores ans ON sr.symbol = ans.symbol AND sr.scan_date = ans.scan_date
                WHERE sr.scan_date = :d AND sr.level IN ('L2', 'L3')
                  AND ans.composite_score >= 70
            """), {"d": scan_dt})
            rows = [(row[0], row[1], row[2]) for row in r.fetchall()]

    if not rows:
        return {"nm_verdicts": {}, "m_count": 0, "n_count": 0, "status": "no_scored"}

    if progress_callback:
        await progress_callback("nm_defense", 0, len(rows), extra=f"分钟线防伪: 检测{len(rows)}只高分...")

    # 2. 并发下载分钟线 + NM 检测 (v5.5: 提高并发+实时进度)
    from app.services.minute_on_demand import get_minute_bars
    from app.services.minute_nm_detector import detect_nm_pattern

    sem = asyncio.Semaphore(30)  # v5.5: 提高并发加速检测
    completed = 0
    total = len(rows)

    async def _scan_one(idx, sym):
        nonlocal completed
        async with sem:
            try:
                bars = await get_minute_bars(sym, period='5min', days=5)
                if len(bars) < 100:
                    return sym, "insufficient"
                nm = detect_nm_pattern(bars)
                return sym, nm["dominant_shape"]
            except Exception as e:
                logger.warning(f"NM scan failed for {sym}: {e}")
                return sym, "error"
            finally:
                completed += 1
                if progress_callback and completed % 10 == 0:
                    pct = int(completed / total * 100)
                    await progress_callback("nm_defense", completed, total, extra=f"防伪检测 {completed}/{total} ({pct}%)")

    tasks = [_scan_one(i, sym) for i, (sym, _, _) in enumerate(rows)]
    results = await asyncio.gather(*tasks)

    # 处理结果
    nm_verdicts = {}
    for r in results:
        if r and r[0]:
            nm_verdicts[r[0]] = r[1]

    # 3. 批量更新 scan_results (v5.5: 使用批量执行)
    m_count = sum(1 for v in nm_verdicts.values() if v.startswith("M"))
    n_count = sum(1 for v in nm_verdicts.values() if v.startswith("N"))

    if nm_verdicts:
        async with async_session_factory() as s:
            # 批量更新：55只股票很快
            for sym, verdict in nm_verdicts.items():
                await s.execute(text("""
                    UPDATE scan_results SET nm_verdict = :v
                    WHERE scan_date = :d AND symbol = :s
                """), {"v": verdict, "d": scan_dt, "s": sym})
            await s.commit()

    if progress_callback:
        verdict_msg = f"N:{n_count} M:{m_count} 中性:{len(nm_verdicts)-m_count-n_count}"
        await progress_callback("nm_defense", len(rows), len(rows), extra=f"防伪完成: {verdict_msg}")

    logger.info(f"Quick NM scan: {len(nm_verdicts)} checked, N:{n_count} M:{m_count}")

    return {
        "nm_verdicts": nm_verdicts,
        "m_count": m_count,
        "n_count": n_count,
        "status": "success",
    }


async def run_nm_defense(scan_date: str) -> dict:
    """TG 扫描后自动运行 — 分钟线防伪防火墙.

    从 analysis_scores 取当日 Top-N 高分信号,
    下载 5 分钟线检测 N/M 形态,
    对 M 型 (出货形态) 信号扣减 composite_score,
    直接更新 analysis_scores 表。

    这是一个防御机制, 无需用户触发。
    对日线漂亮但分钟线拉胯的假信号执行惩戒。
    """
    import asyncio, numpy as np
    from datetime import date as dt_date

    # 取当日 L2/L3 高分信号
    # v4.9: 改用 scan_results 表筛选 L2/L3
    from datetime import datetime
    if isinstance(scan_date, str):
        scan_dt = datetime.strptime(scan_date, "%Y-%m-%d").date()
    else:
        scan_dt = scan_date

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT a.symbol, a.name, a.composite_score, a.archetype, a.market_correction
            FROM analysis_scores a
            JOIN scan_results r ON r.symbol = a.symbol AND r.scan_date = a.scan_date
            WHERE a.scan_date = :d AND r.level IN ('L2', 'L3')
            ORDER BY a.composite_score DESC
            LIMIT :lim
        """), {"d": scan_dt, "lim": TOP_N_DEFENSE})
        rows = [(row[0], row[1], float(row[2] or 0), row[3] or '', row[4] or '主板')
                for row in r.fetchall()]

    if len(rows) < 1:
        return {"status": "skipped", "reason": f"L2/L3信号不足 ({len(rows)} 只)", "penalized": 0}

    logger.info(f"NM Defense: analyzing {len(rows)} top signals")

    # ── 并发下载分钟线 + NM 检测 (使用 minute_on_demand) ──
    from app.services.minute_on_demand import get_minute_bars
    from app.services.minute_nm_detector import detect_nm_pattern

    sem = asyncio.Semaphore(3)

    async def _scan_one(sym, name, score):
        async with sem:
            bars = await get_minute_bars(sym, period='5min', days=15)
            if len(bars) < 100:
                return None
            nm = detect_nm_pattern(bars)
            return {
                "symbol": sym, "name": name,
                "composite_score": score,
                "nm_score": nm["nm_score"],
                "dominant_shape": nm["dominant_shape"],
                "n_days": nm["n_days"], "m_days": nm["m_days"],
                "confidence": nm["confidence"],
                "verdict": nm["verdict"],
            }

    tasks = [_scan_one(sym, name, score) for sym, name, score, _, _ in rows]
    results = await asyncio.gather(*tasks)
    nm_list = [r for r in results if r is not None]

    if not nm_list:
        return {"status": "skipped", "reason": "分钟数据全部不可用", "penalized": 0}

    # ── 板块联盟 ──
    try:
        stocks_for_alliance = [
            {"symbol": r["symbol"], "name": r["name"],
             "composite_score": r["composite_score"]}
            for r in nm_list
        ]
        from app.services.sector_alliance import analyze_sector_alliance
        alliance = await analyze_sector_alliance(
            stocks_for_alliance, dt_date.today(), concurrency=1)
    except Exception:
        alliance = {"stock_adjustments": {}, "sectors": {}}

    # ── 惩戒: 直接更新 analysis_scores ──
    penalized = 0
    boosted = 0
    details = []

    # ★ P2: 预加载反模式检测 (并发)
    anti_tasks = [detect_anti_patterns(r["symbol"], scan_date) for r in nm_list]
    anti_results = await asyncio.gather(*anti_tasks, return_exceptions=True)
    anti_map = {nm_list[i]["symbol"]: (r if not isinstance(r, Exception) else {"score_penalty": 0, "warnings": []})
                for i, r in enumerate(anti_results)}

    async with async_session_factory() as s:
        for nm in nm_list:
            sym = nm["symbol"]
            old_score = nm["composite_score"]
            nm_score = nm["nm_score"]
            shape = nm["dominant_shape"]
            adj = alliance.get("stock_adjustments", {}).get(sym, {})
            alliance_nm = adj.get("sector_nm", 0)
            confidence = nm["confidence"]

            # 信任度: high=1.0, medium=0.5, low=0.2
            trust = {"high": 1.0, "medium": 0.5, "low": 0.2}.get(confidence, 0.2)

            # 计算调整
            if shape in ("M_dominant",) or (shape == "M_leaning" and nm["m_days"] >= 5):
                # 强M: 主导出货 → 重罚
                penalty = STRONG_M_PENALTY * trust
            elif shape in ("M_leaning",):
                # 偏M → 轻罚
                penalty = M_PENALTY_FACTOR * abs(nm_score) * trust
            elif shape in ("N_dominant", "N_leaning") and nm["n_days"] >= 3:
                # N型确认 → 加分
                penalty = -N_BOOST_FACTOR * nm_score * trust  # 负=加分
                if alliance_nm > 0.15:
                    penalty -= ALLIANCE_BOOST * trust
            else:
                penalty = 0

            # 板块联盟修正: 如果个股N但板块M → 拉回中性
            if nm_score > 0 and alliance_nm < -0.15:
                penalty = M_PENALTY_FACTOR * abs(alliance_nm) * trust * 0.7

            new_score = round(max(10, old_score * (1 - penalty)), 1)

            # ★ 叠加反模式惩罚
            ap = anti_map.get(sym, {"score_penalty": 0, "warnings": []})
            ap_penalty = ap.get("score_penalty", 0) / 100  # 转为比例
            ap_trust = {"high": 0.8, "medium": 0.5, "low": 0.2}.get(ap.get("severity", "none"), 0.1)
            new_score = round(max(5, new_score * (1 + ap_penalty * ap_trust)), 1)

            nm_flag = json.dumps({
                "nm_score": nm_score, "shape": shape,
                "n_days": nm["n_days"], "m_days": nm["m_days"],
                "verdict": nm["verdict"], "confidence": confidence,
                "alliance_nm": alliance_nm,
                "penalty": round(penalty, 3),
                "anti_pattern_penalty": ap_penalty,
                "anti_warnings": ap.get("warnings", []),
            }, ensure_ascii=False)
            nm_flag = json.dumps({
                "nm_score": nm_score, "shape": shape,
                "n_days": nm["n_days"], "m_days": nm["m_days"],
                "verdict": nm["verdict"], "confidence": confidence,
                "alliance_nm": alliance_nm,
                "penalty": round(penalty, 3),
            }, ensure_ascii=False)

            await s.execute(text("""
                UPDATE analysis_scores
                SET composite_score = :cs,
                    signal_quality = COALESCE(signal_quality, 0.60) + :adj,
                    details = COALESCE(details, '{}'::jsonb) || CAST(:nm AS jsonb)
                WHERE scan_date = :d AND symbol = :s
            """), {
                "cs": new_score, "d": scan_date, "s": sym,
                "adj": round(-penalty * 0.5, 2),
                "nm": nm_flag,
            })

            if penalty > 0.05:
                penalized += 1
                details.append(f"  ✗ {sym} {nm['name']}: {old_score:.0f}→{new_score:.0f} "
                              f"({shape} nm={nm_score:+.2f} 罚{penalty:.0%})")
            elif penalty < -0.03:
                boosted += 1
                details.append(f"  ✓ {sym} {nm['name']}: {old_score:.0f}→{new_score:.0f} "
                              f"({shape} nm={nm_score:+.2f} 奖{abs(penalty):.0%})")

        await s.commit()

    for line in details[:15]:
        logger.info(line)

    logger.info(f"NM Defense: {penalized} penalized, {boosted} boosted, "
                f"{len(nm_list) - penalized - boosted} unchanged")

    return {
        "status": "success",
        "scanned": len(nm_list),
        "penalized": penalized,
        "boosted": boosted,
        "n_type": sum(1 for r in nm_list if r["nm_score"] > 0.15),
        "m_type": sum(1 for r in nm_list if r["nm_score"] < -0.15),
        "alliance_sectors": len(alliance.get("sectors", {})),
        "penalty_details": details[:10],
    }


# ═══════════════════════════════════════════════════════════
# P2: 反模式检测 — 板块孤例/尾盘做线/小盘操纵/反复推票
# ═══════════════════════════════════════════════════════════

async def detect_anti_patterns(symbol: str, scan_date: str | date) -> dict:
    """检测信号反模式 (假信号特征).

    四项检测:
      1. 板块孤例: 同行业都跌但这只独涨 → 可疑
      2. 尾盘做线: 日内涨幅主要来自最后30分钟 → 日线是画出来的
      3. 小盘操纵: 流通市值<20亿 + 日振幅>5% → 极易被操纵
      4. 反复推票: 30日内已有信号但未盈利 → 系统在重复犯错

    Returns:
        {score_penalty: -10~0, warnings: [...], severity: 'none'|'low'|'medium'|'high'}
    """
    from datetime import date as dt_date
    if isinstance(scan_date, str):
        scan_date = dt_date.fromisoformat(scan_date)

    penalty = 0
    warnings = []

    async with async_session_factory() as s:
        # ── 1. 板块孤例检测 ──
        try:
            from app.services.sector_heat_engine import get_stock_sector_factor, get_sector_rankings
            sf = await get_stock_sector_factor(symbol)
            sector_rank = sf.get("sector_rank_5d", 99)
            sector_pct = sf.get("sector_pct_5d", 0)

            # 查该股近5日涨幅
            r = await s.execute(text("""
                SELECT close FROM daily_kline
                WHERE ts_code = :s ORDER BY trade_date DESC LIMIT 6
            """), {"s": symbol})
            closes = [float(row[0] or 0) for row in r.fetchall()]
            if len(closes) >= 6:
                stock_5d = (closes[0] / closes[4] - 1) * 100 if closes[4] > 0 else 0
                # 个股涨>5% 但板块排名>25(倒数)
                if stock_5d > 5 and sector_rank > 25:
                    penalty -= 8
                    warnings.append(f"板块孤例: 个股+{stock_5d:.1f}%, 板块排名{sector_rank}/31 → 缺乏板块支撑")
                elif stock_5d > 3 and sector_rank > 20:
                    penalty -= 4
                    warnings.append(f"板块偏弱: 个股+{stock_5d:.1f}%, 板块第{sector_rank}名")
        except Exception:
            pass

        # ── 2. 尾盘拉升检测 (使用 minute_on_demand) ──
        try:
            from app.services.minute_on_demand import get_minute_bars

            bars = await get_minute_bars(symbol, period='5min', days=3, trade_date=scan_date)
            if len(bars) >= 30:
                # 排序
                bars.sort(key=lambda x: str(x["trade_time"]))
                # 计算全天涨幅
                day_open = float(bars[0]["open"])
                day_close = float(bars[-1]["close"])
                total_gain = (day_close - day_open) / max(day_open, 0.01) * 100
                # 尾盘最后6根(30分钟)
                tail_start = max(0, len(bars) - 7)
                tail_open = float(bars[tail_start]["open"])
                tail_gain = (day_close - tail_open) / max(tail_open, 0.01) * 100
                if total_gain > 1 and tail_gain > total_gain * 0.6:
                    penalty -= 5
                    warnings.append(f"尾盘做线: 全天涨{total_gain:.1f}%, 尾盘贡献{tail_gain:.1f}% → 日线是画出来的")
        except Exception:
            pass

        # ── 3. 小盘操纵风险 ──
        try:
            r = await s.execute(text("""
                SELECT circ_mv FROM daily_basic
                WHERE ts_code = :s ORDER BY trade_date DESC LIMIT 1
            """), {"s": symbol})
            row = r.fetchone()
            if row and row[0]:
                circ_mv = float(row[0]) / 10000  # 万元→亿元
                if circ_mv < 20:
                    # 查日振幅
                    r2 = await s.execute(text("""
                        SELECT high, low FROM daily_kline
                        WHERE ts_code = :s ORDER BY trade_date DESC LIMIT 5
                    """), {"s": symbol})
                    hl_rows = [(float(rr[0] or 0), float(rr[1] or 0)) for rr in r2.fetchall()]
                    if hl_rows:
                        avg_amp = sum((h - l) / max(l, 0.01) * 100 for h, l in hl_rows) / len(hl_rows)
                        if avg_amp > 5:
                            penalty -= 6
                            warnings.append(f"小盘操纵风险: 流通市值{circ_mv:.0f}亿, 日均振幅{avg_amp:.1f}% → 极易被操纵")
        except Exception:
            pass

        # ── 4. 反复推票检测 ──
        try:
            r = await s.execute(text("""
                SELECT COUNT(*),
                       COUNT(*) FILTER (WHERE was_profitable_5d = FALSE)
                FROM recommendation_tracking
                WHERE symbol = :s AND scan_date >= :cutoff
            """), {"s": symbol, "cutoff": scan_date - timedelta(days=30)})
            row = r.fetchone()
            if row and row[0] >= 2:
                total_recs = row[0]
                failed = row[1] or 0
                fail_rate = failed / total_recs
                if fail_rate > 0.5 and total_recs >= 3:
                    penalty -= 10
                    warnings.append(f"反复推票: 30天内推{total_recs}次, {failed}次未盈利 → 系统在重复犯错")
                elif total_recs >= 2 and failed >= 2:
                    penalty -= 6
                    warnings.append(f"重复亏损: {total_recs}次推荐均未盈利 → 谨慎")
        except Exception:
            pass

    severity = "high" if penalty <= -8 else ("medium" if penalty <= -4 else ("low" if penalty < 0 else "none"))

    return {
        "score_penalty": max(-15, penalty),
        "warnings": warnings,
        "severity": severity,
        "verdict": "; ".join(warnings) if warnings else "无显著反模式",
    }
