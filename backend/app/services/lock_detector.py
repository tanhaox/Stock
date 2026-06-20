"""AlphaFlow 锁死检测 — 双窗口振幅校验.

v2.3 (2026-06-07):
  - 系统已全局使用前复权K线, 除权检测不再需要
"""
import numpy as np
import logging

logger = logging.getLogger("lock_detector")


def detect_lock_simple(closes, highs, lows, index_closes=None):
    """双窗口锁死检测.

    短窗口 15-20 天振幅 ≤ 15%  AND  长窗口 20-40 天振幅 ≤ 17%.
    两个条件同时满足才判定为锁死."""
    n = len(closes)
    if n < 30:
        return {"in_lock": False, "amplitude_30d": 0, "verdict": "数据不足"}

    # ── 短窗: 15~20 天, 振幅 ≤ 15% ──
    n_short = min(n, 15)
    h_short = float(np.max(highs[-n_short:]))
    l_short = float(np.min(lows[-n_short:]))
    amp_short = (h_short - l_short) / l_short * 100 if l_short > 0 else 100

    n_short2 = min(n, 20)
    h_short2 = float(np.max(highs[-n_short2:]))
    l_short2 = float(np.min(lows[-n_short2:]))
    amp_short2 = (h_short2 - l_short2) / l_short2 * 100 if l_short2 > 0 else 100

    short_pass = (amp_short <= 15.0 and amp_short2 <= 15.0)

    # ── 长窗: 20~40 天, 振幅 ≤ 17% ──
    n_long = min(n, 40)
    h_long = float(np.max(highs[-n_long:]))
    l_long = float(np.min(lows[-n_long:]))
    amp_long = (h_long - l_long) / l_long * 100 if l_long > 0 else 100

    n_long2 = min(n, 20)
    h_long2 = float(np.max(highs[-n_long2:]))
    l_long2 = float(np.min(lows[-n_long2:]))
    amp_long2 = (h_long2 - l_long2) / l_long2 * 100 if l_long2 > 0 else 100

    long_pass = (amp_long <= 17.0 and amp_long2 <= 17.0)

    in_lock = short_pass and long_pass

    h_30 = float(np.max(highs[-min(n,30):]))
    l_30 = float(np.min(lows[-min(n,30):]))
    amp_30 = (h_30 - l_30) / l_30 * 100 if l_30 > 0 else 100

    # ── v4.7: 突破方向判定 ──
    # 振幅超标有两种可能：向上突破（真主升浪）vs 向下跌破（破位）。
    # 通过 close vs MA20 + 20日涨跌幅 区分。
    close_now = closes[-1]
    ma20 = float(np.mean(closes[-20:])) if n >= 20 else close_now
    close_20d_ago = closes[-20] if n >= 20 else closes[0]
    trend_20d = (close_now - close_20d_ago) / close_20d_ago * 100 if close_20d_ago > 0 else 0

    if in_lock:
        state = "locked"
    elif close_now > ma20 and trend_20d > -3:
        state = "breakout_up"
    else:
        state = "breakout_down"

    # 已锁天数
    lock_days = 0
    for i in range(1, min(n, 80)):
        w_start = max(0, n - i)
        w_h = float(np.max(highs[w_start:]))
        w_l = float(np.min(lows[w_start:]))
        w_amp = (w_h - w_l) / w_l * 100 if w_l > 0 else 100
        if i <= 20:
            if w_amp > 15.0: break
        else:
            if w_amp > 17.0: break
        lock_days = i

    market_ret = 0.0
    if index_closes is not None and len(index_closes) >= n:
        idx_recent = index_closes[-30:]
        if idx_recent[0] > 0:
            market_ret = (idx_recent[-1] - idx_recent[0]) / idx_recent[0] * 100

    stock_ret = (closes[-1] - closes[-30]) / closes[-30] * 100 if closes[-30] > 0 else 0
    relative_strength = stock_ret - market_ret

    if not in_lock:
        if not short_pass:
            verdict = "非锁死(短窗振幅过大)" if amp_short > 15 else "非锁死(长窗超限)"
        else:
            verdict = "非锁死(长窗超限)"
    elif market_ret < -3:
        verdict = "强势锁死"
    elif market_ret > 5:
        verdict = "弱势横盘"
    elif relative_strength > 3:
        verdict = "蓄力偏强"
    else:
        verdict = "蓄力中"

    reason = ""
    if not short_pass:
        reason = f"15d振幅{amp_short:.1f}% > 15%"
    elif not long_pass:
        reason = f"40d振幅{amp_long:.1f}% > 17%"
    else:
        reason = f"15d:{amp_short:.1f}%/20d:{amp_short2:.1f}% | 20d:{amp_long2:.1f}%/40d:{amp_long:.1f}%"

    return {
        "in_lock": in_lock,
        "state": state,
        "close": round(float(close_now), 2),
        "ma20": round(float(ma20), 2),
        "trend_20d": round(trend_20d, 1),
        "amplitude_short_15d": round(amp_short, 1),
        "amplitude_short_20d": round(amp_short2, 1),
        "amplitude_long_20d": round(amp_long2, 1),
        "amplitude_long_40d": round(amp_long, 1),
        "amplitude_30d": round(amp_30, 1),
        "market_return": round(market_ret, 1),
        "relative_strength": round(relative_strength, 1),
        "lock_days": lock_days,
        "verdict": verdict,
        "reason": reason,
    }
