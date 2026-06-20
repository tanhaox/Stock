"""Per-Stock XGBoost 训练 + 推理.

每只股票独立训练一个轻量 XGBoost 模型。
多窗口联合输出: T+2, T+5, T+10, T+20 超额收益 + 胜率。

核心:
  train_per_stock() — 单只股票训练
  train_all() — 批量训练
  predict() — 推理 (加载模型 + 预测)
"""
import json
import logging
import numpy as np
from pathlib import Path
from datetime import date
from app.utils.numpy_utils import safe_auc
from sqlalchemy import text
from app.core.database import async_session_factory

from .features import ALL_FEAT_NAMES, features_to_array

logger = logging.getLogger("stock_dna.model")

MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "models" / "dna"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

# 训练配置
XGB_PARAMS = {
    "n_estimators": 80,
    "max_depth": 3,
    "learning_rate": 0.03,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "reg_alpha": 1.0,
    "reg_lambda": 1.0,
    "objective": "reg:squarederror",
    "random_state": 42,
    "n_jobs": -1,
}


async def train_per_stock(symbol: str, use_huber: bool = True) -> dict:
    """训练单只股票的 4 窗口 XGBoost 模型。

    Args:
        symbol: 股票代码
        use_huber: 是否使用 Huber 损失 (pseudo-Huber via tweedie)

    Returns:
        {status, n_samples, auc_t5, best_horizon, feature_importance, ...}
    """
    from xgboost import XGBRegressor

    # 加载训练数据
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, daily_features, emotion_features, "
            "excess_ret_t2, excess_ret_t5, excess_ret_t10, excess_ret_t20 "
            "FROM stock_dna.daily_samples WHERE symbol=:sym AND excess_ret_t5 IS NOT NULL "
            "ORDER BY trade_date"
        ), {"sym": symbol})
        rows = r.fetchall()

    if len(rows) < 50:
        return {"status": "insufficient_data", "n_samples": len(rows), "reason": "样本 < 50"}

    # 构建特征矩阵和标签
    X_list, y_t2, y_t5, y_t10, y_t20 = [], [], [], [], []
    for row in rows:
        feat = {}
        # 合并 daily_features (JSONB) 和其他
        df = row[1] if isinstance(row[1], dict) else json.loads(row[1] or '{}')
        feat.update(df)
        ef = row[2] if isinstance(row[2], dict) else json.loads(row[2] or '{}')
        feat.update(ef)

        arr = features_to_array(feat, ALL_FEAT_NAMES)
        X_list.append(arr)
        y_t2.append(float(row[3] or 0))
        y_t5.append(float(row[4] or 0))
        y_t10.append(float(row[5] or 0))
        y_t20.append(float(row[6] or 0))

    X = np.array(X_list, dtype=np.float32)
    y_t2 = np.array(y_t2, dtype=np.float32)
    y_t5 = np.array(y_t5, dtype=np.float32)
    y_t10 = np.array(y_t10, dtype=np.float32)
    y_t20 = np.array(y_t20, dtype=np.float32)

    n = len(X)
    split = int(n * 0.8)
    X_train, X_val = X[:split], X[split:]
    yt5_train, yt5_val = y_t5[:split], y_t5[split:]

    if len(X_val) < 5:
        X_train, X_val = X[:max(n - 20, n // 2)], X[max(n - 20, n // 2):]
        yt5_train, yt5_val = y_t5[:len(X_train)], y_t5[len(X_train):]

    # 训练 T+5 主模型
    params = dict(XGB_PARAMS)
    if use_huber:
        # XGBoost pseudo-Huber: reg:pseudohubererror
        params["objective"] = "reg:pseudohubererror"
        params["huber_slope"] = 3.0

    model_t5 = XGBRegressor(**params)
    model_t5.fit(X_train, yt5_train)

    # 评估
    pred_t5 = model_t5.predict(X_val)
    yt5_binary = (yt5_val > 0).astype(int)
    pred_binary = (pred_t5 > 0).astype(int)
    accuracy = np.mean(yt5_binary == pred_binary)

    # AUC
    from sklearn.metrics import roc_auc_score
    try:
        auc_t5 = roc_auc_score(yt5_binary, pred_t5)
    except Exception:
        auc_t5 = 0.5
    auc_t5 = safe_auc(auc_t5)

    # 多窗口模型
    models = {}
    aucs = {}
    for horizon, y_arr in [(2, y_t2), (5, y_t5), (10, y_t10), (20, y_t20)]:
        y_train = y_arr[:split]
        y_val = y_arr[split:]
        m = XGBRegressor(**{**XGB_PARAMS, "objective": "reg:pseudohubererror" if use_huber else "reg:squarederror", "huber_slope": 3.0})
        m.fit(X[:split], y_train)
        p = m.predict(X_val)
        yb = (y_val > 0).astype(int)
        try:
            auc = roc_auc_score(yb, p)
        except Exception:
            auc = 0.5
        auc = safe_auc(auc)
        models[horizon] = m
        aucs[horizon] = round(float(auc), 4)

    # 保存
    model_path = MODEL_DIR / f"{symbol}_model.json"
    model_t5.save_model(str(model_path))
    for h in [2, 10, 20]:
        hp = MODEL_DIR / f"{symbol}_model_t{h}.json"
        models[h].save_model(str(hp))

    # 特征重要度
    imp = model_t5.feature_importances_
    top_idx = np.argsort(imp)[-10:][::-1]
    top_features = [
        {"name": ALL_FEAT_NAMES[i] if i < len(ALL_FEAT_NAMES) else f"f{i}",
         "importance": round(float(imp[i]), 4)}
        for i in top_idx
    ]

    best_horizon = max(aucs, key=aucs.get)

    # 写入 DNA profile
    dna_path = MODEL_DIR / f"{symbol}_dna.json"
    dna_info = {
        "symbol": symbol,
        "n_samples": n,
        "best_horizon": best_horizon,
        "horizon_aucs": aucs,
        "auc_t5": auc_t5,
        "accuracy_t5": round(float(accuracy), 4),
        "top_features": top_features,
        "best_horizon_auc": aucs[best_horizon],
        "bayesian_confidence": min(n / (n + 500), 0.95),
        "model_path": str(model_path),
        "last_trained": str(date.today()),
    }
    with open(dna_path, 'w', encoding='utf-8') as f:
        json.dump(dna_info, f, ensure_ascii=False, indent=2)

    # 同步到 DB
    await _save_training_result(symbol, dna_info)

    logger.info(f"  {symbol}: {n}样本, AUC_T5={auc_t5:.4f}, 最佳窗口=T+{best_horizon}")
    return {"status": "success", "symbol": symbol, "n_samples": n, "auc_t5": auc_t5,
            "best_horizon": best_horizon, "top_features": top_features}


async def train_all(symbols: list[str] = None, progress_cb=None) -> dict:
    """批量训练所有目标股票。"""
    if symbols is None:
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT DISTINCT symbol FROM stock_dna.daily_samples"))
            symbols = [row[0] for row in r.fetchall()]

    results = []
    for i, sym in enumerate(symbols):
        if progress_cb:
            progress_cb("train", i + 1, len(symbols), f"训练 {sym} ({i+1}/{len(symbols)})")
        try:
            r = await train_per_stock(sym)
            results.append(r)
        except Exception as e:
            logger.error(f"训练 {sym} 失败: {e}")
            results.append({"status": "error", "symbol": sym, "detail": str(e)})

    successful = [r for r in results if r.get("status") == "success"]
    return {
        "status": "success",
        "total": len(symbols),
        "trained": len(successful),
        "failed": len(results) - len(successful),
        "avg_auc_t5": round(float(np.mean([r["auc_t5"] for r in successful])), 4) if successful else 0,
        "details": results,
    }


async def _save_training_result(symbol: str, info: dict):
    """Save training results to DB profiles table."""
    import json as json_mod
    # sanitize NaN → null in horizon_aucs
    clean_aucs = {}
    for k, v in info.get("horizon_aucs", {}).items():
        clean_aucs[k] = safe_auc(v)
    bha = safe_auc(info.get("best_horizon_auc", 0.5))
    auc_t5_val = safe_auc(info.get("auc_t5", 0.5))

    async with async_session_factory() as s:
        await s.execute(text("""
            UPDATE stock_dna.profiles SET
                best_horizon=:bh, best_horizon_auc=:bha,
                horizon_auc_json=CAST(:haj AS jsonb),
                top_features=CAST(:tf AS jsonb),
                model_path=:mp, last_trained=NOW(), updated_at=NOW()
            WHERE symbol=:sym
        """), {
            "sym": symbol,
            "bh": info["best_horizon"],
            "bha": bha,
            "haj": json_mod.dumps(clean_aucs),
            "tf": json_mod.dumps(info["top_features"]),
            "mp": info["model_path"],
        })
        await s.commit()
