"""龙虎榜分析引擎 v1.0 — P0: 三日拆分 + 合力评分 + 净额比.

数据源: toplist_detail (top_inst API 席位明细)
"""
import logging
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# ── 席位标签库 (冷启动, 后续可在系统设置中增删) ──

BROKER_TAGS = {
    # 机构类
    "机构专用": {"type": "机构", "style": "波段", "premium": "正"},
    "深股通专用": {"type": "机构", "style": "波段", "premium": "正"},
    "沪股通专用": {"type": "机构", "style": "波段", "premium": "正"},
    # 顶级游资 (格局型)
    "中信证券上海分公司": {"type": "游资", "style": "格局", "premium": "正"},
    "国泰君安上海分公司": {"type": "游资", "style": "格局", "premium": "正"},
    "华鑫证券上海宛平南路": {"type": "游资", "style": "格局", "premium": "正"},
    "中信证券上海溧阳路": {"type": "游资", "style": "格局", "premium": "正"},
    "光大证券上海世纪大道": {"type": "游资", "style": "格局", "premium": "正"},
    # 一线游资 (活跃型)
    "财通证券杭州上塘路": {"type": "游资", "style": "一日游", "premium": "负"},
    "中国银河北京中关村大街": {"type": "游资", "style": "一日游", "premium": "中性"},
    "华泰证券上海武定路": {"type": "游资", "style": "一日游", "premium": "负"},
    "国盛证券宁波桑田路": {"type": "游资", "style": "一日游", "premium": "负"},
    "中国银河绍兴": {"type": "游资", "style": "格局", "premium": "正"},
    "中信建投杭州庆春路": {"type": "游资", "style": "一日游", "premium": "中性"},
    "华泰证券成都蜀金路": {"type": "游资", "style": "一日游", "premium": "负"},
    "国金证券上海互联网": {"type": "游资", "style": "一日游", "premium": "中性"},
    "光大证券深圳金田路": {"type": "游资", "style": "格局", "premium": "正"},
    # 散户集中营
    "东方财富拉萨团结路第一": {"type": "散户", "style": "跟风", "premium": "负"},
    "东方财富拉萨团结路第二": {"type": "散户", "style": "跟风", "premium": "负"},
    "东方财富拉萨东环路第一": {"type": "散户", "style": "跟风", "premium": "负"},
    "东方财富拉萨东环路第二": {"type": "散户", "style": "跟风", "premium": "负"},
}


def _match_broker_tag(exalter: str) -> dict:
    """匹配席位标签. 模糊匹配——营业部名称包含关键词即可."""
    for key, tag in BROKER_TAGS.items():
        if key in exalter:
            return tag
    return {"type": "未知", "style": "未知", "premium": "未知"}


async def get_daily_toplist_detail(trade_date: str | date) -> list[dict]:
    """获取某日的席位明细(已拆分三日榜)."""
    if isinstance(trade_date, str):
        trade_date = date.fromisoformat(trade_date)
    td = trade_date

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, ts_code, exalter, buy, buy_rate, sell, sell_rate, net_buy, side, reason
            FROM toplist_detail WHERE trade_date = :d
            ORDER BY ts_code, side, buy DESC
        """), {"d": td})
        rows = []
        for row in r.fetchall():
            rows.append({
                "trade_date": str(row[0]), "ts_code": row[1],
                "exalter": row[2], "buy": float(row[3] or 0),
                "buy_rate": float(row[4] or 0), "sell": float(row[5] or 0),
                "sell_rate": float(row[6] or 0), "net_buy": float(row[7] or 0),
                "side": row[8], "reason": row[9] or "",
            })
    return rows


async def split_three_day(rows: list[dict]) -> list[dict]:
    """拆分三日榜: 减去前两日同席位金额, 还原当日真实买卖."""
    if not rows:
        return rows

    trade_date = rows[0]["trade_date"]
    td = date.fromisoformat(trade_date)

    # 获取前两日数据
    prev_dates = []
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM toplist_detail WHERE trade_date < :d ORDER BY trade_date DESC LIMIT 2"
        ), {"d": td})
        prev_dates = [row[0] for row in r.fetchall()]

    if len(prev_dates) < 1:
        # 无前日数据, 不需拆分
        for r in rows:
            r["is_three_day"] = False
        return rows

    # 加载前两日营业部数据
    prev_data = defaultdict(lambda: {"buy": 0, "sell": 0})
    for pd_date in prev_dates:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, exalter, buy, sell FROM toplist_detail WHERE trade_date = :d
            """), {"d": pd_date})
            for row in r.fetchall():
                key = (row[0], row[1])
                prev_data[key]["buy"] += float(row[2] or 0)
                prev_data[key]["sell"] += float(row[3] or 0)

    # 拆分: 减去前两日已上榜金额
    for r in rows:
        is_3d = "连续三个交易日" in r.get("reason", "") or "三日" in r.get("reason", "")
        r["is_three_day"] = is_3d

        if is_3d:
            key = (r["ts_code"], r["exalter"])
            prev = prev_data.get(key, {"buy": 0, "sell": 0})
            r["raw_buy"] = r["buy"]
            r["raw_sell"] = r["sell"]
            r["buy"] = max(0, r["buy"] - prev["buy"])
            r["sell"] = max(0, r["sell"] - prev["sell"])
            r["net_buy"] = r["buy"] - r["sell"]
            r["split_note"] = f"三日榜拆分: 减去前{len(prev_dates)}日 {prev['buy']:.0f}/{prev['sell']:.0f}"

    return rows


async def analyze_single_stock(ts_code: str, trade_date: str | date) -> dict:
    """分析单只股票的龙虎榜质量.

    Returns:
        {force_score, net_quality, broker_profile, signals}
    """
    rows = await get_daily_toplist_detail(trade_date)
    stock_rows = [r for r in rows if r["ts_code"] == ts_code]
    if not stock_rows:
        return {"status": "not_on_list"}

    # 拆分三日榜
    stock_rows = await split_three_day(stock_rows)

    # ── 1. 合力评分 ──
    buyers = [r for r in stock_rows if r["buy"] > 0]
    buyers.sort(key=lambda x: x["buy"], reverse=True)

    if not buyers:
        return {"status": "no_buyers"}

    top1_buy = buyers[0]["buy"] if buyers else 0
    top5_buy_sum = sum(r["buy"] for r in buyers[:5])
    force_score = 0.0
    force_detail = ""

    if top5_buy_sum > 0:
        top1_ratio = top1_buy / top5_buy_sum
        if top1_ratio > 0.5:
            force_score = 1.0  # 一家独大, 差
            force_detail = f"一家独大(买一占{top1_ratio*100:.0f}%)"
        elif top1_ratio > 0.35:
            force_score = 2.5
            force_detail = f"偏集中(买一占{top1_ratio*100:.0f}%)"
        elif top1_ratio > 0.25:
            force_score = 4.0
            force_detail = f"较均匀(买一占{top1_ratio*100:.0f}%)"
        else:
            force_score = 5.0  # 均匀合力, 好
            force_detail = f"合力优秀(买一占{top1_ratio*100:.0f}%)"

    # 买方席位画像
    broker_types = defaultdict(int)
    broker_styles = defaultdict(int)
    notable_brokers = []
    for r in buyers[:5]:
        tag = _match_broker_tag(r["exalter"])
        broker_types[tag["type"]] += 1
        broker_styles[tag["style"]] += 1
        if tag["type"] != "未知":
            notable_brokers.append(f"{tag['type']}:{tag['style']}:{r['exalter'][:8]}")

    broker_profile = f"{'+'.join(f'{v}{k}' for k,v in broker_types.items())}"
    if "格局" in broker_styles:
        broker_profile += " 含格局资金"

    # ── 2. 买卖净额比 ──
    total_buy = sum(r["buy"] for r in buyers[:5])
    total_sell = sum(r["sell"] for r in stock_rows if r["sell"] > 0)
    net_buy_total = total_buy - total_sell

    net_quality = 0.0
    net_detail = ""
    if total_buy > 0:
        buy_sell_ratio = total_sell / total_buy
        if buy_sell_ratio < 0.3:
            net_quality = 5.0
            net_detail = f"买方碾压(卖/买={buy_sell_ratio:.1%})"
        elif buy_sell_ratio < 0.6:
            net_quality = 3.5
            net_detail = f"买卖均衡偏买(卖/买={buy_sell_ratio:.1%})"
        elif buy_sell_ratio < 1.0:
            net_quality = 2.0
            net_detail = f"买卖接近(卖/买={buy_sell_ratio:.1%})"
        else:
            net_quality = 0.5
            net_detail = f"卖方占优(卖/买={buy_sell_ratio:.1%})"

    # ── 3. 三日陷阱检测 ──
    signals = []
    if any(r.get("is_three_day") for r in stock_rows):
        real_net = sum(r["net_buy"] for r in stock_rows)
        raw_net = sum(r.get("raw_buy", r["buy"]) - r.get("raw_sell", r["sell"]) for r in stock_rows)
        if raw_net > 0 and real_net < 0:
            signals.append({"type": "three_day_trap", "severity": "high",
                           "msg": f"三日陷阱: 表面净买{raw_net/1e4:.0f}万, 实际当日净卖{abs(real_net)/1e4:.0f}万"})

    # 一家独大预警
    if top1_ratio > 0.5 and top5_buy_sum > 0:
        signals.append({"type": "single_dominant", "severity": "medium",
                       "msg": f"一家独大: {buyers[0]['exalter'][:12]}占买入{top1_ratio*100:.0f}%"})

    # 散户接盘预警
    retail_count = broker_types.get("散户", 0)
    if retail_count >= 2 and net_buy_total < 0:
        signals.append({"type": "retail_trap", "severity": "high",
                       "msg": f"散户集中接盘: {retail_count}个拉萨席位买入"})

    return {
        "status": "success",
        "ts_code": ts_code,
        "force_score": round(force_score, 1),
        "force_detail": force_detail,
        "net_quality": round(net_quality, 1),
        "net_detail": net_detail,
        "net_buy_total": round(net_buy_total / 1e4, 0),  # 万元
        "broker_profile": broker_profile,
        "notable_brokers": notable_brokers[:3],
        "signals": signals,
        "top1_buy": round(top1_buy / 1e4, 0),
        "top5_buy_sum": round(top5_buy_sum / 1e4, 0),
        "total_sell": round(total_sell / 1e4, 0),
    }


async def analyze_daily_all(trade_date: str | date = None) -> list[dict]:
    """分析当日所有上榜股票."""
    if trade_date is None:
        td = date.today()
    elif isinstance(trade_date, str):
        td = date.fromisoformat(trade_date)
    else:
        td = trade_date

    rows = await get_daily_toplist_detail(td)
    if not rows:
        return []

    # 按股票分组
    by_stock = defaultdict(list)
    for r in rows:
        by_stock[r["ts_code"]].append(r)

    results = []
    for ts_code, stock_rows in by_stock.items():
        # 拆分三日榜
        stock_rows = await split_three_day(stock_rows)

        buyers = [r for r in stock_rows if r["buy"] > 0]
        if not buyers:
            continue
        buyers.sort(key=lambda x: x["buy"], reverse=True)
        top5_buy = sum(r["buy"] for r in buyers[:5])

        # 净值
        total_sell = sum(r["sell"] for r in stock_rows if r["sell"] > 0)
        net_total = top5_buy - total_sell

        # 合力
        top1_r = buyers[0]["buy"] / top5_buy if top5_buy > 0 else 1

        # 席位
        broker_types = defaultdict(int)
        for r in buyers[:5]:
            tag = _match_broker_tag(r["exalter"])
            broker_types[tag["type"]] += 1

        results.append({
            "ts_code": ts_code,
            "market": "创业板" if (ts_code.startswith('300') or ts_code.startswith('301') or ts_code.startswith('688')) else ("中小板" if (ts_code.startswith('002') or ts_code.startswith('003')) else "主板"),
            "net_buy_wan": round(net_total / 1e4, 0),
            "top1_ratio": round(top1_r, 2),
            "force_label": "合力优" if top1_r < 0.3 else ("集中" if top1_r < 0.5 else "一家独大"),
            "institutions": broker_types.get("机构", 0),
            "retail": broker_types.get("散户", 0),
            "notable": broker_types.get("游资", 0),
            "is_three_day": any(r.get("is_three_day") for r in stock_rows),
        })

    return results


async def analyze_sector_resonance(trade_date: str | date = None) -> dict:
    """板块共振检测: 统计各板块上榜股数, 识别资金共振."""
    if trade_date is None:
        trade_date = date.today()

    daily = await analyze_daily_all(trade_date)
    if not daily:
        return {"status": "empty"}

    # 加载板块映射
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, tag_value FROM stock_dimension_tags WHERE dim_name='sector'"
        ))
        sector_map = defaultdict(list)
        for row in r.fetchall():
            sector_map[row[1]].append(row[0])

    # 按板块聚合
    by_sector = defaultdict(list)
    for stock in daily:
        for sector, codes in sector_map.items():
            if stock["ts_code"] in codes or stock["ts_code"][:6] in codes:
                by_sector[sector].append(stock)
                break

    # 板块评分
    sectors = []
    for sector, stocks in by_sector.items():
        if len(stocks) < 2:
            continue
        total_inst = sum(s["institutions"] for s in stocks)
        total_retail = sum(s["retail"] for s in stocks)
        total_notable = sum(s["notable"] for s in stocks)
        good_force = sum(1 for s in stocks if s["force_label"] == "合力优")

        resonance = "weak"
        if len(stocks) >= 3 and total_inst > 0 and total_notable > 0:
            resonance = "strong"
        elif len(stocks) >= 2 and (total_inst > 0 or total_notable > 0):
            resonance = "moderate"

        sectors.append({
            "sector": sector, "count": len(stocks),
            "resonance": resonance,
            "institutions": total_inst, "retail": total_retail, "notable": total_notable,
            "good_force": good_force,
            "stocks": [s["ts_code"] for s in stocks[:5]],
        })

    sectors.sort(key=lambda s: (s["resonance"] != "strong", -s["count"]))
    return {"status": "success", "sectors": sectors, "total_stocks": len(daily)}
