"""个股历史深度复盘 — 本地测试脚本.

用法:
    cd C:\AI-Agent-Local\Stock\backend
    python scripts\test_drill.py 000001.SZ

输出:
    四项复盘结果 + 总结
"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from datetime import date
from app.services.stock_historical_drill import drill_stocks
from app.services.market_gate import get_market_state


async def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/test_drill.py <股票代码> [股票代码...]")
        print("示例: python scripts/test_drill.py 000001.SZ 600036.SH")
        return

    symbols = sys.argv[1:]
    print(f"\n{'='*70}")
    print(f"  个股历史深度复盘 — {len(symbols)} 只股票")
    print(f"{'='*70}\n")

    regime = "unknown"
    try:
        ms = await get_market_state()
        regime = ms.get("regime", "unknown")
        print(f"市场体制: {regime}\n")
    except Exception:
        pass

    async def progress(idx, total, sym, extra=None):
        pct = idx / total * 100
        bar = "=" * int(pct // 5) + ">" if idx < total else "=" * 20
        extra_str = f" ({extra})" if extra else ""
        print(f"\r  [{bar:<20}] {pct:.0f}% {sym}{extra_str}", end="", flush=True)

    results = await drill_stocks(
        symbols=symbols, current_date=date.today(),
        market_regime=regime, force_refresh=True,
        progress_callback=progress,
    )

    print("\n")
    for sym, report in results.items():
        print(f"━━━ {sym} ━━━")

        if report.get("status") != "ok":
            print(f"  状态: {report['status']} — {report.get('reason', '')}")
            continue

        # 信号有效性
        se = report.get("signal_effectiveness", {})
        if se.get("status") == "success":
            print(f"  [SIGNAL] {se.get('history_count',0)} times | " +
                  f"T+5 WR {se.get('win_rate_5d',0)*100:.0f}% | " +
                  f"AvgR {se.get('avg_return_5d',0):+.1f}% | " +
                  f"PLR {se.get('profit_loss_ratio',0)}")
        else:
            print(f"  [SIGNAL] {se.get('message', se.get('status', 'N/A'))}")

        # 形态匹配
        pm = report.get("pattern_matching", {})
        if pm.get("status") == "success":
            print(f"  📈 形态匹配: Top-{len(pm.get('top_similar_segments',[]))} 相似 | "
                  f"预测T+5: {pm.get('predicted_avg_return_5d',0):+.1f}% | "
                  f"胜率 {pm.get('predicted_win_rate_5d',0)*100:.0f}%")
        else:
            print(f"  📈 形态匹配: {pm.get('message', pm.get('status', 'N/A'))}")

        # 关键位置
        cp = report.get("critical_position", {})
        if cp.get("positions"):
            print(f"  🎯 关键位置: {cp.get('verdict', len(cp['positions']))}")
        else:
            print(f"  🎯 关键位置: {cp.get('message', '无关键位置')}")

        # 筹码模拟
        cs = report.get("chip_simulation", {})
        if cs.get("status") == "success":
            print(f"  💰 筹码吸收: 当前AR {cs.get('current_ar',0)*100:.0f}% | "
                  f"趋势 {cs.get('trend','?')} | "
                  f"锁死 {cs.get('lock_days',0)}天")
            curve = cs.get("absorption_curve", [])
            if len(curve) >= 2:
                first_ar = curve[0]["ar_lock"] * 100
                last_ar = curve[-1]["ar_lock"] * 100
                print(f"     吸收率: {first_ar:.0f}% → {last_ar:.0f}% ({len(curve)}段)")
        else:
            print(f"  💰 筹码吸收: {cs.get('message', cs.get('status', 'N/A'))}")

        # 敏感性
        ms = report.get("market_sensitivity", {})
        if ms.get("status") == "success":
            print(f"  🌐 市场敏感性: {ms.get('verdict', '')}")

        # 总结
        print(f"\n  💡 总结: {report.get('drill_summary', 'N/A')}")
        print(f"  ⏱ 耗时: {report.get('elapsed_s', '?')}s")
        print()

    print(f"\n{'='*70}\n  复盘完成\n{'='*70}\n")


if __name__ == "__main__":
    asyncio.run(main())
