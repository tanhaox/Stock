"""Holding strategy service - extracted from holdings.py (v4.3)."""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("holding_strategy")


async def generate_holding_strategies(holdings: list[dict], uid: str) -> dict:
    """Sector concentration + per-stock strategy + exit signals + chip analysis."""
    if not holdings:
        return {"status": "error", "detail": "No holdings"}

    total_value = sum(h["market_value"] for h in holdings)

    # Sector concentration
    sector_exposure = {}
    for h in holdings:
        try:
            from app.services.sector_heat_engine import get_stock_sector_factor
            sf = await get_stock_sector_factor(h["symbol"])
            sec = sf.get("sector_name", "unknown")
            sector_exposure[sec] = sector_exposure.get(sec, 0) + h["market_value"]
        except Exception:
            sector_exposure["unknown"] = sector_exposure.get("unknown", 0) + h["market_value"]

    concentration_warnings = []
    for sec, val in sector_exposure.items():
        pct = round(val / max(total_value, 1) * 100, 1)
        if pct > 40:
            concentration_warnings.append(f"High concentration: {sec} at {pct}%")
        elif pct > 25:
            concentration_warnings.append(f"Moderate: {sec} at {pct}%")

    # Per-stock strategies
    stock_strategies = []
    for h in holdings:
        sym = h["symbol"]
        st = {"symbol": sym, "name": h["name"], "pnl_pct": h["pnl_pct"],
              "holding_days": h["holding_days"],
              "weight_pct": round(h["market_value"] / max(total_value, 1) * 100, 1)}
        try:
            async with async_session_factory() as s2:
                r2 = await s2.execute(text("""
                    SELECT composite_score, win_probability, signal_quality
                    FROM analysis_scores
                    WHERE symbol = :s ORDER BY scan_date DESC LIMIT 1
                """), {"s": sym})
                row = r2.fetchone()
                if row:
                    st["composite_score"] = float(row[0] or 0)
                    st["win_probability"] = float(row[1] or 0) if row[1] else None
                    st["signal_quality"] = float(row[2] or 0) if row[2] else None

            from app.services.exit_signal_detector import detect_exit_signals
            sigs = await detect_exit_signals(sym, date.today() - timedelta(days=max(h["holding_days"], 5)), h["cost_price"])
            if sigs:
                st["exit_signals"] = sigs
                st["has_critical"] = any(s["priority"] == "critical" for s in sigs)

            # Chip absorption
            try:
                from app.services.chip_analyzer import analyze_chip_absorption
                cr = await analyze_chip_absorption(sym)
                if cr and "absorption" in cr and "error" not in cr.get("absorption", {}):
                    ab = cr["absorption"]
                    st["chip"] = {"ar_ratio": ab["ar_ratio"], "verdict": ab["verdict"],
                                  "trend": ab.get("trend", ""),
                                  "vol_lock_pct": ab.get("chips_lock_pct", ab.get("vol_lock_pct", 0)),
                                  "vol_over_pct": ab.get("chips_over_pct", ab.get("vol_over_pct", 0)),
                                  "summary": cr.get("summary", "")}
            except Exception:
                pass

            # Action recommendation
            score = st.get("composite_score", 50); pnl = h["pnl_pct"]; days = h["holding_days"]
            if pnl > 10: action = "take_profit"
            elif pnl > 5: action = "hold"
            elif pnl < -8 and days > 5: action = "stop_loss"
            elif pnl < -3 and score < 40: action = "reduce_50"
            elif score >= 60 and pnl < 0: action = "add"
            elif score >= 55: action = "hold"
            elif score < 35: action = "reduce_observe"
            else: action = "maintain"
            st["suggested_action"] = action
        except Exception as e:
            st["error"] = str(e)[:100]
        stock_strategies.append(st)

    stock_strategies.sort(key=lambda s: (0 if s.get("has_critical") else 1, -(s.get("pnl_pct", 0))))

    return {
        "status": "success",
        "data": {
            "total_value": round(total_value, 0), "holdings_count": len(holdings),
            "concentration_warnings": concentration_warnings,
            "sector_exposure": {k: round(v, 0) for k, v in sector_exposure.items()},
            "stock_strategies": stock_strategies,
        },
    }
