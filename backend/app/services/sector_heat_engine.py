"""板块热度引擎 v2.0 — 全维度板块分析 + 主题生命周期.

v2.0 升级 (2026-05-31):
  - 主题生命周期五阶段: 萌芽→发酵→高潮→分化→退潮
  - 31 申万行业近 5/10/20 日涨幅排名
  - 板块资金净流入趋势 (基于龙虎榜)
  - 输出 sector_factor (0~2): 板块β, 注入 deep_scorer 加权
"""
import asyncio, logging
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("sector_heat")


# ═══════════════════════════════════════════════════════════
# 主题生命周期五阶段
# ═══════════════════════════════════════════════════════════

async def detect_theme_lifecycle() -> dict[str, dict]:
    """检测各行业板块的主题生命周期阶段.

    算法:
      - 萌芽: 板块近5日首次进入涨幅前10, 且近20日排名在中游
      - 发酵: 连续3日板块内涨停数增加, 跟风股启动
      - 高潮: 龙头连续涨停, 板块内涨停>5只, 成交额暴增
      - 分化: 龙头继续但跟风开始下跌, 板块内涨跌比 < 50%
      - 退潮: 龙头开板/暴跌, 板块跌幅前10, 资金净流出
    """
    async with async_session_factory() as s:
        # 取最近 20 天的龙虎榜 + 申万指数数据
        r = await s.execute(text("""
            SELECT index_code, trade_date, pct_chg, close
            FROM sw_sector_index
            WHERE trade_date >= CURRENT_DATE - 30
            ORDER BY index_code, trade_date DESC
        """))
        rows = r.fetchall()

    if not rows:
        return {}

    # 按行业分组
    by_sector = defaultdict(list)
    for row in rows:
        by_sector[row[0]].append({"date": row[1], "pct": float(row[2] or 0), "close": float(row[3] or 0)})

    today = date.today()
    lifecycle = {}

    for sector_code, data in by_sector.items():
        if len(data) < 5:
            continue

        data.sort(key=lambda x: x["date"])

        # 近 5/10/20 日涨幅
        recent = data[-1]
        chg_5d = sum(d["pct"] for d in data[-5:]) if len(data) >= 5 else 0
        chg_10d = sum(d["pct"] for d in data[-10:]) if len(data) >= 10 else chg_5d
        chg_20d = sum(d["pct"] for d in data[-20:]) if len(data) >= 20 else chg_10d

        # 动量: 近 5 日 vs 前 5 日
        if len(data) >= 10:
            momentum = chg_5d - sum(d["pct"] for d in data[-10:-5])
        else:
            momentum = 0

        # 波动率
        pcts = [d["pct"] for d in data[-20:]]
        vol = (sum(p**2 for p in pcts) / max(len(pcts), 1)) ** 0.5

        # 生命周期判定
        if chg_20d > 15 and vol > 2.0:
            stage = "高潮"
        elif chg_20d > 8 and momentum > 2:
            stage = "发酵"
        elif chg_20d > 3 and momentum > 1:
            stage = "萌芽"
        elif chg_5d < -3 and momentum < -1:
            stage = "退潮"
        elif chg_20d > 5 and momentum < -1:
            stage = "分化"
        else:
            stage = "休眠"

        # 板块因子 (用于加权个股评分)
        if stage == "高潮":      factor = 0.6  # 警惕追高
        elif stage == "发酵":    factor = 1.3  # 最强加分
        elif stage == "萌芽":    factor = 1.1  # 早期参与
        elif stage == "分化":    factor = 0.7  # 选股难度大
        elif stage == "退潮":    factor = 0.4  # 不参与
        else:                    factor = 0.85

        lifecycle[sector_code] = {
            "stage": stage,
            "chg_5d": round(chg_5d, 2),
            "chg_10d": round(chg_10d, 2),
            "chg_20d": round(chg_20d, 2),
            "momentum": round(momentum, 2),
            "volatility": round(vol, 2),
            "factor": factor,
        }

    return lifecycle


# ═══════════════════════════════════════════════════════════
# 行业排名 (31申万一级)
# ═══════════════════════════════════════════════════════════

_ranking_cache: dict = {"data": None, "ts": None}

async def get_sector_rankings() -> dict[str, dict]:
    """获取 31 申万行业近 5/10/20 日涨幅排名.

    Returns:
        {sector_code: {rank_5d, rank_10d, rank_20d, pct_5d, ...}, ...}
    """
    global _ranking_cache
    now = date.today()
    if _ranking_cache["ts"] == now and _ranking_cache["data"]:
        return _ranking_cache["data"]

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT index_code, trade_date, pct_chg, close
            FROM sw_sector_index
            WHERE trade_date >= CURRENT_DATE - 30
            ORDER BY index_code, trade_date DESC
        """))
        rows = r.fetchall()

    by_sector = defaultdict(list)
    for row in rows:
        # pct_chg is often 0 in stored data → compute from close delta
        pct = float(row[2] or 0)
        close_val = float(row[3] or 0)
        by_sector[row[0]].append((row[1], pct, close_val))

    rankings = {}
    for code, data in by_sector.items():
        data.sort(key=lambda x: x[0])

        def _compute_pct(days_data):
            """Compute percentage change: prefer stored pct_chg, fall back to close delta."""
            if not days_data or len(days_data) < 2:
                return 0.0
            # Try stored pct first
            stored = sum(d[1] for d in days_data)
            if abs(stored) > 0.001:
                return stored
            # Compute from close values
            first_close = days_data[0][2]
            last_close = days_data[-1][2]
            if first_close > 0:
                return (last_close - first_close) / first_close * 100
            return 0.0

        chg_5d = _compute_pct(data[-5:]) if len(data) >= 5 else 0
        chg_10d = _compute_pct(data[-10:]) if len(data) >= 10 else 0
        chg_20d = _compute_pct(data[-20:]) if len(data) >= 20 else 0
        rankings[code] = {"pct_5d": round(chg_5d, 2), "pct_10d": round(chg_10d, 2),
                          "pct_20d": round(chg_20d, 2)}

    # 排名 (1=最强)
    for period in ["pct_5d", "pct_10d", "pct_20d"]:
        sorted_codes = sorted(rankings.keys(), key=lambda c: rankings[c][period], reverse=True)
        for rank, code in enumerate(sorted_codes, 1):
            rankings[code][f"rank_{period[4:]}"] = rank

    _ranking_cache = {"data": rankings, "ts": now}
    return rankings


# ═══════════════════════════════════════════════════════════
# 个股→板块映射 + 板块因子
# ═══════════════════════════════════════════════════════════

_sector_map_cache: dict = {"data": None, "ts": None}


async def _get_stock_sector_map() -> dict[str, str]:
    """股票代码 → 申万行业代码."""
    global _sector_map_cache
    now = date.today()
    if _sector_map_cache["ts"] == now and _sector_map_cache["data"]:
        return _sector_map_cache["data"]

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT ON (ts_code) ts_code, ths_name
            FROM ths_member WHERE out_date IS NULL
        """))
        mapping = {}
        for row in r.fetchall():
            mapping[row[0]] = row[1] or ""

        # Fallback: scan_results.industry (Tushare stock_basic data)
        r2 = await s.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, industry
            FROM scan_results WHERE industry IS NOT NULL AND industry != ''
            ORDER BY symbol, scan_date DESC
        """))
        for row in r2.fetchall():
            if row[0] not in mapping and row[1]:
                mapping[row[0]] = row[1]

    _sector_map_cache = {"data": mapping, "ts": now}
    return mapping


async def get_stock_sector_factor(symbol: str) -> dict:
    """获取个股的板块因子 (用于 deep_scorer 加权).

    Returns:
        {sector_code, sector_name, lifecycle_stage, factor, sector_rank_5d, sector_pct_5d, ...}
    """
    sector_map = await _get_stock_sector_map()
    sector_name = sector_map.get(symbol, "")
    if not sector_name:
        return {"factor": 1.0, "stage": "无板块数据", "note": "无行业分类"}

    lifecycle = await detect_theme_lifecycle()
    rankings = await get_sector_rankings()

    # 行业名 → 行业代码
    # 申万代码格式如 801010.SI (农林牧渔), 需要名称匹配
    # 直接用 lifecycle 的 key 进行模糊匹配
    matched_code = None
    for code in lifecycle:
        if sector_name[:2] in code or code[:4] in sector_name:
            matched_code = code
            break

    lc_info = lifecycle.get(matched_code, {"stage": "休眠", "factor": 0.85}) if matched_code else {"stage": "休眠", "factor": 0.85}
    rk_info = rankings.get(matched_code, {}) if matched_code else {}

    return {
        "sector_name": sector_name,
        "sector_code": matched_code or "",
        "lifecycle_stage": lc_info.get("stage", "休眠"),
        "factor": lc_info.get("factor", 0.85),
        "sector_rank_5d": rk_info.get("rank_5d", 99),
        "sector_rank_10d": rk_info.get("rank_10d", 99),
        "sector_pct_5d": rk_info.get("pct_5d", 0),
        "sector_pct_20d": rk_info.get("pct_20d", 0),
    }


# ═══════════════════════════════════════════════════════════
# 兼容旧接口
# ═══════════════════════════════════════════════════════════

async def get_sector_heat(days: int = 5) -> dict:
    """返回板块热度: 申万排名 + 龙虎榜热门/风险板块.

    Returns:
        {
            sectors: [...],        # 申万31行业排名
            hot_sectors: [...],    # 龙虎榜热门板块 (上榜多+涨)
            risk_sectors: [...],   # 龙虎榜风险板块 (上榜多+跌)
            lifecycle: {...},       # 主题生命周期
        }
    """
    rankings = await get_sector_rankings()
    lifecycle = await detect_theme_lifecycle()

    sectors = []
    for code, rk in rankings.items():
        lc = lifecycle.get(code, {})
        sectors.append({
            "sector_code": code,
            "pct_5d": rk["pct_5d"],
            "pct_10d": rk["pct_10d"],
            "pct_20d": rk["pct_20d"],
            "rank_5d": rk.get("rank_5d", 0),
            "rank_10d": rk.get("rank_10d", 0),
            "stage": lc.get("stage", "休眠"),
            "factor": lc.get("factor", 0.85),
        })
    sectors.sort(key=lambda x: x["pct_5d"], reverse=True)

    # ── 龙虎榜板块聚合 ──
    hot_sectors, risk_sectors = await _compute_toplist_sector_heat(days)

    return {
        "sectors": sectors,
        "hot_sectors": hot_sectors,
        "risk_sectors": risk_sectors,
        "lifecycle": lifecycle,
    }


async def _compute_toplist_sector_heat(days: int = 5) -> tuple[list[dict], list[dict]]:
    """从龙虎榜聚合板块热度 (热门/风险)."""
    from datetime import date as dt_date, timedelta
    cutoff = dt_date.today() - timedelta(days=days)

    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT t.ts_code, t.pct_change, t.l_net
                FROM toplist_daily t
                WHERE t.trade_date >= :cutoff
            """), {"cutoff": cutoff})
            toplist_rows = [(row[0], float(row[1] or 0), float(row[2] or 0)) for row in r.fetchall()]
    except Exception:
        return [], []

    if not toplist_rows:
        return [], []

    # 加载行业映射
    sector_map = await _get_stock_sector_map()

    by_sector = {}
    for ts_code, pct, net in toplist_rows:
        sec = sector_map.get(ts_code, "")
        if not sec:
            continue
        if sec not in by_sector:
            by_sector[sec] = {"count": 0, "total_pct": 0.0, "total_net": 0.0}
        by_sector[sec]["count"] += 1
        by_sector[sec]["total_pct"] += pct
        by_sector[sec]["total_net"] += net

    all_sectors = []
    for sec, info in by_sector.items():
        if info["count"] < 2:
            continue
        all_sectors.append({
            "name": sec,
            "count": info["count"],
            "avg_pct": round(info["total_pct"] / info["count"], 1),
            "total_net_wan": round(info["total_net"] / 10000, 0),
        })

    all_sectors.sort(key=lambda x: -x["count"])

    # 热门: avg_pct > 0, 取前10
    hot = [s for s in all_sectors if s["avg_pct"] > 0][:10]
    # 风险: avg_pct < 0, 取前5
    risk = [s for s in all_sectors if s["avg_pct"] < 0][:5]

    return hot, risk


async def get_full_sector_report() -> dict:
    """完整板块报告 (兼容旧接口)."""
    return {
        "sectors": await get_sector_heat(),
        "lifecycle_summary": await detect_theme_lifecycle(),
    }


# ═══════════════════════════════════════════════════════════
# 龙虎榜数据同步 (Tushare API → DB)
# ═══════════════════════════════════════════════════════════

async def sync_top_list(trade_date: str | None = None) -> dict:
    """同步指定日期的龙虎榜 daily 数据到 toplist_daily."""
    if trade_date is None:
        trade_date = date.today().strftime("%Y%m%d")

    from app.services.tushare_common import call_tushare

    # 检查是否已同步
    td = date.fromisoformat(f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}")
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT 1 FROM top_list_sync_log WHERE trade_date=:d"), {"d": td})
        if r.fetchone():
            return {"trade_date": trade_date, "status": "already_synced"}

    rows = await call_tushare("top_list", {"trade_date": trade_date},
        "ts_code,name,close,pct_change,turnover_rate,amount,reason,l_buy,l_sell,net_amount")
    if not rows:
        return {"trade_date": trade_date, "status": "no_data"}

    inserted = 0
    async with async_session_factory() as s:
        for r in rows:
            await s.execute(text("""
                INSERT INTO toplist_daily (trade_date, ts_code, name, close, pct_change, turnover_rate, amount, l_buy, l_sell, l_net, reason)
                VALUES (:d, :ts, :n, :cl, :pc, :to, :am, :lb, :ls, :ln, :rs)
                ON CONFLICT (trade_date, ts_code) DO NOTHING
            """), {
                "d": td, "ts": r["ts_code"], "n": r.get("name", ""),
                "cl": float(r.get("close", 0) or 0), "pc": float(r.get("pct_change", 0) or 0),
                "to": float(r.get("turnover_rate", 0) or 0), "am": float(r.get("amount", 0) or 0),
                "lb": float(r.get("l_buy", 0) or 0), "ls": float(r.get("l_sell", 0) or 0),
                "ln": float(r.get("net_amount", 0) or 0), "rs": r.get("reason", "")[:500],
            })
            inserted += 1
        await s.execute(text(
            "INSERT INTO top_list_sync_log (trade_date, stock_count, status) VALUES (:d, :c, 'success') ON CONFLICT (trade_date) DO NOTHING"
        ), {"d": td, "c": inserted})
        await s.commit()

    logger.info(f"Synced {inserted} top_list stocks for {trade_date}")
    return {"trade_date": trade_date, "stock_count": inserted, "status": "success"}


async def sync_toplist_detail(days: int = 5):
    """同步龙虎榜席位明细 (top_inst API)."""
    from app.services.tushare_common import call_tushare

    # 获取最近 N 个交易日
    cal = await call_tushare("trade_cal", {
        "exchange": "SSE",
        "start_date": (date.today() - timedelta(days=days + 5)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
        "is_open": "1",
    }, "cal_date")
    if not cal:
        return []
    trading_days = sorted([r["cal_date"] for r in cal])[-days:]

    results = []
    for td_str in trading_days:
        # 检查是否已同步
        td = date.fromisoformat(f"{td_str[:4]}-{td_str[4:6]}-{td_str[6:8]}")
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT COUNT(*) FROM toplist_detail WHERE trade_date=:d"), {"d": td})
            if r.scalar() and r.scalar() > 0:
                results.append({"trade_date": td_str, "status": "already_synced"})
                continue

        rows = await call_tushare("top_inst", {"trade_date": td_str},
            "trade_date,ts_code,exalter,buy,buy_rate,sell,sell_rate,net_buy,side,reason")
        if not rows:
            results.append({"trade_date": td_str, "status": "no_data"})
            continue

        inserted = 0
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text("""
                    INSERT INTO toplist_detail (trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason)
                    VALUES (:d, :ts, :ex, :buy, :br, :sell, :sr, :net, :side, :rs)
                    ON CONFLICT DO NOTHING
                """), {
                    "d": td, "ts": r["ts_code"], "ex": r.get("exalter", ""),
                    "buy": float(r.get("buy", 0) or 0), "br": float(r.get("buy_rate", 0) or 0),
                    "sell": float(r.get("sell", 0) or 0), "sr": float(r.get("sell_rate", 0) or 0),
                    "net": float(r.get("net_buy", 0) or 0),
                    "side": int(r.get("side", 0) or 0), "rs": r.get("reason", "")[:500],
                })
                inserted += 1
            await s.commit()
        logger.info(f"Synced {inserted} toplist_detail rows for {td_str}")
        results.append({"trade_date": td_str, "stock_count": inserted, "status": "success"})

    return results


async def sync_recent_days(days: int = 5) -> list[dict]:
    """同步最近 N 个交易日的龙虎榜 daily + 席位明细."""
    from app.services.tushare_common import call_tushare

    cal = await call_tushare("trade_cal", {
        "exchange": "SSE",
        "start_date": (date.today() - timedelta(days=days + 5)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
        "is_open": "1",
    }, "cal_date")
    if not cal:
        return []
    trading_days = sorted([r["cal_date"] for r in cal])[-days:]
    results = []
    for td_str in trading_days:
        result = await sync_top_list(td_str)
        results.append(result)
    return results


async def ensure_toplist_fresh() -> dict:
    """智能龙虎榜同步: 检查最新交易日数据是否存在, 不存在则拉取."""
    from datetime import datetime
    from app.services.tushare_common import call_tushare

    # 获取最近交易日
    cal = await call_tushare("trade_cal", {
        "exchange": "SSE",
        "start_date": (date.today() - timedelta(days=10)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
        "is_open": "1",
    }, "cal_date")
    if not cal:
        return {"status": "error", "reason": "无法获取交易日历"}

    tdays = sorted([r["cal_date"] for r in cal], reverse=True)
    if not tdays:
        return {"status": "skipped", "reason": "无交易日"}

    latest_td = tdays[0]
    td = date.fromisoformat(f"{latest_td[:4]}-{latest_td[4:6]}-{latest_td[6:8]}")

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT 1 FROM top_list_sync_log WHERE trade_date=:d"), {"d": td})
        if r.fetchone():
            return {"status": "skipped", "reason": f"最近交易日{latest_td}已同步"}

    # 未同步 → 拉取
    logger.info(f"龙虎榜数据缺失, 同步最近交易日 {latest_td}")
    results = []
    for td_str in tdays[:5]:
        result = await sync_top_list(td_str)
        results.append(result)

    new_count = sum(1 for r in results if r.get("status") == "success")
    return {"status": "synced", "reason": f"同步{new_count}个交易日", "results": results}


# ═══════════════════════════════════════════════════════════
# v4.3: 板块热度 + 龙虎榜交叉验证
# ═══════════════════════════════════════════════════════════

async def cross_validate_with_toplist(trade_date: date = None) -> list[dict]:
    """交叉验证: 板块热度阶段 vs 龙虎榜实际资金流向.

    对每个行业判断:
      - 热度高 + 净买入  → "confirmed" (真主线)
      - 热度高 + 净卖出  → "diverge"   (拉高出货嫌疑)
      - 热度低 + 净买入  → "stealth_buying" (悄悄建仓)
      - 其他            → "neutral"

    Returns:
        按可信度排序的行业验证结果列表
    """
    if trade_date is None:
        trade_date = date.today()

    # 1. 获取板块热度
    lifecycle: dict[str, dict] = {}
    try:
        lifecycle = await detect_theme_lifecycle()
    except Exception as e:
        logger.debug(f"Lifecycle detection failed: {e}")

    # 2. 获取龙虎榜行业净买卖聚合
    toplist_flow: dict[str, float] = {}
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT COALESCE(tm.ths_name, 'unknown') as sector,
                       SUM(COALESCE(tl.l_buy, 0) - COALESCE(tl.l_sell, 0)) as net_flow,
                       COUNT(*) as上榜数
                FROM toplist_daily tl
                LEFT JOIN ths_member tm ON tm.ts_code = tl.ts_code AND tm.out_date IS NULL
                WHERE tl.trade_date = :d AND tm.ths_name IS NOT NULL
                GROUP BY tm.ths_name
                ORDER BY net_flow DESC
            """), {"d": trade_date})
            toplist_flow = {row[0]: float(row[1] or 0) for row in r.fetchall()}
    except Exception as e:
        logger.debug(f"Toplist flow query failed: {e}")

    if not toplist_flow:
        return []

    # 3. 交叉判定
    # 简化: 使用 SW 行业涨幅排名近似热度
    rankings = {}
    try:
        rankings = await get_sector_rankings()
    except Exception:
        pass

    SW_NAME_MAP = {
        '801010.SI': '农林牧渔', '801030.SI': '基础化工', '801040.SI': '钢铁',
        '801050.SI': '有色金属', '801080.SI': '电子', '801110.SI': '家用电器',
        '801120.SI': '食品饮料', '801130.SI': '纺织服饰', '801140.SI': '轻工制造',
        '801150.SI': '医药生物', '801160.SI': '公用事业', '801170.SI': '交通运输',
        '801180.SI': '房地产', '801200.SI': '商贸零售', '801210.SI': '社会服务',
        '801230.SI': '综合', '801710.SI': '建筑材料', '801720.SI': '建筑装饰',
        '801730.SI': '电力设备', '801740.SI': '国防军工', '801750.SI': '计算机',
        '801760.SI': '传媒', '801770.SI': '通信', '801780.SI': '银行',
        '801790.SI': '非银金融', '801880.SI': '汽车', '801890.SI': '机械设备',
        '801950.SI': '煤炭', '801960.SI': '石油石化',
    }

    results = []
    for sector_name, net_flow in sorted(toplist_flow.items(), key=lambda x: -abs(x[1])):
        # 判断该行业是否"热" — Top-8 排名为热
        is_hot = False
        sw_code = None
        for code, sec_name in SW_NAME_MAP.items():
            if sector_name and (sec_name in sector_name or sector_name in sec_name):
                sw_code = code
                break

        if sw_code and rankings:
            rank_info = rankings.get(sw_code, {})
            rank_val = rank_info.get("rank_5d") or rank_info.get("pct_5d")
            if rank_val:
                try:
                    if isinstance(rank_val, (int, float)) and rank_val > 3:
                        is_hot = True
                    elif isinstance(rank_val, int) and rank_val <= 8:
                        is_hot = True
                except (TypeError, ValueError):
                    pass
            # 备选: 5日涨幅 > 2% → 热
            pct_5d = rank_info.get("pct_5d", 0)
            if pct_5d is not None and float(pct_5d) > 2.0:
                is_hot = True

        # 判定
        if is_hot and net_flow > 0:
            verdict = "confirmed"
        elif is_hot and net_flow < 0:
            verdict = "diverge"
        elif not is_hot and net_flow > 0:
            verdict = "stealth_buying"
        else:
            verdict = "neutral"

        results.append({
            "sector": sector_name,
            "net_flow": round(net_flow, 0),
            "net_flow_mil": round(net_flow / 1e4, 0),
            "is_hot": is_hot,
            "verdict": verdict,
        })

    # 按可信度排序: confirmed > stealth_buying > diverge > neutral
    verdict_order = {"confirmed": 0, "stealth_buying": 1, "diverge": 2, "neutral": 3}
    results.sort(key=lambda x: (verdict_order.get(x["verdict"], 4), -abs(x["net_flow"])))

    logger.info(f"Toplist cross-validation: {len(results)} sectors, "
                f"confirmed={sum(1 for r in results if r['verdict']=='confirmed')}, "
                f"stealth={sum(1 for r in results if r['verdict']=='stealth_buying')}, "
                f"diverge={sum(1 for r in results if r['verdict']=='diverge')}")
    return results
