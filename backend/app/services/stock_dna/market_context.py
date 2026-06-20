"""大盘分时联动特征 (15 维).

从 sector_min_kline (000001.SH) 和个股 min_kline 的联合分析中提取：
  - 大盘日内情绪 (6维)
  - 个股-大盘联动 (5维)
  - 个股-大盘分时事件 (4维)

核心函数:
  extract_market_emotion() — 大盘自身的日内情绪
  extract_linkage_features() — 个股 vs 大盘联动指标
"""
import numpy as np
import logging
from app.utils.numpy_utils import safe_corrcoef_or_half, safe_float
from typing import Optional

logger = logging.getLogger("stock_dna.market_context")


def extract_market_emotion(market_bars: list[dict]) -> Optional[dict[str, float]]:
    """从大盘 5 分钟 K 线提取 6 维市场情绪特征。

    Args:
        market_bars: 000001.SH 的 5 分钟 K 线, 至少 30 根

    Returns:
        6 维市场情绪 dict, 数据不足返回 None
    """
    n = len(market_bars)
    if n < 30:
        return None

    closes = np.array([b["close"] for b in market_bars], dtype=np.float64)
    opens = np.array([b["open"] for b in market_bars], dtype=np.float64)
    highs = np.array([b["high"] for b in market_bars], dtype=np.float64)
    lows = np.array([b["low"] for b in market_bars], dtype=np.float64)

    day_open = opens[0]
    day_close = closes[-1]

    m = {}

    # 1. 开盘 30 分方向
    open30_ret = (closes[5] - opens[0]) / max(opens[0], 0.01) * 100 if n >= 6 else 0
    m["mkt_open_dir"] = round(open30_ret, 4)

    # 2. 开盘 30 分振幅
    open30_high = float(np.max(highs[:6])) if n >= 6 else day_open
    open30_low = float(np.min(lows[:6])) if n >= 6 else day_open
    m["mkt_open_volatility"] = round((open30_high - open30_low) / max(day_open, 0.01) * 100, 4)

    # 3. 日内路径类型 (简化: 单边/反转/震荡)
    mid = n // 2
    am_ret = (closes[mid - 1] - opens[0]) / max(opens[0], 0.01) * 100 if mid > 0 else 0
    pm_ret = (closes[-1] - closes[mid]) / max(closes[mid], 0.01) * 100 if mid < n else 0
    if abs(am_ret) > 0.5 and am_ret * pm_ret > 0:
        path_type = 1  # 单边
    elif abs(am_ret) > 0.5 and am_ret * pm_ret < 0:
        path_type = -1  # 反转
    else:
        path_type = 0  # 震荡
    m["mkt_intraday_path_type"] = float(path_type)

    # 4. 上下午势能差
    m["mkt_am_pm_split"] = round(am_ret - pm_ret, 4)

    # 5. 尾盘动作 (最后 6 根)
    tail_ret = (closes[-1] - closes[-6]) / max(closes[-6], 0.01) * 100 if n >= 6 else 0
    m["mkt_close_action"] = round(tail_ret, 4)

    # 6. VWAP 位置
    vols = np.array([b.get("volume", b.get("vol", 0)) for b in market_bars], dtype=np.float64)
    vwap = float(np.sum(closes * vols) / max(np.sum(vols), 1e-9))
    m["mkt_vwap_position"] = round((day_close - vwap) / max(vwap, 0.01) * 100, 4)

    return m


def extract_linkage_features(stock_bars: list[dict], market_bars: list[dict],
                              hist_linkage: list[dict] = None) -> dict[str, float]:
    """计算个股-大盘联动特征 (9 维 = 5 联动 + 4 事件)。

    Args:
        stock_bars: 个股 5 分钟 K 线
        market_bars: 大盘 5 分钟 K 线
        hist_linkage: 历史联动数据 [{date, beta, corr, independent_pct}, ...] (可选)

    Returns:
        9 维联动特征 dict
    """
    n = min(len(stock_bars), len(market_bars))
    lf = {}

    if n < 20:
        return {k: 0.0 for k in ["mkt_beta_intra", "mkt_lead_lag", "mkt_independent_ratio",
                                  "mkt_amplify_ratio", "mkt_contrarian_days_60d",
                                  "mkt_gap_vs_market", "mkt_reversal_vs_market",
                                  "mkt_v_reversal_freq", "mkt_tail_attack_sync"]}

    s_rets = np.array([(stock_bars[i]["close"] / max(stock_bars[i]["open"], 0.01) - 1) * 100 for i in range(n)])
    m_rets = np.array([(market_bars[i]["close"] / max(market_bars[i]["open"], 0.01) - 1) * 100 for i in range(n)])

    # 5.1 日内 β
    if np.std(m_rets) > 0:
        beta = float(np.cov(s_rets, m_rets)[0, 1] / np.var(m_rets))
    else:
        beta = 0.0
    lf["mkt_beta_intra"] = round(max(min(beta, 5.0), -5.0), 4)

    # 5.2 领先/滞后 (交叉相关)
    max_corr, best_lag = 0, 0
    for lag in range(-6, 7):
        if lag < 0 and len(s_rets[-lag:]) > 5:
            c = float(np.corrcoef(s_rets[-lag:], m_rets[:lag])[0, 1])
        elif lag > 0 and len(m_rets[lag:]) > 5:
            c = float(np.corrcoef(s_rets[:len(m_rets) - lag], m_rets[lag:])[0, 1])
        else:
            c = float(np.corrcoef(s_rets, m_rets)[0, 1]) if n > 5 else 0
        if not np.isnan(c) and abs(c) > abs(max_corr):
            max_corr, best_lag = c, lag
    lf["mkt_lead_lag"] = round(float(best_lag), 1)

    # 5.3 独立波动占比
    corr0 = float(np.corrcoef(s_rets, m_rets)[0, 1]) if n > 5 and np.std(s_rets) > 0 and np.std(m_rets) > 0 else 0
    lf["mkt_independent_ratio"] = round(1.0 - corr0 ** 2, 4) if not np.isnan(corr0) else 0.5  # keep isnan guard — corr0 may be valid zero

    # 5.4 放大系数
    s_total_ret = (stock_bars[-1]["close"] / max(stock_bars[0]["open"], 0.01) - 1) * 100
    m_total_ret = (market_bars[-1]["close"] / max(market_bars[0]["open"], 0.01) - 1) * 100
    lf["mkt_amplify_ratio"] = round(s_total_ret / max(abs(m_total_ret), 0.1), 2)

    # 5.5 近 60 天逆势比例
    if hist_linkage:
        contrarian = sum(1 for h in hist_linkage if h.get("beta", 0) < 0.3 or
                        (h.get("independent_pct", 0) or 0) > 0.7)
        lf["mkt_contrarian_days_60d"] = round(contrarian / max(len(hist_linkage), 1), 4)
    else:
        lf["mkt_contrarian_days_60d"] = 0.0

    # 5.6 独立缺口 (个股跳空 - 大盘跳空)
    if n >= 2 and len(stock_bars) >= 2:
        s_gap = (stock_bars[1]["open"] / max(stock_bars[0]["close"], 0.01) - 1) * 100 if stock_bars[0].get("close", 0) > 0 else 0
        m_gap = (market_bars[1]["open"] / max(market_bars[0]["close"], 0.01) - 1) * 100 if market_bars[0].get("close", 0) > 0 else 0
        lf["mkt_gap_vs_market"] = round(s_gap - m_gap, 4)
    else:
        lf["mkt_gap_vs_market"] = 0.0

    # 5.7 反转独立性
    mid = n // 2
    s_rev = 1 if (s_rets[0] > 0 and s_rets[-1] < 0) or (s_rets[0] < 0 and s_rets[-1] > 0) else 0
    m_rev = 1 if (m_rets[0] > 0 and m_rets[-1] < 0) or (m_rets[0] < 0 and m_rets[-1] > 0) else 0
    lf["mkt_reversal_vs_market"] = 1.0 if s_rev == 1 and m_rev == 0 else (0.0 if s_rev == 0 else -1.0)

    # 5.8 V 型反转跟随
    v_rev_count = 0
    for i in range(1, n - 1):
        if (m_rets[i] - m_rets[i - 1]) * (m_rets[i + 1] - m_rets[i]) < 0:
            if (s_rets[i] - s_rets[i - 1]) * (s_rets[i + 1] - s_rets[i]) < 0:
                v_rev_count += 1
    lf["mkt_v_reversal_freq"] = round(v_rev_count / max(n - 2, 1), 4)

    # 5.9 尾盘同步
    if n >= 6:
        s_tail = (stock_bars[-1]["close"] / max(stock_bars[-6]["open"], 0.01) - 1) * 100
        m_tail = (market_bars[-1]["close"] / max(market_bars[-6]["open"], 0.01) - 1) * 100
        lf["mkt_tail_attack_sync"] = 1.0 if s_tail * m_tail > 0 else 0.0
    else:
        lf["mkt_tail_attack_sync"] = 0.0

    return lf
