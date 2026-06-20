#!/usr/bin/env python3
"""test_ma_score.py — 均线评分模块测试."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.services.ma_scorer import calc_ma_score

TEST_STOCKS = [
    ("000001.SZ", "平安银行"),
    ("600519.SH", "贵州茅台"),
    ("300750.SZ", "宁德时代"),
    ("000858.SZ", "五粮液"),
    ("601318.SH", "中国平安"),
    ("600036.SH", "招商银行"),
    ("002415.SZ", "海康威视"),
    ("688981.SH", "中芯国际"),
    ("601919.SH", "中远海控"),
    ("300059.SZ", "东方财富"),
]


async def main():
    print("=" * 70)
    print("均线趋势质量评分测试 (8-21-55-144-250 EMA)")
    print("=" * 70)
    print(f"{'代码':<12} {'名称':<8} {'评分':<6} {'乖离':<6} {'排列':<6} {'趋势':<6} {'筹码':<6} {'说明'}")
    print("-" * 70)

    scores = []
    for sym, name in TEST_STOCKS:
        result = await calc_ma_score(sym)
        if result is None:
            print(f"{sym:<12} {name:<8} {'N/A':<6} {'—':<6} {'—':<6} {'—':<6} {'—':<6} K线不足250根")
            continue
        scores.append(result["score"])
        chip_str = f"{result['chip']:.0f}" if result["has_chip"] else "N/A"
        print(f"{sym:<12} {name:<8} {result['score']:<6.1f} {result['deviation']:<6.1f} {result['alignment']:<6.1f} {result['trend']:<6.1f} {chip_str:<6} {result['details']}")

    if scores:
        print("-" * 70)
        print(f"样本数: {len(scores)} | 均值: {sum(scores)/len(scores):.1f} | 最高: {max(scores):.1f} | 最低: {min(scores):.1f}")
        print(f"分布: >=80: {sum(1 for s in scores if s>=80)} | 60-79: {sum(1 for s in scores if 60<=s<80)} | 40-59: {sum(1 for s in scores if 40<=s<60)} | <40: {sum(1 for s in scores if s<40)}")


if __name__ == "__main__":
    asyncio.run(main())
