"""筹码分析引擎 v2.0 — Tushare cyq_perf + cyq_chips 真实筹码分布.

核心问题: 一只股票横盘锁死了 40 天, 筹码成本分布如何? 上方套牢盘多少?

v2.0 变更 (2026-06-09):
  - 数据源从 5 分钟线手算 → Tushare cyq 真实筹码分布
  - define_zones() 保留 (三区模型), compute_absorption() 退役
  - compute_chip_absorption_from_cyq() 替代手算吸收率
  - compute_chip_trend_from_cyq() 替代分时段量价归因
  - analyze_chip_absorption() 签名不变, 内部衔接到 chip_service

退役清单:
  - compute_absorption()                    → 退役, 替换为 chip_service.compute_chip_absorption_from_cyq()
  - fetch_5min_bars 用于筹码分析            → 退役, 不再拉分钟线做筹码
  - 10 天分段量价归因                       → 退役, 替换为 cyq_perf 历史趋势
  - SEGMENT_DAYS, LOCK_LOOKBACK 常量        → 退役 (仅 define_zones 保留 LOCK_LOOKBACK)

保留:
  - define_zones()                           → 保留 (三区框架仍有结构价值)
  - analyze_chip_absorption()                → 保留, 内部改调用 chip_service
  - 所有调用方无需改代码                     → 签名不变, 返回协议兼容
"""

import logging, numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("chip_analyzer")

LOCK_LOOKBACK = 60  # 日线回看天数 (仅 define_zones 使用)

# ── 全局: 通过环境变量切换新旧数据源 ──
_USE_CYQ = True  # True=Tushare cyq 真实筹码 | False=旧手算 (紧急回退)


# ═══════════════════════════════════════════════════════════
#  保留: 三区模型
# ═══════════════════════════════════════════════════════════

def define_zones(lock_bottom: float, lock_top: float,
                 daily_highs_60d: np.ndarray,
                 daily_lows_60d: np.ndarray) -> dict:
    """定义三个价格区间.

    当锁死区间给定时 (来自锁死检测):
      Z_LOCK  = [lock_bottom, lock_top]  当前横盘范围
      Z_OVER  = [lock_top, 60日最高]    上方套牢区
      Z_BELOW = [60日最低, lock_bottom]  下方支撑区
    """
    h_60 = float(np.max(daily_highs_60d)) if len(daily_highs_60d) > 0 else lock_top * 1.3
    l_60 = float(np.min(daily_lows_60d)) if len(daily_lows_60d) > 0 else lock_bottom * 0.8

    return {
        "Z_LOCK":  {"low": round(lock_bottom, 2), "high": round(lock_top, 2)},
        "Z_OVER":  {"low": round(lock_top, 2), "high": round(h_60, 2)},
        "Z_BELOW": {"low": round(l_60, 2), "high": round(lock_bottom, 2)},
    }


# ═══════════════════════════════════════════════════════════
#  退役: compute_absorption() — 5 分钟线手算吸收率
#  (保留空壳, 调用 chip_service.compute_chip_absorption_from_cyq)
# ═══════════════════════════════════════════════════════════

# ── 此函数已退役, 仅作记号 ──
# def compute_absorption(bars_5min: list[dict], zones: dict) -> dict:
#     ...
# 替代: chip_service.compute_chip_absorption_from_cyq()


# ═══════════════════════════════════════════════════════════
#  主入口: analyze_chip_absorption() — 签名不变
# ═══════════════════════════════════════════════════════════

async def analyze_chip_absorption(
    ts_code: str,
    lock_bottom: float = None,
    lock_top: float = None,
    trade_date: str | None = None,
) -> dict | None:
    """一站式筹码分析 — Tushare cyq 驱动.

    调用方无需任何改动, 返回协议向后兼容。

    Args:
        ts_code: 股票代码
        lock_bottom, lock_top: 锁死区间 (可选, 否则从 DB 自动推断)
        trade_date: 分析日期 (可选, 默认最新)

    Returns:
        {zones, current_price, absorption, summary, cyq_snapshot}
        兼容旧版字段, 新增 cyq_snapshot 透出真实筹码数据
    """
    from app.services.chip_service import (
        get_cyq_perf, get_cyq_chips,
        compute_chip_absorption_from_cyq, compute_chip_trend_from_cyq,
    )

    # 加载日线数据 (计算当前价 + 三区间)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT close, high, low FROM daily_kline
            WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 120
        """), {"c": ts_code})
        rows = list(reversed(r.fetchall()))

    if len(rows) < 30:
        return {"error": "日线数据不足"}

    closes = np.array([float(r[0] or 0) for r in rows])
    highs = np.array([float(r[1] or closes[i]) for i, r in enumerate(rows)])
    lows = np.array([float(r[2] or closes[i]) for i, r in enumerate(rows)])
    current_price = float(closes[-1])

    # 锁死区间 — 如果未提供, 用最近 30 日高低点
    if lock_bottom is None or lock_top is None:
        h_30 = float(np.max(highs[-30:]))
        l_30 = float(np.min(lows[-30:]))
        lock_top = h_30
        lock_bottom = l_30

    # 三区间
    zones = define_zones(lock_bottom, lock_top,
                         highs[-LOCK_LOOKBACK:], lows[-LOCK_LOOKBACK:])

    # ── 真实筹码数据: 当日筹码吸收率 ──
    abs_data = await compute_chip_absorption_from_cyq(
        ts_code, lock_bottom, lock_top, trade_date)

    # ── 筹码成本趋势: cyq_perf 历史 ──
    trend_data = await compute_chip_trend_from_cyq(
        ts_code, lock_bottom, lock_top, lookback_days=LOCK_LOOKBACK)

    # ── cyq_perf 快照 (最新的成本五分位 + 获利盘比例) ──
    cyq_snap = await get_cyq_perf(ts_code, trade_date)

    # ── 构建返回 (向后兼容旧字段) ──
    zl = zones["Z_LOCK"]
    zo = zones["Z_OVER"]

    if "error" in abs_data:
        return {
            "zones": zones,
            "current_price": round(current_price, 2),
            "absorption": abs_data,  # 含 source="tushare_cyq"
            "summary": f"锁死区间 ¥{zl['low']}-{zl['high']} | 上方套牢区 ¥{zo['low']}-{zo['high']} | 筹码数据: {abs_data.get('error', '未知错误')}",
            "cyq_snapshot": cyq_snap,
        }

    ar = abs_data["ar_ratio"]
    verdict = abs_data["verdict"]

    # 摘要
    lines = []
    lines.append(f"锁死区间 ¥{zl['low']}-{zl['high']} | 上方套牢区 ¥{zo['low']}-{zo['high']}")

    if trend_data.get("source") == "tushare_cyq":
        trend = trend_data.get("trend", "?")
        lines.append(f"筹码集中度 {ar*100:.0f}% ({verdict}) | 趋势: {trend}")

        if trend == "快收集筹":
            lines.append("趋势: 获利盘比例快速上升 — 筹码在加速沉淀")
        elif trend == "慢收集筹":
            lines.append("趋势: 获利盘比例缓慢上升 — 筹码在逐步集中")
        elif trend == "筹码稳定":
            lines.append("趋势: 成本分布稳定 — 无明显收集或派发")
        elif trend == "筹码松动":
            lines.append("趋势: 获利盘比例下降 — 可能有资金在出货")
        elif trend == "加速派发":
            lines.append("趋势: 获利盘比例快速下降 — 警惕出货风险")
    else:
        # 旧版兼容: 只有单点 ar_ratio, 没有趋势
        lines.append(f"筹码集中度 {ar*100:.0f}% ({verdict})")

    if ar >= 0.60:
        lines.append("结论: 筹码已高度集中在锁死区, 开锁后抛压轻 — 容易涨")
    elif ar >= 0.50:
        lines.append("结论: 筹码在沉淀中, 但仍有相当套牢盘 — 建议持续观察")
    elif ar >= 0.35:
        lines.append("结论: 锁死区筹码集中度偏低, 上方套牢盘压力较大")
    else:
        lines.append("结论: 套牢盘沉重, 上方筹码仍需时间消化")

    # cyq_perf 增强总结
    if cyq_snap:
        wr = float(cyq_snap.get("winner_rate", 0))
        cost50 = float(cyq_snap.get("cost_50pct", 0))
        cost85 = float(cyq_snap.get("cost_85pct", 0))
        lines.append(
            f"[真实筹码] 获利盘{wr:.1f}% | 中位成本¥{cost50:.2f} | 高成本线¥{cost85:.2f} "
            f"(85%筹码成本≤此价)"
        )

    return {
        "zones": zones,
        "current_price": round(current_price, 2),
        "absorption": {
            **abs_data,
            "trend": trend_data.get("trend", "数据不足"),
            "segments": trend_data.get("segments", []),  # 成本趋势分段 (兼容旧 segments 字段名)
        },
        "summary": " | ".join(lines),
        # ── v2.0 新增: 真实筹码快照 ──
        "cyq_snapshot": cyq_snap,
        "cyq_trend": trend_data,
    }
