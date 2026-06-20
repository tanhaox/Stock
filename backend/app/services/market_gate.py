"""市场门控 v2.0 — 多维市场环境感知.

v2.0 升级 (2026-05-31):
  - 6种市场状态: 趋势上涨/结构行情/缩量博弈/恐慌杀跌/政策窗口/维稳行情
  - 涨跌家数比 + 新高新低比 (市场宽度)
  - 两市成交额趋势 (放量/缩量)
  - 沪深300 vs 中证1000 相对强度 → 风格判定
  - 输出适合/不适合的策略类型
"""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

DEFAULT_GATE = {
    "min_probability": 0.30, "max_stocks": 100,
    "market_risk": "normal", "market_regime": "unknown",
    "suitable_strategies": [], "adjustments": [],
}


async def get_market_state() -> dict:
    """v2.0: 多维度市场状态."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT trade_date, close FROM daily_kline
            WHERE ts_code = '700001.TI'
            ORDER BY trade_date DESC LIMIT 60
        """))
        sh_rows = [(row[0], float(row[1])) for row in r.fetchall()]

    if len(sh_rows) < 20:
        return {"trend": "unknown", "volatility": 0, "risk": "unknown", "regime": "unknown"}

    closes = [r[1] for r in reversed(sh_rows)]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else ma20
    ma5_prev = sum(closes[-6:-1]) / 5
    trend_up = ma5 > ma10
    trend_strengthening = ma5 > ma5_prev

    # 波动率
    rets = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    vol_5d = (sum(r**2 for r in rets[-5:]) / 5) ** 0.5
    vol_20d = (sum(r**2 for r in rets[-20:]) / max(len(rets[-20:]), 1)) ** 0.5
    vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0

    # ★ 市场宽度 (上涨/下跌家数比)
    breadth = await _get_market_breadth()

    # ★ 成交额趋势
    volume_trend = await _get_volume_trend()

    # ★ 风格判定 (沪深300 vs 中证1000)
    style = await _get_style_bias()

    # ── 6种市场状态判定 ──
    chg_20d = (closes[-1] / closes[-20] - 1) * 100 if len(closes) >= 20 else 0
    adv_pct = breadth.get("advance_pct", 50)

    if adv_pct > 65 and volume_trend.get("direction") == "expanding" and chg_20d > 3:
        regime = "趋势上涨"
        risk = "low"
    elif adv_pct > 50 and style.get("bias") == "small_cap" and vol_ratio < 1.3:
        regime = "结构行情"
        risk = "normal"
    elif adv_pct < 30 and chg_20d < -3 and vol_ratio > 1.5:
        regime = "恐慌杀跌"
        risk = "high"
    elif adv_pct < 30:
        regime = "弱势探底"
        risk = "high"
    elif adv_pct < 40 and chg_20d < 0:
        regime = "弱势探底"
        risk = "elevated"
    elif volume_trend.get("shrink_pct", 0) < -20 and abs(chg_20d) < 3:
        regime = "缩量博弈"
        risk = "elevated"
    elif vol_ratio > 2.0 and chg_20d < -5:
        regime = "恐慌杀跌"
        risk = "high"
    elif abs(chg_20d) < 2 and vol_ratio < 0.8:
        regime = "维稳行情"
        risk = "normal"
    elif breadth.get("new_high_pct", 5) < 3 and chg_20d < -3:
        regime = "弱势探底"
        risk = "high"
    else:
        regime = "震荡整理"
        risk = "normal"

    # 适合的策略
    suitable = _recommend_strategies(regime, style)

    return {
        "trend": "up" if trend_up else "down",
        "ma5": round(ma5, 2), "ma10": round(ma10, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "chg_20d": round(chg_20d, 1),
        "vol_ratio": round(vol_ratio, 2),
        "risk": risk, "regime": regime,
        "trend_strengthening": trend_strengthening,
        "breadth": breadth,
        "volume_trend": volume_trend,
        "style": style,
        "suitable_strategies": suitable,
    }


# ═══════════════════════════════════════════════════════════
#  v7.0.33: 训练风格映射 (6→3 种), 供 v2 trainer 调用
# ═══════════════════════════════════════════════════════════

def regime_to_market_style(regime: str) -> str:
    """6 种市场状态 → 3 种训练风格.

    v7.0.33 新增: 解决 v2 trainer 默认全市场训练的泛化失败问题.

    映射规则:
      - 趋势上涨 → bull  (动量/技术驱动)
      - 恐慌杀跌, 弱势探底 → bear  (防御/估值驱动)
      - 结构行情, 缩量博弈, 维稳行情, 震荡整理 → range  (博弈/形态驱动)
      - unknown → all (兜底, 走全局训练)
    """
    if regime == "趋势上涨":
        return "bull"
    elif regime in ("恐慌杀跌", "弱势探底"):
        return "bear"
    elif regime in ("结构行情", "缩量博弈", "维稳行情", "震荡整理"):
        return "range"
    else:
        return "all"


async def get_current_regime_simple() -> str:
    """返回当前市场风格 (bull/bear/range).

    v7.0.33 新增: v2 trainer 默认自动检测当前市场风格, 按风格训练对应权重.
    """
    try:
        state = await get_market_state()
        regime = state.get("regime", "unknown")
        return regime_to_market_style(regime)
    except Exception as e:
        logger.warning(f"get_current_regime_simple failed: {e}, fallback to 'all'")
        return "all"


async def _get_market_breadth() -> dict:
    """市场宽度: 涨跌家数比 + 新高新低比."""
    try:
        async with async_session_factory() as s:
            # 最近交易日全市场涨跌
            r = await s.execute(text("""
                SELECT MAX(trade_date) FROM daily_kline
                WHERE ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%'
            """))
            latest = r.scalar()
            if not latest:
                return {"advance_pct": 50}

            r = await s.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE close > open) as up_count,
                    COUNT(*) as total
                FROM daily_kline
                WHERE trade_date = :d
            """), {"d": latest})
            row = r.fetchone()
            up_cnt = row[0] or 0
            total = row[1] or 1
            advance_pct = round(up_cnt / total * 100, 1)

            # 新高/新低 (近 20 日高点 = 近 60 日高点)
            r2 = await s.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE close >= prev_high * 0.98) as new_high,
                    COUNT(*) FILTER (WHERE close <= prev_low * 1.02) as new_low,
                    COUNT(*) as total2
                FROM (
                    SELECT ts_code, close,
                        MAX(high) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 60 PRECEDING AND 20 PRECEDING) as prev_high,
                        MIN(low) OVER (PARTITION BY ts_code ORDER BY trade_date ROWS BETWEEN 60 PRECEDING AND 20 PRECEDING) as prev_low
                    FROM daily_kline
                    WHERE trade_date = :d
                ) subq
                WHERE prev_high IS NOT NULL
            """), {"d": latest})
            row2 = r2.fetchone()
            nh = row2[0] or 0
            nl = row2[1] or 0
            tot2 = row2[2] or 1

            return {
                "advance_pct": advance_pct,
                "new_high_pct": round(nh / tot2 * 100, 1),
                "new_low_pct": round(nl / tot2 * 100, 1),
                "date": str(latest),
            }
    except Exception:
        return {"advance_pct": 50}


async def _get_volume_trend() -> dict:
    """两市成交额趋势."""
    try:
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT trade_date, SUM(amount) as total_amount
                FROM daily_kline
                WHERE (ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%')
                  AND trade_date >= CURRENT_DATE - 30
                GROUP BY trade_date
                ORDER BY trade_date DESC
            """))
            rows = [(row[0], float(row[1] or 0)) for row in r.fetchall()]

        if len(rows) < 10:
            return {"direction": "stable"}

        amounts = [r[1] for r in rows]
        recent_5 = sum(amounts[:5]) / 5
        prev_5 = sum(amounts[5:10]) / 5
        shrink_pct = round((recent_5 - prev_5) / prev_5 * 100, 1) if prev_5 > 0 else 0

        if shrink_pct > 10: direction = "expanding"
        elif shrink_pct < -20: direction = "shrinking"
        else: direction = "stable"

        return {
            "direction": direction,
            "shrink_pct": shrink_pct,
            "recent_5d_avg": round(recent_5 / 1e8, 0),  # 亿元
            "prev_5d_avg": round(prev_5 / 1e8, 0),
        }
    except Exception:
        return {"direction": "stable"}


async def _get_style_bias() -> dict:
    """风格判定: 沪深300 vs 中证1000 相对强度."""
    try:
        async with async_session_factory() as s:
            for idx_code, name in [("000300.SH", "hs300"), ("000852.SH", "csi1000")]:
                r = await s.execute(text("""
                    SELECT close FROM daily_kline
                    WHERE ts_code = :c ORDER BY trade_date DESC LIMIT 20
                """), {"c": idx_code})
                rows = [float(row[0] or 0) for row in r.fetchall()]

            if not rows:
                return {"bias": "unknown"}

            # 简化为: 查中证1000是否存在
            r = await s.execute(text("""
                SELECT close FROM daily_kline
                WHERE ts_code = '000852.SH' ORDER BY trade_date DESC LIMIT 5
            """))
            csi_rows = [float(row[0] or 0) for row in r.fetchall()]
            if not csi_rows:
                return {"bias": "large_cap", "note": "无中证1000数据"}

            r2 = await s.execute(text("""
                SELECT close FROM daily_kline
                WHERE ts_code = '000300.SH' ORDER BY trade_date DESC LIMIT 20
            """))
            hs_rows = [float(row[0] or 0) for row in r2.fetchall()]

        if len(csi_rows) >= 5 and len(hs_rows) >= 5:
            csi_5d = (csi_rows[0] / csi_rows[4] - 1) * 100
            hs_5d = (hs_rows[0] / hs_rows[4] - 1) * 100
            if csi_5d > hs_5d + 2:
                bias = "small_cap"
            elif hs_5d > csi_5d + 2:
                bias = "large_cap"
            else:
                bias = "balanced"
            return {"bias": bias, "csi1000_5d": round(csi_5d, 1), "hs300_5d": round(hs_5d, 1)}

        return {"bias": "unknown"}
    except Exception:
        return {"bias": "unknown"}


def _recommend_strategies(regime: str, style: dict) -> list[str]:
    """根据市场状态推荐合适的策略."""
    strategies = []
    bias = style.get("bias", "unknown")

    if regime == "趋势上涨":
        strategies = ["趋势追涨", "突破买入", "龙头加仓"]
    elif regime == "结构行情":
        strategies = ["板块轮动", "热点龙头", "强势回调"]
        if bias == "small_cap":
            strategies.append("小盘成长")
    elif regime == "缩量博弈":
        strategies = ["超跌反弹", "防御价值", "低吸不追"]
    elif regime == "恐慌杀跌":
        strategies = ["现金为王", "等待企稳", "分批建仓(左侧)"]
    elif regime == "维稳行情":
        strategies = ["权重护盘", "低波动", "高股息"]
    else:
        strategies = ["均衡配置", "高抛低吸", "波段操作"]

    return strategies


async def check_market_breadth(min_score: float = 40) -> dict:
    """检查市场广度 (兼容旧接口)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT COUNT(*) FROM analysis_scores
            WHERE scan_date = (SELECT MAX(scan_date) FROM analysis_scores)
              AND composite_score >= :ms
        """), {"ms": min_score})
        current = r.scalar() or 0

        r = await s.execute(text("""
            SELECT scan_date, COUNT(*) as cnt
            FROM analysis_scores
            WHERE scan_date IN (
                SELECT DISTINCT scan_date FROM analysis_scores
                ORDER BY scan_date DESC LIMIT 6
            ) AND composite_score >= :ms
            GROUP BY scan_date ORDER BY scan_date DESC
        """), {"ms": min_score})
        counts = [row[1] for row in r.fetchall()]

    if len(counts) < 3:
        return {"current": current, "trend": "stable", "warning": False}

    avg_prev = sum(counts[1:]) / len(counts[1:]) if len(counts) > 1 else current
    change_pct = (current - avg_prev) / avg_prev * 100 if avg_prev > 0 else 0

    return {
        "current": current, "avg_prev_5": round(avg_prev, 1),
        "change_pct": round(change_pct, 1), "warning": change_pct < -30,
    }


async def check_self_feedback() -> dict:
    """检查自反馈 (兼容旧接口)."""
    try:
        from app.services.accuracy_tracker import get_accuracy_stats
        stats = await get_accuracy_stats()
    except Exception:
        return {"recent_win_rate": None, "tighten": False, "reason": "no_data"}

    t3 = stats.get("T+3", {})
    wr_3d = t3.get("win_rate")
    if wr_3d is None or t3.get("verified", 0) < 10:
        return {"recent_win_rate": None, "tighten": False, "reason": "no_data"}

    wr_ratio = wr_3d / 100.0
    tighten = wr_ratio < 0.30
    return {
        "recent_win_rate": round(wr_ratio, 3), "tighten": tighten,
        "reason": f"T+3胜率{wr_3d:.0f}%{'<30%, 收紧阈值' if tighten else '正常'}",
    }


async def get_gate_config() -> dict:
    """获取当前门控配置 — v2.0 多维合并."""
    config = dict(DEFAULT_GATE)
    config["adjustments"] = []

    market = await get_market_state()
    risk = market.get("risk", "normal")
    regime = market.get("regime", "震荡整理")
    config["market_risk"] = risk
    config["market_regime"] = regime
    config["suitable_strategies"] = market.get("suitable_strategies", [])
    config["breadth"] = market.get("breadth", {})
    config["volume_trend"] = market.get("volume_trend", {})
    config["style"] = market.get("style", {})

    # 根据 regime 调整
    regime_gates = {
        "趋势上涨": (0.28, 120),
        "结构行情": (0.30, 100),
        "缩量博弈": (0.38, 60),
        "恐慌杀跌": (0.45, 40),
        "维稳行情": (0.35, 80),
        "弱势探底": (0.42, 50),
        "震荡整理": (0.32, 90),
    }
    min_prob, max_stk = regime_gates.get(regime, (0.35, 80))
    config["min_probability"] = min_prob
    config["max_stocks"] = max_stk

    if risk == "high":
        config["min_probability"] = min(0.45, config["min_probability"] + 0.05)
        config["max_stocks"] = min(50, config["max_stocks"])
    elif risk == "elevated":
        config["min_probability"] = min(0.40, config["min_probability"] + 0.03)

    # 缩量博弈额外收紧
    vt = market.get("volume_trend", {})
    if vt.get("direction") == "shrinking" and vt.get("shrink_pct", 0) < -30:
        config["max_stocks"] = min(40, config["max_stocks"])
        config["adjustments"].append(f"成交额持续萎缩{abs(vt['shrink_pct']):.0f}%, 大幅收紧")

    feedback = await check_self_feedback()
    config["feedback"] = feedback
    if feedback.get("tighten"):
        config["min_probability"] = min(0.45, config["min_probability"] + 0.08)
        config["adjustments"].append(feedback["reason"])

    # ★ 空仓判定 (任务三 v4.3): 恐慌杀跌 + 近期胜率<25% + 上涨家数<20% → 强制空仓
    force_empty = False
    if regime == "恐慌杀跌":
        fb = config.get("feedback", {})
        recent_wr = fb.get("recent_win_rate")
        breadth = market.get("breadth", {})
        adv_pct = breadth.get("adv_pct", 50)
        if recent_wr is not None and recent_wr < 0.25 and adv_pct < 20:
            force_empty = True
            config["adjustments"].append(
                f"⚠ 强制空仓: 恐慌杀跌 + 胜率{recent_wr:.0%} + 上涨{adv_pct:.0f}%"
            )
    config["force_empty"] = force_empty

    config["adjustments"].append(f"市场: {regime}(风险: {risk})")
    b = market.get("breadth", {})
    s = market.get("style", {})
    v = market.get("volume_trend", {})
    config["adjustments"].append(
        f"涨跌比: 上涨{b.get('advance_pct','?')}% | "
        f"风格: {s.get('bias','?')} | "
        f"成交额: {v.get('direction','?')}"
    )

    breadth = await check_market_breadth()
    config["breadth_stats"] = breadth
    if breadth.get("warning"):
        config["min_probability"] = min(0.45, config["min_probability"] + 0.05)
        config["adjustments"].append(f"市场广度骤降{breadth['change_pct']:.0f}%")

    logger.info(f"Gate v2.0: regime={regime}, risk={risk}, "
                f"min_prob={config['min_probability']:.0%}, max={config['max_stocks']}")

    # ★ Phase 31: 阈值自适应 — 从真实验证数据自动调整推荐门槛
    try:
        async with async_session_factory() as s:
            config["adaptive"] = await _get_adaptive_thresholds(s)
    except Exception:
        config["adaptive"] = {"status": "error", "fallback": "hardcoded"}

    return config


async def _get_adaptive_thresholds(session) -> dict:
    """Phase 31: 从 signal_history 统计各分数段真实胜率，自适应调整门控阈值.

    最低 50 条验证样本才信任该分数段数据.
    每天 16:00 get_gate_config() 时自动调用，阈值随数据积累动态收敛.

    优先使用 signal_history (已验证的历史信号)，其次使用 recommendation_tracking。
    """
    MIN_SAMPLES = 50

    # 优先从 signal_history 获取数据（更丰富的历史验证）
    r = await session.execute(text("""
        SELECT
            FLOOR(composite_score / 5) * 5 AS score_bucket,
            COUNT(*) AS n,
            AVG(ret_t5) AS avg_ret,
            SUM(CASE WHEN outcome_label IN ('strong_win', 'weak_win') THEN 1 ELSE 0 END)::float
                / NULLIF(COUNT(*), 0) AS win_rate
        FROM signal_history
        WHERE ret_t5 IS NOT NULL
          AND scan_date >= CURRENT_DATE - 180
        GROUP BY score_bucket
        ORDER BY score_bucket DESC
    """))
    buckets = [
        {"score": int(row[0]), "n": int(row[1]),
         "avg_ret": round(float(row[2] or 0), 2),
         "win_rate": round(float(row[3] or 0), 3)}
        for row in r.fetchall() if int(row[1]) >= MIN_SAMPLES
    ]

    # 如果 signal_history 没有足够数据，尝试 recommendation_tracking
    if not buckets:
        r = await session.execute(text("""
            SELECT
                FLOOR(composite_score / 5) * 5 AS score_bucket,
                COUNT(*) AS n,
                AVG(return_2d) AS avg_ret,
                SUM(CASE WHEN was_profitable_2d THEN 1 ELSE 0 END)::float
                    / NULLIF(COUNT(*), 0) AS win_rate
            FROM recommendation_tracking
            WHERE verified_2d = true AND scan_date >= CURRENT_DATE - 60
            GROUP BY score_bucket
            ORDER BY score_bucket DESC
        """))
        buckets = [
            {"score": int(row[0]), "n": int(row[1]),
             "avg_ret": round(float(row[2] or 0), 2),
             "win_rate": round(float(row[3] or 0), 3)}
            for row in r.fetchall() if int(row[1]) >= MIN_SAMPLES
        ]
        data_source = "recommendation_tracking"
    else:
        data_source = "signal_history"

    if not buckets:
        return {"status": "insufficient_data", "fallback": "hardcoded",
                "note": f"< {MIN_SAMPLES} verified per bucket (tried signal_history + recommendation_tracking)"}

    # 找到胜率 > 50% 的最低分数段 → 推荐阈值
    rec_threshold = 40
    for b in sorted(buckets, key=lambda x: x["score"]):
        if b["win_rate"] >= 0.50:
            rec_threshold = b["score"]
            break

    # 找到胜率 > 60% 的最低分数段 → 强买阈值
    strong_threshold = 55
    for b in sorted(buckets, key=lambda x: x["score"]):
        if b["win_rate"] >= 0.60:
            strong_threshold = b["score"]
            break

    return {
        "status": "adaptive",
        "data_source": data_source,
        "samples_per_bucket": MIN_SAMPLES,
        "buckets": len(buckets),
        "min_score": rec_threshold,
        "strong_buy": strong_threshold,
        "total_verified": sum(b["n"] for b in buckets),
        "bucket_details": buckets,
    }
