"""Predictive scorer — XGBoost 推理服务 (v4.8).

加载训练好的模型, 对单只或批量股票预测 T+5 收益和赢率.
"""
import numpy as np
from app.utils.numpy_utils import sanitize_array
import json
import os as _os
import logging

logger = logging.getLogger(__name__)

_model_reg = None
_model_cls = None
_model_rank = None  # Phase 55: LambdaRank 排序器
_meta = None
_loaded = False

_MODEL_DIR = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    'models'
)


def _load_models():
    global _model_reg, _model_cls, _model_rank, _meta, _loaded
    if _loaded:
        return _model_reg, _model_cls, _meta
    _loaded = True

    reg_path = _os.path.join(_MODEL_DIR, 'predictive_scorer.json')
    cls_path = _os.path.join(_MODEL_DIR, 'predictive_classifier.json')
    rank_path = _os.path.join(_MODEL_DIR, 'predictive_ranker.json')
    meta_path = _os.path.join(_MODEL_DIR, 'predictive_scorer_meta.json')

    if not _os.path.exists(reg_path):
        logger.warning(f"Predictive model not found at {reg_path} — run train_predictive_model.py first")
        return None, None, None

    try:
        import xgboost as xgb
        _model_reg = xgb.XGBRegressor()
        _model_reg.load_model(reg_path)
        _model_cls = xgb.XGBClassifier()
        _model_cls.load_model(cls_path)

        # Phase 55: 排序模型 (optional)
        if _os.path.exists(rank_path):
            _model_rank = xgb.XGBRanker()
            _model_rank.load_model(rank_path)
            logger.info("LambdaRank ranker loaded")
        else:
            _model_rank = None

        if _os.path.exists(meta_path):
            with open(meta_path, encoding='utf-8') as f:
                _meta = json.load(f)

        logger.info(
            f"Predictive model loaded: {_meta.get('n_samples', '?') if _meta else '?'} samples, "
            f"CV_AUC={_meta.get('cv_auc_mean', '?') if _meta else '?'}"
        )
        return _model_reg, _model_cls, _meta
    except Exception as e:
        logger.warning(f"Failed to load predictive model: {e}")
        _model_reg = _model_cls = _model_rank = _meta = None
        return None, None, None


async def predict_returns(symbol: str, scan_date, session) -> dict | None:
    """对单只股票预测 T+5 收益.

    Returns:
        {"predicted_return": float, "win_probability": float, ...} or None
    """
    model_reg, model_cls, meta = _load_models()
    if model_reg is None:
        return None

    from app.services.predictive_features import build_features, FEAT_NAMES

    feats = await build_features(symbol, scan_date, session)
    vec = [feats.get(f, 0.0) for f in FEAT_NAMES]
    X = np.array([vec], dtype=np.float32)
    X = sanitize_array(X, fill=0.0)

    predicted_return = float(model_reg.predict(X)[0])
    try:
        win_prob = float(model_cls.predict_proba(X)[0][1])
    except Exception:
        win_prob = round(float(1 / (1 + np.exp(-predicted_return / 3))), 3)  # sigmoid fallback

    return {
        "predicted_return": round(predicted_return, 2),
        "win_probability": round(min(0.95, max(0.05, win_prob)), 3),
        "model_version": str(meta.get("train_date", "unknown")) if meta else "unknown",
    }


async def batch_predict(symbols: list[str], scan_date, session) -> dict[str, dict]:
    """批量预测 T+5 收益.

    Returns:
        {symbol: {predicted_return, win_probability}}
    """
    model_reg, model_cls, meta = _load_models()
    if model_reg is None or not symbols:
        return {}

    from app.services.predictive_features import FEAT_NAMES, _preload_klines_batch, _KLINES_BATCH, _build_features_from_arrays
    from app.services.predictive_features import _preload_sector_features, _preload_toplist, _toplist_cache

    # Phase 44b: 批量预加载 kline → 内存 (1次SQL替代324次)
    await _preload_klines_batch(session, symbols, scan_date)
    sec_cache = await _preload_sector_features(session, symbols, scan_date)
    await _preload_toplist(session, symbols, scan_date)

    rows = []
    for sym in symbols:
        arr = _KLINES_BATCH.get(sym)
        if arr:
            feats = _build_features_from_arrays(*arr, sym, scan_date)
        else:
            from app.services.predictive_features import build_features
            feats = await build_features(sym, scan_date, session)
        sc = sec_cache.get(sym, {})
        feats.update(sc)
        if sc:
            feats["x_real_alpha"] = round(feats.get("chg_5d", 0) - sc.get("x_real_sector_5d", 0), 2)
        tl = _toplist_cache.get(sym, {})
        feats.update(tl)
        if tl:
            feats["tl_oversold"] = 1.0 if feats.get("rsi_14", 50) < 35 else 0.0
            feats["tl_breakout"] = 1.0 if feats.get("price_vs_ma20", 0) > 2 else 0.0
        rows.append([feats.get(f, 0.0) for f in FEAT_NAMES])

    X = np.array(rows, dtype=np.float32)
    X = sanitize_array(X, fill=0.0)

    preds = model_reg.predict(X)
    try:
        probs = model_cls.predict_proba(X)[:, 1]
    except Exception:
        probs = 1 / (1 + np.exp(-preds / 3))  # sigmoid fallback

    return {
        sym: {
            "predicted_return": round(float(preds[i]), 2),
            "win_probability": round(float(min(0.95, max(0.05, probs[i]))), 3),
        }
        for i, sym in enumerate(symbols)
    }


async def rank_stocks(symbols: list[str], scan_date, session) -> dict[str, float]:
    """Phase 55: LambdaRank 排序分 — 对一批股票输出 0-1 相对排序分.

    只关心同一批候选股中"谁更强"，不关心绝对收益数值。
    分数越高 = 在同批股票中越值得优先买入。

    Returns:
        {symbol: rank_score (0~1, sigmoid归一化)}
    """
    _load_models()
    if _model_rank is None:
        return {}

    from app.services.predictive_features import FEAT_NAMES, _preload_klines_batch, _KLINES_BATCH, _build_features_from_arrays
    from app.services.predictive_features import _preload_sector_features, _preload_toplist, _toplist_cache

    # 批量预加载
    await _preload_klines_batch(session, symbols, scan_date)
    sec_cache = await _preload_sector_features(session, symbols, scan_date)
    await _preload_toplist(session, symbols, scan_date)

    rows = []
    for sym in symbols:
        arr = _KLINES_BATCH.get(sym)
        if arr:
            feats = _build_features_from_arrays(*arr, sym, scan_date)
        else:
            from app.services.predictive_features import build_features
            feats = await build_features(sym, scan_date, session)
        sc = sec_cache.get(sym, {})
        feats.update(sc)
        if sc:
            feats["x_real_alpha"] = round(feats.get("chg_5d", 0) - sc.get("x_real_sector_5d", 0), 2)
        tl = _toplist_cache.get(sym, {})
        feats.update(tl)
        if tl:
            feats["tl_oversold"] = 1.0 if feats.get("rsi_14", 50) < 35 else 0.0
            feats["tl_breakout"] = 1.0 if feats.get("price_vs_ma20", 0) > 2 else 0.0
        rows.append([feats.get(f, 0.0) for f in FEAT_NAMES])

    if not rows:
        return {}

    X = np.array(rows, dtype=np.float32)
    X = sanitize_array(X, fill=0.0)

    # LambdaRank 预测原始排序分
    raw_scores = _model_rank.predict(X)

    # Sigmoid 归一化到 0-1
    score_max = float(np.max(raw_scores)) if len(raw_scores) > 0 else 0
    score_min = float(np.min(raw_scores)) if len(raw_scores) > 0 else 0
    score_range = max(score_max - score_min, 1e-8)

    result = {}
    for i, sym in enumerate(symbols):
        normalized = (float(raw_scores[i]) - score_min) / score_range
        result[sym] = round(normalized, 3)

    return result
