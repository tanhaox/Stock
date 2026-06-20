"""评分权重训练器 v1.0 — 基于真实盈亏反馈训练维度权重.

这是整个学习闭环的核心引擎，替换过去人工猜测的 DEFAULT_WEIGHTS。

训练数据流:
  analysis_scores (dimension_scores) + recommendation_tracking (was_profitable_3d/5d)
  → Logistic Regression (真实盈亏作为标签)
  → 系数提取 → 新权重
  → 写入 param_library (is_active=true)
  → 接入 Bayesian 优化器观测
  → deep_scorer 下次评分时自动加载

注意: 训练数据 100% 来自真实历史行情和盈亏反馈，不允许任何模拟数据混入。
"""
import asyncio
import json
import logging
import numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("scoring_trainer")

# ── 维度键 (与 deep_scorer dimension_scores 对齐) ──
DIM_KEYS = [
    'tech_score', 'kline_score', 'fund_score', 'tg_momentum_score',
    'vol_ratio_score', 'arbr_score', 'sector_alpha_score',
    'market_relative_score', 'valuation_score', 'ma_trend_score',
    'pattern_score', 'trend_deviation_score', 'bbi_score', 'box_score',
    'ambush_score',
    # ★ v4.3: 龙虎榜板块资金流向
    'toplist_sector_score',
]

# 增强维度（如果存在）
EXTRA_DIM_KEYS = [
    'real_fund_score', 'northbound_score', 'institutional_score', 'shareholder_score',
]

# 默认权重映射到 param_library 的参数名
WEIGHT_PARAM_NAMES = {
    'tech_score': 'tech_weight',
    'kline_score': 'kline_weight',
    'fund_score': 'fund_weight',
    'tg_momentum_score': 'tg_momentum_weight',
    'vol_ratio_score': 'vol_ratio_weight',
    'arbr_score': 'arbr_weight',
    'sector_alpha_score': 'sector_alpha_weight',
    'market_relative_score': 'market_relative_weight',
    'valuation_score': 'valuation_weight',
    'ma_trend_score': 'ma_trend_weight',
    'pattern_score': 'pattern_weight',
    'trend_deviation_score': 'trend_deviation_weight',
    'bbi_score': 'bbi_weight',
    'box_score': 'box_weight',
    'ambush_score': 'ambush_weight',
    # ★ v4.3: 龙虎榜板块资金流向
    'toplist_sector_score': 'toplist_sector_weight',
}

MIN_SAMPLES_FOR_TRAINING = 30


async def load_training_data(lookback_days: int = 120) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """从 recommendation_tracking + analysis_scores 加载训练数据.

    返回: (X, y, symbols, feature_names)
    - X: 维度评分矩阵 (n_samples, n_features)
    - y: 盈亏标签 (0/1 — was_profitable_3d)
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT rt.symbol, rt.was_profitable_3d, rt.was_profitable_5d,
                   a.dimension_scores, a.archetype, a.composite_score
            FROM recommendation_tracking rt
            JOIN analysis_scores a ON a.symbol = rt.symbol AND a.scan_date = rt.scan_date
            WHERE rt.scan_date >= :cut
              AND rt.was_profitable_3d IS NOT NULL
              AND a.dimension_scores IS NOT NULL
            ORDER BY rt.scan_date DESC
        """), {"cut": cutoff})
        rows = r.fetchall()

    if not rows:
        logger.warning("No training data available")
        return np.array([]), np.array([]), [], []

    X_rows = []
    y_rows = []
    symbols = []

    for row in rows:
        sym, p3, p5, dims_raw, arch, sc = row
        dims = dims_raw if isinstance(dims_raw, dict) else (json.loads(dims_raw) if dims_raw else None)
        if not dims:
            continue

        features = []
        for k in DIM_KEYS:
            features.append(float(dims.get(k, 5.0)))
        for k in EXTRA_DIM_KEYS:
            features.append(float(dims.get(k, 0)))
        features.append(float(sc or 50))

        X_rows.append(features)
        y_rows.append(1 if p3 else 0)
        symbols.append(sym)

    X = np.array(X_rows)
    y = np.array(y_rows)
    feature_names = DIM_KEYS + EXTRA_DIM_KEYS + ['composite_score']

    logger.info(f"Loaded {len(y_rows)} training samples, win_rate={y.mean()*100:.1f}%")
    return X, y, symbols, feature_names


async def load_training_data_with_regime(lookback_days: int = 120) -> dict:
    """按市场状态分段加载训练数据 — v4.11 改用 signal_history 替代 recommendation_tracking.

    旧实现用 recommendation_tracking JOIN analysis_scores (需要 was_profitable_3d)
    → 推荐追踪延迟严重 (T+3 才验证), 训练数据长期不足.
    → v4.11: signal_history 有 5652 条 outcome_label, 可即时按市场阶段分组.
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        r = await s.execute(text("""
            WITH market_phases AS (
                SELECT trade_date,
                       CASE 
                         WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date)) 
                              / NULLIF(LAG(close, 10) OVER (ORDER BY trade_DATE), 0) * 100 > 1.0 
                         THEN 'bull'
                         WHEN (close - LAG(close, 10) OVER (ORDER BY trade_date)) 
                              / NULLIF(LAG(close, 10) OVER (ORDER BY trade_DATE), 0) * 100 < -1.0 
                         THEN 'bear'
                         ELSE 'range'
                       END as phase
                FROM daily_kline
                WHERE ts_code = '700001.TI'
            )
            SELECT sh.symbol, sh.outcome_label,
                   sh.push_count_30d, sh.price_zone_width_pct,
                   sh.ret_t5, sh.max_gain_pct, sh.max_loss_pct,
                   sh.predicted_return, sh.predicted_win_prob,
                   sh.scan_date, sh.composite_score,
                   COALESCE(mp.phase, 'range') as market_phase
            FROM signal_history sh
            LEFT JOIN market_phases mp ON mp.trade_date = sh.scan_date
            WHERE sh.scan_date >= :cut
              AND sh.outcome_label IN ('strong_win','weak_win','strong_loss','weak_loss')
            ORDER BY sh.scan_date DESC
        """), {"cut": cutoff})
        rows = r.fetchall()

    if not rows:
        logger.warning("No training data available (with regime)")
        return {}

    regime_data: dict[str, list] = {"bull": [], "bear": [], "range": []}

    for row in rows:
        sym, outcome, push30, width, ret5, max_gain, max_loss, pred_r, pred_wp, sd, sc, phase = row

        features = [
            float(push30 or 0),      # 30日推送次数
            float(width or 0),       # 价格区间宽度%
            float(ret5 or 0),        # T+5收益
            float(max_gain or 0),    # 最大涨幅
            float(abs(max_loss or 0)) * -1,  # 最大跌幅(负)
            float(pred_r or 0),      # 预测收益
            float(pred_wp or 0),     # 预测胜率
            float(sc or 50),         # 综合分
        ]
        label = 1 if outcome in ('strong_win', 'weak_win') else 0

        phase_lower = (phase or "").lower()
        if "牛" in phase_lower or "bull" in phase_lower:
            regime = "bull"
        elif "熊" in phase_lower or "bear" in phase_lower:
            regime = "bear"
        else:
            regime = "range"

        regime_data[regime].append({
            "features": features,
            "label": label,
            "symbol": sym,
        })

    result = {}
    SH_FEAT_NAMES = [
        'push_count_30d', 'price_width_pct', 'ret_t5',
        'max_gain_pct', 'max_loss_pct',
        'predicted_return', 'predicted_win_prob', 'composite_score',
    ]

    for regime, samples in regime_data.items():
        if len(samples) < MIN_SAMPLES_FOR_TRAINING:
            logger.info(f"Regime [{regime}]: {len(samples)} samples (insufficient, skip)")
            continue
        X = np.array([s["features"] for s in samples])
        y = np.array([s["label"] for s in samples])
        syms = [s["symbol"] for s in samples]
        result[regime] = {
            "X": X, "y": y, "symbols": syms,
            "n_samples": len(samples),
            "win_rate": float(y.mean()),
            "feature_names": SH_FEAT_NAMES,
        }
        logger.info(f"Regime [{regime}]: {len(samples)} samples, win_rate={y.mean():.1%}")

    return result

async def train_weights(lookback_days: int = 120, min_samples: int = MIN_SAMPLES_FOR_TRAINING) -> dict:
    """基于真实盈亏反馈训练评分权重 (全局, 向后兼容).

    返回: {
        status, n_samples, cv_auc, weights, coefficients, trained_at
    }
    """
    X, y, symbols, feature_names = await load_training_data(lookback_days)

    if len(y) < min_samples:
        return {
            "status": "skipped",
            "reason": f"训练数据不足: {len(y)} < {min_samples}",
            "n_samples": len(y),
        }

    return _fit_logistic_regression(X, y, feature_names)


async def train_weights_by_regime(lookback_days: int = 120,
                                  min_samples: int = MIN_SAMPLES_FOR_TRAINING) -> dict:
    """按市场状态分段训练 — 产生 bull/bear/range 三套权重.

    解决"静态权重打动态市场"问题:
      - 牛市权重: 侧重动量+技术 (tg_momentum, kline, tech)
      - 熊市权重: 侧重防御+估值 (valuation, fund, market_relative)
      - 震荡权重: 侧重博弈+形态 (box, pattern, bbi)

    Returns:
        {regime: {status, n_samples, cv_auc, weights, ...}, "global": {...}}
    """
    regime_data = await load_training_data_with_regime(lookback_days)

    if not regime_data:
        return {"status": "skipped", "reason": "无分段数据"}

    results = {}
    all_X, all_y = [], []

    for regime, data in regime_data.items():
        X, y = data["X"], data["y"]
        all_X.append(X)
        all_y.append(y)

        if len(y) < min_samples:
            results[regime] = {
                "status": "skipped",
                "reason": f"样本不足: {len(y)} < {min_samples}",
                "n_samples": len(y),
                "win_rate": data["win_rate"],
            }
            continue

        fit_result = _fit_logistic_regression(X, y, data["feature_names"])
        fit_result["win_rate"] = data["win_rate"]
        results[regime] = fit_result

    # 同时训练全局权重 (作为 fallback)
    if all_X and all_y:
        global_X = np.vstack(all_X)
        global_y = np.concatenate(all_y)
        if len(global_y) >= min_samples:
            results["global"] = _fit_logistic_regression(global_X, global_y, regime_data.get("bull", {}).get("feature_names", DIM_KEYS + EXTRA_DIM_KEYS + ['composite_score']))
            results["global"]["win_rate"] = float(global_y.mean())

    return results


def _fit_logistic_regression(X: np.ndarray, y: np.ndarray,
                              feature_names: list[str]) -> dict:
    """内部: 标准化 + Logistic Regression + 系数→权重."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import cross_val_score

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        max_iter=2000, C=0.5, class_weight='balanced', random_state=42,
    )
    model.fit(X_scaled, y)

    try:
        cv_scores = cross_val_score(model, X_scaled, y, cv=min(5, len(y)//10), scoring='roc_auc')
        cv_auc = float(cv_scores.mean())
        cv_std = float(cv_scores.std())
    except Exception:
        cv_auc = 0.0
        cv_std = 0.0

    coefs = list(zip(feature_names, model.coef_[0]))
    coefs.sort(key=lambda x: -abs(x[1]))

    max_abs_coef = max(abs(c) for _, c in coefs) if coefs else 1.0
    new_weights = {}
    for name, coef in coefs:
        normalized = abs(coef) / max(max_abs_coef, 0.001) * 3.0
        bounded = max(0.5, min(4.0, normalized))
        new_weights[name] = round(bounded, 2)

    logger.info(
        f"Training complete: {len(y)} samples, "
        f"win_rate={y.mean()*100:.1f}%, "
        f"CV AUC={cv_auc:.3f}(±{cv_std:.3f})"
    )

    for i, (name, coef) in enumerate(coefs[:10]):
        direction = '+' if coef > 0 else '-'
        logger.info(f"  #{i+1} {name}: coef={coef:+.4f} weight→{new_weights.get(name, 0)} {direction}")

    return {
        "status": "success",
        "n_samples": int(len(y)),
        "cv_auc": round(cv_auc, 4),
        "cv_std": round(cv_std, 4),
        "coefficients": {name: round(float(coef), 6) for name, coef in coefs},
        "new_weights": new_weights,
        "feature_names": feature_names,
        "trained_at": str(date.today()),
    }


async def persist_weights(training_result: dict, model_version: str = None,
                         regime: str = "__global__") -> dict:
    """将训练权重持久化到 param_library 和 bayesian_beliefs.

    Args:
        training_result: train_weights() 的输出
        model_version: 版本号
        regime: 市场状态 (__global__ | bull | bear | range)
    """
    if training_result.get("status") != "success":
        return {"status": "skipped", "reason": training_result.get("reason", "training failed")}

    cv_auc = training_result.get("cv_auc", 0)
    if cv_auc < 0.52:
        return {"status": "rejected", "reason": f"AUC {cv_auc:.3f} < 0.52"}

    version = model_version or f"v{date.today().strftime('%Y%m%d')}"
    new_weights = training_result["new_weights"]
    n_samples = training_result.get("n_samples", 0)

    # 1. 反激活旧权重 (独立 session, 独立 commit)
    strategy_filter = f"scoring_{regime}" if regime != "__global__" else "scoring"
    try:
        async with async_session_factory() as s:
            await s.execute(text(
                f"UPDATE param_library SET is_active=false WHERE strategy='{strategy_filter}' AND is_active=true"
            ))
            await s.commit()
    except Exception:
        pass

    # 2. 逐条写入新权重 (每条独立 session, 避免事务污染)
    updated_p = 0
    for dim_key, weight_val in new_weights.items():
        param_name = WEIGHT_PARAM_NAMES.get(dim_key, dim_key)
        try:
            async with async_session_factory() as s:
                await s.execute(text("""
                    INSERT INTO param_library (param_name, mu, sigma, n_observations,
                        is_shadow, is_active, version, strategy, converge_status, updated_at)
                    VALUES (:pn, :mu, 0.3, :n, false, true, :ver, :strat, 'trained', NOW())
                """), {"pn": param_name, "mu": float(weight_val), "n": n_samples,
                       "ver": version, "strat": strategy_filter})
                await s.commit()
            updated_p += 1
        except Exception:
            try:
                async with async_session_factory() as s:
                    await s.execute(text("""
                        UPDATE param_library SET mu=:mu, sigma=0.3, n_observations=:n,
                            is_active=true, is_shadow=false, converge_status='trained', updated_at=NOW()
                        WHERE param_name=:pn AND strategy=:strat
                    """), {"pn": param_name, "mu": float(weight_val), "n": n_samples,
                           "strat": strategy_filter})
                    await s.commit()
                updated_p += 1
            except Exception:
                pass

    # 3. 写入 bayesian_beliefs (独立 session, regime-specific archetype)
    bayes_archetype = regime if regime != "__global__" else "__global__"
    updated_b = 0
    for dim_key, weight_val in new_weights.items():
        param_name = WEIGHT_PARAM_NAMES.get(dim_key, dim_key)
        mu_val = float(weight_val)
        lo_val = max(0.3, mu_val * 0.6)
        hi_val = min(5.0, mu_val * 1.4)
        try:
            async with async_session_factory() as s:
                await s.execute(text("""
                    INSERT INTO bayesian_beliefs (archetype, param_name, mu, sigma, n_observations, lo, hi, last_updated)
                    VALUES (:arch, :pn, :mu, 0.3, :n, :lo, :hi, NOW())
                """), {"arch": bayes_archetype, "pn": param_name, "mu": mu_val,
                       "n": n_samples, "lo": lo_val, "hi": hi_val})
                await s.commit()
            updated_b += 1
        except Exception:
            try:
                async with async_session_factory() as s:
                    await s.execute(text("""
                        UPDATE bayesian_beliefs SET mu=:mu, sigma=0.3, n_observations=:n,
                            lo=:lo, hi=:hi, last_updated=NOW()
                        WHERE archetype=:arch AND param_name=:pn
                    """), {"arch": bayes_archetype, "pn": param_name, "mu": mu_val,
                           "n": n_samples, "lo": lo_val, "hi": hi_val})
                    await s.commit()
                updated_b += 1
            except Exception:
                pass

    # 3.5 写入 regime 的 AUC 元信息 (用于 deep_scorer 加载时的安全门控)
    #     审计修复: 任务一 — 加载层需验证 cv_auc ≥ 0.55 才允许分段权重上线
    cv_auc = training_result.get("cv_auc", 0)
    try:
        async with async_session_factory() as s:
            await s.execute(text("""
                INSERT INTO bayesian_beliefs (archetype, param_name, mu, sigma, n_observations, lo, hi, last_updated)
                VALUES (:arch, '__regime_auc__', :mu, 0.0, :n, 0.0, 1.0, NOW())
                ON CONFLICT (archetype, param_name) DO UPDATE SET
                    mu=EXCLUDED.mu, n_observations=EXCLUDED.n_observations, last_updated=NOW()
            """), {"arch": bayes_archetype, "mu": round(float(cv_auc), 4), "n": n_samples})
            await s.commit()
    except Exception:
        pass  # __regime_auc__ 写入失败不影响权重持久化

    # 3.6 写入 regime 的训练日期 (用于判断权重时效性)
    try:
        async with async_session_factory() as s:
            await s.execute(text("""
                INSERT INTO bayesian_beliefs (archetype, param_name, mu, sigma, n_observations, lo, hi, last_updated)
                VALUES (:arch, '__trained_at__', 0.0, 0.0, :n, 0.0, 1.0, NOW())
                ON CONFLICT (archetype, param_name) DO UPDATE SET
                    n_observations=EXCLUDED.n_observations, last_updated=NOW()
            """), {"arch": bayes_archetype, "n": n_samples})
            await s.commit()
    except Exception:
        pass

    return {
        "status": "success",
        "regime": regime,
        "param_library_updated": updated_p,
        "bayesian_updated": updated_b,
        "version": version,
    }


async def feed_bayesian_optimizer(training_result: dict) -> dict:
    """Bayesian 观测更新已整合到 persist_weights() 中.

    persist_weights 直接写入 bayesian_beliefs 表,
    deep_scorer 通过 get_beliefs() → resolve_scoring_weights() 自动加载.
    """
    return {"status": "done",
            "bayesian_updated": training_result.get("persist", {}).get("bayesian_updated", 0)}


async def full_training_pipeline(lookback_days: int = 120, force: bool = False,
                                  by_regime: bool = True) -> dict:
    """完整训练管线: 加载→训练→持久化→喂入Bayesian.

    这是接入 daily_task 的单一入口.

    v2.0: 默认按市场状态分段训练 (bull/bear/range),
          解决"静态权重打动态市场"问题.
          如果分段数据不足, 自动降级为全局训练.
    """
    if by_regime:
        # ── 分段训练 ──
        regime_results = await train_weights_by_regime(lookback_days)

        if not regime_results or regime_results.get("status") == "skipped":
            # 降级为全局训练
            logger.warning("分段训练数据不足, 降级为全局训练")
            return await full_training_pipeline(lookback_days, force, by_regime=False)

        persist_results = {}
        all_top_features = {}

        for regime, result in regime_results.items():
            if result.get("status") != "success":
                persist_results[regime] = {"status": "skipped",
                                            "reason": result.get("reason", "")}
                continue

            persist_result = await persist_weights(result, regime=regime)
            persist_results[regime] = persist_result

            # 收集每个 regime 的 top features
            all_top_features[regime] = [
                {"name": name, "coef": coef}
                for name, coef in sorted(
                    result.get("coefficients", {}).items(),
                    key=lambda x: -abs(x[1])
                )[:5]
            ]

        # 统计
        trained_regimes = [r for r, p in persist_results.items()
                          if p.get("status") == "success"]
        total_samples = sum(
            regime_results.get(r, {}).get("n_samples", 0)
            for r in trained_regimes
        )

        return {
            "status": "success",
            "method": "regime_segmented",
            "regimes_trained": trained_regimes,
            "total_samples": total_samples,
            "training": {
                r: {
                    "n_samples": regime_results[r].get("n_samples"),
                    "cv_auc": regime_results[r].get("cv_auc"),
                    "win_rate": regime_results[r].get("win_rate"),
                }
                for r in trained_regimes
            },
            "persist": persist_results,
            "top_features": all_top_features,
        }

    # ── 全局训练 (向后兼容) ──
    result = await train_weights(lookback_days)

    if result.get("status") != "success":
        return result

    persist_result = await persist_weights(result)
    bayes_result = await feed_bayesian_optimizer(result)

    return {
        "status": "success",
        "method": "global",
        "training": {
            "n_samples": result.get("n_samples"),
            "cv_auc": result.get("cv_auc"),
            "win_rate": result.get("win_rate"),
        },
        "persist": persist_result,
        "bayesian": bayes_result,
        "top_features": [
            {"name": name, "coef": coef}
            for name, coef in sorted(
                result.get("coefficients", {}).items(),
                key=lambda x: -abs(x[1])
            )[:5]
        ],
    }
