"""原型参数解析器 — 将策略原型映射为评分权重偏移.

每个原型有独立的:
  - 子评分权重覆盖(tech_weight, fund_weight 等)
  - TG 阈值调整(收紧/放宽)
  - Bayesian 信念继承优先级

权重偏移逻辑:
  base_weight * (1.0 + archetype_offset)
  例如: tech_weight = 3.0 * (1.0 + 0.2) = 3.6 (小盘题材)

设计原则:
  - 大盘蓝筹: 基本面权重 +40%, 技术面 -20%, 估值 +20%
  - 小盘题材: 技术面 +30%, 基本面 -30%, TG 动量 +50%
  - 成长科技: 均衡 +10%, 估值 -10%
  - 价值防御: 估值 +40%, 基本面 +20%, 动量 -30%
  - 周期资源: 杠杆 +50%, 基本面 -20%, 跨市场 +30%
"""
import logging
from app.services.bayesian_optimizer import DEFAULT_BELIEFS, PARAM_GROUPS

logger = logging.getLogger(__name__)

# ── 校准覆盖缓存 (由 apply_calibration 填充, resolve_weights 读取) ──
_override_cache: dict[str, dict[str, float]] = {}

# ── 原型权重偏移表(相对 DEFAULT_BELIEFS 的比例偏移)──
# ⚠️ 待校准 (2026-06-03): 以下偏移量全部为初始设计值,
#    从未基于真实盈亏数据回测校准。
#    每个原型的 ±N% 应该由该原型的历史推荐胜率决定,
#    而非领域直觉。
#    校准方法: 按原型分组统计 recommendation_tracking 的 was_profitable_3d,
#             用各原型的实际胜率 vs 全局胜率的差值作为偏移基准。
ARCHETYPE_OFFSETS: dict[str, dict[str, float]] = {
    "large_bluechip": {
        "tech_weight": -0.20,
        "kline_weight": -0.15,
        "fund_weight": -0.10,
        "fundamentals_weight": +0.40,
        "valuation_weight": +0.20,
        "real_fund_weight": +0.15,
        "northbound_weight": +0.30,
        "institutional_weight": +0.25,
        "shareholder_weight": +0.10,
        "tg_momentum_mult": -0.15,
        "dist_low_mult": -0.10,
        "j_value_mult": -0.10,
        "vol_ratio_mult": -0.20,
        "buy_strength_mult": -0.10,
        "tg_momentum_weight": -0.20,
        "ma_trend_weight": +0.20,
        "pattern_weight": +0.15,
    },
    "small_speculative": {
        "tech_weight": +0.30,
        "kline_weight": +0.20,
        "fund_weight": +0.25,
        "fundamentals_weight": -0.30,
        "valuation_weight": -0.10,
        "real_fund_weight": +0.10,
        "northbound_weight": -0.20,
        "institutional_weight": -0.15,
        "shareholder_weight": -0.10,
        "tg_momentum_mult": +0.30,
        "dist_low_mult": +0.20,
        "j_value_mult": +0.25,
        "vol_ratio_mult": +0.35,
        "buy_strength_mult": +0.25,
        "tg_momentum_weight": +0.50,
        "ma_trend_weight": -0.10,
        "pattern_weight": +0.30,
    },
    "growth_tech": {
        "tech_weight": +0.10,
        "fund_weight": +0.10,
        "fundamentals_weight": +0.10,
        "valuation_weight": -0.10,
        "northbound_weight": +0.10,
        "tg_momentum_mult": +0.10,
        "j_value_mult": +0.10,
        "vol_ratio_mult": +0.15,
        "ma_trend_weight": +0.05,
        "pattern_weight": +0.20,
    },
    "value_defensive": {
        "tech_weight": -0.10,
        "fund_weight": -0.15,
        "fundamentals_weight": +0.20,
        "valuation_weight": +0.40,
        "real_fund_weight": +0.10,
        "northbound_weight": +0.15,
        "institutional_weight": +0.20,
        "shareholder_weight": +0.15,
        "tg_momentum_mult": -0.25,
        "dist_low_mult": -0.15,
        "j_value_mult": -0.20,
        "vol_ratio_mult": -0.25,
        "buy_strength_mult": -0.15,
        "tg_momentum_weight": -0.30,
        "ma_trend_weight": +0.15,
        "pattern_weight": +0.10,
    },
    "cyclical_resource": {
        "fundamentals_weight": -0.20,
        "valuation_weight": +0.10,
        "real_fund_weight": +0.30,
        "northbound_weight": +0.10,
        "tg_momentum_mult": +0.15,
        "vol_ratio_mult": +0.20,
        "buy_strength_mult": +0.15,
        "ma_trend_weight": +0.10,
        "pattern_weight": +0.25,
    },
}

# ── TG 阈值调整 ──────────────────────────────────

ARCHETYPE_TG_THRESHOLDS = {
    "large_bluechip": {
        "min_buy_strength": 0.35,    # 大盘更难触发 → 收紧买入强度阈值
        "min_composite_score": 55,   # 降低最低分门槛
        "min_tg_momentum": -1.0,     # 放宽 TG 动量
        "max_dist_low": 25.0,        # 放宽距低点
    },
    "small_speculative": {
        "min_buy_strength": 0.55,    # 小盘易波动 → 提高门槛
        "min_composite_score": 65,
        "min_tg_momentum": 1.5,
        "max_dist_low": 15.0,
    },
    "growth_tech": {
        "min_buy_strength": 0.45,
        "min_composite_score": 60,
        "min_tg_momentum": 0.5,
        "max_dist_low": 20.0,
    },
    "value_defensive": {
        "min_buy_strength": 0.30,
        "min_composite_score": 50,
        "min_tg_momentum": -0.5,
        "max_dist_low": 30.0,
    },
    "cyclical_resource": {
        "min_buy_strength": 0.40,
        "min_composite_score": 58,
        "min_tg_momentum": 0.0,
        "max_dist_low": 22.0,
    },
}

DEFAULT_THRESHOLDS = {
    "min_buy_strength": 0.40,
    "min_composite_score": 55,
    "min_tg_momentum": 0.0,
    "max_dist_low": 20.0,
}


# ── 主函数 ───────────────────────────────────────

def resolve_weights(archetype: str, base_beliefs: dict[str, float] | None = None) -> dict[str, float]:
    """根据原型解析最终的评分权重.

    支持 market_ 前缀 (如 主板_large_bluechip).
    """
    # 去掉市场前缀, 匹配基础原型名
    base_arch = archetype
    for prefix in ["主板_", "创业板_"]:
        if archetype.startswith(prefix):
            base_arch = archetype[len(prefix):]
            break

    if base_beliefs is None:
        base_beliefs = DEFAULT_BELIEFS

    offsets = ARCHETYPE_OFFSETS.get(archetype, {})
    # 叠加校准覆盖 (来自 apply_calibration)
    if archetype in _override_cache:
        offsets = {**offsets, **_override_cache[archetype]}
    resolved = {}

    for param_name, base_value in base_beliefs.items():
        offset = offsets.get(param_name, 0.0)
        adjusted = base_value * (1.0 + offset)
        resolved[param_name] = max(0.05, round(adjusted, 4))

    return resolved


def resolve_thresholds(archetype: str) -> dict:
    """根据原型解析 TG 阈值. 支持市场前缀."""
    base_arch = archetype
    for prefix in ["主板_", "创业板_"]:
        if archetype.startswith(prefix):
            base_arch = archetype[len(prefix):]
            break
    return ARCHETYPE_TG_THRESHOLDS.get(base_arch, DEFAULT_THRESHOLDS)


def resolve_scoring_weights(archetype: str, beliefs: dict[str, dict] | None = None) -> dict[str, float]:
    """从 Bayesian 信念解析评分权重(兼容 get_beliefs 返回格式).

    Args:
        archetype: 原型名称
        beliefs: {param_name: {mu, sigma, n}} 格式的信念字典

    Returns:
        {param_name: weight} 浮点权重
    """
    if beliefs is None:
        return resolve_weights(archetype)

    base_weights = {name: info["mu"] if isinstance(info, dict) else info for name, info in beliefs.items()}
    return resolve_weights(archetype, base_weights)


def get_adjustment_reasons(archetype: str) -> list[str]:
    """返回该原型的权重调整理由(用于前端展示). 支持市场前缀."""
    # 去掉前缀
    base_arch = archetype
    for prefix in ["主板_", "创业板_"]:
        if archetype.startswith(prefix):
            base_arch = archetype[len(prefix):]
            break
    offsets = ARCHETYPE_OFFSETS.get(base_arch, {})
    reasons = []
    for param, offset in sorted(offsets.items(), key=lambda x: abs(x[1]), reverse=True):
        if abs(offset) < 0.05:
            continue
        direction = "上调" if offset > 0 else "下调"
        pct = abs(int(offset * 100))
        group = _param_label(param)
        reasons.append(f"{group}·{param}: {direction} {pct}%")
    return reasons[:8]


def _param_label(param: str) -> str:
    labels = {
        "tech_weight": "技术面", "kline_weight": "K线博弈", "fund_weight": "资金面",
        "fundamentals_weight": "基本面", "valuation_weight": "估值", "real_fund_weight": "真实资金",
        "northbound_weight": "北向", "institutional_weight": "机构", "shareholder_weight": "股东",
        "tg_momentum_mult": "TG动量", "dist_low_mult": "距低点", "j_value_mult": "J值",
        "vol_ratio_mult": "量比", "buy_strength_mult": "买入强度",
        "tg_momentum_weight": "TG动量权重",
        "sector_bonus_l2": "板块L2", "sector_bonus_l3": "板块L3",
        # v7.0.32 新增 5 维
        "macd_weight": "MACD", "kdj_weight": "KDJ",
        "boll_weight": "BOLL", "cci_weight": "CCI", "chip_weight": "筹码",
    }
    return labels.get(param, param)


# ── 校准基础设施 ──────────────────────────────────

async def collect_archetype_calibration_data(lookback_days: int = 180) -> dict:
    """收集原型校准数据 — 为 ARCHETYPE_OFFSETS 校准提供事实基础.

    按原型分组, 统计 recommendation_tracking 中各原型的实际胜率,
    与全局胜率对比, 生成建议偏移量.

    Returns:
        {archetype: {count, win_rate, global_win_rate, suggested_offsets: {...}}}
    """
    from datetime import date as dt_date, timedelta
    from sqlalchemy import text as sql_text
    from app.core.database import async_session_factory

    cutoff = dt_date.today() - timedelta(days=lookback_days)

    async with async_session_factory() as s:
        r = await s.execute(sql_text("""
            SELECT a.archetype, COUNT(*) as cnt,
                   SUM(CASE WHEN rt.was_profitable_3d THEN 1 ELSE 0 END) as wins
            FROM analysis_scores a
            JOIN recommendation_tracking rt
              ON rt.symbol = a.symbol AND rt.scan_date = a.scan_date
            WHERE rt.scan_date >= :cut
              AND rt.was_profitable_3d IS NOT NULL
              AND a.archetype IS NOT NULL
            GROUP BY a.archetype
        """), {"cut": cutoff})
        rows = r.fetchall()

    if not rows:
        return {"status": "no_data", "reason": "无校准数据"}

    # 计算全局胜率
    total_wins = sum(row[2] for row in rows)
    total_count = sum(row[1] for row in rows)
    global_wr = total_wins / total_count if total_count > 0 else 0.5

    result = {"global_win_rate": round(global_wr, 4), "total_samples": total_count, "archetypes": {}}

    for row in rows:
        arch, cnt, wins = row[0], row[1], row[2]
        arch_wr = wins / cnt if cnt > 0 else 0.5
        # 胜率差值 → 建议偏移 (正=该原型表现好, 应提权; 负=该原型表现差, 应降权)
        wr_diff = arch_wr - global_wr
        # 将胜率差值映射到 -0.3 ~ +0.3 的偏移范围
        suggested_shift = round(max(-0.30, min(0.30, wr_diff * 2.0)), 3)

        result["archetypes"][arch] = {
            "count": cnt,
            "win_rate": round(arch_wr, 4),
            "vs_global": round(wr_diff, 4),
            "suggested_global_shift": suggested_shift,
            "note": (
                f"{arch}: 胜率{arch_wr*100:.1f}% vs 全局{global_wr*100:.1f}%"
                f" → 建议{'上调' if suggested_shift > 0 else '下调'} "
                f"全部权重 {abs(suggested_shift)*100:.0f}%"
            ),
        }

    logger.info(f"Archetype calibration: {len(result['archetypes'])} prototypes, "
                f"global WR={global_wr*100:.1f}%")
    return result


async def _load_overrides_from_db():
    """从 DB 加载校准覆盖到内存缓存 (启动时或校准后调用)."""
    global _override_cache
    try:
        from app.core.database import async_session_factory
        async with async_session_factory() as s:
            r = await s.execute(sql_text("""
                SELECT archetype, param_name, override_value
                FROM archetype_offset_overrides
            """))
            cache: dict[str, dict[str, float]] = {}
            for row in r.fetchall():
                arch, param, val = row[0], row[1], float(row[2])
                if arch not in cache:
                    cache[arch] = {}
                cache[arch][param] = val
            _override_cache = cache
            n = sum(len(v) for v in cache.values())
            if n > 0:
                logger.info(f"Loaded {n} archetype overrides for {len(cache)} prototypes")
    except Exception as e:
        # 表不存在或 DB 不可用 → 静默降级
        logger.debug(f"Override cache load skipped: {e}")


async def apply_calibration(min_samples: int = 30, lookback_days: int = 180) -> dict:
    """执行原型偏移校准 — 基于真实盈亏数据更新权重偏移.

    流程:
      1. collect_archetype_calibration_data() 收集各原型胜率
      2. 过滤 count >= min_samples 的原型
      3. 将 suggested_global_shift 应用到该原型的每个权重参数
      4. 写入 archetype_offset_overrides 表 + 更新内存缓存

    Args:
        min_samples: 每个原型最少样本数 (低于则跳过)
        lookback_days: 回看天数

    Returns:
        {applied: N, skipped: M, details: {...}}
    """
    global _override_cache
    from datetime import date as dt_date
    from app.core.database import async_session_factory

    data = await collect_archetype_calibration_data(lookback_days)
    if data.get("status") == "no_data":
        return {"applied": 0, "skipped": 0, "reason": "no_data"}

    archetypes_data = data.get("archetypes", {})
    applied = 0
    skipped = 0
    details = {}

    # archetype_offset_overrides table now via ORM (data_models.py)

    now = dt_date.today()
    for arch, info in archetypes_data.items():
        cnt = info["count"]
        if cnt < min_samples:
            skipped += 1
            details[arch] = f"skipped (n={cnt} < {min_samples})"
            continue

        shift = info["suggested_global_shift"]
        base_offsets = ARCHETYPE_OFFSETS.get(arch, {})
        if not base_offsets:
            skipped += 1
            details[arch] = "skipped (no base offsets)"
            continue

        # 对每个参数: 在原始偏移基础上叠加校准偏移
        # 例如原始 tech_weight=-0.20, 校准 shift=+0.10 → 新值=-0.20*(1-0.10)=-0.18
        # (正shift=原型表现好→减小负面偏移或增大正面偏移)
        new_overrides = {}
        for param, orig_offset in base_offsets.items():
            calibrated = orig_offset + shift * (1.0 + abs(orig_offset))
            calibrated = max(-0.50, min(0.50, calibrated))  # 安全限幅
            new_overrides[param] = round(calibrated, 4)

        # 写入 DB (upsert)
        async with async_session_factory() as s:
            for param, val in new_overrides.items():
                await s.execute(sql_text("""
                    INSERT INTO archetype_offset_overrides
                        (archetype, param_name, override_value, sample_count, calibrated_at)
                    VALUES (:arch, :param, :val, :cnt, :dt)
                    ON CONFLICT (archetype, param_name)
                    DO UPDATE SET override_value = :val, sample_count = :cnt, calibrated_at = :dt
                """), {"arch": arch, "param": param, "val": val, "cnt": cnt, "dt": now})

        applied += 1
        details[arch] = f"applied {len(new_overrides)} params (n={cnt}, shift={shift:+.3f})"

    # 重新加载缓存
    await _load_overrides_from_db()

    result = {"applied": applied, "skipped": skipped, "details": details,
              "global_win_rate": data.get("global_win_rate"), "total_samples": data.get("total_samples")}
    logger.info(f"Calibration applied: {applied} archetypes updated, {skipped} skipped")
    return result
