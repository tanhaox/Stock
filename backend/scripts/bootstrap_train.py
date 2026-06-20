"""Bootstrap Training v3 — 独立运行, 不依赖 API 异步上下文.

直接调用 run_training_round_from_kline, 串行训练, 避免连接池冲突.
"""
import asyncio, sys, json, logging, time
from collections import defaultdict
sys.path.insert(0, r'C:\AI-Agent-Local\Stock\backend')
from app.core.database import async_session_factory
from app.services.shadow_trainer import (
    run_training_round_from_kline, DEFAULT_WEIGHTS, FORECAST_HORIZONS,
    generate_candidates, _get_active_weights,
)
from sqlalchemy import text

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger("bootstrap")

STRATEGIES = ["S1", "S3"]
ARCHETYPES = ["large_bluechip", "growth_tech", "value_defensive",
              "cyclical_resource", "small_speculative"]
PHASES = ["bull", "bear", "range"]
N_ITERATIONS = 10
LOOKBACK = 300


async def sample_stocks():
    """分层抽样 (同 v2)."""
    from datetime import date, timedelta
    today = date.today()

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, tag_value FROM stock_dimension_tags WHERE dim_name='sector'"))
        sector_map = defaultdict(list)
        for row in r.fetchall(): sector_map[row[1]].append(row[0])

        r = await s.execute(text("""
            SELECT DISTINCT ON (ts_code) ts_code, total_mv
            FROM daily_basic WHERE total_mv IS NOT NULL
            ORDER BY ts_code, trade_date DESC"""))
        mcap = {row[0]: float(row[1] or 0) for row in r.fetchall()}

        cutoff = today - timedelta(days=60)
        r = await s.execute(text("""
            SELECT ts_code, (MAX(close)-MIN(close))/NULLIF(MIN(close),0)*100
            FROM daily_kline WHERE trade_date>=:c GROUP BY ts_code"""), {"c": cutoff})
        perf = {row[0]: float(row[1] or 0) for row in r.fetchall()}

        r = await s.execute(text(
            "SELECT DISTINCT symbol, archetype FROM analysis_scores WHERE archetype = ANY(:a)"),
            {"a": ARCHETYPES})
        arch_stocks = defaultdict(list)
        for row in r.fetchall(): arch_stocks[row[1]].append(row[0])

    sampled = {}
    for arch in ARCHETYPES:
        stocks = arch_stocks.get(arch, [])
        # 按市值排序, 均匀取样 ~100只 (覆盖大盘/中盘/小盘)
        ranked = sorted(stocks, key=lambda s: mcap.get(s, 0), reverse=True)
        target = min(100, len(ranked))
        step = max(1, len(ranked) // target)
        selected = ranked[::step][:target]
        # 确保首尾都有 (最大和最小的都包含)
        if selected and ranked[0] not in selected:
            selected[0] = ranked[0]
        if selected and ranked[-1] not in selected:
            selected[-1] = ranked[-1]
        sampled[arch] = list(dict.fromkeys(selected))
        logger.info(f"  {arch}: {len(sampled[arch])} stocks (market cap {mcap.get(selected[0],0):.0f} ~ {mcap.get(selected[-1],0):.0f})")

    return sampled


async def train_one(arch, st, n_iterations, lookback):
    """训练单个原型+策略, 返回最优权重和分数."""
    base_weights = dict(DEFAULT_WEIGHTS)
    best_weights = dict(base_weights)
    best_score = -float('inf')
    no_improve = 0

    for iteration in range(n_iterations):
        # 生成候选
        candidates = generate_candidates(best_weights, 3)
        iter_best = None
        iter_best_score = -float('inf')

        for ci, cand in enumerate(candidates):
            # 在所有 phase 上评估
            phase_scores = []
            for phase in PHASES:
                try:
                    result = await run_training_round_from_kline(
                        arch, st, cand, lookback_days=lookback, phase_filter=phase)
                    score = result.get("sharpe", 0)
                    phase_scores.append(score)
                except Exception as e:
                    logger.warning(f"    {arch}/{st} phase={phase} error: {e}")
                    phase_scores.append(0)

            avg_score = sum(phase_scores) / len(phase_scores) if phase_scores else 0
            if avg_score > iter_best_score:
                iter_best = cand
                iter_best_score = avg_score

        if iter_best and iter_best_score > best_score:
            best_score = iter_best_score
            best_weights = dict(iter_best)
            no_improve = 0
        else:
            no_improve += 1

        if (iteration + 1) % 5 == 0:
            logger.info(f"  {arch}/{st} iter {iteration+1}/{n_iterations}: best={best_score:.4f}")

        if no_improve >= 8:
            logger.info(f"  Converged at iteration {iteration+1}")
            break

    return best_weights, best_score


async def main():
    # Phase 1: 抽样
    logger.info("Phase 1: Sampling...")
    sampled = await sample_stocks()
    total = sum(len(v) for v in sampled.values())
    logger.info(f"Total: {total} stocks\n")

    # 等待连接释放
    await asyncio.sleep(2)

    # Phase 2: 训练
    logger.info("Phase 2: Training...")
    t0 = time.time()
    total_jobs = len(ARCHETYPES) * len(STRATEGIES)
    all_results = {}

    for ji, arch in enumerate(ARCHETYPES):
        for st in STRATEGIES:
            logger.info(f"[{ji*len(STRATEGIES)+1}/{total_jobs}] {arch}/{st} ({N_ITERATIONS} iters, {LOOKBACK}d)...")
            try:
                weights, score = await train_one(arch, st, N_ITERATIONS, LOOKBACK)
                all_results[f"{arch}/{st}"] = {"score": round(score, 4), "weights": weights}
                logger.info(f"  Done: score={score:.4f} weights={dict((k,round(v,2)) for k,v in sorted(weights.items())[:5])}...")
            except Exception as e:
                logger.error(f"  FAILED: {e}")
                all_results[f"{arch}/{st}"] = {"score": 0, "error": str(e)}

    elapsed = time.time() - t0
    logger.info(f"\nPhase 2 complete: {elapsed/60:.0f}min")

    # Phase 3: 自动升级
    logger.info("Phase 3: Auto-upgrading to active...")
    upgraded = 0
    async with async_session_factory() as s:
        for arch in ARCHETYPES:
            for st in STRATEGIES:
                key = f"{arch}/{st}"
                if "error" in all_results.get(key, {}): continue
                w = all_results[key]["weights"]
                acc = all_results[key]["score"]
                await s.execute(text("""
                    INSERT INTO param_library (id, archetype, strategy, is_shadow,
                        scoring_weights, backtest_accuracy, discrimination,
                        converge_status, last_trained_at, n_selections, version,
                        is_active, month, market_style, created_at, updated_at)
                    VALUES (gen_random_uuid(), :a, :st, false,
                        CAST(:w AS jsonb), :acc, :disc,
                        'bootstrapped', NOW(), 0, 'bootstrap-v3', true, 1,
                        'all', NOW(), NOW())
                """), {"a": arch, "st": st, "w": json.dumps(w),
                       "acc": round(acc, 4), "disc": round(acc, 4)})
                upgraded += 1
        await s.commit()

    logger.info(f"Upgraded {upgraded} weights to active")
    logger.info(f"\nResults: {json.dumps({k: v['score'] for k, v in all_results.items()}, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
