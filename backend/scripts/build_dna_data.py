#!/usr/bin/env python3
"""DNA 训练数据生成入口脚本.

从 daily_kline + min_kline 为持仓 + 推荐股票生成训练样本。
完全并行于现有系统。

用法:
  PYTHONPATH=. python scripts/build_dna_data.py
  PYTHONPATH=. python scripts/build_dna_data.py --symbols 000001.SZ,600519.SH
"""
import asyncio, sys, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.stock_dna.data_builder import build_dna_data
from app.services.stock_dna.dna_models import ensure_dna_tables


async def main():
    parser = argparse.ArgumentParser(description="Build DNA training data")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated stock codes")
    parser.add_argument("--start", type=str, default="2022-01-01", help="Start date YYYY-MM-DD")
    args = parser.parse_args()

    # 确保表存在
    print("Init database tables...")
    await ensure_dna_tables()
    print("[OK] Database tables ready")

    # 解析股票列表
    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        print(f"Target stocks: {len(symbols)} (manual)")

    print(f"\nGenerating data (start: {args.start})...")

    def progress_cb(phase, current, total, msg):
        if phase == "dna_data":
            print(f"  [{current}/{total}] {msg}")

    result = await build_dna_data(symbols=symbols, start_date=args.start, progress_cb=progress_cb)

    print(f"\n{'='*60}")
    print(f"Build complete!")
    print(f"  Status: {result['status']}")
    print(f"  Total samples: {result['total_samples']}")
    print(f"  Processed: {result['symbols_processed']} stocks")
    if result.get("errors"):
        print(f"  Errors ({len(result['errors'])}):")
        for e in result["errors"][:5]:
            print(f"    - {e}")


if __name__ == "__main__":
    asyncio.run(main())
