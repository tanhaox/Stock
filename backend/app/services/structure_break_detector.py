"""AlphaFlow 结构性支撑检测 — 基于摆动分析的趋势破坏识别.

核心逻辑:
  1. 找出 200 天内的主要摆动高点(swing highs)和低点(swing lows)
  2. 两个上升高点之间的谷底 = 关键支撑位
  3. 价格跌破关键支撑位 → 趋势破坏 → 这只股票告别主升浪
  4. 支撑位未破 → 趋势完好 → 继续跟踪

比"锁死检测"更根本 — 锁死只是趋势中的一个阶段,
结构破坏才是趋势终结的最终确认。
"""

import logging, numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.structure")


async def detect_trend_break(symbol: str, scan_date: date = None) -> dict:
    """检测趋势结构是否被破坏.

    算法:
      找两个最近的 higher-high (上升的高点),
      取它们之间的 lowest-low 作为 Key Support Level.
      如果当前价格低于 KSL → 趋势破坏.

    Returns:
        {
            status: "intact" | "warning" | "broken" | "dead",
            key_support: float | None,
            swing_points: [...],
            current_price: float,
            days_below_support: int,
        }
    """
    if scan_date is None:
        scan_date = date.today()

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, open, close, volume, high, low
            FROM daily_kline WHERE ts_code = :c AND trade_date <= :d
            ORDER BY trade_date DESC LIMIT 400
        """), {"c": symbol, "d": scan_date})
        rows_raw = r.fetchall()

    rows = list(reversed(rows_raw))
    if len(rows) < 150:
        return {"status": "insufficient_data", "key_support": None, "swing_points": []}

    closes = np.array([float(row[2] or 0) for row in rows])
    highs = np.array([float(row[4] or closes[i]) for i, row in enumerate(rows)])
    lows = np.array([float(row[5] or closes[i]) for i, row in enumerate(rows)])
    n = len(closes)
    current_price = closes[-1]

    # ── 1. 找主要摆动点 ──
    swing_highs = []  # [(idx, price, date)]
    swing_lows = []

    # 摆动高: 左右各 15 天内的最高点 (更宽的窗口捕获主要摆动)
    for i in range(15, n - 15):
        if highs[i] == np.max(highs[i-15:i+16]):
            swing_highs.append((i, float(highs[i]), rows[i][0]))

    # 摆动低: 左右各 15 天内的最低点
    for i in range(15, n - 15):
        if lows[i] == np.min(lows[i-15:i+16]):
            swing_lows.append((i, float(lows[i]), rows[i][0]))

    if len(swing_highs) < 3 or len(swing_lows) < 3:
        return {"status": "insufficient_swings", "key_support": None,
                "swing_highs": len(swing_highs), "swing_lows": len(swing_lows)}

    # ── 2. 找一对上升高点之间的谷底 = 关键支撑 ──
    # 从最近的摆动高往前找
    # 条件: 高点2 > 高点1, 且两者之间有明确低点
    key_support_levels = []

    for i in range(len(swing_highs) - 1, 0, -1):
        h2_idx, h2_price, h2_date = swing_highs[i]
        for j in range(i - 1, -1, -1):
            h1_idx, h1_price, h1_date = swing_highs[j]

            if h2_price <= h1_price:
                continue  # 不是上升高点(下降或平顶)

            # 找到 h1 到 h2 之间的最低摆动低 = 关键支撑
            valley_low = None
            for l_idx, l_price, l_date in swing_lows:
                if h1_idx < l_idx < h2_idx:
                    if valley_low is None or l_price < valley_low[1]:
                        valley_low = (l_idx, l_price, l_date)

            if valley_low is not None:
                key_support_levels.append({
                    "h1_idx": h1_idx, "h1_price": h1_price, "h1_date": str(h1_date)[:10],
                    "h2_idx": h2_idx, "h2_price": h2_price, "h2_date": str(h2_date)[:10],
                    "support_idx": valley_low[0], "support_price": valley_low[1],
                    "support_date": str(valley_low[2])[:10],
                    "dist_to_current": round((current_price - valley_low[1]) / valley_low[1] * 100, 1),
                    "broken": current_price < valley_low[1],
                })

    if not key_support_levels:
        return {"status": "no_key_support", "key_support": None, "swing_points": len(swing_highs)}

    # ── 3. 取最近一轮的关键支撑 ──
    latest = key_support_levels[0]  # 最近的一对上升高点
    key_support = latest["support_price"]
    ks_date = latest["support_date"]

    # 所有上升高点对应的支撑
    all_supports = key_support_levels[:5]

    # ── 4. 判定状态 ──
    # 关键支撑破了的层数
    broken_supports = [s for s in all_supports if s["broken"]]
    # 破了之后在下面待了多少天
    days_below = 0
    if key_support is not None and current_price < key_support:
        for i in range(n-1, -1, -1):
            if closes[i] < key_support:
                days_below += 1
            else:
                break

    # 结构性判定 — 层数规则 + 回撤兜底
    n_broken_total = len(broken_supports)
    highest_broken = max(s["support_price"] for s in broken_supports) if broken_supports else 0
    total_days_below = 0
    if highest_broken > 0:
        for c in closes:
            if c < highest_broken:
                total_days_below += 1

    # 200天峰值回撤 (腰斩=无条件死)
    peak_200d = float(np.max(highs[-200:])) if n >= 200 else float(np.max(highs))
    drawdown = (current_price - peak_200d) / peak_200d * 100

    if n_broken_total >= 3 and total_days_below > 60:
        status = "dead"
        label = f"结构崩塌 — {n_broken_total}层全破，{total_days_below}天"
    elif drawdown < -40 and n_broken_total >= 1:
        status = "dead"
        label = f"腰斩确认 — 从{peak_200d:.1f}跌{abs(drawdown):.0f}%，{n_broken_total}层已破"
    elif n_broken_total >= 2 and total_days_below > 30:
        status = "dead"
        label = f"趋势终结 — {n_broken_total}层已破{total_days_below}天"
    elif latest["broken"] and total_days_below > 20:
        status = "broken"
        label = f"趋势破坏 — 跌破{key_support:.1f}支撑{total_days_below}天"
    elif latest["broken"]:
        status = "warning"
        label = f"跌破关键支撑{key_support:.1f}，回抽还有救"
    else:
        # 找最近未破的最高层支撑作为当前支撑
        intact_support = key_support
        for s in all_supports:
            if not s["broken"]:
                intact_support = max(intact_support, s["support_price"])
        status = "intact"
        label = f"结构完好 — 支撑位{intact_support:.2f}，上方高点{latest['h2_price']:.1f}"

    return {
        "symbol": symbol,
        "scan_date": str(scan_date),
        "status": status,
        "label": label,
        "current_price": round(float(current_price), 2),
        "key_support": key_support,
        "key_support_date": ks_date,
        "days_below_support": days_below,
        "n_broken_supports": len(broken_supports),
        "broken_supports": broken_supports,
        "all_supports": all_supports[:5],
        "n_swing_highs": len(swing_highs),
        "n_swing_lows": len(swing_lows),
    }
