"""老兵周期检测 — 锁死→爆发 (v2: scoring-based).

基于日线 K 线数据, 识别每只股票的锁死-爆发周期节奏。
v2: 评分制替代 AND 制 - 每个条件贡献 1 分, score≥2 即算锁死。
    宽松阈值 + 容错机制 (连续5天有3天达标即可)。

核心函数:
  lockup_score() — 单日锁死评分 (0-3)
  detect_breakout() — 单日爆发检测
  find_cycles() — 从历史K线中找到所有锁死→爆发周期
  cycle_statistics() — 统计该股票的周期规律
  current_cycle_position() — 当前在周期中的位置
"""
import numpy as np
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("stock_dna.cycle")

# ═══ v2 宽松阈值 ═══
LOCKUP_ATR_RATIO = 0.8       # ATR5/ATR20 < 0.8 (原 0.6)
LOCKUP_MA_CONVERGENCE = 0.04  # MA粘合 < 4% (原 2%)
LOCKUP_VOL_RATIO = 0.8        # 量比 < 0.8 (原 0.7)
MIN_SCORE_FOR_LOCKUP = 2      # 至少满足 2/3 条件


def lockup_score(kline_rows: list[dict], idx: int) -> tuple[int, dict]:
    """单日锁死评分 (0-3). 每满足一个条件 +1.

    条件:
      1. ATR5/ATR20 < 0.8 (振幅收窄)
      2. MA粘合度 < 0.04 (均线靠拢)
      3. VOL5/VOL20 < 0.8 (量能萎缩)
    """
    if idx < 25:
        return 0, {"reason": "数据不足"}

    closes = np.array([r["close"] for r in kline_rows], dtype=np.float64)
    highs = np.array([r["high"] for r in kline_rows], dtype=np.float64)
    lows = np.array([r["low"] for r in kline_rows], dtype=np.float64)
    volumes = np.array([r["volume"] for r in kline_rows], dtype=np.float64)

    close = closes[idx]
    score = 0

    # 1. ATR 收窄
    trs = []
    for j in range(idx - 19, idx + 1):
        h, l, pc = highs[j], lows[j], closes[j - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr5 = float(np.mean(trs[-5:]))
    atr20 = float(np.mean(trs))
    atr_ratio = atr5 / max(atr20, 1e-9)
    if atr_ratio < LOCKUP_ATR_RATIO:
        score += 1

    # 2. 均线粘合
    ma5 = float(np.mean(closes[idx - 4:idx + 1]))
    ma10 = float(np.mean(closes[idx - 9:idx + 1])) if idx >= 9 else ma5
    ma20 = float(np.mean(closes[idx - 19:idx + 1]))
    ma_range = max(ma5, ma10, ma20) - min(ma5, ma10, ma20)
    ma_convergence = ma_range / max(close, 0.01)
    if ma_convergence < LOCKUP_MA_CONVERGENCE:
        score += 1

    # 3. 量能萎缩
    vol5 = float(np.mean(volumes[idx - 4:idx + 1]))
    vol20 = float(np.mean(volumes[idx - 19:idx + 1]))
    vol_ratio = vol5 / max(vol20, 1e-9)
    if vol_ratio < LOCKUP_VOL_RATIO:
        score += 1

    diagnostics = {
        "score": score,
        "atr_ratio": round(atr_ratio, 4),
        "ma_convergence": round(ma_convergence, 6),
        "vol_ratio": round(vol_ratio, 4),
        "close": round(float(close), 2),
    }
    return score, diagnostics


# ═══ backward compat alias ═══
def detect_lockup(kline_rows: list[dict], idx: int) -> tuple[bool, dict]:
    """向后兼容: 返回 (is_locked, diagnostics)."""
    score, diag = lockup_score(kline_rows, idx)
    return score >= MIN_SCORE_FOR_LOCKUP, diag


def detect_breakout(kline_rows: list[dict], idx: int) -> tuple[bool, dict]:
    """判断某日是否为爆发日。

    条件:
      - 涨幅 > 2% 或
      - 放量 (VOL > VOL_20 × 1.5) 且收阳

    Args:
        kline_rows: K线列表
        idx: 目标索引

    Returns:
        (is_breakout, diagnostics)
    """
    if idx < 25:
        return False, {"reason": "数据不足"}

    closes = np.array([r["close"] for r in kline_rows], dtype=np.float64)
    opens = np.array([r["open"] for r in kline_rows], dtype=np.float64)
    volumes = np.array([r["volume"] for r in kline_rows], dtype=np.float64)

    close = closes[idx]
    open_p = opens[idx]
    ret = (close - open_p) / max(open_p, 0.01) * 100
    vol20 = float(np.mean(volumes[idx - 19:idx + 1]))
    vol_ratio = volumes[idx] / max(vol20, 1e-9)
    is_up = close > open_p

    is_breakout = ret > 2.0 or (vol_ratio > 1.5 and is_up)

    return is_breakout, {
        "day_ret": round(ret, 2),
        "vol_ratio": round(vol_ratio, 2),
        "is_up": bool(is_up),
    }


# ══════════════════════════════════════════════════════════════════════
# 周期扫描
# ══════════════════════════════════════════════════════════════════════

def find_cycles(kline_rows: list[dict]) -> list[dict]:
    """从历史K线中找出所有锁死→爆发周期。

    Returns:
        [{start_idx, end_idx, lockup_days, breakout_date, breakout_ret, total_return}, ...]
    """
    n = len(kline_rows)
    if n < 60:
        return []

    cycles = []
    in_lockup = False
    lockup_start = 0

    for i in range(30, n):
        score_i, _ = lockup_score(kline_rows, i)
        is_breakout, bo_diag = detect_breakout(kline_rows, i)

        if not in_lockup:
            window = [lockup_score(kline_rows, j)[0] for j in range(max(30, i - 4), i + 1)]
            if sum(1 for s in window if s >= 2) >= 3 and score_i >= 2:
                in_lockup = True
                lockup_start = i
        else:
            if is_breakout:
                lockup_days = i - lockup_start
                if lockup_days >= 2:
                    bo_close = kline_rows[i]["close"]
                    future_idx = min(i + 5, n - 1)
                    future_close = kline_rows[future_idx]["close"]
                    total_ret = (future_close - bo_close) / max(bo_close, 0.01) * 100

                    cycles.append({
                        "lockup_start": lockup_start,
                        "breakout_idx": i,
                        "lockup_days": lockup_days,
                        "breakout_ret": bo_diag["day_ret"],
                        "total_return_5d": round(total_ret, 2),
                    })
                in_lockup = False
            elif score_i < 2:
                fail = sum(1 for j in range(i, max(i - 3, lockup_start), -1)
                          if lockup_score(kline_rows, j)[0] < 2)
                if fail >= 3:
                    in_lockup = False

    return cycles


# ══════════════════════════════════════════════════════════════════════
# 周期统计
# ══════════════════════════════════════════════════════════════════════

def cycle_statistics(cycles: list[dict]) -> dict:
    """从周期列表中计算统计量。

    Returns:
        {avg_lockup, std_lockup, cv, avg_breakout_ret, avg_total_ret,
         n_cycles, regularity}
    """
    if not cycles:
        return {
            "avg_lockup_days": 0.0, "std_lockup_days": 0.0,
            "cycle_cv": 999.0, "avg_breakout_return": 0.0,
            "avg_breakout_days": 0.0, "n_cycles": 0,
            "regularity": "无周期数据",
        }

    lockup_days = [c["lockup_days"] for c in cycles]
    bo_rets = [c["breakout_ret"] for c in cycles]
    total_rets = [c["total_return_5d"] for c in cycles]

    avg_lock = float(np.mean(lockup_days))
    std_lock = float(np.std(lockup_days)) if len(lockup_days) > 1 else 0.0
    cv = std_lock / max(avg_lock, 1e-9)

    if cv < 0.3:
        regularity = "规律型 (CV < 0.3)"
    elif cv < 0.6:
        regularity = "中等规律 (0.3 ≤ CV < 0.6)"
    else:
        regularity = "随机型 (CV ≥ 0.6)"

    return {
        "avg_lockup_days": round(avg_lock, 1),
        "std_lockup_days": round(std_lock, 1),
        "cycle_cv": round(cv, 3),
        "avg_breakout_return": round(float(np.mean(bo_rets)), 2),
        "avg_breakout_days": round(float(np.mean(total_rets)) / max(abs(float(np.mean(bo_rets))), 0.01), 1),
        "n_cycles": len(cycles),
        "regularity": regularity,
    }


# ══════════════════════════════════════════════════════════════════════
# 当前周期位置
# ══════════════════════════════════════════════════════════════════════

def current_cycle_position(kline_rows: list[dict], cycle_stats: dict) -> dict:
    """判断当前在周期中的位置。

    Returns:
        {phase, day, position, interpretation, window_status}
    """
    n = len(kline_rows)
    if n < 25:
        return {"phase": "unknown", "day": 0, "position": 0.0, "interpretation": "数据不足"}

    avg_lock = cycle_stats.get("avg_lockup_days", 10)

    # 从最近向前找锁死起点
    lockup_days = 0
    in_lockup_now = False
    breakout_now = False

    for i in range(n - 1, max(0, n - 60), -1):
        is_bo, _ = detect_breakout(kline_rows, i)
        score, _ = lockup_score(kline_rows, i)
        locked = score >= 2

        if is_bo:
            breakout_now = (i >= n - 2)
            break
        if locked:
            lockup_days = n - 1 - i + 1
            in_lockup_now = True
        else:
            break

    position = lockup_days / max(avg_lock, 1e-9)

    # 解析
    if breakout_now:
        phase = "breakout"
        interpretation = "爆发中 — 关注能否持续"
        window_status = "已爆发"
    elif in_lockup_now:
        phase = "lockup"
        if position < 0.5:
            interpretation = "锁死早期 — 耐心持有"
            window_status = "早期"
        elif position < 0.8:
            interpretation = "窗口临近 — 密切关注"
            window_status = "临近"
        elif position < 1.2:
            interpretation = "窗口期内 — 随时可能爆发"
            window_status = "窗口期"
        else:
            interpretation = "超期 — 要么即将爆发要么失效"
            window_status = "超期"
    else:
        phase = "normal"
        interpretation = "正常交易 — 未检测到锁死"
        window_status = "正常"

    return {
        "phase": phase,
        "day": lockup_days,
        "position": round(position, 3),
        "interpretation": interpretation,
        "window_status": window_status,
        "avg_lockup_days": avg_lock,
    }


def extract_cycle_features(kline_rows: list[dict], cycle_stats: dict) -> dict[str, float]:
    """提取 8 维周期特征。

    Args:
        kline_rows: 该股票的全部K线
        cycle_stats: cycle_statistics() 的结果

    Returns:
        8 维特征 dict
    """
    pos_info = current_cycle_position(kline_rows, cycle_stats)

    avg_lock = cycle_stats.get("avg_lockup_days", 10.0)
    position = pos_info.get("position", 0.0)
    lockup_day = pos_info.get("day", 0)

    cf = {}
    cf["cy_is_locked"] = 1.0 if pos_info["phase"] == "lockup" else 0.0
    cf["cy_lockup_day"] = float(lockup_day)
    cf["cy_position_pct"] = round(position, 4)
    cf["cy_lockup_remaining_est"] = round(max(avg_lock - lockup_day, 0), 1)
    cf["cy_avg_lockup_days"] = round(avg_lock, 1)
    cf["cy_cv_lockup"] = round(cycle_stats.get("cycle_cv", 999.0), 3)

    # 爆发概率: 在窗口期内 (0.8-1.2) 的历史爆发比例
    if pos_info["phase"] == "lockup" and 0.8 <= position <= 1.2:
        cf["cy_breakout_prob"] = 0.6  # 占位 — 由实际统计填充
    elif pos_info["phase"] == "lockup" and position > 1.2:
        cf["cy_breakout_prob"] = 0.3
    elif pos_info["phase"] == "breakout":
        cf["cy_breakout_prob"] = 1.0
    else:
        cf["cy_breakout_prob"] = round(max(1.0 - abs(position - 0.85), 0.05), 2)

    cf["cy_expected_ret_if_breakout"] = round(cycle_stats.get("avg_breakout_return", 3.0), 2)

    return cf
