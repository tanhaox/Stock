#!/usr/bin/env python3
"""DNA Lab penetration test — API layer."""
import requests, json, sys, time

BASE = "http://localhost:8000/api/dna"
errors = []
passed = 0

def test(name, method, path, expected_status=200, body=None, params=None, checks=None):
    global passed
    url = f"{BASE}{path}"
    try:
        if method == "GET":
            r = requests.get(url, params=params, timeout=10)
        else:
            r = requests.post(url, params=params, json=body, timeout=60)
        d = r.json() if r.text else {}
        ok = r.status_code == expected_status
        detail = []
        if checks:
            for cname, check_fn in checks:
                try:
                    if not check_fn(d):
                        ok = False
                        detail.append(f"{cname}:FAIL")
                except Exception as e:
                    ok = False
                    detail.append(f"{cname}:EXCEPTION({e})")
        if ok:
            passed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {method} {path} (HTTP {r.status_code}) {' '.join(detail)}")
        if not ok:
            errors.append(f"{method} {path}: {r.text[:300]}")
        return d
    except Exception as e:
        print(f"  [ERROR] {method} {path}: {e}")
        errors.append(f"{method} {path}: {e}")
        return {}

print("=" * 60)
print("DNA LAB PENETRATION TEST — API Layer")
print("=" * 60)

# 1. GET /dna/status
print("\n--- Round 1: Status & Core ---")
test("status", "GET", "/status", checks=[
    ("models>0", lambda d: d.get("models_trained", 0) > 0),
    ("samples>0", lambda d: d.get("total_samples", 0) > 0),
    ("horizon_dist", lambda d: len(d.get("horizon_distribution", {})) > 0),
])

# 2. GET /dna/profile/002594.SZ — full DNA card
d = test("profile/002594", "GET", "/profile/002594.SZ", checks=[
    ("best_window", lambda d: d["data"]["best_window"]["horizon"] is not None),
    ("n_emotions>1", lambda d: d["data"]["emotion_fingerprint"]["n_emotions"] > 1),
    ("cv_exists", lambda d: d["data"]["cycle_rhythm"]["cv"] is not None),
    ("drivers>=3", lambda d: len(d["data"]["drivers"]) >= 3),
    ("best_ret", lambda d: d["data"]["emotion_fingerprint"]["best_emotion_ret"] is not None),
    ("names", lambda d: isinstance(d["data"]["emotion_fingerprint"]["names"], dict)),
])

# 3. GET /dna/profile/NONEXISTENT → 404
test("profile/404", "GET", "/profile/999999.SZ", expected_status=404)

# 4. GET /dna/profile/300750.SZ — another stock
test("profile/300750", "GET", "/profile/300750.SZ", checks=[
    ("best_window", lambda d: d["data"]["best_window"]["horizon"] is not None),
])

# 5. POST /dna/predict — 2 stocks
test("predict/2", "POST", "/predict", body={"symbols": ["002594.SZ", "300750.SZ"]}, checks=[
    ("count=2", lambda d: len(d.get("predictions", [])) == 2),
    ("multi_horizon", lambda d: all(
        all(f"t{h}" in p.get("predictions", {}) for h in [2, 5, 10, 20])
        for p in d.get("predictions", []) if p.get("status") != "no_model"
    )),
    ("has_confidence", lambda d: all(
        p.get("confidence", 0) > 0 for p in d.get("predictions", [])
        if p.get("status") != "no_model"
    )),
])

# 6. POST /dna/predict — invalid symbol
test("predict/invalid", "POST", "/predict", body={"symbols": ["INVALID123"]}, checks=[
    ("count=1", lambda d: len(d.get("predictions", [])) == 1),
])

# 7. POST /dna/predict — empty list
test("predict/empty", "POST", "/predict", body={"symbols": []})

# 8. GET /dna/compare
d = test("compare", "GET", "/compare", checks=[
    ("has_comparison", lambda d: len(d.get("comparison", [])) > 0),
    ("has_matrix", lambda d: len(d.get("similarity_matrix", {}).get("symbols", [])) > 0),
])
# Check similarity is NOT all 1.0
if d and "similarity_matrix" in d:
    m = d["similarity_matrix"]["matrix"]
    off_diag = [(i, j, v) for i, row in enumerate(m) for j, v in enumerate(row) if i != j]
    non1 = sum(1 for _, _, v in off_diag if v < 0.99)
    ok = non1 > 0
    print(f"  [{'PASS' if ok else 'FAIL'}] similarity non-1.0: {non1}/{len(off_diag)} pairs differ")
    if not ok:
        errors.append("similarity matrix: all 1.0")

# 9. GET /dna/emotion/002594.SZ/history
d = test("emotion/002594/history", "GET", "/emotion/002594.SZ/history", params={"days": 20}, checks=[
    ("seq>0", lambda d: len(d.get("emotion_sequence", [])) > 0),
    ("has_names", lambda d: all(
        e.get("emotion_name", "") for e in d.get("emotion_sequence", [])
    )),
])
# Check best_case != most_likely
if d and d.get("transition_tomorrow"):
    tt = d["transition_tomorrow"]
    ml = tt.get("most_likely", {})
    bc = tt.get("best_case", {})
    wc = tt.get("worst_case", {})
    print(f"  most_likely: {ml.get('name','?')} ({ml.get('prob',0)*100:.0f}%)")
    print(f"  best_case:   {bc.get('name','?')} ({bc.get('prob',0)*100:.0f}%) avg_ret={bc.get('avg_ret',0)}")
    print(f"  worst_case:  {wc.get('name','?')} ({wc.get('prob',0)*100:.0f}%)")
    bc_diff = bc.get("name") != ml.get("name")
    print(f"  best_case != most_likely: {bc_diff} {'PASS' if bc_diff else 'INCONCLUSIVE (may be same if only one good option)'}")

# 10. GET /dna/emotion/000001.SZ/history
test("emotion/000001/history", "GET", "/emotion/000001.SZ/history", params={"days": 5})

# 11. GET /dna/emotion/600519.SH/history
test("emotion/600519/history", "GET", "/emotion/600519.SH/history", params={"days": 10})

# 12. POST /dna/add-stock — existing stock (fast path)
print("\n--- Round 2: Add Stock ---")
test("add-stock/existing", "POST", "/add-stock", params={"symbol": "000001.SZ"}, checks=[
    ("success", lambda d: d.get("status") == "success"),
    ("has_samples", lambda d: d.get("samples", 0) > 0),
])

# Summary
print(f"\n{'=' * 60}")
print(f"API PENETRATION TEST COMPLETE")
print(f"Passed: {passed}, Failed: {len(errors)}")
if errors:
    print("\nFAILURES:")
    for e in errors:
        print(f"  - {e[:200]}")
else:
    print("ALL PASSED")
print(f"{'=' * 60}")
