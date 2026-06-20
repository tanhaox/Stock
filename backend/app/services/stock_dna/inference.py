"""DNA 推理服务。

加载 Per-Stock XGBoost 模型, 对指定股票+日期进行多窗口预测。

核心:
  StockDNAScorer — 单例推理器
  predict(symbol, date) → 多窗口预测 + 周期上下文
  batch_predict(symbols, date) → 批量预测
"""
import json
import logging
import numpy as np
from pathlib import Path
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text

from app.core.database import async_session_factory

from .features import (
    ALL_FEAT_NAMES, features_to_array,
    EMOTION_FEAT_NAMES, MARKET_FEAT_NAMES, TRANSITION_FEAT_NAMES,
    CYCLE_FEAT_NAMES, HISTORY_FEAT_NAMES, INTERACT_FEAT_NAMES,
)

logger = logging.getLogger("stock_dna.inference")

MODEL_DIR = Path(__file__).resolve().parent.parent.parent.parent / "models" / "dna"

# 模型缓存 (lazy load, TTL)
_model_cache: dict[str, dict] = {}


class StockDNAScorer:
    """DNA 推理器 — 单例, 模型懒加载."""

    def __init__(self):
        self._loaded = set()

    async def predict(self, symbol: str, trade_date: date = None) -> Optional[dict]:
        """对单只股票进行 DNA 预测。

        Args:
            symbol: 股票代码
            trade_date: 目标日期 (None=今天)

        Returns:
            {symbol, current_emotion, cycle_position, predictions: {t2,t5,t10,t20}, best_horizon, confidence}
        """
        from xgboost import XGBRegressor

        if trade_date is None:
            trade_date = date.today()

        # 1. 加载模型
        model_path = MODEL_DIR / f"{symbol}_model.json"
        dna_path = MODEL_DIR / f"{symbol}_dna.json"

        if not model_path.exists():
            return {"status": "no_model", "symbol": symbol, "detail": "模型未训练"}

        # Cache
        cache_key = symbol
        if cache_key not in _model_cache:
            try:
                model_t5 = XGBRegressor()
                model_t5.load_model(str(model_path))
                models = {5: model_t5}
                for h in [2, 10, 20]:
                    hp = MODEL_DIR / f"{symbol}_model_t{h}.json"
                    if hp.exists():
                        m = XGBRegressor()
                        m.load_model(str(hp))
                        models[h] = m
                    else:
                        models[h] = model_t5

                dna_info = {}
                if dna_path.exists():
                    with open(dna_path, 'r', encoding='utf-8') as f:
                        dna_info = json.load(f)

                _model_cache[cache_key] = {"models": models, "dna": dna_info, "loaded_at": date.today()}
            except Exception as e:
                logger.error(f"加载 {symbol} 模型失败: {e}")
                return {"status": "error", "symbol": symbol, "detail": str(e)}

        cached = _model_cache[cache_key]
        models = cached["models"]
        dna_info = cached["dna"]

        # 2. 构建当天特征
        features = await self._build_today_features(symbol, trade_date)
        if features is None:
            return {"status": "no_data", "symbol": symbol, "detail": "无法构建今日特征"}

        from .features import ALL_FEAT_NAMES, features_to_array
        X = features_to_array(features, ALL_FEAT_NAMES).reshape(1, -1)

        # 3. 预测
        predictions = {}
        win_probs = {}
        for horizon in [2, 5, 10, 20]:
            m = models.get(horizon)
            if m:
                pred = float(m.predict(X)[0])
                predictions[f"t{horizon}"] = {"excess_return": round(pred, 2),
                                               "win_prob": round(1.0 / (1.0 + np.exp(-pred / 2.0)), 3)}
                win_probs[horizon] = predictions[f"t{horizon}"]["win_prob"]

        best_horizon = max(win_probs, key=win_probs.get) if win_probs else int(dna_info.get("best_horizon", 5))

        # 4. 上下文
        cycle_info = await self._get_cycle_context(symbol, trade_date)
        emotion_info = await self._get_emotion_context(symbol, trade_date)

        # 5. 可信度
        n_samples = dna_info.get("n_samples", 0)
        confidence = round(n_samples / (n_samples + 500), 3)

        return {
            "symbol": symbol,
            "current_emotion": emotion_info,
            "cycle_position": cycle_info,
            "predictions": predictions,
            "best_horizon": best_horizon,
            "confidence": confidence,
            "model_info": {
                "n_samples": n_samples,
                "auc_t5": dna_info.get("auc_t5"),
                "last_trained": dna_info.get("last_trained"),
            },
        }

    async def batch_predict(self, symbols: list[str], trade_date: date = None) -> list[dict]:
        """批量预测。"""
        results = []
        for sym in symbols:
            try:
                r = await self.predict(sym, trade_date)
                if r:
                    results.append(r)
            except Exception as e:
                results.append({"symbol": sym, "status": "error", "detail": str(e)})
        return results

    async def _build_today_features(self, symbol: str, trade_date: date) -> Optional[dict]:
        """Build ~150-dim features for inference.

        v2: Load pre-computed emotion/daily/cycle features from DB;
        fall back to pseudo-emotion from daily kline when min_kline data unavailable.
        """
        start_date = trade_date - timedelta(days=120)

        async with async_session_factory() as s:
            # 1. Load daily_kline + compute 77 dims
            r = await s.execute(text(
                "SELECT trade_date, open, high, low, close, volume FROM daily_kline "
                "WHERE ts_code=:sym AND trade_date BETWEEN :sd AND :ed ORDER BY trade_date"
            ), {"sym": symbol, "sd": start_date, "ed": trade_date})
            kline_rows = [{"trade_date": row[0], "open": float(row[1] or 0), "high": float(row[2] or 0),
                           "low": float(row[3] or 0), "close": float(row[4] or 0), "volume": float(row[5] or 0)}
                          for row in r.fetchall() if float(row[4] or 0) > 0]
            if len(kline_rows) < 25:
                return None

            from .features import compute_daily_features_77
            feat77 = compute_daily_features_77(kline_rows, -1)

            # 2. Load pre-computed context from daily_samples
            r2 = await s.execute(text(
                "SELECT emotion_label, emotion_features, cycle_phase, cycle_day, "
                "lead_lag_min, independent_pct, amplify_ratio, daily_features "
                "FROM stock_dna.daily_samples WHERE symbol=:sym AND trade_date <= :td "
                "ORDER BY trade_date DESC LIMIT 1"
            ), {"sym": symbol, "td": trade_date})
            dna_row = r2.fetchone()

            # 3. Profile: transition matrix + cycle stats + history
            r3 = await s.execute(text(
                "SELECT transition_matrix, stationary_dist, avg_lockup_days, cycle_cv, "
                "avg_breakout_return, n_emotions, training_samples "
                "FROM stock_dna.profiles WHERE symbol=:sym"
            ), {"sym": symbol})
            prof = r3.fetchone()

            # 4. History features from recent daily_samples
            r4 = await s.execute(text(
                "SELECT excess_ret_t2, excess_ret_t5, excess_ret_t10, excess_ret_t20 "
                "FROM stock_dna.daily_samples WHERE symbol=:sym AND trade_date < :td "
                "AND excess_ret_t5 IS NOT NULL ORDER BY trade_date DESC LIMIT 120"
            ), {"sym": symbol, "td": trade_date})
            past_returns = r4.fetchall()

        # ── Assemble features ──
        features = dict(feat77)

        # Emotion features: prefer DB stored, fallback to pseudo-emotion from daily
        emotion_done = False
        if dna_row:
            ef_raw = dna_row[1]  # emotion_features JSONB
            if isinstance(ef_raw, str):
                try: ef_raw = json.loads(ef_raw)
                except Exception: ef_raw = {}
            if isinstance(ef_raw, dict) and ef_raw:
                nonzero = sum(1 for v in ef_raw.values() if abs(float(v or 0)) > 1e-9)
                if nonzero >= 3:  # at least 3 non-zero dims -> real data
                    for k in EMOTION_FEAT_NAMES:
                        features[k] = float(ef_raw.get(k, 0.0) or 0.0)
                    emotion_done = True

        if not emotion_done:
            from .emotion import pseudo_emotion_from_daily
            pseudo = pseudo_emotion_from_daily(kline_rows, len(kline_rows) - 1)
            for k in EMOTION_FEAT_NAMES:
                features[k] = float(pseudo.get(k, 0.0) or 0.0)

        # Market features
        if dna_row:
            features["mkt_lead_lag"] = float(dna_row[4] or 0) if len(dna_row) > 4 else 0.0
            features["mkt_independent_ratio"] = float(dna_row[5] or 0) if len(dna_row) > 5 else 0.5
            features["mkt_amplify_ratio"] = float(dna_row[6] or 0) if len(dna_row) > 6 else 0.0
        for k in MARKET_FEAT_NAMES:
            features.setdefault(k, 0.0)

        # Transition features
        if prof:
            import numpy as np
            P = np.array(prof[0]) if isinstance(prof[0], list) else json.loads(prof[0] or '[]')
            pi = np.array(prof[1]) if isinstance(prof[1], list) else json.loads(prof[1] or '[]')
            emo_label = int(dna_row[0] or 0) if dna_row else 0
            if len(P) > 0 and len(pi) > 0:
                from .emotion import extract_transition_features
                tr_feat = extract_transition_features(P, pi, emo_label)
                features.update(tr_feat)
        for k in TRANSITION_FEAT_NAMES:
            features.setdefault(k, 0.0)

        # Cycle features
        cycle_day = int(dna_row[3]) if dna_row and len(dna_row) > 3 else 0
        avg_lock = float(prof[2]) if prof and len(prof) > 2 else 10.0
        features["cy_is_locked"] = float(1 if (dna_row and dna_row[2] == "lockup") else 0)
        features["cy_lockup_day"] = float(cycle_day)
        features["cy_position_pct"] = round(cycle_day / max(avg_lock, 1), 3)
        features["cy_lockup_remaining_est"] = round(max(avg_lock - cycle_day, 0), 1)
        features["cy_avg_lockup_days"] = float(avg_lock)
        features["cy_cv_lockup"] = float(prof[3]) if prof and len(prof) > 3 else 999.0
        # cy_breakout_prob: derived from cycle regularity (lower CV → more predictable → higher prob in window)
        cv_val = float(prof[3]) if prof and len(prof) > 3 else 999.0
        if cv_val < 0.3: features["cy_breakout_prob"] = 0.65
        elif cv_val < 0.6: features["cy_breakout_prob"] = 0.45
        elif cv_val < 999: features["cy_breakout_prob"] = 0.25
        else: features["cy_breakout_prob"] = 0.15
        features["cy_expected_ret_if_breakout"] = round(float(prof[4] or 0), 2) if prof and len(prof) > 4 and prof[4] else 3.0
        for k in CYCLE_FEAT_NAMES:
            features.setdefault(k, 0.0)

        # History features from past excess returns
        if past_returns:
            import numpy as np
            t2 = [float(r[0] or 0) for r in past_returns if r[0] is not None]
            t5 = [float(r[1] or 0) for r in past_returns if r[1] is not None]
            t10 = [float(r[2] or 0) for r in past_returns if r[2] is not None]
            t20 = [float(r[3] or 0) for r in past_returns if r[3] is not None]
            for h, rets in [("t2", t2), ("t5", t5), ("t10", t10), ("t20", t20)]:
                if rets:
                    features[f"hi_avg_ret_{h}"] = round(float(np.mean(rets)), 3)
                    features[f"hi_winrate_{h}"] = round(sum(1 for r in rets if r > 0) / len(rets), 3)
                else:
                    features[f"hi_avg_ret_{h}"] = 0.0
                    features[f"hi_winrate_{h}"] = 0.5
            all_r = t2 + t5 + t10 + t20
            features["hi_ret_volatility"] = round(float(np.std(all_r)), 3) if all_r else 0.0
            best_h = max([("t2", len(t2)), ("t5", len(t5)), ("t10", len(t10)), ("t20", len(t20))],
                        key=lambda x: features.get(f"hi_winrate_{x[0]}", 0))[0]
            features["hi_best_horizon"] = float({"t2": 2, "t5": 5, "t10": 10, "t20": 20}.get(best_h, 5))
        for k in HISTORY_FEAT_NAMES:
            if k not in features:
                features[k] = 0.0

        # Interaction features (cycle position x emotion)
        cy_pos = features.get("cy_position_pct", 0)
        emo = int(dna_row[0] or 0) if dna_row else 0
        for j in range(4):
            features[f"ix_lockup_emotion_cross_{j}"] = cy_pos * (1 if emo == j else 0)
            features[f"ix_breakout_emotion_cross_{j}"] = (1 - cy_pos) * (1 if emo == j else 0)
        for k in INTERACT_FEAT_NAMES:
            features.setdefault(k, 0.0)

        return features

    async def _get_cycle_context(self, symbol: str, trade_date: date) -> dict:
        """获取当前周期位置."""
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT cycle_phase, cycle_day FROM stock_dna.daily_samples "
                "WHERE symbol=:sym AND trade_date <= :td ORDER BY trade_date DESC LIMIT 1"
            ), {"sym": symbol, "td": trade_date})
            row = r.fetchone()
            if row:
                r2 = await s.execute(text(
                    "SELECT avg_lockup_days FROM stock_dna.profiles WHERE symbol=:sym"
                ), {"sym": symbol})
                prof = r2.fetchone()
                avg_lock = float(prof[0]) if prof else 10.0
                day = int(row[1] or 0)
                return {
                    "phase": row[0] or "normal",
                    "day": day,
                    "position": round(day / max(avg_lock, 1), 3),
                }
        return {"phase": "unknown", "day": 0, "position": 0}

    async def _get_emotion_context(self, symbol: str, trade_date: date) -> dict:
        """获取当前表情."""
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT emotion_label FROM stock_dna.daily_samples "
                "WHERE symbol=:sym AND trade_date <= :td ORDER BY trade_date DESC LIMIT 1"
            ), {"sym": symbol, "td": trade_date})
            row = r.fetchone()
            if row:
                label = int(row[0] or 0)
                # 尝试从 DNA profile 读取表情名称
                r2 = await s.execute(text(
                    "SELECT emotion_names FROM stock_dna.profiles WHERE symbol=:sym"
                ), {"sym": symbol})
                prof = r2.fetchone()
                names = {}
                if prof and prof[0]:
                    names = prof[0] if isinstance(prof[0], dict) else json.loads(prof[0] or '{}')
                return {"id": label, "name": names.get(str(label), f"表情{label}")}
        return {"id": 0, "name": "未知"}


# 全局单例
scorer = StockDNAScorer()
