#!/usr/bin/env python3
"""numpy_utils 全覆盖自动化测试.

覆盖:
  safe_float     — None/NaN/Inf/正常值/边界值/异常类型
  safe_auc       — AUC 专用 0.5 回退
  safe_rsi       — RSI 专用 50 回退
  sanitize_array — NaN 替换/Inf 替换/无 NaN/空数组
  sanitize_for_json — 标量/dict/list/ndarray/嵌套/np.bool_
  div0           — 正常除/零除/NaN 除/Inf 除
  safe_corrcoef  — 正常相关/常数序列/短序列/长度不匹配
  check_array_quality — 统计正确性

用法:
  PYTHONPATH=. python tests/test_numpy_utils.py
"""
import sys
import math
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from app.utils.numpy_utils import (
    safe_float, safe_auc, safe_rsi,
    sanitize_array, sanitize_label_array, sanitize_for_json,
    div0, safe_corrcoef, safe_corrcoef_or_half,
    check_array_quality,
)

passed = 0
failed = 0
failures = []


def test(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        msg = f"  FAIL: {name}"
        if detail:
            msg += f" — {detail}"
        failures.append(msg)


def test_eq(name: str, actual, expected, tolerance=1e-9):
    if isinstance(expected, float):
        ok = abs(actual - expected) < tolerance
    else:
        ok = actual == expected
    test(name, ok, f"got {actual!r}, expected {expected!r}")


print("=" * 60)
print("numpy_utils 全覆盖测试")
print("=" * 60)

# ── safe_float ──
print("\n--- safe_float ---")

test_eq("None → 0.0", safe_float(None), 0.0)
test_eq("NaN → 0.0", safe_float(float('nan')), 0.0)
test_eq("NaN → custom -1", safe_float(float('nan'), default=-1.0), -1.0)
test_eq("Inf → 0.0", safe_float(float('inf')), 0.0)
test_eq("-Inf → 0.0", safe_float(float('-inf')), 0.0)
test_eq("正常 int", safe_float(42), 42.0)
test_eq("正常 float", safe_float(3.14), 3.14)
test_eq("正常 str", safe_float("3.14"), 3.14)
test_eq("str 带 default", safe_float("hello", default=5.0), 5.0)
test_eq("空 str", safe_float("", default=5.0), 5.0)
test_eq("0.0 不是 NaN", safe_float(0.0), 0.0)
test_eq("bool True", safe_float(True), 1.0)
test_eq("bool False", safe_float(False), 0.0)
test_eq("np.float64 NaN", safe_float(np.float64('nan')), 0.0)
test_eq("np.float64 正常", safe_float(np.float64(1.5)), 1.5)
test_eq("np.inf → 0.0", safe_float(np.inf), 0.0)

# ── safe_auc ──
print("\n--- safe_auc ---")
test_eq("AUC NaN → 0.5", safe_auc(float('nan')), 0.5)
test_eq("AUC 正常", safe_auc(0.78), 0.78)
test_eq("AUC None → 0.5", safe_auc(None), 0.5)
test_eq("AUC Inf → 0.5", safe_auc(float('inf')), 0.5)

# ── safe_rsi ──
print("\n--- safe_rsi ---")
test_eq("RSI NaN → 50", safe_rsi(float('nan')), 50.0)
test_eq("RSI 正常", safe_rsi(65.3), 65.3)
test_eq("RSI None → 50", safe_rsi(None), 50.0)

# ── sanitize_array ──
print("\n--- sanitize_array ---")
arr1 = np.array([1.0, np.nan, 3.0, np.inf, 5.0])
sanitize_array(arr1)
test("NaN replaced", not np.isnan(arr1).any(), f"arr={arr1}")
test("Inf replaced", not np.isinf(arr1).any())
test_eq("normal values preserved", arr1[0], 1.0)

arr2 = np.array([np.nan, np.nan])
sanitize_array(arr2, fill=99.0)
test("custom fill NaN→99", arr2[0] == 99.0 and arr2[1] == 99.0)

arr3 = np.array([1.0, 2.0, 3.0])
sanitize_array(arr3)
test_eq("no NaN array unchanged[0]", arr3[0], 1.0)
test_eq("no NaN array unchanged[1]", arr3[1], 2.0)

arr4 = np.array([])
sanitize_array(arr4)
test("empty array handled", len(arr4) == 0)

arr5 = np.float32([np.nan, np.inf, -np.inf, 0.0, -1.0])
sanitize_array(arr5)
test("float32 NaN cleaned", not np.isnan(arr5).any())
test("float32 Inf cleaned", not np.isinf(arr5).any())

# ── sanitize_for_json ──
print("\n--- sanitize_for_json ---")
test_eq("None → None", sanitize_for_json(None), None)
test_eq("float NaN → None", sanitize_for_json(float('nan')), None)
test_eq("float Inf → None", sanitize_for_json(float('inf')), None)
test_eq("float normal", sanitize_for_json(3.14), 3.14)
test_eq("int normal", sanitize_for_json(42), 42)
test_eq("str normal", sanitize_for_json("hello"), "hello")
test_eq("bool normal", sanitize_for_json(True), True)
test_eq("np.float64 NaN → None", sanitize_for_json(np.float64('nan')), None)
test_eq("np.int64", sanitize_for_json(np.int64(42)), 42)
test_eq("np.bool_", sanitize_for_json(np.bool_(True)), True)
test_eq("list with NaN", sanitize_for_json([1.0, float('nan'), 3.0]), [1.0, None, 3.0])
test_eq("dict with NaN", sanitize_for_json({"a": 1.0, "b": float('nan')}), {"a": 1.0, "b": None})
test_eq("nested", sanitize_for_json({"a": [1.0, {"b": float('nan')}]}), {"a": [1.0, {"b": None}]})
test_eq("ndarray", sanitize_for_json(np.array([1.0, float('nan')])), [1.0, None])

# JSON serialization test: must not crash
import json
d = {"score": float('nan'), "items": [1.0, float('inf'), 3.0], "nested": {"x": float('-inf')}}
try:
    json.dumps(sanitize_for_json(d))
    test("JSON serialization after sanitize", True)
except (ValueError, TypeError) as e:
    test("JSON serialization after sanitize", False, str(e))

# Without sanitize — should fail with allow_nan=False
try:
    json.dumps(d, allow_nan=False)
    test("JSON raw NaN should fail", False, "expected ValueError but got success")
except (ValueError,):
    test("JSON raw NaN correctly raises ValueError", True)

# ── div0 ──
print("\n--- div0 ---")
test_eq("正常除法", div0(10.0, 2.0), 5.0)
test_eq("除以 0 → default", div0(10.0, 0.0), 0.0)
test_eq("除以 0 → custom", div0(10.0, 0.0, default=999.0), 999.0)
test_eq("分母接近 0", div0(10.0, 1e-10), 0.0)
test_eq("NaN 分子 → 0", div0(float('nan'), 2.0), 0.0)
test_eq("NaN 分母 → 0", div0(10.0, float('nan')), 0.0)

# ── safe_corrcoef ──
print("\n--- safe_corrcoef ---")
a1 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
b1 = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
test_eq("完美正相关", safe_corrcoef(a1, b1), 1.0)
a2 = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
b2 = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
test_eq("完美负相关", safe_corrcoef(a2, b2), -1.0)
# 常数序列
a3 = np.array([5.0, 5.0, 5.0, 5.0])
b3 = np.array([1.0, 2.0, 3.0, 4.0])
test_eq("常数序列 → default 0.0", safe_corrcoef(a3, b3), 0.0)
# 短序列
test_eq("短序列 → default", safe_corrcoef(np.array([1.0]), np.array([2.0])), 0.0)
# 长度不匹配
test_eq("长度不匹配 → default", safe_corrcoef(a1, np.array([1.0, 2.0])), 0.0)
# safe_corrcoef_or_half
test_eq("corr_or_half 常数 → 0.5", safe_corrcoef_or_half(a3, b3), 0.5)

# ── check_array_quality ──
print("\n--- check_array_quality ---")
quality = check_array_quality(np.array([1.0, np.nan, np.inf, 4.0, 5.0]))
test_eq("总元素数", quality["total"], 5)
test_eq("NaN 数", quality["nan_count"], 1)
test_eq("Inf 数", quality["inf_count"], 1)
test_eq("有效率", quality["valid_rate"], 3.0 / 5.0)
quality2 = check_array_quality(np.array([]))
test_eq("空数组 total=0", quality2["total"], 0)

# ── Summary ──
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
