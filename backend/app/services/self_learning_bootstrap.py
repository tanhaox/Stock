"""自学习冷启动 — 一键启动影子训练 + 历史回填 + 增量调度.

P3 v1.0 (2026-05-31):
  - 从 daily_kline 重建 100 天历史回测数据 → learning_predictions
  - 扰动初始化影子权重 (param_library is_shadow=true)
  - 每日增量训练 (收盘后 4 秒即可完成)
  - 亏损信号自动标记 → experience_replay 优先级提高

用法:
  python -m app.services.self_learning_bootstrap       # 完整冷启动
  python -m app.services.self_learning_bootstrap --quick  # 仅增量训练
"""
import asyncio, logging, numpy as np, os, sys
from datetime import date, timedelta
from sqlalchemy import text
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from app.core.database import async_session_factory

logger = logging.getLogger("self_learning")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")


# ═══════════════════════════════════════════════════════════
# 扰动初始化影子权重
# ═══════════════════════════════════════════════════════════

async def bootstrap_shadow_weights():
    """从现实层权重生成影子权重 — 每个维度 ±20% 随机扰动.

    确保影子从'附近但不同'的位置开始, 既有区分度又不离谱.
    """
    import json
    async with async_session_factory() as s:
        # 取所有活跃的现实层权重
        r = await s.execute(text("""
            SELECT archetype, scoring_weights, version
            FROM param_library
            WHERE is_shadow = false AND is_active = true
        """))
        reality_weights = [(row[0], row[1], row[2]) for row in r.fetchall()]

    if not reality_weights:
        logger.warning("No active reality weights found, using defaults")
        # 使用默认权重
        default_weights = {
            "tg_momentum_weight": 2.5, "tech_weight": 3.0, "kline_weight": 3.0,
            "fund_weight": 2.5, "vol_ratio_weight": 1.5, "arbr_weight": 1.0,
            "sector_alpha_weight": 1.5, "market_relative_weight": 1.5,
            "valuation_weight": 1.0, "ma_trend_weight": 1.5,
            "pattern_weight": 1.5, "trend_deviation_weight": 1.5,
            "bbi_weight": 1.5, "box_weight": 2.0, "fundamentals_weight": 1.5,
            "ambush_weight": 1.5,
        }
        reality_weights = [("__default__", default_weights, "v1")]

    np.random.seed(42)
    strategies = ["S1", "S2", "S3"]
    created = 0

    async with async_session_factory() as s:
        for arch, weights_dict, version in reality_weights:
            if isinstance(weights_dict, str):
                weights_dict = json.loads(weights_dict)

            for strategy in strategies:
                # 扰动: 每个权重 × U(0.7, 1.3), 但保持总量不变
                perturbed = {}
                for k, v in weights_dict.items():
                    perturbed[k] = round(v * np.random.uniform(0.7, 1.3), 3)

                # 归一化到原总和
                orig_sum = sum(weights_dict.values())
                new_sum = sum(perturbed.values())
                if new_sum > 0:
                    perturbed = {k: round(v * orig_sum / new_sum, 3) for k, v in perturbed.items()}

                new_version = f"{version}_shadow_{strategy}"

                await s.execute(text("""
                    INSERT INTO param_library
                    (archetype, strategy, version, parent_version, scoring_weights,
                     is_shadow, is_active, converge_status, discrimination,
                     consecutive_days, last_trained_at, created_at)
                    VALUES (:a, :st, :v, :pv, CAST(:sw AS jsonb),
                            true, true, 'training', 0.50, 0, CURRENT_DATE, NOW())
                    ON CONFLICT (archetype, strategy, version) DO UPDATE SET
                        scoring_weights = CAST(:sw AS jsonb),
                        is_active = true, converge_status = 'training',
                        last_trained_at = CURRENT_DATE
                """), {
                    "a": arch, "st": strategy, "v": new_version,
                    "pv": version, "sw": json.dumps(perturbed),
                })
                created += 1

        await s.commit()

    logger.info(f"Bootstrap: {created} shadow weights created ({len(reality_weights)} archetypes × {len(strategies)} strategies)")
    return created


# ═══════════════════════════════════════════════════════════
# 亏损信号标记 → experience_replay
# ═══════════════════════════════════════════════════════════

async def mark_loss_signals(days_back: int = 90):
    """把历史亏损推荐标记到 experience_replay, 提高反馈优先级."""
    async with async_session_factory() as s:
        # 找到已验证的亏损推荐
        r = await s.execute(text("""
            SELECT symbol, scan_date, composite_score, archetype,
                   COALESCE(return_5d, return_3d, return_15d) as actual_return
            FROM recommendation_tracking
            WHERE (verified_3d = TRUE OR verified_5d = TRUE)
              AND scan_date >= :cutoff
              AND (was_profitable_3d = FALSE OR was_profitable_5d = FALSE)
            ORDER BY scan_date DESC
        """), {"cutoff": date.today() - timedelta(days=days_back)})
        loss_records = [(row[0], row[1], float(row[2] or 0), row[3] or '__global__',
                         float(row[4] or 0)) for row in r.fetchall()]

    if not loss_records:
        logger.info("No loss signals to mark")
        return 0

    import json
    marked = 0
    async with async_session_factory() as s:
        for sym, sd, score, arch, ret in loss_records:
            # 检查是否已标记
            r = await s.execute(text("""
                SELECT 1 FROM experience_replay
                WHERE meta_info->>'symbol' = :s
                  AND meta_info->>'scan_date' = :sd
                  AND event_type = 'loss_signal'
            """), {"s": sym, "sd": str(sd)})
            if r.fetchone():
                continue

            await s.execute(text("""
                INSERT INTO experience_replay
                (event_type, recorded_at, reward, meta_info, archetype, category_tags)
                VALUES ('loss_signal', :rd, :rew, CAST(:mi AS jsonb), :a, CAST(:tags AS jsonb))
            """), {
                "rd": sd, "rew": round(float(ret), 3),
                "mi": json.dumps({"symbol": sym, "scan_date": str(sd),
                                  "composite_score": score, "actual_return": ret}),
                "a": arch,
                "tags": json.dumps(["loss", f"ret_{ret:.1f}%", arch]),
            })
            marked += 1

        await s.commit()

    logger.info(f"Marked {marked} loss signals in experience_replay")
    return marked


# ═══════════════════════════════════════════════════════════
# 每日增量训练 (极轻量, 4秒完成)
# ═══════════════════════════════════════════════════════════

async def daily_incremental_train():
    """每日增量训练 — 基于真实盈亏反馈更新评分权重.

    v2.0 (2026-06-03): 移除 np.random.uniform 伪随机模拟,
    改为调用 scoring_trainer.full_training_pipeline() 基于真实数据训练.
    """
    try:
        from app.services.scoring_trainer import full_training_pipeline

        today = date.today()
        # 每周一次完整训练 (非周一返回空, 避免每天重复训练相同数据)
        if today.weekday() != 0:
            return {"trained": 0, "method": "skipped", "reason": "not_monday"}

        # ★ 冷启动保护: 确保有足够的真实盈亏样本
        from app.core.database import async_session_factory
        from sqlalchemy import text as sql_text
        async with async_session_factory() as s:
            r = await s.execute(sql_text(
                "SELECT COUNT(*) FROM recommendation_tracking WHERE was_profitable_3d IS NOT NULL"
            ))
            sample_count = r.scalar() or 0
        if sample_count < 30:
            logger.info(f"冷启动保护: 仅 {sample_count} 条盈亏样本 (< 30), 跳过训练")
            return {"trained": 0, "method": "skipped", "reason": "insufficient_samples",
                    "sample_count": sample_count, "min_required": 30}

        result = await full_training_pipeline(lookback_days=120)
        logger.info(
            f"Incremental train: {result.get('training', {}).get('n_samples', 0)} samples, "
            f"AUC={result.get('training', {}).get('cv_auc', 0):.3f}"
        )
        return {"trained": 1, "method": "logistic_regression", "result": result}
    except Exception as e:
        logger.warning(f"Incremental training failed: {e}")
        return {"trained": 0, "method": "error", "reason": str(e)}


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

async def bootstrap_all():
    """完整冷启动: 扰动影子权重 + 标记亏损信号 + 增量训练."""
    logger.info("=" * 60)
    logger.info("  Self-Learning Bootstrap v1.0")
    logger.info("=" * 60)

    n_shadow = await bootstrap_shadow_weights()
    n_marked = await mark_loss_signals()
    n_trained = await daily_incremental_train()

    logger.info(f"Bootstrap complete: {n_shadow} shadows, {n_marked} marked, {n_trained} trained")
    return {"shadows": n_shadow, "marked": n_marked, "trained": n_trained}


if __name__ == "__main__":
    asyncio.run(bootstrap_all())
