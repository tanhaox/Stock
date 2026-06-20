"""形态扫描引擎 — 全市场并行扫描, 独立于TG."""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import async_session_factory
from app.services.pattern_scanner import scan_single_stock

logger = logging.getLogger(__name__)


async def get_stock_list() -> list[str]:
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT ts_code FROM daily_kline"))
        return [row[0] for row in r.fetchall()]


async def save_pattern_signals(session: AsyncSession, ts_code: str, trade_date: date, patterns: list[dict]):
    if not patterns:
        return
    for p in patterns:
        await session.execute(text("""
            INSERT INTO pattern_signals (ts_code, trade_date, pattern_type, pattern_score, confidence, details)
            VALUES (:ts, :td, :pt, :ps, :cf, CAST(:dt AS jsonb))
            ON CONFLICT (ts_code, trade_date, pattern_type)
            DO UPDATE SET pattern_score=:ps, confidence=:cf, details=CAST(:dt AS jsonb)
        """), {
            "ts": ts_code, "td": trade_date,
            "pt": p["pattern_type"], "ps": p["pattern_score"],
            "cf": p["confidence"], "dt": __import__("json").dumps(p.get("details", {})),
        })
    await session.commit()


async def run_pattern_scan(progress_callback=None, max_stocks: int = 0) -> dict:
    """全市场形态扫描."""
    stocks = await get_stock_list()
    total = len(stocks)
    if max_stocks > 0:
        stocks = stocks[:max_stocks]
        total = len(stocks)

    today = date.today()
    results = {"scanned": 0, "with_patterns": 0, "total_patterns": 0}

    async with async_session_factory() as s:
        for i, ts_code in enumerate(stocks):
            try:
                patterns = await scan_single_stock(ts_code, today)
                if patterns:
                    await save_pattern_signals(s, ts_code, today, patterns)
                    results["with_patterns"] += 1
                    results["total_patterns"] += len(patterns)
                results["scanned"] += 1
            except Exception:
                pass

            if progress_callback and (i + 1) % 500 == 0:
                await progress_callback("pattern_scan", i + 1, total,
                                        extra=f"形态扫描 {i+1}/{total}, 发现 {results['total_patterns']} 个形态")

    return results
