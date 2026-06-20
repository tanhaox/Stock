# Status: P2 — train() implemented, model files loaded by signal_quality_scorer at inference time.
# Training is NOT connected to scheduler_loop.py — needs weekly scheduling entry.
"""双通道 XGBoost 训练引擎 — 天使增益 + 护法守护.

天使通道: 预测 T+2 收益 (回归)
护法通道: 预测 P(欺骗) (分类)

特征体系 (43维):
  12 信号元数据 — 当前快照
   8 量价行为   — K线形态
   3 个股欺骗史 — 历史统计
   8 学习轨迹   — 跨时间演变 (NEW)
  10 品类编码
   2 市场编码

训练后模型持久化到 models/ (XGBoost JSON格式).
"""
import json, logging, numpy as np
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory
from app.utils.numpy_utils import safe_float

logger = logging.getLogger(__name__)

ARCHETYPES = [
    "主板_large_bluechip", "主板_small_speculative", "主板_growth_tech",
    "主板_value_defensive", "主板_cyclical_resource",
    "创业板_large_bluechip", "创业板_small_speculative", "创业板_growth_tech",
    "创业板_value_defensive", "创业板_cyclical_resource",
]

MARKETS = ["主板", "中小板", "创业板"]

# ── 特征名列表 (训练和预测必须严格对齐) ──

SIGNAL_META_FEATURES = [
    "composite_score_norm",     # 0  归一化评分
    "push_count_30d",           # 1  30日推送次数
    "price_zone_width_norm",    # 2  价格区间宽度(归一化)
    "push_ratio",               # 3  推送比例
    "prev_profitable",          # 4  上一笔是否盈利
    "prev_return_norm",         # 5  上一笔收益(连续值)
    "score_push_interact",      # 6  高分+高频交互
    "score_squared",            # 7  极端分数
    "push_high_flag",           # 8  过热标志
    "narrow_range_flag",        # 9  窄幅横盘标志
    "consec_pushes",            # 10 连续推送
    "days_gap",                 # 11 距上次信号间隔
]

BEHAVIORAL_FEATURES = [
    "vol_price_corr_10d",       # 12 10日量价相关系数
    "vol_expanding_ratio",      # 13 量能变化 (5日均量/20日均量)
    "price_velocity_5d",        # 14 5日价格速度
    "rel_position_20d",         # 15 相对20日区间位置
    "ma_alignment",             # 16 均线多头排列 (0/1/2)
    "volatility_ratio",         # 17 波动率比 (5日/20日)
    "up_vol_ratio_10d",         # 18 上涨日量占比
    "ma20_distance",            # 19 偏离MA20程度
]

STOCK_HISTORY_FEATURES = [
    "stock_deception_rate",     # 20 该股历史欺骗率
    "stock_signal_count_norm",  # 21 该股历史信号数(归一化)
    "stock_avg_return_norm",    # 22 该股历史平均收益(归一化)
]

TRAJECTORY_FEATURES = [
    "score_trend",              # 23 评分趋势 (当前-前3均)/100
    "score_from_peak",          # 24 距历史峰值 (当前-峰值)/100
    "push_density_5d",          # 25 5日内推送密度
    "avg_push_interval_norm",   # 26 平均推送间隔(归一化)
    "market_return_5d",         # 27 大盘5日收益
    "market_vol_20d",           # 28 大盘20日波动率
    "vol_trend_across_pushes",  # 29 推送间量能趋势
    "score_vol_divergence",     # 30 评分上行+量能下行=背离
]

FEATURE_NAMES = (SIGNAL_META_FEATURES + BEHAVIORAL_FEATURES +
                 STOCK_HISTORY_FEATURES + TRAJECTORY_FEATURES)

# 大盘指数映射
MARKET_INDEX = {"主板": "700001.TI", "中小板": "399005.SZ", "创业板": "399006.SZ"}


def extract_behavioral_features(bars: list, close_price: float = None) -> list[float]:
    """从 K 线数据提取信号前的量价行为特征 (8个)."""
    if len(bars) < 10:
        return [0.0] * len(BEHAVIORAL_FEATURES)

    closes = np.array([b["close"] for b in bars], dtype=np.float64)
    volumes = np.array([b["vol"] if "vol" in b else b.get("volume", 0) for b in bars], dtype=np.float64)
    highs = np.array([b.get("high", b["close"]) for b in bars], dtype=np.float64)
    lows = np.array([b.get("low", b["close"]) for b in bars], dtype=np.float64)

    n = len(bars)
    entry_price = close_price if close_price else closes[-1]

    # 1. 量价相关系数 (10日)
    rets_10 = np.diff(closes[-11:]) / closes[-11:-1] if n >= 11 else np.diff(closes) / closes[:-1]
    vols_10 = volumes[-10:] if n >= 10 else volumes[1:]
    if len(rets_10) > 3 and np.std(rets_10) > 1e-10 and np.std(vols_10) > 1e-10:
        vol_price_corr = float(np.corrcoef(rets_10, vols_10)[0, 1])
        vol_price_corr = max(-1.0, min(1.0, safe_float(vol_price_corr, 0.0)))
    else:
        vol_price_corr = 0.0

    # 2. 量能变化
    vol_5d = np.mean(volumes[-5:]) if n >= 5 else np.mean(volumes)
    vol_20d = np.mean(volumes[-20:]) if n >= 20 else np.mean(volumes)
    vol_expanding = float(vol_5d / vol_20d) if vol_20d > 0 else 1.0

    # 3. 价格速度
    if n >= 6:
        price_velocity = float((closes[-1] / closes[-6] - 1) / 5)
    else:
        price_velocity = float((closes[-1] / closes[0] - 1) / max(n - 1, 1))

    # 4. 相对20日区间位置
    high_20 = float(np.max(highs[-20:])) if n >= 20 else float(np.max(highs))
    low_20 = float(np.min(lows[-20:])) if n >= 20 else float(np.min(lows))
    price_range = high_20 - low_20
    rel_position = float((entry_price - low_20) / price_range) if price_range > 0 else 0.5

    # 5. 均线多头排列
    ma5 = float(np.mean(closes[-5:])) if n >= 5 else float(np.mean(closes))
    ma10 = float(np.mean(closes[-10:])) if n >= 10 else float(np.mean(closes))
    ma20 = float(np.mean(closes[-20:])) if n >= 20 else float(np.mean(closes))
    ma_alignment = 0
    if ma5 > ma10: ma_alignment += 1
    if ma10 > ma20: ma_alignment += 1

    # 6. 波动率比
    rets_5 = np.diff(closes[-6:]) / closes[-6:-1] if n >= 6 else np.diff(closes) / closes[:-1]
    rets_20 = np.diff(closes[-21:]) / closes[-21:-1] if n >= 21 else rets_5
    vol_5 = float(np.std(rets_5)) if len(rets_5) > 0 else 0.01
    vol_20 = float(np.std(rets_20)) if len(rets_20) > 0 else 0.01
    volatility_ratio = vol_5 / vol_20 if vol_20 > 1e-10 else 1.0

    # 7. 上涨日量占比
    rets_10d = np.diff(closes[-11:]) / closes[-11:-1] if n >= 11 else np.diff(closes) / closes[:-1]
    vol_10d = volumes[-len(rets_10d):]
    up_vol = sum(v for r, v in zip(rets_10d, vol_10d) if r > 0)
    total_vol = sum(vol_10d)
    up_vol_ratio = float(up_vol / total_vol) if total_vol > 0 else 0.5

    # 8. 偏离MA20
    ma20_dist = float((entry_price - ma20) / ma20) if ma20 > 0 else 0.0

    return [
        round(vol_price_corr, 4),
        round(min(3.0, max(0.3, vol_expanding)), 4),
        round(max(-0.05, min(0.05, price_velocity)), 4),
        round(max(0.0, min(1.0, rel_position)), 4),
        float(ma_alignment),
        round(max(0.2, min(3.0, volatility_ratio)), 4),
        round(max(0.0, min(1.0, up_vol_ratio)), 4),
        round(max(-0.3, min(0.3, ma20_dist)), 4),
    ]


def _compute_trajectory_features(
    sym: str, sd, score: float, push_f: float,
    stock_scores: dict, stock_dates: dict, stock_volumes: dict,
    index_bars: list,
) -> list[float]:
    """计算学习轨迹特征 (8个).

    Args:
        stock_scores: symbol -> [previous scores]
        stock_dates: symbol -> [previous signal dates]
        stock_volumes: symbol -> [volume on previous signal dates]
        index_bars: market index kline bars sorted by date
    """
    prev_scores = stock_scores.get(sym, [])
    prev_dates = stock_dates.get(sym, [])
    prev_vols = stock_volumes.get(sym, [])

    # 1. 评分趋势: (当前 - 前3均值) / 100
    if len(prev_scores) >= 1:
        lookback = min(3, len(prev_scores))
        avg_prev = np.mean(prev_scores[-lookback:])
        score_trend = (score - avg_prev) / 100
    else:
        score_trend = 0.0

    # 2. 距历史峰值: (当前 - 历史最高) / 100
    if prev_scores:
        peak = max(prev_scores)
        score_from_peak = (score - peak) / 100
    else:
        score_from_peak = 0.0

    # 3. 推送密度: 5日内推送次数 / 5
    if prev_dates:
        cutoff_5d = sd - timedelta(days=5)
        pushes_5d = sum(1 for d in prev_dates if d >= cutoff_5d)
        push_density = pushes_5d / 5
    else:
        push_density = 0.0

    # 4. 平均推送间隔 (归一化)
    if len(prev_dates) >= 2:
        intervals = [(prev_dates[i] - prev_dates[i-1]).days for i in range(1, len(prev_dates))]
        intervals = intervals[-5:]  # 最近5个间隔
        avg_interval = np.mean(intervals)
        avg_interval_norm = min(avg_interval, 30) / 30
    elif len(prev_dates) == 1:
        avg_interval_norm = min((sd - prev_dates[0]).days, 30) / 30
    else:
        avg_interval_norm = 1.0  # 无历史=间隔大

    # 5-6. 大盘上下文
    market_return_5d = 0.0
    market_vol_20d = 0.0
    if index_bars:
        idx_before = [b for b in index_bars if b["date"] <= sd]
        if len(idx_before) >= 6:
            market_return_5d = float((idx_before[-1]["close"] / idx_before[-6]["close"] - 1))
            market_return_5d = round(max(-0.1, min(0.1, market_return_5d)), 4)
        if len(idx_before) >= 21:
            idx_rets = np.diff([b["close"] for b in idx_before[-21:]]) / [b["close"] for b in idx_before[-21:-1]]
            market_vol_20d = float(np.std(idx_rets)) if len(idx_rets) > 0 else 0.0
            market_vol_20d = round(min(0.05, market_vol_20d), 4)

    # 7. 推送间量能趋势: (近期量 - 早期量) / 早期量
    if len(prev_vols) >= 2:
        recent_vol = np.mean(prev_vols[-min(3, len(prev_vols)):])
        early_vol = np.mean(prev_vols[:min(3, len(prev_vols))])
        vol_trend = (recent_vol - early_vol) / early_vol if early_vol > 0 else 0.0
        vol_trend = round(max(-1.0, min(1.0, vol_trend)), 4)
    else:
        vol_trend = 0.0

    # 8. 评分-量能背离: 评分上行但量能下行 = 1
    if score_trend > 0.02 and vol_trend < -0.1:
        score_vol_divergence = 1.0
    elif score_trend < -0.02 and vol_trend > 0.1:
        score_vol_divergence = 0.0  # 反向背离(好信号)
    else:
        score_vol_divergence = 0.0

    return [
        round(score_trend, 4),
        round(score_from_peak, 4),
        round(min(1.0, push_density), 4),
        round(avg_interval_norm, 4),
        market_return_5d,
        market_vol_20d,
        vol_trend,
        score_vol_divergence,
    ]


async def build_training_data() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """从 signal_history + daily_kline 构建训练数据 (43维)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT sh.symbol, sh.scan_date, sh.composite_score, sh.archetype,
                   sh.market, sh.push_count_30d, sh.price_zone_width_pct,
                   sh.ret_t2, sh.outcome_label, sh.deception_type
            FROM signal_history sh
            WHERE sh.ret_t2 IS NOT NULL AND sh.archetype IS NOT NULL
            ORDER BY sh.scan_date
        """))
        rows = r.fetchall()

    if not rows:
        return np.array([]), np.array([]), np.array([])

    # ── 批量加载 K 线 (个股 + 大盘指数) ──
    symbols = list(set(r[0] for r in rows))
    index_codes = list(set(MARKET_INDEX.values()))
    all_codes = symbols + index_codes
    min_date = rows[0][1] - timedelta(days=60)
    max_date = rows[-1][1]

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, close, volume, open, high, low
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :d1 AND :d2
            ORDER BY ts_code, trade_date
        """), {"syms": all_codes, "d1": min_date, "d2": max_date})
        klines = defaultdict(list)
        for row in r.fetchall():
            klines[row[0]].append({
                "date": row[1], "close": float(row[2]), "vol": float(row[3]),
                "open": float(row[4]), "high": float(row[5]), "low": float(row[6]),
            })

    logger.info(f"Loaded kline data for {len(klines)} symbols (incl. indices)")

    # 索引 K 线 (fallback: 700001.TI 通用)
    idx_bars_main = klines.get("700001.TI", [])
    idx_bars_chinext = klines.get("399006.SZ", [])

    X_list, y_reg_list, y_cls_list = [], [], []
    prev_outcome = {}
    prev_date = {}
    consec = {}
    stock_signals = {}
    stock_deceptions = {}
    # 轨迹追踪
    stock_scores = defaultdict(list)
    stock_dates = defaultdict(list)
    stock_volumes = defaultdict(list)

    for row in rows:
        sym, sd, score, arch, mkt, push, pz_width, ret_t2, outcome, deception = row

        push_f = float(push or 1)
        score_norm = float(score or 0) / 100
        pz_norm = float(pz_width or 10) / 50
        push_ratio = push_f / 10

        # ── 信号元数据特征 (12个) ──
        days_gap = (sd - prev_date[sym]).days if sym in prev_date else 60
        days_gap_norm = min(days_gap, 60) / 60

        if sym in prev_date and (sd - prev_date[sym]).days <= 5:
            consec[sym] = consec.get(sym, 0) + 1
        else:
            consec[sym] = 0
        consec_norm = min(consec[sym], 10) / 10

        prev_ret = prev_outcome.get(sym, 0)
        prev_ret_norm = max(-20, min(20, prev_ret)) / 20 + 0.5

        meta_feats = [
            score_norm, push_f, pz_norm, push_ratio,
            1.0 if prev_ret > 0 else 0.0, prev_ret_norm,
            score_norm * push_ratio, score_norm ** 2,
            1.0 if push_f > 5 else 0.0,
            1.0 if pz_norm < 0.1 else 0.0,
            consec_norm, days_gap_norm,
        ]

        # ── 量价行为特征 (8个) ──
        symbol_bars = klines.get(sym, [])
        pre_bars = [b for b in symbol_bars if b["date"] <= sd][-25:]
        entry_close = pre_bars[-1]["close"] if pre_bars else None
        behav_feats = extract_behavioral_features(pre_bars, entry_close)

        # ── 个股欺骗史特征 (3个) ──
        stock_total = len(stock_signals.get(sym, []))
        stock_deception_count = stock_deceptions.get(sym, 0)
        sdr = stock_deception_count / stock_total if stock_total > 0 else 0.0
        scn = min(stock_total, 20) / 20
        sar = float(np.mean(stock_signals[sym])) if stock_total > 0 else 0.0
        sarn = max(-20, min(20, sar)) / 20 + 0.5

        history_feats = [sdr, scn, sarn]

        # ── 学习轨迹特征 (8个) ──
        # 获取信号日的成交量
        signal_vol = 0.0
        for b in symbol_bars:
            if b["date"] == sd:
                signal_vol = b.get("vol", 0)
                break

        # 选择对应市场的指数
        idx_bars = idx_bars_chinext if mkt == "创业板" else idx_bars_main

        traj_feats = _compute_trajectory_features(
            sym, sd, float(score), push_f,
            stock_scores, stock_dates, stock_volumes,
            idx_bars,
        )

        # 组装全部特征 (23 base + 10 arch + 2 market = 35, wait no, 12+8+3+8 = 31 + 10 + 2 = 43)
        feats = meta_feats + behav_feats + history_feats + traj_feats

        for a in ARCHETYPES:
            feats.append(1.0 if arch == a else 0.0)
        for m in MARKET_INDEX:
            feats.append(1.0 if mkt == m else 0.0)

        X_list.append(feats)
        y_reg_list.append(float(ret_t2 or 0) / 20)

        is_deceptive = deception not in ("normal", "breakout", "unknown") if deception else False
        y_cls_list.append(1.0 if is_deceptive else 0.0)

        # 更新状态 (必须在特征计算之后, 避免数据泄漏)
        prev_outcome[sym] = float(ret_t2 or 0)
        prev_date[sym] = sd
        if sym not in stock_signals:
            stock_signals[sym] = []
        stock_signals[sym].append(float(ret_t2 or 0))
        if is_deceptive:
            stock_deceptions[sym] = stock_deception_count + 1

        stock_scores[sym].append(float(score))
        stock_dates[sym].append(sd)
        stock_volumes[sym].append(signal_vol)

    X = np.array(X_list, dtype=np.float32)
    y_reg = np.array(y_reg_list, dtype=np.float32)
    y_cls = np.array(y_cls_list, dtype=np.float32)

    logger.info(f"Built training data: {len(X)} samples, {X.shape[1]} features, "
                f"deception_rate={y_cls.mean():.2%}")
    return X, y_reg, y_cls


async def train_dual_channel() -> dict:
    """训练双通道 XGBoost 模型."""
    try:
        import xgboost as xgb
    except ImportError:
        logger.warning("XGBoost not installed, using fallback rules")
        return {"status": "fallback", "reason": "XGBoost not installed"}

    X, y_reg, y_cls = await build_training_data()
    if len(X) < 100:
        return {"status": "error", "reason": f"Not enough data: {len(X)} samples"}

    n = len(X)
    split = int(n * 0.8)
    X_train, X_test = X[:split], X[split:]
    y_reg_train, y_reg_test = y_reg[:split], y_reg[split:]
    y_cls_train, y_cls_test = y_cls[:split], y_cls[split:]

    # ── 天使模型 ──
    angel = xgb.XGBRegressor(
        n_estimators=150, max_depth=4, learning_rate=0.03,
        subsample=0.7, colsample_bytree=0.7, random_state=42,
        reg_alpha=1.0, reg_lambda=2.0,
    )
    angel.fit(X_train, y_reg_train)
    angel_preds = angel.predict(X_test)
    angel_mae = float(np.mean(np.abs(angel_preds - y_reg_test)))

    # ── 护法模型 ──
    guardian = xgb.XGBClassifier(
        n_estimators=150, max_depth=4, learning_rate=0.03,
        subsample=0.7, colsample_bytree=0.7, random_state=42,
        reg_alpha=1.0, reg_lambda=2.0, min_child_weight=3,
        scale_pos_weight=(1 - y_cls_train.mean()) / max(y_cls_train.mean(), 0.01),
    )
    guardian.fit(X_train, y_cls_train)
    guardian_preds = guardian.predict_proba(X_test)[:, 1]

    # ── 特征重要性 ──
    all_feat_names = FEATURE_NAMES + [f"arch_{a}" for a in ARCHETYPES] + [f"mkt_{m}" for m in MARKETS]
    guardian_importance = sorted(
        zip(all_feat_names, guardian.feature_importances_),
        key=lambda x: -x[1]
    )
    logger.info(f"Guardian top-8: {[(n, f'{v:.2%}') for n, v in guardian_importance[:8]]}")

    # AUC
    from sklearn.metrics import roc_auc_score
    try:
        guardian_auc = float(roc_auc_score(y_cls_test, guardian_preds))
    except Exception:
        guardian_auc = 0.5

    try:
        train_preds = guardian.predict_proba(X_train)[:, 1]
        train_auc = float(roc_auc_score(y_cls_train, train_preds))
    except Exception:
        train_auc = 0.5

    # ── 持久化 ──
    import os
    model_dir = os.path.join(os.path.dirname(__file__), "..", "..", "models")
    os.makedirs(model_dir, exist_ok=True)

    angel.save_model(os.path.join(model_dir, "angel_model.json"))
    guardian.save_model(os.path.join(model_dir, "guardian_model.json"))

    with open(os.path.join(model_dir, "dual_channel_meta.json"), "w") as f:
        json.dump({
            "feature_names": all_feat_names,
            "archetypes": ARCHETYPES,
            "markets": MARKETS,
            "samples": n, "features": X.shape[1],
            "angel_mae": round(angel_mae, 4),
            "guardian_auc": round(guardian_auc, 4),
            "train_auc": round(train_auc, 4),
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"Training: angel_mae={angel_mae:.4f}, "
                f"guardian_auc={guardian_auc:.4f}, train_auc={train_auc:.4f}")
    return {
        "status": "success",
        "samples": n, "features": X.shape[1],
        "angel_mae": round(angel_mae, 4),
        "guardian_auc": round(guardian_auc, 4),
        "train_auc": round(train_auc, 4),
        "deception_rate": round(float(y_cls.mean()), 3),
    }
