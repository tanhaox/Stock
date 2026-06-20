"""5分钟线操盘手法反推引擎 (v4.3).

基于 5 分钟 K 线数据自动识别操盘动作（快速拉升、砸盘、托单、尾盘偷袭、开盘冲锋），
并反向统计这些动作发生前共同的技术特征，找出交易员盯盘的触发条件。

核心函数:
  detect_actions()          — 从 5 分钟 DataFrame 检测 5 类操盘动作
  compute_suspect_indicators() — 在指定 K 线位置计算 10 个嫌疑指标
  find_trigger_conditions() — 统计触发条件 vs 随机对照组的提升度
  scan_active_signals()     — 扫描当前持仓/推荐股，返回活跃触发条件
"""
import asyncio
import logging
import numpy as np
from collections import defaultdict
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("micro_behavior")

# ── 动作检测阈值 ──
ACTION_RISE_PCT = 1.5         # 快速拉升: 单根涨幅 > 1.5%
ACTION_DROP_PCT = 1.5         # 快速砸盘: 单根跌幅 > 1.5%
ACTION_VOL_MULT = 2.0         # 量 > 前20根均量的倍数
SUPPORT_AMP_MAX = 0.3         # 托单横盘: 振幅 < 0.3%
SUPPORT_CONSECUTIVE = 6       # 托单横盘: 连续 >= 6 根
TAIL_ATTACK_PCT = 1.0         # 尾盘偷袭: 最后30分钟涨跌 > 1%
TAIL_ATTACK_COUNT = 3         # 同向 K 线 >= 3 根
OPEN_CHARGE_PCT = 1.0         # 开盘冲锋: 单根涨幅 > 1%
OPEN_CHARGE_VOL = 3.0         # 开盘冲锋: 量 > 均量 3 倍
LOOKBACK_MIN_BARS = 180       # 最少需要 180 根 5 分钟线
MIN_ACTIONS_FOR_TRIGGER = 20  # 触发条件发现至少需要 20 次动作
LIFT_THRESHOLD = 3.0          # 提升度阈值
HIT_RATE_THRESHOLD = 0.50     # 触发率阈值


def detect_actions(kline_5min: "np.ndarray | list[dict]") -> list[dict]:
    """从 5 分钟 K 线中检测操盘动作。

    输入: kline_5min — 按时间排序的 5 分钟 K 线列表或结构数组。
            每根: {time, open, high, low, close, volume} 或 ndarray [time,o,h,l,c,vol]
    输出: [{timestamp, type, price_before, volume_before, details}, ...]
    """
    if isinstance(kline_5min, list):
        n = len(kline_5min)
        closes = np.array([b["close"] for b in kline_5min])
        highs = np.array([b["high"] for b in kline_5min])
        lows = np.array([b["low"] for b in kline_5min])
        opens = np.array([b["open"] for b in kline_5min])
        vols = np.array([b.get("vol", b.get("volume", 0)) for b in kline_5min])
        times = [b.get("time", b.get("trade_time", "")) for b in kline_5min]
    else:
        n = len(kline_5min)
        closes = np.array([float(b[4]) for b in kline_5min])
        highs = np.array([float(b[3]) for b in kline_5min])
        lows = np.array([float(b[2]) for b in kline_5min])
        opens = np.array([float(b[1]) for b in kline_5min])
        vols = np.array([float(b[5]) for b in kline_5min])
        times = [str(b[0]) for b in kline_5min]

    if n < 30:
        return []

    # 前 20 根滑动均量
    rolling_vol = np.convolve(vols, np.ones(20)/20, mode='same')
    rolling_vol[:20] = np.mean(vols[:20])

    actions = []

    for i in range(20, n - 1):
        bar_close = closes[i]
        bar_open = opens[i]
        bar_high = highs[i]
        bar_low = lows[i]
        bar_vol = vols[i]
        bar_time = times[i] if i < len(times) else ""
        avg_vol = rolling_vol[i] if i < len(rolling_vol) else 1

        if bar_open <= 0 or bar_close <= 0:
            continue

        pct = (bar_close - bar_open) / bar_open * 100

        # 获取动作前第 3 根 K 线的数据
        ref_idx = max(0, i - 3)
        price_before = float(closes[ref_idx]) if ref_idx >= 0 else float(bar_open)
        vol_before = float(vols[ref_idx]) if ref_idx >= 0 else float(bar_vol)

        # ── 快速拉升 ──
        if pct >= ACTION_RISE_PCT and bar_vol > avg_vol * ACTION_VOL_MULT:
            actions.append({
                "timestamp": bar_time,
                "type": "fast_rise",
                "index": i,
                "price_before": round(price_before, 2),
                "volume_before": round(vol_before, 0),
                "pct": round(pct, 2),
                "vol_ratio": round(bar_vol / max(avg_vol, 1), 2),
            })

        # ── 快速砸盘 ──
        elif pct <= -ACTION_DROP_PCT and bar_vol > avg_vol * ACTION_VOL_MULT:
            actions.append({
                "timestamp": bar_time,
                "type": "fast_fall",
                "index": i,
                "price_before": round(price_before, 2),
                "volume_before": round(vol_before, 0),
                "pct": round(pct, 2),
                "vol_ratio": round(bar_vol / max(avg_vol, 1), 2),
            })

    # ── 托单横盘: 连续 >= SUPPORT_CONSECUTIVE 根，振幅 < SUPPORT_AMP_MAX ──
    streak_start = 0
    for i in range(1, n):
        amp = (highs[i] - lows[i]) / max(opens[i], 0.01) * 100
        if amp < SUPPORT_AMP_MAX and vols[i] > rolling_vol[i] * 1.5:
            if streak_start == 0:
                streak_start = i
        else:
            streak_len = i - streak_start
            if streak_len >= SUPPORT_CONSECUTIVE and streak_start > 3:
                actions.append({
                    "timestamp": times[streak_start] if streak_start < len(times) else "",
                    "type": "support_sideways",
                    "index": streak_start,
                    "price_before": round(float(closes[max(0, streak_start - 3)]), 2),
                    "volume_before": round(float(vols[max(0, streak_start - 3)]), 0),
                    "pct": round((closes[i-1] - closes[streak_start]) / max(closes[streak_start], 0.01) * 100, 2),
                    "vol_ratio": round(float(np.mean(vols[streak_start:i]) / max(np.mean(rolling_vol[streak_start:i]), 1)), 2),
                    "bars": streak_len,
                })
            streak_start = 0

    # ── 尾盘偷袭: 最后 30 分钟 (最后 6 根) ──
    if n >= 6:
        tail_seg = list(range(max(0, n - 6), n))
        tail_closes = closes[tail_seg]
        tail_opens = opens[tail_seg]
        tail_ret = (tail_closes[-1] - tail_opens[0]) / max(tail_opens[0], 0.01) * 100
        if abs(tail_ret) >= TAIL_ATTACK_PCT:
            # 同向计数
            same_dir = 0
            for j in tail_seg:
                if (tail_ret > 0 and closes[j] > opens[j]) or (tail_ret < 0 and closes[j] < opens[j]):
                    same_dir += 1
            if same_dir >= TAIL_ATTACK_COUNT:
                actions.append({
                    "timestamp": times[tail_seg[0]] if tail_seg[0] < len(times) else "",
                    "type": "tail_attack",
                    "index": tail_seg[0],
                    "price_before": round(float(closes[max(0, tail_seg[0] - 3)]), 2),
                    "volume_before": round(float(vols[max(0, tail_seg[0] - 3)]), 0),
                    "pct": round(tail_ret, 2),
                    "vol_ratio": round(float(np.mean(vols[tail_seg]) / max(np.mean(rolling_vol[tail_seg]), 1)), 2),
                    "dir": "up" if tail_ret > 0 else "down",
                })

    # ── 开盘冲锋: 前 12 根 (09:30-10:30) 中单根涨幅 > 1% ──
    for i in range(min(12, n)):
        bar_pct = (closes[i] - opens[i]) / max(opens[i], 0.01) * 100
        if bar_pct >= OPEN_CHARGE_PCT and vols[i] > rolling_vol[i] * OPEN_CHARGE_VOL:
            actions.append({
                "timestamp": times[i] if i < len(times) else "",
                "type": "open_charge",
                "index": i,
                "price_before": float(closes[0]),
                "volume_before": float(vols[0]),
                "pct": round(bar_pct, 2),
                "vol_ratio": round(vols[i] / max(rolling_vol[i], 1), 2),
            })
            break  # 只取最早的一次

    return actions


def compute_suspect_indicators(kline_5min: "np.ndarray | list[dict]",
                                action_index: int) -> dict | None:
    """在动作前的第 3 根 K 线处，计算 10 个技术嫌疑指标。

    Args:
        kline_5min: 5分钟K线列表或 ndarray
        action_index: 动作发生的K线索引

    Returns:
        {vwap_dist, ma20_dist, prev_close_dist, today_open_dist,
         intraday_position, bband_pctile, vol_ratio_vs_5d,
         is_integer_level, ma_cross_proximity, upper_shadow_ratio}
        或 None (数据不足)
    """
    ref_idx = max(0, action_index - 3)
    if ref_idx < 10:
        return None

    if isinstance(kline_5min, list):
        bars = kline_5min
        closes = np.array([b["close"] for b in bars])
        highs = np.array([b["high"] for b in bars])
        lows = np.array([b["low"] for b in bars])
        opens = np.array([b["open"] for b in bars])
        vols = np.array([b.get("vol", b.get("volume", 0)) for b in bars])
    else:
        bars = kline_5min
        closes = np.array([float(b[4]) for b in bars])
        highs = np.array([float(b[3]) for b in bars])
        lows = np.array([float(b[2]) for b in bars])
        opens = np.array([float(b[1]) for b in bars])
        vols = np.array([float(b[5]) for b in bars])

    n = len(closes)
    if ref_idx >= n:
        return None

    price = float(closes[ref_idx])
    vol = float(vols[ref_idx])
    high = float(highs[ref_idx])
    low = float(lows[ref_idx])

    # 1. VWAP 距离 (从今日开盘到 ref_idx)
    today_start = ref_idx
    while today_start > 0:
        t = today_start - 1
        # 判断是否新交易日：时间戳包含新的日期 (简化：查前一根K线时间)
        today_start = t
        break
    today_start = 0  # 简化：用前 ref_idx 根
    vwap_window = closes[:ref_idx + 1]
    vwap_vols = vols[:ref_idx + 1]
    vwap = float(np.sum(vwap_window * vwap_vols) / max(np.sum(vwap_vols), 1))
    vwap_dist = (price - vwap) / max(vwap, 0.01) * 100

    # 2. MA20 距离
    ma20 = float(np.mean(closes[max(0, ref_idx - 20):ref_idx + 1]))
    ma20_dist = (price - ma20) / max(ma20, 0.01) * 100

    # 3. 前收盘价距离 (今天第一根K线的前一根)
    prev_close = float(closes[max(0, ref_idx - 1)])
    prev_close_dist = (price - prev_close) / max(prev_close, 0.01) * 100

    # 4. 今日开盘价距离 (简化为前 20 根K线中的第一根)
    today_open = float(opens[0]) if len(opens) > 0 else price
    today_open_dist = (price - today_open) / max(today_open, 0.01) * 100

    # 5. 日内高低点位置
    day_high = float(np.max(highs[:ref_idx + 1]))
    day_low = float(np.min(lows[:ref_idx + 1]))
    if day_high > day_low:
        intraday_pos = (price - day_low) / (day_high - day_low)
    else:
        intraday_pos = 0.5

    # 6. 布林带宽度分位
    if n >= 20:
        bb_ma20 = np.convolve(closes, np.ones(20)/20, mode='valid')
        if len(bb_ma20) > 0:
            bb_std = np.std(closes[-len(bb_ma20):])
            bb_width = bb_std * 2 / max(bb_ma20[-1], 0.01) * 100
            # 近 20 日分位
            rolling_widths = []
            for j in range(max(0, ref_idx - 40), ref_idx + 1):
                seg = closes[max(0, j - 20):j + 1]
                if len(seg) >= 10:
                    seg_ma = float(np.mean(seg))
                    seg_std = float(np.std(seg))
                    rolling_widths.append(seg_std * 2 / max(seg_ma, 0.01) * 100)
            if rolling_widths:
                bb_pctile = sum(1 for w in rolling_widths if w <= bb_width) / len(rolling_widths)
            else:
                bb_pctile = 0.5
        else:
            bb_pctile = 0.5
    else:
        bb_pctile = 0.5

    # 7. 量比 (vs 前 5 日同时段)
    vol_ratio = vol / max(float(np.mean(vols[max(0, ref_idx - 20):ref_idx + 1])), 1)

    # 8. 是否整数关口
    nearest_int = round(price)
    is_integer = abs(price - nearest_int) / max(price, 0.01) < 0.005

    # 9. 是否均线交叉 (MA5/MA20 差值 < 0.5%)
    ma5 = float(np.mean(closes[max(0, ref_idx - 5):ref_idx + 1])) if ref_idx >= 5 else price
    ma_cross = abs(ma5 - ma20) / max(ma20, 0.01) * 100

    # 10. 前一根 K 线上影线占比
    prev_high = float(highs[max(0, ref_idx - 1)])
    prev_open = float(opens[max(0, ref_idx - 1)])
    prev_close = float(closes[max(0, ref_idx - 1)])
    prev_body_top = max(prev_open, prev_close)
    upper_shadow = (prev_high - prev_body_top) / max(prev_high - float(lows[max(0, ref_idx - 1)]), 0.001)
    upper_shadow = min(1.0, max(0.0, upper_shadow))

    return {
        "vwap_dist": round(vwap_dist, 2),
        "ma20_dist": round(ma20_dist, 2),
        "prev_close_dist": round(prev_close_dist, 2),
        "today_open_dist": round(today_open_dist, 2),
        "intraday_position": round(intraday_pos, 3),
        "bband_pctile": round(bb_pctile, 3),
        "vol_ratio": round(vol_ratio, 2),
        "is_integer_level": bool(is_integer),
        "ma_cross_proximity": round(ma_cross, 2),
        "upper_shadow_ratio": round(upper_shadow, 3),
    }


# ── 条件组合定义 ──
TRIGGER_CONDITIONS = [
    {
        "name": "回踩VWAP",
        "fn": lambda ind: ind["vwap_dist"] > -1.0 and ind["vwap_dist"] < 0.5,
    },
    {
        "name": "压低均价",
        "fn": lambda ind: ind["vwap_dist"] < -1.0 and ind["intraday_position"] < 0.4,
    },
    {
        "name": "布林收窄",
        "fn": lambda ind: ind["bband_pctile"] < 0.25,
    },
    {
        "name": "整数关口",
        "fn": lambda ind: ind["is_integer_level"],
    },
    {
        "name": "缩量横盘",
        "fn": lambda ind: ind["vol_ratio"] < 0.6 and ind["intraday_position"] > 0.3 and ind["intraday_position"] < 0.7,
    },
    {
        "name": "放量筑底",
        "fn": lambda ind: ind["vol_ratio"] > 2.0 and ind["intraday_position"] < 0.3,
    },
    {
        "name": "上影抛压",
        "fn": lambda ind: ind["upper_shadow_ratio"] > 0.5,
    },
    {
        "name": "均线粘合",
        "fn": lambda ind: ind["ma_cross_proximity"] < 0.5 and ind["ma20_dist"] > -2 and ind["ma20_dist"] < 2,
    },
    {
        "name": "VWAP横盘",
        "fn": lambda ind: ind["vwap_dist"] > -0.5 and ind["vwap_dist"] < 0.5
                         and ind["ma_cross_proximity"] < 1.0,
    },
    {
        "name": "开盘回补",
        "fn": lambda ind: ind["today_open_dist"] < -1.0 and ind["intraday_position"] < 0.5,
    },
]


async def find_trigger_conditions(
    symbol: str, action_type: str = "fast_rise",
    lookback_days: int = 180, scan_date: date = None
) -> dict:
    """从 min_kline 加载该股过去 lookback_days 的 5 分钟线，
    检测指定类型动作，计算触发条件 vs 随机对照组的提升度。

    Args:
        symbol: 股票代码
        action_type: 动作类型 (fast_rise/fast_fall/support_sideways/tail_attack/open_charge)
        lookback_days: 回看天数
        scan_date: 当前日期 (默认今天)

    Returns:
        {action_type, total_actions, top_triggers, current_status}
    """
    if scan_date is None:
        scan_date = date.today()
    lookback_start = scan_date - timedelta(days=lookback_days)

    # ── 加载历史 5 分钟线 ──
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_time, open, high, low, close, volume
            FROM min_kline
            WHERE ts_code = :s AND trade_time >= :start
            ORDER BY trade_time
        """), {"s": symbol, "start": lookback_start})
        rows = r.fetchall()

    if len(rows) < LOOKBACK_MIN_BARS:
        return {"insufficient_data": True,
                "reason": f"5分钟线不足 ({len(rows)}根, 需≥{LOOKBACK_MIN_BARS})",
                "action_type": action_type}

    bars = [{
        "time": str(row[0]),
        "open": float(row[1] or 0),
        "high": float(row[2] or 0),
        "low": float(row[3] or 0),
        "close": float(row[4] or 0),
        "vol": float(row[5] or 0),
    } for row in rows if float(row[1] or 0) > 0]

    if len(bars) < LOOKBACK_MIN_BARS:
        return {"insufficient_data": True,
                "reason": f"有效5分钟线不足 ({len(bars)}根)",
                "action_type": action_type}

    # ── 检测所有动作 ──
    all_actions = detect_actions(bars)

    # 筛选目标类型的动作
    target_actions = [a for a in all_actions if a["type"] == action_type]
    total_actions = len(target_actions)

    if total_actions < MIN_ACTIONS_FOR_TRIGGER:
        return {"insufficient_data": True,
                "reason": f"动作不足 ({total_actions}次, 需≥{MIN_ACTIONS_FOR_TRIGGER})",
                "total_actions": total_actions,
                "action_type": action_type}

    # ── 计算每个动作的嫌疑指标 ──
    action_indicators = []
    for a in target_actions:
        ind = compute_suspect_indicators(bars, a["index"])
        if ind:
            action_indicators.append(ind)

    if len(action_indicators) < MIN_ACTIONS_FOR_TRIGGER:
        return {"insufficient_data": True,
                "reason": f"有效指标快照不足 ({len(action_indicators)}个)",
                "total_actions": total_actions,
                "action_type": action_type}

    # ── 随机对照组: 从非动作时段随机抽取同等数量 ──
    action_indices = set(a["index"] for a in target_actions)
    all_indices = list(range(20, len(bars) - 5))
    non_action_indices = [i for i in all_indices if i not in action_indices and i + 3 < len(bars)]

    random_indicators = []
    if len(non_action_indices) >= len(action_indicators) * 2:
        rng = np.random.RandomState(42)
        sampled = rng.choice(non_action_indices, size=min(len(action_indicators) * 2, len(non_action_indices)), replace=False)
        for idx in sampled:
            ind = compute_suspect_indicators(bars, idx + 3)  # +3 补偿
            if ind:
                random_indicators.append(ind)

    # ── 计算每个触发条件的提升度 ──
    trigger_results = []
    for cond in TRIGGER_CONDITIONS:
        # 动作组命中率
        action_hits = sum(1 for ind in action_indicators if cond["fn"](ind))
        hit_rate = action_hits / len(action_indicators)

        # 对照组命中率
        if random_indicators:
            random_hits = sum(1 for ind in random_indicators if cond["fn"](ind))
            random_rate = random_hits / len(random_indicators)
            lift = hit_rate / max(random_rate, 0.001)
        else:
            random_rate = 0
            lift = 999 if hit_rate > 0 else 0

        if lift >= LIFT_THRESHOLD and hit_rate >= HIT_RATE_THRESHOLD:
            trigger_results.append({
                "condition": cond["name"],
                "hit_rate": round(hit_rate, 3),
                "random_rate": round(random_rate, 3),
                "lift_vs_random": round(lift, 1),
                "summary": f"{hit_rate*100:.0f}%的{action_type}前出现'{cond['name']}' (提升{lift:.1f}x)",
            })

    trigger_results.sort(key=lambda x: x["lift_vs_random"], reverse=True)

    # ── 当前状态: 用最近 50 根 K 线的最后位置判断是否满足触发条件 ──
    current_status: dict = {"is_any_trigger_active": False, "active_triggers": [],
                              "estimated_probability": 0.0}
    if len(bars) >= 50:
        recent_bars = bars[-50:]
        recent_idx = len(recent_bars) - 3  # 假设动作位置在倒数第 3 根
        if recent_idx > 0:
            cur_ind = compute_suspect_indicators(recent_bars, recent_idx)
            if cur_ind:
                active = []
                for t in trigger_results:
                    cond = next(c for c in TRIGGER_CONDITIONS if c["name"] == t["condition"])
                    if cond and cond["fn"](cur_ind):
                        active.append(t["condition"])
                if active:
                    max_hit = max(t["hit_rate"] for t in trigger_results if t["condition"] in active)
                    current_status = {
                        "is_any_trigger_active": True,
                        "active_triggers": active,
                        "estimated_probability": round(max_hit, 3),
                    }

    return {
        "status": "success",
        "action_type": action_type,
        "total_actions": total_actions,
        "top_triggers": trigger_results[:5],
        "current_status": current_status,
    }


async def scan_active_signals(
    symbols: list[str], scan_date: date = None
) -> list[dict]:
    """扫描股票列表，返回当前满足历史操盘触发条件的股票。

    对每只股票运行 find_trigger_conditions("fast_rise") 和 ("fast_fall")，
    汇总有活跃触发条件的股票。
    """
    if scan_date is None:
        scan_date = date.today()

    results = []
    for sym in symbols:
        try:
            rise = await find_trigger_conditions(sym, "fast_rise", 180, scan_date)
            fall = await find_trigger_conditions(sym, "fast_fall", 180, scan_date)

            entry = {"symbol": sym, "fast_rise": None, "fast_fall": None}

            if isinstance(rise, dict) and not rise.get("insufficient_data"):
                cs = rise.get("current_status", {})
                if cs.get("is_any_trigger_active"):
                    entry["fast_rise"] = {
                        "active_triggers": cs.get("active_triggers", []),
                        "probability": cs.get("estimated_probability", 0),
                    }

            if isinstance(fall, dict) and not fall.get("insufficient_data"):
                cs = fall.get("current_status", {})
                if cs.get("is_any_trigger_active"):
                    entry["fast_fall"] = {
                        "active_triggers": cs.get("active_triggers", []),
                        "probability": cs.get("estimated_probability", 0),
                    }

            if entry["fast_rise"] or entry["fast_fall"]:
                results.append(entry)
        except Exception as e:
            logger.debug(f"Micro behavior scan skipped for {sym}: {e}")

    return results
