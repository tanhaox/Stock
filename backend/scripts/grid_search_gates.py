"""参数网格搜索 — 在历史数据上测试不同质量门槛组合.

目标: 找到使 T+2 平均收益最大化的 (SQ, WP, trend) 阈值组合.
"""
import asyncio, sys, json, logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)


async def grid_search_quality_gates() -> dict:
    """在 signal_history 上网格搜索最优质量门槛.

    Returns:
        {best_combo: {sq, wp, trend, avg_return, n_passing}, all_results: [...]}
    """
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT sh.symbol, sh.scan_date, sh.composite_score, sh.archetype,
                   sh.market, sh.push_count_30d, sh.price_zone_width_pct,
                   sh.ret_t2, sh.outcome_label, sh.deception_type
            FROM signal_history sh
            WHERE sh.ret_t2 IS NOT NULL AND sh.scan_date >= :cutoff
        """), {"cutoff": date.today() - timedelta(days=60)})
        rows = r.fetchall()

    print(f"Grid search on {len(rows)} historical signals")

    # 参数网格
    sq_levels = [0.5, 0.55, 0.6, 0.65, 0.7]
    wp_levels = [0.40, 0.45, 0.50, 0.55]
    trend_levels = [2, 3, 4]
    min_n = 5  # 最少需要N只股票才算有效

    # 为每条记录估算 SQ/WP (用规则引擎近似, 不调用完整模型以加速)
    results = []
    for sq_min in sq_levels:
        for wp_min in wp_levels:
            for trend_min in trend_levels:
                passing = []
                for row in rows:
                    score = float(row[2] or 0)
                    push = int(row[5] or 1)
                    ret_t2 = float(row[7] or 0)

                    # 规则引擎近似 SQ: 高推送=低质量
                    est_sq = min(0.9, max(0.1, 0.75 - push * 0.05))
                    # 规则引擎近似 WP: 分数越高越乐观
                    est_wp = min(0.7, max(0.2, (score - 30) / 100 + 0.3))
                    # 趋势近似: 用 signal_history 中的 price_zone_width 反推
                    pz = float(row[6] or 10)
                    est_trend = 2 if pz > 8 else 3  # 窄幅=可能盘整, 宽幅=有趋势

                    if est_sq >= sq_min and est_wp >= wp_min and est_trend >= trend_min:
                        passing.append(ret_t2)

                if len(passing) >= min_n:
                    avg_ret = sum(passing) / len(passing)
                    results.append({
                        "sq": sq_min, "wp": wp_min, "trend": trend_min,
                        "n": len(passing), "avg_return": round(avg_ret, 2),
                        "pct_passing": round(len(passing) / len(rows) * 100, 1),
                    })

    results.sort(key=lambda x: x["avg_return"], reverse=True)

    print(f"\n{'SQ':<6} {'WP':<6} {'Trend':<7} {'AvgRet':<9} {'N':<6} {'%Pass':<8}")
    print("-" * 46)
    for r in results[:15]:
        print(f"{r['sq']:<6.2f} {r['wp']:<6.2f} {r['trend']:<7} "
              f"{r['avg_return']:>+8.2f}% {r['n']:<6} {r['pct_passing']:<8.1f}")

    best = results[0] if results else None
    if best:
        print(f"\nBest: SQ≥{best['sq']:.2f} WP≥{best['wp']:.2f} trend≥{best['trend']} "
              f"→ avg T+2 return {best['avg_return']:+.2f}% (n={best['n']})")

    return {"best": best, "results": results}


async def main():
    import logging as _log
    _log.basicConfig(level=_log.WARNING)
    result = await grid_search_quality_gates()
    # 输出最优组合建议
    best = result.get("best")
    if best:
        print(f"\n建议质量门槛: QUALITY_GATE = {{")
        print(f'  "min_signal_quality": {best["sq"]:.2f},')
        print(f'  "min_win_probability": {best["wp"]:.2f},')
        print(f'  "min_trend_score": {best["trend"]},')
        print(f"}}  # avg T+2 return = {best['avg_return']:+.2f}%")

if __name__ == "__main__":
    asyncio.run(main())
