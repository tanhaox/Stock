"""Deep scorer — 14-dimension composite scoring pipeline (v4.4).

Phase 14: 5-phase pipeline decomposition.
Phases: preload → score → enrich → normalize → persist
"""
import json as _json
import logging
import asyncio
import numpy as np
from datetime import date as dt_date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.utils.numpy_utils import sanitize_for_json

logger = logging.getLogger(__name__)

# ═══════════ Re-exports from scoring modules ═══════════
from app.scoring.technical_scorers import (
    score_technical, score_kline_game, score_vol_ratio, score_arbr,
    score_bbi, score_trend_deviation, score_downside_risk, score_weekly_resonance,
)
from app.scoring.fundamental_scorers import (
    score_sector_alpha, score_toplist_sector, score_market_relative,
    score_fund_flow, score_valuation,
    get_fundamental_score, preload_fundamental_scores, get_fundamental_score_cached,
    FUNDA_RULES,
)
from app.scoring.structure_scorers import (
    score_ma_trend, score_pattern_signal, score_multi_box,
)

# ═══════════ Constants ═══════════
DEFAULT_WEIGHTS = {
    "tech_weight": 3.0, "kline_weight": 3.0, "fund_weight": 2.5,
    "tg_momentum_weight": 2.5, "vol_ratio_weight": 2.0, "arbr_weight": 1.5,
    "sector_alpha_weight": 1.5, "market_relative_weight": 1.5,
    "valuation_weight": 1.0,
    "ma_trend_weight": 1.5, "pattern_weight": 1.5,
    "trend_deviation_weight": 1.5, "bbi_weight": 1.5, "box_weight": 2.0,
    "fundamentals_weight": 1.5,
    # Phase 73: 之前遗漏的子维度 — 已在 _deep_score_phase 计算, 补入加权和
    "dist_low_weight": 1.0, "j_value_weight": 1.5,
    "downside_risk_weight": 1.0,
    # v4.8: 筹码维度 (Tushare cyq_perf)
    "chip_winner_weight": 2.0, "chip_cost_weight": 1.5,
    # Phase 73: 之前硬编码 0.5 的 extra dimensions → 可训练
    "weekly_resonance_weight": 0.5,
    "toplist_sector_weight": 0.5,
    "ambush_weight": 0.5,
    "sector_bonus_l2": 0.5, "sector_bonus_l3": 1.5,

    # ── M-4: 三级宏观权重 (Tier1 大盘14 + Tier2 板块18 + Tier3 个股11) ──
    # Tier 1: 大盘级宏观
    "macro_m2_yoy": 1.0, "macro_m1_m2_scissor": 0.5,
    "macro_shibor_spread": 1.0, "macro_shibor_3m_chg": 0.5,
    "macro_lpr_1y": 0.5, "macro_lpr_5y": 0.3,
    "macro_bond_3m_yield": 0.5, "macro_bond_10y_yield": 0.5,
    "macro_pmi_manufacturing": 1.0, "macro_pmi_new_order": 1.0,
    "macro_pmi_export_order": 0.5,
    "macro_cpi_ppi_gap": 0.5, "macro_ppi_mom": 0.5, "macro_gdp_gap": 0.3,
    # Tier 2: 板块级 (商品+行业+概念)
    "macro_crude_oil": 1.0, "macro_copper": 1.0, "macro_aluminum": 0.8,
    "macro_rebar": 0.8, "macro_iron_ore": 0.5, "macro_coke_coal": 0.5,
    "macro_lithium": 1.0, "macro_silicon": 0.8,
    "macro_gold": 0.8, "macro_natural_rubber": 0.5,
    "macro_methanol": 0.5, "macro_pvc": 0.5,
    "macro_sector_sw_5d": 1.0, "macro_sector_sw_20d": 0.8,
    "macro_concept_chg_5d": 1.0, "macro_concept_chg_20d": 0.8,
    "macro_sector_turnover": 0.5, "macro_concept_rank": 0.3,
    # Tier 3: 个股级
    "macro_big_order_net": 0.8, "macro_margin_balance_chg": 0.5,
    "macro_north_hold_chg": 0.8, "macro_north_hold_ratio": 0.5,
    "macro_block_trade_premium": 0.3, "macro_pledge_ratio": 0.3,
    "macro_roe": 1.0, "macro_roe_yoy": 0.8,
    "macro_revenue_yoy": 0.5, "macro_gross_margin": 0.5,
    "macro_forecast_surprise": 0.5,
}

# P2-2: regime 混合比例配置 (可调整 regime 权重对原型权重的影响程度)
REGIME_BLEND_CONFIG = {
    "blend_ratio": 0.5,      # regime 权重混入比例 (0.0=全原型, 1.0=全regime)
    "enabled": True,          # 是否启用 regime 混合
}

# P2-3: regime 权重激活阈值配置 (来自 scoring_trainer.OVERFIT_THRESHOLD_CONFIG)
REGIME_ACTIVATION_CONFIG = {
    "min_samples": 50,        # 最小样本数
    "min_params": 10,         # 最小参数数
    "min_auc": 0.55,          # 最小 AUC
}

# ── Notebook Optimization: 批量处理限制 ──
BATCH_CONFIG = {
    "max_stocks_per_batch": 100,   # 单批次最大股票数
    "kline_days": 120,             # K线历史天数（降低以节省内存）
}

_ARCH_RULES = {
    "large_bluechip": ["银行","保险","证券","金融","信托","白酒","食品","饮料","家电"],
    "growth_tech": ["半导体","芯片","元器件","通信","计算机设备","机械","机器人","光刻","PCB","制药","生物","医疗","医药","中药","创新药","CRO","器械","软件","互联网","IT服务","电信","传媒","游戏","数据","AI"],
    "cyclical_resource": ["石油","煤炭","有色","钢铁","化工","化纤","造纸","稀土","锂"],
    "value_defensive": ["电力","水务","供气","路桥","港口","房产","建筑","建材","环保","家居","纺织","旅游","酒店","农林牧渔","农业","乳业","食品","饮料"],
    "small_speculative": ["综合"],
}


def _arch_for_industry(industry_name: str) -> str:
    """Map industry name to archetype."""
    if not industry_name:
        return "small_speculative"
    for arch, keywords in _ARCH_RULES.items():
        for kw in keywords:
            if kw in (industry_name or ""):
                return arch
    return "small_speculative"


def _derive_strategy_label(r: dict) -> str | None:
    """从原型推导 strategy_label (S1/S2/S3)."""
    arch = r.get("archetype", "")
    if not arch:
        return None
    if "bluechip" in arch or "defensive" in arch:
        return "S1"
    if "growth" in arch or "cyclical" in arch:
        return "S2"
    if "speculative" in arch or "small" in arch:
        return "S3"
    return None


def _apply_hard_rules(r: dict) -> tuple[list, list]:
    """v7.0.30 (铁三角实测校准): 5 条死规则过滤假信号.

    v7.0.30 校准 (基于 1915 行 verified_5d 实测 2026-06-18):
      - R2 bias 阈值 -5% → -3% (实测更宽阈值,期望值提升 +239%)
      - R3 bias 阈值 +5% → +10% (实测更宽阈值,胜率 -8.5%)
      - R2/R3 对 archetype in ('value_defensive', 'cyclical_resource') 跳过
        (周期股/价值股本来就在 MA20 下方操作,超跌反弹是机会,规则会误杀)
      - R1/R4/R5 不变

    数据基础 (实测 v7.0.30 验证):
      单规则期望值差 (剔 1 票 vs 留 1 票):
        R1 mcap<50亿:        +197.5%
        R2 bias<-3%:         +239.7% ⭐⭐
        R3 bias>10%:          +71.2%
        R4 RSI>70:           +260.8% ⭐⭐⭐ (黄金规则)
        R5 MA严格空头:        +152.7% ⭐⭐
      累积 5 条 AND 应用:
        TG 全部:    n=1915 wr=47.3% E=-29%
        5 条全过:   n=649  wr=44.5% E=+5% (期望值由负转正)
      跨年稳定性: 2024/2025/2026 三条主规则 Δwr 全为负,稳.

    Rules (按期望值差排序, 黄金规则先):
      R4 RSI 超买:   rsi > 70 → 剔除 (E 差 +260.8% ⭐⭐⭐)  # 黄金
      R2 弱势股:     bias < -3% (价格远低于 MA20) → 剔除 (E 差 +239.7% ⭐⭐)
      R1 微盘股:     mcap < 50 亿 → 剔除 (E 差 +197.5%)
      R5 严格空头:   MA5<MA10<MA20 → 剔除 (E 差 +152.7% ⭐⭐)
      R3 追高:       bias > 10% (价格远高于 MA20) → 剔除 (E 差 +71.2%)

    Args:
        r: 单只股票 deep_scorer 输出结果

    Returns:
        (passed_rules, failed_rules) 两个 list, 用于诊断
    """
    passed = []
    failed = []

    # R1 微盘股过滤
    mcap = r.get("circulating_market_cap") or 0
    if mcap > 0 and mcap < 50:
        failed.append(("R1_micro_cap", f"流通市值 {mcap:.0f}亿 < 50亿"))
    else:
        passed.append("R1_micro_cap")

    # R2 弱势股 (价格远低于 MA20, bias < -3%)
    # v7.0.30: 阈值 -5% → -3%, value/cyclical archetype 跳过
    bias = r.get("ma5_above_ma20_pct") or 0
    arch = r.get("archetype", "")
    if arch in ("value_defensive", "cyclical_resource"):
        # 周期/价值股超跌反弹是机会, 规则不适用
        passed.append("R2_weak_skipped_archetype")
    elif bias < -3:
        failed.append(("R2_weak", f"价格低于 MA20 {bias:.1f}%"))
    else:
        passed.append("R2_weak")

    # R3 追高股 (bias > 10%)
    # v7.0.30: 阈值 +5% → +10%, value/cyclical archetype 跳过
    if arch in ("value_defensive", "cyclical_resource"):
        passed.append("R3_chase_high_skipped_archetype")
    elif bias > 10:
        failed.append(("R3_chase_high", f"价格高于 MA20 {bias:.1f}%"))
    else:
        passed.append("R3_chase_high")

    # R4 RSI 超买 (rsi > 70) - 黄金规则! 触发胜率仅 10%
    rsi = r.get("rsi_14") or 0
    if 0 < rsi > 70:
        failed.append(("R4_rsi_overbought", f"RSI={rsi:.0f} > 70 (超买)"))
    else:
        passed.append("R4_rsi_overbought")

    # R5 严格空头 (MA5<MA10<MA20) - 强规则
    ma_align = r.get("ma_alignment_strict") or 0
    if ma_align == 0:
        failed.append(("R5_strong_bear", "MA5<MA10<MA20 严格空头"))
    else:
        passed.append("R5_strong_bear")

    # === v7.0.32: 新增 3 条弱规则 (R6/R7/R8) ===
    # 这些是"加分项"——失败不阻断, 但记录到 details 里给前端展示
    # 优先级低于 R1-R5 主规则, 不参与 hard_rules_blocked 主推逻辑

    # R6 MACD 空头 (DIF < 0)
    macd_dif = r.get("macd_dif")
    if macd_dif is not None and macd_dif < 0:
        failed.append(("R6_macd_bear", f"MACD DIF={macd_dif:.2f} < 0 (空头)"))
    else:
        passed.append("R6_macd_bull")

    # R7 KDJ 超买 (J > 80)
    kdj_j = r.get("kdj_j")
    if kdj_j is not None and kdj_j > 80:
        failed.append(("R7_kdj_overbought", f"KDJ J={kdj_j:.0f} > 80 (超买)"))
    else:
        passed.append("R7_kdj_normal")

    # R8 筹码成本过低 (cost_50pct < 5) - 提示无主力
    cost_50 = r.get("cost_50pct")
    if cost_50 is not None and cost_50 < 5:
        failed.append(("R8_chip_too_low", f"筹码中位 {cost_50:.1f} < 5 (无主力)"))
    else:
        passed.append("R8_chip_normal")

    return passed, failed


derive_strategy_label = _derive_strategy_label


def _normalize_within_archetype(results: list[dict]) -> list[dict]:
    """Normalize composite scores within each archetype group (0-100 scale).

    安全阀:
      - 单只股票同组: 跳过归一化, 直接取 raw_total clamp
      - 组内 range=0 (所有分数相同): 统一打 50 分
      - 2-4 只的小组: 用 softmax 替代 min-max (不会因为一头一尾极端值
        使中间股票被打到 0 或 100)
    """
    groups: dict[str, list[dict]] = {}
    for r in results:
        arch = r.get("archetype", "small_speculative")
        groups.setdefault(arch, []).append(r)

    for arch, group in groups.items():
        scores = [r.get("raw_total", 0) for r in group]
        if not scores:
            continue
        mn, mx = min(scores), max(scores)

        if len(group) <= 1:
            # 只有 1 只: raw_total clamp 到 0-100
            for r in group:
                r["composite_score"] = round(float(np.clip(r.get("raw_total", 50), 0, 100)), 1)
            continue

        rng = mx - mn
        if rng <= 0.01:
            # 全组相同: 统一 50
            for r in group:
                r["composite_score"] = 50.0
            continue

        if len(group) <= 4:
            # 小组: softmax 归一化 + clamp 到 0-100
            import numpy as _np
            arr = _np.array(scores, dtype=float)
            arr_centered = arr - _np.mean(arr)
            # 用同比放大而不是 min-max, 保留组内差距但不允许极端
            std = _np.std(arr)
            if std > 0.1:
                normalized = (arr_centered / std) * 15 + 50
            else:
                normalized = _np.full_like(arr, 50.0)
            for i, r in enumerate(group):
                r["composite_score"] = round(float(np.clip(normalized[i], 0, 100)), 1)
            continue

        # 大组 (5+): 标准 min-max
        for r in group:
            raw = r.get("raw_total", 0)
            normalized = (raw - mn) / rng * 100
            r["composite_score"] = round(float(np.clip(normalized, 0, 100)), 1)
    return results


# ═══════════ Phase 1: Data Preload ═══════════

async def _deep_preload_phase(session, symbols: list[str], scan_date) -> dict:
    """Load all prerequisite data: fundamental, patterns, ambush, fingerprints, beliefs.

    v4.13: 预加载使用独立 session, 避免缺表/缺列导致主事务 abort.

    Returns ctx dict with keys: symbols, scan_rows, scan_date_str, archetype_map,
    weights_map, beliefs_map, industry_map, kline_batch, patterns, ambush,
    sector_toplist_flow, market_state
    """
    from app.core.database import async_session_factory as _asf
    scan_date_str = str(scan_date)

    # 1+2+3. Preload fundamental + patterns + ambush in parallel (v4.8)
    import asyncio as _asyncio

    async def _preload_wrapper():
        """并行预加载: fundamental 和 patterns+ambush 可同时运行"""
        results = {"fundamental": None, "patterns": None, "ambush": None}

        async def _load_fundamental():
            try:
                await preload_fundamental_scores(symbols)
                results["fundamental"] = "ok"
            except Exception as e:
                logger.warning(f"Fundamental preload failed: {e}")

        async def _load_patterns_ambush():
            try:
                from app.services.data_preloader import preload_patterns, preload_ambush
                async with _asf() as ps:
                    await preload_patterns(symbols, scan_date_str)
                results["patterns"] = "ok"
                async with _asf() as ps2:
                    await preload_ambush(symbols, scan_date_str)
                results["ambush"] = "ok"
            except Exception as e:
                logger.warning(f"Pattern/ambush preload failed: {e}")

        await _asyncio.gather(_load_fundamental(), _load_patterns_ambush())

    await _preload_wrapper()

    # 4. Load scan_rows
    r = await session.execute(text(
        "SELECT symbol, name, level, tg_momentum, dist_low, j_value, vol_ratio, "
        "buy_strength, close_price, composite_score, industry, trigger_path, "
        "COALESCE(resonance_type,'') as resonance_type, "
        "COALESCE(weekly_tg_momentum,0) as weekly_tg_momentum "
        "FROM scan_results WHERE scan_date=:d AND symbol=ANY(:syms)"
    ), {"d": scan_date, "syms": symbols})
    scan_rows = {row[0]: row for row in r.fetchall()}

    # 5. Build fingerprints + classify
    # v4.8: 优先从 stock_fingerprints 预建表读取原型 (省去每次重新构建)
    archetype_map: dict[str, str] = {}
    try:
        r = await session.execute(text(
            "SELECT symbol, archetype FROM stock_fingerprints WHERE symbol = ANY(:syms)"
        ), {"syms": symbols})
        fp_rows = {row[0]: row[1] for row in r.fetchall()}
        hit_count = sum(1 for s in symbols if s in fp_rows and fp_rows[s] != 'pending')
        total = len(symbols)
        logger.info(f"Fingerprint cache hit: {hit_count}/{total}")

        if hit_count > max(10, total * 0.3):
            # 覆盖率足够, 直接用
            for s in symbols:
                arch = fp_rows.get(s, "")
                if arch and arch != "pending":
                    archetype_map[s] = arch
                else:
                    archetype_map[s] = "large_bluechip"  # 缺数据的默认
        else:
            raise ValueError(f"Fingerprint coverage too low ({hit_count}/{total})")
    except Exception as e:
        # Fallback: 代码前缀+名称关键词分类
        logger.info(f"Fingerprint classify skipped ({e}), using code+name fallback")
        r = await session.execute(text(
            "SELECT DISTINCT ON (symbol) symbol, name, industry FROM scan_results WHERE symbol=ANY(:syms) ORDER BY symbol, scan_date DESC"
        ), {"syms": symbols})
        name_map = {row[0]: (row[1] or "", row[2] or "") for row in r.fetchall()}

        for s in symbols:
            name, ind = name_map.get(s, ("", ""))
            code = s[:3] if s else ""

            # -- 确定 board --
            if code.startswith(("8","4")):
                board = ""  # 北交所/新三板不分board前缀
            elif code.startswith("688"):
                board = "创业板_"
            elif code.startswith(("300","301")):
                board = "创业板_"
            else:
                board = "主板_"

            # -- 按代码前缀 + 名称关键词分类 --
            if code.startswith("8") or code.startswith("4"):
                arch = "small_speculative"
            elif code.startswith("688") or code.startswith("300") or code.startswith("301"):
                arch = "growth_tech"
            elif name and any(kw in name for kw in ["银行","保险","证券","金融","信托","白酒","食品","饮料","家电","乳业"]):
                arch = "large_bluechip"
            elif name and any(kw in name for kw in ["石油","石化","煤炭","有色","钢铁","化工","稀土","锂业","矿业","黄金","铜","铝","水泥","玻璃","纸","化纤","能源","燃气","港口","公路","铁路","航空"]):
                arch = "cyclical_resource"
            elif name and any(kw in name for kw in ["电力","水务","环保","建材","建筑","地产","农林","农业","纺织","旅游","酒店","百货","超市","医药","医疗","中药","制药","生物"]):
                arch = "value_defensive"
            elif name and any(kw in name for kw in ["科技","电子","半导体","芯片","通信","软件","互联网","机器人","光电","精密","智能","数字","数据","网络","信息","计算机","自动化","汽车","新能源","光伏","风能","电池","储能","材料"]):
                arch = "growth_tech"
            elif code.startswith("6") or code.startswith("0"):
                arch = "large_bluechip"
            else:
                arch = "growth_tech"
            archetype_map[s] = board + arch

        dist = {}
        for v in archetype_map.values():
            dist[v] = dist.get(v, 0) + 1
        logger.info(f"Code+name archetype distribution: {dist}")

    # 5.5. Determine current market regime (before weight resolution)
    regime_name = None
    try:
        from app.services.market_gate import get_market_state
        _ms = await get_market_state()
        _regime_str = _ms.get("regime", "")
        if "趋势上涨" in _regime_str or "结构行情" in _regime_str:
            regime_name = "bull"
        elif "恐慌" in _regime_str or "弱势" in _regime_str:
            regime_name = "bear"
        else:
            regime_name = "range"
    except Exception:
        pass

    # 6. Resolve weights + beliefs per archetype
    # v4.8: 加载 regime 权重 (bull/bear/range) 并与原型权重混合
    weights_map: dict[str, dict] = {}
    beliefs_map: dict[str, dict] = {}
    regime_weights: dict[str, float] = {}
    regime_available = False
    try:
        from app.services.archetype_param_resolver import resolve_scoring_weights
        from app.services.bayesian_optimizer import get_beliefs
        seen_arch = set(archetype_map.values())
        for arch in seen_arch:
            beliefs = await get_beliefs(arch)
            beliefs_map[arch] = beliefs
            weights_map[arch] = resolve_scoring_weights(arch, beliefs)

        # ── v4.8: 加载 regime 分段权重 ──
        if regime_name:
            rb = await get_beliefs(regime_name)
            if rb:
                # 安全门控: n≥50 + params≥10 + AUC≥0.55
                regime_n_vals = [info.get("n", 0) if isinstance(info, dict) else 0
                                 for info in rb.values()]
                max_n = max(regime_n_vals) if regime_n_vals else 0
                n_params = len([info for k, info in rb.items()
                                if not k.startswith("__")])
                auc_info = rb.get("__regime_auc__", {})
                auc_val = auc_info.get("mu", 0) if isinstance(auc_info, dict) else 0

                cfg = REGIME_ACTIVATION_CONFIG
                if (max_n >= cfg["min_samples"] and n_params >= cfg["min_params"]
                    and auc_val >= cfg["min_auc"]):
                    for k, info in rb.items():
                        if not k.startswith("__"):
                            regime_weights[k] = info.get("mu", 1.0) if isinstance(info, dict) else float(info if info else 1.0)
                    regime_available = True
                    logger.info(
                        f"Regime [{regime_name}] weights activated: "
                        f"n={max_n}, params={n_params}, AUC={auc_val:.3f}"
                    )
                else:
                    logger.info(
                        f"Regime [{regime_name}] weights REJECTED: "
                        f"n={max_n}/{cfg['min_samples']}, params={n_params}/{cfg['min_params']}, AUC={auc_val:.3f}/{cfg['min_auc']} → fallback to global"
                    )
    except Exception as e:
        logger.warning(f"Weight/belief resolution failed: {e}")

    # ── v4.8: 混合 regime 权重到每个原型的 weights_map ──
    # P2-2: 使用可配置混合比例
    if REGIME_BLEND_CONFIG["enabled"] and regime_available and regime_weights:
        blend_ratio = REGIME_BLEND_CONFIG["blend_ratio"]
        for arch in weights_map:
            for rk, rv in regime_weights.items():
                if rk in weights_map[arch]:
                    original = weights_map[arch][rk]
                    weights_map[arch][rk] = round(original * (1 - blend_ratio) + rv * blend_ratio, 4)

    # 7. Build industry map
    industry_map: dict[str, str] = {}
    try:
        r = await session.execute(text(
            "SELECT ts_code, industry FROM ths_member WHERE out_date IS NULL AND ts_code=ANY(:syms)"
        ), {"syms": symbols})
        for row in r.fetchall():
            if row[1]:
                industry_map[row[0]] = row[1]
    except Exception:
        try: await session.rollback()
        except Exception: pass
        pass

    # 8. Batch-load K-line data（上界约束防止回扫时引入未来数据）
    # ── Notebook: 限制 K 线天数以节省内存 ──
    kline_days = BATCH_CONFIG["kline_days"]
    kline_batch: dict[str, dict] = {}
    try:
        r = await session.execute(text(
            "SELECT ts_code, trade_date, open, high, low, close, volume "
            "FROM daily_kline WHERE ts_code=ANY(:syms) AND trade_date >= :cut AND trade_date <= :scan_date "
            "ORDER BY ts_code, trade_date"
        ), {"syms": symbols, "cut": scan_date - timedelta(days=kline_days), "scan_date": scan_date})
        import pandas as pd
        rows = r.fetchall()
        for row in rows:
            code = row[0]
            if code not in kline_batch:
                kline_batch[code] = {"trade_date": [], "Open": [], "High": [],
                                      "Low": [], "Close": [], "Volume": []}
            for i, col in enumerate(["trade_date", "Open", "High", "Low", "Close", "Volume"]):
                if col == "trade_date":
                    kline_batch[code]["trade_date"].append(row[i+1])
                else:
                    kline_batch[code][col].append(float(row[i+1] or 0))
        # Convert to DataFrame
        for code in kline_batch:
            d = kline_batch[code]
            if d["Close"]:
                kline_batch[code] = pd.DataFrame(d)
            else:
                kline_batch[code] = None
    except Exception as e:
        logger.warning(f"K-line batch load failed: {e}")
        try: await session.rollback()
        except Exception: pass

    # 9. Preload sector toplist flow
    sector_toplist_flow: dict[str, float] = {}
    try:
        r = await session.execute(text(
            "SELECT sector, SUM(net_amount) as total_net FROM toplist_daily "
            "WHERE trade_date=:d GROUP BY sector"
        ), {"d": scan_date})
        for row in r.fetchall():
            sector_toplist_flow[row[0]] = float(row[1] or 0)
    except Exception:
        try: await session.rollback()
        except Exception: pass
        pass

    # 10. Market state
    market_state = {}
    try:
        from app.services.market_gate import get_market_state
        market_state = await get_market_state()
    except Exception:
        pass

    # 11. Preload chip perf (Tushare cyq_perf batch)
    chip_batch: dict[str, dict] = {}
    try:
        from app.services.chip_service import get_cyq_perf_batch
        chip_batch = await get_cyq_perf_batch(symbols, scan_date)
        if chip_batch:
            logger.info(f"Chip perf preloaded: {len(chip_batch)}/{len(symbols)} stocks")
    except Exception as e:
        logger.debug(f"Chip perf preload skipped: {e}")

    return {
        "symbols": symbols,
        "scan_rows": scan_rows,
        "scan_date_str": scan_date_str,
        "archetype_map": archetype_map,
        "weights_map": weights_map,
        "beliefs_map": beliefs_map,
        "industry_map": industry_map,
        "kline_batch": kline_batch,
        "chip_batch": chip_batch,
        "patterns": getattr(__import__('app.services.data_preloader', fromlist=['_pattern_cache']), '_pattern_cache', {}),
        "ambush": getattr(__import__('app.services.data_preloader', fromlist=['_ambush_cache']), '_ambush_cache', {}),
        "sector_toplist_flow": sector_toplist_flow,
        "market_state": market_state,
    }


# ═══════════ Phase 2: Per-Stock Scoring ═══════════

async def _deep_score_phase(session, ctx: dict) -> list[dict]:
    """Score each stock on 13+ dimensions using scoring/ modules.

    Returns list of dicts with dimension_scores filled.
    """
    symbols = ctx["symbols"]
    scan_rows = ctx["scan_rows"]
    kline_batch = ctx["kline_batch"]
    chip_batch = ctx.get("chip_batch", {})
    industry_map = ctx["industry_map"]
    sector_toplist_flow = ctx["sector_toplist_flow"]
    market_state = ctx["market_state"]
    results = []

    for sym in symbols:
        row = scan_rows.get(sym)
        if not row:
            continue

        symbol, name, level, tg_momentum, dist_low, j_value, vol_ratio_sr = row[0], row[1], row[2], row[3], row[4], row[5], row[6]
        buy_strength, close_price, composite_score_sr, industry, trigger_path = row[7], row[8], row[9], row[10], row[11]
        resonance_type = row[12] if len(row) > 12 else ""
        weekly_tg_momentum = row[13] if len(row) > 13 else 0

        # ★ v7.0.34: 修复 r 未定义 bug — 之前 _deep_score_phase 用 r.get() 但循环变量是 sym, 会 NameError
        r = {
            "symbol": symbol, "name": name, "level": level,
            "tg_momentum": tg_momentum, "dist_low": dist_low,
            "j_value": j_value, "vol_ratio": vol_ratio_sr,
            "buy_strength": buy_strength, "close_price": close_price,
            "composite_score": composite_score_sr, "industry": industry,
            "trigger_path": trigger_path, "resonance_type": resonance_type,
            "weekly_tg_momentum": weekly_tg_momentum,
        }

        kline_df = kline_batch.get(sym)
        sector_name = industry_map.get(sym, industry or "")

        dims = {}
        # Archetype
        arch = ctx["archetype_map"].get(sym, _arch_for_industry(sector_name))

        # TG momentum dimension (from scan)
        tg_score = round(float(np.clip((tg_momentum or 0) / 10, 0, 10)), 1)
        dims["tg_momentum"] = {"score": tg_score, "raw": tg_momentum or 0}

        # Distance from low
        dist_score = round(float(np.clip(10 - abs(dist_low or 0) * 0.5, 0, 10)), 1)
        dims["dist_low"] = {"score": dist_score, "raw": dist_low or 0}

        # J-value (KDJ — 0-100. J>80=超买风险低分, J<20=超卖机会高分)
        j_val = j_value or 0
        if j_val > 80:
            j_score = round(float(np.clip((100 - j_val) / 20 * 5, 0, 10)), 1)  # 80→5, 100→0
        elif j_val < 20:
            j_score = round(float(np.clip((20 - j_val) / 20 * 5 + 5, 5, 10)), 1)  # 0→10, 20→5
        else:
            j_score = round(float(np.clip(10 - abs(j_val - 50) / 5, 0, 10)), 1)  # 50→10, 远离50降分
        dims["j_value"] = {"score": j_score, "raw": j_value or 0}

        # K-line based scores
        if kline_df is not None and len(kline_df) >= 20:
            tech_r = score_technical(kline_df)
            if tech_r:
                dims["technical"] = {"score": round(float(np.clip((tech_r["score"] + 10) / 2, 0, 10)), 1), "raw": tech_r["score"], "detail": tech_r.get("detail", "")}

            kline_r = score_kline_game(kline_df)
            if kline_r:
                dims["kline_game"] = {"score": round(float(np.clip((kline_r["score"] + 10) / 2, 0, 10)), 1), "raw": kline_r["score"]}

            vol_r = score_vol_ratio(kline_df)
            if vol_r:
                dims["vol_ratio"] = {"score": round(float(np.clip(vol_r["score"], 0, 10)), 1), "raw": vol_r["score"]}

            arbr_r = score_arbr(kline_df)
            if arbr_r:
                dims["arbr"] = {"score": round(float(np.clip((arbr_r["score"] + 10) / 2, 0, 10)), 1), "raw": arbr_r["score"]}

            bbi_r = score_bbi(kline_df)
            if bbi_r:
                dims["bbi"] = {"score": bbi_r["score"], "raw": bbi_r.get("bbi_deviation", 0)}

            trend_r = score_trend_deviation(kline_df)
            if trend_r:
                dims["trend_deviation"] = {"score": trend_r["score"], "raw": trend_r.get("deviation_pct", 0)}

            risk_r = score_downside_risk(kline_df)
            if risk_r:
                dims["downside_risk"] = {"score": round(float(np.clip((risk_r["score"] + 10) / 2, 0, 10)), 1), "raw": risk_r["score"], "risk_level": risk_r.get("risk_level", "normal")}

            ma_r = score_ma_trend(kline_df)
            if ma_r:
                dims["ma_trend"] = {"score": round(float(np.clip((ma_r["score"] + 10) / 2, 0, 10)), 1), "raw": ma_r["score"]}

            box_r = score_multi_box(kline_df)
            if box_r is not None:
                dims["multi_box"] = {"score": box_r["score"], "raw": box_r.get("raw_score", 0), "detail": box_r.get("detail", "")}

            # Market relative
            market_pct = market_state.get("sh_index_change_pct", 0)
            mkt_r = score_market_relative(kline_df, market_pct)
            if mkt_r:
                dims["market_relative"] = {"score": round(float(np.clip((mkt_r["score"] + 10) / 2, 0, 10)), 1), "raw": mkt_r["score"]}

            # Fund flow
            fund_r = score_fund_flow(kline_df)
            if fund_r:
                dims["fund_flow"] = {"score": round(float(np.clip((fund_r["score"] + 10) / 2, 0, 10)), 1), "raw": fund_r["score"]}

            # Sector alpha
            sector_change = market_state.get("sector_change_pct", 0)
            sec_r = score_sector_alpha(kline_df, sector_change)
            if sec_r:
                dims["sector_alpha"] = {"score": round(float(np.clip((sec_r["score"] + 10) / 2, 0, 10)), 1), "raw": sec_r["score"]}

        # Fundamental score (from cache)
        try:
            funda_score, funda_detail, pb, pe = await get_fundamental_score_cached(sym)
            dims["fundamentals"] = {"score": round(float(np.clip((funda_score + 20) / 4, 0, 10)), 1), "raw": funda_score, "detail": funda_detail}
        except Exception:
            logger.warning(f"Fundamental score unavailable for {sym}")
            dims["fundamentals"] = {"score": 0.0, "raw": 0}

        # Valuation
        try:
            _, _, pb, pe = await get_fundamental_score_cached(sym)
            val_r = score_valuation(pb, pe)
            dims["valuation"] = {"score": round(float(np.clip((val_r["score"] + 10) / 2, 0, 10)), 1), "raw": val_r["score"]}
        except Exception:
            logger.warning(f"Valuation score unavailable for {sym}")
            dims["valuation"] = {"score": 0.0, "raw": 0}

        # Pattern signal
        patterns = ctx.get("patterns", {}).get(sym, "")
        pat_r = score_pattern_signal(patterns)
        if pat_r:
            dims["pattern"] = {"score": round(float(np.clip((pat_r["score"] + 10) / 2, 0, 10)), 1), "raw": pat_r["score"]}

        # Weekly resonance
        if resonance_type:
            wk_r = score_weekly_resonance(resonance_type, weekly_tg_momentum or 0)
            dims["weekly_resonance"] = {"score": wk_r["score"], "detail": wk_r["detail"]}

        # Toplist sector
        tl_r = score_toplist_sector(sym, sector_toplist_flow, industry_map)
        dims["toplist_sector"] = {"score": round(float(np.clip((tl_r["score"] + 5) / 1.5, 0, 10)), 1), "raw": tl_r["score"]}

        # Ambush score
        ambush_score = ctx.get("ambush", {}).get(sym, 0)
        dims["ambush"] = {"score": round(float(np.clip(ambush_score, 0, 10)), 1), "raw": ambush_score}

        # ── v4.8: 筹码维度 (Tushare cyq_perf) ──
        chip = chip_batch.get(sym, {})
        if chip:
            # winner_rate: 获利盘比例 → 30-50% 最优 (底部吸筹区间)
            # v4.10: 强化底部/顶部惩罚
            wr = float(chip.get("winner_rate", 50))
            if 30 <= wr <= 50:
                wr_score = 10.0 - abs(wr - 40) / 5  # 40→10, 30→8, 50→8: 黄金区间
            elif 15 <= wr < 30:
                wr_score = (wr - 15) / 15 * 5 + 2  # 15→2, 30→7: 底部吸筹中
            elif wr < 15:
                wr_score = (wr / 15) * 2  # 0→0, 15→2: 深套无底洞
            elif 50 < wr <= 70:
                wr_score = 8.0 - (wr - 50) / 20 * 3  # 50→8, 70→5: 获利区但尚可
            elif 70 < wr <= 85:
                wr_score = 5.0 - (wr - 70) / 15 * 3  # 70→5, 85→2: 高位风险
            else:
                wr_score = max(0.0, 2.0 - (wr - 85) / 15 * 2)  # 85→2, 100→0: 庄家出货
            dims["chip_winner"] = {"score": round(float(np.clip(wr_score, 0, 10)), 1),
                                    "raw": wr}

            # cost_50pct vs current_price: 成本支撑强度
            cost50 = float(chip.get("cost_50pct", 0))
            if cost50 > 0 and close_price > 0:
                cost_dist = (close_price - cost50) / cost50 * 100  # 当前价距中位成本%
                if -10 <= cost_dist <= 10:
                    cost_score = 8.0 - abs(cost_dist) * 0.6  # 价在成本线附近→高支撑
                elif cost_dist < -20:
                    cost_score = 3.0  # 暴跌远离成本区, 无支撑
                elif cost_dist > 20:
                    cost_score = 5.0 - (cost_dist - 20) * 0.2  # 涨幅过大远离成本
                else:
                    cost_score = 6.0 - abs(cost_dist - (10 if cost_dist > 0 else -10)) * 0.3
                dims["chip_cost"] = {"score": round(float(np.clip(cost_score, 0, 10)), 1),
                                     "raw": round(cost_dist, 2)}
        else:
            # 无筹码数据: 默认 5 分, 等数据
            dims["chip_winner"] = {"score": 5.0, "raw": 0}
            dims["chip_cost"] = {"score": 5.0, "raw": 0}

        # ── v7.0.32: 新增 5 维技术/筹码 维度 (写入 dimension_scores 让 v2 trainer 能训练) ──
        # MACD: DIF 0轴上=多头 (score 5~10), 下=空头 (0~5)
        macd_dif_v = r.get("macd_dif")
        if macd_dif_v is not None:
            macd_bar_v = r.get("macd_bar") or 0
            # clip to [-3, 3] 区间, 0轴附近 score≈5
            macd_score = float(np.clip(5 + (macd_dif_v * 0.5) + (macd_bar_v * 0.05), 0, 10))
            dims["macd"] = {"score": round(macd_score, 1), "raw": float(macd_dif_v),
                            "bar": float(macd_bar_v)}

        # KDJ: J值 0~100, 20~80 中性 (score 5), <20超卖 (8~10), >80超买 (2~5)
        kdj_j_v = r.get("kdj_j")
        if kdj_j_v is not None:
            if kdj_j_v < 0: kdj_j_v = 0
            if kdj_j_v > 100: kdj_j_v = 100
            if kdj_j_v < 20:
                kdj_score = 10 - (kdj_j_v / 20) * 2  # 0→10, 20→8
            elif kdj_j_v > 80:
                kdj_score = 5 - ((kdj_j_v - 80) / 20) * 3  # 80→5, 100→2
            else:
                # 中性区间, 接近 50 最佳
                kdj_score = 5 + (1 - abs(kdj_j_v - 50) / 30) * 2  # 50→7, 20/80→5
            kdj_score = float(np.clip(kdj_score, 0, 10))
            dims["kdj"] = {"score": round(kdj_score, 1), "raw": float(kdj_j_v)}

        # RSI_24: 0~100, 30~70 中性, <30超卖 (8~10), >70超买 (2~5)
        rsi_24_v = r.get("rsi_24")
        if rsi_24_v is not None:
            if rsi_24_v < 0: rsi_24_v = 0
            if rsi_24_v > 100: rsi_24_v = 100
            if rsi_24_v < 30:
                rsi_score = 8 + (30 - rsi_24_v) / 30 * 2  # 0→10, 30→8
            elif rsi_24_v > 70:
                rsi_score = 5 - (rsi_24_v - 70) / 30 * 3  # 70→5, 100→2
            else:
                rsi_score = 5 + (1 - abs(rsi_24_v - 50) / 20) * 2  # 50→7, 30/70→5
            rsi_score = float(np.clip(rsi_score, 0, 10))
            dims["rsi_24"] = {"score": round(rsi_score, 1), "raw": float(rsi_24_v)}

        # BOLL: boll_pos 0~1, 0.3~0.7 中性, <0.1下轨外 (超跌=9), >0.9上轨外 (超涨=2)
        boll_pos_v = r.get("boll_pos")
        if boll_pos_v is not None:
            if boll_pos_v < 0: boll_pos_v = 0
            if boll_pos_v > 1: boll_pos_v = 1
            if boll_pos_v < 0.1:
                boll_score = 9 - boll_pos_v * 10  # 0→9, 0.1→8
            elif boll_pos_v > 0.9:
                boll_score = 3 - (boll_pos_v - 0.9) * 10  # 0.9→3, 1.0→2
            elif boll_pos_v < 0.3:
                boll_score = 5 + (0.3 - boll_pos_v) / 0.2 * 2  # 0.3→5, 0.1→7
            elif boll_pos_v > 0.7:
                boll_score = 5 - (boll_pos_v - 0.7) / 0.2 * 2  # 0.7→5, 0.9→3
            else:
                # 中性区间, 0.5 最佳
                boll_score = 5 + (1 - abs(boll_pos_v - 0.5) / 0.2) * 2  # 0.5→7
            boll_score = float(np.clip(boll_score, 0, 10))
            dims["boll"] = {"score": round(boll_score, 1), "raw": round(boll_pos_v, 3)}

        # CCI: -300~300, 极端值好/坏, ±100 中性
        cci_v = r.get("cci")
        if cci_v is not None:
            if cci_v > 100:
                cci_score = 7 - (cci_v - 100) / 200 * 4  # 100→7, 300→3 超买
            elif cci_v < -100:
                cci_score = 8 + (cci_v + 100) / 200 * 2  # -100→8, -300→10 超卖
            else:
                cci_score = 5 + (1 - abs(cci_v) / 100) * 1  # 0→6
            cci_score = float(np.clip(cci_score, 0, 10))
            dims["cci"] = {"score": round(cci_score, 1), "raw": float(cci_v)}

        # ── v7.0.34: OBV 主力能量潮 (On-Balance Volume) ──
        # 计算逻辑: 收盘上涨→当日量累加, 下跌→累减, 平→不变
        # 20日 OBV 均线: 用于判断量价配合 (OBV > MA20 主力吸筹)
        if kline_df is not None and len(kline_df) >= 20:
            try:
                close = kline_df["Close"].astype(float) if "Close" in kline_df.columns else None
                vol = kline_df["Volume"].astype(float) if "Volume" in kline_df.columns else None
                if close is not None and vol is not None and len(close) >= 20:
                    delta = close.diff()
                    sign = delta.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
                    obv_series = (sign * vol).cumsum()
                    obv_value = float(obv_series.iloc[-1])
                    obv_ma20 = float(obv_series.tail(20).mean())
                    r["obv_value"] = obv_value
                    r["obv_ma20"] = obv_ma20
                    # 量价配合: OBV > MA20 + 收阳 → 主力吸筹加分
                    if obv_value > obv_ma20 and delta.iloc[-1] > 0:
                        obv_score = 7.5
                    elif obv_value > obv_ma20:
                        obv_score = 6.0
                    elif delta.iloc[-1] < 0:
                        obv_score = 3.0  # 价跌 + OBV < MA20 → 主力出货
                    else:
                        obv_score = 4.5
                    dims["obv"] = {"score": round(obv_score, 1),
                                    "raw": obv_value,
                                    "ma20": obv_ma20,
                                    "trend": "up" if obv_value > obv_ma20 else "down"}
            except Exception as e:
                logger.debug(f"OBV 计算失败 {sym}: {e}")
                r["obv_value"] = None
                r["obv_ma20"] = None

        # chip_winner_rate: 30~50 黄金区间, 70+ 风险, <15 深套
        wr_v2 = r.get("winner_rate")
        if wr_v2 is not None and "chip_winner" in dims:
            # dims["chip_winner"] 已经写过, 这里只保留"原生 winner_rate" 维度
            # 名字跟 DIM_KEYS 'chip_winner_rate' 对应 (区别于 chip_winner 的归一化分)
            dims["chip_winner_rate"] = {"score": dims["chip_winner"]["score"],
                                        "raw": float(wr_v2),
                                        "chip_winner_score": dims["chip_winner"]["score"]}

        results.append({
            "symbol": sym,
            "name": name,
            "archetype": arch,
            "close_price": close_price,
            "industry": sector_name,
            "level": level,
            "tg_momentum": tg_momentum or 0,
            "resonance_type": resonance_type,
            "dimension_scores": dims,
        })

    return results


# ═══════════ Phase 3: Enrich (gates + weights + bonuses) ═══════════

async def _deep_enrich_phase(session, results: list[dict], ctx: dict) -> list[dict]:
    """Apply fundamental gate, archetype weights, sector bonuses, multi-timeframe, toplist, event impact."""
    weights_map = ctx.get("weights_map", {})
    scan_date_str = ctx.get("scan_date_str", "")
    market_state = ctx.get("market_state", {})

    # ── ★ P0: Macro impact (Tier 1, precomputed once for all stocks) ──
    macro_adj = 0.0
    try:
        from app.services.macro_data import score_macro_impact
        macro_adj, _ = await score_macro_impact()
    except Exception:
        pass
    logger.info(f"Macro impact this scan: {macro_adj:+.1f}")

    # ── ★ v4.10: 原型历史胜率加权 — 避免低胜率原型持续打出高分 ──
    proto_wr_map: dict[str, float] = {}
    try:
        from sqlalchemy import text as _text
        from app.core.database import async_session_factory as _asf
        async with _asf() as wr_sess:
            r = await wr_sess.execute(_text("""
                SELECT archetype,
                       COUNT(*) FILTER(WHERE outcome_label IN ('strong_win','weak_win'))::float
                       / NULLIF(COUNT(*) FILTER(WHERE outcome_label IS NOT NULL), 0) * 100 as wr
                FROM signal_history
                WHERE archetype IS NOT NULL
                  AND scan_date >= :cut
                GROUP BY archetype
            """), {"cut": scan_date_str})
            for row in r.fetchall():
                proto_wr_map[row[0]] = float(row[1]) if row[1] else 50.0
        logger.info(f"Prototype win rates: {', '.join(f'{k}={v:.0f}%' for k,v in sorted(proto_wr_map.items()))}")
    except Exception:
        pass

    for r in results:
        sym = r["symbol"]
        arch = r["archetype"]
        dims = r.get("dimension_scores", {})
        weights = weights_map.get(arch, dict(DEFAULT_WEIGHTS))

        # ── Fundamental gate adjustment ──
        funda_score = dims.get("fundamentals", {}).get("raw", 0)
        funda_adj = 0.0
        if funda_score < -10:
            funda_adj = -2.0
        elif funda_score < -5:
            funda_adj = -1.0
        elif funda_score < 0:
            funda_adj = -0.5
        r["fundamental_adjustment"] = round(funda_adj, 1)

        # ── Archetype weight synthesis ──
        dim_keys_map = {
            "technical": "tech_weight", "kline_game": "kline_weight",
            "fund_flow": "fund_weight", "tg_momentum": "tg_momentum_weight",
            "vol_ratio": "vol_ratio_weight", "arbr": "arbr_weight",
            "sector_alpha": "sector_alpha_weight", "market_relative": "market_relative_weight",
            "valuation": "valuation_weight", "ma_trend": "ma_trend_weight",
            "pattern": "pattern_weight", "trend_deviation": "trend_deviation_weight",
            "bbi": "bbi_weight", "multi_box": "box_weight",
            "fundamentals": "fundamentals_weight",
            # Phase 73: 补入之前遗漏的子维度
            "dist_low": "dist_low_weight", "j_value": "j_value_weight",
            "downside_risk": "downside_risk_weight",
            # v4.8: 筹码维度 (Tushare cyq_perf)
            "chip_winner": "chip_winner_weight", "chip_cost": "chip_cost_weight",
        }
        weighted_sum = 0.0
        weight_total = 0.0
        for dim_key, weight_key in dim_keys_map.items():
            if dim_key in dims:
                w = weights.get(weight_key, DEFAULT_WEIGHTS.get(weight_key, 1.5))
                s = dims[dim_key].get("score", 5.0)
                weighted_sum += w * s
                weight_total += w
        # Phase 73: extra dimensions 也变为可训练权重
        for extra_dim in ["weekly_resonance", "toplist_sector", "ambush"]:
            if extra_dim in dims:
                wk = f"{extra_dim}_weight"
                w = weights.get(wk, DEFAULT_WEIGHTS.get(wk, 0.5))
                weighted_sum += w * dims[extra_dim].get("score", 5.0)
                weight_total += w

        raw_total = weighted_sum / weight_total * 10 if weight_total > 0 else 50
        r["raw_total"] = round(raw_total, 1)
        r["weight_snapshot"] = {k: round(v, 2) for k, v in weights.items()}

        # ── ★ v4.10: 原型胜率折扣 — 低胜率原型全组成绩打折 ──
        proto_wr = proto_wr_map.get(arch, 50)
        if proto_wr < 30:
            proto_discount = 0.65  # 原型胜率<30% → 几乎不可能盈利，强折扣
        elif proto_wr < 35:
            proto_discount = 0.78
        elif proto_wr < 40:
            proto_discount = 0.88
        elif proto_wr >= 45:
            proto_discount = 1.05  # 高胜率原型微幅奖励
        else:
            proto_discount = 1.0
        r["proto_win_rate"] = round(proto_wr, 1)
        r["proto_discount"] = round(proto_discount, 2)

        # ── Sector bonus (v7.0.32: 集成龙虎榜 + 历史涨幅) ──
        sector_bonus = 0.0
        hot_individuals = set()
        hot_sectors = set()
        sf = None
        try:
            from app.services.sector_heat_engine import get_stock_sector_factor, get_sector_rankings, detect_theme_lifecycle
            if "_sector_preload" not in ctx:
                ctx["_sector_preload"] = {
                    "rankings": await get_sector_rankings(),
                    "theme": await detect_theme_lifecycle(),
                }
                # v7.0.32: 加载龙虎榜热点 (失败不影响主流程)
                try:
                    from app.services.recommendation_gating import collect_hot_sectors
                    hot_sec, hot_ind = await collect_hot_sectors()
                    ctx["_sector_preload"]["hot_sectors"] = hot_sec
                    ctx["_sector_preload"]["hot_individuals"] = hot_ind
                    logger.info(f"Sector preload: {len(hot_sec)} hot sectors, {len(hot_ind)} hot individuals (from toplist)")
                except Exception as e:
                    logger.warning(f"collect_hot_sectors failed (non-fatal): {e}")
                    ctx["_sector_preload"]["hot_sectors"] = set()
                    ctx["_sector_preload"]["hot_individuals"] = set()

            sf = await get_stock_sector_factor(sym,
                preloaded_rankings=ctx["_sector_preload"]["rankings"],
                preloaded_theme=ctx["_sector_preload"]["theme"])

            hot_individuals = ctx["_sector_preload"].get("hot_individuals", set())
            hot_sectors = ctx["_sector_preload"].get("hot_sectors", set())

            # v7.0.32: 龙虎榜直接命中加分 (v4.8 只看历史涨跌幅)
            if sym in hot_individuals:
                sector_bonus = max(sector_bonus, weights.get("sector_bonus_l3", 1.5))
            elif sf and sf.get("sector_name") and sf["sector_name"] in hot_sectors:
                sector_bonus = max(sector_bonus, weights.get("sector_bonus_l2", 0.5))
            elif sf:
                # v7.0.32 修复: lifecycle_stage 不是 heat_level
                # 高潮/发酵 = hot, 萌芽/分化 = warm
                lc_stage = sf.get("lifecycle_stage", "休眠")
                if lc_stage in ("高潮", "发酵"):
                    sector_bonus = max(sector_bonus, weights.get("sector_bonus_l3", 1.5))
                elif lc_stage in ("萌芽", "分化"):
                    sector_bonus = max(sector_bonus, weights.get("sector_bonus_l2", 0.5))
                # 5日涨幅前 10 也算 hot
                elif sf.get("sector_rank_5d", 99) <= 10:
                    sector_bonus = max(sector_bonus, weights.get("sector_bonus_l3", 1.5))
                elif sf.get("sector_rank_5d", 99) <= 20:
                    sector_bonus = max(sector_bonus, weights.get("sector_bonus_l2", 0.5))
        except Exception:
            pass
        r["sector_bonus"] = round(sector_bonus, 1)

        # ── ✦ Macro impact (precomputed Tier 1, shared by all stocks) ──
        r["macro_adjustment"] = round(float(macro_adj), 1)

        # ── Event impact (v4.8: 替换为 Tushare 宏观数据) ──
        # 旧方案: score_event_impact() 读取空白的 stock_events 表
        # 新方案: compute_sector_macro_score() 使用 Tushare 宏观数据
        event_impact = 0.0
        event_label = ""
        try:
            from app.services.macro_data import compute_sector_macro_score
            sector_name = r.get("industry") or r.get("sector") or ""
            if sector_name:
                sector_score, _ = await compute_sector_macro_score(sector_name)
                # 宏观得分映射到事件影响: -3~+3 → -9~+9
                event_impact = round(sector_score * 3, 1)
                if sector_score > 1:
                    event_label = f"宏观利好+{sector_score:.1f}"
                elif sector_score < -1:
                    event_label = f"宏观利空{sector_score:.1f}"
                elif sector_score > 0.5:
                    event_label = "宏观偏多"
                elif sector_score < -0.5:
                    event_label = "宏观偏空"
        except Exception:
            pass
        r["event_impact"] = round(float(event_impact), 1)
        r["event_label"] = event_label

        # ── Market correction (含宏观调整) ──
        regime = market_state.get("regime", "unknown")
        risk = market_state.get("risk", "unknown")
        r["market_correction"] = f"regime={regime} risk={risk} macro={macro_adj:+.1f}"

        # ── Adjustment reasons ──
        reasons = []
        if funda_adj != 0:
            reasons.append(f"基本面调整{funda_adj:+.1f}")
        if sector_bonus > 0:
            # v7.0.32: 标注加成来源 (龙虎榜 vs 历史涨幅)
            reason_suffix = ""
            if 'hot_individuals' in dir() and sym in hot_individuals:
                reason_suffix = '(龙虎榜)'
            elif sf and sf.get('sector_name') and sf['sector_name'] in hot_sectors:
                reason_suffix = '(板块热点)'
            elif sf:
                lc_stage = sf.get('lifecycle_stage', '休眠')
                if lc_stage in ('高潮', '发酵'):
                    reason_suffix = '(生命周期)'
                elif sf.get('sector_rank_5d', 99) <= 10:
                    reason_suffix = '(5日涨幅)'
            reasons.append(f'板块加成+{sector_bonus:.1f}{reason_suffix}')
        if abs(event_impact) > 1:
            reasons.append(f"事件影响{event_impact:+.1f}")
        if abs(macro_adj) > 0.5:
            reasons.append(f"宏观调整{macro_adj:+.1f}")
        r["adjustment_reasons"] = reasons

    # ── 基建层注入: 三层相对强弱 (Phase 26e) ──
    try:
        from app.services.sector_context import load_sector_context
        from app.core.database import async_session_factory as _asf
        scan_date_val = ctx.get("scan_date_str", "")
        if scan_date_val:
            from datetime import date as _dt
            sd = _dt.fromisoformat(scan_date_val) if isinstance(scan_date_val, str) else scan_date_val
        else:
            sd = None

        if sd:
            async with _asf() as fresh_session:  # 独立 session 避免管线事务污染
                ctx_sector = await load_sector_context(fresh_session, sd, [r["symbol"] for r in results])
            market_5d = ctx_sector.get("market_5d", 0)
            stock_sector_map = ctx_sector.get("stock_sector", {})

            for r in results:
                sym = r["symbol"]
                si = stock_sector_map.get(sym, {})
                stock_5d = si.get("stock_5d", 0)
                sector_5d = si.get("pct_5d", market_5d)
                sector_dir = si.get("direction", "震荡")
                lifecycle = si.get("lifecycle", "正常")
                rank_5d = si.get("rank_5d", 16)

                # ── 8 种相对位置判定 ──
                sector_up = sector_5d > 0.5
                market_up = market_5d > 0.3
                stock_beats_sector = stock_5d > sector_5d + 1.0

                if sector_up and market_up and stock_beats_sector:
                    position, adjustment = "领涨龙头", 5
                elif sector_up and market_up and not stock_beats_sector:
                    position, adjustment = "跟涨", 0
                elif sector_up and market_up and stock_5d < -1:
                    position, adjustment = "主力出货", -5
                elif sector_up and not market_up and stock_beats_sector:
                    position, adjustment = "独立走强", 8
                elif not sector_up and market_up and stock_beats_sector:
                    position, adjustment = "逆势抗跌", 2
                elif not sector_up and not market_up and stock_5d < sector_5d - 1:
                    position, adjustment = "领跌", -8
                elif not sector_up and not market_up and stock_beats_sector:
                    position, adjustment = "逆势拉升", 5
                else:
                    position, adjustment = "抗跌", 0

                # 修正 composite_score (含 v4.10 原型胜率折扣)
                adj = r.get("proto_discount", 1.0)
                r["composite_score"] = round(max(0, min(100, (r.get("composite_score", 50) + adjustment) * adj)), 1)
                r["relative_position"] = position
                r["sector_direction"] = sector_dir
                r["sector_lifecycle"] = lifecycle
                r["sector_rank_5d"] = rank_5d
                r["market_5d"] = round(market_5d, 1)

                # 板块退潮 + 个股领跌 → risk_label 升级
                if lifecycle == "退潮" and position in ("领跌", "主力出货"):
                    if r.get("risk_label", "") in ("", "warn"):
                        r["risk_label"] = "danger"
    except Exception as e:
        logger.warning(f"Sector context unavailable, skipping: {e}")

    # ── v4.8: 宏观信号加权 (Tushare 宏观数据 → composite_score) ──
    # 旧方案: 读取空白的 news_aggregated/news_verify 表
    # 新方案: 使用 Tushare 宏观数据 + 板块暴露系数
    try:
        from app.services.macro_data import compute_sector_macro_score

        # 按板块分组处理 (同板块只计算一次)
        sector_cache: dict[str, tuple[float, str]] = {}
        for r in results:
            sector = r.get("industry") or r.get("sector") or ""
            if not sector or sector in sector_cache:
                continue
            try:
                score, _ = await compute_sector_macro_score(sector)
                if score > 1:
                    label = f"宏观利好+{score:.1f}"
                elif score < -1:
                    label = f"宏观利空{score:.1f}"
                elif score > 0.5:
                    label = "宏观偏多"
                elif score < -0.5:
                    label = "宏观偏空"
                else:
                    label = "宏观中性"
                sector_cache[sector] = (score, label)
            except Exception:
                pass

        applied = 0
        for r in results:
            sector = r.get("industry") or r.get("sector") or ""
            if sector and sector in sector_cache:
                score, label = sector_cache[sector]
                # 映射到 composite_score 调整: -3~+3 → -9~+9
                adj = round(score * 3, 1)
                r["composite_score"] = round(max(0, min(100, r.get("composite_score", 50) + adj)), 1)
                r["news_signal"] = label
                applied += 1

        if applied:
            logger.info(f"v4.8 macro signals: applied to {applied}/{len(results)} stocks "
                       f"(sectors={len(sector_cache)})")
    except Exception as e:
        logger.debug(f"Macro signals unavailable: {e}")

    return results


# ═══════════ Phase 4: Normalize ═══════════

def _deep_normalize_phase(results: list[dict], market_coef: float = 1.0, sector_coefs: dict = None) -> list[dict]:
    """Normalize within archetype, compute composite_score, calibrate probability.

    v4.9: 增加三层 Regime 系数调整 (market × sector × stock).

    Args:
        results: 评分结果列表
        market_coef: 大盘系数 (默认 1.0)
        sector_coefs: 板块系数字典 {sector_code: coef}
    """
    if not results:
        return results

    if sector_coefs is None:
        sector_coefs = {}

    # Normalize within archetype
    results = _normalize_within_archetype(results)

    # Apply market gate corrections + sector bonus
    for r in results:
        raw_total = r.get("raw_total", 50)
        sector_bonus = r.get("sector_bonus", 0)
        event_impact = r.get("event_impact", 0)
        funda_adj = r.get("fundamental_adjustment", 0)

        # Composite = normalized base + sector bonus + event impact + fundamental adj
        composite = r.get("composite_score", raw_total) + sector_bonus * 1.5 + event_impact * 0.3 + funda_adj
        r["composite_score"] = round(float(np.clip(composite, 0, 100)), 1)

        # ── v4.9: 三层 Regime 系数调整 ──
        # 获取个股对应的板块系数
        sector_code = r.get("sector_code")  # 需要外部传入
        s_coef = sector_coefs.get(sector_code, 1.0) if sector_code else 1.0

        # 计算最终系数
        from app.services.regime_engine import calc_final_coef
        final_result = calc_final_coef(market_coef, s_coef, 1.0)  # 个股系数默认 1.0
        r["market_coef"] = final_result["market_coef"]
        r["sector_coef"] = final_result["sector_coef"]
        r["final_regime_coef"] = final_result["final_coef"]
        r["regime_signal"] = final_result["signal"]
        r["regime_signal_cn"] = final_result["signal_cn"]

        # 应用最终系数调整 composite_score
        r["regime_adjusted_score"] = round(float(np.clip(
            r["composite_score"] * final_result["final_coef"], 0, 100)), 1)
        # ── v4.9 end ──

        # Probability calibration
        try:
            from app.services.probability_calibrator import calibrate_with_regime
            wp = calibrate_with_regime(r["composite_score"], r.get("archetype", "unknown"), signal_quality=None)
            r["win_probability"] = round(float(wp), 4) if wp is not None else 0.35
        except Exception:
            r["win_probability"] = round(float(np.clip(r["composite_score"] / 200 + 0.05, 0.05, 0.65)), 4)

        # Signal quality — 基于综合分和胜率估算
        r["signal_quality"] = round(float(np.clip(r.get("composite_score", 50) / 100 * 0.8 + r.get("win_probability", 0.3) * 0.4, 0.1, 0.95)), 3)
        r["strategy_label"] = derive_strategy_label(r)

        # Tech score / kline score / fund score for API compatibility
        dims = r.get("dimension_scores", {})
        r["tech_score"] = dims.get("technical", {}).get("score", 5.0)
        r["kline_score"] = dims.get("kline_game", {}).get("score", 5.0)
        r["fund_score"] = dims.get("fund_flow", {}).get("score", 5.0)
        r["trend_score"] = dims.get("ma_trend", {}).get("score", 5.0)
        r["entry_score"] = dims.get("multi_box", {}).get("score", 5.0)
        r["downside_risk"] = dims.get("downside_risk", {}).get("score", 5.0)

        # ── v7.0.10: v2 字段占位 (v2 实际调用在 deep_analyze 主函数批量执行) ──
        r["v2_active"] = False  # 默认 v1 模式, 主函数会按 feature_flag 覆盖

    return results


# ═══════════ Phase 5: Persist ═══════════

async def _deep_persist_phase(session, results: list[dict], session_date) -> None:
    """UPSERT analysis_scores + INSERT recommendation_tracking.

    v4.8: 批量 executemany 减少 roundtrip (使用独立session避免事务abort影响).
    """
    import json

    if not results:
        return

    # ── 构建参数列表 ──
    analysis_params = []
    tracking_params = []
    for r in results:
        analysis_params.append({
            "sd": session_date, "sym": r["symbol"],
            "name": r.get("name", r["symbol"]),
            "ts": r.get("tech_score", 5.0), "ks": r.get("kline_score", 5.0),
            "fs": r.get("fund_score", 5.0), "sb": r.get("sector_bonus", 0),
            "cs": r.get("composite_score", 50), "fa": r.get("fundamental_adjustment", 0),
            "mc": r.get("market_correction", ""),
            "det": json.dumps(sanitize_for_json({
                "dimension_scores": r.get("dimension_scores", {}),
                "predicted_return": r.get("predicted_return"),
                "predicted_win_prob": r.get("predicted_win_prob"),
                "macro_adjustment": r.get("macro_adjustment", 0),
                "relative_position": r.get("relative_position"),
                "sector_direction": r.get("sector_direction"),
                "sector_lifecycle": r.get("sector_lifecycle"),
                "sector_rank_5d": r.get("sector_rank_5d"),
                "market_5d": r.get("market_5d"),
                "news_signal": r.get("news_signal"),
                "limit_up_flag": r.get("limit_up_flag"),
                "rank_score": r.get("rank_score"),
                # ── v4.9: 三层 Regime 系数 ──
                "market_coef": r.get("market_coef", 1.0),
                "sector_coef": r.get("sector_coef", 1.0),
                "final_regime_coef": r.get("final_regime_coef", 1.0),
                "regime_signal": r.get("regime_signal", "neutral"),
                "regime_signal_cn": r.get("regime_signal_cn", "中性"),
                # ── v7.0.10: v2 持仓期建议 (写入 details 供 /result/final 读取) ──
                "v2_active": r.get("v2_active", False),
                "best_horizon": r.get("best_horizon"),
                "best_strategy": r.get("best_strategy"),
                "v2_advice": r.get("v2_advice"),
                "v2_net": r.get("v2_net"),
                # ── v7.0.30: 铁三角死规则 (写入 details 供 /result/final 读取) ──
                "hard_rules_passed": r.get("hard_rules_passed", []),
                "hard_rules_failed": r.get("hard_rules_failed", []),
                "hard_rules_blocked": r.get("hard_rules_blocked", False),
                "hard_rules_summary": r.get("hard_rules_summary", ""),
                "v7_version": "v7.0.30",
            })),
            "arch": r.get("archetype", "small_speculative"),
            "ws": json.dumps(sanitize_for_json(r.get("weight_snapshot", {}))),
            "ar": json.dumps(sanitize_for_json(r.get("adjustment_reasons", []))),
            "dim": json.dumps(sanitize_for_json(r.get("dimension_scores", {}))),
            "wp": r.get("win_probability", 0.35), "dr": r.get("downside_risk", 5.0),
            "sq": r.get("signal_quality", 0.5), "tsc": r.get("trend_score", 5),
            "esc": r.get("entry_score", 5), "sc": r.get("signal_count", 0),
            "sl": r.get("strategy_label", None),
            # v7.0.32: 技术因子 14 字段
            "macd_dif": r.get("macd_dif"), "macd_dea": r.get("macd_dea"),
            "macd_bar": r.get("macd_bar"),
            "kdj_k": r.get("kdj_k"), "kdj_d": r.get("kdj_d"), "kdj_j": r.get("kdj_j"),
            "rsi_6": r.get("rsi_6"), "rsi_12": r.get("rsi_12"), "rsi_24": r.get("rsi_24"),
            "boll_upper": r.get("boll_upper"), "boll_mid": r.get("boll_mid"),
            "boll_lower": r.get("boll_lower"), "boll_width": r.get("boll_width"),
            "boll_pos": r.get("boll_pos"),
            "cci": r.get("cci"),
            # 筹码 7 字段 (后续从 daily_chip_perf JOIN)
            "cost_5pct": None, "cost_50pct": None, "cost_95pct": None,
            "weight_avg": None, "winner_rate": None,
            "cost_spread": None, "price_vs_cost": None,
        })
        tracking_params.append({
            "sd": session_date, "sym": r["symbol"], "rank": 0,
            "cs": r.get("composite_score", 50), "cp": r.get("close_price", 0),
        })

    # ── 使用独立session批量写入, 避免父session事务abort影响 ──
    from app.core.database import async_session_factory as _asf
    try:
        async with _asf() as ws:
            # v7.0.32: 同步拉取技术/筹码字段 (从 daily_chip_perf JOIN)
            chip_lookup = {}
            symbols = [r.get("symbol") for r in results if r.get("symbol")]
            if symbols:
                chip_rows = await ws.execute(text("""
                    SELECT ts_code, trade_date, cost_5pct, cost_50pct, cost_95pct, weight_avg, winner_rate
                    FROM daily_chip_perf
                    WHERE ts_code = ANY(:syms) AND trade_date = :sd
                """), {"syms": symbols, "sd": analysis_params[0]['sd'] if analysis_params else None})
                for c in chip_rows.fetchall():
                    chip_lookup[c.ts_code] = dict(c)

            # v7.0.32: 把新字段加进 params
            for params in analysis_params:
                sym = params['sym']
                chip = chip_lookup.get(sym, {})
                if chip:
                    params['cost_5pct'] = chip.get('cost_5pct')
                    params['cost_50pct'] = chip.get('cost_50pct')
                    params['cost_95pct'] = chip.get('cost_95pct')
                    params['weight_avg'] = chip.get('weight_avg')
                    params['winner_rate'] = chip.get('winner_rate')
                    # 算 cost_spread
                    c5 = chip.get('cost_5pct')
                    c95 = chip.get('cost_95pct')
                    if c5 is not None and c95 is not None:
                        params['cost_spread'] = c95 - c5
                    # 算 price_vs_cost
                    wavg = chip.get('weight_avg')
                    if wavg and wavg > 0:
                        # 找 close 价
                        close_row = await ws.execute(text('''
                            SELECT close FROM daily_kline
                            WHERE ts_code = :s AND trade_date = :d
                        '''), {"s": sym, "d": params['sd']})
                        cr = close_row.first()
                        if cr:
                            params['price_vs_cost'] = (float(cr.close) - wavg) / wavg * 100
                else:
                    params['cost_5pct'] = None
                    params['cost_50pct'] = None
                    params['cost_95pct'] = None
                    params['weight_avg'] = None
                    params['winner_rate'] = None
                    params['cost_spread'] = None
                    params['price_vs_cost'] = None

            await ws.execute(text("""
                INSERT INTO analysis_scores (
                    scan_date, symbol, name, tech_score, kline_score, fund_score,
                    sector_bonus, composite_score, fundamental_adjustment,
                    market_correction, details, archetype, weight_snapshot,
                    adjustment_reasons, dimension_scores, win_probability, downside_risk,
                    signal_quality, trend_score, entry_score, signal_count, strategy_label,
                    -- v7.0.32 新增 22 字段
                    macd_dif, macd_dea, macd_bar,
                    kdj_k, kdj_d, kdj_j,
                    rsi_6, rsi_12, rsi_24,
                    boll_upper, boll_mid, boll_lower, boll_width, boll_pos,
                    cci,
                    cost_5pct, cost_50pct, cost_95pct, weight_avg, winner_rate,
                    cost_spread, price_vs_cost
                ) VALUES (
                    :sd, :sym, :name, :ts, :ks, :fs,
                    :sb, :cs, :fa,
                    :mc, :det, :arch, :ws,
                    :ar, :dim, :wp, :dr,
                    :sq, :tsc, :esc, :sc, :sl,
                    :macd_dif, :macd_dea, :macd_bar,
                    :kdj_k, :kdj_d, :kdj_j,
                    :rsi_6, :rsi_12, :rsi_24,
                    :boll_upper, :boll_mid, :boll_lower, :boll_width, :boll_pos,
                    :cci,
                    :cost_5pct, :cost_50pct, :cost_95pct, :weight_avg, :winner_rate,
                    :cost_spread, :price_vs_cost
                ) ON CONFLICT (scan_date, symbol) DO UPDATE SET
                    name=EXCLUDED.name, tech_score=EXCLUDED.tech_score,
                    kline_score=EXCLUDED.kline_score, fund_score=EXCLUDED.fund_score,
                    sector_bonus=EXCLUDED.sector_bonus, composite_score=EXCLUDED.composite_score,
                    fundamental_adjustment=EXCLUDED.fundamental_adjustment,
                    market_correction=EXCLUDED.market_correction,
                    details=EXCLUDED.details, archetype=EXCLUDED.archetype,
                    weight_snapshot=EXCLUDED.weight_snapshot,
                    adjustment_reasons=EXCLUDED.adjustment_reasons,
                    dimension_scores=EXCLUDED.dimension_scores,
                    win_probability=EXCLUDED.win_probability,
                    downside_risk=EXCLUDED.downside_risk,
                    signal_quality=EXCLUDED.signal_quality,
                    trend_score=EXCLUDED.trend_score,
                    entry_score=EXCLUDED.entry_score,
                    signal_count=EXCLUDED.signal_count,
                    strategy_label=EXCLUDED.strategy_label,
                    -- v7.0.32: 同步更新新字段
                    macd_dif=EXCLUDED.macd_dif, macd_dea=EXCLUDED.macd_dea, macd_bar=EXCLUDED.macd_bar,
                    kdj_k=EXCLUDED.kdj_k, kdj_d=EXCLUDED.kdj_d, kdj_j=EXCLUDED.kdj_j,
                    rsi_6=EXCLUDED.rsi_6, rsi_12=EXCLUDED.rsi_12, rsi_24=EXCLUDED.rsi_24,
                    boll_upper=EXCLUDED.boll_upper, boll_mid=EXCLUDED.boll_mid,
                    boll_lower=EXCLUDED.boll_lower, boll_width=EXCLUDED.boll_width,
                    boll_pos=EXCLUDED.boll_pos,
                    cci=EXCLUDED.cci,
                    cost_5pct=EXCLUDED.cost_5pct, cost_50pct=EXCLUDED.cost_50pct,
                    cost_95pct=EXCLUDED.cost_95pct, weight_avg=EXCLUDED.weight_avg,
                    winner_rate=EXCLUDED.winner_rate,
                    cost_spread=EXCLUDED.cost_spread, price_vs_cost=EXCLUDED.price_vs_cost
            """), analysis_params)

            await ws.execute(text("""
                INSERT INTO recommendation_tracking (scan_date, symbol, rank, composite_score, close_price)
                VALUES (:sd, :sym, :rank, :cs, :cp)
                ON CONFLICT (scan_date, symbol) DO UPDATE SET
                    composite_score=EXCLUDED.composite_score, close_price=EXCLUDED.close_price
            """), tracking_params)

            await ws.commit()
            logger.info(f"Persisted {len(results)} analysis scores for {session_date}")
    except Exception as e:
        logger.error(f"Batch persist failed for {session_date}: {e}")


# ═══════════ Main Orchestrator ═══════════

async def deep_analyze(session, scan_date=None, session_date=None, min_composite_score=0, progress_cb=None):
    """14-dimension deep scoring pipeline — 5-phase orchestration.

    Phase 1: Preload all prerequisite data
    Phase 2: Score each stock on 13+ dimensions
    Phase 3: Enrich with gates, weights, bonuses
    Phase 4: Normalize within archetype + calibrate
    Phase 5: Persist to analysis_scores + recommendation_tracking

    Returns list of scored result dicts.
    """
    if scan_date is None:
        r = await session.execute(text("SELECT MAX(scan_date) FROM scan_results"))
        scan_date = r.scalar()
        if not scan_date:
            return []

    if session_date is None:
        session_date = scan_date

    # Phase 1: Preload
    if progress_cb: await progress_cb("preload", 1, 5, "加载指纹+K线+基本面...")
    # Phase 47: count L1 before filtering
    r = await session.execute(text(
        "SELECT COUNT(*) FILTER(WHERE COALESCE(level,'L1')='L1'), COUNT(*)"
        " FROM scan_results WHERE scan_date=:d"
    ), {"d": scan_date})
    l1_count, total_count = r.fetchone()
    if l1_count:
        logger.info(f"Phase 47: filtering {l1_count}/{total_count} L1 weak signals")
    # v4.9: 只过滤L1，nm_verdict检查在评分后执行
    r = await session.execute(text("""
        SELECT symbol FROM scan_results
        WHERE scan_date=:d AND COALESCE(level,'L1') != 'L1'
        ORDER BY symbol
    """), {"d": scan_date})
    symbols = [row[0] for row in r.fetchall()]
    if not symbols:
        logger.warning(f"No scan results (non-L1) for {scan_date}")
        return []
    # ── Phase 69: 涨跌停入口过滤 — 涨停封板股直接排除, 不进评分管线 ──
    r_chg = await session.execute(text("""
        WITH latest AS (
            SELECT DISTINCT ON (ts_code) ts_code, close,
                   LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date <= :d
            ORDER BY ts_code, trade_date DESC
        )
        SELECT ts_code, close, prev_close FROM latest WHERE prev_close > 0
    """), {"syms": symbols, "d": scan_date})
    sealed_syms: set[str] = set()
    for row in r_chg.fetchall():
        sym = row[0]; c = float(row[1]); pc = float(row[2])
        if pc <= 0: continue
        pct = round((c - pc) / pc * 100, 2)
        if sym.startswith('30') or sym.startswith('688'):    limit = 20.0
        elif sym.startswith('8') or sym.startswith('4'):     limit = 30.0
        else:                                                 limit = 10.0
        if pct >= limit * 0.95:
            sealed_syms.add(sym)

    if sealed_syms:
        symbols = [s for s in symbols if s not in sealed_syms]
        logger.info(f"Phase 69: excluding {len(sealed_syms)} limit-up stocks before scoring "
                    f"(remaining: {len(symbols)})")
    if not symbols:
        logger.warning(f"All symbols excluded by limit-up gate")
        return []

    ctx = await _deep_preload_phase(session, symbols, scan_date)

    # Phase 2: Score
    if progress_cb: await progress_cb("score", 2, 5, f"逐股评分 {len(symbols)}只...")
    results = await _deep_score_phase(session, ctx)
    if not results:
        logger.warning("No stocks passed scoring phase")
        return []

    # Phase 3: Enrich
    if progress_cb: await progress_cb("enrich", 3, 5, f"行业对比+板块门控 {len(results)}只...")
    results = await _deep_enrich_phase(session, results, ctx)

    # Phase 4: Normalize
    if progress_cb: await progress_cb("normalize", 4, 5, "组内归一化+模型预测...")

    # ── v4.9: 获取三层 Regime 系数 ──
    from app.services.regime_judger import get_regime_v2, REGIME_COEF
    from app.services.sector_regime import get_cached_regimes

    market_detail = await get_regime_v2(scan_date)
    market_coef = REGIME_COEF.get(market_detail.get("regime", "range"), 1.0)
    sector_regimes = await get_cached_regimes()
    sector_coefs = {code: info.get("coef", 1.0) for code, info in sector_regimes.items()}
    # ── v4.9 end ──

    results = _deep_normalize_phase(results, market_coef=market_coef, sector_coefs=sector_coefs)

    # ── v7.0.10: v2 feature_flag 批量分支 ──
    # v2 关闭时: 完全跳过, 0 延迟 (v1 14 维评分不受影响)
    # v2 开启时: 批量并发调 predict_optimal_horizon (asyncio.gather)
    try:
        from app.core.feature_flag import is_v2_active
        if await is_v2_active() and results:
            from app.services.deep_scorer_v2 import predict_optimal_horizon
            symbols = [r.get("symbol") for r in results if r.get("symbol")]
            v2_results = await asyncio.gather(
                *[predict_optimal_horizon(s) for s in symbols],
                return_exceptions=True
            )
            for r, v2r in zip(results, v2_results):
                if isinstance(v2r, Exception):
                    r["v2_active"] = False
                    r["v2_error"] = str(v2r)[:100]
                    continue
                if v2r and v2r.get("status") == "success":
                    r["best_horizon"] = v2r.get("best_horizon")
                    r["best_strategy"] = v2r.get("best_strategy")
                    r["v2_advice"] = v2r.get("advice", "")
                    r["v2_net"] = v2r.get("best_net", 0.0)
                    r["v2_active"] = True
                    # v7.0.16: 4-horizon 评分 (基于 verified 实际收益)
                    r["score_4h"] = v2r.get("score_4h", 6)  # 6-10
                    r["score_4h_detail"] = v2r.get("score_4h_detail", {})
                else:
                    r["best_horizon"] = None
                    r["v2_active"] = True  # flag 开但 no_data
    except Exception as e:
        # v2 全链路失败 → 降级 v1 (不影响主流程)
        logger.warning(f"v2 feature_flag branch failed: {e}")
        for r in results:
            r.setdefault("v2_active", False)

    # ★ v7.0.30 (铁三角实测校准): 死规则过滤器
    # 5 条硬规则剔除假信号 (不依赖 v2, 不依赖 ML, 基于 1915 行 verified_5d 验证)
    # v7.0.30: R2/R3 对 value_defensive / cyclical_resource 跳过; 阈值 -5%/-3%, +5%/+10%
    try:
        for r in results:
            rules_passed, rules_failed = _apply_hard_rules(r)
            r["hard_rules_passed"] = rules_passed
            r["hard_rules_failed"] = rules_failed
            r["hard_rules_blocked"] = len(rules_failed) > 0
            # 给人/前端看的一句话总结
            if rules_failed:
                r["hard_rules_summary"] = "❌ " + ", ".join(
                    f"{code}({reason})" for code, reason in rules_failed
                )
            else:
                r["hard_rules_summary"] = f"✅ 通过 {len(rules_passed)}/{len(rules_passed)+len(rules_failed)} 条"
    except Exception as e:
        logger.warning(f"hard rules filter failed: {e}")
        for r in results:
            r.setdefault("hard_rules_blocked", False)

    # ★ Predictive model blend (v4.8): 仅在 <=300 条时启用 (scan 路径样本太多)
    if len(results) <= 300:
        try:
            from app.services.predictive_scorer import batch_predict
            symbols_to_predict = [r["symbol"] for r in results]
            if symbols_to_predict and results:
                preds = await batch_predict(symbols_to_predict, scan_date, session)
                for r in results:
                    pred = preds.get(r["symbol"])
                    if pred:
                        r["predicted_return"] = pred["predicted_return"]
                        r["predicted_win_prob"] = pred["win_probability"]
                        if pred["predicted_return"] > 5:
                            r["composite_score"] = round(min(100, r["composite_score"] + 10), 1)
                        elif pred["predicted_return"] > 0:
                            r["composite_score"] = round(min(100, r["composite_score"] + 5), 1)
                        elif pred["predicted_return"] < -3:
                            r["composite_score"] = round(max(0, r["composite_score"] - 5), 1)
        except Exception as e:
            logger.warning(f"Predictive model unavailable: {e}")

    # ★ Phase 55: 排序学习注入 — 模型学会"同批中谁更强"
    if len(results) >= 10:
        try:
            from app.services.predictive_scorer import rank_stocks
            symbols_to_rank = [r["symbol"] for r in results]
            rank_scores = await rank_stocks(symbols_to_rank, scan_date, session)
            if rank_scores:
                for r in results:
                    rs = rank_scores.get(r["symbol"])
                    if rs is not None:
                        r["rank_score"] = round(rs, 3)
                        # 排序分 ≥ 0.7 → 排序器高度确信该股在同批中更强
                        if rs >= 0.7:
                            r["composite_score"] = round(min(100, r["composite_score"] + 5), 1)
                        elif rs <= 0.3:
                            r["composite_score"] = round(max(0, r["composite_score"] - 5), 1)
        except Exception as e:
            logger.debug(f"Ranker unavailable: {e}")

    # ── Phase 68: 去重 — 全局 100 分只能有一个，98+ 按 raw_total 微调拉开 ──
    if len(results) >= 2:
        sorted_all = sorted(results, key=lambda r: (r.get("composite_score", 0), r.get("raw_total", 0)), reverse=True)
        dup_count: dict[float, int] = {}
        for r in sorted_all:
            cs = round(r.get("composite_score", 0), 1)
            if cs >= 98:
                n = dup_count.get(cs, 0)
                if n >= 1:
                    penalty = n * 0.8
                    r["composite_score"] = round(max(85.0, cs - penalty), 1)
                dup_count[cs] = n + 1

    # Phase 5: Persist
    if progress_cb: await progress_cb("persist", 5, 5, f"写入数据库 {len(results)}条...")
    await _deep_persist_phase(session, results, session_date)

    # Filter by min quality gate
    filtered = [r for r in results if r.get("composite_score", 0) >= min_composite_score]

    logger.info(f"deep_analyze complete: {len(results)} scored, {len(filtered)} passed gate")
    return filtered
