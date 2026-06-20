# -*- coding: utf-8 -*-
"""v2 评分训练器 — 4 horizon × 2 model_type 独立训练 (v7.0).

从 scoring_trainer.py 复制, 改 3 处:
  1. load_training_data → load_training_data_v2 (加 horizon_days + model_type 参数)
  2. 持久化目标表 param_library → param_library_v2
  3. 标签生成: win 取原 profit, loss 取反

8 套独立训练:
  T+2_win / T+2_loss
  T+3_win / T+3_loss
  T+5_win / T+5_loss
  T+10_win / T+10_loss

样本不足 (n<30) 跳过 + warning, 保留占位等数据积累.
"""
import asyncio
import json
import logging
import numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("scoring_trainer_v2")

# ── v7.0.4: 与 analysis_scores.dimension_scores 真实 schema 对齐 ──
# 真实 schema: 27 个顶层 key (kebab-case), value 是 {"score": float, "raw": ...} 结构
# 我们提取每个维度的 score 子字段作为特征
DIM_KEYS = [
    'tg_momentum', 'dist_low', 'j_value', 'technical', 'kline_game',
    'vol_ratio', 'arbr', 'bbi', 'trend_deviation', 'downside_risk',
    'ma_trend', 'multi_box', 'market_relative', 'fund_flow', 'sector_alpha',
    'fundamentals', 'valuation', 'weekly_resonance', 'toplist_sector',
    'ambush', 'chip_winner', 'chip_cost',
    # v7.0.32 新增 5 维 (从 analysis_scores 直接读)
    'macd', 'kdj', 'boll', 'cci', 'chip_winner_rate',
]

EXTRA_DIM_KEYS = []  # v7.0.4 取消, 真实 schema 已含 27 维

# 兼容旧 schema (flat keys like tech_score) — 缺失时当 0
LEGACY_DIM_KEYS = [
    'tech_score', 'kline_score', 'fund_score', 'tg_momentum_score',
    'vol_ratio_score', 'arbr_score', 'sector_alpha_score',
    'market_relative_score', 'valuation_score', 'ma_trend_score',
    'pattern_score', 'trend_deviation_score', 'bbi_score', 'box_score',
    'ambush_score', 'toplist_sector_score',
    'real_fund_score', 'northbound_score', 'institutional_score', 'shareholder_score',
]


def _extract_score(dims: dict, key: str) -> float:
    """从 dimension_scores 提取一维分数.

    优先: 嵌套 schema (key.score)
    回退: 旧 schema (key_score) 或 平铺 (key)
    """
    if not dims:
        return 0.0
    # 嵌套: {"score": 5.0, ...}
    v = dims.get(key)
    if isinstance(v, dict) and 'score' in v:
        return float(v['score'])
    # 平铺
    if isinstance(v, (int, float)):
        return float(v)
    # 兼容旧 _score 后缀
    legacy = dims.get(f"{key}_score")
    if isinstance(legacy, (int, float)):
        return float(legacy)
    if isinstance(legacy, dict) and 'score' in legacy:
        return float(legacy['score'])
    return 0.0


# 旧的 WEIGHT_PARAM_NAMES 不再使用 (param_library 持久化字段为 jsonb, 不需要映射)
WEIGHT_PARAM_NAMES = {}

MIN_SAMPLES_FOR_TRAINING = 30

# v7.0.12 (用户 A 方案): 原型分类 + 样本兜底
# 每个原型独立训练, 但样本 < 50 时降级到混训 (用所有原型样本)
MIN_SAMPLES_PER_PROTOTYPE = 50
FALLBACK_PROTOTYPES = {"__global__", "manual"}  # 这些永远用全局模型 (历史不足, 无法拆)

# ── v2 配置 ──
ALL_HORIZONS = [2, 3, 5, 10]
ALL_MODEL_TYPES = ["win", "loss"]
TARGET_TABLE = "param_library_v2"

# horizon → 业务策略 (与 shadow_trainer 保持一致)
HORIZON_TO_STRATEGY = {2: "S1", 3: "S1", 5: "S2", 10: "S3"}


async def load_training_data_v2(
    lookback_days: int = 880,  # v7.0.34: 730 → 880 (覆盖 2024-02 ~ 2026-06 完整周期)
    horizon_days: int = 5,
    model_type: str = "win",
    archetype: str = "__global__",  # v7.0.12: 新增原型过滤参数
    market_style: str = "all",  # v7.0.33: 新增市场风格过滤 (bull/bear/range/all)
) -> tuple:
    """v2: 从 recommendation_tracking 加载指定 horizon + model_type 训练数据.

    v7.0.6 用户口径: 盈利和避坑真正独立训练 (不是 51/49 对应关系)
    - win 模式: 标签 = was_profitable_Nd (盈利=1, 亏损=0)
    - loss 模式: 标签 = NOT was_profitable_Nd (亏损=1, 盈利=0) - 反例独立训练

    v7.0.12 (A 方案): archetype 过滤
    - archetype='__global__': 全部样本 (兜底模式, 用于小原型或通用)
    - archetype='small_speculative' 等: 只取该原型的样本
    - 内部自动检查样本量, < 50 时降级到混训

    v7.0.33: market_style 过滤 (按市场风格分组训练)
    - market_style='all': 不过滤, 用全部样本 (老行为)
    - market_style='bull'/'bear'/'range': 只用该 phase 期间的样本
    - phase 判定: 700001.TI 当天 LAG(10) close + ±2% (与 v1 一致)

    Returns: (X, y, symbols, feature_names)
    """
    if horizon_days not in ALL_HORIZONS:
        raise ValueError(f"horizon_days must be one of {ALL_HORIZONS}")
    if model_type not in ALL_MODEL_TYPES:
        raise ValueError(f"model_type must be one of {ALL_MODEL_TYPES}")
    if market_style not in ("all", "bull", "bear", "range"):
        raise ValueError(f"market_style must be one of all/bull/bear/range, got {market_style}")

    profit_col = f"was_profitable_{horizon_days}d"
    return_col = f"return_{horizon_days}d"
    verified_col = f"verified_{horizon_days}d"
    cutoff = date.today() - timedelta(days=lookback_days)

    # v7.0.33: market_phase 过滤条件 (market_style != 'all' 时启用)
    phase_filter = ""
    if market_style != "all":
        phase_filter = "AND mp.phase = :ms"
    # v7.0.34: 训练数据排除踢出名单 + 低价股 + 涨停股 (与 scan_all_stocks 保持一致)
    #   1. 排除 exclusion_list (5 reasons 全部)
    #   2. 排除股价 < 5 元
    #   3. 排除当日涨停 (按板 9.5% / 19.5% / 29.5%)
    quality_filter = """
      AND NOT EXISTS (
        SELECT 1 FROM exclusion_list ex
        WHERE ex.symbol = rt.symbol
          AND (ex.expires_at IS NULL OR ex.expires_at > NOW())
      )
      AND rt.close_price >= 5.0
      AND NOT EXISTS (
        SELECT 1 FROM daily_kline dk
        WHERE dk.ts_code = rt.symbol
          AND dk.trade_date = (
            SELECT MAX(trade_date) FROM daily_kline
            WHERE ts_code = rt.symbol AND trade_date <= rt.scan_date
          )
          AND (
            ((dk.ts_code LIKE '6%' OR dk.ts_code LIKE '00%') AND dk.close / NULLIF(
                (SELECT close FROM daily_kline dk2
                 WHERE dk2.ts_code = dk.ts_code AND dk2.trade_date < dk.trade_date
                 ORDER BY dk2.trade_date DESC LIMIT 1), 0) - 1 >= 0.095)
            OR ((dk.ts_code LIKE '30%' OR dk.ts_code LIKE '688%') AND dk.close / NULLIF(
                (SELECT close FROM daily_kline dk2
                 WHERE dk2.ts_code = dk.ts_code AND dk2.trade_date < dk.trade_date
                 ORDER BY dk2.trade_date DESC LIMIT 1), 0) - 1 >= 0.195)
            OR ((dk.ts_code LIKE '8%' OR dk.ts_code LIKE '4%') AND dk.close / NULLIF(
                (SELECT close FROM daily_kline dk2
                 WHERE dk2.ts_code = dk.ts_code AND dk2.trade_date < dk.trade_date
                 ORDER BY dk2.trade_date DESC LIMIT 1), 0) - 1 >= 0.295)
          )
      )
    """
    # market_phases CTE: 给每天打 phase 标签 (与 v1 一致, 用 700001.TI)
    market_phases_cte = """
    market_phases AS (
        SELECT trade_date,
               CASE
                 WHEN LAG(close, 10) OVER (ORDER BY trade_date) IS NULL THEN 'range'
                 WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date))
                      / NULLIF(LAG(close, 10) OVER (ORDER BY trade_date), 0) * 100 > 2.0
                 THEN 'bull'
                 WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date))
                      / NULLIF(LAG(close, 10) OVER (ORDER BY trade_date), 0) * 100 < -2.0
                 THEN 'bear'
                 ELSE 'range'
               END as phase
        FROM daily_kline WHERE ts_code = '700001.TI'
    )
    """

    # v7.0.12 (A 方案): 原型过滤 + 样本兜底
    # 如果 archetype='__global__' 或该原型样本 < 50 → 降级到混训 (用所有原型)
    actual_archetype = archetype
    if archetype != "__global__":
        # v7.0.33: 原型计数也加 phase 过滤 (与主查询保持一致)
        proto_phase_filter = phase_filter
        async with async_session_factory() as _s:
            cnt = await _s.execute(text(f"""
                WITH {market_phases_cte}
                SELECT COUNT(*) FROM recommendation_tracking rt
                JOIN analysis_scores a ON a.symbol=rt.symbol AND a.scan_date=rt.scan_date
                JOIN market_phases mp ON mp.trade_date = rt.scan_date
                WHERE rt.scan_date >= :cut
                  AND rt.{verified_col} = TRUE
                  AND rt.{profit_col} IS NOT NULL
                  AND a.dimension_scores IS NOT NULL
                  AND rt.archetype = :arch
                  {proto_phase_filter}
                  {quality_filter}
            """), {"cut": cutoff, "arch": archetype, **({"ms": market_style} if market_style != "all" else {})})
            n_proto = cnt.scalar() or 0
            if n_proto < MIN_SAMPLES_PER_PROTOTYPE:
                logger.info(f"[v2] {archetype} 样本 {n_proto} < {MIN_SAMPLES_PER_PROTOTYPE}, 降级到混训兜底")
                actual_archetype = "__global__"

    # v7.0.6 用户口径: 盈利和避坑真正独立训练 (不是 51/49 对应关系)
    # 三种方案对比:
    # 方案 A: win+loss 同 532 样本, 标签反转 → 系数反号 (数学必然, 数据量镜像)
    # 方案 B: win 用全部, loss 只用亏损票 (335) → 数据量不镜像, 真正独立
    # 方案 C: win 用盈利票, loss 用亏损票 (双方各半) → 真正独立, 双方都看正样本
    # 当前: 方案 B — loss 独立训练只用亏损票, win 用全部 (包含亏损作负样本)

    async with async_session_factory() as s:
        # v7.0.3: 改严格同日 JOIN (避免 LATERAL 跨日错配数据)
        # v7.0.6: loss 模型只取亏损票 (方案 B — win/loss 真正独立, 数据量不镜像)
        # v7.0.33: LEFT JOIN market_phases 给每条样本打 phase 标签
        if model_type == "loss":
            # loss: 只看亏损票 → 学习"什么特征→跌"
            # 但要平衡正负样本, 取亏损票 1:N 的盈利票 (N=1)
            # v7.0.12: 加 archetype 过滤 (win_sample 也用同一原型)
            loss_arch_filter = ""
            loss_params = {"cut": cutoff}
            if actual_archetype != "__global__":
                loss_arch_filter = "AND rt.archetype = :arch"
                loss_params["arch"] = actual_archetype
            if market_style != "all":
                loss_params["ms"] = market_style
            loss_query = f"""
                WITH {market_phases_cte},
                loss_set AS (
                    SELECT rt.symbol, rt.{profit_col}, rt.{return_col}, rt.{verified_col},
                           a.dimension_scores, a.archetype, a.composite_score, mp.phase
                    FROM recommendation_tracking rt
                    JOIN analysis_scores a ON a.symbol=rt.symbol AND a.scan_date=rt.scan_date
                    JOIN market_phases mp ON mp.trade_date = rt.scan_date
                    WHERE rt.scan_date >= :cut
                      AND rt.{verified_col} = TRUE
                      AND rt.{profit_col} = FALSE
                      AND a.dimension_scores IS NOT NULL
                      {loss_arch_filter}
                      {phase_filter}
                      {quality_filter}
                ),
                win_sample AS (
                    SELECT rt.symbol, rt.{profit_col}, rt.{return_col}, rt.{verified_col},
                           a.dimension_scores, a.archetype, a.composite_score, mp.phase
                    FROM recommendation_tracking rt
                    JOIN analysis_scores a ON a.symbol=rt.symbol AND a.scan_date=rt.scan_date
                    JOIN market_phases mp ON mp.trade_date = rt.scan_date
                    WHERE rt.scan_date >= :cut
                      AND rt.{verified_col} = TRUE
                      AND rt.{profit_col} = TRUE
                      AND a.dimension_scores IS NOT NULL
                      {loss_arch_filter}
                      {phase_filter}
                      {quality_filter}
                    ORDER BY RANDOM() LIMIT (SELECT COUNT(*) FROM loss_set)
                )
                SELECT * FROM loss_set
                UNION ALL
                SELECT * FROM win_sample
            """
            r = await s.execute(text(loss_query), loss_params)
        else:
            # win 模型: 全部样本, 标签=was_profitable
            # v7.0.12: 加 archetype 过滤 (actual_archetype 是 __global__ 时不过滤)
            # v7.0.33: 加 market_phases JOIN + phase 过滤
            archetype_filter = ""
            win_params = {"cut": cutoff}
            if actual_archetype != "__global__":
                archetype_filter = "AND rt.archetype = :arch"
                win_params["arch"] = actual_archetype
            if market_style != "all":
                win_params["ms"] = market_style
            r = await s.execute(text(f"""
                WITH {market_phases_cte}
                SELECT rt.symbol, rt.{profit_col}, rt.{return_col}, rt.{verified_col},
                       a.dimension_scores, a.archetype, a.composite_score, mp.phase
                FROM recommendation_tracking rt
                JOIN analysis_scores a ON a.symbol=rt.symbol AND a.scan_date=rt.scan_date
                JOIN market_phases mp ON mp.trade_date = rt.scan_date
                WHERE rt.scan_date >= :cut
                  AND rt.{verified_col} = TRUE
                  AND rt.{profit_col} IS NOT NULL
                  AND a.dimension_scores IS NOT NULL
                  {archetype_filter}
                  {phase_filter}
                  {quality_filter}
                ORDER BY rt.scan_date DESC
            """), win_params)
        rows = r.fetchall()

    if not rows:
        return np.array([]), np.array([]), [], []

    X_rows, y_rows, symbols = [], [], []
    for row in rows:
        # v7.0.33: row 多了 phase 字段 (mp.phase)
        sym, profit, ret, verified, dims_raw, arch, sc = row[:7]
        phase = row[7] if len(row) > 7 else None
        dims = dims_raw if isinstance(dims_raw, dict) else (json.loads(dims_raw) if dims_raw else None)
        if not dims:
            continue
        # v7.0.4: 用真实 schema 提取特征
        features = [_extract_score(dims, k) for k in DIM_KEYS]
        # composite_score 仍作为额外特征
        features.append(float(sc or 50))

        # ★ v7.0.6 用户口径: 盈利和避坑两套独立模型 (不是 51/49 对应关系)
        # 数学约束: 在"全量同数据"下, win=标签=盈利, loss=NOT 标签=盈利 必然系数反号
        #   (logistic regression 关于 y↔1-y 对称, 系数 w_loss = -w_win)
        # 真正独立必须用不同样本: win=只用盈利票, loss=只用亏损票 (样本减半但特征方向不同)
        # 训练入口 train_single 已按 model_type 过滤样本, 这里只算基础标签
        if model_type == "win":
            y = 1 if profit else 0
        else:  # loss
            y = 1 if not profit else 0

        X_rows.append(features)
        y_rows.append(y)
        symbols.append(sym)

    X = np.array(X_rows)
    y = np.array(y_rows)
    feature_names = DIM_KEYS + ['composite_score']
    logger.info(f"[v2] Loaded {len(y_rows)} samples, horizon=T+{horizon_days}, model={model_type}, "
                f"positive_rate={y.mean()*100:.1f}%")
    return X, y, symbols, feature_names


async def train_single(horizon: int, model_type: str, lookback_days: int = 880, archetype: str = "__global__", market_style: str = "all") -> dict:
    """训练单套: (horizon, model_type, archetype, market_style).

    v7.0.12 (A 方案): archetype 参数
    - archetype='__global__': 全部样本 (兜底)
    - archetype='large_bluechip' 等: 该原型独立训练
    - 内部样本兜底 (load_training_data_v2 自动处理样本不足 50)

    v7.0.33: market_style 参数 + 缺样本降级
    - market_style='all': 全部样本
    - market_style='bull'/'bear'/'range': 仅该 phase 样本
    - 若 phase 样本 < MIN_SAMPLES (30), 自动 fallback to 'all'
    - 返回的 market_style_actual 字段告诉 _persist_v2 实际用了哪个
    """
    key = f"T+{horizon}_{model_type}_{archetype}_{market_style}"
    market_style_actual = market_style  # 记录实际用的风格 (降级时改)
    try:
        X, y, syms, fns = await load_training_data_v2(lookback_days, horizon, model_type, archetype, market_style)
    except Exception as e:
        return {
            "key": key, "horizon": horizon, "model_type": model_type, "archetype": archetype,
            "market_style": market_style, "market_style_actual": market_style,
            "status": "error", "stage": "load", "detail": str(e),
        }

    # v7.0.33: 缺样本降级 — market_style 不是 all 且样本不足 → fallback to all
    if len(y) < MIN_SAMPLES_FOR_TRAINING and market_style != "all":
        logger.warning(f"[v2] {key} 样本 {len(y)} < {MIN_SAMPLES_FOR_TRAINING}, fallback to 'all'")
        market_style_actual = "all"
        try:
            X, y, syms, fns = await load_training_data_v2(lookback_days, horizon, model_type, archetype, "all")
        except Exception as e:
            return {
                "key": key, "horizon": horizon, "model_type": model_type, "archetype": archetype,
                "market_style": market_style, "market_style_actual": "all",
                "status": "error", "stage": "load_fallback", "detail": str(e),
            }

    if len(y) < MIN_SAMPLES_FOR_TRAINING:
        return {
            "key": key, "horizon": horizon, "model_type": model_type, "archetype": archetype,
            "market_style": market_style, "market_style_actual": market_style_actual,
            "status": "skipped",
            "n_samples": len(y),
            "reason": f"样本不足 {len(y)} < {MIN_SAMPLES_FOR_TRAINING}, 保留占位等数据积累",
        }

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import cross_val_score

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        lr = LogisticRegression(max_iter=1000, C=1.0, class_weight="balanced", random_state=42)
        cv_scores = cross_val_score(lr, X_scaled, y, cv=5, scoring="roc_auc")
        cv_auc = float(cv_scores.mean())

        lr.fit(X_scaled, y)
        train_acc = float(lr.score(X_scaled, y))

        # 提取权重
        weights = {fns[i]: float(lr.coef_[0][i]) for i in range(len(fns))}
        weights["_intercept_"] = float(lr.intercept_[0])

        return {
            "key": key, "horizon": horizon, "model_type": model_type, "archetype": archetype,
            "market_style": market_style, "market_style_actual": market_style_actual,
            "status": "success",
            "n_samples": len(y),
            "win_rate": float(y.mean()),
            "cv_auc": round(cv_auc, 4),
            "train_acc": round(train_acc, 4),
            "weights": weights,
        }
    except Exception as e:
        logger.error(f"train_single({key}) failed: {e}", exc_info=True)
        return {
            "key": key, "horizon": horizon, "model_type": model_type, "archetype": archetype,
            "market_style": market_style, "market_style_actual": market_style_actual,
            "status": "error", "stage": "fit", "detail": str(e),
        }


async def _persist_v2(result: dict, market_style: str = "all"):
    """写权重到 param_library_v2 表.

    v7.0.12 (A 方案): archetype 字段写入 (替代写死 '__global__')
    v7.0.13 (regime): market_style 字段 (bull/bear/range/all)
    v7.0.33: 使用 result['market_style_actual'] (降级后实际生效的风格)
    """
    horizon = result["horizon"]
    mt = result["model_type"]
    archetype = result.get("archetype", "__global__")
    weights = result["weights"]
    cv_auc = result["cv_auc"]
    strategy = HORIZON_TO_STRATEGY[horizon]
    # v7.0.33: 用实际生效的 market_style (避免 phase 样本不足时用 phase 标签写库)
    market_style_actual = result.get("market_style_actual", market_style)
    version = f"v2-{date.today().isoformat()}-T+{horizon}-{mt}-{archetype}-{market_style_actual}"

    async with async_session_factory() as s:
        # 1. 停用该 (strategy, horizon, model_type, archetype, market_style) 旧激活
        await s.execute(text(f"""
            UPDATE {TARGET_TABLE}
            SET is_active = false, updated_at = NOW()
            WHERE strategy = :st AND horizon_days = :h AND model_type = :mt
              AND archetype = :arch AND market_style = :ms AND is_active = true
        """), {"st": strategy, "h": horizon, "mt": mt, "arch": archetype, "ms": market_style_actual})

        # 2. 写入新激活
        await s.execute(text(f"""
            INSERT INTO {TARGET_TABLE} (
              id, archetype, strategy, horizon_days, model_type,
              is_shadow, scoring_weights, discrimination,
              converge_status, last_trained_at, version, is_active,
              n_samples, cv_auc, win_rate, market_style,
              created_at, updated_at
            ) VALUES (
              gen_random_uuid(), :arch, :st, :h, :mt,
              false, CAST(:w AS jsonb), :disc,
              'active', NOW(), :v, true,
              :n, :cv, :wr, :ms,
              NOW(), NOW()
            )
        """), {
            "arch": archetype,
            "st": strategy, "h": horizon, "mt": mt,
            "w": json.dumps(weights), "disc": round(cv_auc, 4),
            "v": version,
            "n": result["n_samples"],
            "cv": round(cv_auc, 4),
            "wr": round(result["win_rate"], 4),
            "ms": market_style_actual,
        })
        await s.commit()
    logger.info(f"[v2] Persisted {strategy}/T+{horizon}/{mt}/{market_style_actual}: cv_auc={cv_auc:.4f}")


async def train_4x2(lookback_days: int = 880, archetypes: list | None = None, dry_run: bool = False, market_style: str = "all") -> dict:
    """4×2 全量训练: 8 套独立权重, 写入 param_library_v2.

    v7.0.12 (A 方案): archetypes 参数
    - archetypes=None: 只训全局 (__global__) 8 套, 与原版兼容
    - archetypes=['__global__', 'small_speculative', ...]: 每个原型训 8 套
    - 样本不足 50 的原型自动降级到 __global__ (load_training_data_v2 处理)

    样本不足 (n<30) 跳过 + warning, 保留占位.

    v7.0.13 (新增): dry_run 参数 — 训练不写库, 不污染 is_active 激活权重
    - dry_run=True: 只训练 + 返回结果, 不调 _persist_v2
    - dry_run=False (默认): 跟原版一致, 写库覆盖 is_active

    v7.0.14 (regime): market_style 参数 — 写入权重时标记市场风格
    - market_style='bull'/'bear'/'range'/'all'
    - 同一 (strategy, horizon, model_type, archetype) 多个 market_style 互不覆盖

    v7.0.33: market_style=None 默认按当前市场状态自动检测
    - 调用 get_current_regime_simple() 获取当前 bull/bear/range
    - 解决"牛训的权重在熊市失效"的泛化问题
    """
    if archetypes is None:
        # 默认只训全局 (兼容旧接口)
        archetypes = ["__global__"]

    # v7.0.33: 默认按当前市场状态训练 (auto-detect)
    if market_style is None:
        try:
            from app.services.market_gate import get_current_regime_simple
            market_style = await get_current_regime_simple()
            logger.info(f"[v2] Auto-detected market_style: {market_style}")
        except Exception as e:
            logger.warning(f"[v2] get_current_regime_simple failed: {e}, fallback to 'all'")
            market_style = "all"

    logger.info(f"[v2] train_4x2 start, lookback_days={lookback_days}, archetypes={archetypes}, dry_run={dry_run}, market_style={market_style}")
    results = {}
    persisted = []

    for arch in archetypes:
        for horizon in ALL_HORIZONS:
            for mt in ALL_MODEL_TYPES:
                r = await train_single(horizon, mt, lookback_days, archetype=arch, market_style=market_style)
                results[r["key"]] = r
                if r["status"] == "success" and not dry_run:
                    try:
                        # v7.0.33: 用 result 里的 actual (避免降级后写错标签)
                        await _persist_v2(r, market_style=market_style)
                        persisted.append({"archetype": arch, "horizon": horizon, "model_type": mt,
                                          "cv_auc": r["cv_auc"],
                                          "market_style_actual": r.get("market_style_actual", market_style)})
                    except Exception as e:
                        logger.error(f"persist {r['key']} failed: {e}", exc_info=True)
                elif r["status"] == "success" and dry_run:
                    actual = r.get("market_style_actual", market_style)
                    logger.info(f"[v2] dry_run: 跳过持久化 {r['key']}/{actual} (requested={market_style}), cv_auc={r.get('cv_auc')}")

    n_ok = sum(1 for v in results.values() if v.get("status") == "success")
    n_skip = sum(1 for v in results.values() if v.get("status") == "skipped")
    n_err = sum(1 for v in results.values() if v.get("status") == "error")
    logger.info(f"[v2] train_4x2 done: success={n_ok}, skipped={n_skip}, error={n_err}, dry_run={dry_run}, market_style={market_style}")

    return {
        "method": "4x2_v2_prototype" if len(archetypes) > 1 else "4x2_v2",
        "trained": results,
        "persisted": persisted,
        "n_success": n_ok,
        "n_skipped": n_skip,
        "n_error": n_err,
    }


async def get_4x2_status(market_style: str | None = None) -> dict:
    """查询 8 套权重状态 — 供前端展示.

    v7.0.33: 加 market_style 参数
    - market_style=None: 返回当前 regime 的激活权重 (供前端 dashboard)
    - market_style='all'/'bull'/'bear'/'range': 返回指定 regime 的激活权重
    - 同时按 (archetype, market_style) 去重, 不再有"8 套互相覆盖"问题
    """
    if market_style is None:
        from app.services.market_gate import get_current_regime_simple
        try:
            market_style = await get_current_regime_simple()
        except Exception:
            market_style = "all"

    async with async_session_factory() as s:
        # 按 (horizon, model_type, archetype, market_style) 取最新激活
        # 同一 (h, mt, arch, ms) 只能有 1 行 active (由 _persist_v2 保证)
        # 加 market_style 过滤避免多 regime 互相覆盖
        r = await s.execute(text(f"""
            SELECT horizon_days, model_type, archetype, market_style,
                   discrimination, last_trained_at, version, cv_auc, n_samples, win_rate
            FROM {TARGET_TABLE}
            WHERE is_active = true AND archetype = '__global__'
              AND (CAST(:ms AS text) = 'all' OR market_style = CAST(:ms AS text))
            ORDER BY horizon_days, model_type
        """), {"ms": market_style})
        active = {(row[0], row[1]): {
            "disc": float(row[4]) if row[4] else 0,
            "archetype": row[2],
            "market_style": row[3],
            "trained_at": str(row[5]) if row[5] else None,
            "version": row[6],
            "cv_auc": float(row[7]) if row[7] else 0,
            "n_samples": row[8] or 0,
            "win_rate": float(row[9]) if row[9] else 0,
        } for row in r.fetchall()}

    panel = []
    for h in ALL_HORIZONS:
        for mt in ALL_MODEL_TYPES:
            info = active.get((h, mt), {"disc": 0, "trained_at": None, "version": "untrained",
                                          "cv_auc": 0, "n_samples": 0, "win_rate": 0,
                                          "archetype": "__global__", "market_style": market_style})
            panel.append({
                "horizon_days": h,
                "model_type": mt,
                "key": f"T+{h}_{mt}",
                "strategy": HORIZON_TO_STRATEGY[h],
                "archetype": info.get("archetype", "__global__"),
                "market_style": info.get("market_style", market_style),
                "discrimination": info["disc"],
                "cv_auc": info["cv_auc"],
                "trained_at": info["trained_at"],
                "version": info["version"],
                "n_samples": info["n_samples"],
                "win_rate": info["win_rate"],
                "can_upgrade": info["disc"] > 0.55 and info["n_samples"] >= MIN_SAMPLES_FOR_TRAINING,
            })
    return {"panels": panel, "count": len(panel), "market_style": market_style}
