"""Phase 48: 新闻规则匹配 — stock-macro-mapping 词典→news_signals 信号.

核心流程:
  ① 加载 stock-macro-mapping.md 映射词典
  ② 扫描 news_raw 最近 7 天新闻标题
  ③ 关键词匹配 + 上下文规则
  ④ 去重 (1h 同商品同方向 + 标题相似 >80%)
  ⑤ 写入 news_signals
  ⑥ 自验证: T+1/T+2 方向正确率
"""
import asyncio
import logging
import re
import os
import sys
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("build_news_signals")

# ── 上下文关键词: 新闻标题含商品名时, 必须同时含至少 1 个语境词 ──
CONTEXT_WORDS = [
    "价", "期货", "LME", "沪", "SHFE", "COMEX", "CME", "ICE",
    "收涨", "收跌", "主力合约", "现货", "库存", "交易所",
    "报价", "行情", "走势", "合约", "交割", "仓单",
    "大涨", "大跌", "暴涨", "暴跌", "跳涨", "跳水",
    "突破", "创新高", "创新低", "反弹", "回调",
    "供应", "需求", "产量", "产能", "进口", "出口",
    "制裁", "关税", "政策", "补贴", "限产", "停产",
    "开工", "复产", "减产", "增产", "去产能",
    "上调", "下调", "提价", "降价", "涨价", "跌价",
]

# ── 商品名→新闻标题特有语境词 ──
COMMODITY_EXTRA_CONTEXT = {
    "铜": ["铜价", "沪铜", "LME铜", "铜期货", "铜矿", "铜精矿", "废铜", "电解铜", "阴极铜"],
    "铝": ["铝价", "沪铝", "LME铝", "电解铝", "氧化铝", "铝锭"],
    "黄金": ["金价", "国际金", "现货金", "COMEX金", "黄金期货", "金矿"],
    "白银": ["银价", "白银期货", "现货银"],
    "锌": ["锌价", "沪锌", "LME锌", "锌精矿"],
    "铅": ["铅价", "沪铅", "铅精矿"],
    "锡": ["锡价", "沪锡", "LME锡"],
    "镍": ["镍价", "沪镍", "LME镍", "镍矿"],
    "锂": ["锂价", "碳酸锂", "锂矿", "锂盐", "锂电"],
    "钴": ["钴价", "电解钴", "钴矿"],
    "稀土": ["稀土价格", "稀土永磁", "稀土矿", "稀土配额"],
    "原油": ["油价", "国际油价", "布伦特", "WTI", "OPEC", "欧佩克"],
    "煤炭": ["煤价", "动力煤", "焦煤", "焦炭", "煤矿"],
    "天然气": ["气价", "LNG", "天然气价", "页岩气"],
    "钢铁": ["钢价", "螺纹钢", "热卷", "钢材", "钢厂"],
    "铁矿石": ["铁矿石价格", "铁矿", "普氏"],
    "水泥": ["水泥价格", "熟料"],
    "猪肉": ["猪价", "生猪", "猪肉价", "猪周期"],
    "海运": ["运价", "BDI", "CCFI", "SCFI", "集运", "航运"],
    "光伏": ["光伏", "硅料", "硅片", "组件", "逆变器", "多晶硅"],
    "新能源": ["新能源车", "电动车", "电动汽车", "锂电池"],
    "芯片": ["芯片", "半导体", "晶圆", "光刻"],
    "军工": ["军工", "国防", "军费", "武器"],
    "房地产": ["房地产", "房贷", "楼市", "房价", "拿地", "土拍"],
    "汽车": ["汽车销量", "乘用车", "商用车", "汽车产业"],
}

# ── 全局语境词 ──
GLOBAL_CONTEXT = [
    "涨", "跌", "价", "期货", "现货", "库存", "供应", "需求",
    "LME", "沪", "SHFE", "COMEX", "主力", "合约", "交易所",
    "政策", "补贴", "制裁", "关税", "限产", "停产", "增产",
    "进口", "出口", "上调", "下调", "提价", "降价",
]

# ── 需要精确匹配的短关键词 (不匹配公司名称) ──
SHORT_KEYWORDS = {"铜", "铝", "锌", "铅", "锡", "镍", "钴", "锂", "硅", "钛", "锗", "锑", "铂", "钨", "钼", "锰", "铬"}


def _load_mapping() -> dict[str, list[tuple[str, str, str, str]]]:
    """从 stock-macro-mapping.md 加载映射词典.

    Returns:
        {keyword: [(symbol, direction, confidence, reason), ...]}
        例: {"铜": [("600362.SH","利好","确定","江西铜业-铜矿龙头"), ...]}
    """
    mapping_file = os.path.join(os.path.dirname(__file__), "..", "docs", "stock-macro-mapping.md")
    mapping: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)

    with open(mapping_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("---") or line.startswith("格式"):
                continue
            # Parse: 关键词|symbol|direction|confidence|reason
            parts = line.split("|")
            if len(parts) >= 5:
                kw = parts[0].strip()
                sym = parts[1].strip()
                direction = parts[2].strip()
                confidence = parts[3].strip()
                reason = parts[4].strip()
                mapping[kw].append((sym, direction, confidence, reason))

    total = sum(len(v) for v in mapping.values())
    logger.info(f"Loaded {len(mapping)} keywords, {total} stock mappings")
    return dict(mapping)


def _title_similarity(a: str, b: str) -> float:
    """计算两个标题的相似度 (0~1)."""
    return SequenceMatcher(None, a, b).ratio()


def _has_context(title: str, keyword: str) -> bool:
    """检查新闻标题是否包含商品关键词的上下文.

    规则:
    1. 对于短关键词(如'铜''铝'), 必须含全局语境词或商品专属语境词
    2. 对于长关键词(如'碳酸锂''光伏'), 只要含关键词就匹配
    """
    if keyword not in SHORT_KEYWORDS:
        # 长关键词: 有上下文就触发
        return any(c in title for c in GLOBAL_CONTEXT)

    # 短关键词: 必须含专属语境或全局语境
    extra = COMMODITY_EXTRA_CONTEXT.get(keyword, [])
    if any(ec in title for ec in extra):
        return True
    return any(c in title for c in GLOBAL_CONTEXT)


def _detect_direction_from_title(title: str, base_direction: str) -> str:
    """根据新闻标题情感词微调方向.

    标题含"涨""暴涨""拉升""新高""突破"→保持利好
    标题含"跌""暴跌""跳水""崩盘""新低"→保持利空
    标题含"稳定""持平""窄幅"→中性
    """
    bullish_words = ["涨", "大涨", "暴涨", "拉升", "创新高", "突破", "反弹", "走强", "牛市", "利好", "上调", "增产"]
    bearish_words = ["跌", "大跌", "暴跌", "跳水", "崩盘", "新低", "走弱", "熊市", "利空", "下调", "减产", "限产"]

    title_lower = title.lower()

    is_bullish = any(w in title_lower for w in bullish_words)
    is_bearish = any(w in title_lower for w in bearish_words)

    if is_bullish and not is_bearish:
        return "利好"
    elif is_bearish and not is_bullish:
        return "利空"
    elif base_direction in ("利好", "利空"):
        return base_direction
    return "中性"


def _compute_magnitude(title: str) -> str:
    """根据标题情绪强度计算 magnitude."""
    strong = ["暴涨", "暴跌", "崩盘", "新高", "新低", "突破", "重大", "重磅", "紧急", "突发", "危机"]
    moderate = ["上涨", "下跌", "走强", "走弱", "反弹", "回调", "调整"]

    if any(w in title for w in strong):
        return "大"
    if any(w in title for w in moderate):
        return "中"
    return "小"


# ── Phase 49: 聚合逻辑 ──

# 低频保护类别：政策/宏观永远 signal_count=1, intensity=1.0
LOW_FREQ_CATEGORIES = frozenset({"policy", "macro", "central_bank"})


def _intensity_from_count(signal_count: int) -> float:
    """log 压缩：单条=0.3, 5条=0.8, 15条→1.0."""
    import math
    raw = math.log(signal_count + 1) / math.log(10 + 1)  # log_11(count+1)
    raw = max(0.3, min(1.0, raw))
    return round(raw, 3)


async def _aggregate_news_signals(session, signals: list[dict], today) -> list[dict]:
    """对 news_signals 进行 (date, commodity, direction) 聚合，写入 news_aggregated.

    聚合规则:
      ① signal_count = 桶内原始条数
      ② intensity = 价格变动归一化 × log压缩
      ③ 低频保护: policy/macro 强制 intensity=1.0
      ④ 方向保护: 不同方向 = 独立记录
    """
    from collections import defaultdict
    from datetime import time as dt_time

    if not signals:
        return []

    # ── Bucket by (date, commodity, direction) ──
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for sig in signals:
        # date = today (all signals are from the same run)
        key = (today, sig["commodity"], sig["direction"])
        buckets[key].append(sig)

    # ── Compute intensity per bucket ──
    aggregated: list[dict] = []
    for (d, commodity, direction), items in buckets.items():
        signal_count = len(items)
        category = items[0]["category"]  # 同商品同方向，类别一致

        # 低频保护: policy/macro → signal_count=1, intensity=1.0
        if category in LOW_FREQ_CATEGORIES:
            intensity = 1.0
            signal_count = 1
        else:
            # 从受影响股票中取代表性 pct_change
            symbols = list({it["symbol"] for it in items})
            pct_change = await _compute_pct_change(session, symbols, d)
            price_factor = min(1.0, abs(pct_change) / 10.0)
            count_factor = _intensity_from_count(signal_count)
            intensity = round(price_factor * count_factor, 3)

        # 提取 stocks_json + sources
        stocks_list = sorted(set(it["symbol"] for it in items))
        sources_list = sorted(set(
            it.get("source", "unknown") if isinstance(it.get("source"), str)
            else "stock-macro-mapping"
            for it in items
        ))

        # first_seen / last_seen (approximate: 取最早/最晚新闻时间)
        # 我们没有在 signals 中存储 pub_time，用 None 代替
        first_seen = None
        last_seen = None

        aggregated.append({
            "date": d,
            "commodity": commodity,
            "direction": direction,
            "signal_count": signal_count,
            "intensity": intensity,
            "stocks_json": stocks_list,
            "category": category,
            "sources": sources_list,
            "first_seen": first_seen,
            "last_seen": last_seen,
        })

    # ── UPSERT into news_aggregated ──
    upserted = 0
    for agg in aggregated:
        try:
            import json
            await session.execute(text("""
                INSERT INTO news_aggregated (date, commodity, direction, signal_count,
                    intensity, stocks_json, category, sources, first_seen, last_seen)
                VALUES (:d, :com, :dir, :sc, :inten, :stocks, :cat, :srcs, :fs, :ls)
                ON CONFLICT (date, commodity, direction) DO UPDATE SET
                    signal_count = EXCLUDED.signal_count,
                    intensity = EXCLUDED.intensity,
                    stocks_json = EXCLUDED.stocks_json,
                    category = EXCLUDED.category,
                    sources = EXCLUDED.sources,
                    first_seen = COALESCE(news_aggregated.first_seen, EXCLUDED.first_seen),
                    last_seen = EXCLUDED.last_seen
            """), {
                "d": agg["date"],
                "com": agg["commodity"],
                "dir": agg["direction"],
                "sc": agg["signal_count"],
                "inten": agg["intensity"],
                "stocks": json.dumps(agg["stocks_json"], ensure_ascii=False),
                "cat": agg["category"],
                "srcs": agg["sources"],
                "fs": agg["first_seen"],
                "ls": agg["last_seen"],
            })
            upserted += 1
        except Exception as e:
            logger.warning(f"Aggregate upsert failed for {agg['commodity']}: {e}")
            try: await session.rollback()
            except Exception: pass

    await session.commit()
    return aggregated


async def _compute_pct_change(session, symbols: list[str], d) -> float:
    """计算代表性 pct_change：取受影响股票当日平均涨跌幅（用 LAG 算上一日 close）."""
    import math
    if not symbols:
        return 0.0
    try:
        r = await session.execute(text("""
            WITH latest AS (
                SELECT DISTINCT ON (ts_code) ts_code, close,
                       LAG(close, 1) OVER (PARTITION BY ts_code ORDER BY trade_date) AS prev_close
                FROM daily_kline
                WHERE ts_code = ANY(:syms) AND trade_date <= :d
                ORDER BY ts_code, trade_date DESC
            )
            SELECT AVG((close - prev_close) / NULLIF(prev_close, 0) * 100)
            FROM latest WHERE prev_close > 0 AND close > 0
        """), {"syms": symbols[:20], "d": d})
        avg = r.scalar()
        if avg is None or math.isnan(avg):
            return 0.0
        return round(float(avg), 2)
    except Exception as e:
        try: await session.rollback()
        except Exception: pass
        logger.debug(f"pct_change failed: {e}")
        return 0.0


async def build_news_signals(dry_run: bool = False) -> dict:
    """主流程: 扫描 news_raw → 匹配 → 去重 → 写入 news_signals → 自验证.

    Returns:
        {"matched": N, "inserted": N, "verify": {commodity: {total, correct, pct}}}
    """
    mapping = _load_mapping()
    today = date.today()
    seven_days_ago = today - timedelta(days=7)

    async with async_session_factory() as session:
        # ── 1. 获取最近 7 天新闻 ──
        r = await session.execute(text(
            "SELECT id, title, source, pub_time FROM news_raw "
            "WHERE pub_time >= :since ORDER BY pub_time DESC"
        ), {"since": seven_days_ago})
        news_rows = r.fetchall()
        logger.info(f"Scanning {len(news_rows)} news from {seven_days_ago} to {today}")

        # ── 2. 关键词匹配 ──
        # 同 1h 窗口去重 key: (commodity, direction, hour_bucket)
        seen_buckets: set[tuple[str, str, str]] = set()
        # 标题相似去重: 同商品同方向相似 >80% → skip
        recent_titles: dict[str, list[str]] = defaultdict(list)

        signals: list[dict] = []
        match_stats: dict[str, int] = defaultdict(int)

        for news in news_rows:
            news_id = news[0]
            title = news[1] or ""
            source = news[2] or ""
            pub_time = news[3]

            for keyword, stocks in mapping.items():
                # Keyword must appear in title
                if keyword not in title:
                    continue

                # Must have context
                if not _has_context(title, keyword):
                    continue

                # Hour bucket for dedup
                hour_bucket = pub_time.strftime("%Y-%m-%d %H") if pub_time else ""

                match_stats[keyword] += 1

                for sym, base_dir, confidence, reason in stocks:
                    direction = _detect_direction_from_title(title, base_dir)
                    magnitude = _compute_magnitude(title)

                    # Dedup: 同商品+同股票+同方向+同小时 → 去重
                    dedup_key = (keyword, sym, direction, hour_bucket)
                    if dedup_key in seen_buckets:
                        continue

                    # Dedup: 同股票+同方向+同商品 的标题相似度 >80% → 合并
                    dedup_title_key = f"{keyword}:{sym}:{direction}"
                    prev_titles = recent_titles.get(dedup_title_key, [])
                    if any(_title_similarity(title, pt) > 0.8 for pt in prev_titles[-5:]):
                        continue

                    seen_buckets.add(dedup_key)
                    recent_titles[dedup_title_key].append(title)

                    # Category
                    if keyword in ("铜", "铝", "锌", "铅", "锡", "镍", "钴", "锂", "稀土", "黄金", "白银",
                                   "钨", "钼", "锗", "锑", "铂", "锰", "铬", "硅", "钛", "铁矿石",
                                   "螺纹钢", "不锈钢", "硅钢", "钢铁"):
                        cat = "commodity"
                    elif keyword in ("原油", "天然气", "煤炭", "焦煤", "焦炭", "页岩气", "可燃冰",
                                     "电力", "核电", "光伏", "风电", "储能", "氢能", "充电桩",
                                     "新能源汽车", "锂电池", "碳酸锂"):
                        cat = "commodity"
                    elif keyword in ("PTA", "乙二醇", "甲醇", "尿素", "纯碱", "烧碱", "PVC",
                                     "钛白粉", "MDI", "TDI", "有机硅", "磷化工", "氟化工",
                                     "草甘膦", "化肥", "农药", "染料", "维生素"):
                        cat = "commodity"
                    elif keyword in ("水泥", "玻璃", "玻纤"):
                        cat = "commodity"
                    elif keyword in ("大豆", "玉米", "小麦", "水稻", "棉花", "白糖", "橡胶",
                                     "猪肉", "鸡肉", "饲料", "种子", "棕榈油", "水产品"):
                        cat = "commodity"
                    elif keyword in ("降准", "降息", "LPR", "MLF", "专项债", "RMB贬值", "RMB升值",
                                     "一带一路", "碳中和", "数字经济政策", "房地产政策", "关税",
                                     "中美关系", "地缘政治", "新能源补贴", "环保政策", "集采", "反垄断"):
                        cat = "policy"
                    elif keyword in ("高温", "寒冬", "汛期", "疫情", "流感", "地震", "战争",
                                     "奥运会", "世界杯", "消费刺激"):
                        cat = "macro"
                    else:
                        cat = "sector"

                    signals.append({
                        "news_id": news_id,
                        "symbol": sym,
                        "direction": direction,
                        "magnitude": magnitude,
                        "category": cat,
                        "commodity": keyword,
                        "reason": reason,
                        "confidence": confidence,
                    })

        logger.info(f"Matched {len(signals)} signals from {sum(match_stats.values())} keyword hits")

        # ── 3. Write to news_signals ──
        if dry_run:
            for kw, cnt in sorted(match_stats.items(), key=lambda x: -x[1])[:30]:
                logger.info(f"  {kw}: {cnt} hits")
            return {"matched": len(signals), "inserted": 0, "verify": {}, "match_stats": match_stats}

        inserted = 0
        for sig in signals:
            try:
                await session.execute(text("""
                    INSERT INTO news_signals (news_id, symbol, direction, magnitude, category, commodity, reason, confidence)
                    VALUES (:nid, :sym, :dir, :mag, :cat, :com, :reason, :conf)
                """), {
                    "nid": sig["news_id"], "sym": sig["symbol"], "dir": sig["direction"],
                    "mag": sig["magnitude"], "cat": sig["category"], "com": sig["commodity"],
                    "reason": sig["reason"], "conf": sig["confidence"],
                })
                inserted += 1
            except Exception as e:
                logger.debug(f"Insert failed: {e}")

        await session.commit()
        logger.info(f"Inserted {inserted}/{len(signals)} signals")

        # ── 4. Aggregate: 密度→强度 + 低频保护 (Phase 49) ──
        aggregated = await _aggregate_news_signals(session, signals, today)
        logger.info(f"Aggregated {len(aggregated)} signals into news_aggregated")

        # ── 5. Self-verify: T+1/T+2 direction accuracy ──
        verify_results: dict[str, dict] = {}
        try:
            # 找 7 天前的 news_signals (昨天之前的)
            r = await session.execute(text("""
                SELECT ns.commodity, ns.symbol, ns.direction, ns.created_at,
                       nr.pub_time
                FROM news_signals ns
                JOIN news_raw nr ON ns.news_id = nr.id
                WHERE ns.created_at < CURRENT_DATE
                ORDER BY nr.pub_time DESC
                LIMIT 500
            """))
            verify_rows = r.fetchall()

            if verify_rows:
                # 按 commodity 分组统计
                commodity_verify: dict[str, list[tuple[str, str, datetime]]] = defaultdict(list)
                for row in verify_rows:
                    commodity_verify[row[0]].append((row[1], row[2], row[4]))

                for commodity, items in commodity_verify.items():
                    total = len(items)
                    correct = 0
                    for sym, expected_dir, pub_time in items[:50]:  # 最多验证 50 条
                        try:
                            # 查 T+1, T+2 收盘价
                            r2 = await session.execute(text(
                                "SELECT close FROM daily_kline WHERE ts_code=:sym "
                                "AND trade_date > :d ORDER BY trade_date LIMIT 2"
                            ), {"sym": sym, "d": pub_time.date() if pub_time else today - timedelta(days=7)})
                            closes = [float(row[0] or 0) for row in r2.fetchall()]
                            if len(closes) >= 2 and closes[0] > 0 and closes[1] > 0:
                                t2_return = (closes[1] - closes[0]) / closes[0] * 100
                                if expected_dir == "利好" and t2_return > 0:
                                    correct += 1
                                elif expected_dir == "利空" and t2_return < 0:
                                    correct += 1
                                elif expected_dir == "中性" and abs(t2_return) < 1:
                                    correct += 1
                        except Exception:
                            pass
                    pct = round(correct / total * 100, 1) if total > 0 else 0
                    verify_results[commodity] = {"total": total, "correct": correct, "pct": pct}
                    logger.info(f"  Verify {commodity}: {correct}/{total} correct ({pct}%)")
        except Exception as e:
            logger.warning(f"Verify failed: {e}")

        return {
            "matched": len(signals),
            "inserted": inserted,
            "aggregated": len(aggregated),
            "verify": verify_results,
            "match_stats": dict(sorted(match_stats.items(), key=lambda x: -x[1])[:20]),
        }


async def main():
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.WARNING)

    result = await build_news_signals(dry_run="--dry-run" in sys.argv)
    print(f"\n=== Phase 49 完成 ===")
    print(f"匹配信号: {result['matched']} 条")
    print(f"写入 news_signals: {result['inserted']} 条")
    print(f"聚合 signals: {result.get('aggregated', 0)} 条")

    if result.get("match_stats"):
        print("\nTop 20 关键词命中:")
        for kw, cnt in result["match_stats"].items():
            print(f"  {kw}: {cnt}")

    if result.get("verify"):
        print("\n方向验证 (T+2):")
        for commodity, stats in sorted(result["verify"].items(), key=lambda x: -x[1]["total"]):
            print(f"  {commodity}: {stats['correct']}/{stats['total']} ({stats['pct']}%)")


if __name__ == "__main__":
    asyncio.run(main())
