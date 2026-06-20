"""综合分析报告引擎 v1.0 — 个股结构 + 板块对比 + 大盘天时 + 综合裁决.

编排所有现有分析器, 生成多维度结构化报告.
纯规则引擎 (V1), 无 LLM 依赖.
"""
import asyncio
import logging
import json
import numpy as np
import pandas as pd
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tdx_functions import calc_rsi, EMA

logger = logging.getLogger("comprehensive")


def _sanitize(obj):
    """递归转换 numpy 类型为 Python 原生类型, 确保 JSON 可序列化."""
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return [_sanitize(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(x) for x in obj]
    return obj


async def analyze_comprehensive(ts_code: str) -> dict:
    """对单只股票生成完整的多维度综合分析报告.

    返回结构:
        {symbol, name, current_price,
         individual: {lock, veteran, chip, wave, tg_score, deep_score, kline_trend},
         sector: {sector_name, lifecycle, rank_5d, peers, peer_rank, leader},
         macro: {market_state, gate_config, timing_signals},
         verdict: {dili_score, tianshi_score, overall, action, watch_points, risk_note}}
    """
    code = ts_code.strip().upper()
    report = {"symbol": code, "name": code}

    # ─── 并发加载基础数据 ───
    daily_data, scan_data, deep_data, name_data = await asyncio.gather(
        _load_daily_kline(code),
        _load_scan_score(code),
        _load_deep_score(code),
        _load_name(code),
    )

    if not daily_data or len(daily_data["closes"]) < 60:
        return {"symbol": code, "error": "数据不足 (需≥60条日线)"}

    report["name"] = name_data or code
    report["current_price"] = round(float(daily_data["closes"][-1]), 2)

    # ★ 数据修复: 如果 scan_results 或 analysis_scores 数据不全, 实时计算
    if scan_data is None:
        scan_data = {}
    if deep_data is None:
        deep_data = {}

    # TG 字段为空 → 从日线实时推算
    if not scan_data.get("tg_momentum") or abs(scan_data.get("tg_momentum", 0)) < 0.01:
        _enrich_tg_from_kline(scan_data, daily_data)

    # dimension_scores 为空 → 从日线实时计算
    has_dims = deep_data.get("dimension_scores") is not None
    if not has_dims:
        _enrich_dims_from_kline(deep_data, daily_data, scan_data.get("market", "主板"))

    # ─── 并行分析 ───
    individual, sector, macro = await asyncio.gather(
        _analyze_individual(code, daily_data, scan_data, deep_data),
        _analyze_sector(code, daily_data, scan_data),
        _analyze_macro(),
    )

    report["individual"] = individual
    report["sector"] = sector
    report["macro"] = macro

    # ★ 老兵检测在 gather 外独立运行 (避免连接池竞争)
    try:
        from app.services.alphaflow_veteran import detect_veteran
        vet = await detect_veteran(code)
        if vet:
            report["individual"]["veteran"] = vet
            logger.info(f"Veteran detected for {code}: level={vet.get('level')}, score={vet.get('score')}")
    except Exception as e:
        logger.warning(f"Veteran detection failed for {code}: {e}")

    # ★ 波段预测也在 gather 外独立运行
    try:
        from app.services.wave_predictor import predict_wave_target
        cs = daily_data["closes"]; hs = daily_data["highs"]; ls = daily_data["lows"]
        cutoff = individual.get("lock", {}).get("ex_rights_cutoff", 0)
        cs_c = cs[cutoff:] if cutoff and cutoff > 0 else cs
        hs_c = hs[cutoff:] if cutoff and cutoff > 0 else hs
        ls_c = ls[cutoff:] if cutoff and cutoff > 0 else ls
        lock_cycles = _detect_lock_cycles(daily_data["dates"], cs_c, hs_c, ls_c)
        if lock_cycles and len(lock_cycles) >= 2:
            wave = await predict_wave_target(code, lock_cycles, float(cs[-1]))
            if wave and "error" not in wave:
                report["individual"]["wave"] = wave
                logger.info(f"Wave prediction for {code}: avg={wave.get('avg_wave_pct')}%")
    except Exception as e:
        logger.warning(f"Wave prediction failed for {code}: {e}")

    # ─── 综合裁决 (规则引擎) ───
    report["verdict"] = _compute_verdict(individual, sector, macro)

    return _sanitize(report)


# ═══════════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════════

async def _load_daily_kline(code: str) -> dict | None:
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, open, high, low, close, volume FROM daily_kline "
            "WHERE ts_code = :c ORDER BY trade_date"
        ), {"c": code})
        rows = list(r.fetchall())
    if len(rows) < 60:
        return None
    return {
        "dates": [rw[0] for rw in rows],
        "opens": np.array([float(rw[1] or 0) for rw in rows]),
        "highs": np.array([float(rw[2] or 0) for rw in rows]),
        "lows": np.array([float(rw[3] or 0) for rw in rows]),
        "closes": np.array([float(rw[4] or 0) for rw in rows]),
        "volumes": np.array([float(rw[5] or 0) for rw in rows]),
    }


async def _load_scan_score(code: str) -> dict | None:
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT level, composite_score, tg_momentum, close_price, market, "
            "j_value, vol_ratio, buy_strength, dist_low "
            "FROM scan_results WHERE symbol = :c ORDER BY scan_date DESC LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
    if not row:
        return None
    return {
        "level": row[0] or "",
        "composite_score": float(row[1] or 0),
        "tg_momentum": float(row[2] or 0),
        "close_price": float(row[3] or 0),
        "market": row[4] or "",
        "j_value": float(row[5] or 0) if row[5] else 0,
        "vol_ratio": float(row[6] or 0) if row[6] else 0,
        "buy_strength": float(row[7] or 0) if row[7] else 0,
        "dist_low": float(row[8] or 0) if row[8] else 0,
    }


async def _load_deep_score(code: str) -> dict | None:
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT composite_score, tech_score, kline_score, fund_score, "
            "sector_bonus, win_probability, signal_quality, trend_score, archetype, level, "
            "dimension_scores "
            "FROM analysis_scores WHERE symbol = :c ORDER BY scan_date DESC LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
    if not row:
        return None
    dims_raw = row[10]
    return {
        "composite_score": float(row[0] or 0),
        "tech_score": float(row[1] or 0),
        "kline_score": float(row[2] or 0),
        "fund_score": float(row[3] or 0),
        "sector_bonus": float(row[4] or 0),
        "win_probability": float(row[5] or 0) if row[5] is not None else None,
        "signal_quality": float(row[6] or 0) if row[6] is not None else None,
        "trend_score": int(row[7] or 0),
        "archetype": row[8] or "unknown",
        "level": row[9] or "",
        "dimension_scores": dims_raw if isinstance(dims_raw, dict) else (json.loads(dims_raw) if dims_raw else None),
    }


async def _load_name(code: str) -> str:
    async with async_session_factory() as s:
        # scan_results first
        r = await s.execute(text(
            "SELECT name FROM scan_results WHERE symbol = :c ORDER BY scan_date DESC LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
        if row and row[0]:
            return row[0]
        # fallback: stock_name_cache
        r = await s.execute(text(
            "SELECT name FROM stock_name_cache WHERE symbol = :c"
        ), {"c": code})
        row = r.fetchone()
        if row:
            return row[0]
    return code


# ═══════════════════════════════════════════════════════════
# 数据补全: 当DB数据不完整时, 从日线实时推算
# ═══════════════════════════════════════════════════════════

def _kdj(highs, lows, closes, n=9):
    """KDJ指标, 返回 (K, D, J)."""
    lowest_low = np.array([np.min(lows[max(0,i-n+1):i+1]) for i in range(len(lows))])
    highest_high = np.array([np.max(highs[max(0,i-n+1):i+1]) for i in range(len(highs))])
    rsv = np.where(highest_high != lowest_low,
                   (closes - lowest_low) / (highest_high - lowest_low) * 100, 50)
    k = EMA(pd.Series(rsv), 3).values
    d = EMA(pd.Series(k), 3).values
    j = 3 * k - 2 * d
    return k, d, j


def _enrich_tg_from_kline(scan: dict, daily: dict):
    """从日线数据实时推算TG指标, 补全 scan_results 缺失字段."""
    cs = daily["closes"]; hs = daily["highs"]; ls = daily["lows"]
    vs = daily.get("volumes", np.ones(len(cs)))
    n = len(cs)

    # 距低点距离 (30日)
    low30 = float(np.min(ls[-30:])) if n >= 30 else float(np.min(ls))
    dist_low = round((cs[-1] - low30) / low30 * 100, 1) if low30 > 0 else 0

    # KDJ J值
    _, _, j_arr = _kdj(hs, ls, cs)
    j_val = round(float(j_arr[-1]), 2) if len(j_arr) > 0 else 50

    # 量比: 5日均量 / 20日均量
    vol5 = float(np.mean(vs[-5:])) if n >= 5 else 0
    vol20 = float(np.mean(vs[-20:])) if n >= 20 else vol5
    vol_ratio = round(vol5 / vol20, 2) if vol20 > 0 else 1.0

    # 买入强度: 近5日阳线成交 / 总成交
    recent_opens = daily["opens"][-5:] if len(daily.get("opens", [])) >= 5 else cs[-5:]
    buy_vol = sum(float(vs[i]) for i in range(max(0, n-5), n) if cs[i] > (recent_opens[i - (n-5)] if i >= n-5 else cs[i-1]))
    total_vol5 = float(np.sum(vs[-5:]))
    buy_strength = round(buy_vol / total_vol5, 2) if total_vol5 > 0 else 0.5

    # TG动量: 简化版 (近5日涨幅 × 量比修正)
    chg5 = (cs[-1] / cs[-5] - 1) * 100 if n >= 5 and cs[-5] > 0 else 0
    tg_momentum = round(chg5 * 0.6 + (vol_ratio - 1) * 5, 2)

    # Level判定: 简化版
    if tg_momentum > 4: level = "L2"
    elif tg_momentum > 1.5: level = "L1"
    else: level = ""

    scan["tg_momentum"] = tg_momentum
    scan["j_value"] = j_val
    scan["vol_ratio"] = vol_ratio
    scan["buy_strength"] = buy_strength
    scan["dist_low"] = dist_low
    scan["level"] = level or scan.get("level", "")
    scan["_enriched"] = True


def _enrich_dims_from_kline(deep: dict, daily: dict, market: str = "主板"):
    """从日线数据实时计算14维基本评分, 补全 dimension_scores."""
    cs = daily["closes"]; hs = daily["highs"]; ls = daily["lows"]
    vs = daily.get("volumes", np.ones(len(cs)))
    n = len(cs)
    px = cs[-1]

    # MA计算
    ma5 = float(np.mean(cs[-5:])) if n >= 5 else px
    ma10 = float(np.mean(cs[-10:])) if n >= 10 else px
    ma20 = float(np.mean(cs[-20:])) if n >= 20 else px
    ma60 = float(np.mean(cs[-60:])) if n >= 60 else ma20
    dims = {}

    # 1. tech_score — RSI位置 (0-10)
    rsi14 = calc_rsi(pd.Series(cs), 14)
    rsi_v = float(rsi14.iloc[-1]) if len(rsi14) > 0 else 50
    # RSI 40-60 = 中性=5分, <30=超卖=8分, >70=超买=2分
    if rsi_v < 25: dims["tech_score"] = 8.0
    elif rsi_v < 35: dims["tech_score"] = 7.0
    elif rsi_v < 45: dims["tech_score"] = 6.0
    elif rsi_v < 55: dims["tech_score"] = 5.0
    elif rsi_v < 65: dims["tech_score"] = 4.0
    else: dims["tech_score"] = 2.0

    # 2. kline_score — 近5日形态 (0-10)
    up_days = sum(1 for i in range(max(1, n-5), n) if cs[i] > cs[i-1])
    dims["kline_score"] = round(up_days / 5 * 10, 1)

    # 3. fund_score — 量价配合 (0-10)
    vol_trend = float(np.mean(vs[-10:])) / max(float(np.mean(vs[-30:])), 1) if n >= 30 else 1
    dims["fund_score"] = round(min(10, max(0, vol_trend * 5 + (1 if cs[-1] > ma20 else -1))), 1)

    # 4. tg_momentum_score (0-10)
    chg5 = (cs[-1] / cs[-5] - 1) * 100 if n >= 5 and cs[-5] > 0 else 0
    # Use the tg_momentum score from scan or compute
    tg_norm = max(0, min(10, 5 + chg5 * 0.5))
    dims["tg_momentum_score"] = round(tg_norm, 1)

    # 5. vol_ratio_score (0-10)
    vol5 = float(np.mean(vs[-5:])) if n >= 5 else 1
    vol20_v = float(np.mean(vs[-20:])) if n >= 20 else vol5
    vr = vol5 / max(vol20_v, 1)
    dims["vol_ratio_score"] = round(min(10, max(0, vr * 5)), 1)

    # 6. arbr_score — 振幅情绪 (0-10)
    amp5 = (max(hs[-5:]) - min(ls[-5:])) / max(ls[-5:].min(), 0.01) * 100 if n >= 5 else 0
    dims["arbr_score"] = round(min(10, max(0, 5 - abs(amp5 - 5) * 0.5)), 1)

    # 7. market_relative_score — 与已知数据一致 (0-10)
    dims["market_relative_score"] = 5.0  # 无大盘索引时默认中性

    # 8. valuation_score (0-10, 中性)
    dims["valuation_score"] = 5.0

    # 9. ma_trend_score — 均线多头排列
    ma_score = 0.0
    if px > ma5: ma_score += 2
    if px > ma10: ma_score += 2
    if px > ma20: ma_score += 2
    if px > ma60: ma_score += 1
    if ma5 > ma10: ma_score += 1
    if ma10 > ma20: ma_score += 1
    if ma20 > ma60: ma_score += 1
    dims["ma_trend_score"] = round(ma_score, 1)

    # 10. pattern_score (0-10)
    dims["pattern_score"] = 5.0

    # 11. trend_deviation_score — 乖离率
    dev = abs(px - ma20) / ma20 * 100 if ma20 > 0 else 0
    # 乖离<3%=5分, <5%=4分, >10%=1分
    if dev < 3: dims["trend_deviation_score"] = 6.0
    elif dev < 5: dims["trend_deviation_score"] = 5.0
    elif dev < 8: dims["trend_deviation_score"] = 4.0
    elif dev < 12: dims["trend_deviation_score"] = 3.0
    else: dims["trend_deviation_score"] = 1.0

    # 12. bbi_score (0-10)
    dims["bbi_score"] = 5.0

    # 13. box_score (0-10)
    amp20 = (max(hs[-20:]) - min(ls[-20:])) / max(ls[-20:].min(), 0.01) * 100 if n >= 20 else 0
    # 箱体清晰度: 振幅适中=高分
    if 5 <= amp20 <= 20: dims["box_score"] = 7.0
    elif amp20 <= 30: dims["box_score"] = 5.0
    else: dims["box_score"] = 3.0

    # 14. ambush_score (0-10)
    dims["ambush_score"] = 3.0

    # === v7.0.32: 新增 5 维技术因子评分 ===
    # 15. macd_score (0-10): MACD 多头加分, 空头减分
    macd_dif = deep.get("macd_dif")
    macd_dea = deep.get("macd_dea")
    if macd_dif is not None and macd_dea is not None:
        if macd_dif > 0 and macd_dea > 0:
            dims["macd_score"] = 8.0  # 双多头
        elif macd_dif > macd_dea:
            dims["macd_score"] = 6.0  # 金叉中
        elif macd_dif < 0 and macd_dea < 0:
            dims["macd_score"] = 2.0  # 双空头
        else:
            dims["macd_score"] = 4.0  # 死叉中
    else:
        dims["macd_score"] = 5.0

    # 16. kdj_score (0-10): J 值超卖加分, 超买减分
    kdj_j = deep.get("kdj_j")
    if kdj_j is not None:
        if kdj_j < 20: dims["kdj_score"] = 9.0  # 严重超卖
        elif kdj_j < 40: dims["kdj_score"] = 7.5  # 超卖
        elif kdj_j < 60: dims["kdj_score"] = 6.0  # 偏弱
        elif kdj_j < 80: dims["kdj_score"] = 5.0  # 中性
        elif kdj_j < 100: dims["kdj_score"] = 3.0  # 偏强
        else: dims["kdj_score"] = 1.5  # 严重超买
    else:
        dims["kdj_score"] = 5.0

    # 17. boll_score (0-10): BOLL 位置 0.3-0.7 给高分
    boll_pos = deep.get("boll_pos")
    if boll_pos is not None:
        if 0.3 <= boll_pos <= 0.7: dims["boll_score"] = 8.0  # 中部
        elif 0.1 <= boll_pos < 0.3: dims["boll_score"] = 6.0  # 偏低
        elif 0.7 < boll_pos <= 0.9: dims["boll_score"] = 5.0  # 偏高
        elif boll_pos < 0.1: dims["boll_score"] = 4.0  # 触底
        else: dims["boll_score"] = 3.0  # 触顶
    else:
        dims["boll_score"] = 5.0

    # 18. cci_score (0-10): CCI 在 -100 ~ 100 区间给高分
    cci_val = deep.get("cci")
    if cci_val is not None:
        if -100 <= cci_val <= 100: dims["cci_score"] = 7.0  # 正常区间
        elif -200 <= cci_val < -100: dims["cci_score"] = 5.0  # 弱超卖
        elif 100 < cci_val <= 200: dims["cci_score"] = 4.0  # 弱超买
        elif cci_val < -200: dims["cci_score"] = 6.0  # 强超卖
        else: dims["cci_score"] = 2.0  # 强超买
    else:
        dims["cci_score"] = 5.0

    # 19. chip_score (0-10): 筹码成本适中 + 获利盘
    cost_50 = deep.get("cost_50pct")
    winner_rate = deep.get("winner_rate")
    if cost_50 is not None and winner_rate is not None:
        score = 5.0
        if 5 < cost_50 < 100: score += 1.5  # 成本适中
        if 30 < cost_50 < 80: score += 1.0  # 成本更佳
        if winner_rate > 50: score += 1.5  # 多数获利
        if winner_rate > 80: score += 0.5  # 极度获利
        if cost_50 < 3: score -= 2.0  # 成本过低 (无主力)
        dims["chip_score"] = max(0, min(10, score))
    else:
        dims["chip_score"] = 5.0

    # 简易 composite: 19维加权平均 (v7.0.32: 14+5)
    weights = {
        "tech_score": 2.5, "kline_score": 2.5, "fund_score": 2.0,
        "tg_momentum_score": 2.5, "vol_ratio_score": 2.0, "arbr_score": 1.5,
        "market_relative_score": 1.5, "valuation_score": 1.0,
        "ma_trend_score": 1.0, "pattern_score": 1.5,
        "trend_deviation_score": 1.5, "bbi_score": 1.5,
        "box_score": 2.0, "ambush_score": 1.5,
        # v7.0.32 新增 5 维
        "macd_score": 2.0, "kdj_score": 1.5, "boll_score": 1.0,
        "cci_score": 0.5, "chip_score": 2.0,
    }
    total_w = sum(weights.values())
    weighted_sum = sum(dims.get(k, 5) * w for k, w in weights.items())
    estimated_composite = round(weighted_sum / total_w * 10, 1)

    deep["composite_score"] = deep.get("composite_score") or estimated_composite
    deep["tech_score"] = deep.get("tech_score") or dims.get("tech_score", 5)
    deep["kline_score"] = deep.get("kline_score") or dims.get("kline_score", 5)
    deep["fund_score"] = deep.get("fund_score") or dims.get("fund_score", 5)
    # v7.0.32: 5 维新评分也写回 deep (后续落库)
    for new_dim in ["macd_score", "kdj_score", "boll_score", "cci_score", "chip_score"]:
        deep[new_dim] = deep.get(new_dim) or dims.get(new_dim, 5)
    deep["dimension_scores"] = dims
    deep["archetype"] = deep.get("archetype") or "large_bluechip"
    deep["win_probability"] = deep.get("win_probability") or round(min(0.65, max(0.10, estimated_composite / 100)), 3)
    deep["_enriched"] = True


# ═══════════════════════════════════════════════════════════
# 一、个股结构分析
# ═══════════════════════════════════════════════════════════

async def _analyze_individual(code: str, daily: dict, scan: dict | None, deep: dict | None) -> dict:
    result = {}

    cs = daily["closes"]; hs = daily["highs"]; ls = daily["lows"]; n = len(cs)

    # 大盘指数
    idx_code = '399006.SZ' if (code.startswith('300') or code.startswith('301') or code.startswith('688')) else '700001.TI'
    idx_closes = await _load_index_closes(idx_code, n)

    # 1. 锁死检测
    try:
        from app.services.lock_detector import detect_lock_simple
        lock = detect_lock_simple(cs, hs, ls, idx_closes)
        result["lock"] = {
            "in_lock": bool(lock["in_lock"]),
            "amplitude_short_15d": lock.get("amplitude_short_15d", 0),
            "amplitude_long_40d": lock.get("amplitude_long_40d", 0),
            "amplitude_30d": lock.get("amplitude_30d", 0),
            "lock_days": lock.get("lock_days", 0),
            "relative_strength": lock.get("relative_strength", 0),
            "verdict": lock.get("verdict", ""),
            "lock_reason": lock.get("reason", ""),
            "ex_rights_cutoff": cutoff,
        }
    except Exception as e:
        logger.warning(f"Lock detection failed for {code}: {e}")
        result["lock"] = {"error": str(e)}

    # 2. (老兵检测移到主函数, 避免 gather 内连接池竞争)

    # 3. 筹码吸收
    try:
        from app.services.chip_analyzer import analyze_chip_absorption
        l30 = float(np.min(ls[-30:])) if n >= 30 else float(cs[-1]) * 0.9
        h30 = float(np.max(hs[-30:])) if n >= 30 else float(cs[-1]) * 1.1
        chip = await analyze_chip_absorption(code, lock_bottom=l30, lock_top=h30)
        if chip and "error" not in chip:
            result["chip"] = chip
    except Exception as e:
        logger.warning(f"Chip analysis failed for {code}: {e}")

    # 4. (波段预测移到主函数, 避免 gather 内连接池竞争)

    # 5. TG 评分
    result["tg_score"] = scan or {"note": "未通过TG扫描"}

    # 6. 深度评分
    result["deep_score"] = deep or {"note": "未进入深度评分"}

    # 7. K线趋势 (自算)
    result["kline_trend"] = _compute_kline_trend(cs)

    return result


def _compute_kline_trend(closes: np.ndarray) -> dict:
    n = len(closes)
    ma5 = float(np.mean(closes[-5:])) if n >= 5 else 0
    ma10 = float(np.mean(closes[-10:])) if n >= 10 else 0
    ma20 = float(np.mean(closes[-20:])) if n >= 20 else 0
    chg5 = (closes[-1] / closes[-5] - 1) * 100 if n >= 5 else 0
    chg10 = (closes[-1] / closes[-10] - 1) * 100 if n >= 10 else 0
    chg20 = (closes[-1] / closes[-20] - 1) * 100 if n >= 20 else 0
    return {
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "chg_5d": round(chg5, 1), "chg_10d": round(chg10, 1), "chg_20d": round(chg20, 1),
        "above_ma5": bool(closes[-1] > ma5), "above_ma10": bool(closes[-1] > ma10), "above_ma20": bool(closes[-1] > ma20),
    }


def _detect_lock_cycles(dates, closes, highs, lows) -> list[dict]:
    """双窗口锁周期检测 (复用 lock-detail API 的逻辑)."""
    n = len(closes)
    # dates is a list of date objects
    has_dates = dates is not None and len(dates) >= n
    cycles = []
    i = 0
    while i < n - 20:
        w20_l = min(lows[i:i+20]); w20_h = max(highs[i:i+20])
        w20_lv = float(w20_l); w20_hv = float(w20_h)
        w20_amp = (w20_hv - w20_lv) / w20_lv * 100 if w20_lv > 0 else 100
        if w20_amp <= 15.0:
            start = i; lh = w20_hv; ll = w20_lv
            while i < n - 1:
                i += 1
                if i + 10 > n: break
                lh = max(lh, float(highs[i])); ll = min(ll, float(lows[i]))
                seg_len = i - start + 1
                if seg_len <= 20:
                    amp = (lh - ll) / ll * 100 if ll > 0 else 100
                    if amp > 15.0: break
                else:
                    if seg_len >= 40:
                        recent_20_l = min(lows[i-19:i+1]); recent_20_h = max(highs[i-19:i+1])
                        long_l = min(ll, float(recent_20_l)); long_h = max(lh, float(recent_20_h))
                        long_amp = (long_h - long_l) / long_l * 100 if long_l > 0 else 100
                        if long_amp > 17.0: break
                        lh = long_h; ll = long_l
            seg_len = i - start + 1
            if seg_len >= 15:
                start_date = dates[start] if has_dates and start < len(dates) else start
                end_date = dates[min(i, n-1)] if has_dates and min(i, n-1) < len(dates) else i
                cycles.append({
                    "n": len(cycles) + 1,
                    "start": str(start_date),
                    "end": str(end_date),
                    "days": seg_len,
                    "high": round(lh, 2), "low": round(ll, 2),
                    "mid": round((lh + ll) / 2, 2),
                    "amp": round((lh - ll) / ll * 100, 1) if ll > 0 else 0,
                })
        else:
            i += 1
    return cycles


async def _load_index_closes(idx_code: str, n: int) -> np.ndarray:
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT close FROM daily_kline WHERE ts_code = :c ORDER BY trade_date DESC LIMIT :lim"
        ), {"c": idx_code, "lim": n + 10})
        rows = [float(rw[0] or 0) for rw in r.fetchall()]
    rows.reverse()
    if len(rows) >= n:
        return np.array(rows[-n:])
    return np.zeros(n)


# ═══════════════════════════════════════════════════════════
# 二、板块横向对比
# ═══════════════════════════════════════════════════════════

async def _analyze_sector(code: str, daily: dict, scan: dict | None) -> dict:
    result = {"sector_name": "未知", "peers": [], "peer_rank": "-"}

    # 1. 确定行业 — 优先级: ths_member > scan_results.industry
    sector_name = await _get_sector_for_stock(code)
    if not sector_name and scan and scan.get("market"):
        sector_name = scan["market"]  # fallback to market type
    result["sector_name"] = sector_name or "未知"

    # 2. 获取同行列表
    peer_symbols = await _get_peer_stocks(code, sector_name) if sector_name else []
    if not peer_symbols:
        return result

    # 3. 同行K线涨跌幅 + TG 评分
    cs = daily["closes"]; n = len(cs)
    self_chg5 = (cs[-1] / cs[-5] - 1) * 100 if n >= 5 else 0
    self_chg20 = (cs[-1] / cs[-20] - 1) * 100 if n >= 20 else 0

    peer_data = await _load_peer_data(peer_symbols[:15])  # 最多15只同行
    peers_sorted = sorted(peer_data, key=lambda p: p.get("chg_5d", -999), reverse=True)

    # 排名
    all_chg5 = [p.get("chg_5d", -999) for p in peers_sorted] + [self_chg5]
    all_chg5.sort(reverse=True)
    try:
        rank = all_chg5.index(self_chg5) + 1
    except ValueError:
        rank = len(all_chg5)
    result["peer_rank"] = f"{rank}/{len(all_chg5)}"
    result["peers"] = peers_sorted[:10]  # TOP 10
    result["leader"] = peers_sorted[0]["symbol"] if peers_sorted else ""

    # 4. 板块生命周期 + 申万排名 (通过 sector_heat_engine)
    try:
        from app.services.sector_heat_engine import get_stock_sector_factor
        sf = await get_stock_sector_factor(code)
        result["lifecycle"] = sf.get("lifecycle_stage", "未知")
        result["sector_rank_5d"] = sf.get("sector_rank_5d", 99)
        result["sector_pct_5d"] = sf.get("sector_pct_5d", 0)
        result["sector_pct_20d"] = sf.get("sector_pct_20d", 0)
        result["sector_code"] = sf.get("sector_code", "")
    except Exception:
        result["lifecycle"] = "未知"
        result["sector_rank_5d"] = 99

    # 5. 板块共振度: 同行中正涨幅的占比
    positive_peers = sum(1 for p in peers_sorted if p.get("chg_5d", -999) > 0)
    result["resonance_pct"] = round(positive_peers / max(len(peers_sorted), 1) * 100, 0)

    return result


async def _get_sector_for_stock(code: str) -> str | None:
    """获取股票的行业分类."""
    async with async_session_factory() as s:
        # 1. ths_member (同花顺行业) — 列名是 ths_name
        r = await s.execute(text(
            "SELECT ths_name FROM ths_member WHERE ts_code = :c AND out_date IS NULL LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
        if row and row[0]:
            return row[0]
        # 2. scan_results.industry
        r = await s.execute(text(
            "SELECT industry FROM scan_results WHERE symbol = :c AND industry IS NOT NULL AND industry != '' ORDER BY scan_date DESC LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
        if row and row[0]:
            return row[0]
    return None


async def _get_peer_stocks(code: str, sector_name: str) -> list[str]:
    """获取同行业股票列表."""
    async with async_session_factory() as s:
        # 1. 通过 ths_member 查找同行业
        r = await s.execute(text(
            "SELECT ts_code FROM ths_member WHERE ths_name = :sn AND out_date IS NULL AND ts_code != :c LIMIT 20"
        ), {"sn": sector_name, "c": code})
        peers = [row[0] for row in r.fetchall()]
        if peers:
            return peers

        # 2. fallback: 从名称提取关键词搜索 (如 "佛燃能源" → "能源")
        r = await s.execute(text(
            "SELECT name FROM scan_results WHERE symbol = :c ORDER BY scan_date DESC LIMIT 1"
        ), {"c": code})
        row = r.fetchone()
        name = row[0] if row else ""
        if name and len(name) >= 2:
            # 取名称最后2-3个字作为行业关键词
            for kw in [name[-2:], name[-3:], name[:2]]:
                if len(kw) < 2: continue
                r = await s.execute(text(
                    "SELECT DISTINCT symbol FROM scan_results "
                    "WHERE name LIKE :kw AND symbol != :c AND scan_date = (SELECT MAX(scan_date) FROM scan_results) "
                    "LIMIT 20"
                ), {"kw": f"%{kw}%", "c": code})
                peers = [r2[0] for r2 in r.fetchall()]
                if len(peers) >= 3:
                    return peers
        return []


async def _load_peer_data(peer_symbols: list[str]) -> list[dict]:
    """批量加载同行股票的 TG 评分和 K 线涨跌."""
    if not peer_symbols:
        return []
    async with async_session_factory() as s:
        # 最新 scan_results
        r = await s.execute(text(
            "SELECT symbol, name, composite_score, tg_momentum, level, market "
            "FROM scan_results WHERE symbol = ANY(:syms) "
            "AND scan_date = (SELECT MAX(scan_date) FROM scan_results)"
        ), {"syms": peer_symbols})
        scan_map = {}
        for row in r.fetchall():
            scan_map[row[0]] = {
                "name": row[1] or row[0],
                "composite_score": float(row[2] or 0),
                "tg_momentum": float(row[3] or 0),
                "level": row[4] or "",
                "market": row[5] or "",
            }

        # K线最近5/20日收盘 (逐只查询, 避免超大结果集)
        result = []
        for sym in peer_symbols[:15]:
            r2 = await s.execute(text(
                "SELECT close FROM daily_kline WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 20"
            ), {"c": sym})
            rows = [float(rw[0] or 0) for rw in r2.fetchall()]
            chg5 = (rows[0] / rows[4] - 1) * 100 if len(rows) >= 5 else 0
            chg20 = (rows[0] / rows[-1] - 1) * 100 if len(rows) >= 20 else 0
            si = scan_map.get(sym, {})
            result.append({
                "symbol": sym,
                "name": si.get("name", sym),
                "market": si.get("market", ""),
                "composite_score": si.get("composite_score", 0),
                "tg_momentum": si.get("tg_momentum", 0),
                "level": si.get("level", ""),
                "chg_5d": round(chg5, 1),
                "chg_20d": round(chg20, 1),
            })
        return result


# ═══════════════════════════════════════════════════════════
# 三、大盘天时
# ═══════════════════════════════════════════════════════════

async def _analyze_macro() -> dict:
    result = {}
    # 1. 市场状态
    try:
        from app.services.market_gate import get_market_state, get_gate_config
        ms = await get_market_state()
        gc = await get_gate_config()
        result["market_state"] = ms
        result["gate_config"] = gc
    except Exception as e:
        logger.warning(f"Market state failed: {e}")
        result["market_state"] = {"error": str(e), "regime": "未知", "risk": "unknown"}
        result["gate_config"] = {}

    # 2. 天时信号 (规则引擎)
    result["timing_signals"] = _compute_timing_signals(result.get("market_state", {}))

    return result


def _compute_timing_signals(ms: dict) -> list[dict]:
    """规则引擎: 5个天时信号, 每个 0/1 判定."""
    signals = []

    regime = ms.get("regime", "未知")
    risk = ms.get("risk", "unknown")
    breadth = ms.get("breadth", {})
    volume = ms.get("volume_trend", {})
    style = ms.get("style", {})

    adv_pct = breadth.get("advance_pct", 50)
    shrink_pct = volume.get("shrink_pct", 0)
    csi1000_5d = style.get("csi1000_5d", 0)
    bias = style.get("bias", "unknown")

    # 信号1: 大盘趋势 — 非恐慌/弱势
    good_regimes = ["趋势上涨", "结构行情", "震荡整理", "维稳行情"]
    s1_pass = regime in good_regimes and risk != "high"
    signals.append({
        "signal": "大盘环境健康",
        "pass": s1_pass,
        "detail": f"体制: {regime}(风险:{risk}) | 上证20日涨跌:{ms.get('chg_20d',0):+.1f}%",
        "weight": 25,
    })

    # 信号2: 市场宽度 — 涨跌比 > 45%
    s2_pass = adv_pct > 45
    signals.append({
        "signal": "市场宽度正常",
        "pass": s2_pass,
        "detail": f"上涨占比: {adv_pct}% {'✓' if s2_pass else '(偏空, 需>45%)'}",
        "weight": 20,
    })

    # 信号3: 成交额 — 未显著萎缩
    s3_pass = shrink_pct > -15
    signals.append({
        "signal": "成交额健康",
        "pass": s3_pass,
        "detail": f"成交额变化: {shrink_pct:+.0f}% (近5日均量vs前5日) {'✓' if s3_pass else '(萎缩超15%)'}",
        "weight": 20,
    })

    # 信号4: 风格匹配 — 不是小票屠杀
    s4_pass = bias != "small_cap" or csi1000_5d > -2
    signals.append({
        "signal": "风格无极端偏向",
        "pass": s4_pass,
        "detail": f"风格: {bias} | 中证1000 5日:{csi1000_5d:+.1f}% {'✓' if s4_pass else '(小票遭重锤)'}",
        "weight": 15,
    })

    # 信号5: 板块共振 — 有领涨板块
    suitable = ms.get("suitable_strategies", [])
    s5_pass = len(suitable) > 0 and suitable[0] != "保守观望"
    signals.append({
        "signal": "存在可操作策略",
        "pass": s5_pass,
        "detail": f"推荐策略: {', '.join(suitable[:3]) if suitable else '无'}",
        "weight": 20,
    })

    return signals


# ═══════════════════════════════════════════════════════════
# 四、综合裁决
# ═══════════════════════════════════════════════════════════

def _compute_verdict(individual: dict, sector: dict, macro: dict) -> dict:
    """规则引擎综合评分."""

    # ── 地利分 (个股结构, 0-100) ──
    dili = 0
    lock = individual.get("lock", {}); vet = individual.get("veteran")
    chip = individual.get("chip", {}); wave = individual.get("wave")
    ab = chip.get("absorption", {}); ar = ab.get("ar_ratio", 0)
    kt = individual.get("kline_trend", {})

    # 1. 锁死质量 (30分) — 核心指标
    amp15 = lock.get("amplitude_short_15d", 100)
    amp40 = lock.get("amplitude_long_40d", 100)
    if lock.get("in_lock"):
        dili += 22
        if amp15 <= 10: dili += 8   # 极紧锁死
        elif amp15 <= 12: dili += 4
        elif amp15 <= 15: dili += 2
    elif amp15 <= 10 and amp40 > 17:
        # 虽非严格锁死但短窗极紧 (40d被历史大爆发污染)
        dili += 16
        if amp15 <= 8: dili += 6
    elif amp15 <= 12:
        dili += 10
    elif amp15 <= 15:
        dili += 5

    # 2. 筹码吸收 (25分)
    if ar > 0.75: dili += 22
    elif ar > 0.6: dili += 18
    elif ar > 0.5: dili += 12
    elif ar > 0.3: dili += 6
    if ab.get("quality", 0) >= 8: dili += 3
    if ab.get("trend", "") == "加速吸收": dili += 2

    # 3. 振幅收敛 (15分) — 从 lock 数据推断
    # early vs recent amplitude trend
    if amp15 <= 10: dili += 8  # 当前极紧
    elif amp15 <= 12: dili += 5
    if kt.get("chg_20d", 0) < 0: dili += 3  # 回调中 (在筑底)

    # 4. 老兵 (15分) — 如果有检测结果
    if vet:
        dili += 8
        level = vet.get("level", "")
        if level == "pre_breakout": dili += 7
        elif level == "late_stage": dili += 4
        elif level == "monitoring": dili += 2
        cycles = vet.get("total_cycles", 0)
        if cycles >= 6: dili += 3
        elif cycles >= 4: dili += 1
    elif ar > 0.7 and amp15 <= 12:
        # 老兵未检出但结构特征符合: 高吸收+紧振幅 = 大概率老兵
        dili += 6

    # 5. 波段潜力 (10分)
    if wave:
        avg_pct = wave.get("avg_wave_pct", 0)
        if avg_pct > 30: dili += 8
        elif avg_pct > 20: dili += 6
        elif avg_pct > 10: dili += 3

    # 6. 价格位置 (5分)
    chg20 = kt.get("chg_20d", 0)
    if -15 <= chg20 <= -5: dili += 5  # 充分回调, 最佳位置
    elif -5 < chg20 <= 0: dili += 3  # 温和回调
    elif chg20 < -15: dili += 1  # 跌太深

    dili = min(100, dili)

    # ── 天时分 (外部环境, 0-100) ──
    timing_signals = macro.get("timing_signals", [])
    tianshi = sum(s["weight"] for s in timing_signals if s["pass"])
    tianshi = min(100, tianshi)

    # ── 整体判定 ──
    if dili >= 70 and tianshi >= 60:
        overall = "地利+天时兼备"
        action = "可积极介入"
    elif dili >= 60 and tianshi >= 40:
        overall = "地利充足, 天时可期"
        action = "适度参与"
    elif dili >= 60 and tianshi >= 20:
        overall = "地利充足, 天时不足"
        action = "观察等待(重点监控)"
    elif dili >= 40 and tianshi >= 50:
        overall = "结构尚可, 环境一般"
        action = "轻仓试探"
    elif tianshi >= 70:
        overall = "天时有利, 个股偏弱"
        action = "等待个股信号改善"
    elif tianshi < 20:
        overall = "天时极差, 不宜操作"
        action = "空仓观望"
    else:
        overall = "地利天时均需改善"
        action = "继续观察"

    # ── 关注点 ──
    watch_points = []
    ms = macro.get("market_state", {})
    if ms.get("regime", "") in ["弱势探底", "恐慌杀跌"]:
        watch_points.append(f"大盘脱离{ms.get('regime','')}体制 (当前涨跌比{ms.get('breadth',{}).get('advance_pct','?')}%)")
    vt = ms.get("volume_trend", {})
    if vt.get("shrink_pct", 0) < -15:
        watch_points.append(f"成交额萎缩见底回升 (当前{vt.get('shrink_pct',0):+.0f}%)")
    st = ms.get("style", {})
    if st.get("csi1000_5d", 0) < -2:
        watch_points.append(f"中证1000止跌转强 (当前5日{st.get('csi1000_5d',0):+.1f}%)")
    if not kt.get("above_ma5", False):
        watch_points.append(f"个股放量站回MA5({kt.get('ma5','?')})")

    # ── 风险提示 ──
    risk_parts = []
    if ms.get("risk") == "high":
        risk_parts.append(f"市场风险等级: HIGH ({ms.get('regime','')})")
    if ar < 0.4:
        risk_parts.append(f"筹码吸收率仅{ar*100:.0f}%, 上方套牢盘重")
    if lock.get("in_lock") is False:
        risk_parts.append("当前未处于严格锁死状态")

    return {
        "dili_score": dili,
        "tianshi_score": tianshi,
        "overall": overall,
        "action": action,
        "watch_points": watch_points,
        "risk_note": "; ".join(risk_parts) if risk_parts else "无明显结构性风险",
    }
