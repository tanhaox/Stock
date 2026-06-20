"""概率校准器 — 将 composite_score 映射为 P(T+2 > 0) 胜率.

按原型分组校准，因为不同原型的分数分布不同。
使用历史 analysis_scores + daily_kline 构建分段桶校准曲线。
"""
import asyncio, json, logging
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# 缓存 + 线程安全锁
_calibration_cache: dict[str, list[tuple]] = {}
_cache_lock = asyncio.Lock()
_cache_loaded = False


async def build_calibration(lookback_days: int = 180) -> dict:
    """从历史数据构建校准曲线.

    Returns:
        {archetype: {"buckets": [...], "monotonic": bool}}
    """
    from datetime import date as dt_date
    cutoff = dt_date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        # 获取所有 analysis_scores 历史记录
        r = await s.execute(text("""
            SELECT a.symbol, a.scan_date, a.composite_score, a.archetype
            FROM analysis_scores a
            WHERE a.scan_date >= :cutoff
              AND a.composite_score IS NOT NULL
            ORDER BY a.scan_date, a.symbol
        """), {"cutoff": cutoff})
        rows = r.fetchall()

    if not rows:
        logger.warning("No analysis_scores data for calibration")
        return {}

    # 获取交易日历
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline "
            "WHERE trade_date >= :cutoff ORDER BY trade_date"
        ), {"cutoff": cutoff})
        trading_days = [str(row[0]) for row in r.fetchall()]

    if len(trading_days) < 3:
        return {}

    # 预加载所有相关日期的价格
    symbols = list(set(row[0] for row in rows))
    date_set = set()
    date_objs = set()
    for row in rows:
        scan_date = row[1]  # date object
        scan_date_str = str(scan_date)
        try:
            idx = trading_days.index(scan_date_str)
            # 审计修复 (任务三): T+3 标签 — 与 scoring_trainer 的 was_profitable_3d 统一时间轴
            if idx + 3 < len(trading_days):
                date_objs.add(scan_date)
                t3_str = trading_days[idx + 3]
                date_objs.add(date.fromisoformat(t3_str))
        except ValueError:
            continue

    date_list = sorted(date_objs)

    # 批量加载价格
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, close FROM daily_kline
            WHERE ts_code = ANY(:syms)
              AND trade_date = ANY(:dts)
        """), {"syms": symbols, "dts": date_list})
        prices = {}
        for row in r.fetchall():
            prices[(row[0], str(row[1]))] = float(row[2])

    # 按原型分组构建桶
    archetype_data = defaultdict(list)
    for row in rows:
        sym, scan_date, score, arch = row[0], str(row[1]), float(row[2] or 0), row[3] or "unknown"
        try:
            idx = trading_days.index(scan_date)
            t3_day = trading_days[idx + 3] if idx + 3 < len(trading_days) else None
        except ValueError:
            continue
        if not t3_day:
            continue

        entry_price = prices.get((sym, scan_date))
        exit_price = prices.get((sym, t3_day))
        if entry_price and exit_price and entry_price > 0:
            ret = (exit_price - entry_price) / entry_price
            archetype_data[arch].append((score, ret > 0))

    # 构建桶校准 (每 5 分一个桶)
    result = {}
    for arch, data in archetype_data.items():
        if len(data) < 10:
            continue
        buckets = defaultdict(list)
        for score, is_win in data:
            bucket_key = int(score // 5) * 5
            buckets[bucket_key].append(is_win)

        bucket_list = []
        for lo in sorted(buckets.keys()):
            samples = buckets[lo]
            wr = sum(1 for w in samples if w) / len(samples)
            bucket_list.append({
                "score_min": lo, "score_max": lo + 5,
                "win_rate": round(wr, 4), "samples": len(samples),
            })

        # 检查单调性
        win_rates = [b["win_rate"] for b in bucket_list]
        monotonic = all(win_rates[i] <= win_rates[i+1] for i in range(len(win_rates)-1))

        result[arch] = {
            "buckets": bucket_list,
            "monotonic": monotonic,
            "total_samples": len(data),
        }
        logger.info(f"Calibration [{arch}]: {len(bucket_list)} buckets, "
                    f"{len(data)} samples, monotonic={monotonic}")

    return result


def calibrate(composite_score: float, archetype: str = "unknown",
              calibration: dict = None, signal_quality: float = None) -> float:
    """将裸分映射为胜率概率 (v2: 禁止0% + 信质融合 + 小样本降权).

    Args:
        composite_score: 0-100 的复合评分
        archetype: 原型名
        calibration: build_calibration() 的输出
        signal_quality: 信号质量 0-1 (可选, 用于降低不确定度高分的置信度)

    Returns:
        0.05 ~ 0.78 的概率值 (硬底 5%, 硬顶 78%)
    """
    cal = calibration or _calibration_cache
    arch_cal = cal.get(archetype, cal.get("unknown", {}))

    buckets = arch_cal.get("buckets", [])
    score = max(0, min(100, composite_score))

    # ── 无桶: 纯 Sigmoid ──
    if not buckets:
        return _fallback_sigmoid(score, signal_quality)

    # ── 找匹配桶 ──
    bucket_wr = None
    bucket_n = 0
    for bucket in buckets:
        if bucket["score_min"] <= score < bucket["score_max"]:
            bucket_wr = bucket["win_rate"]
            bucket_n = bucket.get("samples", 0)
            break

    if bucket_wr is None:
        if score >= buckets[-1]["score_max"]:
            bucket_wr = buckets[-1]["win_rate"]
            bucket_n = buckets[-1].get("samples", 0)
        else:
            bucket_wr = buckets[0]["win_rate"]
            bucket_n = buckets[0].get("samples", 0)

    # ── Sigmoid 基线 ──
    sigmoid_wr = _fallback_sigmoid(score)

    # ── 小样本降权: bucket样本越少, sigmoid权重越高 ──
    #     10个样本=100%信任桶, 0个样本=100%信任sigmoid
    MIN_SAMPLES = 10
    bucket_weight = min(1.0, bucket_n / MIN_SAMPLES)
    sigmoid_weight = 1.0 - bucket_weight
    blended_wr = bucket_wr * bucket_weight + sigmoid_wr * sigmoid_weight

    # ── 硬底: 禁止返回 0% (评分>20分不可能胜率为0) ──
    if score > 20:
        blended_wr = max(blended_wr, 0.08)
    if score > 35:
        blended_wr = max(blended_wr, 0.15)

    # ── 信号质量修正: 高质量信号增加置信度向均值靠拢 ──
    if signal_quality is not None and signal_quality > 0:
        # sq越高, 概率向 0.5 靠拢(更确信有边可做)
        # sq越低, 概率保持原值(高不确定)
        sq_factor = min(1.0, signal_quality)
        blended_wr = blended_wr * (1 - sq_factor * 0.3) + 0.5 * sq_factor * 0.3

    return round(min(0.78, max(0.05, blended_wr)), 4)


def _fallback_sigmoid(score: float, signal_quality: float = None) -> float:
    """Sigmoid 近似: 分越高概率越高, 硬底5%硬顶65%."""
    import math
    if score < 20:
        return 0.05
    try:
        base = 0.10 + 0.55 / (1 + math.exp(-(score - 50) / 15))
        return round(min(0.65, max(0.05, base)), 4)
    except OverflowError:
        return 0.60


async def get_cached_calibration(force_rebuild: bool = False) -> dict:
    """获取缓存的校准数据，必要时重建(线程安全)."""
    global _calibration_cache, _cache_loaded
    async with _cache_lock:
        if _cache_loaded and not force_rebuild:
            return _calibration_cache
        _calibration_cache = await build_calibration()
        _cache_loaded = True
        return _calibration_cache


async def scheduled_recalibrate() -> dict:
    """定期重校准入口 — 由 daily_task 调用 (每周日执行).

    解决"校准曲线可能过时"问题:
      - 每周重建一次校准曲线, 基于最新 180 天数据
      - 校准参数随市场演进自动更新
    """
    global _calibration_cache, _cache_loaded
    logger.info("Scheduled recalibration: rebuilding calibration curves...")
    try:
        new_cal = await build_calibration(lookback_days=180)
        async with _cache_lock:
            _calibration_cache = new_cal
            _cache_loaded = True
        archetypes = list(new_cal.keys())
        total_samples = sum(v.get("total_samples", 0) for v in new_cal.values())
        logger.info(f"Recalibration complete: {len(archetypes)} archetypes, {total_samples} total samples")
        return {"status": "success", "archetypes": len(archetypes), "total_samples": total_samples}
    except Exception as e:
        logger.warning(f"Recalibration failed: {e}")
        return {"status": "error", "reason": str(e)}


# ── 便捷函数：从 DB 直接查单只股票的概率 ──


# ── Regime-aware 分段校准 (审计修复: 任务三) ──

_regime_calibration_cache: dict[str, dict] = {}  # {regime: {archetype: {buckets, ...}}}
_regime_cache_lock = asyncio.Lock()
_regime_cache_loaded = False


async def build_calibration_by_regime(lookback_days: int = 180) -> dict:
    """按 bull/bear/range 分段构建校准曲线.

    与 scoring_trainer 的分段训练对齐: 同一个 composite_score 在不同市场状态下
    对应不同的实际胜率, 因此需要独立的校准器.

    现阶段: 当某个 regime 样本 < 100 时, 回退到全局校准器。
    数据积累足够后自动启用分段校准。
    """
    from datetime import date as dt_date, timedelta
    cutoff = dt_date.today() - timedelta(days=lookback_days)
    MIN_REGIME_CAL_SAMPLES = 100

    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT a.symbol, a.scan_date, a.composite_score, a.archetype,
                   COALESCE(ms.phase, 'unknown') as market_phase
            FROM analysis_scores a
            LEFT JOIN market_status_log ms ON ms.trade_date = a.scan_date
            WHERE a.scan_date >= :cutoff
              AND a.composite_score IS NOT NULL
            ORDER BY a.scan_date, a.symbol
        """), {"cutoff": cutoff})
        rows = r.fetchall()

    if not rows:
        logger.warning("No data for regime-aware calibration")
        return {}

    # 获取交易日历
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT trade_date FROM daily_kline "
            "WHERE trade_date >= :cutoff ORDER BY trade_date"
        ), {"cutoff": cutoff})
        trading_days = [str(row[0]) for row in r.fetchall()]

    if len(trading_days) < 5:
        return {}

    # 预加载价格
    symbols = list(set(row[0] for row in rows))
    date_objs = set()
    for row in rows:
        scan_date_str = str(row[1])
        try:
            idx = trading_days.index(scan_date_str)
            if idx + 3 < len(trading_days):
                date_objs.add(row[1])
                date_objs.add(date.fromisoformat(trading_days[idx + 3]))
        except ValueError:
            continue

    date_list = sorted(date_objs)
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, close FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date = ANY(:dts)
        """), {"syms": symbols, "dts": date_list})
        prices = {}
        for row in r.fetchall():
            prices[(row[0], str(row[1]))] = float(row[2])

    # 按 (regime, archetype) 分组
    regime_arch_data: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        sym, scan_date, score, arch, phase = row[0], str(row[1]), float(row[2] or 0), row[3] or "unknown", row[4] or ""
        try:
            idx = trading_days.index(scan_date)
            t3_day = trading_days[idx + 3] if idx + 3 < len(trading_days) else None
        except ValueError:
            continue
        if not t3_day:
            continue

        entry_price = prices.get((sym, scan_date))
        exit_price = prices.get((sym, t3_day))
        if not entry_price or not exit_price or entry_price <= 0:
            continue

        ret = (exit_price - entry_price) / entry_price

        # 市场阶段 → regime
        phase_lower = (phase or "").lower()
        if "牛" in phase_lower or "bull" in phase_lower:
            regime = "bull"
        elif "熊" in phase_lower or "bear" in phase_lower:
            regime = "bear"
        else:
            regime = "range"

        regime_arch_data[regime][arch].append((score, ret > 0))

    # 对每个 regime 构建桶 (与 build_calibration 相同逻辑)
    result = {}
    for regime, arch_data in regime_arch_data.items():
        # 检查该 regime 的总样本量
        total_regime_samples = sum(len(v) for v in arch_data.values())
        if total_regime_samples < MIN_REGIME_CAL_SAMPLES:
            logger.info(
                f"Regime [{regime}] calibration: {total_regime_samples} samples "
                f"< {MIN_REGIME_CAL_SAMPLES}, falling back to global calibrator"
            )
            continue

        regime_result = {}
        for arch, data in arch_data.items():
            if len(data) < 10:
                continue
            buckets = defaultdict(list)
            for score, is_win in data:
                bucket_key = int(score // 5) * 5
                buckets[bucket_key].append(is_win)

            bucket_list = []
            for lo in sorted(buckets.keys()):
                samples = buckets[lo]
                wr = sum(1 for w in samples if w) / len(samples)
                bucket_list.append({
                    "score_min": lo, "score_max": lo + 5,
                    "win_rate": round(wr, 4), "samples": len(samples),
                })

            win_rates = [b["win_rate"] for b in bucket_list]
            monotonic = all(win_rates[i] <= win_rates[i+1] for i in range(len(win_rates)-1))

            regime_result[arch] = {
                "buckets": bucket_list,
                "monotonic": monotonic,
                "total_samples": len(data),
            }

        if regime_result:
            result[regime] = regime_result
            logger.info(
                f"Regime calibration [{regime}]: {len(regime_result)} archetypes, "
                f"{total_regime_samples} total samples"
            )

    return result


def calibrate_with_regime(composite_score: float, archetype: str = "unknown",
                           regime: str = None, signal_quality: float = None) -> float:
    """Regime-aware 概率校准.

    审计修复 (任务三): 按市场状态加载对应的校准器。
    如果 regime 校准器不可用, 自动回退到全局校准器。

    Args:
        composite_score: 0-100 复合评分
        archetype: 原型名
        regime: 市场状态 (bull/bear/range/None)
        signal_quality: 信号质量 0-1

    Returns:
        0.05 ~ 0.78 的概率值
    """
    if regime and regime in _regime_calibration_cache:
        cal = _regime_calibration_cache.get(regime, {})
        if cal:  # 该 regime 有可用校准器
            return calibrate(composite_score, archetype, cal, signal_quality)
    # 回退到全局校准器
    return calibrate(composite_score, archetype, None, signal_quality)


async def scheduled_recalibrate_with_regime() -> dict:
    """定期分段重校准 — 重建全局 + regime 校准器 (每周日由 daily_task 调用).

    Returns:
        {global, regimes: {bull/bear/range: {archetypes, total_samples}}}
    """
    global _regime_calibration_cache, _regime_cache_loaded
    logger.info("Scheduled recalibration (with regime): rebuilding all calibration curves...")

    # 1. 重建全局校准器
    global_result = await scheduled_recalibrate()

    # 2. 重建 regime 校准器
    try:
        new_regime_cal = await build_calibration_by_regime(lookback_days=180)
        async with _regime_cache_lock:
            _regime_calibration_cache = new_regime_cal
            _regime_cache_loaded = True
        regime_summary = {
            r: {"archetypes": len(cal), "total_samples": sum(v.get("total_samples", 0) for v in cal.values())}
            for r, cal in new_regime_cal.items()
        }
        logger.info(f"Regime recalibration: {regime_summary}")
    except Exception as e:
        logger.warning(f"Regime recalibration failed (global still active): {e}")
        regime_summary = {"error": str(e)}

    return {"global": global_result, "regimes": regime_summary}


# ── 内部辅助 ──
