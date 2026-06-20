"""SXQS signal computation service — v4.6 with price trend safety gate."""
import numpy as np
import logging

logger = logging.getLogger("sxqs_signal")

TIMING_ADJUST = {
    "trend_up": 0, "structural": -1, "range": -2, "stable": -2,
    "shrinking": -3, "weak_bottom": -4, "panic": -5,
}


async def compute_sxqs_signal(ts_code: str, market_factor: float = 1.0) -> dict | None:
    """SXQS signal + ZIG pattern + price trend safety gate (v4.6)."""
    from app.services.alphaflow_features import compute_sxqs_features
    from app.core.database import async_session_factory
    from sqlalchemy import text

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT close, high, low FROM daily_kline "
            "WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 120"
        ), {"c": ts_code})
        rows = list(reversed(r.fetchall()))

    if len(rows) < 30:
        return None

    closes = np.array([float(row[0] or 0) for row in rows])
    highs = np.array([float(row[1] or closes[i]) for i, row in enumerate(rows)])
    lows = np.array([float(row[2] or closes[i]) for i, row in enumerate(rows)])
    n = len(closes)

    sxqs = compute_sxqs_features(closes, highs, lows)

    d_sig = sxqs.get("d_signal", 0)
    w_sig = sxqs.get("w_signal", 0)
    h1h2 = sxqs.get("h1h2_up", 0)
    net_power = sxqs.get("net_power", 0)

    if d_sig > 0 and h1h2 > 0:
        raw_label = "buy"
    elif w_sig > 0:
        raw_label = "sell"
    elif h1h2 > 0 and net_power > 0:
        raw_label = "strong"
    elif h1h2 > 0:
        raw_label = "hold"
    else:
        raw_label = "watch"

    # Market timing adjustment
    if market_factor < 0.85 and raw_label == "buy":
        label = "buy_light"
    elif market_factor < 0.75 and raw_label == "buy":
        label = "buy_wait"
    else:
        label = raw_label

    # ── v4.6 Price trend safety gate ──
    # SXQS only analyzes ZIG/H1-H2 patterns. In a lockup (tight
    # range, low vol) a single volume bar can trigger a false ZIG
    # turning point. Check actual price trend before trusting the signal.
    if raw_label in ("buy", "strong", "hold"):
        try:
            ma20 = float(np.mean(closes[-20:])) if n >= 20 else closes[-1]
            close_now = closes[-1]
            close_20d_ago = closes[-20] if n >= 20 else closes[0]
            trend_20d = (close_now - close_20d_ago) / close_20d_ago * 100 if close_20d_ago > 0 else 0
            # If price is below MA20 and trending down, override to sell
            if close_now < ma20 and trend_20d < 0:
                raw_label = "sell"
                label = "sell"
        except Exception:
            pass

    return {
        "signal": raw_label, "label": label,
        "d_signal": d_sig, "w_signal": w_sig,
        "net_power": round(float(net_power), 1),
        "market_factor": market_factor,
    }
