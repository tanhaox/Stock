"""Backtest Phase D: compare holding to maturity vs using exit signals.

For each historical recommendation, simulate:
1. Buy at entry date close
2. Strategy A (hold): hold to T+5, sell at close
3. Strategy B (signal): check each day for exit signals, exit on first trigger
4. Strategy C (trailing stop): 5% trailing stop from peak
5. Strategy D (fixed T+2): always sell at T+2 close

Compare returns across strategies.
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
            "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= '2026-05-01' ORDER BY trade_date"
        ))
        all_tdays = [str(row[0]) for row in r.fetchall()]

    # Collect raw price series for each recommendation
    all_trades = []

    for rec_date_str, codes in recs.items():
        actual_day = None
        for td in all_tdays:
            if td >= rec_date_str: actual_day = td; break
        if not actual_day: continue
        try:
            idx = all_tdays.index(actual_day)
        except ValueError: continue

        # Get up to T+10 prices
        end_idx = min(idx + 11, len(all_tdays))
        day_range = all_tdays[idx:end_idx]
        if len(day_range) < 3: continue

        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT ts_code, trade_date, high, low, close FROM daily_kline
                WHERE ts_code = ANY(:c) AND trade_date BETWEEN :d0 AND :d1
                ORDER BY ts_code, trade_date
            """), {"c": codes, "d0": date.fromisoformat(day_range[0]),
                   "d1": date.fromisoformat(day_range[-1])})
            rows = r.fetchall()

        price_series = defaultdict(list)
        for row in rows:
            price_series[row[0]].append({
                "date": str(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
            })

        for code in codes:
            series = price_series.get(code, [])
            if len(series) < 3: continue
            entry = series[0]["close"]

            # Strategy A: Hold to T+5
            t5_idx = min(5, len(series) - 1)
            hold_ret = (series[t5_idx]["close"] - entry) / entry

            # Strategy B: Exit on signal (trailing stop 5%)
            peak = entry
            signal_ret = None
            signal_day = 0
            for i, bar in enumerate(series[1:], 1):  # Start from T+1
                peak = max(peak, bar["high"])
                dd_from_peak = (bar["close"] - peak) / peak
                ret_from_entry = (bar["close"] - entry) / entry

                # Exit triggers
                exit_triggered = False
                if dd_from_peak < -0.05 and (peak - entry) / entry > 0.03:
                    exit_triggered = True  # trailing stop
                elif ret_from_entry < -0.05:
                    exit_triggered = True  # stop loss
                elif ret_from_entry > 0.10:
                    exit_triggered = True  # take profit

                if exit_triggered:
                    signal_ret = ret_from_entry
                    signal_day = i
                    break

            if signal_ret is None:
                signal_ret = (series[-1]["close"] - entry) / entry
                signal_day = len(series) - 1

            # Strategy C: Pure trailing stop 5%
            peak = entry
            trail_ret = None
            for i, bar in enumerate(series[1:], 1):
                peak = max(peak, bar["high"])
                dd = (bar["close"] - peak) / peak
                if dd < -0.05:
                    trail_ret = (bar["close"] - entry) / entry
                    break

            if trail_ret is None:
                trail_ret = (series[-1]["close"] - entry) / entry

            # Strategy D: Fixed T+2
            t2_idx = min(2, len(series) - 1)
            t2_ret = (series[t2_idx]["close"] - entry) / entry

            all_trades.append({
                "code": code,
                "entry_date": actual_day,
                "hold_ret": round(hold_ret * 100, 2),
                "signal_ret": round(signal_ret * 100, 2),
                "signal_day": signal_day,
                "trail_ret": round(trail_ret * 100, 2),
                "t2_ret": round(t2_ret * 100, 2),
                "peak_ret": round((max(b["high"] for b in series) - entry) / entry * 100, 2),
            })

    # Summary
    n = len(all_trades)
    if n == 0:
        print("No data"); return

    strategies = {
        "持有到T+5": [t["hold_ret"] for t in all_trades],
        "信号退出": [t["signal_ret"] for t in all_trades],
        "纯移动止盈": [t["trail_ret"] for t in all_trades],
        "固定T+2": [t["t2_ret"] for t in all_trades],
    }

    print(f"样本数: {n}\n")
    print(f"{'策略':<16} {'胜率':<10} {'平均收益':<10} {'中位数':<10} {'最大':<10} {'最小':<10}")
    print("-" * 70)
    for name, rets in strategies.items():
        wins = sum(1 for r in rets if r > 0)
        avg = sum(rets) / len(rets)
        sorted_rets = sorted(rets)
        med = sorted_rets[len(sorted_rets)//2]
        print(f"{name:<16} {wins/n*100:.1f}%{'':<5} {avg:+.2f}%{'':<5} {med:+.2f}%{'':<5} {max(rets):+.2f}%{'':<5} {min(rets):+.2f}%")

    # Peak capture rate
    total_peak = sum(max(0, t["peak_ret"]) for t in all_trades)
    for name, rets in strategies.items():
        total_ret = sum(max(0, r) for r in rets)
        capture = total_ret / total_peak * 100 if total_peak > 0 else 0
        print(f"  {name}: 峰值捕获率 {capture:.1f}%")

    # Win rate improvement
    base_wr = sum(1 for t in all_trades if t["hold_ret"] > 0) / n * 100
    sig_wr = sum(1 for t in all_trades if t["signal_ret"] > 0) / n * 100
    print(f"\n结论: 信号退出 vs 持有到期: 胜率 {base_wr:.1f}% -> {sig_wr:.1f}% (+{sig_wr-base_wr:.1f}%)")


asyncio.run(backtest())
