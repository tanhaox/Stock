"""AlphaFlow 老兵(Veteran)检测器 — 基于 lock-detail 已有的锁周期数据.

不重复锁检测算法, 直接使用 lock-detail 端点的锁周期历史,
分析锁死规律: 平均天数、最长天数、当前进度、振幅收敛。

用于:
  - lock-detail API: 附加 veteran 字段
  - pool scan: old hands auto-enroll bypass XGBoost threshold
"""
import numpy as np
import logging
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.veteran")


async def detect_veteran(ts_code: str) -> dict | None:
    """检测是否为锁死老兵 (5+ 轮历史锁死, 复用 lock-detail 逻辑)."""

    async with async_session_factory() as s:
        # 只取最近 500 根 K 线 (≈ 2 年), 足够识别锁死周期
        r = await s.execute(text("""
            SELECT trade_date, close, high, low, volume
            FROM daily_kline WHERE ts_code = :c
            ORDER BY trade_date DESC LIMIT 800
        """ ), {"c": ts_code})
        rows = list(reversed(r.fetchall()))
        rows = [(row[0], float(row[1] or 0), float(row[2] or 0),
                 float(row[3] or 0), float(row[4] or 0)) for row in rows]

    if len(rows) < 200:
        return None

    closes = np.array([r[1] for r in rows])
    highs = np.array([r[2] for r in rows])
    lows = np.array([r[3] for r in rows])
    volumes = np.array([r[4] for r in rows])

    n = len(closes)

    # ── 锁死周期检测: 双窗口 (15-20天≤15% + 20-40天≤17%) ──
    lock_segments = []
    i = 0
    while i < n - 20:
        w20_l = float(np.min(lows[i:i+20]))
        w20_h = float(np.max(highs[i:i+20]))
        if w20_l <= 0:
            i += 1
            continue
        w20_amp = (w20_h - w20_l) / w20_l * 100
        if w20_amp <= 15.0:
            start, lh, ll = i, w20_h, w20_l
            while i < n - 1:
                if i + 10 > n:
                    break
                lh = max(lh, float(highs[i]))
                ll = min(ll, float(lows[i]))
                seg_len = i - start + 1
                if seg_len <= 20:
                    if (lh - ll) / max(ll, 0.01) * 100 > 15.0:
                        break
                else:
                    if (lh - ll) / max(ll, 0.01) * 100 > 17.0:
                        break
                i += 1
            end = i - 1
            lock_days = end - start + 1
            if lock_days >= 20:
                lock_segments.append({
                    "start": start, "end": end,
                    "days": lock_days,
                    "start_date": str(rows[start][0]),
                    "end_date": str(rows[end][0]),
                    "high": round(float(lh), 2),
                    "low": round(float(ll), 2),
                    "avg_price": float(np.mean(closes[start:end+1])),
                    "avg_vol": float(np.mean(volumes[start:end+1])),
                })
                i = end + 1  # 跳过已记录的段, 避免重复
            else:
                i = start + 1  # 锁死太短, 前进一步
        else:
            i += 1

    total_cycles = len(lock_segments)
    logger.info(f"Veteran {ts_code}: {total_cycles} lock cycles found")

    if total_cycles < 4:
        return None

    # ── 历史统计 ──
    cycle_days = [s["days"] for s in lock_segments]
    avg_cycle_days = float(np.mean(cycle_days))
    max_cycle_days = int(np.max(cycle_days))
    median_cycle_days = float(np.median(cycle_days))
    std_cycle_days = float(np.std(cycle_days))

    # ── 当前是否在锁死中? ──
    last_seg = lock_segments[-1]
    current_in_lock = (last_seg["end"] >= n - 5)  # 最后一个段结尾靠近今天
    current_cycle = total_cycles
    current_days = last_seg["days"]

    if current_in_lock:
        days_ratio = current_days / max(avg_cycle_days, 1)
    else:
        # 不在锁死中, 看距上次锁死结束多少天
        days_since_last = n - last_seg["end"] - 1
        if days_since_last > 30:
            return None  # 已出锁超过 30 天, 不关注
        # 刚出锁, 使用上一轮数据
        current_days = last_seg["days"]
        days_ratio = current_days / max(avg_cycle_days, 1)

    rank_pct = sum(1 for d in cycle_days if d >= current_days) / total_cycles * 100

    # ── 振幅收敛: 当前锁死前后半段 ──
    seg_start, seg_end = last_seg["start"], last_seg["end"]
    seg_len = seg_end - seg_start + 1
    if seg_len > 10:
        mid = seg_start + seg_len // 2
        early_h = float(np.max(highs[seg_start:mid]))
        early_l = float(np.min(lows[seg_start:mid]))
        late_h = float(np.max(highs[mid:seg_end+1]))
        late_l = float(np.min(lows[mid:seg_end+1]))
        early_amp = (early_h - early_l) / max(early_l, 0.01) * 100
        late_amp = (late_h - late_l) / max(late_l, 0.01) * 100
        amp_converging = late_amp < early_amp * 0.85
    else:
        early_amp = late_amp = 0
        amp_converging = False

    # ── 量能萎缩 ──
    if seg_len > 10:
        half = seg_len // 2
        early_vol = float(np.mean(volumes[seg_start:seg_start+half]))
        late_vol = float(np.mean(volumes[seg_start+half:seg_end+1]))
        vol_shrinking = late_vol < early_vol * 0.7 and early_vol > 0
    else:
        vol_shrinking = False

    # ── 老兵等级 ──
    conditions = 0
    if days_ratio >= 0.80:
        conditions += 1
    if amp_converging:
        conditions += 1
    if vol_shrinking:
        conditions += 1

    if conditions == 3:
        level = "pre_breakout"
    elif conditions >= 2:
        level = "late_stage"
    else:
        level = "monitoring"

    duration_score = min(100, max(0, (current_days - avg_cycle_days * 0.5) / max(avg_cycle_days, 1) * 100))
    converge_bonus = 20 if amp_converging else 0
    vol_bonus = 15 if vol_shrinking else 0
    cycle_bonus = min(25, total_cycles * 2)
    score = min(100, duration_score + converge_bonus + vol_bonus + cycle_bonus)

    verdict_map = {
        "pre_breakout": f"老兵尾期: {total_cycles}轮, 当前{days_ratio*100:.0f}%进度(均{avg_cycle_days:.0f}d), 振幅收敛+量缩 — 随时启动",
        "late_stage": f"老兵后期: {total_cycles}轮, {days_ratio*100:.0f}%进度, 距均值{avg_cycle_days:.0f}d",
        "monitoring": f"老兵监控: {total_cycles}轮, 当前{days_ratio*100:.0f}%进度",
    }

    logger.info(f"Veteran {ts_code}: level={level} score={score} cycles={total_cycles} "
                f"days={current_days}/{avg_cycle_days:.0f}d ratio={days_ratio:.2f} "
                f"amp={early_amp:.1f}%→{late_amp:.1f}% conv={amp_converging} vol_sk={vol_shrinking}")

    return {
        "veteran": True,
        "level": level,
        "total_cycles": total_cycles,
        "current_cycle": current_cycle,
        "current_days": current_days,
        "avg_cycle_days": round(avg_cycle_days, 1),
        "max_cycle_days": max_cycle_days,
        "median_cycle_days": round(median_cycle_days, 1),
        "std_cycle_days": round(std_cycle_days, 1),
        "days_ratio": round(days_ratio, 2),
        "rank_pct": round(rank_pct, 1),
        "amp_converging": amp_converging,
        "amp_early": round(early_amp, 1),
        "amp_late": round(late_amp, 1),
        "vol_shrinking": vol_shrinking,
        "score": round(score, 1),
        "verdict": verdict_map.get(level, "老兵"),
    }


async def backtest_veteran_breakout_rate(lookback_days: int = 180) -> dict:
    """回测老兵突破率 — 验证 veteran 检测阈值是否有效.

    核心问题: "老兵强制入池" 是否让低质量股票污染了池子?
    回答: 统计历史 veteran 信号发出后 T+5/T+20 的实际突破率.

    Returns:
        {total_veterans, broke_out_5d, broke_out_20d, avg_breakout_pct,
         by_level: {pre_breakout/late_stage/monitoring: {count, breakout_rate, avg_gain}},
         threshold_analysis: {...}}
    """
    from datetime import date as dt_date, timedelta
    cutoff = dt_date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        # 查历史 pool 中有 veteran 标记的记录
        # v7.0.34: alphaflow_pool 是 per-stock 当前快照, 用 last_updated 作为"扫描日期"
        #          移除不存在的列 (in_pool / xgb_prob / scan_date 已废弃)
        r = await s.execute(text("""
            SELECT ap.ts_code, ap.last_updated as scan_date, ap.veteran_level, ap.veteran_score,
                   ap.current_prob,
                   dk.close as scan_close,
                   dk5.close as close_5d, dk20.close as close_20d
            FROM alphaflow_pool ap
            JOIN daily_kline dk ON dk.ts_code = ap.ts_code AND dk.trade_date = ap.last_updated
            LEFT JOIN daily_kline dk5 ON dk5.ts_code = ap.ts_code
                AND dk5.trade_date = (SELECT trade_date FROM daily_kline
                    WHERE ts_code = ap.ts_code AND trade_date > ap.last_updated
                    ORDER BY trade_date LIMIT 1 OFFSET 4)
            LEFT JOIN daily_kline dk20 ON dk20.ts_code = ap.ts_code
                AND dk20.trade_date = (SELECT trade_date FROM daily_kline
                    WHERE ts_code = ap.ts_code AND trade_date > ap.last_updated
                    ORDER BY trade_date LIMIT 1 OFFSET 19)
            WHERE ap.last_updated >= :cut
              AND ap.veteran_detected = true
            ORDER BY ap.last_updated DESC
        """), {"cut": cutoff})
        rows = r.fetchall()

    if not rows:
        return {"status": "skipped", "reason": "无历史 veteran 数据", "total_veterans": 0}

    total = len(rows)
    broke_5d = 0
    broke_20d = 0
    total_gain_5d = 0.0
    total_gain_20d = 0.0
    by_level: dict[str, dict] = {}

    for row in rows:
        # v7.0.34: row 索引 (无 in_pool / xgb_prob) — ts_code, scan_date, level, vet_score, current_prob, scan_close, close_5d, close_20d
        ts_code, scan_date, level, vet_score, current_prob = row[0:5]
        scan_close = float(row[5] or 0)
        close_5d = float(row[6] or 0)
        close_20d = float(row[7] or 0)

        level = level or "monitoring"
        gain_5d = (close_5d - scan_close) / scan_close * 100 if scan_close > 0 and close_5d > 0 else None
        gain_20d = (close_20d - scan_close) / scan_close * 100 if scan_close > 0 and close_20d > 0 else None

        # 突破定义: T+5 涨>3% 或 T+20 涨>5%
        is_breakout_5d = gain_5d is not None and gain_5d > 3.0
        is_breakout_20d = gain_20d is not None and gain_20d > 5.0

        if is_breakout_5d:
            broke_5d += 1
            total_gain_5d += gain_5d
        if is_breakout_20d:
            broke_20d += 1
            total_gain_20d += gain_20d

        if level not in by_level:
            by_level[level] = {"count": 0, "broke_5d": 0, "broke_20d": 0,
                               "gains_5d": [], "gains_20d": []}
        by_level[level]["count"] += 1
        if is_breakout_5d:
            by_level[level]["broke_5d"] += 1
            by_level[level]["gains_5d"].append(gain_5d)
        if is_breakout_20d:
            by_level[level]["broke_20d"] += 1
            by_level[level]["gains_20d"].append(gain_20d)

    # 组装结果
    level_stats = {}
    for level, data in by_level.items():
        cnt = data["count"]
        level_stats[level] = {
            "count": cnt,
            "breakout_rate_5d": round(data["broke_5d"] / cnt * 100, 1) if cnt > 0 else 0,
            "breakout_rate_20d": round(data["broke_20d"] / cnt * 100, 1) if cnt > 0 else 0,
            "avg_gain_5d": round(float(np.mean(data["gains_5d"])), 1) if data["gains_5d"] else 0,
            "avg_gain_20d": round(float(np.mean(data["gains_20d"])), 1) if data["gains_20d"] else 0,
        }

    # 阈值分析: 不同 veteran_score 段的突破率
    threshold_analysis = []
    for threshold in [40, 50, 60, 70, 80]:
        above = sum(1 for row in rows if (row[3] or 0) >= threshold)
        above_breakout = sum(1 for row in rows
                            if (row[3] or 0) >= threshold
                            and float(row[8] or 0) / max(float(row[6] or 1), 0.01) > 1.05)
        threshold_analysis.append({
            "score_threshold": threshold,
            "stocks_above": above,
            "breakout_rate": round(above_breakout / above * 100, 1) if above > 0 else 0,
        })

    avg_breakout_5d = round(broke_5d / total * 100, 1) if total > 0 else 0
    avg_breakout_20d = round(broke_20d / total * 100, 1) if total > 0 else 0

    logger.info(f"Veteran backtest: {total} veterans, "
                f"breakout_5d={avg_breakout_5d}%, breakout_20d={avg_breakout_20d}%")

    return {
        "status": "success",
        "total_veterans": total,
        "breakout_rate_5d": avg_breakout_5d,
        "breakout_rate_20d": avg_breakout_20d,
        "avg_gain_5d": round(total_gain_5d / max(broke_5d, 1), 1),
        "avg_gain_20d": round(total_gain_20d / max(broke_20d, 1), 1),
        "by_level": level_stats,
        "threshold_analysis": threshold_analysis,
        "verdict": (
            f"老兵突破率: T+5={avg_breakout_5d}% T+20={avg_breakout_20d}%. "
            + (f"pre_breakout精度最高({level_stats.get('pre_breakout',{}).get('breakout_rate_20d',0)}%)"
               if 'pre_breakout' in level_stats else "分级统计待积累")
        ),
    }

