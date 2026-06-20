"""推荐准确率追踪 — 回填历史推荐的实际收益, 计算胜率."""
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# 交易日缓存
_trading_days_cache: list[date] = []
_trading_days_loaded = False


async def _load_trading_days():
    """加载交易日历 (从 daily_kline 提取)."""
    global _trading_days_cache, _trading_days_loaded
    if _trading_days_loaded:
        return _trading_days_cache
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline WHERE trade_date >= CURRENT_DATE - INTERVAL '540 days' ORDER BY trade_date"
        ))
        _trading_days_cache = [row[0] for row in r.fetchall()]
        _trading_days_loaded = True
    return _trading_days_cache


def _trading_day_offset(from_date: date, offset_days: int, trading_days: list[date]) -> date | None:
    """计算交易日偏移: +N = 第N个交易日之后, -N = 第N个交易日之前."""
    if from_date not in trading_days:
        # 找之后第一个交易日
        for td in trading_days:
            if td >= from_date:
                from_date = td
                break
    try:
        idx = trading_days.index(from_date)
        target_idx = idx + offset_days
        if 0 <= target_idx < len(trading_days):
            return trading_days[target_idx]
    except ValueError:
        pass
    return None


async def verify_recommendations(horizon_days: int = 5):
    """回填推荐结果的实际收益.

    对 recommendation_tracking 中未验证的记录, 从 daily_kline 查 horizon_days 后的收盘价,
    计算收益率并写入.
    """
    col_return = f"return_{horizon_days}d"
    col_profitable = f"was_profitable_{horizon_days}d"
    col_verified = f"verified_{horizon_days}d"

    tdays = await _load_trading_days()
    today = date.today()
    # 交易日截止: 今天往前推 horizon_days 个交易日
    cutoff = _trading_day_offset(today, -horizon_days, tdays)
    if not cutoff:
        return {"verified": 0, "message": f"无法计算 T+{horizon_days} 截止日"}

    async with async_session_factory() as s:
        r = await s.execute(text(f"""
            SELECT rt.scan_date, rt.symbol, rt.close_price
            FROM recommendation_tracking rt
            WHERE rt.{col_verified} = FALSE
              AND rt.scan_date <= :cutoff
              AND rt.close_price > 0
            ORDER BY rt.scan_date
            LIMIT 500
        """), {"cutoff": cutoff})
        pending = [(row[0], row[1], float(row[2] or 0)) for row in r.fetchall()]

    if not pending:
        return {"verified": 0, "message": f"无待验证的 T+{horizon_days} 记录"}

    verified = 0
    profitable = 0
    for scan_date, symbol, buy_price in pending:
        # 交易日偏移: scan_date + horizon_days 个交易日
        target_date = _trading_day_offset(scan_date, horizon_days, tdays)
        if not target_date:
            continue
        async with async_session_factory() as s:
            r = await s.execute(text("""
                SELECT close FROM daily_kline
                WHERE ts_code = :sym AND trade_date BETWEEN :d1 AND :d2
                ORDER BY trade_date LIMIT 1
            """), {"sym": symbol, "d1": target_date, "d2": target_date + timedelta(days=3)})
            row = r.fetchone()

        if not row or buy_price <= 0:
            continue

        sell_price = float(row[0] or 0)
        if sell_price <= 0:
            continue

        ret = round((sell_price - buy_price) / buy_price * 100, 2)
        is_profitable = ret > 0

        async with async_session_factory() as s:
            await s.execute(text(f"""
                UPDATE recommendation_tracking SET
                    {col_return} = :ret, {col_profitable} = :prof, {col_verified} = TRUE, updated_at = NOW()
                WHERE scan_date = :d AND symbol = :s
            """), {"ret": ret, "prof": is_profitable, "d": scan_date, "s": symbol})
            await s.commit()

        verified += 1
        if is_profitable:
            profitable += 1

    logger.info(f"Verified T+{horizon_days}: {verified} records, {profitable} profitable "
                f"({round(profitable/max(verified,1)*100,1)}%)")
    return {"verified": verified, "profitable": profitable,
            "win_rate": round(profitable / max(verified, 1) * 100, 1)}


async def get_accuracy_stats(days_back: int = 30) -> dict:
    """获取近期推荐准确率统计."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE verified_3d) as v3,
                COUNT(*) FILTER (WHERE was_profitable_3d) as p3,
                COUNT(*) FILTER (WHERE verified_5d) as v5,
                COUNT(*) FILTER (WHERE was_profitable_5d) as p5,
                COUNT(*) FILTER (WHERE verified_15d) as v15,
                COUNT(*) FILTER (WHERE was_profitable_15d) as p15
            FROM recommendation_tracking
            WHERE scan_date >= :cutoff
        """), {"cutoff": date.today() - timedelta(days=days_back)})
        row = r.fetchone()
        return {
            "T+3": {"verified": row[0] or 0, "profitable": row[1] or 0,
                    "win_rate": round((row[1] or 0) / max(row[0] or 1, 1) * 100, 1)},
            "T+5": {"verified": row[2] or 0, "profitable": row[3] or 0,
                    "win_rate": round((row[3] or 0) / max(row[2] or 1, 1) * 100, 1)},
            "T+15": {"verified": row[4] or 0, "profitable": row[5] or 0,
                     "win_rate": round((row[5] or 0) / max(row[4] or 1, 1) * 100, 1)},
        }


async def verify_all_periods():
    """验证所有周期的推荐结果."""
    results = {}
    for horizon in [3, 5, 15]:
        r = await verify_recommendations(horizon)
        results[f"T+{horizon}"] = r
    return results


# ── 闭环: 胜率反馈到权重系统 ──────────────────

async def apply_accuracy_feedback(min_samples: int = 20):
    """根据近期推荐准确率调整现实层权重的 discrimination 值.

    逻辑:
      T+5 胜率 > 55% → 当前权重有效, discrimination × 1.05 (加强)
      T+5 胜率 < 45% → 当前权重偏差, discrimination × 0.90 (衰减)
      T+5 胜率 < 35% → 严重偏差, 触发自动回滚到上一个版本
      T+5 样本不足 → 降级使用 T+3 胜率 (需 ≥30 样本)
      T+3 也不足 → 跳过 (数据积累中)
    """
    from datetime import date as dt_date
    from app.services.accuracy_tracker import get_accuracy_stats

    stats = await get_accuracy_stats(days_back=30)
    t5 = stats.get("T+5", {})
    t3 = stats.get("T+3", {})

    # 优先 T+5, 不足时降级到 T+3
    if t5.get("verified", 0) >= min_samples:
        win_rate = t5["win_rate"]
        period = "T+5"
    elif t3.get("verified", 0) >= 30:
        win_rate = t3["win_rate"]
        period = "T+3"
        logger.info(f"Accuracy feedback: T+5 samples insufficient ({t5.get('verified', 0)}), "
                    f"falling back to T+3 (n={t3['verified']}, WR={win_rate}%)")
    else:
        logger.info(f"Accuracy feedback: insufficient samples "
                    f"(T+5={t5.get('verified', 0)}, T+3={t3.get('verified', 0)}), skip")
        return {"action": "skip", "reason": f"样本不足 (T+5={t5.get('verified', 0)}, T+3={t3.get('verified', 0)})"}

    logger.info(f"Accuracy feedback: {period} win_rate={win_rate}% ({t5['profitable']}/{t5['verified']})")

    async with async_session_factory() as s:
        if win_rate >= 55:
            # 加强: 提升所有活跃现实层权重的 discrimination
            await s.execute(text("""
                UPDATE param_library SET discrimination = LEAST(discrimination * 1.05, 1.0),
                    updated_at = NOW()
                WHERE is_shadow = false AND is_active = true
            """))
            await s.commit()
            return {"action": "boost", "win_rate": win_rate, "detail": "discrimination × 1.05"}

        elif win_rate >= 45:
            return {"action": "hold", "win_rate": win_rate, "detail": "胜率正常，维持"}

        elif win_rate >= 35:
            # 轻微衰减
            await s.execute(text("""
                UPDATE param_library SET discrimination = GREATEST(discrimination * 0.90, 0.1),
                    updated_at = NOW()
                WHERE is_shadow = false AND is_active = true
            """))
            await s.commit()
            return {"action": "penalize", "win_rate": win_rate, "detail": "discrimination × 0.90"}

        else:
            # 严重偏差: 自动回滚
            r = await s.execute(text("""
                SELECT archetype, strategy, version, parent_version
                FROM param_library WHERE is_shadow = false AND is_active = true
                ORDER BY created_at DESC LIMIT 5
            """))
            rolled = []
            for row in r.fetchall():
                arch, st, ver, parent = row[0], row[1], row[2], row[3]
                if parent and parent != "initial":
                    # 回滚到父版本
                    await s.execute(text(
                        "UPDATE param_library SET is_active = false WHERE archetype=:a AND strategy=:st AND is_shadow=false"
                    ), {"a": arch, "st": st})
                    await s.execute(text(
                        "UPDATE param_library SET is_active = true WHERE archetype=:a AND strategy=:st AND version=:pv"
                    ), {"a": arch, "st": st, "pv": parent})
                    rolled.append(f"{arch}/{st} → {parent}")
            await s.commit()
            return {"action": "rollback", "win_rate": win_rate, "rolled": rolled, "detail": f"回滚 {len(rolled)} 个权重"}
