"""个股历史深度复盘 — 5分钟颗粒度精研 (v4.3).

对精选出的少量股票，基于 5 分钟数据做历史回测和模式学习。
将老股民"盯着一只股票反复琢磨"的经验在系统内自动化。

四大子复盘:
  1. 信号有效性回溯 — 此股历史上 TG 信号发出后的真实表现
  2. K线形态匹配 — 当前形态在历史上的最相似走势及后续结果
  3. 关键位置博弈 — 当前是否触及关键均线/前高前低，历史突破率
  4. 筹码吸收模拟 — 利用5分钟数据反推锁死期每日吸收率曲线
  5. 市场敏感性 — 不同 market regime 下的表现差异

缓存: 同日同股的复盘结果缓存于 _drill_cache (内存), 避免重复计算.
"""
import asyncio
import logging
import numpy as np
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("stock_drill")

# ── 内存缓存 ──
_drill_cache: dict[str, dict] = {}  # key = "YYYYMMDD_symbol"

MIN_BARS_FOR_DRILL = 60
HISTORY_LOOKBACK_DAYS = 500
PATTERN_WINDOW = 10
CHIP_SEGMENT_DAYS = 10
TOP_SIMILAR_N = 5


async def drill_stocks(
    symbols: list[str],
    current_date: date,
    market_regime: str = "unknown",
    force_refresh: bool = False,
    progress_callback=None,
) -> dict[str, dict]:
    """主入口 — 对精选股票列表执行四项深度复盘.

    Args:
        symbols: 股票代码列表 (最多30只)
        current_date: 当前扫描日期
        market_regime: 当前市场体制
        force_refresh: 是否强制刷新缓存
        progress_callback: async fn(idx, total, symbol, extra=None)

    Returns:
        {symbol: {signal_effectiveness, pattern_matching, critical_position,
                   chip_simulation, market_sensitivity, drill_summary}}
    """
    total = len(symbols)
    results: dict[str, dict] = {}

    for idx, sym in enumerate(symbols):
        cache_key = f"{current_date}_{sym}"
        if not force_refresh and cache_key in _drill_cache:
            results[sym] = _drill_cache[cache_key]
            if progress_callback:
                await progress_callback(idx + 1, total, sym, "缓存命中")
            continue

        if progress_callback:
            await progress_callback(idx + 1, total, sym, "复盘计算中...")

        report = {"symbol": sym, "date": str(current_date), "status": "ok"}
        start_time = asyncio.get_event_loop().time()

        try:
            # ── 预加载日线数据 (所有子复盘共享) ──
            async with async_session_factory() as s:
                r = await s.execute(text("""
                    SELECT trade_date, open, high, low, close, volume
                    FROM daily_kline WHERE ts_code = :c
                    ORDER BY trade_date DESC LIMIT :lim
                """), {"c": sym, "lim": HISTORY_LOOKBACK_DAYS})
                rows = list(reversed(r.fetchall()))

            if len(rows) < 60:
                report["status"] = "insufficient_data"
                report["reason"] = f"日线数据不足 ({len(rows)}天)"
                results[sym] = report
                _drill_cache[cache_key] = report
                continue

            dates = [row[0] for row in rows]
            opens = np.array([float(row[1] or 0) for row in rows])
            highs = np.array([float(row[2] or 0) for row in rows])
            lows = np.array([float(row[3] or 0) for row in rows])
            closes = np.array([float(row[4] or 0) for row in rows])
            volumes = np.array([float(row[5] or 0) for row in rows])
            current_price = float(closes[-1])

            # ── 子复盘 1: 信号有效性回溯 ──
            try:
                report["signal_effectiveness"] = await _drill_signal_effectiveness(
                    sym, current_date, dates
                )
            except Exception as e:
                logger.debug(f"Signal drill failed for {sym}: {e}")
                report["signal_effectiveness"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ── 子复盘 2: K线形态匹配 ──
            try:
                report["pattern_matching"] = await _drill_pattern_matching(
                    closes, dates
                )
            except Exception as e:
                logger.debug(f"Pattern drill failed for {sym}: {e}")
                report["pattern_matching"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ── 子复盘 3: 关键位置博弈 ──
            try:
                report["critical_position"] = await _drill_critical_position(
                    sym, current_price, closes, highs, lows, dates
                )
            except Exception as e:
                logger.debug(f"Critical position drill failed for {sym}: {e}")
                report["critical_position"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ── 子复盘 4: 筹码吸收模拟 ──
            try:
                report["chip_simulation"] = await _drill_chip_simulation(
                    sym, closes, highs, lows, volumes, dates
                )
            except Exception as e:
                logger.debug(f"Chip simulation drill failed for {sym}: {e}")
                report["chip_simulation"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ── 子复盘 5: 市场敏感性 ──
            try:
                report["market_sensitivity"] = await _drill_market_sensitivity(sym)
            except Exception as e:
                logger.debug(f"Market sensitivity drill failed for {sym}: {e}")
                report["market_sensitivity"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ★ 子复盘 6: 四维共振分析 (v4.3) ──
            try:
                from app.services.resonance_analyzer import analyze_all_resonance
                # 收集历史信号日 (从 scan_results 获取)
                async with async_session_factory() as s:
                    r = await s.execute(text("""
                        SELECT scan_date::text FROM scan_results
                        WHERE symbol = :s AND level IN ('L2','L3')
                        ORDER BY scan_date DESC LIMIT 30
                    """), {"s": sym})
                    signal_dates = [row[0] for row in r.fetchall()]
                if signal_dates:
                    report["resonance"] = await analyze_all_resonance(
                        sym, signal_dates, current_date
                    )
                else:
                    report["resonance"] = {"status": "insufficient",
                                           "summary": "无历史 TG 信号数据"}
            except Exception as e:
                logger.debug(f"Resonance analysis failed for {sym}: {e}")
                report["resonance"] = {"status": "unavailable", "reason": str(e)[:80]}

            # ★ 子复盘 7: 操盘手法反推 (v4.3) ──
            try:
                from app.services.micro_behavior_analyzer import find_trigger_conditions
                rise_triggers = await find_trigger_conditions(
                    sym, "fast_rise", 180, current_date
                )
                fall_triggers = await find_trigger_conditions(
                    sym, "fast_fall", 180, current_date
                )
                report["micro_behavior"] = {
                    "fast_rise": rise_triggers,
                    "fast_fall": fall_triggers,
                }
            except Exception as e:
                logger.debug(f"Micro behavior analysis failed for {sym}: {e}")
                report["micro_behavior"] = {"status": "unavailable",
                                            "reason": str(e)[:80]}

            # ── 生成总结 ──
            report["drill_summary"] = _generate_summary(report, current_price)

        except Exception as e:
            logger.warning(f"Drill failed for {sym}: {e}")
            report["status"] = "error"
            report["reason"] = str(e)[:120]

        elapsed = round(asyncio.get_event_loop().time() - start_time, 2)
        report["elapsed_s"] = elapsed
        logger.debug(f"Drill {sym}: {elapsed}s")

        results[sym] = report
        if len(_drill_cache) > 200:
            _drill_cache.pop(next(iter(_drill_cache)))
        _drill_cache[cache_key] = report

    return results


# ═══════════════════════════════════════════════════════════
# 子复盘 1: 信号有效性回溯
# ═══════════════════════════════════════════════════════════

def _compute_signal_features(closes, highs, lows, volumes) -> dict:
    """从日线数据提取简化的信号特征向量.

    用于匹配历史相似信号, 不是完整 TG 指标计算。
    """
    if len(closes) < 20:
        return {}
    n = len(closes)
    # RSI(14)
    deltas = np.diff(closes[-15:])
    gains = np.sum(deltas[deltas > 0]) if np.any(deltas > 0) else 0
    losses = -np.sum(deltas[deltas < 0]) if np.any(deltas < 0) else 1e-9
    rsi = float(100 - 100 / (1 + gains / max(losses, 1e-9)))
    # 量比: 最近5日 vs 前20日均量
    vol5 = float(np.mean(volumes[-5:])) if len(volumes) >= 5 else 0
    vol20 = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else 1
    vol_ratio = vol5 / max(vol20, 1)
    # 5日涨跌幅
    ret5 = float((closes[-1] / closes[-5] - 1) * 100) if len(closes) >= 5 else 0
    # 距20日高点%
    high20 = float(np.max(highs[-20:])) if len(highs) >= 20 else closes[-1]
    dist_high = float((closes[-1] / high20 - 1) * 100) if high20 > 0 else 0
    return {
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "ret5d": round(ret5, 2),
        "dist_high20d": round(dist_high, 2),
    }


async def _drill_signal_effectiveness(
    symbol: str, current_date: date, dates: list
) -> dict:
    """评估当前 TG 信号在此股历史上的真实胜率."""
    cutoff = current_date - timedelta(days=HISTORY_LOOKBACK_DAYS)

    async with async_session_factory() as s:
        # 查历史 scan_results + analysis_scores
        r = await s.execute(text("""
            SELECT sr.scan_date, sr.close_price, sr.tg_momentum,
                   a.composite_score, a.dimension_scores
            FROM scan_results sr
            LEFT JOIN analysis_scores a ON a.symbol = sr.symbol AND a.scan_date = sr.scan_date
            WHERE sr.symbol = :sym AND sr.scan_date >= :cut AND sr.scan_date < :today
              AND sr.level IN ('L2','L3')
            ORDER BY sr.scan_date DESC LIMIT 30
        """), {"sym": symbol, "cut": cutoff, "today": current_date})
        hist_signals = [(row[0], float(row[1] or 0), float(row[2] or 0),
                         float(row[3] or 0), row[4]) for row in r.fetchall()]

    if len(hist_signals) < 3:
        return {"status": "insufficient", "history_count": len(hist_signals),
                "message": f"历史信号不足 ({len(hist_signals)}条, 需≥3)"}

    # 对每个历史信号，计算 T+5 收益
    td_map = {d: i for i, d in enumerate(dates)}
    outcomes = []
    for scan_dt, entry_price, tg_mom, sc, dims in hist_signals:
        scan_str = str(scan_dt)
        if scan_str not in td_map:
            continue
        idx = td_map[scan_str]
        # T+5: 5个交易日后
        exit_idx = min(idx + 5, len(dates) - 1)
        if exit_idx <= idx:
            continue
        exit_dt = dates[exit_idx]

        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT close FROM daily_kline WHERE ts_code=:s AND trade_date=:d"
            ), {"s": symbol, "d": exit_dt})
            exit_row = r.fetchone()
        if not exit_row:
            continue

        exit_price = float(exit_row[0] or 0)
        if entry_price <= 0 or exit_price <= 0:
            continue

        ret = (exit_price - entry_price) / entry_price
        # 最大盈利: 查 min_kline 中信号后5天的最高点 (如有)
        max_gain = ret
        try:
            async with async_session_factory() as s:
                r = await s.execute(text("""
                    SELECT MAX(high) FROM min_kline
                    WHERE ts_code = :s AND trade_time::date BETWEEN :d1 AND :d2
                """), {"s": symbol, "d1": scan_dt, "d2": exit_dt})
                max_row = r.fetchone()
                if max_row and max_row[0] and entry_price > 0:
                    max_gain = (float(max_row[0]) - entry_price) / entry_price
        except Exception:
            pass

        outcomes.append({
            "scan_date": str(scan_dt),
            "entry": round(entry_price, 2),
            "exit": round(exit_price, 2),
            "return": round(ret * 100, 2),
            "max_gain": round(max_gain * 100, 2),
            "is_win": ret > 0,
        })

    if len(outcomes) < 3:
        return {"status": "insufficient", "history_count": len(outcomes),
                "message": f"有效历史信号不足 ({len(outcomes)}条有价格数据)"}

    win_rate = sum(1 for o in outcomes if o["is_win"]) / len(outcomes)
    avg_ret = np.mean([o["return"] for o in outcomes])
    avg_max = np.mean([o["max_gain"] for o in outcomes])
    max_drawdown = min(o["return"] for o in outcomes) if outcomes else 0

    # 盈亏比: 平均盈利 / |平均亏损|
    wins = [o["return"] for o in outcomes if o["is_win"]]
    losses = [abs(o["return"]) for o in outcomes if not o["is_win"]]
    profit_loss_ratio = round(np.mean(wins) / max(np.mean(losses), 0.01), 2) if wins and losses else 0

    return {
        "status": "success",
        "history_count": len(outcomes),
        "win_rate_5d": round(win_rate, 3),
        "avg_return_5d": round(avg_ret, 2),
        "avg_max_gain_5d": round(avg_max, 2),
        "max_drawdown_5d": round(max_drawdown, 2),
        "profit_loss_ratio": profit_loss_ratio,
        "recent_outcomes": outcomes[-5:],
        "verdict": (
            f"历史{len(outcomes)}次同类信号, T+5胜率{win_rate*100:.0f}%, "
            f"平均收益{avg_ret:+.1f}%, 最大盈利{avg_max:.1f}%, 盈亏比{profit_loss_ratio}"
            if outcomes else "数据不足"
        ),
    }


# ═══════════════════════════════════════════════════════════
# 子复盘 2: K线形态相似性匹配
# ═══════════════════════════════════════════════════════════

def _normalize_sequence(seq: np.ndarray) -> np.ndarray:
    """归一化: 除以第一个值使得所有序列在相同尺度下比较."""
    if seq[0] <= 0:
        return np.zeros_like(seq)
    return seq / seq[0]


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度."""
    dot = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return float(dot / (norm_a * norm_b))


async def _drill_pattern_matching(
    closes: np.ndarray, dates: list
) -> dict:
    """用最近10天K线形态匹配历史最相似窗口，预测后续走势."""
    n = len(closes)
    if n < PATTERN_WINDOW + 5:
        return {"status": "insufficient", "message": f"数据不足 ({n}天)"}

    # 当前窗口: 最近10天归一化序列
    current_seq = closes[-PATTERN_WINDOW:]
    current_norm = _normalize_sequence(current_seq)

    # 遍历历史所有10天窗口, 计算余弦相似度
    # 只回溯最近500天
    sim_scores = []
    max_start = n - PATTERN_WINDOW - 5  # 留出后续计算空间

    for start in range(max(0, max_start - 500), max_start):
        hist_seq = closes[start:start + PATTERN_WINDOW]
        if len(hist_seq) < PATTERN_WINDOW:
            continue
        hist_norm = _normalize_sequence(hist_seq)
        sim = _cosine_similarity(current_norm, hist_norm)
        if sim > 0.85:  # 只保留高相似度
            # 窗口后 T+5, T+10 的涨跌幅
            t5_idx = min(start + PATTERN_WINDOW + 5, n - 1)
            t10_idx = min(start + PATTERN_WINDOW + 10, n - 1)
            if t5_idx > start + PATTERN_WINDOW:
                ret5 = (closes[t5_idx] / closes[start + PATTERN_WINDOW - 1] - 1) * 100
                ret10 = (closes[t10_idx] / closes[start + PATTERN_WINDOW - 1] - 1) * 100
                sim_scores.append({
                    "start_date": str(dates[start]) if start < len(dates) else "?",
                    "end_date": str(dates[start + PATTERN_WINDOW - 1]) if start + PATTERN_WINDOW - 1 < len(dates) else "?",
                    "similarity": round(sim, 4),
                    "ret_5d": round(float(ret5), 2),
                    "ret_10d": round(float(ret10), 2),
                    "is_win_5d": ret5 > 0,
                })

    # 取 Top-5 相似
    sim_scores.sort(key=lambda x: x["similarity"], reverse=True)
    top5 = sim_scores[:TOP_SIMILAR_N]

    if len(top5) < 2:
        return {"status": "insufficient", "message": f"相似窗口不足 ({len(top5)}个, 余弦>0.85)",
                "top_similar": top5}

    avg_ret5 = np.mean([s["ret_5d"] for s in top5])
    avg_ret10 = np.mean([s["ret_10d"] for s in top5])
    win_rate5 = sum(1 for s in top5 if s["is_win_5d"]) / len(top5)

    return {
        "status": "success",
        "top_similar_segments": top5,
        "predicted_avg_return_5d": round(float(avg_ret5), 2),
        "predicted_avg_return_10d": round(float(avg_ret10), 2),
        "predicted_win_rate_5d": round(win_rate5, 2),
        "verdict": (
            f"Top-{len(top5)}相似形态后T+5均收{avg_ret5:+.1f}%, "
            f"胜率{win_rate5*100:.0f}%"
            if top5 else "无高相似历史形态"
        ),
    }


# ═══════════════════════════════════════════════════════════
# 子复盘 3: 关键位置博弈
# ═══════════════════════════════════════════════════════════

def _calc_ma(closes: np.ndarray, period: int) -> float:
    if len(closes) < period:
        return float(np.mean(closes))
    return float(np.mean(closes[-period:]))

async def _drill_critical_position(
    symbol: str, current_price: float,
    closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, dates: list
) -> dict:
    """判断当前是否处于关键位置，并回溯历史突破成功率."""
    positions = []
    n = len(closes)

    # MA60/120/250
    for ma_name, ma_period in [("MA60", 60), ("MA120", 120), ("MA250", 250)]:
        if n < ma_period:
            continue
        ma_val = _calc_ma(closes, ma_period)
        dist_pct = (current_price - ma_val) / ma_val * 100 if ma_val > 0 else 0
        if abs(dist_pct) < 2.0:
            # 在均线附近 ±2% — 回溯触碰此均线的历史表现
            action = "support" if current_price > ma_val else "resistance"
            positions.append({
                "type": ma_name,
                "value": round(ma_val, 2),
                "distance_pct": round(dist_pct, 2),
                "action": action,
            })

    # 前高/前低
    high_250 = float(np.max(highs[-250:])) if n >= 250 else float(np.max(highs))
    low_250 = float(np.min(lows[-250:])) if n >= 250 else float(np.min(lows))
    dist_high = (current_price - high_250) / high_250 * 100 if high_250 > 0 else 0
    dist_low = (current_price - low_250) / low_250 * 100 if low_250 > 0 else 0

    if abs(dist_high) < 3.0:
        positions.append({
            "type": "前高(250日)",
            "value": round(high_250, 2),
            "distance_pct": round(dist_high, 2),
            "action": "resistance",
        })
    if abs(dist_low) < 3.0:
        positions.append({
            "type": "前低(250日)",
            "value": round(low_250, 2),
            "distance_pct": round(dist_low, 2),
            "action": "support",
        })

    if not positions:
        return {"status": "no_position", "positions": [],
                "message": "当前价格不在任何关键位置 (±2%均线, ±3%前高前低)"}

    # 对每个关键位置, 简化为触碰后 T+5 表现
    for pos in positions:
        # 查找历史上价格接近此位置的所有日子
        check_price = pos["value"]
        close_indices = []
        for i in range(n - 10):
            if abs(closes[i] - check_price) / check_price < 0.02:
                if i + 5 < n:
                    ret = (closes[i + 5] / closes[i] - 1) * 100
                    close_indices.append(round(float(ret), 2))
        if len(close_indices) >= 3:
            pos["history_count"] = len(close_indices)
            pos["history_breakout_rate"] = round(
                sum(1 for r in close_indices if r > 0) / len(close_indices), 3
            )
            pos["avg_bounce"] = round(float(np.mean(close_indices)), 2)
        else:
            pos["history_count"] = len(close_indices)
            pos["uncertain"] = True

    return {
        "status": "success",
        "positions": positions,
        "verdict": "; ".join(
            f"{p['type']}({p['action']}): 历史触碰{p.get('history_count',0)}次, "
            f"突破率{p.get('history_breakout_rate',0)*100:.0f}%"
            for p in positions if not p.get("uncertain")
        ),
    }


# ═══════════════════════════════════════════════════════════
# 子复盘 4: 筹码吸收模拟
# ═══════════════════════════════════════════════════════════

async def _drill_chip_simulation(
    symbol: str, closes: np.ndarray, highs: np.ndarray,
    lows: np.ndarray, volumes: np.ndarray, dates: list
) -> dict:
    """利用5分钟数据模拟锁死期间的筹码吸收过程."""
    # 检测当前是否在锁死 — 使用简单振幅检测
    n = len(closes)
    if n < 30:
        return {"status": "insufficient", "message": "数据不足"}

    # 过去30天振幅
    h30 = float(np.max(highs[-30:]))
    l30 = float(np.min(lows[-30:]))
    amp30 = (h30 - l30) / l30 * 100 if l30 > 0 else 100

    if amp30 > 18:
        return {"status": "not_in_lock", "message": f"当前不在锁死状态 (30日振幅{amp30:.1f}%>18%)"}

    # 确定锁死区间
    lock_low = l30
    lock_high = h30
    lock_start_date = dates[-30] if len(dates) >= 30 else dates[0]

    # 尝试从 min_kline 获取5分钟数据
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_time, high, low, volume
            FROM min_kline
            WHERE ts_code = :s AND trade_time >= :start
            ORDER BY trade_time
        """), {"s": symbol, "start": lock_start_date})
        bars = r.fetchall()

    if len(bars) < 500:
        return {"status": "insufficient", "message": f"5分钟数据不足 ({len(bars)}根, 需≥500)",
                "bar_count": len(bars)}

    # 按日分组 → 逐日计算吸收率
    by_date: dict[str, dict] = defaultdict(lambda: {"vol_lock": 0.0, "vol_over": 0.0, "vol_below": 0.0})

    for bar in bars:
        trade_time = bar[0]
        bar_high = float(bar[1] or 0)
        bar_low = float(bar[2] or 0)
        vol = float(bar[3] or 0)
        mid = (bar_high + bar_low) / 2
        day = str(trade_time.date()) if hasattr(trade_time, 'date') else str(trade_time)[:10]

        if mid >= lock_low and mid <= lock_high:
            by_date[day]["vol_lock"] += vol
        elif mid > lock_high:
            by_date[day]["vol_over"] += vol
        else:
            by_date[day]["vol_below"] += vol

    # 构建吸收率曲线
    sorted_days = sorted(by_date.keys())
    absorption_curve = []
    rolling_ar: list[float] = []

    for i, day in enumerate(sorted_days):
        d = by_date[day]
        total = d["vol_lock"] + d["vol_over"] + d["vol_below"]
        if total > 0:
            ar = d["vol_lock"] / total
        else:
            ar = 0.0
        rolling_ar.append(ar)

        # 每 CHIP_SEGMENT_DAYS 聚合一段
        if (i + 1) % CHIP_SEGMENT_DAYS == 0 or i == len(sorted_days) - 1:
            seg_start = max(0, i - CHIP_SEGMENT_DAYS + 1)
            seg_ars = rolling_ar[seg_start:i + 1]
            absorption_curve.append({
                "date": day,
                "ar_lock": round(float(np.mean(seg_ars)) if seg_ars else ar, 3),
                "days_segment": len(seg_ars),
            })

    # 趋势判定
    trend = "stable"
    if len(absorption_curve) >= 3:
        recent = [s["ar_lock"] for s in absorption_curve[-3:]]
        early = [s["ar_lock"] for s in absorption_curve[:3]]
        recent_avg = float(np.mean(recent)) if recent else 0
        early_avg = float(np.mean(early)) if early else 0
        if recent_avg > early_avg * 1.2:
            trend = "accelerating"
        elif recent_avg > early_avg * 1.05:
            trend = "slowly_improving"
        elif recent_avg < early_avg * 0.8:
            trend = "declining"
        elif recent_avg < early_avg * 0.95:
            trend = "stagnating"

    current_ar = absorption_curve[-1]["ar_lock"] if absorption_curve else 0.0

    return {
        "status": "success",
        "lock_zone": {"low": round(lock_low, 2), "high": round(lock_high, 2)},
        "lock_start": str(lock_start_date),
        "lock_days": len(sorted_days),
        "current_ar": round(current_ar, 3),
        "absorption_curve": absorption_curve,
        "trend": trend,
        "verdict": (
            f"锁死{len(sorted_days)}天, 当前吸收率{current_ar*100:.0f}%, "
            f"趋势{trend}"
        ),
    }


# ═══════════════════════════════════════════════════════════
# 子复盘 5: 市场敏感性
# ═══════════════════════════════════════════════════════════

async def _drill_market_sensitivity(symbol: str) -> dict:
    """统计此股在不同市场体制下的表现."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ms.phase, AVG(dk.close - dk.open) / NULLIF(AVG(dk.open), 0) * 100,
                   COUNT(*)
            FROM daily_kline dk
            JOIN market_status_log ms ON ms.trade_date = dk.trade_date
            WHERE dk.ts_code = :s AND dk.trade_date >= CURRENT_DATE - 500
            GROUP BY ms.phase
        """), {"s": symbol})
        rows = r.fetchall()

    if not rows:
        return {"status": "insufficient", "message": "无 market_status_log 数据"}

    regime_returns = {}
    total_days = 0
    for row in rows:
        phase_raw = (row[0] or "").lower()
        if "牛" in phase_raw or "bull" in phase_raw:
            regime = "bull"
        elif "熊" in phase_raw or "bear" in phase_raw:
            regime = "bear"
        else:
            regime = "range"
        avg_ret = float(row[1] or 0)
        days = int(row[2] or 0)
        regime_returns[regime] = {"avg_return": round(avg_ret, 3), "days": days}
        total_days += days

    # 独立性得分: 各 regime 收益的标准差越小 → 越不依赖市场
    rets = [v["avg_return"] for v in regime_returns.values()]
    independence = round(1.0 - min(1.0, np.std(rets) / max(abs(np.mean(rets)), 0.01) if rets else 0), 2)

    return {
        "status": "success",
        "regime_returns": regime_returns,
        "independence_score": independence,
        "total_days": total_days,
        "verdict": (
            f"牛{regime_returns.get('bull',{}).get('avg_return',0):+.2f}% / "
            f"熊{regime_returns.get('bear',{}).get('avg_return',0):+.2f}% / "
            f"震{regime_returns.get('range',{}).get('avg_return',0):+.2f}%, "
            f"独立性{independence:.0%}"
        ),
    }


# ═══════════════════════════════════════════════════════════
# 生成总结
# ═══════════════════════════════════════════════════════════

def _generate_summary(report: dict, current_price: float) -> str:
    """从四个子复盘报告中提取一句综合总结."""
    parts = []

    # 信号有效性
    se = report.get("signal_effectiveness", {})
    if se.get("status") == "success":
        wr = se.get("win_rate_5d", 0)
        if wr >= 0.6:
            parts.append(f"历史信号胜率{wr*100:.0f}%✅")
        elif wr >= 0.4:
            parts.append(f"历史信号胜率{wr*100:.0f}%⚠")
        elif wr > 0:
            parts.append(f"历史信号胜率{wr*100:.0f}%❌")

    # 筹码趋势
    cs = report.get("chip_simulation", {})
    if cs.get("status") == "success":
        trend = cs.get("trend", "")
        ar = cs.get("current_ar", 0)
        if trend == "accelerating":
            parts.append(f"筹码加速吸收(AR {ar*100:.0f}%)🔥")
        elif trend == "slowly_improving":
            parts.append(f"筹码缓慢吸收(AR {ar*100:.0f}%)📈")
        elif trend == "stagnating":
            parts.append(f"筹码吸收停滞(AR {ar*100:.0f}%)⏸")

    # 关键位置
    cp = report.get("critical_position", {})
    if cp.get("positions"):
        pos_texts = []
        for p in cp["positions"]:
            if p.get("uncertain"):
                continue
            br = p.get("history_breakout_rate")
            if br is not None:
                pos_texts.append(f"{p['type']}突破率{br*100:.0f}%")
        if pos_texts:
            parts.append(", ".join(pos_texts[:2]))

    # 形态匹配
    pm = report.get("pattern_matching", {})
    if pm.get("status") == "success":
        pred = pm.get("predicted_avg_return_5d", 0)
        parts.append(f"形态预测T+5: {pred:+.1f}%")

    # 敏感性
    ms = report.get("market_sensitivity", {})
    if ms.get("status") == "success":
        ind = ms.get("independence_score", 0)
        if ind > 0.7:
            parts.append("独立于大盘🔄")

    if not parts:
        return f"{current_price:.2f} — data accumulating"

    return f"{current_price:.2f} | " + " | ".join(parts)
