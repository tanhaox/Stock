"""Weekly/periodic background tasks — extracted from background_sync.py (Phase 7)."""
import logging
from datetime import date
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("scheduler.weekly")


async def task_scoring_weight_training():
    """Monday: Scoring weight training via Logistic Regression."""
    from app.services.scoring_trainer import full_training_pipeline
    result = await full_training_pipeline()
    logger.info(
        f"Weight training: {result.get('training',{}).get('n_samples',0)} samples "
        f"AUC={result.get('training',{}).get('cv_auc',0):.3f} "
        f"persist={result.get('persist',{}).get('status','?')}"
    )
    return result


async def task_probability_recalibration():
    """Sunday: Probability calibrator recalibration (with regime)."""
    from app.services.probability_calibrator import scheduled_recalibrate_with_regime
    result = await scheduled_recalibrate_with_regime()
    global_info = result.get("global", {})
    regime_info = result.get("regimes", {})
    logger.info(f"Recal: global={global_info.get('archetypes',0)} archs, regimes={list(regime_info.keys())}")
    return result


async def task_archetype_calibration():
    """First Sunday of month: Archetype offset calibration."""
    from app.services.archetype_param_resolver import apply_calibration
    result = await apply_calibration(min_samples=30, lookback_days=180)
    logger.info(f"Archetype cal: applied={result.get('applied',0)}, skipped={result.get('skipped',0)}")
    return result


async def task_dual_channel_training():
    """Sunday: Dual channel model training (angel + guardian)."""
    from app.services.dual_channel_trainer import train_dual_channel
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(*) FROM signal_history"))
        sig_count = r.scalar() or 0
    if sig_count >= 100:
        result = await train_dual_channel()
        logger.info(f"Dual channel: {result.get('status','?')}, samples={result.get('samples',0)}")
        return result
    logger.info(f"Dual channel skip: {sig_count} samples (<100)")
    return {"status": "skipped", "reason": f"insufficient samples ({sig_count})"}


async def task_veteran_backtest():
    """Saturday: Veteran breakout rate backtest."""
    from app.services.alphaflow_veteran import backtest_veteran_breakout_rate
    result = await backtest_veteran_breakout_rate()
    logger.info(f"Veteran BT: {result.get('total_veterans',0)} vets, T+20={result.get('breakout_rate_20d',0)}%")
    return result


async def task_shadow_evaluation():
    """Sunday: Shadow model evaluation + auto-switch."""
    from app.services.shadow_trainer import evaluate_shadow_vs_main
    result = await evaluate_shadow_vs_main()
    logger.info(f"Shadow eval: {result.get('verdict','?')}, switched={result.get('auto_switched',[])}")
    return result


async def task_retrain_predictive_model():
    """Sunday: Retrain XGBoost predictive model on latest signal_history."""
    try:
        from scripts.train_predictive_model import main as retrain
        await retrain()
        logger.info("Predictive model retrained successfully")
    except Exception as e:
        logger.error(f"Predictive model retrain failed: {e}")
