"""Phase 50: 新闻信号验证 — 回溯 7-30 天新闻, 验证方向命中率, 标记 is_active.

流程:
  ① 加载 stock-macro-mapping 词典
  ② 扫描 news_raw 7-30 天前的新闻 (保证 T+2 窗口)
  ③ 关键词匹配 (与 build_news_signals 逻辑一致)
  ④ 每条: 从 daily_kline 取 pub_date 后 2 天的实际涨跌幅
  ⑤ 判断方向: 利好且 T+2>0 → correct, 利空且 T+2<0 → correct
  ⑥ 按 (commodity, direction, symbol) 聚合
  ⑦ UPSERT news_verify, 标记 is_active = (total >= 3 AND hit_rate_t2 >= 0.55)
"""
import asyncio
import logging
import os
import sys
import math
from datetime import date, datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("verify_news_signals")

# ── 与 build_news_signals 共享的配置 ──
SHORT_KEYWORDS = {"铜","铝","锌","铅","锡","镍","钴","锂","硅","钛","锗","锑","铂","钨","钼","锰","铬"}

GLOBAL_CONTEXT = [
    "涨","跌","价","期货","现货","库存","供应","需求",
    "LME","沪","SHFE","COMEX","主力","合约","交易所",
    "政策","补贴","制裁","关税","限产","停产","增产",
    "进口","出口","上调","下调","提价","降价",
]

COMMODITY_EXTRA_CONTEXT = {
    "铜": ["铜价","沪铜","LME铜","铜期货","铜矿","铜精矿","废铜","电解铜","阴极铜"],
    "铝": ["铝价","沪铝","LME铝","电解铝","氧化铝","铝锭"],
    "黄金": ["金价","国际金","现货金","COMEX金","黄金期货","金矿"],
    "白银": ["银价","白银期货","现货银"],
    "锌": ["锌价","沪锌","LME锌","锌精矿"],
    "铅": ["铅价","沪铅","铅精矿"],
    "锡": ["锡价","沪锡","LME锡"],
    "镍": ["镍价","沪镍","LME镍","镍矿"],
    "锂": ["锂价","碳酸锂","锂矿","锂盐","锂电"],
    "钴": ["钴价","电解钴","钴矿"],
    "稀土": ["稀土价格","稀土永磁","稀土矿","稀土配额"],
    "原油": ["油价","国际油价","布伦特","WTI","OPEC","欧佩克"],
    "煤炭": ["煤价","动力煤","焦煤","焦炭","煤矿"],
    "天然气": ["气价","LNG","天然气价","页岩气"],
    "钢铁": ["钢价","螺纹钢","热卷","钢材","钢厂"],
    "铁矿石": ["铁矿石价格","铁矿","普氏"],
    "水泥": ["水泥价格","熟料"],
    "猪肉": ["猪价","生猪","猪肉价","猪周期"],
    "海运": ["运价","BDI","CCFI","SCFI","集运","航运"],
    "光伏": ["光伏","硅料","硅片","组件","逆变器","多晶硅"],
    "新能源": ["新能源车","电动车","电动汽车","锂电池"],
    "芯片": ["芯片","半导体","晶圆","光刻"],
    "军工": ["军工","国防","军费","武器"],
    "房地产": ["房地产","房贷","楼市","房价","拿地","土拍"],
    "汽车": ["汽车销量","乘用车","商用车","汽车产业"],
}


def _load_mapping() -> dict[str, list[tuple[str, str, str, str]]]:
    """从 stock-macro-mapping.md 加载映射词典."""
    mapping_file = os.path.join(os.path.dirname(__file__), "..", "docs", "stock-macro-mapping.md")
    mapping: dict[str, list[tuple[str, str, str, str]]] = defaultdict(list)
    with open(mapping_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("---") or line.startswith("格式"):
                continue
            parts = line.split("|")
            if len(parts) >= 5:
                mapping[parts[0].strip()].append((
                    parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()
                ))
    return dict(mapping)


def _has_context(title: str, keyword: str) -> bool:
    """检查标题是否有上下文."""
    if keyword not in SHORT_KEYWORDS:
        return any(c in title for c in GLOBAL_CONTEXT)
    extra = COMMODITY_EXTRA_CONTEXT.get(keyword, [])
    if any(ec in title for ec in extra):
        return True
    return any(c in title for c in GLOBAL_CONTEXT)


def _detect_direction_from_title(title: str, base_direction: str) -> str:
    bullish = ["涨","大涨","暴涨","拉升","创新高","突破","反弹","走强","牛市","利好","上调","增产"]
    bearish = ["跌","大跌","暴跌","跳水","崩盘","新低","走弱","熊市","利空","下调","减产","限产"]
    title_lower = title.lower()
    is_bullish = any(w in title_lower for w in bullish)
    is_bearish = any(w in title_lower for w in bearish)
    if is_bullish and not is_bearish:
        return "利好"
    elif is_bearish and not is_bullish:
        return "利空"
    return base_direction


async def verify_news_signals(lookback_days: int = 30, min_age_days: int = 5) -> dict:
    """主流程: 回溯验证新闻信号方向准确性.

    Args:
        lookback_days: 回溯多少天内的新闻
        min_age_days: 最少需要多旧才能验证 (保证 T+2 有数据)
    """
    mapping = _load_mapping()
    today = date.today()
    since = today - timedelta(days=lookback_days)
    cutoff = today - timedelta(days=min_age_days)

    # ── 1. 取历史新闻 ──
    async with async_session_factory() as session:
        r = await session.execute(text(
            "SELECT id, title, source, pub_time FROM news_raw "
            "WHERE pub_time >= :since AND pub_time <= :cutoff ORDER BY pub_time"
        ), {"since": since, "cutoff": cutoff + timedelta(days=1)})
        news_rows = r.fetchall()
        logger.info(f"Scanning {len(news_rows)} history news ({since} ~ {cutoff})")

        # ── 2. 关键词匹配 + 方向检测 ──
        # (news_id, keyword, direction, symbol) → 只留唯一组合去验证
        verifications: list[dict] = []
        seen = set()

        for news in news_rows:
            news_id = news[0]
            title = news[1] or ""
            pub_time = news[3]
            pub_date = pub_time.date() if pub_time else None
            if not pub_date:
                continue

            for keyword, stocks in mapping.items():
                if keyword not in title:
                    continue
                if not _has_context(title, keyword):
                    continue

                for sym, base_dir, confidence, reason in stocks:
                    direction = _detect_direction_from_title(title, base_dir)
                    key = (news_id, keyword, direction, sym)
                    if key in seen:
                        continue
                    seen.add(key)
                    verifications.append({
                        "news_id": news_id, "keyword": keyword,
                        "sym": sym, "direction": direction, "pub_date": pub_date,
                    })

        logger.info(f"Matched {len(verifications)} (news, commodity, direction, symbol) tuples")

        # ── 3. 批量查 daily_kline T+1/T+2 ──
        # 预先加载所有 (symbol, pub_date) 的 kline
        symbol_dates: dict[str, set[date]] = defaultdict(set)
        for v in verifications:
            symbol_dates[v["sym"]].add(v["pub_date"])

        # 批量查询: 每个 symbol 的 3 天窗口 (T+0到T+2)
        kline_cache: dict[tuple[str, date], list[float]] = {}
        for sym, dates in symbol_dates.items():
            if not dates:
                continue
            min_d = min(dates)
            max_d = max(dates) + timedelta(days=3)
            try:
                r = await session.execute(text(
                    "SELECT trade_date, close FROM daily_kline "
                    "WHERE ts_code = :sym AND trade_date >= :lo AND trade_date <= :hi "
                    "ORDER BY trade_date"
                ), {"sym": sym, "lo": min_d, "hi": max_d})
                for row in r.fetchall():
                    td = row[0]
                    kline_cache[(sym, td)] = float(row[1])
            except Exception as e:
                logger.debug(f"K-line query failed for {sym}: {e}")

        # ── 4. 逐条验证 ──
        commodity_results: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {"total": 0, "correct_t1": 0, "correct_t2": 0, "returns": [], "last_date": None}
        )

        for v in verifications:
            sym, direction, pub_date, commodity = v["sym"], v["direction"], v["pub_date"], v["keyword"]

            # 取 T+0, T+1, T+2 close
            c0 = kline_cache.get((sym, pub_date))
            c1 = kline_cache.get((sym, pub_date + timedelta(days=1)))
            c2 = kline_cache.get((sym, pub_date + timedelta(days=2)))

            if not (c0 and c2 and c0 > 0 and c2 > 0):
                continue

            t1_ret = (c1 - c0) / c0 * 100 if c1 and c1 > 0 else 0
            t2_ret = (c2 - c0) / c0 * 100

            key = (commodity, direction, sym)
            commodity_results[key]["total"] += 1
            commodity_results[key]["returns"].append(t2_ret)
            if pub_date > (commodity_results[key]["last_date"] or date(2000, 1, 1)):
                commodity_results[key]["last_date"] = pub_date

            # T+1 正确?
            if (direction == "利好" and t1_ret > 0) or (direction == "利空" and t1_ret < 0):
                commodity_results[key]["correct_t1"] += 1

            # T+2 正确?
            if (direction == "利好" and t2_ret > 0) or (direction == "利空" and t2_ret < 0):
                commodity_results[key]["correct_t2"] += 1

        # ── 5. UPSERT news_verify ──
        upserted = 0
        activated = 0
        for (commodity, direction, sym), stats in commodity_results.items():
            total = stats["total"]
            correct_t1 = stats["correct_t1"]
            correct_t2 = stats["correct_t2"]
            hit_rate_t2 = round(correct_t2 / total * 100, 2) if total > 0 else 0
            avg_return = round(sum(stats["returns"]) / len(stats["returns"]), 2) if stats["returns"] else 0
            is_active = total >= 3 and hit_rate_t2 >= 55.0

            try:
                await session.execute(text("""
                    INSERT INTO news_verify (commodity, direction, symbol, total,
                        correct_t1, correct_t2, hit_rate_t2, avg_return, last_signal_date,
                        is_active, updated_at)
                    VALUES (:c, :d, :s, :t, :c1, :c2, :hr, :ar, :lsd, :ia, NOW())
                    ON CONFLICT (commodity, direction, symbol) DO UPDATE SET
                        total = EXCLUDED.total,
                        correct_t1 = EXCLUDED.correct_t1,
                        correct_t2 = EXCLUDED.correct_t2,
                        hit_rate_t2 = EXCLUDED.hit_rate_t2,
                        avg_return = EXCLUDED.avg_return,
                        last_signal_date = EXCLUDED.last_signal_date,
                        is_active = EXCLUDED.is_active,
                        updated_at = NOW()
                """), {
                    "c": commodity, "d": direction, "s": sym,
                    "t": total, "c1": correct_t1, "c2": correct_t2,
                    "hr": hit_rate_t2, "ar": avg_return,
                    "lsd": stats["last_date"], "ia": is_active,
                })
                upserted += 1
                if is_active:
                    activated += 1
            except Exception as e:
                logger.warning(f"Upsert failed for {commodity}/{direction}/{sym}: {e}")

        await session.commit()
        logger.info(f"Upserted {upserted} rows, {activated} active")

        # ── 6. 按商品维度汇总输出 ──
        commodity_agg: dict[str, dict] = defaultdict(lambda: {"total": 0, "correct_t2": 0, "syms": set()})
        for (commodity, direction, sym), stats in commodity_results.items():
            commodity_agg[commodity]["total"] += stats["total"]
            commodity_agg[commodity]["correct_t2"] += stats["correct_t2"]
            commodity_agg[commodity]["syms"].add(sym)

        return {
            "upserted": upserted,
            "activated": activated,
            "details": [
                {
                    "commodity": c,
                    "total": s["total"],
                    "correct_t2": s["correct_t2"],
                    "hit_rate": round(s["correct_t2"] / s["total"] * 100, 1) if s["total"] else 0,
                    "syms": len(s["syms"]),
                    "active": any(
                        commodity_results.get((c, d, sym), {}).get("total", 0) >= 3
                        and commodity_results.get((c, d, sym), {}).get("correct_t2", 0)
                        / max(commodity_results.get((c, d, sym), {}).get("total", 1), 1) >= 0.55
                        for d in ("利好", "利空")
                        for sym in s["syms"]
                    ),
                }
                for c, s in sorted(commodity_agg.items(), key=lambda x: -x[1]["total"])
                if s["total"] >= 5
            ],
        }


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("app").setLevel(logging.WARNING)

    result = await verify_news_signals(lookback_days=30, min_age_days=5)

    print(f"\n=== Phase 50 验证完成 ===")
    print(f"Upserted: {result['upserted']} rows")
    print(f"Activated (hit_rate>=55% & total>=3): {result['activated']} rows")
    print()

    # 按商品汇总
    print(f"{'商品':10s} {'信号':>5s} {'正确T2':>7s} {'命中率':>7s} {'股票':>5s} {'状态':>6s}")
    print("-" * 50)
    for d in result["details"]:
        status = "✅保持" if d["active"] else "❌禁用"
        print(f"{d['commodity']:10s} {d['total']:5d} {d['correct_t2']:7d} {d['hit_rate']:6.1f}% {d['syms']:5d} {status:>6s}")

    # 打印全零样本数（数据不足以计算命中率）
    total_rows = result["upserted"]
    print(f"\n总计 {total_rows} 条 (commodity,direction,symbol) 组合有验证数据")


if __name__ == "__main__":
    asyncio.run(main())
