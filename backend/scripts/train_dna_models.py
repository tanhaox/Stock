#!/usr/bin/env python3
"""Per-Stock DNA model training entry script.

Trains a lightweight XGBoost model per stock (multi-horizon output).

Usage:
  PYTHONPATH=. python scripts/train_dna_models.py
  PYTHONPATH=. python scripts/train_dna_models.py --symbols 000001.SZ,600519.SH
  PYTHONPATH=. python scripts/train_dna_models.py --symbol 000001.SZ
"""
import asyncio, sys, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    parser = argparse.ArgumentParser(description="Train per-stock DNA models")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated stock codes")
    parser.add_argument("--symbol", type=str, default=None, help="Single stock code")
    args = parser.parse_args()

    from app.services.stock_dna.model import train_per_stock, train_all
    from app.services.stock_dna.dna_models import ensure_dna_tables

    print("Init database tables...")
    await ensure_dna_tables()
    print("[OK] Database tables ready\n")

    if args.symbol:
        print(f"Training single stock: {args.symbol}")
        result = await train_per_stock(args.symbol)
        print(f"\n{'='*60}")
        print(f"Training complete!")
        print(f"  Status: {result['status']}")
        if result["status"] == "success":
            print(f"  Samples: {result['n_samples']}")
            print(f"  AUC_T5: {result.get('auc_t5', 'N/A')}")
            print(f"  Best horizon: T+{result.get('best_horizon', 'N/A')}")
            print(f"  Top 3 features:")
            for tf in result.get("top_features", [])[:3]:
                print(f"    - {tf['name']}: {tf['importance']:.4f}")
        else:
            print(f"  Reason: {result.get('reason', 'unknown')}")

    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        print(f"Training {len(symbols)} stocks...")

        def progress_cb(phase, current, total, msg):
            print(f"  [{current}/{total}] {msg}")

        result = await train_all(symbols, progress_cb=progress_cb)
        print(f"\n{'='*60}")
        print(f"Batch training complete!")
        print(f"  Total: {result['total']}")
        print(f"  Success: {result['trained']}")
        print(f"  Failed: {result['failed']}")
        print(f"  Avg AUC_T5: {result['avg_auc_t5']:.4f}")

    else:
        print("Training all stocks with DNA data...")
        result = await train_all(progress_cb=lambda p, c, t, m: print(f"  [{c}/{t}] {m}"))
        print(f"\n{'='*60}")
        print(f"Batch training complete!")
        print(f"  Total: {result['total']}")
        print(f"  Success: {result['trained']}")
        print(f"  Failed: {result['failed']}")
        print(f"  Avg AUC_T5: {result['avg_auc_t5']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
