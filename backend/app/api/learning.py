"""学习面板 + 原型 + 回放缓冲 API."""
import asyncio, json
from datetime import date
from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.schemas.learning import UpgradeRequest, RollbackRequest
from sqlalchemy import text
from app.core.database import async_session_factory

router = APIRouter(prefix="/learning", tags=["learning"])


def _upgrade_state(consecutive_days: int, discrimination: float, converge_status: str) -> str:
    """升级按钮状态: green(收敛且显著超越,可升级) / yellow(训练中) / gray(未训练)."""
    if converge_status in ("untrained", "upgraded"):
        return "gray"
    if converge_status == "training":
        return "yellow"  # 训练中，未收敛不可升级
    if converge_status == "overfit":
        return "gray"     # 过拟合，不可升级
    # converge_status == 'converged': 需要 discrimination > 0.5 且显著超越现实
    if discrimination > 0.5:
        return "green"
    if discrimination > 0.3:
        return "yellow"
    return "gray"


@router.get("/params")
async def get_learning_params():
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT param_name, mu, sigma, n_observations, lo, hi FROM bayesian_beliefs WHERE archetype='__global__' ORDER BY param_name"
        ))
        data = [{"name": row[0], "mu": float(row[1]) if row[1] else 0, "sigma": float(row[2]) if row[2] else 0, "n": row[3] or 0, "lo": float(row[4]) if row[4] else 0, "hi": float(row[5]) if row[5] else 0} for row in r.fetchall()]
    return {"status": "success", "data": data}


@router.get("/self-status")
async def get_self_status():
    """Phase 53: 自学习综合状态 — 预测模型 + 新闻验证 + 自适应阈值 + 推荐追踪 + 数据新鲜度."""
    result = {}

    # ── 1. 预测模型状态 ──
    try:
        with open("models/predictive_scorer_meta.json", "r", encoding="utf-8") as f:
            model_meta = json.load(f)
        result["predictive_model"] = {
            "features": model_meta.get("n_features", 0),
            "samples": model_meta.get("n_samples", 0),
            "auc": round(model_meta.get("cv_auc_mean", 0), 4),
            "r2": round(model_meta.get("r2", 0), 4),
            "win_rate": round(model_meta.get("win_rate", 0) * 100, 1),
            "last_trained": model_meta.get("train_date", "unknown"),
            "sources": model_meta.get("data_sources", {}),
        }
    except Exception:
        result["predictive_model"] = {"features": 0, "samples": 0, "auc": 0, "status": "untrained"}

    # ── 2. 新闻验证 ──
    async with async_session_factory() as s:
        try:
            r = await s.execute(text(
                "SELECT COUNT(*), SUM(CASE WHEN is_active THEN 1 ELSE 0 END) FROM news_verify"
            ))
            total, active = r.fetchone()
            r_top = await s.execute(text(
                "SELECT commodity, symbol, hit_rate_t2, total FROM news_verify "
                "WHERE is_active = TRUE AND total >= 5 ORDER BY hit_rate_t2 DESC LIMIT 5"
            ))
            top_hit = [{"commodity": row[0], "symbol": row[1], "hit_rate": float(row[2]), "total": row[3]}
                       for row in r_top.fetchall()]
            r_miss = await s.execute(text(
                "SELECT commodity, symbol, hit_rate_t2, total FROM news_verify "
                "WHERE is_active = FALSE AND total >= 5 ORDER BY hit_rate_t2 LIMIT 5"
            ))
            top_miss = [{"commodity": row[0], "symbol": row[1], "hit_rate": float(row[2]), "total": row[3]}
                        for row in r_miss.fetchall()]
            result["news_verification"] = {
                "total_mappings": total or 0,
                "active_mappings": active or 0,
                "activation_pct": round((active or 0) / max(total or 1, 1) * 100, 1),
                "top_hit": top_hit,
                "top_miss": top_miss,
            }
        except Exception:
            result["news_verification"] = {"total_mappings": 0, "active_mappings": 0, "status": "no_data"}

        # ── 3. 自适应阈值 ──
        try:
            from app.services.market_gate import _get_adaptive_thresholds
            adaptive = await _get_adaptive_thresholds(s)
            result["adaptive_thresholds"] = adaptive
        except Exception:
            result["adaptive_thresholds"] = {"status": "unavailable"}

        # ── 4. 推荐追踪验证 ──
        try:
            r = await s.execute(text("""
                SELECT
                    COUNT(*) FILTER(WHERE verified_2d = TRUE) AS t2_verified,
                    ROUND((AVG(CASE WHEN return_2d > 0 THEN 1.0 ELSE 0.0 END) FILTER(WHERE verified_2d = TRUE AND return_2d IS NOT NULL) * 100)::numeric, 1) AS t2_wr,
                    ROUND(AVG(return_2d) FILTER(WHERE verified_2d = TRUE AND return_2d IS NOT NULL)::numeric, 2) AS t2_avg_ret,
                    COUNT(*) FILTER(WHERE verified_3d = TRUE) AS t3_verified,
                    ROUND((AVG(CASE WHEN return_3d > 0 THEN 1.0 ELSE 0.0 END) FILTER(WHERE verified_3d = TRUE AND return_3d IS NOT NULL) * 100)::numeric, 1) AS t3_wr,
                    COUNT(*) FILTER(WHERE verified_5d = TRUE) AS t5_verified,
                    ROUND((AVG(CASE WHEN return_5d > 0 THEN 1.0 ELSE 0.0 END) FILTER(WHERE verified_5d = TRUE AND return_5d IS NOT NULL) * 100)::numeric, 1) AS t5_wr
                FROM recommendation_tracking
            """))
            row = r.fetchone()
            result["recommendation_tracking"] = {
                "t2_verified": row[0] or 0,   "t2_wr": float(row[1]) if row[1] is not None else 0,
                "t2_avg_ret": float(row[2]) if row[2] is not None else 0,
                "t3_verified": row[3] or 0,   "t3_wr": float(row[4]) if row[4] is not None else 0,
                "t5_verified": row[5] or 0,   "t5_wr": float(row[6]) if row[6] is not None else 0,
            }
        except Exception as e:
            result["recommendation_tracking"] = {"status": "no_data", "error": str(e)}

        # ── 5. 数据新鲜度 ──
        try:
            freshness = {}
            tables = [
                ("daily_kline", "trade_date"),
                ("index_daily", "trade_date"),
                ("sector_trend", "trade_date"),
                ("scan_results", "scan_date"),
                ("analysis_scores", "scan_date"),
                ("news_aggregated", "date"),
                ("news_signals", "created_at"),
                ("recommendation_tracking", "scan_date"),
            ]
            for table, col in tables:
                try:
                    r = await s.execute(text(f"SELECT MAX({col}) FROM {table}"))
                    val = r.scalar()
                    freshness[table] = str(val) if val else "no_data"
                except Exception:
                    freshness[table] = "error"
            result["data_freshness"] = freshness
        except Exception:
            result["data_freshness"] = {"status": "error"}

    return {"status": "success", "data": result}


@router.get("/news-verify-summary")
async def get_news_verify_summary():
    """Phase 62: 按商品聚合的新闻验证命中率, 供前端"新闻验证"Tab."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT commodity, COUNT(*) as syms,
                   SUM(total) as total_signals,
                   SUM(correct_t2) as correct,
                   ROUND(SUM(correct_t2)::numeric / NULLIF(SUM(total), 0) * 100, 1) as hit_rate,
                   SUM(CASE WHEN is_active THEN 1 ELSE 0 END) as active
            FROM news_verify
            GROUP BY commodity HAVING SUM(total) >= 5
            ORDER BY SUM(total) DESC
        """))
        data = [{
            "commodity": row[0], "stocks": row[1], "total": row[2],
            "correct": row[3], "hit_rate": float(row[4]), "active": row[5],
        } for row in r.fetchall()]
    return {"status": "success", "data": data, "count": len(data)}


@router.post("/sync-infra")
async def sync_infrastructure():
    """Phase 72: 手动触发核心基建表同步 (index_daily + sw_sector + sector_trend)."""
    from app.scheduler.daily_tasks import task_sync_index_daily, task_sync_sw_sector, task_build_sector_trend
    from app.core.database import async_session_factory as _sf
    from sqlalchemy import text

    results = {}

    # 1. 大盘指数
    try:
        r = await task_sync_index_daily()
        results["index_daily"] = {"status": "ok", **r} if isinstance(r, dict) else {"status": "ok", "detail": str(r)}
    except Exception as e:
        results["index_daily"] = {"status": "error", "detail": str(e)[:100]}

    # 2. SW 行业
    try:
        r = await task_sync_sw_sector()
        results["sw_sector"] = {"status": "ok", **r} if isinstance(r, dict) else {"status": "ok", "detail": str(r)}
    except Exception as e:
        results["sw_sector"] = {"status": "error", "detail": str(e)[:100]}

    # 3. 板块趋势
    try:
        r = await task_build_sector_trend()
        results["sector_trend"] = {"status": "ok", **r} if isinstance(r, dict) else {"status": "ok", "detail": str(r)}
    except Exception as e:
        results["sector_trend"] = {"status": "error", "detail": str(e)[:100]}

    # 4. 返回各表最新日期
    freshness = {}
    async with _sf() as s:
        for tbl, col in [
            ("index_daily", "trade_date"),
            ("sw_sector_index", "trade_date"),
            ("sector_trend", "trade_date"),
        ]:
            try:
                r = await s.execute(text(f"SELECT MAX({col}) FROM {tbl}"))
                val = r.scalar()
                freshness[tbl] = str(val) if val else "N/A"
            except Exception:
                freshness[tbl] = "error"

    return {"status": "success", "results": results, "freshness": freshness}


@router.get("/stats")
async def get_learning_stats():
    from app.services.learning_engine import get_learning_stats as _stats
    stats = await _stats()
    return {"status": "success", "data": stats}


@router.post("/backtest")
async def trigger_backtest(days: int = Query(default=60, le=180)):
    from app.services.learning_engine import run_rolling_backtest
    result = await run_rolling_backtest(lookback_days=days)
    return {"status": "success", "data": result}


@router.post("/backtest/bootstrap")
async def trigger_bootstrap(days: int = Query(default=90, le=180)):
    """冷启动回测：全历史训练，适用于首次运行或参数重置后."""
    from app.services.learning_engine import run_bootstrap_backtest
    result = await run_bootstrap_backtest(lookback_days=days)
    return {"status": "success", "data": result}


# ── 后台训练状态追踪 ──────────────────────────────

_training_jobs: dict[str, dict] = {}  # key: "archetype/strategy"
_training_semaphore = None  # lazy init: 限制并发数避免DB连接池耗尽
_keep_awake_active = False


def _keep_awake_enable():
    """防止Windows待机/休眠导致训练中断."""
    global _keep_awake_active
    if _keep_awake_active:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000002)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        _keep_awake_active = True
    except Exception:
        pass  # 非Windows系统忽略


def _keep_awake_disable():
    """恢复Windows正常待机."""
    global _keep_awake_active
    if not _keep_awake_active:
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
        _keep_awake_active = False
    except Exception:
        pass


def _check_all_training_done():
    """检查是否所有训练任务已完成，如完成则恢复待机."""
    running = any(j.get("running") for j in _training_jobs.values() if j and isinstance(j, dict))
    if not running:
        _keep_awake_disable()


async def _run_training_bg(arch: str, st: str, iterations: int):
    """后台执行单个原型+策略的训练(信号量限制并发≤8，防止DB连接池耗尽)."""
    global _training_semaphore
    import asyncio as _asyncio
    if _training_semaphore is None:
        _training_semaphore = _asyncio.Semaphore(8)

    from app.services.shadow_trainer import train_shadow
    import logging
    _logger = logging.getLogger(__name__)
    async with _training_semaphore:
        _keep_awake_enable()
        try:
            result = await train_shadow(arch, st, n_iterations=iterations)
            _training_jobs[f"{arch}/{st}"] = {"running": False, "result": result}
        except Exception as e:
            _logger.error(f"Training {arch}/{st} failed: {e}", exc_info=True)
            _training_jobs[f"{arch}/{st}"] = {"running": False, "error": str(e)}
    _check_all_training_done()


@router.post("/shadow-train")
async def trigger_shadow_train(
    archetype: str = Query(default="all"),
    strategy: str = Query(default="S2"),
    iterations: int = Query(default=20, le=50),
    force: bool = Query(default=False),
):
    """触发影子层训练(按原型+策略)— 后台异步执行，立即返回.

    archetype=all 时对所有原型依次训练.
    自动跳过 4 小时内已训练的原型+策略组合.
    """
    try:
        from datetime import datetime, timedelta

        async with async_session_factory() as s:
            if archetype == "all":
                r = await s.execute(text(
                    "SELECT archetype FROM archetype_profiles WHERE is_trainable=true ORDER BY sample_count DESC"
                ))
                archs = [row[0] for row in r.fetchall()]
            else:
                archs = [archetype]

        strategies = [strategy] if strategy != "all" else ["S1", "S2", "S3"]

        scheduled = []
        skipped = []
        cutoff = datetime.now() - timedelta(hours=4)

        for arch in archs:
            for st in strategies:
                job_key = f"{arch}/{st}"
                async with async_session_factory() as s:
                    r = await s.execute(text(
                        "SELECT last_trained_at FROM param_library WHERE archetype=:a AND strategy=:st AND is_shadow=true ORDER BY last_trained_at DESC LIMIT 1"
                    ), {"a": arch, "st": st})
                    last_row = r.fetchone()

                if not force and last_row and last_row[0]:
                    lt = last_row[0]
                    lt_naive = lt.replace(tzinfo=None) if getattr(lt, 'tzinfo', None) else lt
                    if lt_naive > cutoff:
                        skipped.append({"archetype": arch, "strategy": st, "last_trained_at": lt_naive.isoformat()})
                        continue

                if job_key in _training_jobs and _training_jobs[job_key].get("running"):
                    skipped.append({"archetype": arch, "strategy": st, "reason": "已在训练中"})
                    continue

                scheduled.append({"archetype": arch, "strategy": st})
                _training_jobs[job_key] = {"running": True, "started_at": datetime.now().isoformat()}
                asyncio.create_task(_run_training_bg(arch, st, iterations))

        return {
            "status": "scheduled",
            "scheduled": scheduled,
            "skipped": skipped,
            "message": f"{len(scheduled)} 个训练任务已启动，{len(skipped)} 个跳过"
        }
    except Exception as e:
        import traceback
        return {"status": "error", "detail": str(e), "traceback": traceback.format_exc()}


@router.get("/shadow-train/status")
async def training_jobs_status():
    """查看当前训练任务状态."""
    from app.services.shadow_trainer import _kline_progress
    return {"status": "success", "data": _training_jobs, "kline_progress": _kline_progress}


@router.get("/shadow-status")
async def get_shadow_status(archetype: str = Query(default="all")):
    """查询影子训练进度."""
    async with async_session_factory() as s:
        if archetype == "all":
            r = await s.execute(text("""
                SELECT archetype, strategy, discrimination, converge_status, consecutive_days, last_trained_at
                FROM param_library WHERE is_shadow=true
                ORDER BY archetype, strategy
            """))
        else:
            r = await s.execute(text("""
                SELECT archetype, strategy, discrimination, converge_status, consecutive_days, last_trained_at
                FROM param_library WHERE is_shadow=true AND archetype=:a
                ORDER BY strategy
            """), {"a": archetype})
        data = [{
            "archetype": row[0], "strategy": row[1],
            "discrimination": float(row[2]) if row[2] else 0,
            "converge_status": row[3],
            "consecutive_days": row[4] or 0,
            "last_trained_at": str(row[5]) if row[5] else None,
        } for row in r.fetchall()]
    return {"status": "success", "data": data}


@router.get("/panel")
async def get_learning_panel():
    """学习面板数据：所有原型的现实层 vs 影子层对比."""
    async with async_session_factory() as s:
        # 获取所有可训练原型
        r = await s.execute(text(
            "SELECT archetype, sample_count FROM archetype_profiles WHERE is_trainable=true ORDER BY sample_count DESC"
        ))
        archs = [(row[0], row[1]) for row in r.fetchall()]

        # 获取每个原型×策略×市场阶段的影子训练结果
        r = await s.execute(text("""
            SELECT DISTINCT ON (archetype, strategy, market_style)
                archetype, strategy, market_style, discrimination, converge_status, consecutive_days, last_trained_at
            FROM param_library WHERE is_shadow=true AND converge_status != 'upgraded'
            ORDER BY archetype, strategy, market_style, created_at DESC
        """))
        shadow_data = {}
        for row in r.fetchall():
            phase = row[2] if row[2] in ("bull", "bear", "range") else "all"
            key = f"{row[0]}_{row[1]}_{phase}"
            shadow_data[key] = {
                "discrimination": float(row[3]) if row[3] else 0,
                "converge_status": row[4],
                "consecutive_days": row[5] or 0,
                "last_trained_at": str(row[6]) if row[6] else None,
                "market_phase": phase,
            }

        # 兼容: 旧数据无 market_style → 取每个策略的最新一条作为 'all' 回退
        r = await s.execute(text("""
            SELECT DISTINCT ON (archetype, strategy) archetype, strategy, discrimination, converge_status, consecutive_days, last_trained_at
            FROM param_library WHERE is_shadow=true AND converge_status != 'upgraded'
            ORDER BY archetype, strategy, created_at DESC
        """))
        for row in r.fetchall():
            key = f"{row[0]}_{row[1]}_all"
            if key not in shadow_data:
                shadow_data[key] = {
                    "discrimination": float(row[2]) if row[2] else 0,
                    "converge_status": row[3],
                    "consecutive_days": row[4] or 0,
                    "last_trained_at": str(row[5]) if row[5] else None,
                    "market_phase": "all",
                }

        # 获取现实层实际夏普(从生产回测，非硬编码)
        r = await s.execute(text("""
            SELECT DISTINCT ON (archetype, strategy) archetype, strategy, discrimination
            FROM param_library WHERE is_shadow=false AND is_active=true
            ORDER BY archetype, strategy, created_at DESC
        """))
        reality_sharpes = {}
        for row in r.fetchall():
            reality_sharpes[f"{row[0]}_{row[1]}"] = float(row[2]) if row[2] else 0.5

    result = []
    for arch, count in archs:
        arch_entry = {"archetype": arch, "sample_count": count, "strategies": {}}
        for st in ["S1", "S2", "S3"]:
            # 按市场阶段聚合影子数据
            phases = {}
            best_shadow = 0
            best_status = "untrained"
            best_trained = None
            for phase in ["bull", "bear", "range"]:
                shadow = shadow_data.get(f"{arch}_{st}_{phase}")
                if not shadow:
                    shadow = shadow_data.get(f"{arch}_{st}_all", {})  # fallback: 旧数据无阶段标签
                real_sharpe = reality_sharpes.get(f"{arch}_{st}", 0.5)
                shadow_sharpe = shadow.get("discrimination", 0)
                phases[phase] = {
                    "shadow_sharpe": shadow_sharpe,
                    "reality_sharpe": real_sharpe,
                    "converge_status": shadow.get("converge_status", "untrained"),
                    "last_trained_at": shadow.get("last_trained_at"),
                    "can_upgrade": (
                        shadow.get("converge_status") == "converged"
                        and shadow_sharpe > max(real_sharpe * 1.05, 0.5)
                    ),
                    "upgrade_state": _upgrade_state(
                        shadow.get("consecutive_days", 0), shadow_sharpe,
                        shadow.get("converge_status", "")
                    ),
                }
                if shadow_sharpe > best_shadow:
                    best_shadow = shadow_sharpe
                    best_status = shadow.get("converge_status", "untrained")
                    best_trained = shadow.get("last_trained_at")

            real_sharpe = reality_sharpes.get(f"{arch}_{st}", 0.5)
            arch_entry["strategies"][st] = {
                "shadow_sharpe": best_shadow,
                "reality_sharpe": real_sharpe,
                "converge_status": best_status,
                "last_trained_at": best_trained,
                "is_training": _training_jobs.get(f"{arch}/{st}", {}).get("running", False),
                "can_upgrade": any(p["can_upgrade"] for p in phases.values()),
                "upgrade_state": _upgrade_state(0, best_shadow, best_status),
                "phases": phases,  # 新增: 各阶段独立数据
            }
        result.append(arch_entry)

    # 训练数据概况
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(DISTINCT scan_date), MIN(scan_date), MAX(scan_date) FROM analysis_scores"))
        dates, d1, d2 = r.fetchone()
        r = await s.execute(text("SELECT COUNT(*) FROM analysis_scores"))
        total = r.scalar()
        r = await s.execute(text("SELECT COUNT(*) FROM daily_kline"))
        kline_rows = r.scalar()
        r = await s.execute(text("SELECT MIN(trade_date), MAX(trade_date) FROM daily_kline"))
        k1, k2 = r.fetchone()

    training_info = {
        "analysis_dates": dates,
        "analysis_range": f"{d1} ~ {d2}",
        "analysis_total_rows": total,
        "kline_rows": kline_rows,
        "kline_range": f"{k1} ~ {k2}",
        "horizon_info": {
            "S1": {"horizon": 1, "verifiable_dates": 0},
            "S2": {"horizon": 5},
            "S3": {"horizon": 15},
        },
    }
    # 计算各策略可验证日期
    from datetime import date as _dt, timedelta as _td
    today = _dt.today()
    async with async_session_factory() as s2:
        r = await s2.execute(text("SELECT DISTINCT scan_date FROM analysis_scores ORDER BY scan_date"))
        all_dates = [row[0] for row in r.fetchall()]
    for st, h in [("S1", 1), ("S2", 5), ("S3", 15)]:
        verifiable = [d for d in all_dates if d + _td(days=h) <= today]
        training_info["horizon_info"][st]["verifiable_dates"] = len(verifiable)
        training_info["horizon_info"][st]["path"] = "analysis_scores" if len(verifiable) >= 5 else "kline(250天)"

    return {"status": "success", "data": result, "training_info": training_info}


@router.post("/upgrade")
async def upgrade_shadow(req: UpgradeRequest):
    """将影子权重升级为现实层(含冲突检测).

    S1 升级时自动触发 S2/S3 快速重验证(5轮)，S3 避坑率下降 >3% 则阻止.
    """
    import json as _json
    from app.services.shadow_trainer import train_shadow

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT scoring_weights, discrimination FROM param_library
            WHERE archetype=:a AND strategy=:st AND is_shadow=true
            ORDER BY created_at DESC LIMIT 1
        """), {"a": req.archetype, "st": req.strategy})
        shadow = r.fetchone()
        if not shadow: return {"status": "error", "detail": "无影子训练结果"}

        conflicts = []
        revalidated = {}

        # S1 升级 → 触发 S2/S3 重验证(5 轮快速回测)
        if req.strategy == "S1":
            # 获取 S3 当前分数作为基准
            r = await s.execute(text("""
                SELECT discrimination FROM param_library
                WHERE archetype=:a AND strategy='S3' AND is_shadow=true
                ORDER BY created_at DESC LIMIT 1
            """), {"a": req.archetype})
            s3_before_row = r.fetchone()
            s3_before = float(s3_before_row[0]) if s3_before_row and s3_before_row[0] else None
    # 释放连接，在会话外执行耗时的重验证
    if req.strategy == "S1":
        for vst in ["S2", "S3"]:
            try:
                v_result = await train_shadow(req.archetype, vst, n_iterations=5)
                # 新格式: {"phases": {"bull": {"best_score":...}, ...}}
                phases = v_result.get("phases", {})
                best = max((p.get("best_score", -999) for p in phases.values()), default=0)
                revalidated[vst] = {"score": round(best, 4), "metric": v_result.get("metric", "sharpe")}
            except Exception as e:
                revalidated[vst] = {"error": str(e)}

        if s3_before is not None and "score" in revalidated.get("S3", {}):
            s3_after = revalidated["S3"]["score"]
            drop = s3_before - s3_after
            if drop > 0.03:
                conflicts.append(f"S3将从{s3_before:.4f}降至{s3_after:.4f}(下降{drop*100:.1f}%)")

    if conflicts:
        return {"status": "conflict", "conflicts": conflicts, "revalidated": revalidated}

    # Phase 3: 写入升级结果(独立短会话)
    weights = shadow[0] if isinstance(shadow[0], dict) else _json.loads(shadow[0] or "{}")
    async with async_session_factory() as s2:
        r = await s2.execute(text("""
            SELECT version FROM param_library
            WHERE archetype=:a AND strategy=:st AND is_shadow=false AND is_active=true
            ORDER BY created_at DESC LIMIT 1
        """), {"a": req.archetype, "st": req.strategy})
        parent = r.fetchone()
        parent_ver = parent[0] if parent else "initial"
        version = f"upgrade-{date.today().isoformat()}-{int(__import__('time').time())%1000}"

        await s2.execute(text("""
            INSERT INTO param_library (id, archetype, strategy, is_shadow, scoring_weights,
                discrimination, converge_status, last_trained_at, version, parent_version, is_active,
                month, market_style, created_at, updated_at)
            VALUES (gen_random_uuid(), :a, :st, false, CAST(:w AS jsonb),
                :disc, 'active', NOW(), :v, :pv, true, 1, 'all', NOW(), NOW())
        """), {"a": req.archetype, "st": req.strategy, "w": _json.dumps(weights),
               "disc": float(shadow[1] or 0), "v": version, "pv": parent_ver})
        # 标记原影子记录为已升级，防止重复升级
        await s2.execute(text("""
            UPDATE param_library SET converge_status='upgraded', is_active=false
            WHERE archetype=:a AND strategy=:st AND is_shadow=true AND converge_status!='upgraded'
        """), {"a": req.archetype, "st": req.strategy})
        await s2.commit()
    return {"status": "success", "revalidated": revalidated}


@router.post("/rollback")
async def rollback_shadow(req: RollbackRequest):
    """回滚到上一个现实层版本."""
    async with async_session_factory() as s:
        # 找当前活跃版本
        r = await s.execute(text("""
            SELECT id, version, parent_version FROM param_library
            WHERE archetype=:a AND strategy=:st AND is_shadow=false AND is_active=true
            ORDER BY created_at DESC LIMIT 1
        """), {"a": req.archetype, "st": req.strategy})
        current = r.fetchone()
        if not current: return {"status": "error", "detail": "无可回滚的活跃版本"}

        parent_ver = current[2]
        if not parent_ver or parent_ver == "initial":
            return {"status": "error", "detail": "已是最初版本，无法回滚"}

        # 找父版本
        r = await s.execute(text("""
            SELECT id FROM param_library
            WHERE archetype=:a AND strategy=:st AND version=:pv AND is_shadow=false
            ORDER BY created_at DESC LIMIT 1
        """), {"a": req.archetype, "st": req.strategy, "pv": parent_ver})
        parent = r.fetchone()
        if not parent: return {"status": "error", "detail": f"父版本 {parent_ver} 不存在"}

        # 停用当前版本
        await s.execute(text("UPDATE param_library SET is_active=false WHERE id=:id"), {"id": current[0]})
        # 激活父版本
        await s.execute(text("UPDATE param_library SET is_active=true, updated_at=NOW() WHERE id=:id"), {"id": parent[0]})
        await s.commit()
    return {"status": "success", "message": f"已回滚到 {parent_ver}"}


@router.get("/experiences")
async def get_experiences(limit: int = Query(default=20, le=100)):
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT event_type, recorded_at, reward, meta_info, archetype, category_tags
            FROM experience_replay ORDER BY created_at DESC LIMIT :lim
        """), {"lim": limit})
        data = [{
            "event_type": row[0],
            "recorded_at": str(row[1]) if row[1] else None,
            "reward": float(row[2]) if row[2] else 0,
            "meta_info": row[3] if row[3] else {},
            "archetype": row[4] or "__global__",
            "category_tags": row[5] if row[5] else [],
        } for row in r.fetchall()]
    return {"status": "success", "data": data, "count": len(data)}


# ── Replay Buffer ─────────────────────────────────

@router.get("/buffer/sample")
async def sample_replay_buffer(batch_size: int = Query(default=32, le=128), archetype: str | None = None):
    from app.services.replay_buffer import sample_experiences, sample_balanced
    if archetype:
        data = await sample_experiences(batch_size, archetype=archetype)
    else:
        data = await sample_balanced(batch_size)
    return {"status": "success", "data": data, "count": len(data)}


@router.get("/buffer/stats")
async def replay_buffer_stats(days: int = Query(default=30)):
    from app.services.replay_buffer import get_archetype_stats
    stats = await get_archetype_stats(days_back=days)
    return {"status": "success", "data": stats}


# ── Archetype / 原型 ──────────────────────────────

@router.get("/archetypes")
async def get_archetypes():
    from app.services.archetype_classifier import load_centroids_from_db
    centroids = await load_centroids_from_db()
    return {"status": "success", "data": {k: {"centroid": v, "dimensions": len(v)} for k, v in centroids.items()}}


@router.post("/archetypes/cluster")
async def run_clustering(n_clusters: int = Query(default=5, le=8)):
    from app.services.archetype_classifier import run_kmeans_clustering
    import json
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, fingerprint_vector FROM stock_fingerprints WHERE scan_date >= CURRENT_DATE - 30"
        ))
        rows = r.fetchall()
    if not rows:
        return {"status": "error", "detail": "没有可用的指纹数据"}
    fingerprints = {}
    for row in rows:
        vec = json.loads(row[1]) if isinstance(row[1], str) else row[1]
        if vec and len(vec) == 11:
            fingerprints[row[0]] = [float(v) for v in vec]
    centroids = await run_kmeans_clustering(fingerprints, n_clusters=n_clusters)
    return {"status": "success", "data": {k: {"centroid": v} for k, v in centroids.items()}, "n_stocks": len(fingerprints)}


@router.get("/archetypes/distribution")
async def get_archetype_distribution():
    from app.services.archetype_classifier import get_archetype_distribution
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT DISTINCT symbol FROM scan_results WHERE scan_date=(SELECT MAX(scan_date) FROM scan_results) LIMIT 100"))
        symbols = [row[0] for row in r.fetchall()]
    if not symbols:
        return {"status": "error", "detail": "无扫描结果"}
    dist = await get_archetype_distribution(symbols)
    return {"status": "success", "data": dist}


# ── 维度注册表 ──────────────────────────────

@router.get("/dimensions")
async def list_dimensions():
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT dim_name, dim_key, priority, status, ic_value, probation_start FROM learning_dimension_registry ORDER BY priority"))
        data = [{"name": row[0], "key": row[1], "priority": row[2], "status": row[3], "ic": float(row[4]) if row[4] else None, "probation_start": str(row[5]) if row[5] else None} for row in r.fetchall()]
    return {"status": "success", "data": data}


@router.post("/dimensions/check-convergence")
async def check_convergence():
    """检测是否有原型+策略已收敛，触发新维度注入."""
    async with async_session_factory() as s:
        # 找 converge_status='converged' 的影子模型
        r = await s.execute(text("""
            SELECT archetype, strategy, discrimination, created_at
            FROM param_library WHERE is_shadow=true AND converge_status='converged'
            ORDER BY created_at DESC LIMIT 5
        """))
        converged = [{"archetype": row[0], "strategy": row[1], "score": float(row[2])} for row in r.fetchall()]

        # 找下一个候选维度
        r = await s.execute(text("SELECT dim_name, dim_key, priority FROM learning_dimension_registry WHERE status='candidate' ORDER BY priority LIMIT 1"))
        next_dim = r.fetchone()
        candidate = {"name": next_dim[0], "key": next_dim[1], "priority": next_dim[2]} if next_dim else None

    return {"status": "success", "converged_models": len(converged), "next_candidate_dimension": candidate, "details": converged}


@router.post("/dimensions/inject")
async def inject_dimension(dim_key: str = Query(...)):
    """将一个候选维度注入影子训练(设为试用期)."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT dim_name FROM learning_dimension_registry WHERE dim_key=:k AND status='candidate'"), {"k": dim_key})
        dim = r.fetchone()
        if not dim: return {"status": "error", "detail": "维度不存在或非候选状态"}

        await s.execute(text("""UPDATE learning_dimension_registry SET status='probation',
            probation_start=CURRENT_DATE, probation_end=CURRENT_DATE+20 WHERE dim_key=:k"""), {"k": dim_key})
        await s.commit()
    return {"status": "success", "message": f"维度 {dim[0]} 已注入试用期 (20天)"}


@router.post("/beliefs-sync")
async def sync_beliefs_from_shadow(archetype: str = Query(default=None), strategy: str = Query(default="S2")):
    """将影子层最优权重同步到贝叶斯信念系统."""
    from app.services.learning_engine import update_beliefs_from_shadow
    result = await update_beliefs_from_shadow(archetype=archetype, strategy=strategy)
    return {"status": "success", "data": result}


@router.post("/archetypes/snapshot")
async def take_archetype_snapshot():
    """存储当前原型分配快照(月度执行，第5交易日调用)."""
    from datetime import date as dt_date
    async with async_session_factory() as s:
        # 获取当前各原型股数
        r = await s.execute(text("SELECT archetype, sample_count, is_trainable FROM archetype_profiles WHERE effective_date = (SELECT MAX(effective_date) FROM archetype_profiles)"))
        current = [(row[0], row[1], row[2]) for row in r.fetchall()]
        today = dt_date.today()
        for arch, cnt, trainable in current:
            await s.execute(text("""INSERT INTO archetype_profiles (id, archetype, label, description, sample_count, is_trainable, effective_date, created_at, updated_at)
                VALUES (gen_random_uuid(), :a, :l, :d, :c, :t, :e, NOW(), NOW())"""),
                {"a": arch, "l": arch, "d": f"{arch}原型 ({today}快照)", "c": cnt, "t": trainable, "e": today})
        await s.commit()
    return {"status": "success", "message": f"已存储 {len(current)} 个原型的月度快照 ({today})"}


@router.post("/dimensions/evaluate")
async def evaluate_probation_dimensions():
    """评估试用期维度：IC>0→晋升，IC≤0→移除.

    IC = corr(predicted_score, actual_return) 而非简单的 mean(actual_return).
    """
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT dim_key, dim_name FROM learning_dimension_registry WHERE status='probation' AND probation_end <= CURRENT_DATE"))
        expired = [(row[0], row[1]) for row in r.fetchall()]
        if not expired: return {"status": "success", "message": "无到期维度", "evaluated": 0}

        results = []
        for dim_key, dim_name in expired:
            # IC = 预测分数与实际收益的相关系数
            r = await s.execute(text("""
                SELECT CORR(predicted_score, actual_return)
                FROM learning_predictions
                WHERE created_at >= CURRENT_DATE - 20 AND predicted_score IS NOT NULL AND actual_return IS NOT NULL
            """))
            ic_val = r.scalar()
            ic_val = float(ic_val) if ic_val is not None else 0

            if ic_val > 0:
                await s.execute(text("UPDATE learning_dimension_registry SET status='active', ic_value=:ic WHERE dim_key=:k"), {"k": dim_key, "ic": round(ic_val, 4)})
                results.append(f"{dim_name}: 晋升 (IC={ic_val:.4f})")
            else:
                await s.execute(text("UPDATE learning_dimension_registry SET status='removed', removed_at=NOW() WHERE dim_key=:k"), {"k": dim_key})
                results.append(f"{dim_name}: 移除 (IC={ic_val:.4f})")
        await s.commit()
    return {"status": "success", "evaluated": len(results), "results": results}


@router.post("/dimensions/auto-maintain")
async def auto_maintain_dimensions():
    """一键维度闭环：收敛检测→扰动验证→注入→评估."""
    results = {}

    # Step 1: 检测收敛 + 扰动验证
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT archetype,strategy,scoring_weights,discrimination FROM param_library WHERE is_shadow=true AND converge_status='converged' ORDER BY created_at DESC LIMIT 1"))
        row = r.fetchone()
        if not row: return {"status": "success", "message": "无收敛模型", "data": {}}

        arch, st, weights_raw, score = row[0], row[1], row[2], float(row[3] or 0)
        weights = weights_raw if isinstance(weights_raw, dict) else json.loads(weights_raw or "{}")
        if not weights: return {"status": "success", "message": "权重数据异常"}

    # 扰动验证
    import random, numpy as np
    perturbed = {}; orig_total = sum(weights.values())
    for k, v in weights.items(): perturbed[k] = v * (1.0 + np.random.uniform(-0.2, 0.2))
    total = sum(perturbed.values())
    perturbed = {k: round(v / total * orig_total, 4) for k, v in perturbed.items()}

    from app.services.shadow_trainer import train_shadow
    recovery = await train_shadow(arch, st, n_iterations=3)
    recovered = recovery.get("best_score", 0)
    ratio = recovered / max(score, 0.001)
    truly_converged = ratio > 0.98
    results["convergence"] = {"truly_converged": truly_converged, "orig": round(score,4), "recovered": round(recovered,4), "ratio": round(ratio,3)}

    # Step 2: 真收敛 → 注入新维度
    if truly_converged:
        async with async_session_factory() as s:
            r = await s.execute(text("SELECT dim_key FROM learning_dimension_registry WHERE status='candidate' ORDER BY priority LIMIT 1"))
            row = r.fetchone()
            if row:
                await s.execute(text("UPDATE learning_dimension_registry SET status='probation', probation_start=CURRENT_DATE, probation_end=CURRENT_DATE+20 WHERE dim_key=:k"), {"k": row[0]})
                await s.commit()
                results["injected"] = row[0]

    # Step 3: 评估到期维度
    eval_r = await evaluate_probation_dimensions()
    results["evaluated"] = eval_r.get("results", [])

    return {"status": "success", "data": results}


# ── 退市股同步 ──────────────────────────────

@router.post("/delisted/sync")
async def sync_delisted():
    """从 Tushare 拉取退市股列表 + 历史K线，存入 delisted_stocks.

    季度执行。用于 S3 幸存者偏差修复.
    """
    from app.services.shadow_trainer import sync_delisted_stocks
    result = await sync_delisted_stocks()
    return {"status": "success", "data": result}


@router.get("/delisted/stats")
async def delisted_stats():
    """退市股库统计."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(*), AVG(kline_count), MAX(kline_count) FROM delisted_stocks"))
        total, avg, mx = r.fetchone()
        r = await s.execute(text("SELECT COUNT(*) FROM delisted_stocks WHERE kline_count >= 200"))
        usable = r.scalar()
    return {"status": "success", "data": {"total": total, "avg_kline": round(float(avg or 0), 0), "max_kline": mx, "usable_s3": usable}}


@router.get("/accuracy")
async def get_accuracy_stats(days: int = 30):
    """推荐准确率统计."""
    from app.services.accuracy_tracker import get_accuracy_stats
    stats = await get_accuracy_stats(days)
    return {"status": "success", "data": stats}


@router.post("/accuracy/verify")
async def verify_accuracy():
    """手动触发推荐准确率验证."""
    from app.services.accuracy_tracker import verify_all_periods
    results = await verify_all_periods()
    return {"status": "success", "data": results}


@router.post("/accuracy/feedback")
async def trigger_accuracy_feedback():
    """手动触发闭环反馈 — 根据推荐准确率调整权重."""
    from app.services.accuracy_tracker import apply_accuracy_feedback
    result = await apply_accuracy_feedback()
    return {"status": "success", "data": result}


@router.post("/train-weights")
async def trigger_weight_training(force: bool = False):
    """★ 基于真实盈亏反馈训练评分维度权重.

    从 recommendation_tracking + analysis_scores 加载历史预测和实际盈亏,
    使用 Logistic Regression 训练维度权重, 写入 bayesian_beliefs 表.
    这是学习闭环的核心入口 — 替代了人工猜测的 DEFAULT_WEIGHTS.
    """
    from app.services.scoring_trainer import full_training_pipeline
    import traceback
    try:
        result = await full_training_pipeline(force=force)
    except Exception as e:
        return {"status": "error", "detail": str(e), "traceback": traceback.format_exc()}

    method = result.get("method", "global")
    if method == "regime_segmented":
        # v4.1: 分段训练返回 {range: {...}, bull: {...}, global: {...}}
        training_summary = {}
        for regime, info in result.get("training", {}).items():
            training_summary[regime] = {
                "n_samples": info.get("n_samples"),
                "cv_auc": info.get("cv_auc"),
                "win_rate": info.get("win_rate"),
            }
        return {"status": "success", "method": method, "data": {
            "total_samples": result.get("total_samples"),
            "regimes_trained": result.get("regimes_trained"),
            "training": training_summary,
            "persist": result.get("persist", {}),
            "top_features": result.get("top_features", {}),
        }}
    else:
        # 全局训练 (向后兼容)
        t = result.get("training", {})
        return {"status": "success", "method": method, "data": {
            "training": {
                "samples": t.get("n_samples"),
                "cv_auc": t.get("cv_auc"),
                "win_rate": t.get("win_rate"),
            },
            "persist": result.get("persist", {}),
            "bayesian": result.get("bayesian", {}),
            "top_features": result.get("top_features", [])[:8],
        }}


@router.get("/system-readiness")
async def get_system_readiness():
    """★ v4.3: 组件就绪状态看板 — 供 MonitorPage 展示.

    返回各 regime 权重/校准器/原型偏移的样本进度条和激活状态。
    """
    from app.services.system_health import get_readiness_report
    report = await get_readiness_report()
    return {"status": "success", "data": report}


@router.get("/weights-trained")
async def check_trained_weights():
    """检查 Bayesian 信念表中的训练观测数."""
    from sqlalchemy import text
    from app.core.database import async_session_factory

    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT param_name, mu, n_observations FROM bayesian_beliefs "
            "WHERE archetype='__global__' AND n_observations > 0 ORDER BY n_observations DESC LIMIT 15"
        ))
        bayes_rows = [{"name": row[0], "mu": float(row[1]) if row[1] else 0, "n": row[2] or 0}
                      for row in r.fetchall()]

    return {
        "status": "success",
        "bayesian_trained_params": len(bayes_rows),
        "detail": bayes_rows,
        "message": "这些参数已被真实盈亏数据训练, deep_scorer 通过 get_beliefs() 自动加载"
    }


@router.get("/archetypes/calibration-data")
async def get_archetype_calibration(days: int = 180):
    """收集原型校准数据 — 为 ARCHETYPE_OFFSETS 提供事实校准基础.

    按原型分组统计实际胜率 vs 全局胜率, 生成建议偏移量.
    """
    from app.services.archetype_param_resolver import collect_archetype_calibration_data
    result = await collect_archetype_calibration_data(lookback_days=days)
    return result


@router.get("/bandit/stats")
async def get_bandit_stats(days_back: int = 90):
    """返回 contextual bandit 各 arm (S1/S2/S3) 的统计和当前推荐策略.

    使用 Thompson Sampling 基于历史 hit_rate 和 excess_return 选择最优策略.
    """
    from app.services.contextual_bandit import select_arm, get_arm_stats, get_arm_rewards
    stats = get_arm_stats(days_back=days_back)
    rewards = get_arm_rewards(days_back=days_back)
    best = select_arm({}, days_back=days_back)
    return {
        "best_arm": best.get("arm", "S2"),
        "arm_stats": stats,
        "arm_rewards": rewards,
        "days_back": days_back,
    }
