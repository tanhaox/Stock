"""系统健康检查与自动升级触发器 (v4.3).

当分段权重、分段校准器等达到数据门槛时，自动标记为可用，
避免"数据够了但没人启用"的被动等待。

集成:
  - background_sync.py daily_task 每日 16:00 调用
  - MonitorPage 的 GET /api/system/readiness 展示
"""
import logging
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("system_health")

# ── 门槛常量 (与 deep_scorer 保持一致) ──
MIN_REGIME_SAMPLES = 50
MIN_REGIME_PARAMS = 10
MIN_REGIME_AUC = 0.55
MIN_CAL_SAMPLES = 100
MIN_ARCHETYPE_SAMPLES = 50


async def get_readiness_report() -> dict:
    """聚合所有组件的就绪状态，供 MonitorPage 展示."""
    report = {
        "regime_weights": {},
        "regime_calibration": {},
        "archetype_offsets": {},
        "training_data": {},
        "timestamp": "",
    }

    from datetime import datetime
    report["timestamp"] = datetime.now().isoformat()

    # 1. 分段权重就绪状态
    for regime in ["bull", "bear", "range"]:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT COUNT(*) FILTER (WHERE n_observations > 0),
                       MAX(n_observations)
                FROM bayesian_beliefs
                WHERE archetype = :arch AND param_name NOT LIKE '__%'
            """), {"arch": regime})
            params, max_n = r.fetchone()
            params = params or 0
            max_n = max_n or 0

            # 查 AUC
            r2 = await s.execute(text("""
                SELECT mu FROM bayesian_beliefs
                WHERE archetype = :arch AND param_name = '__regime_auc__'
            """), {"arch": regime})
            auc_row = r2.fetchone()
            auc = float(auc_row[0]) if auc_row else 0.0

        samples_ok = max_n >= MIN_REGIME_SAMPLES
        params_ok = params >= MIN_REGIME_PARAMS
        auc_ok = auc >= MIN_REGIME_AUC
        ready = samples_ok and params_ok and auc_ok

        report["regime_weights"][regime] = {
            "samples": max_n, "params_trained": params,
            "auc": round(auc, 4),
            "checks": {"samples": samples_ok, "params": params_ok, "auc": auc_ok},
            "ready": ready,
            "pct": min(100, int(max_n / MIN_REGIME_SAMPLES * 100)),
        }

    # 2. 分段校准器就绪状态
    try:
        from app.services.probability_calibrator import _regime_calibration_cache
        for regime in ["bull", "bear", "range"]:
            cal_data = _regime_calibration_cache.get(regime, {})
            total = sum(v.get("total_samples", 0) for v in cal_data.values()) if cal_data else 0
            ready = total >= MIN_CAL_SAMPLES
            report["regime_calibration"][regime] = {
                "samples": total,
                "ready": ready,
                "pct": min(100, int(total / MIN_CAL_SAMPLES * 100)),
            }
    except Exception:
        report["regime_calibration"] = {"error": "校准器未加载"}

    # 3. 原型偏移校准数据
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT a.archetype, COUNT(*)
            FROM analysis_scores a
            JOIN recommendation_tracking rt
              ON rt.symbol = a.symbol AND rt.scan_date = a.scan_date
            WHERE rt.was_profitable_3d IS NOT NULL
              AND a.archetype IS NOT NULL
            GROUP BY a.archetype
        """))
        for row in r.fetchall():
            arch, cnt = row[0], row[1]
            report["archetype_offsets"][arch] = {
                "samples": cnt,
                "ready": cnt >= MIN_ARCHETYPE_SAMPLES,
                "pct": min(100, int(cnt / MIN_ARCHETYPE_SAMPLES * 100)),
            }

    # 4. 训练数据总量
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE was_profitable_3d IS NOT NULL)
            FROM recommendation_tracking
        """))
        total, verified = r.fetchone()
        report["training_data"] = {
            "total": total or 0,
            "verified": verified or 0,
            "pct": min(100, int((verified or 0) / max(total or 1, 1) * 100)),
        }

    return report


async def check_and_upgrade_components() -> dict:
    """每日自动检查并激活达标组件.

    由 background_sync.daily_task 在 16:00 后调用。
    只激活，不降级（降级需人工判断）。
    """
    report = {"activated": [], "skipped": [], "errors": []}

    # ── 1. 检查分段权重 ──
    for regime in ["bull", "bear", "range"]:
        try:
            async with async_session_factory() as s:
                r = await s.execute(text("""
                    SELECT MAX(n_observations), COUNT(*) FILTER (WHERE n_observations > 0)
                    FROM bayesian_beliefs
                    WHERE archetype = :arch AND param_name NOT LIKE '__%'
                """), {"arch": regime})
                max_n, params = r.fetchone()
                max_n = max_n or 0; params = params or 0

                r2 = await s.execute(text("""
                    SELECT mu FROM bayesian_beliefs
                    WHERE archetype = :arch AND param_name = '__regime_auc__'
                """), {"arch": regime})
                auc_row = r2.fetchone()
                auc = float(auc_row[0]) if auc_row else 0.0

                if max_n >= MIN_REGIME_SAMPLES and params >= MIN_REGIME_PARAMS and auc >= MIN_REGIME_AUC:
                    strategy = f"scoring_{regime}"
                    # 检查是否已激活
                    r3 = await s.execute(text(
                        "SELECT 1 FROM param_library WHERE strategy = :st AND is_active = true LIMIT 1"
                    ), {"st": strategy})
                    if not r3.fetchone():
                        # 自动激活
                        await s.execute(text(
                            "UPDATE param_library SET is_active = true, updated_at = NOW() "
                            "WHERE strategy = :st"
                        ), {"st": strategy})
                        await s.commit()
                        logger.info(f"✅ Regime [{regime}] 权重自动激活 (n={max_n}, params={params}, AUC={auc:.3f})")
                        report["activated"].append(f"regime_weight:{regime}")
                    else:
                        report["skipped"].append(f"regime_weight:{regime} (already active)")
                else:
                    report["skipped"].append(
                        f"regime_weight:{regime} (n={max_n}/{MIN_REGIME_SAMPLES}, "
                        f"params={params}/{MIN_REGIME_PARAMS}, AUC={auc:.3f}/{MIN_REGIME_AUC})"
                    )
        except Exception as e:
            report["errors"].append(f"regime_weight:{regime}: {e}")

    logger.info(f"Health check: activated={report['activated']}, "
                f"skipped={len(report['skipped'])}, errors={len(report['errors'])}")
    return report
