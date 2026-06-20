"""全维度个股历史复盘 — 集成测试脚本.

包含四维共振分析 + 操盘手法反推。
用法:
    cd C:\AI-Agent-Local\Stock\backend
    python scripts\test_full_drill.py 600905.SH
"""
import asyncio, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from datetime import date
from app.services.stock_historical_drill import drill_stocks
from app.services.market_gate import get_market_state


async def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/test_full_drill.py <股票代码> [股票代码...]")
        print("示例: python scripts/test_full_drill.py 600905.SH 600742.SH")
        return

    symbols = sys.argv[1:]
    print("=" * 70)
    print(f"  Full Drill Test — {len(symbols)} stocks")
    print("=" * 70)

    regime = "unknown"
    try:
        ms = await get_market_state()
        regime = ms.get("regime", "unknown")
        print(f"Market regime: {regime}")
    except Exception:
        pass

    results = await drill_stocks(
        symbols=symbols, current_date=date.today(),
        market_regime=regime, force_refresh=True,
    )

    for sym, report in results.items():
        print(f"\n{'='*70}")
        print(f"  {sym}")
        print(f"{'='*70}")

        if report.get("status") != "ok":
            print(f"  Status: {report['status']} — {report.get('reason', '')}")
            continue

        # 信号有效性
        se = report.get("signal_effectiveness", {})
        print(f"  [Signal] status={se.get('status')} count={se.get('history_count')} "
              f"WR={se.get('win_rate_5d')} avgR={se.get('avg_return_5d')}")

        # 形态匹配
        pm = report.get("pattern_matching", {})
        print(f"  [Pattern] topN={len(pm.get('top_similar_segments',[]))} "
              f"pred5={pm.get('predicted_avg_return_5d')} WR={pm.get('predicted_win_rate_5d')}")

        # 关键位置
        cp = report.get("critical_position", {})
        print(f"  [Position] status={cp.get('status')} positions={len(cp.get('positions',[]))}")

        # 筹码模拟
        cs = report.get("chip_simulation", {})
        print(f"  [Chip] status={cs.get('status')} trend={cs.get('trend')} AR={cs.get('current_ar')}")

        # 市场敏感性
        mks = report.get("market_sensitivity", {})
        print(f"  [Market] independence={mks.get('independence_score')}")

        # ── 四维共振 ──
        res = report.get("resonance", {})
        if res and res.get("status") != "insufficient":
            print(f"  [Resonance]")
            for dim in ["index_resonance", "sector_resonance", "news_resonance", "chip_resonance"]:
                d = res.get(dim, {})
                s = d.get("summary", d.get("status", "?"))
                print(f"    {dim}: {s}")
        else:
            print(f"  [Resonance] {res.get('status', 'N/A')}")

        # ── 操盘手法 ──
        mb = report.get("micro_behavior", {})
        if mb and mb.get("status") != "unavailable":
            print(f"  [MicroBehavior]")
            for at in ["fast_rise", "fast_fall"]:
                a = mb.get(at, {})
                if a.get("insufficient_data"):
                    print(f"    {at}: insufficient ({a.get('reason','')[:60]})")
                elif a.get("status") == "success":
                    trigs = a.get("top_triggers", [])
                    cs = a.get("current_status", {})
                    print(f"    {at}: {a.get('total_actions')} actions, {len(trigs)} triggers, "
                          f"active={cs.get('is_any_trigger_active')}")
                    for t in trigs[:3]:
                        print(f"      -> {t['condition']} hit={t['hit_rate']} lift={t['lift_vs_random']}")
        else:
            print(f"  [MicroBehavior] {mb.get('status', 'N/A')}")

        # 总结
        print(f"\n  [Summary] {report.get('drill_summary', 'N/A')}")

    print(f"\n{'='*70}")
    print(f"  Drill test complete")
    print(f"{'='*70}")


if __name__ == "__main__":
    asyncio.run(main())
