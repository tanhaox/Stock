#!/usr/bin/env python3
"""Train XGBoost predictive model on signal_history ret_t5 labels.

Usage: python scripts/train_predictive_model.py

Replaces the manual composite_score with a data-driven T+5 return predictor.
"""
import numpy as np
import json, os, sys, logging
from datetime import date
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, mean_squared_error, r2_score

logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
logger = logging.getLogger("train_predictive")


async def main():
    from app.core.database import async_session_factory
    from app.services.predictive_features import build_training_data, FEAT_NAMES

    async with async_session_factory() as s:
        X, y, weights, sources, groups = await build_training_data(s)

    if len(X) < 100:
        logger.error(f"Not enough training data: {len(X)} samples")
        return

    logger.info(f"Training set: {len(X)} samples, {X.shape[1]} features")
    logger.info(f"  signal_history: {sources.get('signal_history',0)}, recommendations: {sources.get('recommendations',0)}, historical: {sources.get('historical',0)}")
    logger.info(f"  sample_weight: rec ×3, signal ×1, historical ×0.5")
    logger.info(f"Label (ret_t5/ret_2d%): mean={y.mean():.2f}%, median={np.median(y):.2f}%")
    logger.info(f"  >0: {(y>0).mean()*100:.1f}%  >5%: {(y>5).mean()*100:.1f}%  <-5%: {(y<-5).mean()*100:.1f}%")

    # ── Check XGBoost availability ──
    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost not installed. Run: pip install xgboost")
        return

    # ── Time-series CV: 按时间切分 (避免前瞻偏差) ──
    tscv = TimeSeriesSplit(n_splits=5)

    # ── Regression model: predict T+5 % return ──
    reg = xgb.XGBRegressor(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=1.0, reg_lambda=1.0,
        objective='reg:squarederror', random_state=42, n_jobs=-1,
    )
    reg.fit(X, y, sample_weight=weights)
    y_pred_reg = reg.predict(X)

    mse = mean_squared_error(y, y_pred_reg)
    r2 = r2_score(y, y_pred_reg)
    logger.info(f"Regression: MSE={mse:.2f}, R2={r2:.4f}")

    # ── Classification model: predict T+5 > 0 (win probability) ──
    y_cls = (y > 0).astype(int)
    logger.info(f"Class balance: win={(y_cls==1).mean()*100:.1f}%  lose={(y_cls==0).mean()*100:.1f}%")

    cls = xgb.XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, reg_alpha=1.0, reg_lambda=1.0,
        objective='binary:logistic', random_state=42, n_jobs=-1,
    )

    # Time-series CV AUC
    auc_scores = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y_cls[train_idx], y_cls[val_idx]

        cls_cv = xgb.XGBClassifier(
            n_estimators=150, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective='binary:logistic', random_state=42 + fold, n_jobs=-1,
        )
        cls_cv.fit(X_tr, y_tr)
        y_prob = cls_cv.predict_proba(X_val)[:, 1]
        auc = roc_auc_score(y_val, y_prob)
        auc_scores.append(auc)
        logger.info(f"  Fold {fold+1}: val_size={len(X_val)}, AUC={auc:.4f}")

    cv_auc = float(np.mean(auc_scores))
    logger.info(f"CV AUC (mean of 5 folds): {cv_auc:.4f}")

    # Final fit on all data
    cls.fit(X, y_cls, sample_weight=weights)

    # ── Feature importance ──
    imp = sorted(zip(FEAT_NAMES, reg.feature_importances_), key=lambda x: -x[1])
    logger.info("\nTop 15 features (regression):")
    for name, val in imp[:15]:
        logger.info(f"  {name}: {val:.4f}")

    cls_imp = sorted(zip(FEAT_NAMES, cls.feature_importances_), key=lambda x: -x[1])
    logger.info("\nTop 10 features (classification):")
    for name, val in cls_imp[:10]:
        logger.info(f"  {name}: {val:.4f}")

    # ── Save models ──
    os.makedirs('models', exist_ok=True)
    reg.save_model('models/predictive_scorer.json')
    cls.save_model('models/predictive_classifier.json')

    # ── 3. 排序模型: LambdaRank (Phase 55) ──
    rank_groups = len(groups)
    logger.info(f"\nRanker training: {rank_groups} groups ({sum(groups)} samples)")
    if len(groups) >= 3 and any(g >= 3 for g in groups):
        model_rank = xgb.XGBRanker(
            n_estimators=200, max_depth=6, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            objective='rank:pairwise',
            random_state=42,
        )
        model_rank.fit(X, y, group=groups)  # weights per-sample incompatible with group ranking
        model_rank.save_model('models/predictive_ranker.json')
        logger.info(f"  Ranker saved: predictive_ranker.json, {rank_groups} groups")
        ranker_trained = True
    else:
        logger.info(f"  Ranker skipped: need ≥3 groups with ≥3 samples each (got {rank_groups} groups)")
        ranker_trained = False

    # ── Save metadata ──
    model_meta = {
        "train_date": str(date.today()),
        "label": "excess_return_vs_700001",  # Phase 54: 绝对收益→超额收益
        "n_samples": int(len(X)),
        "n_features": int(X.shape[1]),
        "feat_names": FEAT_NAMES,
        "y_mean": float(y.mean()),
        "y_std": float(y.std()),
        "win_rate": float((y > 0).mean()),
        "cv_auc_mean": cv_auc,
        "cv_auc_std": float(np.std(auc_scores)),
        "mse": float(mse),
        "r2": float(r2),
        "top_features_reg": [(n, float(v)) for n, v in imp[:20]],
        "top_features_cls": [(n, float(v)) for n, v in cls_imp[:20]],
        "top_features": [(n, float(v)) for n, v in imp[:20]],  # alias for backward compat
        # Percentile thresholds for score → return mapping
        "pred_percentiles": {
            "p10": float(np.percentile(y_pred_reg, 10)),
            "p25": float(np.percentile(y_pred_reg, 25)),
            "p50": float(np.percentile(y_pred_reg, 50)),
            "p75": float(np.percentile(y_pred_reg, 75)),
            "p90": float(np.percentile(y_pred_reg, 90)),
        },
        "data_sources": sources,
        "training_range": "2022-2026 (signal + historical)",
        "ranker_objective": "rank:pairwise",
        "ranker_groups": rank_groups,
        "ranker_trained": ranker_trained,
    }

    with open('models/predictive_scorer_meta.json', 'w', encoding='utf-8') as f:
        json.dump(model_meta, f, indent=2, ensure_ascii=False)

    logger.info(f"\n✅ Models saved: predictive_scorer.json + predictive_classifier.json")
    logger.info(f"   Samples: {model_meta['n_samples']}")
    logger.info(f"   CV AUC:  {cv_auc:.4f} {'✓ effective' if cv_auc > 0.55 else '⚠ weak — needs more data/features'}")
    logger.info(f"   R²:      {r2:.4f}")
    logger.info(f"   WR:      {model_meta['win_rate']*100:.1f}%")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
