"""老兵浪幅预测 + 上升途中分时吸收追踪.

从老兵历史锁周期中提取每轮主升浪的涨幅, 预测本次的目标区间。
在上升过程中, 每日用5分钟线追踪"抛售量 vs 承接量", 判断是否提前结束。
"""
import asyncio, logging, numpy as np
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("wave_predictor")


async def predict_wave_target(ts_code: str, lock_cycles: list[dict],
                               current_price: float,
                               all_daily_closes: np.ndarray = None,
                               all_daily_highs: np.ndarray = None) -> dict:
    """预测本次主升浪的目标区间。

    Args:
        ts_code: 股票代码
        lock_cycles: 锁周期列表 [{start: idx, end: idx, days: int}, ...]
        current_price: 当前价
        all_daily_closes, all_daily_highs: 已截断除权的日线数据 (可选)
    """
    if len(lock_cycles) < 2:
        return {"error": "锁周期不足, 无法预测"}

    # ★ 幸存者偏差防护: 检查是否为退市股 (退市股的历史浪幅会污染统计)
    try:
        async with async_session_factory() as s:
            r = await s.execute(text(
                "SELECT 1 FROM delisted_stocks WHERE ts_code = :c LIMIT 1"
            ), {"c": ts_code})
            if r.fetchone():
                logger.warning(f"Wave predictor [{ts_code}]: excluded — delisted stock")
                return {"error": "退市股, 历史浪幅不可用于预测"}
    except Exception:
        pass  # delisted_stocks 表可能不存在

    # 如果没有传入数据, 从 DB 加载 (+除权截断)
    already_cleaned = all_daily_closes is not None
    if not already_cleaned:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT close, high FROM daily_kline
                WHERE ts_code = :c ORDER BY trade_date
            """), {"c": ts_code})
            rows_data = r.fetchall()
            all_daily_closes = np.array([float(row[0] or 0) for row in rows_data])
            all_daily_highs = np.array([float(row[1] or all_daily_closes[i])
                                         for i, row in enumerate(rows_data)])

    if len(all_daily_closes) < 60:
        return {"error": "日线数据不足"}

    # 对每段历史锁死, 找开锁后的最高点
    wave_pcts = []
    wave_details = []

    for i, seg in enumerate(lock_cycles[:-1]):
        seg_end = seg["end"]
        if seg_end + 5 >= len(all_daily_closes):
            continue

        lock_avg = float(np.mean(all_daily_closes[seg["start"]:seg["end"]+1]))

        look_ahead = min(seg_end + 60, len(all_daily_closes))
        post_highs = all_daily_highs[seg_end+1:look_ahead+1]
        if len(post_highs) < 10:
            continue

        peak_price = float(np.max(post_highs))
        peak_idx = int(np.argmax(post_highs))
        peak_day = seg_end + 1 + peak_idx

        wave_pct = (peak_price - lock_avg) / max(lock_avg, 0.01) * 100

        if wave_pct > 0:
            wave_pcts.append(wave_pct)
            wave_details.append({
                "cycle_n": i + 1,
                "lock_days": seg["days"],
                "lock_avg": round(lock_avg, 1),
                "peak_price": round(peak_price, 1),
                "peak_day_offset": peak_day - seg_end,
                "wave_pct": round(wave_pct, 1),
            })

    if len(wave_pcts) < 2:
        return {"error": "有效浪幅不足", "waves_found": len(wave_pcts)}

    # ★ 极端值过滤: 剔除 >3σ 的异常浪幅 (ST异动/重组等噪音)
    wave_pcts_arr = np.array(wave_pcts)
    if len(wave_pcts_arr) >= 5:
        pct_mean = float(np.mean(wave_pcts_arr))
        pct_std = float(np.std(wave_pcts_arr))
        pct_upper = pct_mean + 3.0 * pct_std
        pct_lower = max(0, pct_mean - 3.0 * pct_std)
        filtered = [p for p in wave_pcts if pct_lower <= p <= pct_upper]
        n_filtered = len(wave_pcts) - len(filtered)
        if n_filtered > 0 and len(filtered) >= 2:
            logger.info(f"Wave predictor [{ts_code}]: filtered {n_filtered} outliers "
                       f"(range {pct_lower:.1f}%-{pct_upper:.1f}%)")
            wave_pcts = filtered
            wave_details = [wd for wd, wp in zip(wave_details, wave_pcts_arr)
                          if pct_lower <= wp <= pct_upper]

    if len(wave_pcts) < 2:
        return {"error": "有效浪幅不足(过滤后)", "waves_found": len(wave_pcts)}

    wave_pcts_sorted = sorted(wave_pcts)

    avg_wave = round(float(np.mean(wave_pcts)), 1)
    median_wave = round(float(np.median(wave_pcts)), 1)
    min_wave = round(float(wave_pcts_sorted[0]), 1)
    max_wave = round(float(wave_pcts_sorted[-1]), 1)

    # 最近 3 轮的涨幅
    recent_3 = wave_pcts[-3:] if len(wave_pcts) >= 3 else wave_pcts

    # 目标区间
    target_low = round(current_price * (1 + min_wave / 100), 2)
    target_mid = round(current_price * (1 + avg_wave / 100), 2)
    target_high = round(current_price * (1 + max_wave / 100), 2)

    # 谨慎区间: 涨到均值的 80% 时进入谨慎
    caution_pct = round(avg_wave * 0.80, 1)
    caution_price = round(current_price * (1 + caution_pct / 100), 2)

    # 危险区间: 涨到均值的 100% 时进入危险
    danger_pct = avg_wave
    danger_price = round(current_price * (1 + danger_pct / 100), 2)

    # 止盈线: 涨到均值的 120% 或历史最大 90%
    stop_pct = min(avg_wave * 1.2, max_wave * 0.9)
    stop_price = round(current_price * (1 + stop_pct / 100), 2)

    # 评估当前安全边际
    if current_price > 0:
        room_to_avg = round((target_mid - current_price) / current_price * 100, 1)
    else:
        room_to_avg = 0

    return {
        "cycles_analyzed": len(wave_pcts),
        "avg_wave_pct": avg_wave,
        "median_wave_pct": median_wave,
        "min_wave_pct": min_wave,
        "max_wave_pct": max_wave,
        "recent_wave_pcts": [round(p, 1) for p in recent_3],
        "wave_details": wave_details[-5:],
        "target_zone": {
            "conservative": target_low,
            "expected": target_mid,
            "aggressive": target_high,
        },
        "risk_levels": {
            "caution": {"pct": caution_pct, "price": caution_price},
            "danger": {"pct": danger_pct, "price": danger_price},
            "stop": {"pct": round(stop_pct, 1), "price": stop_price},
        },
        "room_to_avg_pct": room_to_avg,
        "summary": (
            f"历史{len(wave_pcts)}轮: 均值+{avg_wave:.0f}% 中位+{median_wave:.0f}% "
            f"范围{min_wave:.0f}%~{max_wave:.0f}% | "
            f"目标{target_mid:.2f} | "
            f"谨慎{caution_price:.2f}(+{caution_pct:.0f}%) "
            f"危险{danger_price:.2f}(+{danger_pct:.0f}%) "
            f"止盈{stop_price:.2f}(+{stop_pct:.0f}%)"
        ),
    }


async def detect_distribution(ts_code: str, entry_price: float,
                               lock_top: float = None) -> dict:
    """上升途中检测抛售/出货迹象。

    触发时机: 已建仓, 在上升途中每天跑一次。

    核心原理:
      把日内 5 分钟线分三个区:
        Z_CURRENT: 当前价上下 2%  (主流竞价区)
        Z_UPPER:   上探区 (> 当前+2%) (追高区)
        Z_LOWER:   下探区 (< 当前-2%) (踩踏区)

      如果 Z_UPPER 的成交量在上升, 且 Z_UPPER 多为卖单 (close < open),
      说明每次拉高都有人在抛 → 出货迹象。

    Returns:
        {distribution_score, verdict, upper_vol_pct, upper_sell_pct, ...}
    """
    from app.services.minute_data import fetch_5min_bars

    bars = await fetch_5min_bars(ts_code, lookback_days=5)
    if not bars or len(bars) < 100:
        return {"error": "分钟数据不足"}

    # 取最近 5 天的数据
    recent_bars = [b for b in bars
                   if b.get("time", b.get("trade_time", ""))[:10]
                   >= (date.today() - timedelta(days=5)).strftime('%Y-%m-%d')]

    if len(recent_bars) < 50:
        return {"error": "近期数据不足"}

    # 按天分组
    by_day = defaultdict(list)
    for b in recent_bars:
        day = b.get("time", b.get("trade_time", ""))[:10]
        by_day[day].append(b)

    daily_stats = []
    total_upper_vol = 0.0
    total_upper_sell_vol = 0.0
    total_current_vol = 0.0

    for day in sorted(by_day.keys()):
        day_bars = by_day[day]
        if len(day_bars) < 30:
            continue

        opens = np.array([b["open"] for b in day_bars])
        closes = np.array([b["close"] for b in day_bars])
        vols = np.array([b["vol"] for b in day_bars])

        day_open = float(opens[0])
        day_vwap = float(np.sum((opens + closes) / 2 * vols) / max(np.sum(vols), 1))

        # 日内区间划分
        upper_threshold = day_vwap * 1.02
        lower_threshold = day_vwap * 0.98

        upper_vol = 0.0
        upper_sell = 0.0
        current_vol = 0.0

        for j in range(len(day_bars)):
            mid = (opens[j] + closes[j]) / 2
            vol = float(vols[j])

            if mid > upper_threshold:
                upper_vol += vol
                if closes[j] < opens[j]:
                    upper_sell += vol
            elif mid >= lower_threshold:
                current_vol += vol

        total_upper_vol += upper_vol
        total_upper_sell_vol += upper_sell
        total_current_vol += current_vol

        daily_stats.append({
            "date": day,
            "upper_vol_pct": round(upper_vol / max(upper_vol + current_vol, 1) * 100, 1),
            "upper_sell_pct": round(upper_sell / max(upper_vol, 1) * 100, 1),
        })

    if total_upper_vol + total_current_vol <= 0:
        return {"error": "无有效成交"}

    # 核心指标
    upper_vol_ratio = total_upper_vol / (total_upper_vol + total_current_vol)
    upper_sell_ratio = total_upper_sell_vol / max(total_upper_vol, 1)

    # 趋势: 最近 2 天 vs 前两天
    if len(daily_stats) >= 4:
        recent_2_upper = float(np.mean([
            s["upper_sell_pct"] for s in daily_stats[-2:]]))
        early_2_upper = float(np.mean([
            s["upper_sell_pct"] for s in daily_stats[:2]]))
        sell_trend = "加速抛售" if recent_2_upper > early_2_upper * 1.3 else (
            "抛售减弱" if recent_2_upper < early_2_upper * 0.7 else "抛售平稳")
    else:
        sell_trend = "数据不足"

    # 判定
    if upper_sell_ratio >= 0.60 and upper_vol_ratio >= 0.20:
        # 上探区 60%+ 是卖单, 且上探区占总量的 20%+
        score = 80
        verdict = "明确出货 — 拉高减仓特征明显, 本轮浪可能提前结束"
    elif upper_sell_ratio >= 0.50 and upper_vol_ratio >= 0.15:
        score = 60
        verdict = "出货嫌疑 — 上探区卖单偏多"
    elif upper_sell_ratio >= 0.40:
        score = 40
        verdict = "偏中性 — 有一定抛压但不够强"
    elif upper_vol_ratio < 0.08:
        score = 10
        verdict = "健康上涨 — 成交量集中在主流竞价区, 没有到上方去派发"
    else:
        score = 20
        verdict = "正常 — 无明显出货迹象"

    return {
        "distribution_score": score,
        "verdict": verdict,
        "upper_vol_ratio": round(upper_vol_ratio * 100, 1),
        "upper_sell_ratio": round(upper_sell_ratio * 100, 1),
        "sell_trend": sell_trend,
        "daily_stats": daily_stats,
        "entry_price": round(entry_price, 2),
        "days_tracked": len(daily_stats),
    }


async def full_wave_analysis(ts_code: str, lock_cycles: list[dict] = None,
                              current_price: float = None) -> dict:
    """完整浪分析: 浪幅预测 + 吸收率 + 抛售检测 (一键)."""
    from app.services.chip_analyzer import analyze_chip_absorption

    # 日线
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT close, high, low FROM daily_kline
            WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 120
        """), {"c": ts_code})
        rows = list(reversed(r.fetchall()))

    if len(rows) < 30:
        return {"error": "数据不足"}

    cs = np.array([float(r[0] or 0) for r in rows])
    hs = np.array([float(r[1] or cs[i]) for i in range(len(rows))])
    ls = np.array([float(r[2] or cs[i]) for i in range(len(rows))])

    if current_price is None:
        current_price = float(cs[-1])

    # 锁死区间
    h30 = float(np.max(hs[-30:]))
    l30 = float(np.min(ls[-30:]))

    # 如果没传锁周期, 从 veteran 获取
    if lock_cycles is None:
        from app.services.alphaflow_veteran import detect_veteran
        vet = await detect_veteran(ts_code)
        if vet:
            lock_cycles = [{"start": 0, "end": 0, "days": vet["current_days"]}]
        else:
            lock_cycles = []

    # 1. 浪幅预测
    wave = await predict_wave_target(ts_code, lock_cycles, current_price)

    # 2. 吸收率
    absorption = await analyze_chip_absorption(ts_code, l30, h30)

    # 3. 抛售检测 (上升中)
    if current_price > l30 * 1.03 and "error" not in wave:
        distribution = await detect_distribution(
            ts_code, current_price, h30)
    else:
        distribution = {"verdict": "未进入上升阶段, 不检测抛售"}

    return {
        "current_price": round(current_price, 2),
        "lock_range": f"{l30:.2f}-{h30:.2f}",
        "wave_prediction": wave,
        "absorption": absorption.get("absorption", {}),
        "distribution": distribution,
    }
