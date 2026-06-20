"""AlphaFlow 评估器 — 历史浪质量 + 策略分类.

环节二: 从历史锁周期分析"锁后有没有浪、吃相是否难看"
环节三: 当前锁质量判定 (位置/收敛/量/相对强度)
环节四: 组合策略归类 — 不是加权分, 是标签匹配
"""

import logging, numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.eval")

# ── 历史浪质量常量 ──
MIN_WAVE_PCT = 8.0         # 开锁后最少涨 8% 才算"有浪"
MAX_CRASH_PCT = 50.0        # 浪后回撤超过涨幅的 50% = 断崖
MIN_LOCK_DAYS_FOR_HISTORY = 10  # 锁死最少天数


async def evaluate_history(ts_code: str, lock_cycles: list[dict]) -> dict:
    """环节二: 分析历史锁周期的开锁后表现.

    Args:
        lock_cycles: lock-detail 的锁周期列表 [{start, end, high, low, days}, ...]

    Returns:
        {
            total_cycles, valid_waves, crash_cycles,闷杀_cycles,
            avg_wave_pct, avg_crash_pct, decay_trend,
            fatal_tags: [A,B,...],
            history_label: 'strong'|'moderate'|'weak'|'none',
        }
    """
    n = len(lock_cycles)
    if n < 3:
        return {"history_label": "none", "fatal_tags": [],
                "total_cycles": n, "valid_waves": 0}

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, close FROM daily_kline
            WHERE ts_code = :c ORDER BY trade_date
        """), {"c": ts_code})
        all_closes = np.array([float(row[1] or 0) for row in r.fetchall()])
        all_dates = [row[0] for row in r.fetchall()]

    if len(all_closes) < 200:
        return {"history_label": "none", "fatal_tags": [],
                "total_cycles": n, "valid_waves": 0}

    # ── 对每段锁死, 找其后 40 个交易日的最高点 → 浪的幅度 ──
    wave_details = []
    for i, seg in enumerate(lock_cycles[:-1]):  # 最后一轮是当前锁, 不算
        seg_end = seg["end"]
        if seg_end + 5 >= len(all_closes):
            continue

        # 锁死价格中枢
        lock_avg = float(np.mean(all_closes[seg["start"]:seg["end"]+1]))

        # 开锁后 40 日最高价
        look_ahead = min(seg_end + 40, len(all_closes))
        post_close = all_closes[seg_end+1:look_ahead+1]
        if len(post_close) < 10:
            continue

        peak_price = float(np.max(post_close))
        peak_idx = int(np.argmax(post_close))
        exit_price = float(post_close[-1])

        wave_pct = (peak_price - lock_avg) / max(lock_avg, 0.01) * 100

        # 断崖: 从峰顶到离开窗口时的回撤
        if wave_pct >= MIN_WAVE_PCT:
            crash_pct = (peak_price - exit_price) / max(peak_price, 0.01) * 100
            crash_ratio = crash_pct / wave_pct * 100  # 回撤占涨幅的%
        else:
            crash_pct = 0
            crash_ratio = 0

        # 闷杀: 开锁后连涨都没涨, 直接跌
        is_mensha = (wave_pct < 3.0)  # 开锁后 40 天涨幅 < 3%

        wave_details.append({
            "cycle_n": i + 1,
            "lock_days": seg["days"],
            "wave_pct": round(wave_pct, 1),
            "crash_pct": round(crash_pct, 1),
            "crash_ratio": round(crash_ratio, 1),
            "is_mensha": is_mensha,
            "had_wave": wave_pct >= MIN_WAVE_PCT,
            "ate_all": crash_ratio > MAX_CRASH_PCT and wave_pct >= MIN_WAVE_PCT,
        })

    # ── 致命判定 ──
    fatal_tags = []
    valid_waves = [w for w in wave_details if w["had_wave"]]

    # A: 有锁无浪
    if len(valid_waves) == 0:
        fatal_tags.append("A_no_waves")
    # B: 断崖吃相 — 每轮有浪的都断崖
    if valid_waves and all(w["ate_all"] for w in valid_waves):
        fatal_tags.append("B_all_crash")

    # ── 降级标记 ──
    downgrade_tags = []
    mensha_count = sum(1 for w in wave_details if w["is_mensha"])
    mensha_ratio = mensha_count / len(wave_details) if wave_details else 0

    # C: 闷杀比例
    if mensha_ratio > 0.3:
        downgrade_tags.append(f"C_mensha_{mensha_ratio:.0%}")

    # E: 浪衰减 (线性回归 slope)
    if len(valid_waves) >= 3:
        wave_pcts = [w["wave_pct"] for w in valid_waves]
        slope = float(np.polyfit(range(len(wave_pcts)), wave_pcts, 1)[0])
        if slope < -0.5:
            downgrade_tags.append(f"E_decay_{slope:.1f}")

    # F: 锁时间延长
    if n >= 4:
        lock_days_list = [s["days"] for s in lock_cycles[:-1]]
        # 前半 vs 后半
        mid = len(lock_days_list) // 2
        if len(lock_days_list[:mid]) >= 2 and len(lock_days_list[mid:]) >= 2:
            early_avg = float(np.mean(lock_days_list[:mid]))
            late_avg = float(np.mean(lock_days_list[mid:]))
            if late_avg > early_avg * 1.4:
                downgrade_tags.append(f"F_lengthen_{late_avg/early_avg:.1f}x")

    # ── 历史标签 ──
    if fatal_tags:
        history_label = "fatal"
    elif len(valid_waves) >= 3 and not downgrade_tags:
        history_label = "strong"
    elif len(valid_waves) >= 2:
        history_label = "moderate"
    elif len(valid_waves) >= 1:
        history_label = "weak"
    else:
        history_label = "none"

    return {
        "total_cycles": n,
        "valid_waves": len(valid_waves),
        "wave_details": wave_details,
        "avg_wave_pct": round(float(np.mean([w["wave_pct"] for w in valid_waves])), 1) if valid_waves else 0,
        "fatal_tags": fatal_tags,
        "downgrade_tags": downgrade_tags,
        "history_label": history_label,
        "crash_cycles": sum(1 for w in wave_details if w["ate_all"]),
        "mensha_cycles": mensha_count,
    }


async def evaluate_current_lock(
    ts_code: str, lock_result: dict, index_closes: np.ndarray = None
) -> dict:
    """环节三: 当前锁质量判定.

    检查: 位置(底/中/顶)、收敛程度、量趋势、相对大盘强度.
    """
    from app.services.lock_detector import detect_lock_simple

    # 锁位置: 现价在锁区间的哪里
    amp_short = lock_result.get("amplitude_short_15d", lock_result.get("amplitude_30d", 0))
    amp_long = lock_result.get("amplitude_long_40d", lock_result.get("amplitude_30d", 0))

    # 振幅收敛: 短窗 vs 长窗
    # 短窗振幅 / 长窗振幅 — 越小越收敛
    convergence = round(amp_short / max(amp_long, 0.1), 2) if amp_long > 0 else 1.0

    # 锁位置: 需要从 DB 查现价 vs 锁区间
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT close FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 50
        """), {"c": ts_code})
        closes = [float(row[0] or 0) for row in r.fetchall()]
        r_v = await s.execute(text("""
            SELECT volume FROM daily_kline WHERE ts_code=:c ORDER BY trade_date DESC LIMIT 50
        """), {"c": ts_code})
        vols = [float(row[0] or 0) for row in r_v.fetchall()]

    if len(closes) < 30:
        return {"quality_label": "unknown", "convergence": convergence}

    recent_c = closes[:30]
    recent_high = max(recent_c)
    recent_low = min(recent_c)
    current = recent_c[0]

    # 位置: 0=底, 1=顶
    if recent_high > recent_low:
        position = (current - recent_low) / (recent_high - recent_low)
    else:
        position = 0.5

    if position < 0.30:
        pos_label = "底部"
    elif position < 0.50:
        pos_label = "中下部"
    elif position < 0.70:
        pos_label = "中部"
    elif position < 0.85:
        pos_label = "中上部"
    else:
        pos_label = "顶部"

    # 量趋势: 近 5 日 vs 近 20 日
    vol_5d = float(np.mean(vols[:5])) if len(vols) >= 5 else 0
    vol_20d = float(np.mean(vols[:20])) if len(vols) >= 20 else vol_5d
    vol_ratio = vol_5d / max(vol_20d, 1)
    if vol_ratio < 0.7:
        vol_label = "缩量"
    elif vol_ratio > 1.5:
        vol_label = "放量"
    else:
        vol_label = "平稳"

    # 相对大盘强度
    market_ret = lock_result.get("market_return", 0)
    relative_strength = lock_result.get("relative_strength", 0)
    if market_ret < -3 and relative_strength > 5:
        rs_label = "抗跌"
    elif market_ret > 5 and relative_strength < -3:
        rs_label = "弱于大盘"
    elif relative_strength > 3:
        rs_label = "强于大盘"
    elif relative_strength < -3:
        rs_label = "弱于大盘"
    else:
        rs_label = "跟随"

    # 质量判定: 收敛 + 缩量 + 底部位 = 优质
    quality_score = 0
    if convergence < 0.7:
        quality_score += 1  # 振幅收敛
    if vol_ratio < 0.8:
        quality_score += 1  # 缩量
    if position < 0.50:
        quality_score += 1  # 底部或中下部

    if quality_score >= 3:
        quality_label = "优"
    elif quality_score >= 2:
        quality_label = "中"
    elif quality_score >= 1:
        quality_label = "偏弱"
    else:
        quality_label = "差"

    return {
        "position_pct": round(position * 100, 1),
        "position_label": pos_label,
        "convergence": convergence,
        "vol_ratio": round(vol_ratio, 2),
        "vol_label": vol_label,
        "relative_strength_label": rs_label,
        "quality_label": quality_label,
        "quality_score": quality_score,
    }


def classify_strategy(history_label: str, quality_label: str,
                      market_risk: str) -> dict:
    """环节四: 组合策略归类.

    两两组合 → 策略类型 → 优先级排序
    """
    # 优先级映射
    priority_map = {
        "满配_顺风": 1,
        "满配_逆风": 1,      # 抗跌验证, 同样高优
        "满配_平风": 2,
        "待确认_顺风": 3,
        "新秀_逆风": 4,
        "新秀_顺风": 5,
        "弱_any": 6,
        "致命": 99,           # 不被展示
    }

    market_key = "平风"
    if market_risk in ("high", "elevated"):
        market_key = "逆风"
    elif market_risk == "low":
        market_key = "顺风"

    if history_label == "fatal":
        return {"strategy": "排除",
                "priority": 99,
                "group": "已排除",
                "label": "致命缺陷: " + ("有锁无浪" if "A" in str(history_label) else "断崖吃相"),
                "display": False}

    if history_label == "strong":
        if quality_label in ("优", "中"):
            key = "满配"
            if market_key == "逆风":
                group = "抗跌真强"
                label = f"老兵+锁{quality_label}, 大盘逆风验证 — 高优先级"
            elif market_key == "顺风":
                group = "满配顺风"
                label = f"老兵+锁{quality_label}, 大盘托举"
            else:
                group = "满配待启"
                label = f"老兵+锁{quality_label}, 观察启动信号"
            return {"strategy": key, "priority": priority_map.get(f"满配_{market_key}", 3),
                    "group": group, "label": label, "display": True}

    if history_label == "moderate":
        if quality_label == "优":
            return {"strategy": "待确认", "priority": 3,
                    "group": "待确认", "label": "历史中等+当前锁优", "display": True}
        return {"strategy": "观察", "priority": 5,
                "group": "观察", "label": "历史中等", "display": True}

    if history_label == "weak" and quality_label == "优":
        return {"strategy": "新秀", "priority": 4,
                "group": "新秀锁优",
                "label": f"历史弱但当前锁{quality_label} — 轻仓试" if market_key!="逆风" else "历史弱但抗跌 — 关注",
                "display": True}

    if history_label in ("weak", "none") and quality_label == "差":
        return {"strategy": "排除", "priority": 99,
                "group": "已排除", "label": "历史弱+当前锁差", "display": False}

    return {"strategy": "观察", "priority": 7,
            "group": "普通观察", "label": f"历史{history_label}/锁{quality_label}",
            "display": True}
