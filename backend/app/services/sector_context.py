"""Sector context service — 一次性加载板块上下文 (v4.9 Phase 26e).

提供三层数据:
  1. 大盘相对强弱 (从 index_daily)
  2. 板块趋势 (从 sector_trend)
  3. 个股→板块映射 (从 scan_results.industry + sw_sector_index)
"""
import logging
from datetime import date, timedelta
from sqlalchemy import text

logger = logging.getLogger(__name__)

# SW 28 行业代码 → 中文名索引 (从 sw_sector_index 拉取, 用于匹配 scan_results.industry)
_sector_name_to_code: dict[str, str] | None = None


async def _ensure_sector_name_map(session) -> dict[str, str]:
    """Build industry_name → sector_code mapping from sw_sector_index."""
    global _sector_name_to_code
    if _sector_name_to_code is not None:
        return _sector_name_to_code

    r = await session.execute(text(
        "SELECT DISTINCT index_code, name FROM sw_sector_index WHERE name IS NOT NULL"
    ))
    _sector_name_to_code = {}
    for row in r.fetchall():
        code, name = row[0], row[1]
        if name:
            _sector_name_to_code[name] = code
    logger.debug(f"Sector name map: {len(_sector_name_to_code)} entries")
    return _sector_name_to_code


def _fuzzy_match(industry: str | None, name_map: dict[str, str]) -> str | None:
    """Match scan_results.industry to sw_sector_index.name → return sector_code."""
    if not industry:
        return None
    # Exact match
    if industry in name_map:
        return name_map[industry]
    # Substring match: "医药生物" matches "医药生物"
    for name, code in name_map.items():
        if industry in name or name in industry:
            return code
    return None


async def load_sector_context(session, scan_date, symbols: list[str]) -> dict:
    """一次性加载板块上下文.

    Returns:
        {
            "market_5d": float,       # 上证 5 日涨跌幅%
            "market_20d": float,      # 上证 20 日涨跌幅%
            "stock_sector": {         # 个股→板块趋势映射
                "000001.SZ": {
                    "sector_code": "801780.SI", "sector_name": "银行",
                    "pct_5d": 1.2, "pct_10d": 3.5, "pct_20d": 5.1,
                    "rank_5d": 3, "direction": "上升",
                    "lifecycle": "发酵", "vol_ratio": 1.5,
                    "stock_5d": 2.3,  # 个股自身 5 日涨幅
                },
            },
            "sector_rankings": {},
        }
    """
    if isinstance(scan_date, str):
        scan_date = date.fromisoformat(scan_date)

    market_5d = 0.0
    market_20d = 0.0

    # ── 1. 大盘涨跌幅 (从 index_daily) ──
    try:
        r = await session.execute(text("""
            SELECT close FROM index_daily
            WHERE ts_code = '700001.TI' AND trade_date IN (:d0, :d5, :d20)
            ORDER BY trade_date DESC
        """), {
            "d0": scan_date,
            "d5": scan_date - timedelta(days=10),  # 约 5 个交易日
            "d20": scan_date - timedelta(days=35),  # 约 20 个交易日
        })
        closes = [float(row[0] or 0) for row in r.fetchall()]
        if len(closes) >= 2 and closes[-1] > 0:
            market_5d = round((closes[0] - closes[-1]) / closes[-1] * 100, 2)
            # 精确: 用实际的 5 日前和 20 日前 close
            # 这里用最近 N 天前的最旧值近似
    except Exception as e:
        logger.debug(f"Market index query failed: {e}")

    # ── 2. 更精确地计算大盘 5d/20d (用窗口函数) ──
    try:
        r = await session.execute(text("""
            WITH ranked AS (
                SELECT trade_date, close,
                       LAG(close, 5) OVER (ORDER BY trade_date) as close_5d_ago,
                       LAG(close, 20) OVER (ORDER BY trade_date) as close_20d_ago
                FROM index_daily
                WHERE ts_code = '700001.TI' AND trade_date <= :d
                ORDER BY trade_date DESC LIMIT 1
            )
            SELECT close, close_5d_ago, close_20d_ago FROM ranked
        """), {"d": scan_date})
        row = r.fetchone()
        if row:
            close_now = float(row[0] or 0)
            c5 = float(row[1] or 0)
            c20 = float(row[2] or 0)
            if c5 > 0:
                market_5d = round((close_now - c5) / c5 * 100, 2)
            if c20 > 0:
                market_20d = round((close_now - c20) / c20 * 100, 2)
    except Exception:
        pass

    # ── 3. 所有板块最新趋势 ──
    sector_trends: dict[str, dict] = {}
    sector_rankings: dict[str, dict] = {}
    try:
        r = await session.execute(text("""
            SELECT sector_code, pct_5d, pct_10d, pct_20d,
                   rank_5d, direction, lifecycle, vol_ratio
            FROM sector_trend
            WHERE trade_date = (SELECT MAX(trade_date) FROM sector_trend
                                WHERE trade_date <= :d)
        """), {"d": scan_date})
        for row in r.fetchall():
            code = row[0]
            entry = {
                "sector_code": code,
                "pct_5d": round(float(row[1]) if row[1] is not None else market_5d, 2),
                "pct_10d": round(float(row[2]) if row[2] is not None else 0, 2),
                "pct_20d": round(float(row[3]) if row[3] is not None else market_20d, 2),
                "rank_5d": row[4] or 16,
                "direction": row[5] or "震荡",
                "lifecycle": row[6] or "正常",
                "vol_ratio": round(float(row[7]) if row[7] is not None else 1.0, 2),
            }
            sector_trends[code] = entry
            sector_rankings[code] = {"rank_5d": row[4] or 16, "pct_5d": round(float(row[1]) if row[1] is not None else 0, 2)}
    except Exception as e:
        logger.debug(f"Sector trend query failed: {e}")

    # ── 4. 个股→板块映射 (从 scan_results.industry) ──
    # Phase 45: 用逐股 LIMIT 6 查询替代 WITH ranked LAG (索引命中, 0.09s/30stocks vs 15.77s)
    SYMBOL_LIMIT = 300
    sample_symbols = symbols[:SYMBOL_LIMIT]
    name_map = await _ensure_sector_name_map(session)

    stock_sector: dict[str, dict] = {}
    try:
        # Get industry per stock from scan_results
        r = await session.execute(text(
            "SELECT symbol, industry, close_price FROM scan_results "
            "WHERE scan_date = :d AND symbol = ANY(:syms)"
        ), {"d": scan_date, "syms": sample_symbols})
        stock_info = {}
        for row in r.fetchall():
            stock_info[row[0]] = (row[1], float(row[2] or 0))

        # Per-stock 5d return via indexed query (Phase 45: LIMIT 6 per stock, 0.003s each)
        stock_5d_map: dict[str, float] = {}
        for sym in sample_symbols[:300]:  # cap at 300 to stay fast
            try:
                r2 = await session.execute(text(
                    "SELECT close FROM daily_kline "
                    "WHERE ts_code = :s AND trade_date <= :d "
                    "ORDER BY trade_date DESC LIMIT 6"
                ), {"s": sym, "d": scan_date})
                closes = [float(row[0] or 0) for row in r2.fetchall()]
                if len(closes) >= 6 and closes[5] > 0:
                    stock_5d_map[sym] = round((closes[0] - closes[5]) / closes[5] * 100, 2)
                else:
                    stock_5d_map[sym] = 0.0
            except Exception:
                stock_5d_map[sym] = 0.0

        for sym in symbols:
            info = stock_info.get(sym)
            industry = info[0] if info else None
            st_5d = stock_5d_map.get(sym, 0.0)

            sc = _fuzzy_match(industry, name_map) if industry else None
            trend = sector_trends.get(sc, {}) if sc else {}

            stock_sector[sym] = {
                "sector_code": sc or "unknown",
                "sector_name": industry or "unknown",
                "stock_5d": st_5d,
                "pct_5d": trend.get("pct_5d", market_5d) if trend else market_5d,
                "pct_10d": trend.get("pct_10d", 0) if trend else 0,
                "pct_20d": trend.get("pct_20d", market_20d) if trend else market_20d,
                "rank_5d": trend.get("rank_5d", 16) if trend else 16,
                "direction": trend.get("direction", "震荡") if trend else "震荡",
                "lifecycle": trend.get("lifecycle", "正常") if trend else "正常",
                "vol_ratio": trend.get("vol_ratio", 1.0) if trend else 1.0,
            }
    except Exception as e:
        logger.debug(f"Stock-sector mapping failed: {e}")
        for sym in symbols:
            stock_sector[sym] = {
                "sector_code": "unknown", "sector_name": "unknown",
                "stock_5d": 0, "pct_5d": market_5d, "pct_10d": 0,
                "pct_20d": market_20d, "rank_5d": 16,
                "direction": "震荡", "lifecycle": "正常", "vol_ratio": 1.0,
            }

    return {
        "market_5d": market_5d,
        "market_20d": market_20d,
        "stock_sector": stock_sector,
        "sector_rankings": sector_rankings,
    }
