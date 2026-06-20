"""日内表情聚类 + 马尔可夫转移矩阵.

基于 5 分钟 K 线数据, 每只股票独立聚类, 生成个性化表情标签。
每只股票的"喜怒哀乐"不同——泸州老窖的贪婪 ≠ 宁德时代的贪婪。

核心函数:
  extract_emotion_vector() — 从一天的分时K线提取 15 维表情向量
  cluster_emotions() — 对单只股票的所有交易日进行 KMeans++ 聚类
  build_transition_matrix() — 构建马尔可夫转移矩阵 (Laplace 平滑)
  emotion_entropy_rate() — 计算情绪熵率 (可预测性度量)
"""
import numpy as np
import logging
from collections import Counter, defaultdict
from typing import Optional

logger = logging.getLogger("stock_dna.emotion")

# ══════════════════════════════════════════════════════════════════════
# 操盘动作检测 (复用 micro_behavior_analyzer 逻辑, 内联避免依赖)
# ══════════════════════════════════════════════════════════════════════

def _detect_actions_intraday(bars: list[dict]) -> dict[str, int]:
    """从单日 5 分钟 K 线中检测 5 类操盘动作的频次.

    Args:
        bars: 按时间排序的 5 分钟 K 线列表 [{open,high,low,close,volume}, ...]

    Returns:
        {fast_rise: N, fast_fall: N, support_sideways: N, tail_attack_dir: ±1, open_charge: 0/1}
    """
    n = len(bars)
    if n < 30:
        return {"fast_rise": 0, "fast_fall": 0, "support_sideways": 0, "tail_attack": 0, "open_charge": 0}

    closes = np.array([b["close"] for b in bars])
    opens = np.array([b["open"] for b in bars])
    highs = np.array([b["high"] for b in bars])
    lows = np.array([b["low"] for b in bars])
    vols = np.array([b.get("volume", b.get("vol", 0)) for b in bars])

    rolling_vol = np.convolve(vols, np.ones(20) / 20, mode='same')
    rolling_vol[:20] = np.mean(vols[:20])

    actions = {"fast_rise": 0, "fast_fall": 0, "support_sideways": 0, "tail_attack": 0, "open_charge": 0}

    # 快速拉升/砸盘
    for i in range(20, n - 1):
        if opens[i] <= 0 or closes[i] <= 0:
            continue
        pct = (closes[i] - opens[i]) / opens[i] * 100
        avg_vol = rolling_vol[i]
        if pct >= 1.5 and vols[i] > avg_vol * 2.0:
            actions["fast_rise"] += 1
        elif pct <= -1.5 and vols[i] > avg_vol * 2.0:
            actions["fast_fall"] += 1

    # 托单横盘: 连续 >= 6 根振幅 < 0.3% 且放量
    streak = 0
    for i in range(1, n):
        amp = (highs[i] - lows[i]) / max(opens[i], 0.01) * 100
        if amp < 0.3 and vols[i] > rolling_vol[i] * 1.5:
            streak += 1
        else:
            if streak >= 6:
                actions["support_sideways"] += 1
            streak = 0

    # 尾盘偷袭 (最后 6 根)
    if n >= 6:
        tail_ret = (closes[-1] - opens[-6]) / max(opens[-6], 0.01) * 100
        if abs(tail_ret) >= 1.0:
            same_dir = sum(1 for j in range(n - 6, n)
                         if (tail_ret > 0 and closes[j] > opens[j]) or (tail_ret < 0 and closes[j] < opens[j]))
            if same_dir >= 3:
                actions["tail_attack"] = 1 if tail_ret > 0 else -1

    # 开盘冲锋 (前 12 根)
    for i in range(min(12, n)):
        bar_pct = (closes[i] - opens[i]) / max(opens[i], 0.01) * 100
        if bar_pct >= 1.0 and vols[i] > rolling_vol[i] * 3.0:
            actions["open_charge"] = 1
            break

    return actions


# ══════════════════════════════════════════════════════════════════════
# 15 维表情向量提取
# ══════════════════════════════════════════════════════════════════════

def extract_emotion_vector(bars: list[dict], market_bars: list[dict] = None) -> Optional[dict[str, float]]:
    """从单日 5 分钟 K 线提取 15 维表情向量.

    Args:
        bars: 个股 5 分钟 K 线, 至少 30 根
        market_bars: 大盘 5 分钟 K 线 (000001.SH), 用于 lead_lag 计算

    Returns:
        15 维表情向量 dict, 数据不足返回 None
    """
    n = len(bars)
    if n < 30:
        return None

    closes = np.array([b["close"] for b in bars], dtype=np.float64)
    opens = np.array([b["open"] for b in bars], dtype=np.float64)
    highs = np.array([b["high"] for b in bars], dtype=np.float64)
    lows = np.array([b["low"] for b in bars], dtype=np.float64)
    vols = np.array([b.get("volume", b.get("vol", 0)) for b in bars], dtype=np.float64)

    day_open = opens[0]
    day_close = closes[-1]
    day_ret = (day_close - day_open) / max(day_open, 0.01) * 100

    e = {}

    # 1. 开盘 30 分方向 (前 6 根)
    open30_ret = (closes[5] - opens[0]) / max(opens[0], 0.01) * 100 if n >= 6 else 0
    e["em_open_dir"] = round(open30_ret, 4)

    # 2. 开盘 30 分量比
    avg_vol_full = float(np.mean(vols))
    open30_vol = float(np.mean(vols[:6])) if n >= 6 else avg_vol_full
    e["em_open_vol_ratio"] = round(open30_vol / max(avg_vol_full, 1e-9), 4)

    # 3. 趋势持续性 (连续同向K线占比)
    same_dir = 0
    for i in range(1, n):
        if (closes[i] - opens[i]) * (closes[i - 1] - opens[i - 1]) > 0:
            same_dir += 1
    e["em_trend_persistence"] = round(same_dir / max(n - 1, 1), 4)

    # 4. 反转频率 (>1% 的反转次数)
    reversals = sum(1 for i in range(1, n)
                    if abs(closes[i] / max(opens[i], 0.01) - 1) * 100 > 1.0
                    and (closes[i] - opens[i]) * (closes[i - 1] - opens[i - 1]) < 0)
    e["em_reversal_freq"] = round(reversals / max(n, 1), 4)

    # 5. VWAP 位置
    vwap_vol = vols.copy()
    vwap = float(np.sum(closes * vwap_vol) / max(np.sum(vwap_vol), 1e-9))
    e["em_vwap_position"] = round((day_close - vwap) / max(vwap, 0.01) * 100, 4)

    # 6. 上下午势能差
    mid = n // 2
    am_ret = (closes[mid - 1] - opens[0]) / max(opens[0], 0.01) * 100 if mid > 0 else 0
    pm_ret = (closes[-1] - closes[mid]) / max(closes[mid], 0.01) * 100 if mid < n else 0
    e["em_am_pm_split"] = round(am_ret - pm_ret, 4)

    # 7. 量能集中度 (最大的 3 根 K 线量占全天比)
    top3_vol = float(np.sum(np.sort(vols)[-3:]))
    e["em_vol_concentration"] = round(top3_vol / max(float(np.sum(vols)), 1e-9), 4)

    # 8. 量价相关系数 (5 分钟级别)
    rets = np.diff(closes) / closes[:-1] * 100
    vol_diffs = vols[1:]
    if len(rets) > 2 and np.std(vol_diffs) > 0:
        corr = float(np.corrcoef(rets, vol_diffs)[0, 1])
        if np.isnan(corr): corr = 0.0
    else:
        corr = 0.0
    e["em_vol_price_corr"] = round(corr, 4)

    # 9. 大单偏向 (高量K线的方向)
    high_vol_idx = np.argsort(vols)[-5:]
    high_bias = sum(1 if closes[i] > opens[i] else -1 for i in high_vol_idx)
    e["em_large_bar_bias"] = round(high_bias / 5.0, 4)

    # 10. 尾盘动作 (最后 6 根的收益)
    tail_ret = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) * 100 if n >= 6 else 0
    e["em_close_action"] = round(tail_ret, 4)

    # 11. 均价线斜率 (用 VWAP 的线性回归斜率)
    cum_vol = np.cumsum(vwap_vol)
    cum_vw_price = np.cumsum(closes * vwap_vol) / np.maximum(cum_vol, 1e-9)
    x = np.arange(len(cum_vw_price))
    if len(x) > 2:
        slope = float(np.polyfit(x, cum_vw_price, 1)[0])
        e["em_vwap_slope"] = round(slope / max(day_open, 0.01) * 100, 4)
    else:
        e["em_vwap_slope"] = 0.0

    # 12. 振幅分位数 (今日振幅 / 近 20 日振幅排名)
    today_amp = (float(np.max(highs)) - float(np.min(lows))) / max(day_open, 0.01) * 100
    e["em_amplitude_pctile"] = round(today_amp, 4)  # 分位数由聚类后的统计计算

    # 13. 操盘动作密度
    actions = _detect_actions_intraday(bars)
    e["em_action_density"] = round((actions["fast_rise"] + actions["fast_fall"] +
                                     actions["support_sideways"] + abs(actions["tail_attack"]) +
                                     actions["open_charge"]) / max(n / 48, 1), 4)

    # 14. 领先大盘时延 (如果提供了 market_bars)
    e["em_lead_lag_min"] = _compute_lead_lag(bars, market_bars) if market_bars else 0.0

    # 15. 独立波动占比
    if market_bars and len(market_bars) >= 30:
        indep = _compute_independent_pct(bars, market_bars)
    else:
        indep = 0.5
    e["em_independent_pct"] = round(indep, 4)

    return e


def _compute_lead_lag(stock_bars: list[dict], market_bars: list[dict]) -> float:
    """计算个股领先/滞后大盘的分钟数 (交叉相关峰值位置)."""
    s_rets = np.array([(stock_bars[i]["close"] / max(stock_bars[i]["open"], 0.01) - 1) * 100
                       for i in range(min(len(stock_bars), len(market_bars)))])
    m_rets = np.array([(market_bars[i]["close"] / max(market_bars[i]["open"], 0.01) - 1) * 100
                       for i in range(min(len(stock_bars), len(market_bars)))])

    if len(s_rets) < 20 or np.std(s_rets) == 0 or np.std(m_rets) == 0:
        return 0.0

    # 交叉相关: 个股领先 = 负 lag
    max_corr, best_lag = 0, 0
    for lag in range(-6, 7):  # ±30 分钟
        if lag < 0:
            c = float(np.corrcoef(s_rets[-lag:], m_rets[:lag])[0, 1]) if len(s_rets[-lag:]) > 5 else 0
        elif lag > 0:
            c = float(np.corrcoef(s_rets[:len(m_rets) - lag], m_rets[lag:])[0, 1]) if len(m_rets[lag:]) > 5 else 0
        else:
            c = float(np.corrcoef(s_rets, m_rets)[0, 1])
        if not np.isnan(c) and abs(c) > abs(max_corr):
            max_corr, best_lag = c, lag

    return round(float(best_lag) * 5, 1)  # 转换为分钟


def _compute_independent_pct(stock_bars: list[dict], market_bars: list[dict]) -> float:
    """计算个股独立于大盘的波动占比 (1 - R²)."""
    n = min(len(stock_bars), len(market_bars))
    if n < 20:
        return 0.5

    s_rets = np.array([(stock_bars[i]["close"] / max(stock_bars[i]["open"], 0.01) - 1) * 100 for i in range(n)])
    m_rets = np.array([(market_bars[i]["close"] / max(market_bars[i]["open"], 0.01) - 1) * 100 for i in range(n)])

    if np.std(m_rets) == 0 or np.std(s_rets) == 0:
        return 0.5

    corr = float(np.corrcoef(s_rets, m_rets)[0, 1])
    if np.isnan(corr):
        return 0.5
    return round(1.0 - corr ** 2, 4)


# ══════════════════════════════════════════════════════════════════════
# 日线伪表情 (无 min_kline 数据时的降级方案)
# ══════════════════════════════════════════════════════════════════════

def pseudo_emotion_from_daily(kline_rows: list[dict], idx: int) -> dict[str, float]:
    """从日线 OHLCV 计算简化的 10 维表情特征 (无需分时数据).

    用作 min_kline 数据不可用时的降级方案.
    """
    n = len(kline_rows)
    if idx < 6 or n < 10:
        return {}

    closes = np.array([r["close"] for r in kline_rows], dtype=np.float64)
    opens = np.array([r["open"] for r in kline_rows], dtype=np.float64)
    highs = np.array([r["high"] for r in kline_rows], dtype=np.float64)
    lows = np.array([r["low"] for r in kline_rows], dtype=np.float64)
    volumes = np.array([r["volume"] for r in kline_rows], dtype=np.float64)

    c = closes[idx]; o = opens[idx]; h = highs[idx]; l = lows[idx]
    v = volumes[idx]
    pc = closes[idx - 1]; po = opens[idx - 1]
    hl_range = max(h - l, 0.001)

    e = {}

    # 1-3: 影线代理
    e["em_open_dir"] = round((c - o) / max(o, 0.01) * 100, 4)
    e["em_amplitude_pctile"] = round((h - l) / max(o, 0.01) * 100, 4)
    upper_shadow = (h - max(o, c)) / hl_range
    lower_shadow = (min(o, c) - l) / hl_range
    e["em_large_bar_bias"] = round(upper_shadow - lower_shadow, 4)  # 正=上影长(抛压), 负=下影长(支撑)

    # 4-5: 量价关系
    vol20 = float(np.mean(volumes[max(0, idx - 19):idx + 1]))
    e["em_open_vol_ratio"] = round(v / max(vol20, 1e-9), 4)
    ret = (c - o) / max(o, 0.01) * 100
    e["em_vol_price_corr"] = round(ret * e["em_open_vol_ratio"] / 10, 4)  # 量价乘积

    # 6: 跳空
    gap = (o - pc) / max(pc, 0.01) * 100 if pc > 0 else 0
    e["em_close_action"] = round(gap, 4)  # 用 close_action 存跳空

    # 7: 收盘强弱
    close_strength = (c - o) / hl_range if hl_range > 0.001 else 0
    e["em_vwap_position"] = round(close_strength * 100, 4)

    # 8: VWAP斜率代理 (N日趋势)
    if idx >= 5:
        ma5 = float(np.mean(closes[idx - 4:idx + 1]))
        e["em_vwap_slope"] = round((c - ma5) / max(ma5, 0.01) * 100, 4)
    else:
        e["em_vwap_slope"] = 0.0

    # 9: 趋势持续性
    cons_up = sum(1 for j in range(idx, max(0, idx - 5), -1) if closes[j] > closes[j - 1])
    e["em_trend_persistence"] = round(cons_up / 5.0, 4)

    # 10-15: 填充默认值 (分时特有的维度)
    e["em_reversal_freq"] = 0.0
    e["em_am_pm_split"] = 0.0
    e["em_vol_concentration"] = 0.33
    e["em_action_density"] = 0.0
    e["em_lead_lag_min"] = 0.0
    e["em_independent_pct"] = 0.5

    return e


# ══════════════════════════════════════════════════════════════════════
# KMeans++ 聚类 (Per-Stock)
# ══════════════════════════════════════════════════════════════════════

def cluster_emotions(emotion_vectors: list[dict], k_range: tuple = (6, 10)) -> tuple[np.ndarray, int, dict]:
    """对单只股票的所有交易日表情向量进行 KMeans++ 聚类.

    Args:
        emotion_vectors: 该股票所有交易日的 15 维表情向量列表
        k_range: K 的范围 (min, max)

    Returns:
        (labels, best_k, cluster_info)
        labels: 每个交易日的聚类标签 (0 ~ K-1)
        best_k: 选出的最佳 K
        cluster_info: {k: {silhouette, sse, centers}}
    """
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    if len(emotion_vectors) < 30:
        return np.zeros(len(emotion_vectors), dtype=int), 1, {}

    # 构建矩阵 (keys must be defined before precheck)
    keys = ["em_open_dir", "em_open_vol_ratio", "em_trend_persistence",
            "em_reversal_freq", "em_vwap_position", "em_am_pm_split",
            "em_vol_concentration", "em_vol_price_corr", "em_large_bar_bias",
            "em_close_action", "em_vwap_slope", "em_amplitude_pctile",
            "em_action_density", "em_lead_lag_min", "em_independent_pct"]

    # zero-vector guard: if all vectors identical, return single cluster
    X_precheck = np.zeros((len(emotion_vectors), len(keys)), dtype=np.float64)
    for i, ev in enumerate(emotion_vectors):
        for j, k in enumerate(keys):
            X_precheck[i, j] = float(ev.get(k, 0.0) or 0.0)
    if np.all(np.std(X_precheck, axis=0) < 1e-9):
        logger.info(f"All emotion vectors identical (likely no min_kline data). n_emotions=1")
        return np.zeros(len(emotion_vectors), dtype=int), 1, {"k": 1, "silhouette": 0, "centers": {"0": {"count": len(emotion_vectors)}}, "info": {1: {"silhouette": 0, "sse": 0}}}

    X = np.zeros((len(emotion_vectors), len(keys)), dtype=np.float64)
    for i, ev in enumerate(emotion_vectors):
        for j, k in enumerate(keys):
            X[i, j] = float(ev.get(k, 0.0) or 0.0)

    # 标准化
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    best_k, best_score, best_labels, best_info = 4, -1, None, {}
    for k in range(k_range[0], min(k_range[1] + 1, len(emotion_vectors) // 5 + 1)):
        if k < 2:
            continue
        km = KMeans(n_clusters=k, init='k-means++', n_init=10, random_state=42, max_iter=300)
        labels = km.fit_predict(X_scaled)

        # 轮廓系数
        if k > 1 and len(set(labels)) > 1:
            sil = silhouette_score(X_scaled, labels)
        else:
            sil = 0.0

        best_info[k] = {"silhouette": round(sil, 4), "sse": round(km.inertia_, 2)}
        if sil > best_score:
            best_score, best_k, best_labels = sil, k, labels

    if best_k == 4:
        best_k = min(6, len(emotion_vectors) // 5)
        km = KMeans(n_clusters=best_k, init='k-means++', n_init=10, random_state=42)
        best_labels = km.fit_predict(X_scaled)

    # 计算簇中心和应用标签
    centers = {}
    for j in range(best_k):
        mask = best_labels == j
        if mask.sum() > 0:
            centers[str(j)] = {
                "count": int(mask.sum()),
                "avg_close_ret": round(float(np.mean([emotion_vectors[i].get("day_ret", 0) or 0 for i in range(len(emotion_vectors)) if best_labels[i] == j])), 4) if "day_ret" in (emotion_vectors[0] if emotion_vectors else {}) else 0.0,
            }

    return best_labels.astype(int), best_k, {"k": best_k, "silhouette": best_score, "centers": centers, "info": best_info}


# ══════════════════════════════════════════════════════════════════════
# 马尔可夫转移矩阵
# ══════════════════════════════════════════════════════════════════════

def build_transition_matrix(labels: np.ndarray, n_states: int, alpha: float = 1.0) -> np.ndarray:
    """构建 Laplace 平滑的马尔可夫转移矩阵.

    Args:
        labels: 按时间排序的表情标签序列
        n_states: 状态数 K
        alpha: Laplace 平滑参数 (默认 1)

    Returns:
        P: (K, K) 转移概率矩阵, P[i,j] = P(Z_{t+1}=j | Z_t=i)
    """
    if n_states <= 1:
        return np.array([[1.0]])

    C = np.zeros((n_states, n_states))
    for t in range(len(labels) - 1):
        i, j = int(labels[t]), int(labels[t + 1])
        if 0 <= i < n_states and 0 <= j < n_states:
            C[i, j] += 1

    P = np.zeros((n_states, n_states))
    for i in range(n_states):
        row_sum = C[i].sum() + alpha * n_states
        for j in range(n_states):
            P[i, j] = (C[i, j] + alpha) / row_sum

    return P


def stationary_distribution(P: np.ndarray) -> np.ndarray:
    """计算马尔可夫链的平稳分布 (幂迭代法)."""
    K = P.shape[0]
    if K == 1:
        return np.array([1.0])
    pi = np.ones(K) / K
    for _ in range(1000):
        pi_new = pi @ P
        if np.max(np.abs(pi_new - pi)) < 1e-10:
            break
        pi = pi_new
    return pi


def emotion_entropy_rate(P: np.ndarray, pi: np.ndarray = None) -> float:
    """计算情绪熵率 H(P|π) = -Σ π_i Σ P_ij log P_ij.

    越低说明该股票的情绪转移越可预测。
    """
    if pi is None:
        pi = stationary_distribution(P)
    K = P.shape[0]
    H = 0.0
    for i in range(K):
        for j in range(K):
            if P[i, j] > 0:
                H -= pi[i] * P[i, j] * np.log2(P[i, j])
    return round(float(H), 4)


def extract_transition_features(P: np.ndarray, pi: np.ndarray, current_emotion: int) -> dict[str, float]:
    """从转移矩阵中提取 12 维特征.

    Args:
        P: 转移矩阵 (K, K)
        pi: 平稳分布 (K,)
        current_emotion: 当前表情标签

    Returns:
        12 维转移特征 dict
    """
    K = P.shape[0]
    tf = {}

    # 熵率
    tf["tr_entropy_rate"] = emotion_entropy_rate(P, pi)

    # 持续性 (对角线均值)
    tf["tr_persistence"] = round(float(np.mean(np.diag(P))), 4)

    # 反转概率 (非对角线均值)
    off_diag = [P[i, j] for i in range(K) for j in range(K) if i != j]
    tf["tr_reversal_prob"] = round(float(np.mean(off_diag)) if off_diag else 0.0, 4)

    # 平稳分布众数和熵
    tf["tr_stationary_mode"] = round(float(np.argmax(pi)), 4)
    pi_entropy = -sum(p * np.log2(p) for p in pi if p > 0)
    tf["tr_stationary_entropy"] = round(float(pi_entropy), 4)

    # 混合时间估计 (1 / |第二特征值|)
    eigvals = np.linalg.eigvals(P)
    sorted_ev = sorted([abs(v) for v in eigvals], reverse=True)
    if len(sorted_ev) > 1 and sorted_ev[1] < 1:
        mix_time = 1.0 / max(1 - sorted_ev[1], 0.01)
    else:
        mix_time = K * 10
    tf["tr_mix_time_est"] = round(min(mix_time, 100.0), 1)

    # 当前表情的最佳/最坏转移
    if 0 <= current_emotion < K:
        row = P[current_emotion]
        tf["tr_best_transition_prob"] = round(float(np.max(row)), 4)
        tf["tr_worst_transition_prob"] = round(float(np.min(row)), 4)
        tf["tr_best_emotion_id"] = round(float(np.argmax(row)), 1)
        tf["tr_worst_emotion_id"] = round(float(np.argmin(row)), 1)
    else:
        tf["tr_best_transition_prob"] = tf["tr_worst_transition_prob"] = 0.0
        tf["tr_best_emotion_id"] = tf["tr_worst_emotion_id"] = 0.0

    # 转移稳定性 (行熵的均值)
    row_entropies = []
    for i in range(K):
        h = -sum(P[i, j] * np.log2(P[i, j]) for j in range(K) if P[i, j] > 0)
        row_entropies.append(h)
    tf["tr_transition_stability"] = round(float(np.mean(row_entropies)), 4)

    # 明日最佳情况 (从当前表情出发的最有利转移)
    # 这是一个占位——实际值在后续填充
    tf["tr_tomorrow_best_ret"] = 0.0

    return tf


# ══════════════════════════════════════════════════════════════════════
# 标签命名 (给每种表情一个可读名称)
# ══════════════════════════════════════════════════════════════════════

def name_emotions(labels: np.ndarray, emotion_vectors: list[dict],
                  n_states: int, label_to_day_ret: dict[int, float]) -> dict[int, str]:
    """根据表情的特征为每种表情分配可读名称.

    基于: 日内收益 + 动作密度 + 尾盘方向 + VWAP位置
    """
    names = {}
    for j in range(n_states):
        mask = labels == j
        if mask.sum() == 0:
            names[j] = f"表情{j}"
            continue

        avg_ret = label_to_day_ret.get(j, 0.0)
        indices = [i for i in range(len(labels)) if labels[i] == j]

        # 计算该簇的平均特征
        avg_rise = np.mean([emotion_vectors[i].get("em_action_density", 0) or 0 for i in indices]) if indices else 0
        avg_close = np.mean([emotion_vectors[i].get("em_close_action", 0) or 0 for i in indices]) if indices else 0
        avg_vwap = np.mean([emotion_vectors[i].get("em_vwap_position", 0) or 0 for i in indices]) if indices else 0

        if avg_ret > 2 and avg_rise > 1.5:
            name = "贪婪冲锋"
        elif avg_ret < -2 and avg_rise > 1:
            name = "恐惧抛售"
        elif abs(avg_ret) < 1 and avg_rise < 0.5 and avg_vwap > -0.5:
            name = "冷静吸筹"
        elif avg_ret > 2 and avg_rise > 0.5 and avg_close < -1:
            name = "高潮见顶"
        elif abs(avg_ret) < 1.5 and avg_rise < 0.3:
            name = "冷漠横盘"
        elif avg_ret > 0 and avg_vwap > 0.5:
            name = "稳步上行"
        elif avg_ret < 0 and avg_vwap < -0.5:
            name = "阴跌下行"
        elif avg_ret > 1 and avg_close > 0.5:
            name = "尾盘拉升"
        elif avg_ret < -1 and avg_close < -0.5:
            name = "尾盘杀跌"
        else:
            name = f"混合型-{j}"

        names[j] = name

    return names
