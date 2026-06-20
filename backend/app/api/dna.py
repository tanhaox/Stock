"""DNA 系统 API 端点 — /api/dna

独立于现有 API, 与现有系统完全并行。

端点:
  GET  /dna/status           — DNA 系统状态
  GET  /dna/profile/{symbol} — 单股 DNA 档案
  POST /dna/predict           — 多窗口预测
  POST /dna/scan              — 对推荐+持仓运行 DNA 评分
  GET  /dna/compare           — DNA 对比
  GET  /dna/emotion/{symbol}/history — 表情序列历史
"""
import json
import logging
from datetime import date
from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("stock_dna.api")

router = APIRouter(prefix="/dna", tags=["dna"])


# ══════════════════════════════════════════════════════════════════════
# 请求模型
# ══════════════════════════════════════════════════════════════════════

class PredictRequest(BaseModel):
    symbols: list[str]
    trade_date: str | None = None  # YYYY-MM-DD, None=今天


class ScanRequest(BaseModel):
    trade_date: str | None = None


# ══════════════════════════════════════════════════════════════════════
# 端点实现
# ══════════════════════════════════════════════════════════════════════

@router.get("/status")
async def dna_status():
    """DNA 系统总览."""
    async with async_session_factory() as s:
        # 模型数
        r = await s.execute(text("SELECT COUNT(*) FROM stock_dna.profiles WHERE best_horizon IS NOT NULL"))
        models_trained = r.fetchone()[0]

        # 样本数
        r2 = await s.execute(text("SELECT COUNT(*) FROM stock_dna.daily_samples"))
        total_samples = r2.fetchone()[0]

        # 平均 AUC
        r3 = await s.execute(text(
            "SELECT AVG(best_horizon_auc) FROM stock_dna.profiles WHERE best_horizon_auc IS NOT NULL"
        ))
        avg_auc = r3.fetchone()[0]

        # 最佳窗口分布
        r4 = await s.execute(text(
            "SELECT best_horizon, COUNT(*) FROM stock_dna.profiles "
            "WHERE best_horizon IS NOT NULL GROUP BY best_horizon ORDER BY best_horizon"
        ))
        horizon_dist = {f"T+{row[0]}": row[1] for row in r4.fetchall()}

        # 周期规律分布
        r5 = await s.execute(text(
            "SELECT COUNT(*) FROM stock_dna.profiles WHERE cycle_cv < 0.3"
        ))
        regular = r5.fetchone()[0]
        r6 = await s.execute(text(
            "SELECT COUNT(*) FROM stock_dna.profiles WHERE cycle_cv >= 0.3 AND cycle_cv < 999"
        ))
        irregular = r6.fetchone()[0]

    return {
        "status": "success",
        "models_trained": models_trained,
        "total_samples": total_samples,
        "avg_auc_t5": round(float(avg_auc), 4) if avg_auc else 0,
        "horizon_distribution": horizon_dist,
        "regular_cycles": regular,
        "irregular_cycles": irregular,
    }


@router.get("/profile/{symbol}")
async def get_dna_profile(symbol: str):
    """获取单只股票的完整 DNA 档案."""
    async with async_session_factory() as s:
        r = await s.execute(text("SELECT * FROM stock_dna.profiles WHERE symbol=:sym"), {"sym": symbol})
        row = r.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的 DNA 档案")

        cols = [c[0] for c in r.cursor.description]
        prof = dict(zip(cols, row))

    # 格式化 JSONB 字段
    for key in ["transition_matrix", "stationary_dist", "top_features", "horizon_auc_json",
                "similar_stocks", "emotion_names"]:
        val = prof.get(key)
        if isinstance(val, str):
            try:
                prof[key] = json.loads(val)
            except Exception:
                pass

    # 构建可读的 DNA 卡
    dna_card = {
        "symbol": symbol,
        "best_window": {
            "horizon": prof.get("best_horizon"),
            "auc": prof.get("best_horizon_auc"),
            "all_aucs": prof.get("horizon_auc_json", {}),
        },
        "emotion_fingerprint": {
            "n_emotions": prof.get("n_emotions"),
            "entropy": prof.get("emotion_entropy"),
            "best_emotion": prof.get("best_emotion"),
            "best_emotion_ret": prof.get("best_emotion_ret"),
            "names": prof.get("emotion_names", {}),
        },
        "cycle_rhythm": {
            "avg_lockup_days": prof.get("avg_lockup_days"),
            "std_lockup_days": prof.get("std_lockup_days"),
            "cv": prof.get("cycle_cv"),
            "avg_breakout_return": prof.get("avg_breakout_return"),
            "avg_breakout_days": prof.get("avg_breakout_days"),
        },
        "drivers": prof.get("top_features", [])[:5],
        "behavior": {
            "crash_resilience": prof.get("crash_resilience"),
            "rally_capture": prof.get("rally_capture"),
            "deception_rate": prof.get("deception_rate"),
            "consistency": prof.get("consistency"),
            "extreme_tail": prof.get("extreme_tail"),
        },
        "meta": {
            "training_samples": prof.get("training_samples"),
            "archetype": prof.get("archetype"),
            "last_trained": str(prof.get("last_trained")) if prof.get("last_trained") else None,
            "last_dna_update": str(prof.get("last_dna_update")) if prof.get("last_dna_update") else None,
        },
    }

    return {"status": "success", "data": dna_card}


@router.post("/predict")
async def predict(req: PredictRequest):
    """对指定股票列表运行 DNA 预测."""
    from app.services.stock_dna.inference import scorer

    td = None
    if req.trade_date:
        td = date.fromisoformat(req.trade_date)

    results = await scorer.batch_predict(req.symbols, td)
    return {"status": "success", "predictions": results}


@router.post("/scan")
async def run_dna_scan(req: ScanRequest = None):
    """对持仓 + 最近推荐股票运行 DNA 评分, 写入 predictions."""
    async with async_session_factory() as s:
        # 获取目标股票
        r = await s.execute(text("SELECT DISTINCT symbol FROM holdings"))
        syms = set(row[0] for row in r.fetchall())
        r2 = await s.execute(text(
            "SELECT DISTINCT ON (symbol) symbol FROM analysis_scores ORDER BY symbol, scan_date DESC LIMIT 100"
        ))
        for row in r2.fetchall():
            syms.add(row[0])

    td = date.fromisoformat(req.trade_date) if req and req.trade_date else date.today()
    from app.services.stock_dna.inference import scorer
    results = await scorer.batch_predict(sorted(syms), td)

    # 写入 predictions
    async with async_session_factory() as s:
        for pred in results:
            if pred.get("status") == "no_model":
                continue
            await s.execute(text("""
                INSERT INTO stock_dna.predictions
                    (scan_date, symbol, current_emotion, current_cycle_phase, current_cycle_day,
                     pred_excess_t2, pred_excess_t5, pred_excess_t10, pred_excess_t20,
                     pred_win_prob_t2, pred_win_prob_t5, pred_win_prob_t10, pred_win_prob_t20,
                     best_horizon, confidence, created_at)
                VALUES (:sd, :sym, :ce, :cp, :cd,
                        :pt2, :pt5, :pt10, :pt20,
                        :pw2, :pw5, :pw10, :pw20,
                        :bh, :conf, NOW())
                ON CONFLICT (scan_date, symbol) DO UPDATE SET
                    pred_excess_t2=EXCLUDED.pred_excess_t2,
                    pred_excess_t5=EXCLUDED.pred_excess_t5,
                    pred_excess_t10=EXCLUDED.pred_excess_t10,
                    pred_excess_t20=EXCLUDED.pred_excess_t20,
                    pred_win_prob_t2=EXCLUDED.pred_win_prob_t2,
                    pred_win_prob_t5=EXCLUDED.pred_win_prob_t5,
                    pred_win_prob_t10=EXCLUDED.pred_win_prob_t10,
                    pred_win_prob_t20=EXCLUDED.pred_win_prob_t20,
                    best_horizon=EXCLUDED.best_horizon,
                    confidence=EXCLUDED.confidence,
                    created_at=NOW()
            """), {
                "sd": td,
                "sym": pred.get("symbol"),
                "ce": pred.get("current_emotion", {}).get("id", 0),
                "cp": pred.get("cycle_position", {}).get("phase", "unknown"),
                "cd": pred.get("cycle_position", {}).get("day", 0),
                "pt2": pred.get("predictions", {}).get("t2", {}).get("excess_return", 0),
                "pt5": pred.get("predictions", {}).get("t5", {}).get("excess_return", 0),
                "pt10": pred.get("predictions", {}).get("t10", {}).get("excess_return", 0),
                "pt20": pred.get("predictions", {}).get("t20", {}).get("excess_return", 0),
                "pw2": pred.get("predictions", {}).get("t2", {}).get("win_prob", 0),
                "pw5": pred.get("predictions", {}).get("t5", {}).get("win_prob", 0),
                "pw10": pred.get("predictions", {}).get("t10", {}).get("win_prob", 0),
                "pw20": pred.get("predictions", {}).get("t20", {}).get("win_prob", 0),
                "bh": pred.get("best_horizon", 5),
                "conf": pred.get("confidence", 0),
            })
        await s.commit()

    n_written = sum(1 for r in results if r.get("status") != "no_model")
    return {"status": "success", "predictions_written": n_written, "total_symbols": len(syms)}


@router.get("/compare")
async def compare_dna(symbols: str = Query(default="")):
    """DNA 对比矩阵."""
    async with async_session_factory() as s:
        if not symbols:
            r = await s.execute(text(
                "SELECT DISTINCT symbol FROM stock_dna.profiles WHERE best_horizon IS NOT NULL LIMIT 20"
            ))
            syms = [row[0] for row in r.fetchall()]
        else:
            syms = [s.strip() for s in symbols.split(",") if s.strip()]

    from app.services.stock_dna.similarity import compute_similarity_matrix
    similarity = await compute_similarity_matrix(syms)

    # 摘要
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT symbol, best_horizon, best_horizon_auc, cycle_cv, avg_lockup_days, training_samples "
            "FROM stock_dna.profiles WHERE symbol=ANY(:syms)"
        ), {"syms": syms})
        summary = [
            {"symbol": row[0], "best_horizon": row[1], "auc": float(row[2] or 0),
             "cycle_cv": float(row[3] or 999), "avg_lockup": float(row[4] or 0),
             "training_samples": int(row[5] or 0)}
            for row in r.fetchall()
        ]

    return {
        "status": "success",
        "comparison": summary,
        "similarity_matrix": similarity,
    }


@router.get("/emotion/{symbol}/history")
async def get_emotion_history(symbol: str, days: int = Query(default=60)):
    """某只股票的表情序列历史."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT trade_date, emotion_label FROM stock_dna.daily_samples "
            "WHERE symbol=:sym ORDER BY trade_date DESC LIMIT :lim"
        ), {"sym": symbol, "lim": days})
        seq = [{"date": str(row[0]), "emotion": row[1]} for row in r.fetchall()]

        # 加载表情名称
        r2 = await s.execute(text(
            "SELECT emotion_names FROM stock_dna.profiles WHERE symbol=:sym"
        ), {"sym": symbol})
        prof = r2.fetchone()
        names = {}
        if prof and prof[0]:
            names = prof[0] if isinstance(prof[0], dict) else json.loads(prof[0] or '{}')

        for item in seq:
            item["emotion_name"] = names.get(str(item["emotion"]), f"表情{item['emotion']}")

        # 转移矩阵 + best_emotion_ret → 明日预测
        r3 = await s.execute(text(
            "SELECT transition_matrix, stationary_dist, best_emotion, best_emotion_ret FROM stock_dna.profiles WHERE symbol=:sym"
        ), {"sym": symbol})
        prof2 = r3.fetchone()
        transition_tomorrow = None
        if prof2 and prof2[0] and seq:
            import numpy as np
            P = np.array(prof2[0]) if isinstance(prof2[0], list) else json.loads(prof2[0] or '[]')
            # 最佳/最差表情基于实际收益 (best_emotion_ret), 非转移概率
            best_emotion_ret = prof2[3] if len(prof2) > 3 and prof2[3] else {}
            if isinstance(best_emotion_ret, str):
                best_emotion_ret = json.loads(best_emotion_ret or '{}')
            if len(P) > 0:
                current = seq[0]["emotion"]
                if 0 <= current < len(P):
                    row_p = P[current]
                    best_j = int(np.argmax(row_p))
                    worst_j = int(np.argmin(row_p))
                    # best_case: 可到达表情中历史收益最高的
                    best_ret_j = best_j
                    if best_emotion_ret and isinstance(best_emotion_ret, dict):
                        reachable = {str(j): float(best_emotion_ret.get(str(j), 0) or 0)
                                    for j in range(len(P)) if row_p[j] > 0.05}
                        if reachable:
                            best_ret_j = int(max(reachable, key=reachable.get))
                    transition_tomorrow = {
                        "most_likely": {"emotion": best_j, "name": names.get(str(best_j), f"表情{best_j}"),
                                        "prob": round(float(row_p[best_j]), 3)},
                        "best_case": {"emotion": best_ret_j, "name": names.get(str(best_ret_j), f"表情{best_ret_j}"),
                                      "prob": round(float(row_p[best_ret_j]) if best_ret_j < len(row_p) else 0, 3),
                                      "avg_ret": round(float(best_emotion_ret.get(str(best_ret_j), 0) or 0), 2) if best_emotion_ret else 0},
                        "worst_case": {"emotion": worst_j, "name": names.get(str(worst_j), f"表情{worst_j}"),
                                       "prob": round(float(row_p[worst_j]), 3)},
                    }

    return {
        "status": "success",
        "symbol": symbol,
        "emotion_sequence": seq,
        "transition_tomorrow": transition_tomorrow,
    }


@router.post("/add-stock")
async def add_stock(symbol: str = Query(...)):
    """新增一只股票到 DNA 系统: 生成数据 + 训练模型.

    一步完成 build + train, 适合从前端交互式添加.
    """
    from app.services.stock_dna.data_builder import build_dna_data
    from app.services.stock_dna.model import train_per_stock

    # Step 1: 生成数据
    build_result = await build_dna_data(symbols=[symbol], start_date="2024-01-01")
    if build_result["status"] != "success" and build_result["total_samples"] == 0:
        detail = build_result.get("errors", ["未知错误"])[0] if build_result.get("errors") else "数据生成失败"
        raise HTTPException(status_code=400, detail=f"数据生成失败: {detail}")

    # Step 2: 训练模型
    train_result = await train_per_stock(symbol)
    if train_result["status"] != "success":
        raise HTTPException(status_code=500, detail=f"训练失败: {train_result.get('reason', '未知')}")

    return {
        "status": "success",
        "symbol": symbol,
        "samples": train_result.get("n_samples", 0),
        "auc_t5": train_result.get("auc_t5"),
        "best_horizon": train_result.get("best_horizon"),
    }
