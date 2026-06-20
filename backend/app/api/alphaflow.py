"""AlphaFlow API — 主升浪候选池 + 乏力度检测."""
import asyncio as _asyncio
import json as _json
import numpy as np
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db, async_session_factory
from app.services.alphaflow_features import compute_wave_features, compute_sxqs_features
from datetime import date

router = APIRouter(prefix="/alphaflow", tags=["alphaflow"])


def _timing_note(regime: str, factor: float) -> str:
    """天时因子说明."""
    if factor >= 1.1:
        return f"{regime} — 天时有利, 排名加权 ×{factor}"
    elif factor >= 0.95:
        return f"{regime} — 天时中性, 排名微调 ×{factor}"
    elif factor >= 0.75:
        return f"{regime} — 天时偏弱, 排名折价 ×{factor}"
    else:
        return f"{regime} — 天时严厉, 排名大幅折价 ×{factor}"


async def _compute_sxqs_signal(ts_code: str, market_factor: float = 1.0) -> dict | None:
    """SXQS signal — delegated to sxqs_signal service (v4.3)."""
    from app.services.sxqs_signal import compute_sxqs_signal
    return await compute_sxqs_signal(ts_code, market_factor)



@router.get("/pool")
async def get_pool(
    tier: str = Query("all", description="active/observe/dormant/all"),
    limit: int = Query(50),
    fast: bool = Query(True, description="Skip goose detection + structure maintenance for speed"),
):
    """AlphaFlow pool — delegated to alphaflow_pool_service (v4.5)."""
    tier_filter = ""
    if tier == "active": tier_filter = "AND tier = 'active'"
    elif tier == "observe": tier_filter = "AND tier = 'observe'"
    elif tier == "dormant": tier_filter = "AND tier = 'dormant'"

    from app.services.alphaflow_pool_service import get_pool_with_maintenance
    return await get_pool_with_maintenance(tier_filter, limit, fast=fast)


@router.post("/scan")
async def trigger_scan():
    """两期 SSE 扫描: ①池内股票快速更新 → ②后台全市场扫新蛋."""
    from app.services.alphaflow_pool import daily_scan

    async def sse_gen():
        queue = _asyncio.Queue()

        def _evt(data_dict):
            return f"data: {_json.dumps(data_dict, ensure_ascii=False)}\n\n"

        async def progress_cb(phase, current, total, extra=None):
            try:
                await queue.put({
                    "type": "progress",
                    "phase": phase, "current": current, "total": total,
                    "msg": str(extra) if extra else f"{phase} {current}/{total}"
                })
            except Exception:
                pass

        async def run_scan():
            try:
                # ── Phase 1: 扫描池内股票 (~100只, 快速) ──
                from sqlalchemy import text
                async with async_session_factory() as s:
                    r = await s.execute(text("SELECT ts_code FROM alphaflow_pool"))
                    pool_codes = [row[0] for row in r.fetchall()]

                if pool_codes:
                    await queue.put({"type": "phase", "phase": "1", "msg": f"第一期: 扫描池内 {len(pool_codes)} 只股票..."})
                    result1 = await daily_scan(progress_callback=progress_cb,
                                               restrict_symbols=pool_codes)
                    await queue.put({"type": "done_phase1", "data": result1,
                                     "pool_scanned": len(pool_codes)})
                else:
                    await queue.put({"type": "done_phase1", "data": {"total_pool": 0},
                                     "pool_scanned": 0})

                # ── Phase 2: 全市场扫描新蛋 (后台) ──
                await queue.put({"type": "phase", "phase": "2",
                                 "msg": "第二期: 全市场扫新蛋..."})
                # 取已入池的代码, Phase 2 扫全市场但排除已入池 (避免重复评分)
                async with async_session_factory() as s:
                    r = await s.execute(text("SELECT ts_code FROM alphaflow_pool"))
                    existing = {row[0] for row in r.fetchall()}

                # 全市场列表, 只扫未入池的
                async with async_session_factory() as s:
                    r = await s.execute(text("""
                        SELECT DISTINCT ts_code FROM daily_kline
                        WHERE trade_date >= CURRENT_DATE - 5
                    """))
                    all_codes = [row[0] for row in r.fetchall()]

                _SKIP = ("000300.SH","000016.SH","000905.SH","000852.SH","000001.SH",
                         "000688.SH","399001.SZ","399006.SZ","399005.SZ")
                new_codes = [c for c in all_codes
                             if c not in existing and not c.endswith(".SI") and c not in _SKIP]

                if new_codes:
                    result2 = await daily_scan(progress_callback=progress_cb,
                                               restrict_symbols=new_codes)
                    await queue.put({"type": "done", "data": result2,
                                     "phase1_pool": len(pool_codes),
                                     "phase2_new": len(new_codes)})
                else:
                    await queue.put({"type": "done", "data": result1})
            except Exception as e:
                await queue.put({"type": "error", "msg": str(e)[:200]})

        _asyncio.create_task(run_scan())

        yield _evt({"type": "start", "msg": "两期扫描启动..."})

        while True:
            try:
                event = await _asyncio.wait_for(queue.get(), timeout=600)
            except _asyncio.TimeoutError:
                yield _evt({"type": "heartbeat", "msg": "扫描进行中..."})
                continue
            yield _evt(event)
            if event.get("type") in ("done", "error"):
                break

    return StreamingResponse(sse_gen(), media_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/fatigue")
async def get_fatigue(
    symbols: str = Query("", description="逗号分隔的股票代码"),
):
    """检测乏力信号 — 批量或单只."""
    from app.services.fatigue_detector import detect_fatigue

    codes = [s.strip() for s in symbols.split(",") if s.strip()][:10]
    if not codes:
        # 默认查池中最高概率的 5 只
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT ts_code FROM alphaflow_pool ORDER BY current_prob DESC LIMIT 5"
            ))
            codes = [row[0] for row in r.fetchall()]

    results = []
    for code in codes:
        r = await detect_fatigue(code, date.today())
        results.append(r)

    return {"status": "success", "data": results, "count": len(results)}


@router.get("/lock-detail")
async def get_lock_detail(symbol: str = Query(...)):
    """单只股票的锁死详情: 锁周期/底延顶延/振幅收敛/T模式建议.

    v4.3: 委托 lock_detail_service.
    """
    from app.services.lock_detail_service import get_full_lock_detail
    return await get_full_lock_detail(symbol)

@router.get("/chip-analysis")
async def get_chip_analysis(symbol: str = Query(...)):
    """单只股票筹码吸收率分析 — 锁死/套牢/支撑三区间."""
    from app.services.chip_analyzer import analyze_chip_absorption

    code = symbol.strip().upper()
    result = {"symbol": code}

    chip = await analyze_chip_absorption(code)
    if not chip or "error" in chip:
        result["error"] = chip.get("error", "筹码数据不可用") if chip else "分析失败"
        return result

    result.update(chip)
    return {"status": "success", "data": result}


@router.get("/wave-predict")
async def get_wave_prediction(symbol: str = Query(...)):
    """浪幅预测: 基于历史锁周期预测本次目标区间."""
    from app.services.wave_predictor import predict_wave_target
    import numpy as np

    code = symbol.strip().upper()
    result = {"symbol": code}

    # 获取锁周期历史 (与 lock-detail 相同的算法)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, close, high, low
            FROM daily_kline WHERE ts_code = :c ORDER BY trade_date
        """), {"c": code})
        rows = [(row[0], float(row[1] or 0), float(row[2] or 0), float(row[3] or 0))
               for row in r.fetchall()]

    if len(rows) < 200:
        return {"status": "error", "detail": "数据不足"}

    cs = np.array([r[1] for r in rows])
    hs = np.array([r[2] for r in rows])
    ls = np.array([r[3] for r in rows])
    nn = len(cs)

    # 锁周期检测
    lock_cycles = []; i = 0
    while i < nn - 20:
        w20_l = float(np.min(ls[i:i+20])); w20_h = float(np.max(hs[i:i+20]))
        if w20_l <= 0: i += 1; continue
        if (w20_h - w20_l) / w20_l * 100 <= 15.0:
            start, lh, ll = i, w20_h, w20_l
            while i < nn - 1:
                if i + 10 > nn: break
                lh = max(lh, float(hs[i])); ll = min(ll, float(ls[i]))
                seg_len = i - start + 1
                if seg_len <= 20:
                    if (lh - ll) / max(ll, 0.01) * 100 > 15.0: break
                else:
                    if (lh - ll) / max(ll, 0.01) * 100 > 17.0: break
                i += 1
            end = i - 1
            if end - start >= 20:
                lock_cycles.append({"start": start, "end": end, "days": end - start + 1,
                    "high": round(float(lh), 2), "low": round(float(ll), 2)})
            i = end + 1
        else: i += 1

    if len(lock_cycles) < 2:
        return {"status": "error", "detail": f"锁周期不足({len(lock_cycles)}轮), 无法预测"}

    current_price = float(cs[-1])
    # 传入已截断除权的日线数据, 避免浪幅被除权前的高价污染
    wp = await predict_wave_target(code, lock_cycles, current_price,
                                    all_daily_closes=cs,
                                    all_daily_highs=hs)

    result.update(wp)
    return {"status": "success", "data": result}


@router.get("/zigzag-trend")
async def get_zigzag_trend(symbol: str = Query(..., description="股票代码")):
    """ZigZag 趋势线 — 同花顺趋势自动线复刻 (5%偏差)."""
    from app.services.zigzag_trendline import compute_zigzag_signal
    result = await compute_zigzag_signal(symbol.strip().upper())
    if result is None:
        return {"status": "error", "detail": "数据不足"}
    return {"status": "success", "data": result}


@router.get("/status")
async def get_status():
    """AlphaFlow 系统状态."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT tier, COUNT(*) as n,
                   ROUND(AVG(current_prob)::numeric, 3) as avg_prob,
                   ROUND(AVG(days_in_pool)::numeric, 0) as avg_days
            FROM alphaflow_pool GROUP BY tier ORDER BY tier
        """))
        tiers = {}
        for row in r.fetchall():
            tiers[row[0]] = {
                "count": row[1],
                "avg_prob": float(row[2] or 0),
                "avg_days": float(row[3] or 0),
            }

        r2 = await s.execute(text("SELECT COUNT(*) FROM alphaflow_pool"))
        total = r2.scalar() or 0
        r3 = await s.execute(text(
            "SELECT COUNT(*) FROM alphaflow_pool WHERE last_updated = CURRENT_DATE"
        ))
        updated_today = r3.scalar() or 0

    return {
        "status": "success",
        "total_pool": total,
        "updated_today": updated_today,
        "model_version": "wave_v4_veteran",
        "tiers": tiers,
    }


@router.get("/big-fairy")
async def get_big_fairy(symbol: str = Query(..., description="股票代码，如 600025.SH")):
    """单只股票大神仙空头评分 — KDJ+MACD+MA+RSI 四大维度综合."""
    from app.services.big_fairy import compute_big_fairy

    code = symbol.strip().upper()
    bf = await compute_big_fairy(code)
    if bf is None:
        return {"status": "error", "detail": f"数据不足, 无法计算"}
    return {"status": "success", "data": bf}


@router.get("/veteran-backtest")
async def get_veteran_backtest(days: int = 180):
    """回测老兵突破率 — 验证检测阈值是否有效.

    统计历史 veteran 信号发出后 T+5/T+20 的实际突破率,
    按级别(pre_breakout/late_stage/monitoring)和分数段分析。
    """
    from app.services.alphaflow_veteran import backtest_veteran_breakout_rate
    result = await backtest_veteran_breakout_rate(lookback_days=days)
    return result
