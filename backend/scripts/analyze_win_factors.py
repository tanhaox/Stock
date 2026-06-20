"""Deep analysis: What makes winning stocks win?

Analyzes: market cap, industry, TG level, archetype, technical state
to find predictive factors for recommendation success.
"""
import os, re, glob, asyncio, sys, json
from datetime import date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from app.core.database import async_session_factory
import logging
logging.disable(logging.CRITICAL)

DOWNLOADS = r"C:\Users\tanha\Downloads"

def parse_files():
    """Returns {rec_date: [(code, rank_position), ...]} where rank=file order"""
    results = {}
    for pat in [os.path.join(DOWNLOADS, "stocks_*.txt"), os.path.join(DOWNLOADS, "推荐股票_*.txt")]:
        for fpath in sorted(glob.glob(pat)):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(fpath))
            if not m: continue
            file_date = m.group(1)
            with open(fpath, 'r', encoding='utf-8') as f:
                codes = []
                for i, line in enumerate(f):
                    line = line.strip().upper()
                    if not line: continue
                    if '.' not in line:
                        from app.utils.stock_code import normalize_ts_code
                        line = normalize_ts_code(line)
                        if not line
                    codes.append((line, i+1))
            if file_date not in results:
                results[file_date] = []
            results[file_date].extend(codes)
    return results

async def deep_analyze():
    recs = parse_files()
    print(f"Total recommendation days: {len(recs)}")

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-05-01' ORDER BY trade_date"))
        all_tdays = [str(row[0]) for row in r.fetchall()]

    # Collect all stocks with their T+3 returns + metadata
    all_analyzed = []

    for rec_date_str, coded_list in recs.items():
        codes_only = [c for c, _ in coded_list]
        # Find actual trading day
        actual_day = None
        for td in all_tdays:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day: continue

        try:
            idx = all_tdays.index(actual_day)
            t3_day = all_tdays[idx + 3] if idx + 3 < len(all_tdays) else None
        except (ValueError, IndexError):
            continue
        if not t3_day: continue

        # Get entry prices
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
            ), {"c": codes_only, "td": date.fromisoformat(actual_day)})
            entry = {row[0]: float(row[1]) for row in r.fetchall()}

        # Get T+3 prices
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
            ), {"c": codes_only, "td": date.fromisoformat(t3_day)})
            t3p = {row[0]: float(row[1]) for row in r.fetchall()}

        # Get market cap (latest daily_basic)
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, total_mv FROM daily_basic
                WHERE ts_code = ANY(:c) AND trade_date = :td
            """), {"c": codes_only, "td": date.fromisoformat(actual_day)})
            mcaps = {row[0]: float(row[1] or 0) for row in r.fetchall()}

        # Get industry from latest scan_results
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, industry, level, composite_score
                FROM scan_results
                WHERE symbol = ANY(:c) AND scan_date <= :td
                ORDER BY symbol, scan_date DESC
            """), {"c": codes_only, "td": date.fromisoformat(actual_day)})
            scan_info = {}
            for row in r.fetchall():
                scan_info[row[0]] = {
                    "industry": row[1] or "未知",
                    "level": row[2] or "L0",
                    "score": float(row[3] or 0),
                }

        # Get archetype from stock_fingerprints (if available)
        async with async_session_factory() as s:
            try:
                r = await s.execute(text("""
                    SELECT ts_code, archetype FROM stock_fingerprints
                    WHERE ts_code = ANY(:c) AND calc_date <= :td
                    ORDER BY calc_date DESC
                """), {"c": codes_only, "td": date.fromisoformat(actual_day)})
                arch_info = {row[0]: row[1] for row in r.fetchall()}
            except Exception:
                arch_info = {}

        for code, rank in coded_list:
            if code not in entry or code not in t3p: continue
            ret = (t3p[code] - entry[code]) / entry[code] * 100
            info = scan_info.get(code, {})
            mv = mcaps.get(code, 0)
            # Market cap category (total_mv in 万元, 10k yuan)
            if mv > 10_000_000: cap_cat = "large"       # >1000亿
            elif mv > 2_000_000: cap_cat = "mid"         # 200-1000亿
            elif mv > 500_000: cap_cat = "small"         # 50-200亿
            else: cap_cat = "micro"                       # <50亿

            all_analyzed.append({
                "code": code, "rec_date": rec_date_str, "rank": rank,
                "ret": round(ret, 2), "win": ret > 0,
                "industry": info.get("industry", "未知"),
                "level": info.get("level", "L0"),
                "score": info.get("score", 0),
                "market_cap": mv,
                "cap_cat": cap_cat,
                "archetype": arch_info.get(code, "unknown"),
            })

    print(f"Total analyzed: {len(all_analyzed)}")
    wins = [a for a in all_analyzed if a["win"]]
    losses = [a for a in all_analyzed if not a["win"]]
    print(f"Wins: {len(wins)} ({len(wins)/len(all_analyzed)*100:.1f}%)")
    print(f"Losses: {len(losses)} ({len(losses)/len(all_analyzed)*100:.1f}%)")

    # ── Analysis 1: Rank position vs win rate ──
    print(f"\n{'='*70}")
    print("1. 排名位置 vs 胜率")
    print(f"{'排名段':<12} {'样本':<8} {'胜率':<10} {'平均收益':<10}")
    print("-" * 40)
    for rank_range, label in [((1,5), "Top 1-5"), ((6,10), "6-10"), ((11,20), "11-20"),
                               ((21,50), "21-50"), ((51,100), "51-100")]:
        group = [a for a in all_analyzed if rank_range[0] <= a["rank"] <= rank_range[1]]
        if not group: continue
        wr = sum(1 for a in group if a["win"]) / len(group) * 100
        avgr = sum(a["ret"] for a in group) / len(group)
        print(f"{label:<12} {len(group):<8} {wr:.1f}%{'':<5} {avgr:+.2f}%")

    # ── Analysis 2: Market cap vs win rate ──
    print(f"\n{'='*70}")
    print("2. 市值分组 vs 胜率")
    print(f"{'市值':<12} {'样本':<8} {'胜率':<10} {'平均收益':<10} {'盈利均':<10} {'亏损均':<10}")
    print("-" * 60)
    for cap in ["large", "mid", "small", "micro"]:
        group = [a for a in all_analyzed if a["cap_cat"] == cap]
        if not group: continue
        wr = sum(1 for a in group if a["win"]) / len(group) * 100
        avgr = sum(a["ret"] for a in group) / len(group)
        w_avg = sum(a["ret"] for a in group if a["win"]) / max(1, sum(1 for a in group if a["win"]))
        l_avg = sum(a["ret"] for a in group if not a["win"]) / max(1, sum(1 for a in group if not a["win"]))
        label = {"large":"大盘>1000亿","mid":"中盘200-1000亿","small":"小盘50-200亿","micro":"微盘<50亿"}.get(cap, cap)
        print(f"{label:<12} {len(group):<8} {wr:.1f}%{'':<5} {avgr:+.2f}%{'':<6} {w_avg:+.2f}%{'':<6} {l_avg:+.2f}%")

    # ── Analysis 3: Industry vs win rate ──
    print(f"\n{'='*70}")
    print("3. 行业 vs 胜率 (样本>=15)")
    ind_stats = defaultdict(list)
    for a in all_analyzed: ind_stats[a["industry"]].append(a["ret"])
    print(f"{'行业':<12} {'样本':<8} {'胜率':<10} {'平均收益':<10}")
    print("-" * 40)
    for ind, rets in sorted(ind_stats.items(), key=lambda x: sum(1 for r in x[1] if r>0)/len(x[1]), reverse=True):
        if len(rets) < 15: continue
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"{ind:<12} {len(rets):<8} {wr:.1f}%{'':<5} {sum(rets)/len(rets):+.2f}%")

    # ── Analysis 4: TG Level vs win rate ──
    print(f"\n{'='*70}")
    print("4. TG信号等级 vs 胜率")
    lvl_stats = defaultdict(list)
    for a in all_analyzed: lvl_stats[a["level"]].append(a["ret"])
    print(f"{'等级':<10} {'样本':<8} {'胜率':<10} {'平均收益':<10}")
    print("-" * 38)
    for lvl in ["L3", "L2", "L1", "L0"]:
        rets = lvl_stats.get(lvl, [])
        if not rets: continue
        wr = sum(1 for r in rets if r > 0) / len(rets) * 100
        print(f"{lvl:<10} {len(rets):<8} {wr:.1f}%{'':<5} {sum(rets)/len(rets):+.2f}%")

    # ── Analysis 5: Score bucket vs win rate ──
    print(f"\n{'='*70}")
    print("5. 综合评分段 vs 胜率")
    buckets = [(0,2), (2,4), (4,6), (6,8), (8,10), (10,20)]
    for lo, hi in buckets:
        group = [a for a in all_analyzed if lo <= a["score"] < hi]
        if not group: continue
        wr = sum(1 for a in group if a["win"]) / len(group) * 100
        avgr = sum(a["ret"] for a in group) / len(group)
        print(f"  分数 {lo}-{hi}: {len(group)}只, 胜率 {wr:.1f}%, 均收益 {avgr:+.2f}%")

    # ── Analysis 6: Consecutive winners (which stocks win repeatedly) ──
    print(f"\n{'='*70}")
    print("6. 重复推荐 & 连赢分析")
    code_appearances = defaultdict(list)
    for a in all_analyzed: code_appearances[a["code"]].append(a)

    # Stocks recommended 3+ times with high win rate
    multi_rec = {c: apps for c, apps in code_appearances.items() if len(apps) >= 3}
    print(f"  被推荐3次以上的股票: {len(multi_rec)} 只")
    high_wr_multi = [(c, sum(1 for a in apps if a["win"])/len(apps), len(apps),
                      sum(a["ret"] for a in apps)/len(apps))
                     for c, apps in multi_rec.items()]
    high_wr_multi.sort(key=lambda x: x[1], reverse=True)
    print(f"\n  胜率最高的重复推荐股 (>=3次):")
    for code, wr, cnt, avgr in high_wr_multi[:15]:
        apps = code_appearances[code]
        ind = apps[0].get("industry", "?")
        cap = apps[0].get("cap_cat", "?")
        lvl = apps[0].get("level", "?")
        print(f"    {code} | {ind:<8} | {cap:<8} | Lv:{lvl} | 推{cnt}次 | 胜率{wr*100:.0f}% | 均收益{avgr:+.2f}%")

    # ── Analysis 7: Winning pattern - rank + cap + score combined ──
    print(f"\n{'='*70}")
    print("7. 复合条件筛选 (寻找高胜率组合)")
    combos = [
        ("排名Top10 + 中大盘 + L3信号",
         [a for a in all_analyzed if a["rank"] <= 10 and a["cap_cat"] in ("large","mid") and a["level"] == "L3"]),
        ("排名Top10 + 小盘 + L3信号",
         [a for a in all_analyzed if a["rank"] <= 10 and a["cap_cat"] in ("small","micro") and a["level"] == "L3"]),
        ("排名Top20 + 大盘",
         [a for a in all_analyzed if a["rank"] <= 20 and a["cap_cat"] == "large"]),
        ("排名Top20 + 微盘",
         [a for a in all_analyzed if a["rank"] <= 20 and a["cap_cat"] == "micro"]),
        ("排名Top10 + 高评分(>=6)",
         [a for a in all_analyzed if a["rank"] <= 10 and a["score"] >= 6]),
        ("排名11-30 + 高评分(>=6)",
         [a for a in all_analyzed if 11 <= a["rank"] <= 30 and a["score"] >= 6]),
        ("排名Top5 (极致精选)",
         [a for a in all_analyzed if a["rank"] <= 5]),
    ]
    print(f"{'筛选条件':<35} {'样本':<8} {'胜率':<10} {'均收益':<10}")
    print("-" * 65)
    for label, group in combos:
        if len(group) < 5: continue
        wr = sum(1 for a in group if a["win"]) / len(group) * 100
        avgr = sum(a["ret"] for a in group) / len(group)
        print(f"{label:<35} {len(group):<8} {wr:.1f}%{'':<5} {avgr:+.2f}%")

asyncio.run(deep_analyze())
