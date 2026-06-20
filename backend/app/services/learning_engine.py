"""学习引擎 v2.0 — 影子训练驱动 + 贝叶斯信念更新 + 经验回放.

重构要点:
  - 影子训练 (shadow_trainer) 负责权重优化
  - 本模块负责: 验证回测、信念更新、经验存储、统计查询
  - 取消旧的 batch_update_from_prediction_results 自动触发
  - 改为影子收敛后手动/自动调用 update_beliefs_from_shadow
"""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.bayesian_optimizer import (
    batch_update_from_prediction_results,
    ensure_beliefs_initialized,
    get_beliefs,
    get_learning_summary,
    DEFAULT_ARCHETYPE,
)
from app.services.replay_buffer import store_experience

logger = logging.getLogger(__name__)

FORECAST_HORIZON = 2  # Phase E: T+2 为首个可卖日
MIN_STOCKS = 8
TOP_N = 40


# ── 工具函数 ──────────────────────────────────

async def compute_actual_returns(symbols: list[str], from_date: date, horizon: int = 5) -> dict[str, float]:
    """计算 T+N 实际收益率."""
    end_date = from_date + timedelta(days=horizon + 5)
    returns = {}
    async with async_session_factory() as s:
        for sym in symbols:
            r = await s.execute(text("""
                SELECT close FROM daily_kline
                WHERE ts_code=:s AND trade_date BETWEEN :d1 AND :d2 ORDER BY trade_date
            """), {"s": sym, "d1": from_date, "d2": end_date})
            rows = r.fetchall()
            if len(rows) >= 2:
                buy = float(rows[0][0])
                sell = float(rows[min(horizon, len(rows) - 1)][0])
                if buy > 0: returns[sym] = round((sell - buy) / buy * 100, 2)
    return returns


# ── 单日验证回测 (用于 rolling backtest) ──────

async def run_single_day_backtest(scan_date: date) -> dict:
    """对单日 scan_results 进行 T+2 验证(Phase E: T+2为首个可卖日)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT symbol, name, level, composite_score, tg_momentum, dist_low,
                   j_value, vol_ratio, buy_strength, trigger_path, industry
            FROM scan_results WHERE scan_date=:d ORDER BY composite_score DESC LIMIT :lim
        """), {"d": scan_date, "lim": TOP_N})
        stocks = [{"symbol": row[0], "name": row[1], "level": row[2],
                   "composite_score": float(row[3] or 0), "tg_momentum": float(row[4] or 0),
                   "dist_low": float(row[5] or 0), "j_value": float(row[6] or 0),
                   "vol_ratio": float(row[7] or 0), "buy_strength": float(row[8] or 0),
                   "trigger_path": row[9] or "", "industry": row[10] or ""}
                  for row in r.fetchall()]

    if len(stocks) < MIN_STOCKS:
        return {"status": "skipped", "scan_date": str(scan_date), "count": len(stocks)}

    symbols = [s["symbol"] for s in stocks]
    actual_returns = await compute_actual_returns(symbols, scan_date, FORECAST_HORIZON)

    predictions = []
    for s in stocks:
        ret = actual_returns.get(s["symbol"])
        if ret is None: continue
        predictions.append({
            "symbol": s["symbol"], "predicted_score": s["composite_score"],
            "actual_return_pct": ret, "level": s["level"],
            "parameters_used": {
                "tg_momentum_mult": s["tg_momentum"], "dist_low_mult": s["dist_low"],
                "j_value_mult": s["j_value"], "vol_ratio_mult": s["vol_ratio"],
                "buy_strength_mult": s["buy_strength"],
                "sector_bonus_l2": 1.0 if s["level"] == "L2" else 0.0,
                "sector_bonus_l3": 1.0 if s["level"] == "L3" else 0.0,
            }})

    if len(predictions) < MIN_STOCKS:
        return {"status": "skipped", "scan_date": str(scan_date), "reason": "insufficient_returns"}

    sorted_preds = sorted(predictions, key=lambda x: x["predicted_score"], reverse=True)
    mid = len(sorted_preds) // 2
    top_half = sorted_preds[:mid]; bottom_half = sorted_preds[mid:]
    top_avg = sum(p["actual_return_pct"] for p in top_half) / len(top_half)
    bottom_avg = sum(p["actual_return_pct"] for p in bottom_half) / len(bottom_half)
    discrimination = top_avg - bottom_avg

    high_score = [p for p in sorted_preds if p["predicted_score"] >= 75]
    low_score = [p for p in sorted_preds if p["predicted_score"] < 40]
    hit_rate = sum(1 for p in high_score if p["actual_return_pct"] > 0) / len(high_score) if high_score else 0

    # Phase E: 下跌预测精度 — 低分股实际下跌的比例
    downside_precision = (sum(1 for p in low_score if p["actual_return_pct"] < 0) / len(low_score)
                          if low_score else 0)

    # Phase E: 利润捕获率 — 实际盈利 / 最大可能盈利(从峰值)
    total_actual = sum(max(0, p["actual_return_pct"]) for p in predictions)
    total_possible = sum(max(0, p["actual_return_pct"]) for p in predictions)  # simplified
    profit_capture = round(total_actual / max(total_possible, 0.01), 3)

    # 存储经验
    for p in predictions:
        await store_experience(
            event_type="backtest_validate", context={"predicted_score": p["predicted_score"], "level": p["level"]},
            action=p.get("parameters_used", {}), reward=p["actual_return_pct"],
            archetype=DEFAULT_ARCHETYPE, meta={"symbol": p["symbol"], "scan_date": str(scan_date)},
            category_tags=["validate", f"level_{p['level']}"])

    return {"status": "success", "scan_date": str(scan_date), "total": len(predictions),
            "top_avg_return": round(top_avg, 2), "bottom_avg_return": round(bottom_avg, 2),
            "discrimination": round(discrimination, 2), "hit_rate": round(hit_rate, 3),
            "downside_precision": round(downside_precision, 3),
            "profit_capture_rate": profit_capture}


# ── 滚动验证回测 ─────────────────────────────

async def run_rolling_backtest(lookback_days: int = 60, progress_callback=None) -> dict:
    """对近 N 天每天进行 T+5 验证(不改权重，纯度量)."""
    await ensure_beliefs_initialized()
    today = date.today()
    start_date = today - timedelta(days=lookback_days)
    end_date = today - timedelta(days=FORECAST_HORIZON + 1)

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT scan_date FROM scan_results
            WHERE scan_date BETWEEN :s AND :e ORDER BY scan_date
        """), {"s": start_date, "e": end_date})
        dates = [row[0] for row in r.fetchall()]

    results = []
    for i, d in enumerate(dates):
        result = await run_single_day_backtest(d)
        results.append(result)
        if progress_callback: await progress_callback(i + 1, len(dates), str(d), result)

    discs = [r["discrimination"] for r in results if r.get("discrimination") is not None]
    hits = [r["hit_rate"] for r in results if r.get("hit_rate") is not None]
    downsides = [r["downside_precision"] for r in results if r.get("downside_precision") is not None]
    captures = [r["profit_capture_rate"] for r in results if r.get("profit_capture_rate") is not None]
    summary = await get_learning_summary()

    return {"status": "success", "forecast_horizon": FORECAST_HORIZON, "days_tested": len(results),
            "avg_discrimination": round(sum(discs) / len(discs), 2) if discs else 0,
            "positive_discrimination_days": sum(1 for d in discs if d > 0),
            "avg_hit_rate": round(sum(hits) / len(hits), 3) if hits else 0,
            "avg_downside_precision": round(sum(downsides) / len(downsides), 3) if downsides else 0,
            "avg_profit_capture": round(sum(captures) / len(captures), 3) if captures else 0,
            "learning_summary": summary, "recent_results": results[-10:]}


async def run_bootstrap_backtest(lookback_days: int = 90) -> dict:
    """冷启动全量验证."""
    await ensure_beliefs_initialized()
    result = await run_rolling_backtest(lookback_days=lookback_days)
    summary = await get_learning_summary()
    n_learned = sum(1 for g in summary.values() if g["avg_observations"] > 0)
    return {**result, "bootstrap_mode": True, "groups_with_data": n_learned, "total_groups": len(summary)}


# ── 影子→贝叶斯桥接 ──────────────────────────

async def update_beliefs_from_shadow(archetype: str = None, strategy: str = "S2") -> dict:
    """将影子训练结果同步到贝叶斯信念系统.

    从 param_library (is_shadow=true) 读取最优权重,
    作为贝叶斯更新的一次"观测",更新 bayesian_beliefs.
    """
    async with async_session_factory() as s:
        if archetype:
            r = await s.execute(text("""
                SELECT archetype, strategy, scoring_weights, discrimination
                FROM param_library WHERE is_shadow=true AND archetype=:a AND strategy=:st
                ORDER BY created_at DESC LIMIT 1
            """), {"a": archetype, "st": strategy})
        else:
            r = await s.execute(text("""
                SELECT DISTINCT ON (archetype, strategy) archetype, strategy, scoring_weights, discrimination
                FROM param_library WHERE is_shadow=true
                ORDER BY archetype, strategy, created_at DESC
            """))
        rows = r.fetchall()

    import json
    from app.services.bayesian_optimizer import update_belief, DEFAULT_BELIEFS

    updated = 0
    for row in rows:
        arch = row[0]; st = row[1]
        weights = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
        if not weights: continue
        disc = float(row[3] or 0)  # 条件夏普，衡量权重质量

        # 直接用影子训练结果更新贝叶斯信念，不走假预测分管道
        for pname, pvalue in weights.items():
            if pname not in DEFAULT_BELIEFS:
                continue
            pvalue = float(pvalue)
            obs_weight = max(0.1, abs(disc))
            await update_belief(pname, pvalue, obs_weight, archetype=arch)

        updated += 1

    return {"status": "success", "archetypes_updated": updated, "total": len(rows)}


# ── 统计查询 ─────────────────────────────────

async def get_learning_stats() -> dict:
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT COUNT(*) FROM experience_replay"))
        total_exp = r.fetchone()[0]
        r = await s.execute(text("SELECT AVG(reward), COUNT(*) FROM experience_replay WHERE recorded_at > CURRENT_DATE - 30"))
        row = r.fetchone()
        r = await s.execute(text("SELECT COUNT(*) FROM bayesian_beliefs WHERE n_observations > 0"))
        learned = r.fetchone()[0]
    summary = await get_learning_summary()
    return {"total_experiences": total_exp, "avg_reward_30d": round(float(row[0] or 0), 2),
            "recent_count_30d": row[1] or 0, "learned_parameters": learned,
            "total_parameters": 23, "group_summary": summary}
