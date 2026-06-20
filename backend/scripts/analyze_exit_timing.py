"""Analyze: (1) T+2 as first actionable exit, (2) peak vs endpoint returns.

Addresses two critical gaps:
- Gap 1: System predicts "will it go up?" but never "when to sell?"
  A stock peaking at T+2 +10% then settling T+5 +3% is NOT a weak winner.
- Gap 2: T+1 rule means T+2 is the first day you CAN sell.
  Prediction should optimize for T+2 being up, not T+3/T+5.
"""
import os, re, glob, asyncio, sys
from datetime import date, timedelta
from collections import defaultdict, Counter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from app.core.database import async_session_factory
import logging
logging.disable(logging.CRITICAL)

DOWNLOADS = r"C:\Users\tanha\Downloads"

def parse_files():
    results = {}
    for pat in [os.path.join(DOWNLOADS, "stocks_*.txt"), os.path.join(DOWNLOADS, "推荐股票_*.txt")]:
        for fpath in sorted(glob.glob(pat)):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(fpath))
            if not m: continue
            file_date = m.group(1)
            with open(fpath, 'r', encoding='utf-8') as f:
                codes = []
                for line in f:
                    line = line.strip().upper()
                    if not line: continue
                    if '.' not in line:
                        from app.utils.stock_code import normalize_ts_code
                        line = normalize_ts_code(line)
                        if not line
                    codes.append(line)
            if file_date not in results:
                results[file_date] = []
            results[file_date].extend(codes)
    return results

async def analyze():
    recs = parse_files()

    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-05-01' ORDER BY trade_date"))
        all_tdays = [str(row[0]) for row in r.fetchall()]

    # Collect T+1 through T+5 daily returns per stock
    all_results = []

    for rec_date_str, codes_only in recs.items():
        actual_day = None
        for td in all_tdays:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day: continue

        try:
            idx = all_tdays.index(actual_day)
        except ValueError:
            continue

        # Get all close prices from T+0 to T+5 for these stocks
        end_idx = min(idx + 6, len(all_tdays))
        day_range = all_tdays[idx:end_idx]
        if len(day_range) < 3: continue  # need at least T+0, T+1, T+2

        # Batch query all prices across the window
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, trade_date, close FROM daily_kline
                WHERE ts_code = ANY(:c) AND trade_date BETWEEN :d0 AND :d1
                ORDER BY ts_code, trade_date
            """), {"c": codes_only, "d0": date.fromisoformat(day_range[0]),
                   "d1": date.fromisoformat(day_range[-1])})
            rows = r.fetchall()

        # Organize: {code: {trade_date: close}}
        price_series = defaultdict(dict)
        for row in rows:
            price_series[row[0]][str(row[1])] = float(row[2])

        for code in codes_only:
            if code not in price_series: continue
            series = price_series[code]
            if day_range[0] not in series: continue
            entry = series[day_range[0]]

            # Calculate returns for T+1 through T+5 (as available)
            daily_rets = {}
            peak_ret = 0.0
            peak_day = 0
            max_dd = 0.0  # max drawdown from peak

            running_peak = 0.0
            for i, d in enumerate(day_range[1:], 1):  # T+1, T+2, ...
                if d not in series: break
                ret = (series[d] - entry) / entry * 100
                daily_rets[i] = round(ret, 2)
                if ret > peak_ret:
                    peak_ret = ret
                    peak_day = i
                # Drawdown from peak
                dd = peak_ret - ret
                if dd > max_dd:
                    max_dd = dd

            # Key metrics
            t2_ret = daily_rets.get(2, None)  # first actionable exit
            t3_ret = daily_rets.get(3, None)
            t5_ret = daily_rets.get(5, None)
            t1_ret = daily_rets.get(1, None)  # buy day return (can't sell)

            # "Endpoint miss" detection: did it peak much higher than where it ended?
            if t3_ret is not None and peak_ret > t3_ret + 5:
                endpoint_miss = True  # peaked 5%+ higher than T+3 close
            elif t5_ret is not None and peak_ret > t5_ret + 5:
                endpoint_miss = True
            else:
                endpoint_miss = False

            all_results.append({
                "code": code, "rec_date": rec_date_str,
                "t1_ret": t1_ret,  # buy day (can't sell)
                "t2_ret": t2_ret,  # first sell day ← KEY
                "t3_ret": t3_ret,
                "t5_ret": t5_ret,
                "peak_ret": round(peak_ret, 2),
                "peak_day": peak_day,
                "max_dd": round(max_dd, 2),
                "endpoint_miss": endpoint_miss,
            })

    # ── Analysis 1: T+2 as the real target ──
    print("=" * 70)
    print("问题二量化: T+2 才是第一个可卖日")
    print()
    valid_t1 = [r for r in all_results if r["t1_ret"] is not None]
    valid_t2 = [r for r in all_results if r["t2_ret"] is not None]
    valid_t3 = [r for r in all_results if r["t3_ret"] is not None]
    valid_t5 = [r for r in all_results if r["t5_ret"] is not None]

    for label, data in [("T+1(买日不可卖)", valid_t1), ("T+2(首可卖日)", valid_t2),
                         ("T+3", valid_t3), ("T+5", valid_t5)]:
        wins = sum(1 for r in data if r[f"t{label[2]}_ret"] > 0)
        avgr = sum(r[f"t{label[2]}_ret"] for r in data) / len(data)
        print(f"  {label:<20} 样本{len(data):<6} 胜率{wins/len(data)*100:.1f}%  均收益{avgr:+.2f}%")

    # ── Analysis 2: Endpoint miss — stocks that peaked way higher ──
    print(f"\n{'='*70}")
    print("问题一量化: 端点收益 vs 峰值收益 (有多少'假弱者')")
    print()
    misses = [r for r in all_results if r["endpoint_miss"]]
    print(f"  峰值远高于终点的股票: {len(misses)}/{len(all_results)} ({len(misses)/len(all_results)*100:.1f}%)")
    print(f"  即: 这些股票本可以赚更多, 但因为没卖而回吐了大量利润")

    if misses:
        print(f"\n  典型'假弱者'案例 (T+3胜但其实T+2已到顶):")
        # Sort by peak_ret descending, but T+3 is still positive (just much less)
        show = sorted([m for m in misses if m.get("t3_ret") is not None],
                      key=lambda m: m["peak_ret"] - (m.get("t3_ret") or 0), reverse=True)[:10]
        for r in show:
            print(f"    {r['code']} | {r['rec_date']} | T+1:{r['t1_ret']:+.1f}% T+2:{r['t2_ret']:+.1f}% "
                  f"T+3:{r['t3_ret']:+.1f}% | 峰值:{r['peak_ret']:+.1f}%(T+{r['peak_day']}) | "
                  f"回撤:{r['max_dd']:.1f}%")

    # ── Analysis 3: Optimal exit day distribution ──
    print(f"\n{'='*70}")
    print("峰值分布: 哪天卖最优?")
    peak_dist = Counter()
    peak_rets = defaultdict(list)
    for r in all_results:
        peak_dist[r["peak_day"]] += 1
        peak_rets[r["peak_day"]].append(r["peak_ret"])

    for day in sorted(peak_dist.keys()):
        rets = peak_rets[day]
        avgp = sum(rets) / len(rets)
        print(f"  T+{day}为峰值: {peak_dist[day]}只 ({peak_dist[day]/len(all_results)*100:.1f}%)  峰值均收益: {avgp:+.2f}%")

    # ── Analysis 4: If we sold at T+2 instead of holding ──
    print(f"\n{'='*70}")
    print("策略对比: 固定T+2卖出 vs 固定T+3卖出")
    both = [r for r in all_results if r["t2_ret"] is not None and r["t3_ret"] is not None]
    t2_wins = sum(1 for r in both if r["t2_ret"] > 0)
    t3_wins = sum(1 for r in both if r["t3_ret"] > 0)
    t2_avg = sum(r["t2_ret"] for r in both) / len(both)
    t3_avg = sum(r["t3_ret"] for r in both) / len(both)
    print(f"  T+2卖出: 胜率{t2_wins/len(both)*100:.1f}%  均收益{t2_avg:+.2f}%  样本{len(both)}")
    print(f"  T+3卖出: 胜率{t3_wins/len(both)*100:.1f}%  均收益{t3_avg:+.2f}%  样本{len(both)}")

    # ── Analysis 5: If we had a simple trailing stop ──
    print(f"\n{'='*70}")
    print("假设加入简单止盈: T+2到达+5%就卖 vs 持有到T+3")
    t2_exit_sim = []
    for r in both:
        if r["t2_ret"] >= 5.0:
            t2_exit_sim.append(5.0)  # took profit at +5%
        else:
            t2_exit_sim.append(r["t3_ret"])  # held to T+3
    wins_sim = sum(1 for r in t2_exit_sim if r > 0)
    avg_sim = sum(t2_exit_sim) / len(t2_exit_sim)
    print(f"  T+2止盈(>5%就卖)+T+3兜底: 胜率{wins_sim/len(t2_exit_sim)*100:.1f}%  均收益{avg_sim:+.2f}%")
    print(f"  对比固定T+3持有: 胜率{t3_wins/len(both)*100:.1f}%  均收益{t3_avg:+.2f}%")

asyncio.run(analyze())
