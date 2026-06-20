"""Predictive features — 从 daily_kline 构建 30+ 维特征向量 (v4.8).

用于 XGBoost 训练预测 T+5 收益, 替代手工 composite_score.
"""
import numpy as np
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.utils.numpy_utils import sanitize_array

logger = logging.getLogger(__name__)

# 特征名列表 — 训练/推理时必须保持顺序一致
FEAT_NAMES = [
    # A. 价格位置 (5维)
    "price_vs_ma5", "price_vs_ma20", "price_vs_ma60",
    "price_vs_20d_high", "price_vs_20d_low",
    # B. 动量 (6维)
    "chg_1d", "chg_5d", "chg_20d",
    "rsi_14", "macd_hist", "bb_position",
    # C. 成交量 (5维)
    "vol_ratio_5v20", "vol_ratio_1v20", "vol_trend",
    "amount_ma5_log", "turnover_rate",
    # D. 波动率 (4维)
    "volatility_5d", "volatility_20d", "atr_pct", "amplitude_5d",
    # E. 形态 (6维)
    "consecutive_up", "up_down_ratio_10d", "high_low_range_20d",
    "gap_count_5d", "shadow_ratio", "bullish_engulf",
    # F. 信号历史 (4维)
    "push_count_30d", "days_since_last_signal",
    "same_stock_win_rate", "same_sector_signal_count",
    # G. 交互特征 — Phase 32a (14维: 11保留 + 3真实板块)
    # G1: 超卖+量确认 (3维)
    "x_oversold_vol", "x_overbought_vol", "x_rsi_vol_interact",
    # G2: 均线压力组合 (3维)
    "x_ma5_ma20_gap", "x_price_ma20_bias", "x_price_ma60_bias",
    # G4: 波动率+趋势方向 (2维)
    "x_vol_trend_sign", "x_vol_chg_ratio_short",
    # G6: 量价配合度 (3维)
    "x_vol_price_corr_5d", "x_up_vol_ratio", "x_buy_pressure",
    # G7: 真实板块上下文 (3维) — 由 _preload_sector_features() 批量缓存
    "x_real_sector_5d", "x_real_alpha", "x_real_rank_5d",
    # H: 龙虎榜因子 — Phase 33 (11维) — 由 _preload_toplist() 批量缓存
    # G8: 上榜质量 (4维)
    "tl_on_toplist", "tl_net_buy_ratio", "tl_inst_ratio", "tl_buy_concentration",
    # G9: 上榜持续性 (4维)
    "tl_appearances_5d", "tl_appearances_20d", "tl_avg_net_5d", "tl_net_trend",
    # G10: 上榜交互 (3维)
    "tl_oversold", "tl_breakout", "tl_turnover_signal",
    # K: 龙虎榜深度特征 — Phase 60 (6维)
    "tl_inst_continuous", "tl_seat_quality",
    "tl_net_trend_10d", "tl_consecutive_days",
    "tl_avg_amount_ratio", "tl_inst_net_streak",
    # I: 板块轮动特征 — Phase 34 (4维) — 由 _preload_sector_features() 批量缓存
    "sector_direction_up", "sector_direction_down",
    "sector_rank_top8", "sector_lifecycle_hot",
    # J: 新闻暴露特征 — Phase 51 (4维) — 由 _preload_news() 批量缓存
    "news_commodity_bear", "news_commodity_bull",
    "news_policy_bear", "news_policy_bull",
    # L: 多时间维度特征 — Phase 69 (8维)
    # L1: 周线动量 (2维) — 由 _preload_weekly_features() 批量缓存
    "weekly_tg_momentum",
    "weekly_daily_divergence",
    # L2: 月线趋势 (3维) — 从 daily_kline 聚合计算
    "monthly_chg",
    "monthly_volatility",
    "monthly_ma_position",
    # L3: 板块轮动速度 (3维) — 由 _preload_sector_features() 批量缓存
    "sector_rank_20d",
    "sector_velocity",
    "sector_vol_chg",
]


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均."""
    if len(series) == 0:
        return np.array([])
    alpha = 2 / (period + 1)
    result = np.zeros_like(series)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


# Batch kline cache — Phase 44b: 批量预加载避免逐股 SQL
_KLINES_BATCH: dict[str, tuple] = {}

async def _preload_klines_batch(session, symbols: list[str], scan_date) -> None:
    """一次性拉取所有 symbol 的 kline 数据到内存缓存."""
    global _KLINES_BATCH
    _KLINES_BATCH.clear()
    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)
    cut = scan_date - timedelta(days=200)
    r = await session.execute(text(
        "SELECT ts_code, trade_date, open, high, low, close, volume, amount "
        "FROM daily_kline WHERE ts_code = ANY(:syms) AND trade_date >= :cut "
        "ORDER BY ts_code, trade_date"
    ), {"syms": symbols, "cut": cut})
    by_sym: dict[str, tuple] = {}
    for row in r.fetchall():
        code = row[0]
        if code not in by_sym:
            by_sym[code] = ([], [], [], [], [], [], [])
        # row: ts_code, trade_date, open, high, low, close, volume, amount
        # arrays: td, o, h, l, c, v, a
        for i, arr in enumerate(by_sym[code]):
            val = row[i + 1]
            arr.append(float(val or 0) if i > 0 and val is not None else val)
    for code, (td, o, h, l, c, v, a) in by_sym.items():
        _KLINES_BATCH[code] = (
            np.array(c, dtype=np.float64), np.array(o, dtype=np.float64),
            np.array(h, dtype=np.float64), np.array(l, dtype=np.float64),
            np.array(v, dtype=np.float64), np.array(a, dtype=np.float64),
        )
    logger.debug(f"Preloaded klines for {len(_KLINES_BATCH)} symbols")


def _build_features_from_arrays(closes, opens, highs, lows, volumes, amounts,
                                 symbol, scan_date) -> dict:
    """Pure-numpy feature builder — 与 build_features 完全相同的逻辑，不从 DB 读."""
    feats: dict[str, float] = {f: 0.0 for f in FEAT_NAMES}
    n = len(closes)
    close = closes[-1]
    if close <= 0 or n < 20:
        return feats

    ma5 = np.mean(closes[-5:]) if n >= 5 else close
    ma20 = np.mean(closes[-20:]) if n >= 20 else close
    ma60 = np.mean(closes[-60:]) if n >= 60 else close
    high20 = np.max(highs[-20:]) if n >= 20 else close
    low20 = np.min(lows[-20:]) if n >= 20 else close

    feats["price_vs_ma5"] = round((close - ma5) / ma5 * 100, 4) if ma5 > 0 else 0
    feats["price_vs_ma20"] = round((close - ma20) / ma20 * 100, 4) if ma20 > 0 else 0
    feats["price_vs_ma60"] = round((close - ma60) / ma60 * 100, 4) if ma60 > 0 else 0
    feats["price_vs_20d_high"] = round(close / high20, 4) if high20 > 0 else 1
    feats["price_vs_20d_low"] = round(close / low20, 4) if low20 > 0 else 1

    feats["chg_1d"] = round(float((closes[-1] / closes[-2] - 1) * 100), 4) if n >= 2 and closes[-2] > 0 else 0
    feats["chg_5d"] = round(float((closes[-1] / closes[-5] - 1) * 100), 4) if n >= 5 and closes[-5] > 0 else 0
    feats["chg_20d"] = round(float((closes[-1] / closes[-20] - 1) * 100), 4) if n >= 20 and closes[-20] > 0 else 0
    feats["rsi_14"] = round(_rsi(closes), 2)

    ema12 = _ema(closes, 12); ema26 = _ema(closes, 26)
    diff = ema12 - ema26; dea = _ema(diff, 9)
    feats["macd_hist"] = round(float(2 * (diff[-1] - dea[-1])), 4) if len(diff) > 0 else 0
    std20 = float(np.std(closes[-20:])) if n >= 20 else 0.01
    feats["bb_position"] = round(float((close - ma20) / (2 * std20)), 4) if std20 > 0 else 0

    vol_ma5 = float(np.mean(volumes[-5:])) if n >= 5 else volumes[-1]
    vol_ma20 = float(np.mean(volumes[-20:])) if n >= 20 else volumes[-1]
    feats["vol_ratio_5v20"] = round(float(vol_ma5 / vol_ma20), 4) if vol_ma20 > 0 else 1
    feats["vol_ratio_1v20"] = round(float(volumes[-1] / vol_ma20), 4) if vol_ma20 > 0 else 1
    if n >= 20:
        recent5 = float(np.mean(volumes[-5:])); prior15 = float(np.mean(volumes[-20:-5]))
        feats["vol_trend"] = round(float((recent5 / prior15 - 1) * 100), 2) if prior15 > 0 else 0
    feats["amount_ma5_log"] = round(float(np.log1p(np.mean(amounts[-5:]))), 4) if n >= 5 else 0
    if n >= 20 and close > 0:
        feats["turnover_rate"] = round(float(np.mean(amounts[-20:]) / (close * 1e8) * 100), 4)

    if n >= 6:
        rets_5d = np.diff(closes[-6:]) / closes[-6:-1]
        rets_5d = rets_5d[np.isfinite(rets_5d)]
        feats["volatility_5d"] = round(float(np.std(rets_5d) * 100), 4) if len(rets_5d) > 0 else 0
    if n >= 21:
        rets_20d = np.diff(closes[-21:]) / closes[-21:-1]
        rets_20d = rets_20d[np.isfinite(rets_20d)]
        feats["volatility_20d"] = round(float(np.std(rets_20d) * 100), 4) if len(rets_20d) > 0 else 0
    if n >= 15:
        trs = []
        for i in range(max(1, n - 14), n):
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - closes[i - 1]) if closes[i - 1] > 0 else 0,
                     abs(lows[i] - closes[i - 1]) if closes[i - 1] > 0 else 0)
            trs.append(tr)
        atr14 = float(np.mean(trs)) if trs else 0
        feats["atr_pct"] = round(float(atr14 / close * 100), 4) if close > 0 else 0
    if n >= 5:
        amps = [(highs[i] - lows[i]) / closes[i] * 100 for i in range(n - 5, n) if closes[i] > 0]
        feats["amplitude_5d"] = round(float(np.mean(amps)), 4) if amps else 0

    up_days = 0
    for i in range(n - 1, max(0, n - 11), -1):
        if closes[i] > closes[i - 1]: up_days += 1
        else: break
    feats["consecutive_up"] = float(up_days)
    ups = sum(1 for i in range(max(0, n - 10), n) if closes[i] > opens[i])
    downs = sum(1 for i in range(max(0, n - 10), n) if closes[i] < opens[i])
    feats["up_down_ratio_10d"] = round(float(ups / max(downs, 1)), 4)
    feats["high_low_range_20d"] = round(float((high20 - low20) / low20 * 100), 2) if low20 > 0 else 0
    gaps = 0
    for i in range(max(0, n - 5), n):
        if i > 0 and closes[i - 1] > 0:
            if lows[i] > highs[i - 1]: gaps += 1
            elif highs[i] < lows[i - 1]: gaps += 1
    feats["gap_count_5d"] = float(gaps)
    if n >= 5:
        upper_shadows = [(highs[i] - max(opens[i], closes[i])) / max(highs[i] - lows[i], 0.001) for i in range(n - 5, n)]
        lower_shadows = [(min(opens[i], closes[i]) - lows[i]) / max(highs[i] - lows[i], 0.001) for i in range(n - 5, n)]
        feats["shadow_ratio"] = round(float(np.mean(upper_shadows) / max(np.mean(lower_shadows), 0.001)), 4)
    engulf = 0
    for i in range(max(1, n - 5), n):
        if (closes[i] > opens[i] and opens[i - 1] > closes[i - 1]
            and closes[i] > opens[i - 1] and opens[i] < closes[i - 1]):
            engulf = 1; break
    feats["bullish_engulf"] = float(engulf)

    # Interaction features (Phase 32/33)
    rsi = feats["rsi_14"]; vol5v20 = feats.get("vol_ratio_5v20", 1.0) or 1.0
    vol5d = feats.get("volatility_5d", 0) or 0; vol20d = feats.get("volatility_20d", 0) or 0
    chg5 = feats["chg_5d"]; pv_ma20 = feats.get("price_vs_ma20", 0) or 0; pv_ma60 = feats.get("price_vs_ma60", 0) or 0
    feats["x_oversold_vol"] = round((1.0 if rsi < 35 else 0.0) * vol5v20, 4)
    feats["x_overbought_vol"] = round((1.0 if rsi > 70 else 0.0) * vol5v20, 4)
    feats["x_rsi_vol_interact"] = round((50.0 - rsi) * vol5v20 / 100.0, 4)
    try:
        ma5v = float(np.mean(closes[-5:])) if n >= 5 else close
        feats["x_ma5_ma20_gap"] = round((ma5v - ma20) / max(ma20, 0.01) * 100, 4)
    except Exception: feats["x_ma5_ma20_gap"] = 0.0
    feats["x_price_ma20_bias"] = round(pv_ma20 * vol5v20, 4)
    feats["x_price_ma60_bias"] = round(pv_ma60 * vol5v20, 4)
    sign_chg5 = 1.0 if chg5 > 0 else (-1.0 if chg5 < 0 else 0.0)
    feats["x_vol_trend_sign"] = round(vol5d * sign_chg5, 4)
    feats["x_vol_chg_ratio_short"] = round(vol5d / max(vol20d, 0.01), 4)
    if n >= 5:
        recent_c = closes[-5:]; recent_v = volumes[-5:]
        c_deltas = np.diff(recent_c); v_deltas = np.diff(recent_v)
        if len(c_deltas) >= 3 and np.std(c_deltas) > 0 and np.std(v_deltas) > 0:
            corr = np.corrcoef(c_deltas, v_deltas)[0, 1]
            feats["x_vol_price_corr_5d"] = round(float(corr) if not np.isnan(corr) else 0.0, 4)
        up_vol = sum(recent_v[i] for i in range(len(recent_c) - 1) if recent_c[i+1] > recent_c[i])
        total_vol_5 = sum(recent_v)
        up_vol_ratio = up_vol / max(total_vol_5, 1.0)
        feats["x_up_vol_ratio"] = round(float(up_vol_ratio), 4)
        feats["x_buy_pressure"] = round(float(up_vol_ratio) * abs(chg5) / 10.0, 4)

    # ── L2: 月线趋势 (Phase 69) — 从已有数组计算 ──
    if n >= 21:
        ma21 = np.mean(closes[-21:])
        if ma21 > 0:
            feats["monthly_chg"] = round((close - closes[-21]) / closes[-21] * 100, 4) if closes[-21] > 0 else 0.0
            feats["monthly_ma_position"] = round((close - ma21) / ma21 * 100, 4)
        rets_21 = np.diff(closes[-22:]) / closes[-22:-1] * 100 if n >= 22 else np.zeros(20)
        feats["monthly_volatility"] = round(float(np.std(rets_21)), 4) if len(rets_21) > 0 else 0.0

    return feats


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    """RSI(14)."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-period - 1:])
    gain = np.sum(deltas[deltas > 0])
    loss = -np.sum(deltas[deltas < 0])
    if loss == 0:
        return 100.0
    rs = gain / loss
    return float(100 - 100 / (1 + rs))


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均."""
    if len(series) < period:
        return np.full_like(series, np.nan)
    result = np.full_like(series, np.nan)
    cumsum = np.cumsum(np.insert(series, 0, 0))
    result[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return result


async def build_features(symbol: str, scan_date, session) -> dict:
    """从 daily_kline + 信号历史 构建特征向量.

    Args:
        symbol: 股票代码
        scan_date: 推荐日期 (date 或 str)
        session: 数据库 session

    Returns:
        {feat_name: float} dict, 缺失值填 0
    """
    feats: dict[str, float] = {f: 0.0 for f in FEAT_NAMES}

    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)

    try:
        # 取 120 日 K 线 (足够计算 MA60 和 20日波动率)
        r = await session.execute(text(
            "SELECT trade_date, open, high, low, close, volume, amount "
            "FROM daily_kline WHERE ts_code = :c AND trade_date <= :d "
            "ORDER BY trade_date DESC LIMIT 150"
        ), {"c": symbol, "d": scan_date})
        rows = list(reversed(r.fetchall()))  # 升序

        if len(rows) < 20:
            return feats

        closes = np.array([float(rw[4] or 0) for rw in rows], dtype=np.float64)
        opens = np.array([float(rw[1] or 0) for rw in rows], dtype=np.float64)
        highs = np.array([float(rw[2] or 0) for rw in rows], dtype=np.float64)
        lows = np.array([float(rw[3] or 0) for rw in rows], dtype=np.float64)
        volumes = np.array([float(rw[5] or 0) for rw in rows], dtype=np.float64)
        amounts = np.array([float(rw[6] or 0) for rw in rows], dtype=np.float64)

        n = len(closes)
        close = closes[-1]
        if close <= 0:
            return feats

        # ═══ A. 价格位置 ═══
        ma5 = np.mean(closes[-5:]) if n >= 5 else close
        ma20 = np.mean(closes[-20:]) if n >= 20 else close
        ma60 = np.mean(closes[-60:]) if n >= 60 else close
        high20 = np.max(highs[-20:]) if n >= 20 else close
        low20 = np.min(lows[-20:]) if n >= 20 else close

        feats["price_vs_ma5"] = round((close - ma5) / ma5 * 100, 4) if ma5 > 0 else 0
        feats["price_vs_ma20"] = round((close - ma20) / ma20 * 100, 4) if ma20 > 0 else 0
        feats["price_vs_ma60"] = round((close - ma60) / ma60 * 100, 4) if ma60 > 0 else 0
        feats["price_vs_20d_high"] = round(close / high20, 4) if high20 > 0 else 1
        feats["price_vs_20d_low"] = round(close / low20, 4) if low20 > 0 else 1

        # ═══ B. 动量 ═══
        feats["chg_1d"] = round(float((closes[-1] / closes[-2] - 1) * 100), 4) if n >= 2 and closes[-2] > 0 else 0
        feats["chg_5d"] = round(float((closes[-1] / closes[-5] - 1) * 100), 4) if n >= 5 and closes[-5] > 0 else 0
        feats["chg_20d"] = round(float((closes[-1] / closes[-20] - 1) * 100), 4) if n >= 20 and closes[-20] > 0 else 0
        feats["rsi_14"] = round(_rsi(closes), 2)

        # MACD
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        diff = ema12 - ema26
        dea = _ema(diff, 9)
        feats["macd_hist"] = round(float(2 * (diff[-1] - dea[-1])), 4) if len(diff) > 0 else 0

        # BB position
        std20 = float(np.std(closes[-20:])) if n >= 20 else 0.01
        feats["bb_position"] = round(float((close - ma20) / (2 * std20)), 4) if std20 > 0 else 0

        # ═══ C. 成交量 ═══
        vol_ma5 = float(np.mean(volumes[-5:])) if n >= 5 else volumes[-1]
        vol_ma20 = float(np.mean(volumes[-20:])) if n >= 20 else volumes[-1]
        feats["vol_ratio_5v20"] = round(float(vol_ma5 / vol_ma20), 4) if vol_ma20 > 0 else 1
        feats["vol_ratio_1v20"] = round(float(volumes[-1] / vol_ma20), 4) if vol_ma20 > 0 else 1

        # vol_trend: 量前5 vs 再前15
        if n >= 20:
            recent5 = float(np.mean(volumes[-5:]))
            prior15 = float(np.mean(volumes[-20:-5]))
            feats["vol_trend"] = round(float((recent5 / prior15 - 1) * 100), 2) if prior15 > 0 else 0

        feats["amount_ma5_log"] = round(float(np.log1p(np.mean(amounts[-5:]))), 4) if n >= 5 else 0

        # Turnover (简化: amount / price 作为流通股近似)
        if n >= 20 and close > 0:
            avg_amt_20 = float(np.mean(amounts[-20:]))
            feats["turnover_rate"] = round(float(avg_amt_20 / (close * 1e8) * 100), 4)  # 近似换手率

        # ═══ D. 波动率 ═══
        if n >= 6:
            rets_5d = np.diff(closes[-6:]) / closes[-6:-1]
            rets_5d = rets_5d[np.isfinite(rets_5d)]
            feats["volatility_5d"] = round(float(np.std(rets_5d) * 100), 4) if len(rets_5d) > 0 else 0

        if n >= 21:
            rets_20d = np.diff(closes[-21:]) / closes[-21:-1]
            rets_20d = rets_20d[np.isfinite(rets_20d)]
            feats["volatility_20d"] = round(float(np.std(rets_20d) * 100), 4) if len(rets_20d) > 0 else 0

        # ATR(14)
        if n >= 15:
            trs = []
            for i in range(max(1, n - 14), n):
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i - 1]) if closes[i - 1] > 0 else 0,
                         abs(lows[i] - closes[i - 1]) if closes[i - 1] > 0 else 0)
                trs.append(tr)
            atr14 = float(np.mean(trs)) if trs else 0
            feats["atr_pct"] = round(float(atr14 / close * 100), 4) if close > 0 else 0

        # 5日均振幅
        if n >= 5:
            amps = [(highs[i] - lows[i]) / closes[i] * 100 for i in range(n - 5, n) if closes[i] > 0]
            feats["amplitude_5d"] = round(float(np.mean(amps)), 4) if amps else 0

        # ═══ E. 形态 ═══
        # Consecutive up days
        up_days = 0
        for i in range(n - 1, max(0, n - 11), -1):
            if closes[i] > closes[i - 1]:
                up_days += 1
            else:
                break
        feats["consecutive_up"] = float(up_days)

        # Up/down ratio (10d)
        ups = sum(1 for i in range(max(0, n - 10), n) if closes[i] > opens[i])
        downs = sum(1 for i in range(max(0, n - 10), n) if closes[i] < opens[i])
        feats["up_down_ratio_10d"] = round(float(ups / max(downs, 1)), 4)

        # High-low range 20d
        feats["high_low_range_20d"] = round(float((high20 - low20) / low20 * 100), 2) if low20 > 0 else 0

        # Gap count 5d
        gaps = 0
        for i in range(max(0, n - 5), n):
            if i > 0 and closes[i - 1] > 0:
                if lows[i] > highs[i - 1]:  # 向上跳空
                    gaps += 1
                elif highs[i] < lows[i - 1]:  # 向下跳空
                    gaps += 1
        feats["gap_count_5d"] = float(gaps)

        # Shadow ratio (上影/下影)
        if n >= 5:
            upper_shadows = [(highs[i] - max(opens[i], closes[i])) / max(highs[i] - lows[i], 0.001)
                           for i in range(n - 5, n)]
            lower_shadows = [(min(opens[i], closes[i]) - lows[i]) / max(highs[i] - lows[i], 0.001)
                           for i in range(n - 5, n)]
            avg_upper = float(np.mean(upper_shadows))
            avg_lower = float(np.mean(lower_shadows))
            feats["shadow_ratio"] = round(float(avg_upper / max(avg_lower, 0.001)), 4)

        # Bullish engulf (最近5日)
        engulf = 0
        for i in range(max(1, n - 5), n):
            if (closes[i] > opens[i] and
                opens[i - 1] > closes[i - 1] and  # 前天阴线
                closes[i] > opens[i - 1] and opens[i] < closes[i - 1]):  # 包住前天
                engulf = 1
                break
        feats["bullish_engulf"] = float(engulf)

        # ═══ F. 信号历史 ═══
        try:
            r2 = await session.execute(text(
                "SELECT COUNT(*), "
                "MAX(scan_date), "
                "AVG(CASE WHEN ret_t5 > 0 THEN 1.0 ELSE 0.0 END) "
                "FROM signal_history "
                "WHERE symbol = :s AND scan_date < :d"
            ), {"s": symbol, "d": scan_date})
            row = r2.fetchone()
            if row:
                feats["push_count_30d"] = float(row[0] or 0)
                last_date = row[1]
                feats["same_stock_win_rate"] = round(float(row[2] or 0.5), 4)
                if last_date and isinstance(last_date, date):
                    feats["days_since_last_signal"] = float((scan_date - last_date).days)
        except Exception:
            pass

    except Exception as e:
        logger.debug(f"Feature build failed for {symbol}: {e}")

    # ═══ G. 交互特征 — Phase 32a (11维手动 + 3维真实板块) ═══
    # 快捷引用
    rsi = feats["rsi_14"]
    macd = feats["macd_hist"]
    vol5v20 = feats.get("vol_ratio_5v20", 1.0) or 1.0
    vol5d = feats.get("volatility_5d", 0) or 0
    vol20d = feats.get("volatility_20d", 0) or 0
    chg5 = feats["chg_5d"]
    pv_ma20 = feats.get("price_vs_ma20", 0) or 0
    pv_ma60 = feats.get("price_vs_ma60", 0) or 0

    # ── G1: 超卖/超买 + 量确认 (3维) ──
    feats["x_oversold_vol"] = round((1.0 if rsi < 35 else 0.0) * vol5v20, 4)
    feats["x_overbought_vol"] = round((1.0 if rsi > 70 else 0.0) * vol5v20, 4)
    feats["x_rsi_vol_interact"] = round((50.0 - rsi) * vol5v20 / 100.0, 4)

    # ── G2: 均线压力组合 (3维) ──
    try:
        ma5v = float(np.mean(closes[-5:])) if n >= 5 else close
        feats["x_ma5_ma20_gap"] = round((ma5v - ma20) / max(ma20, 0.01) * 100, 4)
    except Exception:
        feats["x_ma5_ma20_gap"] = 0.0
    feats["x_price_ma20_bias"] = round(pv_ma20 * vol5v20, 4)
    feats["x_price_ma60_bias"] = round(pv_ma60 * vol5v20, 4)

    # ── G4: 波动率 + 趋势方向 (2维) ──
    sign_chg5 = 1.0 if chg5 > 0 else (-1.0 if chg5 < 0 else 0.0)
    feats["x_vol_trend_sign"] = round(vol5d * sign_chg5, 4)
    feats["x_vol_chg_ratio_short"] = round(vol5d / max(vol20d, 0.01), 4)

    # ── G6: 量价配合度 (3维) ──
    if n >= 5:
        recent_c = closes[-5:]; recent_v = volumes[-5:]
        c_deltas = np.diff(recent_c); v_deltas = np.diff(recent_v)
        if len(c_deltas) >= 3 and np.std(c_deltas) > 0 and np.std(v_deltas) > 0:
            corr = np.corrcoef(c_deltas, v_deltas)[0, 1]
            feats["x_vol_price_corr_5d"] = round(float(corr) if not np.isnan(corr) else 0.0, 4)
        else:
            feats["x_vol_price_corr_5d"] = 0.0
        up_vol = sum(recent_v[i] for i in range(len(recent_c) - 1) if recent_c[i+1] > recent_c[i])
        total_vol_5 = sum(recent_v)
        up_vol_ratio = up_vol / max(total_vol_5, 1.0)
        feats["x_up_vol_ratio"] = round(float(up_vol_ratio), 4)
        feats["x_buy_pressure"] = round(float(up_vol_ratio) * abs(chg5) / 10.0, 4)

    # ── G7: 真实板块上下文 — 默认 0，由 _preload_sector_features 批量填充 ──
    # values are set by batch preload in _build_from_* functions, not here

    # ── J: 新闻暴露特征 — 默认 0，由 _preload_news 批量填充 ──

    # ── L2: 月线趋势 (Phase 69) — 从已有数组计算 ──
    if n >= 21:
        ma21 = np.mean(closes[-21:])
        if ma21 > 0:
            feats["monthly_chg"] = round((close - closes[-21]) / closes[-21] * 100, 4) if closes[-21] > 0 else 0.0
            feats["monthly_ma_position"] = round((close - ma21) / ma21 * 100, 4)
        rets_21 = np.diff(closes[-22:]) / closes[-22:-1] * 100 if n >= 22 else np.zeros(20)
        feats["monthly_volatility"] = round(float(np.std(rets_21)), 4) if len(rets_21) > 0 else 0.0
    else:
        feats["monthly_chg"] = feats["chg_20d"]  # fallback to 20d
        feats["monthly_volatility"] = feats["volatility_20d"]
        feats["monthly_ma_position"] = feats["price_vs_ma20"]

    return feats


async def _preload_sector_features(session, symbols: list[str], scan_date) -> dict[str, dict]:
    """Batch-preload real sector features for many symbols at once (Phase 32a).

    One bulk query to stock_sector_map + one to sector_trend
    instead of per-symbol SQL in build_features().
    """
    result: dict[str, dict] = {s: {} for s in symbols}
    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)
    try:
        # Single query: get sw_code per symbol
        r = await session.execute(text(
            "SELECT ts_code, sw_code FROM stock_sector_map WHERE ts_code = ANY(:syms)"
        ), {"syms": symbols})
        sym_to_sw = {row[0]: row[1] for row in r.fetchall() if row[1]}
        if not sym_to_sw:
            return result
        # Single query: get sector trend for all relevant sectors
        sw_codes = list(set(sym_to_sw.values()))
        r = await session.execute(text(
            "SELECT sector_code, pct_5d, rank_5d, direction, lifecycle, "
            "       COALESCE(rank_20d, rank_5d) as rank_20d, COALESCE(vol_ratio, 1) as vol_ratio "
            "FROM sector_trend "
            "WHERE sector_code = ANY(:sc) AND trade_date <= :d "
            "ORDER BY trade_date DESC"
        ), {"sc": sw_codes, "d": scan_date})
        # Take first row per sector (most recent ≤ scan_date)
        sw_trends: dict[str, tuple] = {}
        for row in r.fetchall():
            if row[0] not in sw_trends:
                sw_trends[row[0]] = (float(row[1] or 0), row[2] or 16,
                                     row[3] or "震荡", row[4] or "正常",
                                     row[5] or 16, float(row[6] or 1))
        for sym, sw in sym_to_sw.items():
            if sw in sw_trends:
                pct_5d, rank, direction, lifecycle, rank_20d, vol_ratio = sw_trends[sw]
                result[sym] = {
                    "x_real_sector_5d": round(pct_5d, 2),
                    "x_real_alpha": 0.0,
                    "x_real_rank_5d": round(rank / 32.0, 4),
                    # Phase 34: 板块轮动特征
                    "sector_direction_up": 1.0 if direction == "上升" else 0.0,
                    "sector_direction_down": 1.0 if direction == "下降" else 0.0,
                    "sector_rank_top8": 1.0 if rank <= 8 else 0.0,
                    "sector_lifecycle_hot": 1.0 if lifecycle in ("发酵", "高潮") else 0.0,
                    # Phase 69: 板块轮动速度
                    "sector_rank_20d": round(rank_20d / 32.0, 4),
                    "sector_velocity": round((rank_20d - rank) / 32.0, 4),
                    "sector_vol_chg": round((vol_ratio - 1.0) / max(abs(vol_ratio), 0.01), 4),
                }
    except Exception:
        pass
    return result


_toplist_cache: dict[str, dict] = {}

async def _preload_toplist(session, symbols: list[str], scan_date) -> None:
    """Batch-preload dragon-tiger board features for all symbols at once (Phase 33).

    One pass over 3 toplist tables → cache dict with per-symbol feature values.
    Most stocks never appear on toplist → cache entry = {} (all zeros).
    """
    global _toplist_cache
    _toplist_cache.clear()

    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)
    cutoff_20d = scan_date - timedelta(days=40)  # ~20 trading days
    td_str = str(scan_date)

    try:
        # ── Today's board appearance ──
        r = await session.execute(text(
            "SELECT ts_code, l_buy, l_sell, l_net, amount, turnover_rate "
            "FROM toplist_daily WHERE trade_date = :td AND ts_code = ANY(:syms)"
        ), {"td": scan_date, "syms": symbols})
        today_map = {}
        for row in r.fetchall():
            l_buy = float(row[1] or 0)
            l_sell = float(row[2] or 0)
            l_net = float(row[3] or 0)
            today_map[row[0]] = {
                "tl_on_toplist": 1,
                "l_buy": l_buy, "l_sell": l_sell, "l_net": l_net,
                "amount": float(row[4] or 0),
                "turnover_rate": float(row[5] or 0),
            }

        if not today_map:
            # No stock on today's toplist → all features stay 0
            return

        today_codes = list(today_map.keys())

        # ── Institutional participation ──
        r = await session.execute(text(
            "SELECT ts_code, SUM(buy) as inst_buy, SUM(sell) as inst_sell "
            "FROM toplist_inst WHERE trade_date = :td AND ts_code = ANY(:c) "
            "GROUP BY ts_code"
        ), {"td": scan_date, "c": today_codes})
        inst_map = {}
        for row in r.fetchall():
            inst_buy = float(row[1] or 0)
            inst_sell = float(row[2] or 0)
            total_buy = today_map.get(row[0], {}).get("l_buy", 1) or 1
            inst_map[row[0]] = inst_buy / total_buy if total_buy > 0 else 0

        # ── Buy concentration (top1 desk / total buy) ──
        r = await session.execute(text(
            "SELECT ts_code, MAX(buy) as top1, SUM(buy) as total "
            "FROM toplist_detail WHERE trade_date = :td AND ts_code = ANY(:c) "
            "GROUP BY ts_code"
        ), {"td": scan_date, "c": today_codes})
        conc_map = {}
        for row in r.fetchall():
            top1 = float(row[1] or 0)
            total = float(row[2] or 0)
            conc_map[row[0]] = top1 / total if total > 0 else 0

        # ── Historical appearances (5d, 20d) + net trend ──
        r = await session.execute(text(
            "SELECT ts_code, COUNT(*) as apps, AVG(l_net) as avg_net "
            "FROM toplist_daily "
            "WHERE trade_date >= :cut AND trade_date < :td AND ts_code = ANY(:c) "
            "GROUP BY ts_code"
        ), {"cut": cutoff_20d, "td": scan_date, "c": today_codes})
        hist_map = {}
        for row in r.fetchall():
            hist_map[row[0]] = (int(row[1] or 0), float(row[2] or 0))

        # Past 5 trading days separately
        cutoff_5d = scan_date - timedelta(days=10)
        r = await session.execute(text(
            "SELECT ts_code, COUNT(*) FROM toplist_daily "
            "WHERE trade_date >= :cut AND trade_date <= :td AND ts_code = ANY(:c) "
            "GROUP BY ts_code"
        ), {"cut": cutoff_5d, "td": scan_date, "c": today_codes})
        apps_5d_map = {row[0]: row[1] for row in r.fetchall()}

        # ── Phase 60: 龙虎榜深度特征 ──
        # 1. 机构连续买入天数 (consecutive trading days with inst net > 0)
        inst_continuous_map: dict[str, int] = {}
        # 2. 营业部质量 (institutional seat ratio)
        seat_quality_map: dict[str, float] = {}
        # 3. 10日净买入趋势 (slope over past 10d appearances)
        net_trend_10d_map: dict[str, float] = {}
        # 4. 连续上榜天数
        consec_days_map: dict[str, int] = {}
        # 5. 成交额占比
        amount_ratio_map: dict[str, float] = {}
        # 6. 机构净买入连续
        inst_streak_map: dict[str, float] = {}

        # ── 1. 机构连续买入天数 ──
        r = await session.execute(text("""
            SELECT ts_code, trade_date, SUM(net) as inst_net
            FROM toplist_inst
            WHERE ts_code = ANY(:c)
            GROUP BY ts_code, trade_date
            ORDER BY ts_code, trade_date DESC
        """), {"c": today_codes})
        inst_daily = defaultdict(list)
        for row in r.fetchall():
            inst_daily[row[0]].append((row[1], float(row[2] or 0)))
        for code, days in inst_daily.items():
            streak = 0
            for _, net in days:
                if net > 0:
                    streak += 1
                else:
                    break
            inst_continuous_map[code] = streak
            inst_streak_map[code] = 1.0 if (days and days[0][1] > 0) else 0.0

        # ── 2. 营业部质量 (institutional seat ratio from today's detail) ──
        r = await session.execute(text("""
            SELECT ts_code, COUNT(*) FILTER(WHERE
                exalter LIKE '%专用%' OR exalter LIKE '%机构%' OR exalter LIKE '%总部%'
                OR exalter LIKE '%公司%')::float / COUNT(*)::float
            FROM toplist_detail
            WHERE trade_date = :td AND ts_code = ANY(:c)
            GROUP BY ts_code
        """), {"td": scan_date, "c": today_codes})
        for row in r.fetchall():
            seat_quality_map[row[0]] = round(float(row[1]), 4)

        # ── 3. 10日净买入趋势 (slope of l_net over past 10d) ──
        r = await session.execute(text("""
            SELECT ts_code, trade_date, l_net FROM toplist_daily
            WHERE trade_date >= :cut AND ts_code = ANY(:c)
            ORDER BY ts_code, trade_date
        """), {"cut": cutoff_20d, "c": today_codes})
        net_10d = defaultdict(list)
        for row in r.fetchall():
            net_10d[row[0]].append(float(row[2] or 0))
        for code, nets in net_10d.items():
            if len(nets) >= 3:
                x = np.arange(len(nets))
                # simple linear slope normalized by absolute mean
                mean_abs = np.mean(np.abs(nets))
                if mean_abs > 0:
                    slope = np.polyfit(x, np.array(nets), 1)[0]
                    net_trend_10d_map[code] = round(float(slope / mean_abs), 4)
                else:
                    net_trend_10d_map[code] = 0.0
            else:
                net_trend_10d_map[code] = 0.0

        # ── 4. 连续上榜天数 ──
        r = await session.execute(text("""
            SELECT ts_code, trade_date FROM toplist_daily
            WHERE ts_code = ANY(:c)
            ORDER BY ts_code, trade_date DESC
        """), {"c": today_codes})
        consec_raw = defaultdict(list)
        for row in r.fetchall():
            consec_raw[row[0]].append(row[1])
        for code, dates in consec_raw.items():
            streak = 1
            for i in range(1, len(dates)):
                # calendar-day gap <= 2 = consecutive trading day
                if (dates[i-1] - dates[i]).days <= 2:
                    streak += 1
                else:
                    break
            consec_days_map[code] = streak

        # ── 5. 成交额占比 (avg 5d amount / turnover_rate proxy) ──
        for code, tday in today_map.items():
            amount = tday.get("amount", 0)
            turnover = tday.get("turnover_rate", 1)
            if amount > 0 and turnover > 0:
                amount_ratio_map[code] = round(
                    float(amount) / max(float(turnover) * 1e8, 1.0), 4)
            else:
                amount_ratio_map[code] = 0.0

        # ── Assemble cache ──
        for code, tday in today_map.items():
            total_buy = tday["l_buy"] + tday["l_sell"]
            net_ratio = tday["l_net"] / max(total_buy, 1.0)
            inst_ratio = inst_map.get(code, 0)
            top1_conc = conc_map.get(code, 0)
            hist = hist_map.get(code, (0, 0))
            apps_5 = apps_5d_map.get(code, 0)
            apps_20 = hist[0]
            avg_net_5 = hist[1] / 10000.0 if hist[1] else 0  # 元→万
            net_trend = tday["l_net"] / max(abs(tday["l_net"]), 1.0) * min(apps_5, 3) / 3.0

            turnover_now = tday.get("turnover_rate", 0)
            turnover_avg = tday.get("turnover_rate", 0)  # could be avg of past 20d
            turnover_diff = round(turnover_now / max(turnover_avg, 0.01) - 1.0, 3) if turnover_avg > 0 else 0

            _toplist_cache[code] = {
                "tl_on_toplist": 1,
                "tl_net_buy_ratio": round(net_ratio, 4),
                "tl_inst_ratio": round(inst_ratio, 4),
                "tl_buy_concentration": round(top1_conc, 4),
                "tl_appearances_5d": float(apps_5),
                "tl_appearances_20d": float(apps_20),
                "tl_avg_net_5d": round(avg_net_5, 2),
                "tl_net_trend": round(net_trend, 4),
                "tl_turnover_signal": round(turnover_diff, 4),
                # Phase 60: 龙虎榜深度特征
                "tl_inst_continuous": inst_continuous_map.get(code, 0),
                "tl_seat_quality": seat_quality_map.get(code, 0.0),
                "tl_net_trend_10d": net_trend_10d_map.get(code, 0.0),
                "tl_consecutive_days": consec_days_map.get(code, 1),
                "tl_avg_amount_ratio": amount_ratio_map.get(code, 0.0),
                "tl_inst_net_streak": inst_streak_map.get(code, 0.0),
            }
    except Exception as e:
        logger.debug(f"Toplist preload skipped: {e}")


# ── Phase 51: 新闻暴露特征 ──
_news_cache: dict[str, dict] = {}


async def _preload_news(session, symbols: list[str], scan_date) -> None:
    """Batch-preload verified news exposure features (Phase 51).

    Queries news_aggregated JOIN news_verify.is_active=true
    to get only verified commodity→stock mappings.
    Stores per-symbol binary flags in _news_cache.
    """
    global _news_cache
    _news_cache.clear()

    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)

    try:
        r = await session.execute(text("""
            SELECT na.commodity, na.direction, na.stocks_json, na.category
            FROM news_aggregated na WHERE na.date = :d
        """), {"d": scan_date})
        news_rows = r.fetchall()
        if not news_rows:
            return

        # Load verified active mappings
        try:
            r_nv = await session.execute(text(
                "SELECT commodity, direction, symbol FROM news_verify WHERE is_active = TRUE"
            ))
            active_set = {(nv_row[0], nv_row[1], nv_row[2]) for nv_row in r_nv.fetchall()}
        except Exception:
            active_set = set()

        if not active_set:
            return

        # For each symbol in the batch, build feature flags
        symbol_set = set(symbols)
        for row in news_rows:
            commodity = row[0]
            direction = row[1]
            stocks_json = row[3]
            category = row[4]

            if not stocks_json:
                continue
            import json
            try:
                stocks = json.loads(stocks_json) if isinstance(stocks_json, str) else stocks_json
            except Exception:
                continue

            is_policy = category in ("policy", "macro")
            is_bear = direction == "利空"
            is_bull = direction == "利好"

            for sym in stocks:
                if sym not in symbol_set:
                    continue
                if (commodity, direction, sym) not in active_set:
                    continue

                entry = _news_cache.setdefault(sym, {})
                if is_policy and is_bear:
                    entry["news_policy_bear"] = 1.0
                elif is_policy and is_bull:
                    entry["news_policy_bull"] = 1.0
                elif not is_policy and is_bear:
                    entry["news_commodity_bear"] = 1.0
                elif not is_policy and is_bull:
                    entry["news_commodity_bull"] = 1.0
    except Exception as e:
        logger.debug(f"News preload skipped: {e}")


# ── Phase 69: 周线特征 ──
_weekly_cache: dict[str, dict] = {}


async def _preload_weekly_features(session, symbols: list[str], scan_date) -> None:
    """Batch-preload weekly TG momentum from scan_results (Phase 69)."""
    global _weekly_cache
    _weekly_cache.clear()
    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)
    try:
        r = await session.execute(text(
            "SELECT symbol, COALESCE(weekly_tg_momentum, 0), tg_momentum, resonance_type "
            "FROM scan_results WHERE scan_date = :d AND symbol = ANY(:syms)"
        ), {"d": scan_date, "syms": symbols})
        for row in r.fetchall():
            sym = row[0]
            w_tg = float(row[1] or 0)
            d_tg = float(row[2] or 0)
            resonance = row[3] or ""
            # 日周背离: 日线和周线方向相反时=1 (如日线V形反转买入 vs 周线仍在下行)
            divergence = 1.0 if (d_tg * w_tg < 0) and abs(w_tg) > 1 else 0.0
            _weekly_cache[sym] = {
                "weekly_tg_momentum": round(w_tg, 2),
                "weekly_daily_divergence": divergence,
            }
    except Exception as e:
        logger.debug(f"Weekly preload skipped: {e}")


# ── Phase 54: 市场基准 700001.TI 日线缓存 ──
_market_closes: dict[date, float] = {}

async def _preload_market_closes(session) -> dict[date, float]:
    """预加载 700001.TI 每日收盘价，用于计算超额收益标签."""
    global _market_closes
    if _market_closes:
        return _market_closes
    try:
        r = await session.execute(text(
            "SELECT trade_date, close FROM daily_kline "
            "WHERE ts_code = '700001.TI' ORDER BY trade_date"
        ))
        _market_closes = {row[0]: float(row[1]) for row in r.fetchall() if row[1]}
    except Exception:
        _market_closes = {}
    return _market_closes


def _excess_return(market_closes: dict[date, float], scan_date, stock_ret: float, horizon_days: int) -> float:
    """计算超额收益 = 股票收益 - 同期大盘收益.

    Args:
        market_closes: {trade_date: close}
        scan_date: 信号日期
        stock_ret: 股票 T+N 日收益率 (%)
        horizon_days: T+? (2 or 5)
    """
    if not market_closes or scan_date is None:
        return stock_ret  # fallback: 保留原值
    mkt_dates = sorted(d for d in market_closes if d > scan_date)
    if len(mkt_dates) >= horizon_days:
        c0 = market_closes.get(mkt_dates[0], 0)
        cN = market_closes.get(mkt_dates[horizon_days - 1], 0)
        if c0 > 0 and cN > 0:
            mkt_ret = (cN - c0) / c0 * 100
            return round(stock_ret - mkt_ret, 4)
    return stock_ret


async def build_training_data(session, start_date: str = '2026-01-01') -> tuple:
    """从 signal_history + recommendation_tracking 构建完整训练集 (Phase 30).

    recommendation_tracking 已验证样本给 3x 权重 (精选后推荐比原始信号更有价值).

    Returns:
        (X, y, weights, sources_dict)
        X: np.ndarray (n_samples, n_features)
        y: np.ndarray (n_samples,) — ret_t5 或 ret_2d
        weights: np.ndarray (n_samples,) — 样本权重
        sources_dict: {"signal_history": N, "recommendations": N}
    """
    # Phase 54: 预加载大盘基准用于超额收益标签
    await _preload_market_closes(session)

    # 源1: signal_history
    X1, y1, sd1 = await _build_from_signal_history(session, start_date)

    # 源2: recommendation_tracking (Phase 29 verified return_2d)
    X2, y2, sd2 = await _build_from_recommendations(session, start_date)

    if len(X1) == 0 and len(X2) == 0:
        logger.warning("No training data in any source")
        return np.empty((0, len(FEAT_NAMES))), np.empty(0), np.ones(0), {}, []

    X_parts, y_parts = [], []
    if len(X1) > 0:
        X_parts.append(X1)
        y_parts.append(y1)
    if len(X2) > 0:
        X_parts.append(X2)
        y_parts.append(y2)

    X = np.vstack(X_parts)
    y = np.hstack(y_parts)

    # Phase 55: 按 scan_date 分组用于排序学习
    all_sd = list(sd1) + list(sd2)
    groups = _compute_groups(all_sd)

    # 权重: recommendation ×3
    weights = np.ones(len(X), dtype=np.float32)
    n_sig = len(X1); n_rec = len(X2)
    if n_rec > 0:
        weights[n_sig:n_sig + n_rec] = 3.0

    logger.info(f"Training data: {len(X)} samples, {len(groups)} groups "
                f"(signal: {n_sig}, rec: {n_rec})")

    return X, y, weights, {"signal_history": n_sig, "recommendations": n_rec}, groups


def _compute_groups(scan_dates: list[date]) -> list[int]:
    """将排序后的 scan_date 列表转换为 LambdaRank 所需的 group 大小数组."""
    if not scan_dates:
        return []
    sorted_dates = sorted(scan_dates)
    groups = []
    current = sorted_dates[0]
    count = 0
    for d in sorted_dates:
        if d == current:
            count += 1
        else:
            if count > 0:
                groups.append(count)
            current = d
            count = 1
    if count > 0:
        groups.append(count)
    return groups


async def _build_from_signal_history(session, start_date) -> tuple:
    """源1: signal_history 的 ret_t5 标签."""
    r = await session.execute(text("""
        SELECT symbol, scan_date, composite_score, archetype,
               push_count_30d, ret_t5, ret_t2, outcome_label
        FROM signal_history
        WHERE ret_t5 IS NOT NULL AND scan_date >= :start
        ORDER BY scan_date
    """), {"start": date.fromisoformat(start_date) if isinstance(start_date, str) else start_date})
    rows = r.fetchall()

    if not rows:
        logger.info("No training data in signal_history")
        return np.empty((0, len(FEAT_NAMES))), np.empty(0), []

    # ★ Batch preload real sector features (Phase 32a)
    symbols = list(set(row[0] for row in rows))
    scan_date = rows[-1][1]  # use latest scan_date as proxy for sector trend query
    sector_cache = await _preload_sector_features(session, symbols, scan_date)
    await _preload_toplist(session, symbols, scan_date)  # Phase 33
    await _preload_news(session, symbols, scan_date)  # Phase 51
    await _preload_weekly_features(session, symbols, scan_date)  # Phase 69

    X_list, y_list = [], []
    sd_list: list[date] = []

    for i, row in enumerate(rows):
        symbol, scan_date = row[0], row[1]
        sd_list.append(scan_date)
        feats = await build_features(symbol, scan_date, session)
        # Inject preloaded sector + toplist features
        sc = sector_cache.get(symbol, {})
        feats.update(sc)
        if sc:
            feats["x_real_alpha"] = round(feats.get("chg_5d", 0) - sc.get("x_real_sector_5d", 0), 2)
        tl = _toplist_cache.get(symbol, {})
        feats.update(tl)
        if tl:
            feats["tl_oversold"] = 1.0 if feats.get("rsi_14", 50) < 35 else 0.0
            feats["tl_breakout"] = 1.0 if feats.get("price_vs_ma20", 0) > 2 else 0.0
        # Phase 51: 新闻暴露特征注入
        nc = _news_cache.get(symbol, {})
        feats.update(nc)
        # Phase 69: 周线特征注入
        wc = _weekly_cache.get(symbol, {})
        feats.update(wc)
        vec = [feats.get(f, 0.0) for f in FEAT_NAMES]
        X_list.append(vec)
        # Phase 54: 超额收益 = 股票T+5 - 大盘T+5
        label = _excess_return(_market_closes, row[1], float(row[5] or 0), 5)
        y_list.append(label)

        if (i + 1) % 500 == 0:
            logger.info(f"  signal_history: {i + 1}/{len(rows)}")

    X1 = np.array(X_list, dtype=np.float32)
    y1 = np.array(y_list, dtype=np.float32)
    X1 = sanitize_array(X1, fill=0.0)
    y1 = sanitize_array(y1, fill=0.0)
    return X1, y1, sd_list


async def _build_from_recommendations(session, start_date) -> tuple:
    """源2: recommendation_tracking 的 verified return_2d 标签 (Phase 29+30)."""
    try:
        r = await session.execute(text("""
            SELECT symbol, scan_date, close_price, return_2d, return_5d
            FROM recommendation_tracking
            WHERE verified_2d = true AND scan_date >= :start AND return_2d IS NOT NULL
            ORDER BY scan_date
        """), {"start": date.fromisoformat(start_date) if isinstance(start_date, str) else start_date})
        rows = r.fetchall()
    except Exception:
        return np.empty((0, len(FEAT_NAMES))), np.empty(0), []

    if not rows:
        logger.info("No verified recommendations for training")
        return np.empty((0, len(FEAT_NAMES))), np.empty(0), []

    # ★ Batch preload real sector features (Phase 32a)
    symbols = list(set(row[0] for row in rows))
    scan_d = rows[-1][1] if rows else date.today()
    sector_cache = await _preload_sector_features(session, symbols, scan_d)
    await _preload_toplist(session, symbols, scan_d)  # Phase 33
    await _preload_news(session, symbols, scan_d)  # Phase 51
    await _preload_weekly_features(session, symbols, scan_d)  # Phase 69

    X_list, y_list = [], []
    sd_list_rec: list[date] = []
    for i, row in enumerate(rows):
        symbol, scan_date = row[0], row[1]
        sd_list_rec.append(scan_date)
        feats = await build_features(symbol, scan_date, session)
        sc = sector_cache.get(symbol, {})
        feats.update(sc)
        if sc:
            feats["x_real_alpha"] = round(feats.get("chg_5d", 0) - sc.get("x_real_sector_5d", 0), 2)
        tl = _toplist_cache.get(symbol, {})
        feats.update(tl)
        if tl:
            feats["tl_oversold"] = 1.0 if feats.get("rsi_14", 50) < 35 else 0.0
            feats["tl_breakout"] = 1.0 if feats.get("price_vs_ma20", 0) > 2 else 0.0
        # Phase 51: 新闻暴露特征注入
        nc = _news_cache.get(symbol, {})
        feats.update(nc)
        # Phase 69: 周线特征注入
        wc = _weekly_cache.get(symbol, {})
        feats.update(wc)
        vec = [feats.get(f, 0.0) for f in FEAT_NAMES]
        X_list.append(vec)
        # Phase 54: 超额收益 = 股票T+2 - 大盘T+2
        label = _excess_return(_market_closes, row[1], float(row[3] or 0), 2)
        y_list.append(label)

    logger.info(f"  recommendation_tracking: {len(rows)} verified samples")

    X2 = np.array(X_list, dtype=np.float32)
    y2 = np.array(y_list, dtype=np.float32)
    X2 = sanitize_array(X2, fill=0.0)
    y2 = sanitize_array(y2, fill=0.0)
    return X2, y2, sd_list_rec
