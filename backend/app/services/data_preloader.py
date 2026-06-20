"""数据预加载 — 从 deep_scorer.py 拆分 (v4.3)."""
import logging
from datetime import date as dt_date
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

_ambush_cache: dict[str, float] = {}
_pattern_cache: dict[str, str] = {}

async def preload_ambush(symbols: list[str], scan_date_str: str):
    """批量预加载潜伏猎手信号."""
    from app.core.database import async_session_factory as _s
    from datetime import date as dt_date
    global _ambush_cache
    _ambush_cache.clear()
    try:
        async with _s() as s:
            r = await s.execute(text(
                "SELECT symbol, composite_score FROM ambush_signals "
                "WHERE scan_date = :d AND symbol = ANY(:syms)"
            ), {"d": dt_date.fromisoformat(scan_date_str), "syms": symbols})
            for row in r.fetchall():
                _ambush_cache[row[0]] = float(row[1] or 0)
    except Exception:
        pass


async def preload_patterns(symbols: list[str], scan_date_str: str):
    """批量预加载形态信号."""
    from app.core.database import async_session_factory as _s
    from datetime import date as dt_date
    global _pattern_cache
    sd = dt_date.fromisoformat(scan_date_str) if isinstance(scan_date_str, str) else scan_date_str
    _pattern_cache.clear()
    async with _s() as s:
        result = await s.execute(text(
            "SELECT ts_code, STRING_AGG(pattern_type, ',') as patterns "
            "FROM pattern_signals WHERE trade_date = :d AND ts_code = ANY(:syms) "
            "GROUP BY ts_code"
        ), {"d": sd, "syms": symbols})
        for row in result.fetchall():
            _pattern_cache[row[0]] = row[1] or ""

