"""训练蛋期孵化分类器 — 基于 8 维分钟线特征预测蛋→鹅概率.

输入: mins_train_samples 表中 label IN ('hatched', 'failed') 的记录
输出: models/mins_egg_classifier.joblib

运行: python -m scripts.mins_train_classifier
"""
import asyncio, sys, os, logging, numpy as np
from sqlalchemy import text
sys.path.insert(0, '.')
from app.core.database import async_session_factory

logger = logging.getLogger("mins_classifier")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

FEATURE_COLS = [f"feature_{i}" for i in range(1, 9)]
FEATURE_NAMES = [
    "V反转频率", "尾盘量比", "量集中度", "早盘冲高率",
    "日均振幅", "脉冲方向比", "最长红柱", "价格稳定度",
]


async def main():
    logger.info("=== 蛋期孵化分类器训练 ===")

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score, classification_report
    from sklearn.model_selection import cross_val_score
    import joblib

    # 1. 加载标注数据
    async with async_session_factory() as s:
        r = await s.execute(text(f"""
            SELECT ts_code, {', '.join(FEATURE_COLS)}, label
            FROM mins_train_samples
            WHERE label IN ('hatched', 'failed')
        """))
        rows = r.fetchall()

    if len(rows) < 20:
        logger.warning(f"标注数据不足 ({len(rows)} < 20), 跳过训练")
        return

    logger.info(f"训练数据: {len(rows)} 条")

    X = np.array([[float(row[i] or 0) for i in range(1, 9)] for row in rows])
    y = np.array([1 if row[9] == "hatched" else 0 for row in rows])

    n_pos = sum(y)
    n_neg = len(y) - n_pos
    logger.info(f"正样本(hatched): {n_pos}, 负样本(failed): {n_neg}")

    if n_pos < 5 or n_neg < 5:
        logger.warning(f"正/负样本太少 (pos={n_pos}, neg={n_neg}), 跳过训练")
        return

    # 2. 标准化 + 训练
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        C=0.5, class_weight="balanced", max_iter=500, random_state=42,
    )
    model.fit(X_scaled, y)

    # 3. 评估
    y_pred = model.predict_proba(X_scaled)[:, 1]
    auc = roc_auc_score(y, y_pred) if len(set(y)) > 1 else 0.0
    logger.info(f"训练 AUC: {auc:.4f}")

    # 交叉验证 (如果样本够多)
    if len(y) >= 30:
        cv_scores = cross_val_score(model, X_scaled, y, cv=min(5, len(y)//2), scoring="roc_auc")
        logger.info(f"CV AUC: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")

    # 特征重要性
    coefs = model.coef_[0]
    importance = sorted(zip(FEATURE_NAMES, coefs), key=lambda x: abs(x[1]), reverse=True)
    logger.info("特征重要性 (|coef|):")
    for name, coef in importance:
        logger.info(f"  {name:<12}: {coef:+.4f}")

    # 4. 保存
    os.makedirs("models", exist_ok=True)
    artifact = {"model": model, "scaler": scaler, "feature_names": FEATURE_NAMES}
    joblib.dump(artifact, "models/mins_egg_classifier.joblib")
    logger.info(f"模型已保存: models/mins_egg_classifier.joblib (AUC={auc:.4f})")


if __name__ == "__main__":
    asyncio.run(main())
