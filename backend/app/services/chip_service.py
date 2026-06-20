"""Tushare 筹码分布数据服务 — cyq_perf + cyq_chips.

v2.1 (2026-06-09): 本地 DB 优先
  - cyq_perf: 优先读 daily_chip_perf 表 (每日同步), DB 无数据才调 API
  - cyq_chips: 保留按需 API 拉取 + 内存缓存 (每日 105 档, 入库成本过高)
  - 批量加载: get_cyq_perf_batch() 供深度评分使用, 一次 SQL 加载全市场

缓存策略:
  - cyq_chips: 模块级内存缓存, 按 (ts_code, trade_date) 键控
  - cyq_perf: DB 优先, 内存缓存为 API fallback 做准备
"""

import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare

logger = logging.getLogger("chip_service")

# ── 内存缓存 (仅 cyq_chips 和 API fallback) ──
_cyq_perf_cache: dict[str, dict] = {}     # key: "ts_code|trade_date"
_cyq_chips_cache: dict[str, list] = {}    # key: "ts_code|trade_date"
_chip_db_cache: dict[str, dict] = {}      # key: "ts_code|trade_date" — DB 查询缓存


def _cache_key(ts_code: str, trade_date: str) -> str:
    return f"{ts_code}|{trade_date}"


# ═══════════════════════════════════════════════════════════
#  cyq_perf: 本地 DB 优先
# ═══════════════════════════════════════════════════════════

async def get_cyq_perf(ts_code: str, trade_date: str | None = None) -> dict | None:
    """获取单只股票某日的筹码性能指标. 优先读本地 DB.

    返回字段:
      his_low, his_high     — 历史最低/最高价
      cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct  — 五分位成本
      weight_avg             — 加权平均成本
      winner_rate            — 获利盘比例 %
    """
    td = trade_date or date.today().isoformat()
    key = _cache_key(ts_code, td)
    td_val = date.fromisoformat(td)  # Python date 对象传递给 SQL

    # 1. 内存缓存
    if key in _chip_db_cache:
        return _chip_db_cache[key]

    # 2. 直接查本地 DB (取 <= target_date 的最新一条)
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, his_low, his_high,
                       cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                       weight_avg, winner_rate
                FROM daily_chip_perf
                WHERE ts_code = :c AND trade_date <= :d
                ORDER BY trade_date DESC LIMIT 1
            """), {"c": ts_code, "d": td_val})
            row = r.fetchone()
            if row is not None:
                wr = row[9]  # winner_rate 列 (0-based)
                if wr is not None:
                    result = {
                    "ts_code": ts_code,
                    "trade_date": str(row[0]),
                    "his_low": float(row[1]) if row[1] is not None else None,
                    "his_high": float(row[2]) if row[2] is not None else None,
                    "cost_5pct": float(row[3]) if row[3] is not None else None,
                    "cost_15pct": float(row[4]) if row[4] is not None else None,
                    "cost_50pct": float(row[5]) if row[5] is not None else None,
                    "cost_85pct": float(row[6]) if row[6] is not None else None,
                    "cost_95pct": float(row[7]) if row[7] is not None else None,
                    "weight_avg": float(row[8]) if row[8] is not None else None,
                    "winner_rate": float(row[9]) if row[9] is not None else None,
                }
                _chip_db_cache[key] = result
                return result
    except Exception as e:
        logger.debug(f"DB chip lookup failed for {ts_code}: {e}")

    # 3. API fallback
    return await _get_cyq_perf_api(ts_code, td)


async def get_cyq_perf_batch(
    symbols: list[str], ref_date: date
) -> dict[str, dict]:
    """批量加载全市场筹码数据 — 供深度评分使用.

    一次 SQL 查询加载所有指定股票的筹码数据,
    返回 {ts_code: cyq_perf_dict} (仅 winner_rate 非 None 的股票).
    """
    if not symbols:
        return {}

    result: dict[str, dict] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT DISTINCT ON (ts_code)
                    ts_code, trade_date,
                    his_low, his_high,
                    cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                    weight_avg, winner_rate
                FROM daily_chip_perf
                WHERE ts_code = ANY(:codes) AND trade_date <= :d
                ORDER BY ts_code, trade_date DESC
            """), {"codes": symbols, "d": ref_date})

            for row in r.fetchall():
                ts = row[0]
                entry = {
                    "ts_code": ts,
                    "trade_date": str(row[1]),
                    "his_low": float(row[2]) if row[2] is not None else None,
                    "his_high": float(row[3]) if row[3] is not None else None,
                    "cost_5pct": float(row[4]) if row[4] is not None else None,
                    "cost_15pct": float(row[5]) if row[5] is not None else None,
                    "cost_50pct": float(row[6]) if row[6] is not None else None,
                    "cost_85pct": float(row[7]) if row[7] is not None else None,
                    "cost_95pct": float(row[8]) if row[8] is not None else None,
                    "weight_avg": float(row[9]) if row[9] is not None else None,
                    "winner_rate": float(row[10]) if row[10] is not None else None,
                }
                if entry["winner_rate"] is not None:
                    result[ts] = entry
                    # 同时存入内存缓存
                    key = _cache_key(ts, str(row[1]))
                    if key not in _chip_db_cache:
                        _chip_db_cache[key] = entry

    except Exception as e:
        logger.warning(f"Batch chip perf load failed: {e}")

    return result


async def _get_cyq_perf_api(ts_code: str, trade_date: str) -> dict | None:
    """API fallback: 单只股票的筹码性能指标."""
    key = _cache_key(ts_code, trade_date)
    if key in _cyq_perf_cache:
        return _cyq_perf_cache[key]

    try:
        rows = await call_tushare("cyq_perf", {"ts_code": ts_code, "trade_date": trade_date},
            "ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")
    except Exception as e:
        logger.warning(f"cyq_perf API failed for {ts_code} @ {trade_date}: {e}")
        return None

    if not rows:
        return None

    result = rows[0]  # 指定日期只返回 1 条
    _cyq_perf_cache[key] = result
    return result


# ═══════════════════════════════════════════════════════════
#  cyq_chips: 按需 API 拉取 + 内存缓存 (不入库, 每日 105 档)
# ═══════════════════════════════════════════════════════════

async def get_cyq_chips(ts_code: str, trade_date: str | None = None) -> list[dict] | None:
    """获取单只股票某日的逐档筹码密度分布. 保留 API 按需拉取."""
    key = _cache_key(ts_code, trade_date or "latest")
    if key in _cyq_chips_cache:
        return _cyq_chips_cache[key]

    params: dict = {"ts_code": ts_code}
    if trade_date:
        params["trade_date"] = trade_date

    try:
        rows = await call_tushare("cyq_chips", params, "ts_code,trade_date,price,percent")
    except Exception as e:
        logger.warning(f"cyq_chips failed for {ts_code} @ {trade_date}: {e}")
        return None

    if not rows:
        return None

    # 过滤到单日
    if trade_date:
        rows = [r for r in rows if r.get("trade_date", "") == trade_date]
    else:
        latest_date = rows[0].get("trade_date", "")
        rows = [r for r in rows if r.get("trade_date", "") == latest_date]

    _cyq_chips_cache[key] = rows
    return rows


# ═══════════════════════════════════════════════════════════
#  cyq_perf 历史趋势 (本地 DB 优先)
# ═══════════════════════════════════════════════════════════

async def get_cyq_history(ts_code: str, days: int = 60) -> dict[str, dict]:
    """获取某只股票近 N 日的筹码性能历史. 优先读本地 DB.

    Returns:
        {trade_date: cyq_perf_dict, ...}  按日期升序
    """
    history: dict[str, dict] = {}

    # 1. 本地 DB
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, trade_date,
                       his_low, his_high,
                       cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
                       weight_avg, winner_rate
                FROM daily_chip_perf
                WHERE ts_code = :c
                ORDER BY trade_date DESC
                LIMIT :lim
            """), {"c": ts_code, "lim": days + 5})  # +5 容错非交易日

            for row in r.fetchall():
                td = str(row[1])
                td_dt = date(int(td[:4]), int(td[4:6]), int(td[6:8]))
                history[td] = {
                    "ts_code": row[0],
                    "trade_date": td,
                    "his_low": float(row[2]) if row[2] is not None else None,
                    "his_high": float(row[3]) if row[3] is not None else None,
                    "cost_5pct": float(row[4]) if row[4] is not None else None,
                    "cost_15pct": float(row[5]) if row[5] is not None else None,
                    "cost_50pct": float(row[6]) if row[6] is not None else None,
                    "cost_85pct": float(row[7]) if row[7] is not None else None,
                    "cost_95pct": float(row[8]) if row[8] is not None else None,
                    "weight_avg": float(row[9]) if row[9] is not None else None,
                    "winner_rate": float(row[10]) if row[10] is not None else None,
                }

            if len(history) >= 10:  # DB 数据足够
                # 限制到最近 N 天
                if len(history) > days:
                    sorted_dates = sorted(history.keys())[-days:]
                    history = {td: history[td] for td in sorted_dates}
                return history

    except Exception as e:
        logger.debug(f"DB history failed for {ts_code}: {e}")

    # 2. API fallback
    try:
        rows = await call_tushare("cyq_perf", {"ts_code": ts_code},
            "ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")
    except Exception as e:
        logger.warning(f"cyq_perf history API failed for {ts_code}: {e}")
        return {}

    if not rows:
        return {}

    for r in rows:
        td = r.get("trade_date", "")
        if not td:
            continue
        history[td] = r

    if len(history) > days:
        sorted_dates = sorted(history.keys())[-days:]
        history = {td: history[td] for td in sorted_dates}

    return history


# ═══════════════════════════════════════════════════════════
#  筹码吸收率计算 (基于 cyq_chips)
# ═══════════════════════════════════════════════════════════

async def compute_chip_absorption_from_cyq(
    ts_code: str,
    lock_bottom: float,
    lock_top: float,
    trade_date: str | None = None,
) -> dict:
    """基于 Tushare cyq_chips 真实筹码数据计算三区筹码集中度.

    将逐档筹码密度按价格归入三个区间:
      Z_LOCK  = [lock_bottom, lock_top]  → 锁死区内筹码
      Z_OVER  = (lock_top, ∞)            → 上方套牢筹码
      Z_BELOW = (-∞, lock_bottom)        → 下方获利筹码
    """
    chips = await get_cyq_chips(ts_code, trade_date)
    if not chips or len(chips) < 10:
        return {"error": "cyq_chips 数据不足", "source": "tushare_cyq"}

    chips_lock = 0.0
    chips_over = 0.0
    chips_below = 0.0

    for c in chips:
        price = float(c.get("price", 0))
        pct = float(c.get("percent", 0))
        if pct <= 0:
            continue

        if lock_bottom <= price <= lock_top:
            chips_lock += pct
        elif price > lock_top:
            chips_over += pct
        else:
            chips_below += pct

    total = chips_lock + chips_over + chips_below
    if total <= 0:
        return {"error": "筹码总量为零", "source": "tushare_cyq"}

    ar_lock = chips_lock / total
    absorb_total = chips_lock + chips_over
    ar_ratio = chips_lock / absorb_total if absorb_total > 0 else 0.0

    if ar_ratio >= 0.60:
        verdict = "强集中"; quality = 10
    elif ar_ratio >= 0.50:
        verdict = "中等集中"; quality = 7
    elif ar_ratio >= 0.35:
        verdict = "弱集中"; quality = 4
    else:
        verdict = "套牢重压"; quality = 2

    return {
        "source": "tushare_cyq",
        "total_chips_pct": round(total, 2),
        "chips_lock": round(chips_lock, 2),
        "chips_over": round(chips_over, 2),
        "chips_below": round(chips_below, 2),
        "chips_lock_pct": round(chips_lock / total * 100, 1),
        "chips_over_pct": round(chips_over / total * 100, 1),
        "chips_below_pct": round(chips_below / total * 100, 1),
        "ar_lock": round(ar_lock, 3),
        "ar_ratio": round(ar_ratio, 3),
        "verdict": verdict,
        "quality": quality,
        "chip_count": len(chips),
    }


async def compute_chip_trend_from_cyq(
    ts_code: str,
    lock_bottom: float,
    lock_top: float,
    lookback_days: int = 60,
) -> dict:
    """基于 cyq_perf 历史数据追踪筹码成本趋势 (本地 DB 优先)."""
    history = await get_cyq_history(ts_code, days=lookback_days)
    if not history or len(history) < 10:
        return {
            "trend": "数据不足",
            "segments": [],
            "verdict": "cyq 历史数据不足",
            "quality": 0,
            "source": "tushare_cyq",
        }

    sorted_dates = sorted(history.keys())
    segments = [
        {
            "date": td,
            "winner_rate": round(float(history[td].get("winner_rate", 0)), 2),
            "cost_50pct": round(float(history[td].get("cost_50pct", 0)), 2),
            "weight_avg": round(float(history[td].get("weight_avg", 0)), 2),
        }
        for td in sorted_dates
    ]

    if len(segments) >= 20:
        recent_wr = [s["winner_rate"] for s in segments[-10:]]
        early_wr = [s["winner_rate"] for s in segments[:10]]
        recent_avg = sum(recent_wr) / len(recent_wr)
        early_avg = sum(early_wr) / len(early_wr)
        wr_change = recent_avg - early_avg

        if wr_change > 8: trend = "快收集筹"
        elif wr_change > 4: trend = "慢收集筹"
        elif wr_change > -4: trend = "筹码稳定"
        elif wr_change > -8: trend = "筹码松动"
        else: trend = "加速派发"
    else:
        trend = "数据累积中"

    latest_seg = segments[-1] if segments else None
    if latest_seg:
        wr = latest_seg["winner_rate"]
        if wr > 70 and trend in ("快收集筹", "慢收集筹"):
            verdict = "强收集"; quality = 9
        elif wr > 50:
            verdict = "中等收集"; quality = 6
        elif wr > 30:
            verdict = "筹码分散"; quality = 4
        else:
            verdict = "套牢深重"; quality = 2
    else:
        verdict = "未知"; quality = 0

    return {
        "trend": trend,
        "segments": segments,
        "verdict": verdict,
        "quality": quality,
        "source": "tushare_cyq",
    }
