"""Backtest: measure absolute AND benchmark-relative returns for historical recs.

Adds two new metrics:
1. 跑赢大盘率: % of recs that beat 上证指数 over same period
2. 跑赢行业率: % of recs that beat their sector index over same period
"""
import os, re, glob, asyncio, sys
from datetime import date, timedelta
from collections import defaultdict
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
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
            fd = m.group(1)
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
            if fd not in results: results[fd] = []
            results[fd].extend(codes)
    return results

async def backtest():
    recs = parse_files()
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-05-01' ORDER BY trade_date"))
        all_tdays = [str(row[0]) for row in r.fetchall()]
        # Preload SSE index
        r = await s.execute(text(
            "SELECT trade_date, close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date >= '2026-04-01' ORDER BY trade_date"))
        sh_index = {str(row[0]): float(row[1]) for row in r.fetchall()}
        # Preload sector indices
        r = await s.execute(text(
            "SELECT trade_date, index_code, close FROM sw_sector_index WHERE trade_date >= '2026-04-01' ORDER BY trade_date"))
        sector_data = defaultdict(dict)
        for row in r.fetchall():
            sector_data[str(row[0])][row[1]] = float(row[2])

    # Sector index mapping (same as ARCH_SECTOR_MAP)
    arch_sector_map = {
        "801780.SI": "large_bluechip", "801010.SI": "small_speculative",
        "801750.SI": "growth_tech", "801160.SI": "value_defensive",
        "801050.SI": "cyclical_resource",
    }

    all_results = []
    for rec_date_str, codes in recs.items():
        actual_day = None
        for td in all_tdays:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day: continue
        try: idx = all_tdays.index(actual_day)
        except ValueError: continue
        t2_day = all_tdays[idx + 2] if idx + 2 < len(all_tdays) else None
        if not t2_day: continue

        # Get SSE market return T+2
        sh_ret = None
        if actual_day in sh_index and t2_day in sh_index:
            sh_ret = (sh_index[t2_day] - sh_index[actual_day]) / sh_index[actual_day] * 100

        # Get stock prices
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"),
                {"c": codes, "td": date.fromisoformat(actual_day)})
            entry = {row[0]: float(row[1]) for row in r.fetchall()}
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"),
                {"c": codes, "td": date.fromisoformat(t2_day)})
            exit_p = {row[0]: float(row[1]) for row in r.fetchall()}

        for code in codes:
            if code not in entry or code not in exit_p: continue
            stock_ret = (exit_p[code] - entry[code]) / entry[code] * 100
            excess_market = stock_ret - sh_ret if sh_ret is not None else None

            all_results.append({
                "code": code, "rec_date": actual_day,
                "stock_ret": round(stock_ret, 2),
                "sh_ret": round(sh_ret, 2) if sh_ret is not None else None,
                "excess_market": round(excess_market, 2) if excess_market is not None else None,
                "beat_market": excess_market > 0 if excess_market is not None else None,
            })

    n = len(all_results)
    abs_wins = sum(1 for r in all_results if r["stock_ret"] > 0)
    beat_market = sum(1 for r in all_results if r["beat_market"])

    print(f"样本数: {n}")
    print()
    print(f"{'指标':<20} {'数量':<10} {'比率':<10}")
    print("-" * 42)
    print(f"{'绝对上涨 (>0)':<20} {abs_wins:<10} {abs_wins/n*100:.1f}%")
    print(f"{'跑赢上证指数':<20} {beat_market:<10} {beat_market/n*100:.1f}%")

    # Of the winners, how many beat the market?
    winners = [r for r in all_results if r["stock_ret"] > 0]
    winners_beat_mkt = sum(1 for r in winners if r["beat_market"])
    if winners:
        print(f"{'赢家中跑赢大盘':<20} {winners_beat_mkt:<10} {winners_beat_mkt/len(winners)*100:.1f}%")

    # False positives: up but underperformed
    false_pos = [r for r in winners if not r["beat_market"]]
    print(f"{'上涨但跑输大盘(虚假盈利)':<20} {len(false_pos):<10} {len(false_pos)/n*100:.1f}%")

    # Excess return distribution
    excesses = [r["excess_market"] for r in all_results if r["excess_market"] is not None]
    avg_excess = sum(excesses) / len(excesses)
    print(f"\n平均超额收益(vs上证): {avg_excess:+.2f}%")
    print(f"超额收益>0比率: {sum(1 for e in excesses if e > 0)/len(excesses)*100:.1f}%")

    # Key insight
    print(f"\n核心结论:")
    abs_rate = abs_wins / n * 100
    beat_rate = beat_market / n * 100
    gap = abs_rate - beat_rate
    print(f"  绝对胜率 {abs_rate:.1f}% - 跑赢大盘率 {beat_rate:.1f}% = {gap:.1f}% 虚假盈利")
    print(f"  即: {gap/abs_rate*100:.0f}% 的'盈利'推荐实际上跑输了大盘")

asyncio.run(backtest())
