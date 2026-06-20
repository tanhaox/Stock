"""Per-Stock DNA 特征工程 (146 维).

完全从 daily_kline 计算，不依赖 signal_history/analysis_scores/scan_results。

73 维日线截面 (纯K线版本)
15 维日内表情 (来自 emotion.py 聚类后)
15 维市场情绪 (来自 market_context.py)
12 维转移矩阵特征 (来自 emotion.py 马尔可夫链)
 8 维周期节律 (来自 cycle.py)
15 维历史统计 DNA (聚合统计)
 8 维交互特征 (周期位置 × 表情)

总计: 73 + 15 + 15 + 12 + 8 + 15 + 8 = 146 维
(注: 30/73 日线维在无外部数据源时恒为零 — 龙虎榜/新闻/板块, 属预期行为)
"""
import numpy as np
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("stock_dna.features")

# ══════════════════════════════════════════════════════════════════════
# 73 维日线特征名 (与 predictive_features.FEAT_NAMES 对齐)
# ══════════════════════════════════════════════════════════════════════
FEAT_NAMES_77 = [  # 保留变量名兼容性, 实际 73 维
    # A: 价格位置 (5)
    "ma5_dist", "ma20_dist", "ma60_dist", "dist_20d_high", "dist_20d_low",
    # B: 动量 (6)
    "chg_1d", "chg_5d", "chg_20d", "rsi_14", "macd_hist", "bb_position",
    # C: 成交量 (5)
    "vol_ratio_5d", "vol_ratio_20d", "amount_ma5_log", "turnover_rate", "vol_trend_5d",
    # D: 波动率 (4)
    "volatility_5d", "volatility_20d", "atr_pct_14", "amplitude_5d",
    # E: 形态 (6)
    "consecutive_up", "up_down_ratio_5d", "hl_range_5d", "gap_count_20d",
    "shadow_ratio", "bullish_engulf",
    # F: 信号历史 (4) — 无 signal_history 时填 0
    "signal_push_30d", "days_since_signal", "signal_win_rate", "sector_signal_count",
    # G1-G3: 交互 (14)
    "oversold_5d", "overbought_5d", "rsi_vol_ratio",
    "ma5_ma20_gap", "ma5_ma60_gap", "ma20_bias",
    "vol_trend_5d_g2", "vol_sign_5d",
    "vol_price_corr_5d", "vol_price_corr_20d",
    "sector_direction_up", "sector_direction_down", "sector_rank_top8", "sector_lifecycle_hot",
    # H+K: 龙虎榜 (17) — 历史数据填 0
    "tl_on_board", "tl_net_ratio", "tl_inst_ratio", "tl_concentration",
    "tl_appearances_5d", "tl_appearances_20d", "tl_net_trend_5d",
    "tl_oversold", "tl_breakout", "tl_volume_ratio",
    "tl_consecutive_up", "tl_inst_continuous", "tl_seat_quality",
    "tl_net_trend_10d", "tl_consecutive_days",
    "tl_avg_amount_ratio", "tl_inst_net_streak",
    # J: 新闻 (4) — 历史数据填 0
    "news_commodity_bear", "news_commodity_bull", "news_policy_bear", "news_policy_bull",
    # L: 多时间维度 (8)
    "weekly_momentum", "weekly_daily_divergence",
    "monthly_chg", "monthly_volatility",
    "monthly_ma_position", "sector_rank_20d",
    "sector_velocity_5d", "sector_vol_chg",
]

# ══════════════════════════════════════════════════════════════════════
# 新增 DNA 维度名称 (73 维)
# ══════════════════════════════════════════════════════════════════════
EMOTION_FEAT_NAMES = [
    # 15 维表情向量 (来自聚类前的原始向量, 不是聚类标签)
    "em_open_dir", "em_open_vol_ratio", "em_trend_persistence",
    "em_reversal_freq", "em_vwap_position", "em_am_pm_split",
    "em_vol_concentration", "em_vol_price_corr", "em_large_bar_bias",
    "em_close_action", "em_vwap_slope", "em_amplitude_pctile",
    "em_action_density", "em_lead_lag_min", "em_independent_pct",
]

MARKET_FEAT_NAMES = [
    # 15 维市场情绪
    "mkt_open_dir", "mkt_open_volatility", "mkt_intraday_path_type",
    "mkt_am_pm_split", "mkt_close_action", "mkt_vwap_position",
    "mkt_beta_intra", "mkt_lead_lag", "mkt_independent_ratio",
    "mkt_amplify_ratio", "mkt_contrarian_days_60d",
    "mkt_gap_vs_market", "mkt_reversal_vs_market",
    "mkt_v_reversal_freq", "mkt_tail_attack_sync",
]

TRANSITION_FEAT_NAMES = [
    # 12 维转移矩阵特征
    "tr_entropy_rate", "tr_persistence", "tr_reversal_prob",
    "tr_stationary_mode", "tr_stationary_entropy",
    "tr_mix_time_est", "tr_best_transition_prob",
    "tr_worst_transition_prob", "tr_best_emotion_id",
    "tr_worst_emotion_id", "tr_transition_stability",
    "tr_tomorrow_best_ret",
]

CYCLE_FEAT_NAMES = [
    # 8 维周期节律
    "cy_is_locked", "cy_lockup_day", "cy_position_pct",
    "cy_lockup_remaining_est", "cy_avg_lockup_days",
    "cy_cv_lockup", "cy_breakout_prob", "cy_expected_ret_if_breakout",
]

HISTORY_FEAT_NAMES = [
    # 15 维历史统计 DNA
    "hi_avg_ret_t2", "hi_avg_ret_t5", "hi_avg_ret_t10", "hi_avg_ret_t20",
    "hi_winrate_t2", "hi_winrate_t5", "hi_winrate_t10", "hi_winrate_t20",
    "hi_ret_volatility", "hi_best_horizon",
    "hi_crash_resilience", "hi_rally_capture",
    "hi_deception_rate", "hi_consistency", "hi_extreme_tail",
]

INTERACT_FEAT_NAMES = [
    # 8 维交互特征 (周期位置 × 情感)
    "ix_lockup_emotion_cross_0", "ix_lockup_emotion_cross_1",
    "ix_lockup_emotion_cross_2", "ix_lockup_emotion_cross_3",
    "ix_breakout_emotion_cross_0", "ix_breakout_emotion_cross_1",
    "ix_breakout_emotion_cross_2", "ix_breakout_emotion_cross_3",
]

ALL_FEAT_NAMES = (
    FEAT_NAMES_77 + EMOTION_FEAT_NAMES + MARKET_FEAT_NAMES +
    TRANSITION_FEAT_NAMES + CYCLE_FEAT_NAMES + HISTORY_FEAT_NAMES +
    INTERACT_FEAT_NAMES
)


# ══════════════════════════════════════════════════════════════════════
# 77 维日线特征计算 (纯 K 线版本, 不依赖数据库)
# ══════════════════════════════════════════════════════════════════════

def compute_daily_features_77(kline_rows: list[dict], target_idx: int = -1) -> dict[str, float]:
    """从该股票的K线列表计算 77 维日线特征。

    Args:
        kline_rows: 按日期升序的K线记录列表, 每行 {open,high,low,close,volume,trade_date}
        target_idx: 目标日期的索引, -1 表示最后一天

    Returns:
        77 维特征 dict, 数据不足时返回全0
    """
    n = len(kline_rows)
    if n < 25:
        return {k: 0.0 for k in FEAT_NAMES_77}

    i = target_idx if target_idx >= 0 else n + target_idx
    if i < 25:
        return {k: 0.0 for k in FEAT_NAMES_77}

    closes = np.array([r["close"] for r in kline_rows], dtype=np.float64)
    opens = np.array([r["open"] for r in kline_rows], dtype=np.float64)
    highs = np.array([r["high"] for r in kline_rows], dtype=np.float64)
    lows = np.array([r["low"] for r in kline_rows], dtype=np.float64)
    volumes = np.array([r["volume"] for r in kline_rows], dtype=np.float64)

    close = closes[i]
    prev_close = closes[i - 1] if i > 0 else close
    f = {}

    # ── A: 价格位置 (5) ──
    ma5 = float(np.mean(closes[i - 4:i + 1])) if i >= 4 else close
    ma20 = float(np.mean(closes[i - 19:i + 1])) if i >= 19 else ma5
    ma60 = float(np.mean(closes[i - 59:i + 1])) if i >= 59 else ma20
    h20 = float(np.max(highs[max(0, i - 19):i + 1]))
    l20 = float(np.min(lows[max(0, i - 19):i + 1]))

    f["ma5_dist"] = round((close - ma5) / max(ma5, 0.01) * 100, 4)
    f["ma20_dist"] = round((close - ma20) / max(ma20, 0.01) * 100, 4)
    f["ma60_dist"] = round((close - ma60) / max(ma60, 0.01) * 100, 4)
    f["dist_20d_high"] = round((close - h20) / max(h20, 0.01) * 100, 4)
    f["dist_20d_low"] = round((close - l20) / max(l20, 0.01) * 100, 4)

    # ── B: 动量 (6) ──
    f["chg_1d"] = round((close - prev_close) / max(prev_close, 0.01) * 100, 4)
    c5 = closes[i - 5] if i >= 5 else closes[0]
    c20 = closes[i - 20] if i >= 20 else closes[0]
    f["chg_5d"] = round((close - c5) / max(c5, 0.01) * 100, 4)
    f["chg_20d"] = round((close - c20) / max(c20, 0.01) * 100, 4)

    # RSI 14
    if i >= 14:
        gains = [max(0, closes[j] - closes[j - 1]) for j in range(i - 13, i + 1)]
        losses = [max(0, closes[j - 1] - closes[j]) for j in range(i - 13, i + 1)]
        avg_gain = float(np.mean(gains)) if gains else 0
        avg_loss = float(np.mean(losses)) if losses else 1e-9
        rs = avg_gain / max(avg_loss, 1e-9)
        f["rsi_14"] = round(100 - 100 / (1 + rs), 2)
    else:
        f["rsi_14"] = 50.0

    # MACD hist (12-26-9)
    if i >= 35:
        ema12 = _ema(closes[:i + 1], 12)
        ema26 = _ema(closes[:i + 1], 26)
        dif = ema12 - ema26
        # DEA: 9-period EMA of DIF, using last 15 DIF values
        dif_vals = []
        for j in range(max(26, i - 14), i + 1):
            e12 = _ema(closes[:j + 1], 12)
            e26 = _ema(closes[:j + 1], 26)
            dif_vals.append(e12 - e26)
        dea = _ema(np.array(dif_vals), 9)
        f["macd_hist"] = round(float(2 * (dif - dea)), 4)
    else:
        f["macd_hist"] = 0.0

    # BB 位置
    if i >= 20:
        bb_ma20 = float(np.mean(closes[i - 19:i + 1]))
        bb_std = float(np.std(closes[i - 19:i + 1]))
        bb_upper = bb_ma20 + 2 * bb_std
        bb_lower = bb_ma20 - 2 * bb_std
        f["bb_position"] = round((close - bb_lower) / max(bb_upper - bb_lower, 0.01), 4)
    else:
        f["bb_position"] = 0.5

    # ── C: 成交量 (5) ──
    vol5 = float(np.mean(volumes[max(0, i - 4):i + 1]))
    vol20 = float(np.mean(volumes[max(0, i - 19):i + 1]))
    f["vol_ratio_5d"] = round(volumes[i] / max(vol5, 1e-9), 4)
    f["vol_ratio_20d"] = round(volumes[i] / max(vol20, 1e-9), 4)
    amt5 = float(np.mean(volumes[max(0, i - 4):i + 1] * closes[max(0, i - 4):i + 1]))
    f["amount_ma5_log"] = round(np.log(max(volumes[i] * close, 0.01) / max(amt5, 0.01)), 4)
    t20 = float(np.mean(volumes[max(0, i - 19):i + 1] / 1e8))
    f["turnover_rate"] = round(volumes[i] / max(t20 * 1e8, 1e-9) * 100, 4)
    f["vol_trend_5d"] = round((volumes[i] - vol20) / max(vol20, 1e-9), 4)

    # ── D: 波动率 (4) ──
    if i >= 5:
        rets5 = np.diff(closes[i - 4:i + 1]) / closes[i - 4:i] * 100
        f["volatility_5d"] = round(float(np.std(rets5)), 4)
    else:
        f["volatility_5d"] = 0.0
    if i >= 20:
        rets20 = np.diff(closes[i - 19:i + 1]) / closes[i - 19:i] * 100
        f["volatility_20d"] = round(float(np.std(rets20)), 4)
    else:
        f["volatility_20d"] = 0.0
    if i >= 14:
        trs = []
        for j in range(i - 13, i + 1):
            h, l = highs[j], lows[j]
            pc = closes[j - 1]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        f["atr_pct_14"] = round(float(np.mean(trs)) / max(close, 0.01) * 100, 4)
    else:
        f["atr_pct_14"] = 0.0
    f["amplitude_5d"] = round((float(np.max(highs[i - 4:i + 1])) - float(np.min(lows[i - 4:i + 1]))) / max(closes[i - 5], 0.01) * 100, 4) if i >= 5 else 0.0

    # ── E: 形态 (6) ──
    cons_up = 0
    for j in range(i, max(0, i - 6), -1):
        if closes[j] > closes[j - 1]: cons_up += 1
        else: break
    f["consecutive_up"] = float(cons_up)
    up_cnt = sum(1 for j in range(max(0, i - 4), i + 1) if closes[j] > closes[j - 1])
    f["up_down_ratio_5d"] = round(up_cnt / 5.0, 4)
    f["hl_range_5d"] = round((float(np.max(highs[i - 4:i + 1])) - float(np.min(lows[i - 4:i + 1]))) / max(float(np.mean(closes[i - 4:i + 1])), 0.01), 4)
    f["gap_count_20d"] = float(sum(1 for j in range(max(0, i - 19), i + 1) if opens[j] > closes[j - 1] * 1.01 or opens[j] < closes[j - 1] * 0.99))
    body = abs(close - opens[i])
    shadow = highs[i] - lows[i]
    f["shadow_ratio"] = round((shadow - body) / max(shadow, 0.01), 4) if shadow > 0 else 0.0
    f["bullish_engulf"] = 1.0 if (i >= 1 and close > opens[i] and closes[i - 1] < opens[i - 1] and close > opens[i - 1] and opens[i] < closes[i - 1]) else 0.0

    # ── F: 信号历史 (4) — 纯K线版本填 0 ──
    f["signal_push_30d"] = 0.0
    f["days_since_signal"] = -1.0
    f["signal_win_rate"] = 0.5
    f["sector_signal_count"] = 0.0

    # ── G1-G3: 交互 (14) ──
    f["oversold_5d"] = 1.0 if f.get("chg_5d", 0) < -5 and f.get("rsi_14", 50) < 30 else 0.0
    f["overbought_5d"] = 1.0 if f.get("chg_5d", 0) > 5 and f.get("rsi_14", 50) > 70 else 0.0
    f["rsi_vol_ratio"] = round(f.get("rsi_14", 50) * f.get("vol_ratio_20d", 1) / 50, 4)
    f["ma5_ma20_gap"] = round((ma5 - ma20) / max(ma20, 0.01) * 100, 4)
    f["ma5_ma60_gap"] = round((ma5 - ma60) / max(ma60, 0.01) * 100, 4)
    f["ma20_bias"] = round((ma20 - ma60) / max(ma60, 0.01) * 100, 4)
    f["vol_trend_5d_g2"] = f["vol_trend_5d"]
    f["vol_sign_5d"] = 1.0 if f.get("chg_5d", 0) > 0 and f.get("vol_trend_5d", 0) > 0 else (-1.0 if f.get("chg_5d", 0) < 0 and f.get("vol_trend_5d", 0) > 0 else 0.0)
    if i >= 5:
        rets = np.diff(closes[i - 4:i + 1]) / closes[i - 4:i] * 100
        vols_5 = volumes[i - 4:i]  # same length as rets (4)
        vpc5 = float(np.corrcoef(rets, vols_5)[0, 1]) if len(rets) > 1 and np.std(vols_5) > 0 else 0.0
        f["vol_price_corr_5d"] = round(vpc5, 4)
    else:
        f["vol_price_corr_5d"] = 0.0
    if i >= 20:
        rets20c = np.diff(closes[i - 19:i + 1]) / closes[i - 19:i] * 100
        vols_20c = volumes[i - 19:i]  # same length (19)
        vpc20 = float(np.corrcoef(rets20c, vols_20c)[0, 1]) if len(rets20c) > 1 and np.std(vols_20c) > 0 else 0.0
        f["vol_price_corr_20d"] = round(vpc20, 4)
    else:
        f["vol_price_corr_20d"] = 0.0
    f["sector_direction_up"] = 0.0
    f["sector_direction_down"] = 0.0
    f["sector_rank_top8"] = 0.0
    f["sector_lifecycle_hot"] = 0.0

    # ── H+K: 龙虎榜 (17) — 全 0 ──
    for fn in FEAT_NAMES_77[48:65]:
        f[fn] = 0.0

    # ── J: 新闻 (4) — 全 0 ──
    for fn in FEAT_NAMES_77[65:69]:
        f[fn] = 0.0

    # ── L: 多时间维度 (8) ──
    f["weekly_momentum"] = round(f.get("chg_5d", 0), 4)
    f["weekly_daily_divergence"] = round(f.get("chg_5d", 0) - f.get("chg_1d", 0), 4)
    if i >= 20:
        f["monthly_chg"] = round((close - closes[i - 20]) / max(closes[i - 20], 0.01) * 100, 4)
        f["monthly_volatility"] = round(float(np.std(np.diff(closes[i - 19:i + 1]) / closes[i - 19:i] * 100)), 4)
    else:
        f["monthly_chg"] = 0.0
        f["monthly_volatility"] = 0.0
    f["monthly_ma_position"] = round((close - ma20) / max(ma20, 0.01) * 100, 4)
    f["sector_rank_20d"] = 0.0
    f["sector_velocity_5d"] = 0.0
    f["sector_vol_chg"] = 0.0

    return f


def _ema(prices: np.ndarray, span: int) -> float:
    """计算 EMA 最新值."""
    if len(prices) < span:
        return float(np.mean(prices))
    alpha = 2.0 / (span + 1)
    ema = float(np.mean(prices[:span]))
    for p in prices[span:]:
        ema = alpha * p + (1 - alpha) * ema
    return ema


# ══════════════════════════════════════════════════════════════════════
# 特征向量化
# ══════════════════════════════════════════════════════════════════════

def features_to_array(features_dict: dict[str, float], feat_names: list[str] = ALL_FEAT_NAMES) -> np.ndarray:
    """将特征 dict 转为 float32 np.array (用于 XGBoost)."""
    arr = np.zeros(len(feat_names), dtype=np.float32)
    for j, name in enumerate(feat_names):
        arr[j] = float(features_dict.get(name, 0.0) or 0.0)
    return arr


def check_feature_quality(features_dict: dict[str, float]) -> dict:
    """检查特征质量: NaN/Inf/覆盖率."""
    total = len(ALL_FEAT_NAMES)
    valid = sum(1 for k in ALL_FEAT_NAMES if k in features_dict and not np.isnan(features_dict[k]) and not np.isinf(features_dict[k]))
    nonzero = sum(1 for k in ALL_FEAT_NAMES if k in features_dict and abs(features_dict.get(k, 0)) > 1e-9)
    return {
        "total_features": total,
        "valid_count": valid,
        "nonzero_count": nonzero,
        "coverage": round(valid / total, 4),
        "nonzero_rate": round(nonzero / total, 4),
        "has_nan": any(np.isnan(features_dict.get(k, 0)) for k in ALL_FEAT_NAMES),
    }
