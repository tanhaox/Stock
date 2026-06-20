#!/usr/bin/env python3
"""DNA Lab penetration test — Data layer."""
import asyncio, sys, json
sys.path.insert(0, '.')
from app.core.database import async_session_factory
from sqlalchemy import text

async def main():
    errors = []; passed = 0
    def chk(name, condition, detail=""):
        nonlocal passed
        if condition:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            errors.append(name)
            print(f"  [FAIL] {name} — {detail}")

    async with async_session_factory() as s:
        print("=" * 60)
        print("DNA LAB PENETRATION TEST — Data Layer")
        print("=" * 60)

        # ── Table existence ──
        print("\n--- Table Existence ---")
        r = await s.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='stock_dna'"))
        tables = sorted(r[0] for r in r.fetchall())
        chk("daily_samples exists", "daily_samples" in tables)
        chk("profiles exists", "profiles" in tables)
        chk("predictions exists", "predictions" in tables)

        # ── daily_samples integrity ──
        print("\n--- daily_samples Integrity ---")
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples"))
        n = r.fetchone()[0]
        chk("has data (>0)", n > 0, f"count={n}")
        chk(">= 2000 samples", n >= 2000, f"count={n}")

        # No NULL in critical columns
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples WHERE emotion_label IS NULL"))
        chk("emotion_label no NULLs", r.fetchone()[0] == 0)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples WHERE excess_ret_t5 IS NULL"))
        chk("excess_ret_t5 no NULLs", r.fetchone()[0] == 0)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples WHERE daily_features IS NULL"))
        chk("daily_features no NULLs", r.fetchone()[0] == 0)

        # MACD is now non-zero
        r = await s.execute(text(
            "SELECT COUNT(*) FROM stock_dna.daily_samples "
            "WHERE symbol='000001.SZ' AND (daily_features->>'macd_hist')::numeric != 0"
        ))
        macd_nonzero = r.fetchone()[0]
        chk("MACD non-zero rows > 100", macd_nonzero > 100, f"got {macd_nonzero}")

        r = await s.execute(text(
            "SELECT AVG(ABS((daily_features->>'macd_hist')::numeric)) "
            "FROM stock_dna.daily_samples WHERE symbol='000001.SZ'"
        ))
        chk("MACD avg abs > 0.01", float(r.fetchone()[0] or 0) > 0.01,
            f"avg={round(float(r.fetchone()[0] or 0), 4)}")

        # Per-stock row counts
        r = await s.execute(text(
            "SELECT symbol, COUNT(*) FROM stock_dna.daily_samples GROUP BY symbol ORDER BY symbol"
        ))
        all_ok = True
        for row in r.fetchall():
            ok = row[1] >= 100
            if not ok: all_ok = False
            print(f"    {row[0]}: {row[1]} rows {'OK' if ok else 'FAIL (<100)'}")
        chk("all stocks >= 100 samples", all_ok)

        # ── Emotion integrity ──
        print("\n--- Emotion Integrity ---")
        r = await s.execute(text(
            "SELECT symbol, COUNT(DISTINCT emotion_label) as d, n_emotions "
            "FROM stock_dna.daily_samples ds JOIN stock_dna.profiles p USING(symbol) "
            "GROUP BY symbol, n_emotions ORDER BY symbol"
        ))
        all_emo_ok = True
        for row in r.fetchall():
            distinct = row[1]
            n_emo = row[2] or 0
            ok = distinct >= 2
            if not ok: all_emo_ok = False
            print(f"    {row[0]}: {distinct} distinct labels (profile says {n_emo}) {'OK' if ok else 'FAIL'}")
        chk("all stocks have emotion diversity", all_emo_ok)

        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE transition_matrix IS NOT NULL"))
        chk("transition_matrix populated", r.fetchone()[0] == 8)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE stationary_dist IS NOT NULL"))
        chk("stationary_dist populated", r.fetchone()[0] == 8)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE emotion_entropy IS NOT NULL"))
        chk("emotion_entropy populated", r.fetchone()[0] == 8)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE emotion_names IS NOT NULL"))
        chk("emotion_names populated", r.fetchone()[0] == 8)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE best_emotion_ret IS NOT NULL"))
        chk("best_emotion_ret populated", r.fetchone()[0] == 8)

        # ── Cycle integrity ──
        print("\n--- Cycle Integrity ---")
        r = await s.execute(text(
            "SELECT COUNT(*) FROM stock_dna.profiles WHERE cycle_cv < 999"
        ))
        n_cycle = r.fetchone()[0]
        chk(">= 6 stocks have valid cycles", n_cycle >= 6, f"got {n_cycle}/8")

        r = await s.execute(text(
            "SELECT symbol, COUNT(*) FROM stock_dna.daily_samples "
            "WHERE cycle_phase != 'normal' GROUP BY symbol"
        ))
        cycle_rows = {row[0]: row[1] for row in r.fetchall()}
        n_non_normal = sum(cycle_rows.values())
        chk("non-normal cycle phases exist", n_non_normal > 0, f"total={n_non_normal}")
        for sym, cnt in sorted(cycle_rows.items()):
            print(f"    {sym}: {cnt} non-normal days")

        # ── Model files ──
        print("\n--- Model Files ---")
        import os
        model_dir = os.path.join(os.path.dirname(__file__), "..", "models", "dna")
        if os.path.exists(model_dir):
            files = os.listdir(model_dir)
            json_models = [f for f in files if f.endswith('_model.json')]
            t2 = [f for f in files if '_model_t2.json' in f]
            t10 = [f for f in files if '_model_t10.json' in f]
            t20 = [f for f in files if '_model_t20.json' in f]
            dna_json = [f for f in files if '_dna.json' in f]
            total_size = sum(os.path.getsize(os.path.join(model_dir, f)) for f in files)
            chk("T+5 models exist (>=8)", len(json_models) >= 8, f"got {len(json_models)}")
            chk("T+2 models exist (>=8)", len(t2) >= 8, f"got {len(t2)}")
            chk("T+10 models exist (>=8)", len(t10) >= 8, f"got {len(t10)}")
            chk("T+20 models exist (>=8)", len(t20) >= 8, f"got {len(t20)}")
            chk("DNA profiles exist (>=8)", len(dna_json) >= 8, f"got {len(dna_json)}")
            print(f"    Total: {len(files)} files, {total_size/1024:.0f} KB")
        else:
            chk("model dir exists", False, str(model_dir))

        # ── AUC distribution ──
        print("\n--- AUC Health ---")
        r = await s.execute(text(
            "SELECT symbol, best_horizon_auc, training_samples "
            "FROM stock_dna.profiles ORDER BY best_horizon_auc DESC"
        ))
        aucs = []
        for row in r.fetchall():
            auc = float(row[1] or 0)
            aucs.append(auc)
            overfit = "⚠️ OVERFIT" if auc > 0.85 else "OK"
            print(f"    {row[0]}: AUC={auc:.4f} T+{row[2]} {overfit}")
        avg_auc = sum(aucs) / len(aucs) if aucs else 0
        chk("avg AUC in healthy range (0.4-0.75)", 0.4 < avg_auc < 0.75, f"avg={avg_auc:.4f}")
        chk("no extreme overfit (all < 0.90)", all(a < 0.90 for a in aucs))

        # ── Data quality edge cases ──
        print("\n--- Edge Cases ---")
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples WHERE symbol NOT IN (SELECT symbol FROM stock_dna.profiles)"))
        chk("no orphan samples (all have profiles)", r.fetchone()[0] == 0)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE symbol NOT IN (SELECT DISTINCT symbol FROM stock_dna.daily_samples)"))
        chk("no orphan profiles (all have samples)", r.fetchone()[0] == 0)
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples WHERE excess_ret_t2 IS NULL AND excess_ret_t5 IS NULL AND excess_ret_t10 IS NULL AND excess_ret_t20 IS NULL"))
        chk("labels not all-NULL", r.fetchone()[0] < n * 0.5)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"DATA LAYER TEST COMPLETE")
    print(f"Passed: {passed}, Failed: {len(errors)}")
    if errors:
        print("\nFAILURES:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("ALL PASSED")
    print(f"{'=' * 60}")

asyncio.run(main())
