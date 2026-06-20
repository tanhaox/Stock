"""方案 B 回测验证 — 统计三种共振类型的 T+5/T+10/T+20 实际表现.

用法:
    cd C:\AI-Agent-Local\Stock\backend
    python scripts\backtest_weekly_resonance.py

输出: 按 daily_only / weekly_resonance / weekly_driven 分组统计收益和胜率
"""
import asyncio, sys, os
import numpy as np
from datetime import date, timedelta
from collections import defaultdict
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy import text
from app.core.database import async_session_factory


async def backtest(lookback_days: int = 180):
    """回测过去 N 天，按 resonance_type 统计各周期收益."""
    cutoff = date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        # 加载交易日历
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline "
            "WHERE trade_date >= :cutoff ORDER BY trade_date"
        ), {"cutoff": cutoff})
        trading_days = [row[0] for row in r.fetchall()]

    if len(trading_days) < 30:
        print("交易日不足 30 天, 无法回测")
        return

    # 加载分析记录 + 共振类型
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT a.symbol, a.scan_date, a.composite_score,
                   COALESCE(s.resonance_type, 'daily_only') as resonance_type,
                   s.close_price
            FROM analysis_scores a
            LEFT JOIN scan_results s ON s.symbol = a.symbol AND s.scan_date = a.scan_date
            WHERE a.scan_date >= :cut
            ORDER BY a.scan_date, a.symbol
        """), {"cut": cutoff})
        rows = r.fetchall()

    if not rows:
        print("无分析记录, 无法回测")
        return

    # 预加载日线价格
    symbols = list(set(r[0] for r in rows))
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, close
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date >= :cut
            ORDER BY ts_code, trade_date
        """), {"syms": symbols, "cut": cutoff})
        prices: dict[tuple, float] = {}
        for row in r.fetchall():
            prices[(row[0], row[1])] = float(row[2])

    # 按 resonance_type 分组统计
    stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "periods": {"T+5": [], "T+10": [], "T+20": []},
    })

    td_strs = [str(td) for td in trading_days]
    skipped_missing = 0

    for sym, scan_date, score, resonance, close_price in rows:
        scan_str = str(scan_date)
        try:
            idx = td_strs.index(scan_str)
        except ValueError:
            skipped_missing += 1
            continue

        entry_price = close_price

        for label, offset in [("T+5", 5), ("T+10", 10), ("T+20", 20)]:
            if idx + offset >= len(td_strs):
                continue
            exit_date = trading_days[idx + offset]
            exit_price = prices.get((sym, exit_date))
            if exit_price and exit_price > 0 and entry_price and entry_price > 0:
                ret = (exit_price - entry_price) / entry_price * 100
                stats[resonance]["periods"][label].append(ret)
            else:
                skipped_missing += 1

        stats[resonance]["count"] += 1

    # ── 输出 ──
    print(f"\n{'='*80}")
    print(f"  方案 B 周线共振回测 (近 {lookback_days} 天)")
    print(f"{'='*80}")
    print(f"  分析记录: {len(rows)} 条 | 交易日: {len(trading_days)} | 跳过缺失: {skipped_missing}")
    print()

    # 表头
    header = f"{'共振类型':<20} {'样本':>6} {'T+5胜率':>10} {'T+5均收%':>10} {'T+10胜率':>10} {'T+10均收%':>10} {'T+20胜率':>10} {'T+20均收%':>10}"
    print(header)
    print("-" * len(header))

    # 按优先级排序
    type_order = ["weekly_resonance", "daily_only", "weekly_driven"]
    type_labels = {
        "weekly_resonance": "⭐ 日线+周线共振",
        "daily_only": "   仅日线驱动",
        "weekly_driven": "📅 周线驱动",
    }

    summary_lines = []
    for rt in type_order:
        if rt not in stats:
            continue
        s = stats[rt]
        cnt = s["count"]

        line_parts = [f"{type_labels.get(rt, rt):<20} {cnt:>6}"]
        for label in ["T+5", "T+10", "T+20"]:
            returns = s["periods"][label]
            if len(returns) >= 5:
                wr = sum(1 for r in returns if r > 0) / len(returns) * 100
                avg = np.mean(returns)
                line_parts.append(f"{wr:>9.1f}% {avg:>9.2f}%")
            else:
                line_parts.append(f"{'—':>10} {'—':>10}")

        line = " ".join(line_parts)
        print(line)

        # 收集汇总
        t5_returns = s["periods"]["T+5"]
        t10_returns = s["periods"]["T+10"]
        summary_lines.append({
            "type": rt,
            "label": type_labels.get(rt, rt),
            "count": cnt,
            "t5_wr": round(sum(1 for r in t5_returns if r > 0) / len(t5_returns) * 100, 1) if len(t5_returns) >= 5 else None,
            "t5_avg": round(np.mean(t5_returns), 2) if len(t5_returns) >= 5 else None,
            "t10_wr": round(sum(1 for r in t10_returns if r > 0) / len(t10_returns) * 100, 1) if len(t10_returns) >= 5 else None,
            "t10_avg": round(np.mean(t10_returns), 2) if len(t10_returns) >= 5 else None,
            "t20_wr": round(sum(1 for r in t20_returns if r > 0) / len(t20_returns) * 100, 1) if len(t20_returns) >= 5 else None,
            "t20_avg": round(np.mean(t20_returns), 2) if len(t20_returns) >= 5 else None,
        })

    print()
    print("─" * 80)

    # 检查样本量
    warnings = []
    for s in summary_lines:
        if s["count"] < 30:
            warnings.append(f"⚠ {s['label']}: 仅 {s['count']} 条样本 (<30), 统计不可靠")

    if warnings:
        print("  ⚠ 样本量警告:")
        for w in warnings:
            print(f"    {w}")

    # 结论
    print()
    print("  结论:")
    if len(summary_lines) >= 2:
        res = [s for s in summary_lines if s["type"] == "weekly_resonance"]
        daily = [s for s in summary_lines if s["type"] == "daily_only"]
        if res and daily:
            r_t5 = res[0].get("t5_wr")
            d_t5 = daily[0].get("t5_wr")
            if r_t5 and d_t5 and r_t5 > d_t5:
                print(f"    ✅ 共振信号 T+5 胜率 ({r_t5}%) > 纯日线 ({d_t5}%) — 双周期共振有效")
            elif r_t5 and d_t5:
                print(f"    ❌ 共振信号 T+5 胜率 ({r_t5}%) ≤ 纯日线 ({d_t5}%) — 需进一步分析")
            else:
                print(f"    ⏳ 样本不足, 无法比较共振 vs 日线")

    print(f"\n{'='*80}\n")
    return summary_lines


if __name__ == "__main__":
    asyncio.run(backtest())
