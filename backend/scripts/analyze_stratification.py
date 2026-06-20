"""Quick analysis: Top-N win rate stratification."""
import os, re, glob, asyncio, sys
from datetime import date, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlalchemy import text
from app.core.database import async_session_factory
import logging
logging.disable(logging.CRITICAL)

DOWNLOADS = r"C:\Users\tanha\Downloads"

def parse_files_per_date():
    results = defaultdict(list)
    for pat in [os.path.join(DOWNLOADS, "stocks_*.txt"), os.path.join(DOWNLOADS, "推荐股票_*.txt")]:
        for fpath in glob.glob(pat):
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
            results[file_date].extend(codes)
    return results

async def analyze_stratification():
    recs = parse_files_per_date()

    async with async_session_factory() as s:
        # Get all trading days in range
        r = await s.execute(text("SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-05-01' ORDER BY trade_date"))
        all_tdays = [row[0] for row in r.fetchall()]

    tday_set = set(str(d) for d in all_tdays)
    tday_list = [str(d) for d in all_tdays]

    # For each recommendation date, find actual trading day, then T+3/T+5
    results_by_topn = {10: [], 20: [], 30: [], 50: [], 100: []}

    for rec_date_str, codes in recs.items():
        # Find first trading day >= rec_date
        actual_day = None
        for td in tday_list:
            if td >= rec_date_str:
                actual_day = td
                break
        if not actual_day: continue

        # Find T+3 trading day
        try:
            idx = tday_list.index(actual_day)
            t3_day = tday_list[idx + 3] if idx + 3 < len(tday_list) else None
            t5_day = tday_list[idx + 5] if idx + 5 < len(tday_list) else None
        except (ValueError, IndexError):
            continue

        # Get prices on actual_day
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:codes) AND trade_date = :td"
            ), {"codes": codes, "td": date.fromisoformat(actual_day)})
            entry_prices = {row[0]: float(row[1]) for row in r.fetchall()}

        # Get T+3 prices
        t3_prices = {}
        if t3_day:
            async with async_session_factory() as s:
                r = await s.execute(text(
                    "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:codes) AND trade_date = :td"
                ), {"codes": codes, "td": date.fromisoformat(t3_day)})
                t3_prices = {row[0]: float(row[1]) for row in r.fetchall()}

        # For each stock with data, calculate T+3 return
        stock_returns = []
        for code in codes:
            if code in entry_prices and code in t3_prices:
                ret = (t3_prices[code] - entry_prices[code]) / entry_prices[code] * 100
                stock_returns.append((code, ret))

        # The key: stocks are in file order (which is the system's ranking order)
        for top_n in [10, 20, 30, 50, 100]:
            top_stocks = stock_returns[:min(top_n, len(stock_returns))]
            for _, ret in top_stocks:
                results_by_topn[top_n].append(ret)

    print("=== Top-N 分层胜率 (T+3) ===\n")
    print(f"{'Top-N':<10} {'样本数':<8} {'胜率':<10} {'平均收益':<10} {'中位数收益':<10} {'盈利均':<10} {'亏损均':<10}")
    print("-" * 66)
    for top_n in [10, 20, 30, 50, 100]:
        returns = results_by_topn[top_n]
        if not returns: continue
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        flat = [r for r in returns if r == 0]
        sorted_rets = sorted(returns)
        median = sorted_rets[len(sorted_rets)//2]
        print(f"Top-{top_n:<5} {len(returns):<8} {len(wins)/len(returns)*100:.1f}%{'':<5} "
              f"{sum(returns)/len(returns):+.2f}%{'':<5} {median:+.2f}%{'':<5} "
              f"{sum(wins)/len(wins):+.2f}%{'':<5} {sum(losses)/len(losses):+.2f}%")

    # Market condition analysis
    print(f"\n=== 市场环境 vs 胜率 ===\n")
    # Get SSE Composite index data
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date >= '2026-05-01' ORDER BY trade_date"
        ))
        index_data = {str(row[0]): float(row[1]) for row in r.fetchall()}

    for rec_date_str in sorted(recs.keys()):
        actual_day = None
        for td in tday_list:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day or actual_day not in index_data: continue

        # Index 5-day trend before recommendation
        try:
            idx_pos = tday_list.index(actual_day)
            if idx_pos >= 4:
                prev_5 = tday_list[idx_pos - 5]
                idx_5d_ago = index_data.get(prev_5)
                idx_today = index_data.get(actual_day)
                if idx_5d_ago and idx_today:
                    idx_trend = (idx_today - idx_5d_ago) / idx_5d_ago * 100

                    # Calculate win rate for this date (Top-20)
                    codes = recs[rec_date_str][:20]
                    async with async_session_factory() as s:
                        r = await s.execute(text(
                            "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
                        ), {"c": codes, "td": date.fromisoformat(actual_day)})
                        entry = {row[0]: float(row[1]) for row in r.fetchall()}

                    t3 = tday_list[idx_pos + 3] if idx_pos + 3 < len(tday_list) else None
                    t3p = {}
                    if t3:
                        async with async_session_factory() as s:
                            r = await s.execute(text(
                                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
                            ), {"c": codes, "td": date.fromisoformat(t3)})
                            t3p = {row[0]: float(row[1]) for row in r.fetchall()}

                    rets = [(t3p[c] - entry[c]) / entry[c] * 100 for c in codes if c in entry and c in t3p]
                    wr = sum(1 for r in rets if r > 0) / len(rets) * 100 if rets else 0
                    print(f"  {rec_date_str} | 指数5日: {idx_trend:+.2f}% | Top-20 胜率: {wr:.1f}% | 样本: {len(rets)}")
        except Exception as e:
            pass

asyncio.run(analyze_stratification())
