"""Analyze stock recommendation win rates from exported txt files.

Reads all recommendation txt files from Downloads, queries daily_kline for
price data, and calculates win rates at T+1, T+3, T+5 holding periods.
"""
import os, re, glob, asyncio
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text, select
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from app.core.database import async_session_factory

DOWNLOADS = r"C:\Users\tanha\Downloads"


def parse_files():
    """Parse all recommendation txt files. Returns dict: {date_str: set(stock_codes)}"""
    results = defaultdict(set)
    # Match both naming patterns:
    # stocks_2026-05-15.txt
    # 推荐股票_2026-05-07.txt / 推荐股票_2026-05-08 (1).txt
    patterns = [
        os.path.join(DOWNLOADS, "stocks_*.txt"),
        os.path.join(DOWNLOADS, "推荐股票_*.txt"),
    ]
    for pat in patterns:
        for fpath in glob.glob(pat):
            basename = os.path.basename(fpath)
            # Extract date from filename
            m = re.search(r'(\d{4}-\d{2}-\d{2})', basename)
            if not m:
                continue
            file_date = m.group(1)
            # Read stocks
            with open(fpath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    code = line.upper()
                    # Normalize: use global normalize_ts_code (handles 9xx BJ and edge cases)
                    from app.utils.stock_code import normalize_ts_code
                    code = normalize_ts_code(code)
                    if not code:
                        continue
                    # Validate format
                    if not re.match(r'\d{6}\.(SZ|SH|BJ)', code):
                        continue
                    results[file_date].add(code)
    return {d: sorted(codes) for d, codes in sorted(results.items())}


async def get_trading_days(start_date: str, count: int = 10):
    """Get the next `count` trading days starting from start_date (inclusive)."""
    sd = date.fromisoformat(start_date)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT trade_date FROM daily_kline
            WHERE trade_date >= :sd
            ORDER BY trade_date
            LIMIT :lim
        """), {"sd": sd, "lim": count})
        return [row[0] for row in r.fetchall()]


async def get_prices(codes: list[str], target_date: str):
    """Get close prices for given codes on a specific date."""
    td = date.fromisoformat(target_date)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, close FROM daily_kline
            WHERE ts_code = ANY(:codes) AND trade_date = :td
        """), {"codes": codes, "td": td})
        return {row[0]: float(row[1]) for row in r.fetchall()}


async def analyze():
    recommendations = parse_files()
    print(f"Total recommendation files: {sum(len(v) for v in recommendations.values())} records")
    print(f"Unique dates: {len(recommendations)}")
    print(f"Date range: {min(recommendations.keys())} ~ {max(recommendations.keys())}")
    print()

    # For each date, get trading days and calculate returns
    all_results = []  # list of {code, rec_date, rec_price, t1_price, t1_ret, t3_price, t3_ret, ...}

    for rec_date_str, codes in recommendations.items():
        # Get trading days: rec_date + next 10 trading days
        trading_days = await get_trading_days(rec_date_str, count=11)
        if not trading_days:
            print(f"  {rec_date_str}: no trading data, skipping {len(codes)} stocks")
            continue

        actual_rec_day = trading_days[0]  # first available trading day on or after rec_date

        # Get prices on recommendation day
        rec_day_str = str(actual_rec_day)
        rec_prices = await get_prices(codes, rec_day_str)

        # Get prices on T+1, T+3, T+5
        t_days = {}
        for label, idx in [("T+1", 1), ("T+3", 3), ("T+5", 5)]:
            if idx < len(trading_days):
                t_days[label] = str(trading_days[idx])

        future_prices = {}
        for label, day_str in t_days.items():
            future_prices[label] = await get_prices(codes, day_str)

        for code in codes:
            rec_price = rec_prices.get(code)
            if rec_price is None:
                continue

            result = {
                "code": code,
                "rec_date": rec_day_str,
                "rec_price": rec_price,
            }
            for label in ["T+1", "T+3", "T+5"]:
                fp = future_prices.get(label, {}).get(code)
                if fp is not None:
                    ret = round((fp - rec_price) / rec_price * 100, 2)
                    result[f"{label}_price"] = fp
                    result[f"{label}_ret"] = ret
                else:
                    result[f"{label}_price"] = None
                    result[f"{label}_ret"] = None
            all_results.append(result)

    # ── Summary statistics ──
    print(f"\n{'='*80}")
    print(f"Total valid recommendations (with price data): {len(all_results)}")
    print()

    for label in ["T+1", "T+3", "T+5"]:
        valid = [r for r in all_results if r.get(f"{label}_ret") is not None]
        if not valid:
            print(f"{label}: no data")
            continue

        wins = [r for r in valid if r[f"{label}_ret"] > 0]
        flat = [r for r in valid if r[f"{label}_ret"] == 0]
        losses = [r for r in valid if r[f"{label}_ret"] < 0]

        avg_ret = sum(r[f"{label}_ret"] for r in valid) / len(valid)
        avg_win = sum(r[f"{label}_ret"] for r in wins) / len(wins) if wins else 0
        avg_loss = sum(r[f"{label}_ret"] for r in losses) / len(losses) if losses else 0

        print(f"--- {label} (持有{label[2:]}个交易日) ---")
        print(f"  样本数: {len(valid)}")
        print(f"  胜率: {len(wins)}/{len(valid)} = {len(wins)/len(valid)*100:.1f}%")
        print(f"  平: {len(flat)} | 负: {len(losses)}")
        print(f"  平均收益: {avg_ret:+.2f}%")
        print(f"  平均盈利: {avg_win:+.2f}% | 平均亏损: {avg_loss:+.2f}%")
        print(f"  盈亏比: {abs(avg_win/avg_loss) if avg_loss else 0:.2f}")
        print()

    # ── By date breakdown ──
    print(f"{'='*80}")
    print("按推荐日期分解 (T+3):")
    print(f"{'日期':<12} {'推荐数':<8} {'有效':<8} {'胜率':<10} {'平均收益':<10}")
    print("-" * 50)
    for rec_date_str in sorted(recommendations.keys()):
        day_results = [r for r in all_results if r["rec_date"] == rec_date_str or r["rec_date"] >= rec_date_str]
        # Get results for this specific recommendation date
        trading_days = await get_trading_days(rec_date_str, count=6)
        if trading_days:
            actual_day = str(trading_days[0])
            day_results = [r for r in all_results if r["rec_date"] == actual_day]
            valid = [r for r in day_results if r.get("T+3_ret") is not None]
            if valid:
                wins = [r for r in valid if r["T+3_ret"] > 0]
                avg_r = sum(r["T+3_ret"] for r in valid) / len(valid)
                print(f"{rec_date_str:<12} {len(codes):<8} {len(valid):<8} {len(wins)/len(valid)*100:.1f}%{'':<5} {avg_r:+.2f}%")

    # ── Top/Bottom performers ──
    print(f"\n{'='*80}")
    print("T+3 收益最高 Top 10:")
    valid_t3 = sorted(
        [r for r in all_results if r.get("T+3_ret") is not None],
        key=lambda r: r["T+3_ret"], reverse=True
    )[:10]
    for r in valid_t3:
        print(f"  {r['code']} | 推荐日 {r['rec_date']} | 买入 {r['rec_price']:.2f} | "
              f"T+3 {r.get('T+3_price', 0):.2f} | 收益 {r['T+3_ret']:+.2f}%")

    print(f"\nT+3 收益最低 Bottom 10:")
    valid_t3_bottom = sorted(
        [r for r in all_results if r.get("T+3_ret") is not None],
        key=lambda r: r["T+3_ret"]
    )[:10]
    for r in valid_t3_bottom:
        print(f"  {r['code']} | 推荐日 {r['rec_date']} | 买入 {r['rec_price']:.2f} | "
              f"T+3 {r.get('T+3_price', 0):.2f} | 收益 {r['T+3_ret']:+.2f}%")


if __name__ == "__main__":
    asyncio.run(analyze())
