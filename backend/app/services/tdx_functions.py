"""TDX 通达信函数 — numpy/pandas 向量化实现."""
import pandas as pd
import numpy as np

def MA(series, period):
    return series.rolling(window=period, min_periods=1).mean()

def EMA(series, period):
    return series.ewm(span=period, adjust=False).mean()

def SMA(series, period, weight=1):
    return series.ewm(alpha=weight/period, adjust=False).mean()

def REF(series, n):
    return series.shift(n)

def HHV(series, period):
    return series.rolling(window=period, min_periods=1).max()

def LLV(series, period):
    return series.rolling(window=period, min_periods=1).min()

def HHV_VARIABLE(series, period_series):
    return pd.Series([series.iloc[max(0,i-int(p)+1):i+1].max() if pd.notna(p) else np.nan for i,p in enumerate(period_series)], index=series.index)

def LLV_VARIABLE(series, period_series):
    return pd.Series([series.iloc[max(0,i-int(p)+1):i+1].min() if pd.notna(p) else np.nan for i,p in enumerate(period_series)], index=series.index)

def BARSLAST(cond):
    cond = cond.astype(bool)
    result = pd.Series(np.nan, index=cond.index)
    last_true = np.nan
    for i in range(len(cond)):
        if cond.iloc[i]: last_true = 0
        elif not np.isnan(last_true): last_true += 1
        result.iloc[i] = last_true if not np.isnan(last_true) else 0
    return result.fillna(999).astype(int)

def COUNT(cond, N):
    return cond.rolling(window=N, min_periods=1).sum()

def CROSS(a, b):
    return (a > b) & (REF(a, 1) <= REF(b, 1))

def STD(series, period):
    return series.rolling(window=period, min_periods=1).std()

def IF(cond, true_val, false_val):
    """向量化 IF: cond 为 True 取 true_val, 否则取 false_val."""
    c = cond.astype(bool)
    t_is_scalar = isinstance(true_val, (int, float, np.number)) or np.isscalar(true_val)
    f_is_scalar = isinstance(false_val, (int, float, np.number)) or np.isscalar(false_val)

    if t_is_scalar and f_is_scalar:
        return pd.Series(np.where(c, float(true_val), float(false_val)), index=cond.index)

    t_vals = np.full(len(cond), true_val, dtype=object) if t_is_scalar else np.asarray(true_val, dtype=float)
    f_vals = np.full(len(cond), false_val, dtype=object) if f_is_scalar else np.asarray(false_val, dtype=float)
    return pd.Series(np.where(c, t_vals, f_vals), index=cond.index)

def ABS(series):
    return series.abs()


def calc_rsi(prices, period: int = 14):
    """相对强弱指标 RSI — 返回完整时间序列.

    Args:
        prices: pd.Series (收盘价) 或 np.ndarray
        period: RSI 周期

    Returns:
        pd.Series — 完整的 RSI (0-100) 时间序列
    """
    if not isinstance(prices, pd.Series):
        prices = pd.Series(prices)
    deltas = prices.diff()
    gains = deltas.clip(lower=0)
    losses = -deltas.clip(upper=0)
    avg_gain = gains.ewm(span=period, adjust=False).mean()
    avg_loss = losses.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)
