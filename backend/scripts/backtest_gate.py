"""Backtest Phase C gate: compare full vs gate-filtered T+2 win rates.

Simulates the gate on historical recommendation data to see if it would have helped.
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


async def backtest_gate():
    recs = parse_files()

    # Get all trading days and index data
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-04-01' ORDER BY trade_date"))
        all_tdays = [str(row[0]) for row in r.fetchall()]
        r = await s.execute(text(
            "SELECT trade_date, close FROM daily_kline WHERE ts_code='700001.TI' AND trade_date >= '2026-04-01' ORDER BY trade_date"
        ))
        idx_data = {str(row[0]): float(row[1]) for row in r.fetchall()}

    results_by_date = []

    for rec_date_str, codes in recs.items():
        # Find actual trading day
        actual_day = None
        for td in all_tdays:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day or actual_day not in idx_data: continue
        try:
            idx_pos = all_tdays.index(actual_day)
        except ValueError: continue

        # Simulate market state on this date
        idx_dates = sorted(idx_data.keys())
        try:
            td_idx = idx_dates.index(actual_day)
            closes = [idx_data[d] for d in idx_dates[max(0,td_idx-19):td_idx+1]]
        except ValueError: continue
        if len(closes) < 10: continue

        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma5_prev = sum(closes[-6:-1]) / 5
        trend_up = ma5 > ma10
        trend_strengthening = ma5 > ma5_prev

        rets = [(closes[i]-closes[i-1])/closes[i-1] for i in range(1,len(closes))]
        vol_5d = (sum(r**2 for r in rets[-5:])/5)**0.5 if len(rets)>=5 else 0
        vol_20d = (sum(r**2 for r in rets)/len(rets))**0.5 if rets else 0
        vol_ratio = vol_5d/vol_20d if vol_20d > 0 else 1.0

        if not trend_up and vol_ratio > 1.5: risk = "high"
        elif not trend_up and vol_ratio > 1.2: risk = "elevated"
        elif trend_up and trend_strengthening and vol_ratio < 1.2: risk = "low"
        else: risk = "normal"

        # Determine gate config for this date
        if risk == "high":
            min_prob, max_n = 0.40, 60
        elif risk == "elevated":
            min_prob, max_n = 0.35, 80
        elif risk == "low":
            min_prob, max_n = 0.28, 120
        else:
            min_prob, max_n = 0.30, 100

        # Get T+2 data
        t2_day = all_tdays[idx_pos + 2] if idx_pos + 2 < len(all_tdays) else None
        if not t2_day: continue

        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
            ), {"c": codes, "td": date.fromisoformat(actual_day)})
            entry = {row[0]: float(row[1]) for row in r.fetchall()}
            r = await s.execute(text(
                "SELECT ts_code, close FROM daily_kline WHERE ts_code = ANY(:c) AND trade_date = :td"
            ), {"c": codes, "td": date.fromisoformat(t2_day)})
            exit_p = {row[0]: float(row[1]) for row in r.fetchall()}

        # Full list T+2 returns
        stock_rets = []
        for code in codes:
            if code in entry and code in exit_p:
                ret = (exit_p[code] - entry[code]) / entry[code] * 100
                stock_rets.append(ret)

        # Gate-filtered: take top max_n
        gated_rets = stock_rets[:min(max_n, len(stock_rets))]

        full_wr = sum(1 for r in stock_rets if r > 0) / len(stock_rets) * 100 if stock_rets else 0
        gate_wr = sum(1 for r in gated_rets if r > 0) / len(gated_rets) * 100 if gated_rets else 0
        full_avg = sum(stock_rets) / len(stock_rets) if stock_rets else 0
        gate_avg = sum(gated_rets) / len(gated_rets) if gated_rets else 0

        results_by_date.append({
            "date": rec_date_str, "risk": risk, "min_prob": min_prob, "max_n": max_n,
            "full_wr": full_wr, "gate_wr": gate_wr,
            "full_avg": full_avg, "gate_avg": gate_avg,
            "full_n": len(stock_rets), "gate_n": len(gated_rets),
        })

    # Summary
    if not results_by_date:
        print("No data")
        return

    print(f"{'日期':<12} {'风险':<8} {'全量胜率':<10} {'门控胜率':<10} {'提升':<8} {'全量均':<8} {'门控均':<8}")
    print("-" * 70)
    total_full_wins = 0; total_gate_wins = 0; total_full_n = 0; total_gate_n = 0
    total_full_sum = 0; total_gate_sum = 0

    for r in results_by_date:
        total_full_wins += sum(1 for _ in range(r["full_n"]) if r["full_wr"] > 0)  # approximation
        total_gate_wins += sum(1 for _ in range(r["gate_n"]) if r["gate_wr"] > 0)
        total_full_n += r["full_n"]
        total_gate_n += r["gate_n"]
        total_full_sum += r["full_avg"] * r["full_n"]
        total_gate_sum += r["gate_avg"] * r["gate_n"]

        lift = r["gate_wr"] - r["full_wr"]
        print(f"{r['date']:<12} {r['risk']:<8} {r['full_wr']:.1f}%{'':<5} {r['gate_wr']:.1f}%{'':<5} {lift:+.1f}%{'':<3} {r['full_avg']:+.2f}%{'':<3} {r['gate_avg']:+.2f}%")

    # Recalculate totals properly
    all_full = []; all_gate = []
    for r in results_by_date:
        all_full.append({"wr": r["full_wr"], "avg": r["full_avg"], "n": r["full_n"]})
        all_gate.append({"wr": r["gate_wr"], "avg": r["gate_avg"], "n": r["gate_n"]})

    full_wr_total = sum(r["wr"] * r["n"] for r in all_full) / sum(r["n"] for r in all_full)
    gate_wr_total = sum(r["wr"] * r["n"] for r in all_gate) / sum(r["n"] for r in all_gate)
    full_avg_total = sum(r["avg"] * r["n"] for r in all_full) / sum(r["n"] for r in all_full)
    gate_avg_total = sum(r["avg"] * r["n"] for r in all_gate) / sum(r["n"] for r in all_gate)

    print("-" * 70)
    print(f"{'总计':<12} {'':<8} {full_wr_total:.1f}%{'':<5} {gate_wr_total:.1f}%{'':<5} {gate_wr_total-full_wr_total:+.1f}%{'':<3} {full_avg_total:+.2f}%{'':<3} {gate_avg_total:+.2f}%")
    print(f"\n结论: 门控将全量{total_full_n}只 → {total_gate_n}只，胜率 {full_wr_total:.1f}% → {gate_wr_total:.1f}% ({gate_wr_total-full_wr_total:+.1f}%)")


asyncio.run(backtest_gate())
