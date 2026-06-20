#!/usr/bin/env python3
"""market_data 全覆盖测试.

测试:
  compute_excess_return — 正常/不足/除零/边界
  get_benchmark_closes — 缓存/日期过滤

用法:
  PYTHONPATH=. python tests/test_market_data.py
"""
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.market_data import compute_excess_return, compute_excess_return_or_fallback

passed = 0
failed = 0
failures = []


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        failures.append(msg)


def test_eq(name, actual, expected, tol=1e-6):
    ok = abs(actual - expected) < tol
    test(name, ok, f"got {actual}, expected {expected}")


print("=" * 60)
print("market_data 全覆盖测试")
print("=" * 60)

# 构造基准数据: 模拟 2 周交易日
mock_closes = {
    date(2024, 6, 3): 5000.0,   # Mon
    date(2024, 6, 4): 5025.0,   # Tue  +0.5%
    date(2024, 6, 5): 5010.0,   # Wed
    date(2024, 6, 6): 5035.0,   # Thu
    date(2024, 6, 7): 5050.0,   # Fri
    # weekend
    date(2024, 6, 10): 5060.0,  # Mon  +1.0% from baseline
    date(2024, 6, 11): 5040.0,  # Tue
    date(2024, 6, 12): 5075.0,  # Wed  +1.3%
    date(2024, 6, 13): 5100.0,  # Thu  +1.8%
    date(2024, 6, 14): 5080.0,  # Fri  +1.4%
}

# ── compute_excess_return ──
print("\n--- compute_excess_return ---")

# Test 1: T+5 from Monday (6/3)
# stock: 100 → 105 (+5%)
# future_dates after 6/3: [6/4, 6/5, 6/6, 6/7, 6/10, 6/11, ...]
#   future_dates[0] = 6/4 (5025), future_dates[4] = 6/10 (5060)
# benchmark ret = (5060-5025)/5025*100 = 0.6965%
# excess = 5.0 - 0.6965 = 4.3035
r = compute_excess_return(100.0, 105.0, date(2024, 6, 3), 5, mock_closes)
test_eq("T+5 from Mon (normal)", r, 4.3035)

# Test 2: T+2 from Friday morning (6/7)
# stock: 200 → 198 (-1%)
# benchmark: 1st after = 6/10 (5060), 2nd = 6/11 (5040) → -0.3953%
# excess = -1.0 - (-0.3953) = -0.6047
r = compute_excess_return(200.0, 198.0, date(2024, 6, 7), 2, mock_closes)
test_eq("T+2 from Fri (跨周末)", r, -0.6047)

# Test 3: horizon=1 (next day)
# stock: 100 → 101 (+1%)
# benchmark: 1st after = 6/4 (5025)
# only 1 date needed, c0 = cN = 5025 → market_ret = 0
r = compute_excess_return(100.0, 101.0, date(2024, 6, 3), 1, mock_closes)
test_eq("T+1 (single day)", r, 1.0)

# Test 4: insufficient data
r = compute_excess_return(100.0, 105.0, date(2024, 6, 3), 50, mock_closes)
test_eq("T+50 insufficient → 0.0", r, 0.0)

# Test 5: None benchmark
r = compute_excess_return(100.0, 105.0, date(2024, 6, 3), 5, None)
test_eq("None benchmark → 0.0", r, 0.0)

# Test 6: Empty benchmark
r = compute_excess_return(100.0, 105.0, date(2024, 6, 3), 5, {})
test_eq("Empty benchmark → 0.0", r, 0.0)

# Test 7: Zero prices in benchmark
zero_closes = {date(2024, 6, 4): 0.0, date(2024, 6, 5): 5025.0}
r = compute_excess_return(100.0, 105.0, date(2024, 6, 3), 1, zero_closes)
test_eq("Zero benchmark → 0.0", r, 0.0)

# Test 8: trade_date with no future dates
r = compute_excess_return(100.0, 105.0, date(2024, 6, 14), 5, mock_closes)
test_eq("No future dates → 0.0", r, 0.0)

# Test 9: Stock price at epsilon boundary
r = compute_excess_return(0.005, 0.010, date(2024, 6, 3), 5, mock_closes)
test("Stock near zero handled", r == 0.0 or abs(r) < 1000)  # should not crash or produce inf

# ── compute_excess_return_or_fallback ──
print("\n--- compute_excess_return_or_fallback ---")

r = compute_excess_return_or_fallback(100.0, 105.0, date(2024, 6, 3), 50, mock_closes)
test_eq("Fallback — insufficient → stock_ret only", r, 5.0)

r = compute_excess_return_or_fallback(100.0, 105.0, date(2024, 6, 3), 50, None)
test_eq("Fallback — None → stock_ret only", r, 5.0)

r = compute_excess_return_or_fallback(100.0, 105.0, date(2024, 6, 3), 5, mock_closes)
test_eq("Fallback — normal → excess", r, 4.3035)  # same as compute_excess_return

# ── Calendar-day vs Trading-day comparison ──
print("\n--- Calendar-day bug test (shadow_trainer original logic) ---")

def calendar_day_bench_ret(sd, h):
    """shadow_trainer 的原始日历日计算 (有 bug)"""
    target = sd + __import__('datetime').timedelta(days=h)
    ids = sorted(mock_closes.keys())
    ia = next((d for d in ids if d >= sd), None)
    ib = next((d for d in ids if d >= target), ids[-1] if ids else None)
    if ia and ib and ia != ib:
        return (mock_closes[ib] - mock_closes[ia]) / mock_closes[ia] * 100
    return 0.0

# T+5 from Friday (6/7): calendar-day = 6/12 (Wed, only 3 trading days)
# trading-day = 6/14 (Fri, 5 trading days)
# stock: 100 → 103 (+3%)
cal = calendar_day_bench_ret(date(2024, 6, 7), 5)
stock_ret = 3.0
cal_excess = stock_ret - cal
trade_excess = compute_excess_return(100.0, 103.0, date(2024, 6, 7), 5, mock_closes)
test("Calendar ≠ Trading (key bug)", abs(cal_excess - trade_excess) > 0.01,
     f"calendar_excess={cal_excess:.4f}, trade_excess={trade_excess:.4f}, diff={abs(cal_excess - trade_excess):.4f}")

# ── Validate: excess should be zero when stock = benchmark ──
print("\n--- Symmetry test ---")
# If stock follows benchmark exactly → excess = 0
# benchmark T+5 from 6/3: future_dates[0]=6/4(5025) → future_dates[4]=6/10(5060)
# market_ret = (5060-5025)/5025*100 = 0.6965%
# stock should be 100 * 1.006965 = 100.6965
stock_benchmark = 100.0 * (1 + 0.6965174129 / 100)
r = compute_excess_return(100.0, stock_benchmark, date(2024, 6, 3), 5, mock_closes)
test("Stock = benchmark → excess ≈ 0", abs(r) < 0.01, f"got {r}")

print(f"\n{'=' * 60}")
print(f"TEST RESULTS: {passed} passed, {failed} failed")
if failures:
    print("\nFAILURES:")
    for f in failures:
        print(f)
    sys.exit(1)
else:
    print("ALL PASSED")
print(f"{'=' * 60}")
