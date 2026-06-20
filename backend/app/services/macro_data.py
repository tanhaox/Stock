"""Tushare 宏观数据模块 (P0 系统级).

结构化替代新闻管线中的宏观经济分析层。
零 LLM 成本 — 精确数值替代文本猜测。

数据源:
  宏观基本面: cn_m / cn_cpi / cn_ppi / cn_gdp / cn_pmi
  资金面:     shibor / margin / hk_hold / moneyflow
  新闻事件:   major_news / cctv_news (替代爬虫, Tushare 官方)
  商品价格:   fut_daily
  概念板块:   ths_daily

核心函数:
  sync_macro_cache()       — 全量同步宏观数据到 macro_cache 表
  get_macro_snapshot()     — 获取最新宏观快照
  score_macro_impact()     — 宏观因子对评分的影响
  get_major_news()         — Tushare 官方新闻 (替代爬虫)
  generate_morning_brief() — 数据驱动的早报 (零 LLM 成本)
"""
import asyncio
import logging
from datetime import date, timedelta
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tushare_common import call_tushare

logger = logging.getLogger("macro_data")

# ══════════════════════════════════════════════════════════════════════
# Indicator definitions
# ══════════════════════════════════════════════════════════════════════

INDICATORS = {
    # 宏观基本面 (月频)
    "m2_yoy":       {"api": "cn_m",       "field": "m2_yoy",   "freq": "monthly",  "unit": "%"},
    "m1_yoy":       {"api": "cn_m",       "field": "m1_yoy",   "freq": "monthly",  "unit": "%"},
    "m0_yoy":       {"api": "cn_m",       "field": "m0_yoy",   "freq": "monthly",  "unit": "%"},
    "cpi_yoy":      {"api": "cn_cpi",     "field": "nt_yoy",   "freq": "monthly",  "unit": "%"},
    "cpi_mom":      {"api": "cn_cpi",     "field": "nt_mom",   "freq": "monthly",  "unit": "%"},
    "ppi_yoy":      {"api": "cn_ppi",     "field": "ppi_yoy",  "freq": "monthly",  "unit": "%"},
    "pmi":          {"api": "cn_pmi",     "field": "PMI010000","freq": "monthly",  "unit": "index"},
    "gdp_yoy":      {"api": "cn_gdp",     "field": "gdp_yoy",  "freq": "quarterly","unit": "%"},
    # 资金面 (日频)
    "shibor_on":    {"api": "shibor",     "field": "on",       "freq": "daily",    "unit": "%"},
    "shibor_1w":    {"api": "shibor",     "field": "1w",       "freq": "daily",    "unit": "%"},
    "shibor_1m":    {"api": "shibor",     "field": "1m",       "freq": "daily",    "unit": "%"},
    "shibor_3m":    {"api": "shibor",     "field": "3m",       "freq": "daily",    "unit": "%"},
    "shibor_1y":    {"api": "shibor",     "field": "1y",       "freq": "daily",    "unit": "%"},
    # 杠杆情绪 (日频)
    "margin_balance": {"api": "margin",   "field": "rzye",     "freq": "daily",    "unit": "万元", "agg": "sum"},
    "short_balance":  {"api": "margin",   "field": "rqye",     "freq": "daily",    "unit": "万元", "agg": "sum"},
    # 北向资金 (日频)
    "north_hold_vol": {"api": "hk_hold",  "field": "vol",      "freq": "daily",    "unit": "万股", "agg": "sum"},

    # ── Phase 73: M-1 新指标 ──
    # 宏观货币 (大盘级)
    "m1_m2_scissor":   {"api": "cn_m",    "field": "m1_yoy",    "freq": "monthly", "unit": "%",    "transform": "diff_m2"},
    "shibor_spread":   {"api": "shibor",  "field": "spread",    "freq": "daily",   "unit": "bp",   "computed": True},
    "shibor_3m_chg":   {"api": "shibor",  "field": "3m",        "freq": "daily",   "unit": "%",    "transform": "chg_20d"},
    "lpr_1y":          {"api": "shibor_lpr","field":"1y",       "freq": "daily",   "unit": "%"},
    "lpr_5y":          {"api": "shibor_lpr","field":"5y",       "freq": "daily",   "unit": "%"},
    # 宏观实体 (大盘级)
    "pmi_new_order":   {"api": "cn_pmi",  "field": "PMI010402",  "freq": "monthly", "unit": "index"},
    "pmi_export_order":{"api": "cn_pmi",  "field": "PMI010403",  "freq": "monthly", "unit": "index"},
    "pmi_production":  {"api": "cn_pmi",  "field": "PMI010100",  "freq": "monthly", "unit": "index"},
    "pmi_employment":  {"api": "cn_pmi",  "field": "PMI010200",  "freq": "monthly", "unit": "index"},
    "cpi_core":        {"api": "cn_cpi",  "field": "nt_yoy",     "freq": "monthly", "unit": "%"},
    "ppi_producer":    {"api": "cn_ppi",  "field": "ppi_mp_yoy", "freq": "monthly", "unit": "%"},
    "ppi_consumer":    {"api": "cn_ppi",  "field": "ppi_cg_yoy", "freq": "monthly", "unit": "%"},
    "gdp_pi_yoy":      {"api": "cn_gdp",  "field": "pi_yoy",     "freq": "quarterly","unit":"%"},
    "gdp_si_yoy":      {"api": "cn_gdp",  "field": "si_yoy",     "freq": "quarterly","unit":"%"},
    "gdp_ti_yoy":      {"api": "cn_gdp",  "field": "ti_yoy",     "freq": "quarterly","unit":"%"},
    # 汇率
    "cny_usd":         {"api": "fx_daily","field": "bid",        "freq": "daily",   "unit": "CNY/USD"},
    # 债券
    "bond_3m_yield":   {"api": "gz_index","field": "m3_rate",    "freq": "daily",   "unit": "%"},
    "bond_10y_yield":  {"api": "gz_index","field": "d10_rate",   "freq": "daily",   "unit": "%"},
}

# ── Phase 73: M-1 商品期货同步 ──

COMMODITY_SPECS = {
    "crude_oil":    {"exchange": "INE",  "symbol": "sc",   "contract_hint": "主力"},
    "copper":       {"exchange": "SHFE", "symbol": "cu",   "contract_hint": "主力"},
    "aluminum":     {"exchange": "SHFE", "symbol": "al",   "contract_hint": "主力"},
    "rebar":        {"exchange": "SHFE", "symbol": "rb",   "contract_hint": "主力"},
    "iron_ore":     {"exchange": "DCE",  "symbol": "i",    "contract_hint": "主力"},
    "coke_coal":    {"exchange": "DCE",  "symbol": "jm",   "contract_hint": "主力"},
    "lithium":      {"exchange": "CZCE", "symbol": "lc",   "contract_hint": "主力", "unavailable": True, "note": "Tushare无GFEX广期所碳酸锂/工业硅合约"},
    "silicon":      {"exchange": "CZCE", "symbol": "si",   "contract_hint": "主力", "unavailable": True, "note": "Tushare无GFEX广期所碳酸锂/工业硅合约"},
    "gold":         {"exchange": "SHFE", "symbol": "au",   "contract_hint": "主力"},
    "natural_rubber":{"exchange":"SHFE", "symbol": "ru",   "contract_hint": "主力"},
    "methanol":     {"exchange": "CZCE", "symbol": "ma",   "contract_hint": "主力"},
    "pvc":          {"exchange": "DCE",  "symbol": "v",    "contract_hint": "主力"},
}


async def _sync_commodity_prices():
    """同步商品期货主力合约日线到 macro_cache (M-1 v2: batched loop).

    fut_daily 单次返回上限 2000 行 ~7 天。改为每次拉 30 天分段, 回溯 600 天。
    """
    end_date = date.today()
    total_days = 600
    batch_days = 30
    inserted = 0

    for name, spec in COMMODITY_SPECS.items():
        if spec.get("unavailable"):
            logger.info(f"Commodity {name}: skipped (unavailable on Tushare)")
            continue
        try:
            all_entries: dict[str, list] = {}
            from collections import defaultdict
            lower_sym = spec["symbol"].lower()

            # 分段拉取: 每批 30 天, 共 20 批
            batch_end = end_date
            for _ in range(total_days // batch_days):
                batch_start = batch_end - timedelta(days=batch_days)
                batch_end_str = batch_end.strftime("%Y%m%d")
                batch_start_str = batch_start.strftime("%Y%m%d")

                rows = await call_tushare("fut_daily", {
                    "exchange": spec["exchange"],
                    "start_date": batch_start_str,
                    "end_date": batch_end_str,
                }, "ts_code,trade_date,close,oi")
                await asyncio.sleep(0.3)  # API 节流

                if not rows:
                    batch_end = batch_start
                    continue

                for r in rows:
                    code = r.get("ts_code", "")
                    if not code:
                        continue
                    code_base = code.split(".")[0].rstrip("0123456789")
                    if code_base.lower() != lower_sym:
                        continue
                    td = r.get("trade_date", "")
                    if not td:
                        continue
                    close = r.get("close")
                    oi = r.get("oi", 0) or 0
                    if close is None:
                        continue
                    all_entries.setdefault(td, []).append((float(oi), float(close)))

                batch_end = batch_start

            if not all_entries:
                logger.debug(f"Commodity {name}: no data across all batches")
                continue

            # 写入: 每日期取 oi 最大的合约
            async with async_session_factory() as s:
                for td, entries in all_entries.items():
                    entries.sort(reverse=True)
                    dominant_close = entries[0][1]
                    period = _parse_date(td)
                    if not period:
                        continue
                    ind = f"commodity:{name}"
                    await s.execute(text("""
                        INSERT INTO macro_cache (indicator, period, value)
                        VALUES (:ind, :per, :val)
                        ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
                    """), {"ind": ind, "per": period, "val": dominant_close})
                await s.commit()
                inserted += len(all_entries)
            logger.info(f"Commodity {name}: {len(all_entries)} days synced over {total_days//batch_days} batches")

        except Exception as e:
            logger.warning(f"Commodity {name} sync failed: {e}")

    return inserted


async def _sync_sector_indices():
    """同步 SW 行业指数 + 概念指数到 macro_cache (M-1)."""
    inserted = 0
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=365)).strftime("%Y%m%d")

    # SW 行业指数
    try:
        rows = await call_tushare("sw_daily", {
            "start_date": start, "end_date": end,
        }, "ts_code,trade_date,pct_change")
        if rows:
            async with async_session_factory() as s:
                for r in rows:
                    td = r.get("trade_date", "")
                    period = _parse_date(td)
                    if not period:
                        continue
                    pct = r.get("pct_change")
                    if pct is None:
                        continue
                    code = r.get("ts_code", "")
                    ind = f"sector:{code}"
                    await s.execute(text("""
                        INSERT INTO macro_cache (indicator, period, value)
                        VALUES (:ind, :per, :val)
                        ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
                    """), {"ind": ind, "per": period, "val": float(pct)})
                await s.commit()
                inserted += len(rows)
            logger.info(f"SW sector: {len(rows)} rows synced")
    except Exception as e:
        logger.warning(f"SW sector sync failed: {e}")

    await asyncio.sleep(0.3)

    # 概念指数 (ths_daily)
    concept_codes = {
        "885420.TI": "AI概念", "885431.TI": "新能源汽车", "885571.TI": "光伏概念",
        "885544.TI": "半导体", "885506.TI": "白酒概念", "885573.TI": "医药概念",
        "885700.TI": "军工概念",
    }
    try:
        rows = await call_tushare("ths_daily", {
            "ts_code": ",".join(concept_codes.keys()),
            "start_date": start, "end_date": end,
        }, "ts_code,trade_date,pct_change")
        if rows:
            async with async_session_factory() as s:
                for r in rows:
                    td = r.get("trade_date", "")
                    period = _parse_date(td)
                    if not period:
                        continue
                    pct = r.get("pct_change")
                    if pct is None:
                        continue
                    code = r.get("ts_code", "")
                    label = concept_codes.get(code, code)
                    ind = f"concept:{label}"
                    await s.execute(text("""
                        INSERT INTO macro_cache (indicator, period, value)
                        VALUES (:ind, :per, :val)
                        ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
                    """), {"ind": ind, "per": period, "val": float(pct)})
                await s.commit()
                inserted += len(rows)
            logger.info(f"Concept indices: {len(rows)} rows synced")
    except Exception as e:
        logger.warning(f"Concept sync failed: {e}")

    return inserted


# ══════════════════════════════════════════════════════════════════════
# Data sync
# ══════════════════════════════════════════════════════════════════════

async def ensure_macro_tables():
    """创建 macro_cache 表 (如果不存在)."""
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS macro_cache (
                indicator VARCHAR(50) NOT NULL,
                period DATE NOT NULL,
                value DOUBLE PRECISION,
                source VARCHAR(20) DEFAULT 'tushare',
                fetched_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (indicator, period)
            )
        """))
        await s.commit()
    logger.info("macro_cache table ready")


async def _sync_monthly(name: str, api: str, field: str):
    """同步月度指标."""
    # cn_pmi fields parameter is broken — use empty fields and extract from full response
    fields_str = "" if api == "cn_pmi" else f"month,{field}"
    rows = await call_tushare(api, {
        "start_m": "202301",
        "end_m": date.today().strftime("%Y%m"),
    }, fields_str)
    if not rows:
        return 0

    # cn_pmi returns UPPERCASE field names like MONTH, PMI010000
    lookup_field = field.upper() if api == "cn_pmi" else field
    month_field = "MONTH" if api == "cn_pmi" else "month"

    async with async_session_factory() as s:
        for r in rows:
            m = r.get(month_field, "")
            if len(m) == 6:
                period = date(int(m[:4]), int(m[4:6]), 1)
            else:
                continue
            val = r.get(lookup_field)
            if val is None:
                continue
            await s.execute(text("""
                INSERT INTO macro_cache (indicator, period, value)
                VALUES (:ind, :per, :val)
                ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
            """), {"ind": name, "per": period, "val": float(val)})
        await s.commit()
    return len(rows)


async def _sync_quarterly(name: str, api: str, field: str):
    """同步季度指标 (GDP)."""
    rows = await call_tushare(api, {
        "start_q": "2020Q1",
        "end_q": f"{date.today().year}Q{(date.today().month-1)//3+1}",
    }, f"quarter,{field}")
    if not rows:
        return 0

    async with async_session_factory() as s:
        for r in rows:
            q = r.get("quarter", "")
            if len(q) == 6 and q[4] == 'Q':
                yr = int(q[:4])
                qtr = int(q[5])
                period = date(yr, qtr * 3 - 2, 1)
            else:
                continue
            val = r.get(field)
            if val is None:
                continue
            await s.execute(text("""
                INSERT INTO macro_cache (indicator, period, value)
                VALUES (:ind, :per, :val)
                ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
            """), {"ind": name, "per": period, "val": float(val)})
        await s.commit()
    return len(rows)


async def _sync_daily(name: str, api: str, field: str, agg: str = None):
    """同步日频指标 (shibor/margin/hk_hold)."""
    end = date.today()
    start = end - timedelta(days=90)  # 90 days of daily data

    params: dict = {
        "start_date": start.strftime("%Y%m%d"),
        "end_date": end.strftime("%Y%m%d"),
    }

    rows = await call_tushare(api, params, f"trade_date,{field}" if api != "shibor" else f"date,{field}")

    # Fix: different APIs use different date field names
    date_f = "trade_date"
    if api == "shibor":
        date_f = "date"

    if not rows:
        return 0

    if agg == "sum":
        # Aggregate: sum all rows per date
        from collections import defaultdict
        daily_totals: dict[str, float] = defaultdict(float)
        for r in rows:
            d = r.get(date_f, "")
            if d:
                val = r.get(field)
                if val is not None:
                    daily_totals[d] += float(val)

        async with async_session_factory() as s:
            for d_str, total in daily_totals.items():
                period = _parse_date(d_str)
                if period:
                    await s.execute(text("""
                        INSERT INTO macro_cache (indicator, period, value)
                        VALUES (:ind, :per, :val)
                        ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
                    """), {"ind": name, "per": period, "val": total})
            await s.commit()
        return len(daily_totals)
    else:
        async with async_session_factory() as s:
            count = 0
            for r in rows:
                d = r.get(date_f, "")
                period = _parse_date(d)
                if not period:
                    continue
                val = r.get(field)
                if val is None:
                    continue
                await s.execute(text("""
                    INSERT INTO macro_cache (indicator, period, value)
                    VALUES (:ind, :per, :val)
                    ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()
                """), {"ind": name, "per": period, "val": float(val)})
                count += 1
            await s.commit()
        return count


def _parse_date(d_str: str) -> Optional[date]:
    """Parse various date formats."""
    if not d_str:
        return None
    try:
        if len(d_str) == 8:
            return date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
        if '-' in d_str and len(d_str) == 10:
            return date.fromisoformat(d_str)
    except (ValueError, TypeError):
        pass
    return None


async def sync_macro_cache(progress_cb=None) -> dict:
    """全量同步宏观数据到 macro_cache."""
    await ensure_macro_tables()

    results = {}
    for name, cfg in INDICATORS.items():
        try:
            api = cfg.get("api", "")
            field = cfg.get("field", "")

            # Handle computed indicators
            if cfg.get("computed"):
                if name == "shibor_spread":
                    n = await _sync_shibor_spread()
                else:
                    n = 0
                results[name] = n
                if n > 0:
                    logger.info(f"macro sync {name}: {n} rows")
                continue

            if cfg["freq"] == "monthly":
                n = await _sync_monthly(name, api, field)
            elif cfg["freq"] == "quarterly":
                n = await _sync_quarterly(name, api, field)
            elif cfg["freq"] == "daily":
                if api == "shibor_lpr":
                    n = await _sync_shibor_lpr(name, field)
                elif api == "fx_daily":
                    n = await _sync_fx(name, field)
                elif api == "gz_index":
                    n = await _sync_gz_index(name, field)
                else:
                    n = await _sync_daily(name, api, field, cfg.get("agg"))
            else:
                n = 0
            results[name] = n
            if n > 0:
                logger.info(f"macro sync {name}: {n} rows")
        except Exception as e:
            logger.warning(f"macro sync {name} failed: {e}")
            results[name] = -1

        if progress_cb:
            await progress_cb("macro", 0, 0, f"synced {name}")

    # M-1: 商品 + 板块同步
    try:
        c = await _sync_commodity_prices()
        results["_commodity"] = c
    except Exception as e:
        logger.warning(f"commodity sync failed: {e}")
        results["_commodity"] = -1

    try:
        s = await _sync_sector_indices()
        results["_sector"] = s
    except Exception as e:
        logger.warning(f"sector sync failed: {e}")
        results["_sector"] = -1

    total = sum(v for v in results.values() if v > 0)
    failed = sum(1 for v in results.values() if v < 0)
    return {"status": "success", "total_rows": total, "failed": failed, "indicators": results}


# ── M-1: Special API sync helpers ──

async def _sync_shibor_lpr(name: str, field: str):
    """同步 LPR 数据."""
    rows = await call_tushare("shibor_lpr", {
        "start_date": "20230101",
        "end_date": date.today().strftime("%Y%m%d"),
    }, f"date,{field}")
    if not rows:
        return 0
    async with async_session_factory() as s:
        for r in rows:
            d = r.get("date", "")
            period = _parse_date(d)
            if not period:
                continue
            val = r.get(field)
            if val is None:
                continue
            await s.execute(text(
                "INSERT INTO macro_cache (indicator, period, value) "
                "VALUES (:ind, :per, :val) "
                "ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()"
            ), {"ind": name, "per": period, "val": float(val)})
        await s.commit()
    return len(rows)


async def _sync_fx(name: str, field: str):
    """同步汇率 (筛选 USDCNY)."""
    rows = await call_tushare("fx_daily", {
        "start_date": (date.today() - timedelta(days=90)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
    }, "trade_date,bid_cny,currency")
    if not rows:
        return 0
    async with async_session_factory() as s:
        count = 0
        for r in rows:
            if r.get("currency", "") != "USDCNY":
                continue
            d = r.get("trade_date", "")
            period = _parse_date(d)
            if not period:
                continue
            val = r.get("bid_cny") or r.get(field)
            if val is None:
                continue
            await s.execute(text(
                "INSERT INTO macro_cache (indicator, period, value) "
                "VALUES (:ind, :per, :val) "
                "ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()"
            ), {"ind": name, "per": period, "val": float(val)})
            count += 1
        await s.commit()
    return count


async def _sync_gz_index(name: str, field: str):
    """同步国债收益率 — gz_index 数据只到 2019年."""
    rows = await call_tushare("gz_index", {
        "start_date": (date.today() - timedelta(days=90)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
    }, f"trade_date,{field}")

    # gz_index 数据只到 2019年，如果最近 90天没数据，用全部数据中最新的一条
    if not rows:
        rows = await call_tushare("gz_index", {}, f"trade_date,{field}")

    if not rows:
        return 0
    async with async_session_factory() as s:
        count = 0
        for r in rows:
            d = r.get("trade_date", "")
            period = _parse_date(d)
            if not period:
                continue
            val = r.get(field)
            if val is None:
                continue
            await s.execute(text(
                "INSERT INTO macro_cache (indicator, period, value) "
                "VALUES (:ind, :per, :val) "
                "ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()"
            ), {"ind": name, "per": period, "val": float(val)})
            count += 1
        await s.commit()
    return count


async def _sync_shibor_spread():
    """计算 shibor 3M - ON 利差."""
    rows = await call_tushare("shibor", {
        "start_date": (date.today() - timedelta(days=90)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
    }, "date,on,3m")
    if not rows:
        return 0
    async with async_session_factory() as s:
        count = 0
        for r in rows:
            d = r.get("date", "")
            period = _parse_date(d)
            if not period:
                continue
            on_val = r.get("on")
            m3_val = r.get("3m")
            if on_val is None or m3_val is None:
                continue
            spread = (float(m3_val) - float(on_val)) * 100  # bp
            await s.execute(text(
                "INSERT INTO macro_cache (indicator, period, value) "
                "VALUES (:ind, :per, :val) "
                "ON CONFLICT (indicator, period) DO UPDATE SET value=EXCLUDED.value, fetched_at=NOW()"
            ), {"ind": "shibor_spread", "per": period, "val": spread})
            count += 1
        await s.commit()
    return count


# ══════════════════════════════════════════════════════════════════════
# Query API
# ══════════════════════════════════════════════════════════════════════

async def get_macro_snapshot(session=None) -> dict[str, dict]:
    """获取最新宏观快照: {indicator: {period, value, direction, signal}}

    v4.8: 增加 prev_value 和 change 字段, 用于前端显示涨跌方向.
    """
    async def _query(s):
        r = await s.execute(text("""
            SELECT DISTINCT ON (indicator) indicator, period, value
            FROM macro_cache
            ORDER BY indicator, period DESC
        """))
        return r.fetchall()

    # 批量获取每个 indicator 的前一期值
    async def _prev_query(s, indicators):
        prev_map = {}
        for ind in indicators:
            r = await s.execute(text("""
                SELECT value FROM macro_cache
                WHERE indicator = :ind ORDER BY period DESC OFFSET 1 LIMIT 1
            """), {"ind": ind})
            row = r.fetchone()
            if row and row[0] is not None:
                prev_map[ind] = float(row[0])
        return prev_map

    if session:
        rows = await _query(session)
        prev_map = await _prev_query(session, [row[0] for row in rows])
    else:
        async with async_session_factory() as s:
            rows = await _query(s)
            prev_map = await _prev_query(s, [row[0] for row in rows])

    snapshot = {}
    for row in rows:
        name = row[0]
        cfg = INDICATORS.get(name, {})
        val = float(row[2]) if row[2] is not None else 0.0
        prev_val = prev_map.get(name)

        # Determine direction (bullish/bearish/neutral) based on indicator type
        direction = "neutral"
        if name in ("m2_yoy", "m1_yoy", "pmi", "gdp_yoy", "margin_balance"):
            # Higher = expansionary/bullish
            direction = "bullish" if val > 0 else "bearish"
        elif name in ("cpi_yoy", "ppi_yoy", "shibor_on", "shibor_1y"):
            # Moderate is good; extreme high/low = problem
            if name in ("cpi_yoy", "ppi_yoy"):
                direction = "bullish" if 0 < val < 3 else ("neutral" if 3 <= val < 5 else "bearish")
            else:
                direction = "bullish" if val < 2.0 else ("neutral" if val < 4.0 else "bearish")

        entry = {
            "period": str(row[1]),
            "value": round(val, 4),
            "direction": direction,
            "unit": cfg.get("unit", ""),
        }
        if prev_val is not None:
            entry["prev_value"] = round(prev_val, 4)
            entry["change"] = round(val - prev_val, 4)
        snapshot[name] = entry
    return snapshot


async def get_indicator_history(indicator: str, months: int = 24) -> list[dict]:
    """获取单个指标的历史序列."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT period, value FROM macro_cache
            WHERE indicator = :ind
            ORDER BY period DESC
            LIMIT :lim
        """), {"ind": indicator, "lim": months})
        return [{"period": str(row[0]), "value": float(row[1]) if row[1] else 0} for row in r.fetchall()]


# ══════════════════════════════════════════════════════════════════════
# Scoring integration
# ══════════════════════════════════════════════════════════════════════

async def score_macro_impact(session=None) -> tuple[float, dict]:
    """评估宏观环境对评分的影响 (返回: adjustment_value, diagnostics).

    正值 = 宏观偏多 (加分), 负值 = 宏观偏空 (减分).
    范围: -5.0 ~ +5.0

    考虑维度:
      - 货币环境 (M2/利率): 40% 权重
      - 通胀压力 (CPI/PPI): 20%
      - 经济景气 (PMI/GDP): 25%
      - 杠杆情绪 (融资/北向): 15%
    """
    snapshot = await get_macro_snapshot(session)
    if not snapshot:
        return 0.0, {"status": "no_macro_data"}

    score = 0.0
    details = {}

    # 1. 货币环境 (40%)
    m2 = snapshot.get("m2_yoy", {}).get("value", 0)
    shibor_3m = snapshot.get("shibor_3m", {}).get("value", 0)

    if m2 > 10:
        score += 1.5  # 宽松
    elif m2 > 7:
        score += 0.8  # 适度
    elif m2 > 5:
        score += 0.0  # 中性
    else:
        score -= 1.0  # 收紧

    if shibor_3m < 1.5:
        score += 0.5  # 资金充裕
    elif shibor_3m < 2.5:
        score += 0.0
    else:
        score -= 0.5  # 资金紧张

    details["monetary"] = {"m2_yoy": m2, "shibor_3m": shibor_3m, "impact": round(score, 2)}

    # 2. 通胀压力 (20%)
    cpi = snapshot.get("cpi_yoy", {}).get("value", 0)
    ppi = snapshot.get("ppi_yoy", {}).get("value", 0)

    if 0 < cpi < 2 and ppi > 0:
        score += 1.0  # 温和通胀 + PPI 正 = 良性
    elif cpi < 0 or ppi < 0:
        score -= 1.5  # 通缩风险
    elif cpi > 5:
        score -= 1.0  # 高通胀压力

    details["inflation"] = {"cpi_yoy": cpi, "ppi_yoy": ppi, "impact": round(score - details["monetary"]["impact"], 2)}

    # 3. 经济景气 (25%)
    pmi = snapshot.get("pmi", {}).get("value", 0)
    gdp = snapshot.get("gdp_yoy", {}).get("value", 0)

    if pmi > 50.5:
        score += 0.8  # 扩张
    elif pmi > 50:
        score += 0.2  # 临界
    elif pmi > 49:
        score -= 0.3  # 收缩
    else:
        score -= 1.0  # 明显收缩

    if gdp > 5.5:
        score += 0.5
    elif gdp < 4.0:
        score -= 0.5

    details["economy"] = {"pmi": pmi, "gdp_yoy": gdp, "impact": round(score - 2.0, 2)}

    # 4. 杠杆情绪 (15%)
    margin = snapshot.get("margin_balance", {}).get("value", 0)
    north = snapshot.get("north_hold_vol", {}).get("value", 0)

    # margin in 万元 → 亿元
    margin_yi = margin / 1e8 if margin > 1e8 else 0
    if margin_yi > 16000:
        score += 0.5  # 高杠杆 = 情绪亢奋
    elif margin_yi > 14000:
        score += 0.2
    elif margin_yi < 12000:
        score -= 0.3  # 低杠杆 = 情绪谨慎

    details["sentiment"] = {"margin_yi": round(margin_yi, 0), "north_hold": round(north, 0),
                            "impact": round(score - sum(v.get("impact", 0) for v in details.values()), 2)}

    adjustment = round(max(-5.0, min(5.0, score)), 2)
    return adjustment, {"score": adjustment, "details": details, "status": "ok"}


# ══════════════════════════════════════════════════════════════════════
# Sector-macro exposure mapping
# ══════════════════════════════════════════════════════════════════════

SECTOR_MACRO_EXPOSURE = {
    # 银行: 利率敏感, 融资敏感
    "银行":     {"shibor_1y": 1.5, "m2_yoy": 1.0, "margin_balance": 0.5},
    # 房地产: 利率高度敏感, M2 高度敏感
    "房地产":   {"shibor_1y": 2.0, "m2_yoy": 1.5, "shibor_on": 0.5},
    # 有色金属: PPI + 商品价格敏感
    "有色金属": {"ppi_yoy": 2.0, "cpi_yoy": 0.5},
    # 煤炭: PPI 敏感
    "煤炭":     {"ppi_yoy": 1.5},
    # 汽车: 利率敏感, PMI 敏感
    "汽车":     {"shibor_1y": 1.0, "pmi": 1.0, "m2_yoy": 0.5},
    # 食品饮料: CPI 受益
    "食品饮料": {"cpi_yoy": 1.5},
    # 电子/半导体: PMI 领先指标
    "电子":     {"pmi": 1.0, "shibor_3m": 0.5},
    # 计算机/AI: 利率敏感 (成长型), PMI
    "计算机":   {"shibor_1y": 1.0, "pmi": 0.5},
    # 公用事业/电力: 防御型, 利率影响低
    "公用事业": {"shibor_1y": -0.5},
    # 医药生物: 防御型
    "医药生物": {},
    # 钢铁: PPI + M2 基建驱动
    "钢铁":     {"ppi_yoy": 1.5, "m2_yoy": 1.0},
    # 基础化工: PPI
    "基础化工": {"ppi_yoy": 1.0},
    # 机械设备: PMI + M2
    "机械设备": {"pmi": 1.0, "m2_yoy": 0.5},
    # 通信: 防御+政策
    "通信":     {"pmi": 0.5},
    # 石油石化: PPI
    "石油石化": {"ppi_yoy": 1.0},
}

DEFAULT_EXPOSURE = {"pmi": 0.3, "m2_yoy": 0.3, "shibor_1y": -0.2}


async def get_sector_macro_exposure(sector: str) -> dict[str, float]:
    """获取指定板块对宏观变量的暴露系数."""
    return SECTOR_MACRO_EXPOSURE.get(sector, DEFAULT_EXPOSURE)


async def compute_sector_macro_score(sector: str, session=None) -> tuple[float, dict]:
    """计算板块在当前宏观环境下的得分.

    Returns:
        (score, breakdown): score 范围 -3 ~ +3
    """
    exposure = await get_sector_macro_exposure(sector)
    snapshot = await get_macro_snapshot(session)

    total = 0.0
    breakdown = {}
    for indicator, weight in exposure.items():
        snap = snapshot.get(indicator, {})
        val = snap.get("value", 0)
        direction = snap.get("direction", "neutral")

        # Normalize: positive effect * weight
        effect = (1.0 if direction == "bullish" else (-1.0 if direction == "bearish" else 0.0))
        impact = round(effect * weight, 2)
        total += impact
        breakdown[indicator] = {"value": val, "exposure": weight, "effect": effect, "impact": impact}

    return round(max(-3.0, min(3.0, total)), 2), breakdown


# ══════════════════════════════════════════════════════════════════════
# Tushare 官方新闻 (替代爬虫)
# ══════════════════════════════════════════════════════════════════════

async def get_major_news(days: int = 7) -> list[dict]:
    """从 Tushare major_news API 获取近期重大新闻.

    替代 4 源爬虫 (xq/sina/jinrongjie/fenghuang).
    800 篇/周, Tushare 官方源, 自带 ts_code.

    Returns:
        [{title, content, source, datetime, ts_code}, ...]
    """
    from datetime import date as dt_date, timedelta

    end = dt_date.today()
    start = end - timedelta(days=days)

    try:
        rows = await call_tushare("major_news", {
            "start_date": start.strftime("%Y%m%d"),
            "end_date": end.strftime("%Y%m%d"),
        }, "title,content,pub_time,src")
        if not rows:
            return []

        results = []
        for r in rows:
            title = r.get("title", "")
            content = r.get("content", "")
            src = r.get("src", "")
            pub = r.get("pub_time", "")
            results.append({
                "title": title,
                "content": content[:500] if content else "",
                "source": src or "tushare",
                "datetime": pub,
            })
        return results
    except Exception as e:
        logger.warning(f"major_news fetch failed: {e}")
        return []


async def get_cctv_news(days: int = 7) -> list[dict]:
    """从 Tushare cctv_news API 获取 CCTV 联播新闻."""
    from datetime import date as dt_date, timedelta

    end = dt_date.today()
    start = end - timedelta(days=days)
    results = []
    current = start
    while current <= end:
        try:
            rows = await call_tushare("cctv_news", {
                "date": current.strftime("%Y%m%d"),
            }, "date,title,content")
            if rows:
                for r in rows:
                    results.append({
                        "date": r.get("date", ""),
                        "title": r.get("title", ""),
                        "content": r.get("content", "")[:300],
                    })
        except Exception:
            pass
        current += timedelta(days=1)
    return results


# ══════════════════════════════════════════════════════════════════════
# 早报模板 (零 LLM 成本)
# ══════════════════════════════════════════════════════════════════════

async def generate_morning_brief() -> dict:
    """生成数据驱动的早报 (模板 + 数据填充, 零 LLM 成本).

    Returns:
        {sections: [{title, items}], macro_summary: str, generated_at: str}
    """
    from datetime import date as dt_date

    snapshot = await get_macro_snapshot()

    sections = []

    # ── 宏观环境 ──
    macro_items = []
    m2 = snapshot.get("m2_yoy", {}).get("value", 0)
    cpi = snapshot.get("cpi_yoy", {}).get("value", 0)
    pmi = snapshot.get("pmi", {}).get("value", 0)
    gdp = snapshot.get("gdp_yoy", {}).get("value", 0)

    if m2:
        tone = "宽松" if m2 > 9 else ("适度" if m2 > 7 else "偏紧")
        macro_items.append(f"M2 同比 {m2}% — 货币环境{tone}")
    if cpi:
        tone = "温和" if 0 < cpi < 3 else ("通缩风险" if cpi < 0 else "通胀压力")
        macro_items.append(f"CPI 同比 {cpi}% — 物价{tone}")
    if pmi and pmi > 0:
        tone = "扩张" if pmi > 50 else ("收缩" if pmi < 50 else "临界")
        macro_items.append(f"PMI {pmi} — 制造业{tone}")
    if gdp:
        macro_items.append(f"GDP 同比 {gdp}%")
    if macro_items:
        sections.append({"title": "宏观环境", "items": macro_items})

    # ── 资金面 ──
    flow_items = []
    shibor_on = snapshot.get("shibor_on", {}).get("value", 0)
    shibor_1y = snapshot.get("shibor_1y", {}).get("value", 0)
    margin = snapshot.get("margin_balance", {}).get("value", 0)

    if shibor_on:
        flow_items.append(f"隔夜拆借 {shibor_on}%")
    if shibor_1y:
        flow_items.append(f"1年期 {shibor_1y}%")
    if margin and margin > 1e8:
        margin_yi = margin / 1e8
        tone = "亢奋" if margin_yi > 16000 else ("正常" if margin_yi > 13000 else "谨慎")
        flow_items.append(f"融资余额 {margin_yi:.0f} 亿 — 情绪{tone}")
    if flow_items:
        sections.append({"title": "资金面", "items": flow_items})

    # ── 政策要闻 (CCTV) ──
    cctv = await get_cctv_news(days=3)
    if cctv:
        cctv_items = [f"{n['title']}" for n in cctv[:5]]
        sections.append({"title": "政策要闻 (CCTV)", "items": cctv_items})

    # ── 重大新闻 (Tushare major_news) ──
    major = await get_major_news(days=3)
    if major:
        news_items = [f"{n['title'][:80]}" for n in major[:8]]
        sections.append({"title": "重大新闻", "items": news_items})

    # ── 宏观一句话 ──
    adj, _ = await score_macro_impact()
    if adj > 1.0:
        macro_summary = "宏观偏多 — 货币宽松 + 经济数据向好，整体环境有利于A股"
    elif adj > 0.3:
        macro_summary = "宏观中性偏多 — 经济基本面稳定，需关注结构性机会"
    elif adj > -0.3:
        macro_summary = "宏观中性 — 多空因素交织，建议谨慎操作"
    elif adj > -1.0:
        macro_summary = "宏观偏空 — 经济数据走弱，建议降低仓位"
    else:
        macro_summary = "宏观明确偏空 — 多重不利因素叠加，建议减仓观望"

    return {
        "sections": sections,
        "macro_summary": macro_summary,
        "macro_score": adj,
        "generated_at": dt_date.today().isoformat(),
    }
