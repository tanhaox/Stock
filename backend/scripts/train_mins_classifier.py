#!/usr/bin/env python3
"""Train minute-pattern classifier on mins_train_samples (Phase 7 — P2).

The mins_train_samples table holds 100 pre-goose egg-phase snapshots:
  8 features from minute-level behavior + egg_days/lock_avg/total_gain.
  Label = 'pre_goose' (future breakout), 'flat' (stayed dormant).

This script trains a lightweight XGBoost classifier to predict:
  Is this locked stock about to break out (goose) in the next T+5 days?

Usage:
  PYTHONPATH=. python -m scripts.train_mins_classifier
"""
import asyncio, logging, json, os
import numpy as np
from datetime import date
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, accuracy_score

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("train_mins")


async def main():
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT feature_1,feature_2,feature_3,feature_4,feature_5,feature_6,feature_7,feature_8,"
            "egg_days,lock_avg,total_gain,label FROM mins_train_samples"
        ))
        rows = [(float(rw[0] or 0), float(rw[1] or 0), float(rw[2] or 0), float(rw[3] or 0),
                 float(rw[4] or 0), float(rw[5] or 0), float(rw[6] or 0), float(rw[7] or 0),
                 float(rw[8] or 0), float(rw[9] or 0), float(rw[10] or 0), rw[11])
                for rw in r.fetchall()]

    if len(rows) < 20:
        logger.error(f"Not enough samples: {len(rows)}")
        return

    X = np.array([r[:11] for r in rows], dtype=np.float32)
    y = np.array([1 if r[11] == "pre_goose" else 0 for r in rows], dtype=int)

    logger.info(f"Samples: {len(X)} (pre_goose={y.sum()}/{len(y)})")

    try:
        import xgboost as xgb
    except ImportError:
        logger.error("xgboost not installed")
        return

    # Train/test split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)

    clf = xgb.XGBClassifier(
        n_estimators=50, max_depth=3, learning_rate=0.05,
        subsample=0.8, objective="binary:logistic", random_state=42,
    )
    clf.fit(X_train, y_train)

    y_prob = clf.predict_proba(X_test)[:, 1]
    try:
        auc = roc_auc_score(y_test, y_prob)
    except Exception:
        auc = 0.5
    acc = accuracy_score(y_test, clf.predict(X_test))

    logger.info(f"Test AUC: {auc:.4f}, Acc: {acc:.3f}")

    # Save model
    os.makedirs("models", exist_ok=True)
    clf.save_model("models/mins_classifier.json")

    feat_names = ["f1","f2","f3","f4","f5","f6","f7","f8","egg_days","lock_avg","total_gain"]
    imp = sorted(zip(feat_names, clf.feature_importances_), key=lambda x: -x[1])
    logger.info("Top features:")
    for name, val in imp[:5]:
        logger.info(f"  {name}: {val:.4f}")

    meta = {
        "train_date": str(date.today()),
        "n_samples": len(X),
        "n_features": X.shape[1],
        "auc": round(auc, 4),
        "acc": round(acc, 4),
        "top_features": [(n, round(float(v), 4)) for n, v in imp[:5]],
    }
    with open("models/mins_classifier_meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    logger.info(f"Model saved: models/mins_classifier.json")


if __name__ == "__main__":
    asyncio.run(main())
