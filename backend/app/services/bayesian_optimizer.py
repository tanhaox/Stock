"""Bayesian 参数优化器 — Normal-Normal 共轭更新 + 参数分组.

每个参数维护信念分布: θ ~ N(mu, sigma²)
- mu: 后验均值(点估计)
- sigma: 后验标准差(精度加权更新)
- n: 有效观察次数(信念强度)
- lo/hi: 95% 可信区间
"""
import logging
import math
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

DEFAULT_ARCHETYPE = "__global__"

# ── 默认信念(按估值逻辑分组)─────────────────────
# 组 1: 子评分权重(决定各子评分在 composite 中的比重)
# 注意: 所有子维度已归一化到 0-100 同尺度，权重直接可比
GROUP_SCORING_WEIGHTS = {
    "tech_weight": 2.5,        # 技术面 (v7.0.32 降权, 因为 RSI 已纳入 RSI 6/12/24 细化)
    "kline_weight": 2.5,        # K线博弈
    "fund_weight": 2.0,         # 资金面
    "fundamentals_weight": 1.5, # 基本面
    "valuation_weight": 1.0,    # 估值
    "real_fund_weight": 1.0,    # 真实资金流
    "northbound_weight": 0.5,   # 北向资金
    "institutional_weight": 0.5, # 机构持仓
    "shareholder_weight": 0.5,   # 股东变化
    "ma_trend_weight": 1.0,     # 均线趋势质量
    "pattern_weight": 1.0,      # 形态识别
    "tg_momentum_weight": 2.5,  # TG动量
    "vol_ratio_weight": 2.0,    # 量比
    "arbr_weight": 1.5,         # ARBR
    "sector_alpha_weight": 1.5, # 行业Alpha
    "market_relative_weight": 1.5, # 大盘相对
    "trend_deviation_weight": 1.5, # 趋势偏离
    "bbi_weight": 1.5,          # BBI
    "box_weight": 2.0,          # 箱体
    # Phase 73: 补入之前遗漏的子维度
    "dist_low_weight": 1.0,
    "j_value_weight": 1.5,
    "downside_risk_weight": 1.0,
    "weekly_resonance_weight": 0.5,
    "toplist_sector_weight": 0.5,
    "ambush_weight": 0.5,
    # v7.0.32: 新增 5 维技术因子权重
    "macd_weight": 2.0,         # MACD (趋势确认)
    "kdj_weight": 1.5,          # KDJ (超买超卖)
    "boll_weight": 1.0,         # BOLL 布林带 (价格位置)
    "cci_weight": 0.5,          # CCI 顺势指标
    "chip_weight": 2.0,         # 筹码分布 (主力成本)
}
# 组 2: TG 信号乘数(调整 TG 各维度的灵敏度)
GROUP_TG_MULTIPLIERS = {
    "tg_momentum_mult": 1.0,
    "tg_momentum_weight": 2.5,  # TG 信号在 composite 中的权重(已修正尺度)
    "dist_low_mult": 1.0,
    "j_value_mult": 1.0,
    "vol_ratio_mult": 1.0,
    "buy_strength_mult": 1.0,
}
# 组 3: 市场环境修正(不同市态下的评分缩放)
GROUP_MARKET_ADJUSTMENTS = {
    "market_上升": 1.2,
    "market_下降": 0.2,
    "market_回踩": 0.5,
    "market_震荡": 0.85,
}
# 组 4: 板块加成
GROUP_SECTOR_BONUS = {
    "sector_bonus_l2": 0.5,
    "sector_bonus_l3": 1.5,
}

DEFAULT_BELIEFS = {}
DEFAULT_BELIEFS.update(GROUP_SCORING_WEIGHTS)
DEFAULT_BELIEFS.update(GROUP_TG_MULTIPLIERS)
DEFAULT_BELIEFS.update(GROUP_MARKET_ADJUSTMENTS)
DEFAULT_BELIEFS.update(GROUP_SECTOR_BONUS)

PARAM_GROUPS = {
    "scoring_weights": list(GROUP_SCORING_WEIGHTS.keys()),
    "tg_multipliers": list(GROUP_TG_MULTIPLIERS.keys()),
    "market_adjustments": list(GROUP_MARKET_ADJUSTMENTS.keys()),
    "sector_bonus": list(GROUP_SECTOR_BONUS.keys()),
}

# 每个组的先验 sigma + 观测方差
GROUP_SIGMA = {
    "scoring_weights": 0.8,
    "tg_multipliers": 0.3,
    "market_adjustments": 0.2,
    "sector_bonus": 0.4,
}
# 每个组的单次观测方差(参数值变化的主观估计方差)
GROUP_OBS_VARIANCE = {
    "scoring_weights": 0.05,     # 评分权重不确定性较高
    "tg_multipliers": 0.01,      # TG 乘数较稳定
    "market_adjustments": 0.005, # 市态修正较稳定
    "sector_bonus": 0.02,        # 板块加成中等
}

LEARNING_RATE = 0.01  # 每次观测的参数调整步长
MIN_OBSERVATIONS_FOR_ADJUSTMENT = 5


def get_group_for_param(param_name: str) -> str:
    for group, members in PARAM_GROUPS.items():
        if param_name in members:
            return group
    return "scoring_weights"


# ── 读取 / 初始化 ─────────────────────────────────

async def get_beliefs(archetype: str = DEFAULT_ARCHETYPE) -> dict[str, dict]:
    """返回 {param_name: {mu, sigma, n, lo, hi}} 字典."""
    beliefs = {}
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT param_name, mu, sigma, n_observations, lo, hi FROM bayesian_beliefs WHERE archetype=:a"
        ), {"a": archetype})
        for row in r.fetchall():
            beliefs[row[0]] = {
                "mu": float(row[1]) if row[1] else 1.0,
                "sigma": float(row[2]) if row[2] else 0.5,
                "n": row[3] or 0,
                "lo": float(row[4]) if row[4] else 0.1,
                "hi": float(row[5]) if row[5] else 5.0,
            }
    for name, mu in DEFAULT_BELIEFS.items():
        if name not in beliefs:
            group = get_group_for_param(name)
            sigma = GROUP_SIGMA.get(group, 0.5)
            beliefs[name] = {"mu": mu, "sigma": sigma, "n": 0, "lo": mu * 0.5, "hi": mu * 2.0}
    return beliefs


async def ensure_beliefs_initialized():
    """确保所有默认参数已写入数据库(按原型分组)."""
    async with async_session_factory() as s:
        for name, mu in DEFAULT_BELIEFS.items():
            group = get_group_for_param(name)
            sigma = GROUP_SIGMA.get(group, 0.5)
            await s.execute(text("""
                INSERT INTO bayesian_beliefs (archetype, param_name, mu, sigma, n_observations, lo, hi, last_updated)
                VALUES (CAST(:a AS text), CAST(:n AS text), CAST(:m AS float8), CAST(:s AS float8),
                        0, CAST(:lo AS float8), CAST(:hi AS float8), NOW())
                ON CONFLICT (archetype, param_name) DO NOTHING
            """), {
                "a": str(DEFAULT_ARCHETYPE), "n": str(name), "m": float(mu),
                "s": float(sigma), "lo": float(mu * 0.5), "hi": float(mu * 2.0),
            })
        await s.commit()


# ── Normal-Normal 共轭更新 ────────────────────────

async def update_belief(
    param_name: str,
    observed_value: float,
    weight: float = 1.0,
    archetype: str = DEFAULT_ARCHETYPE,
) -> dict:
    """Normal-Normal 共轭贝叶斯更新.

    先验: θ ~ N(mu_0, σ_0²)     — 即 均值=mu_0, 方差=σ_0²
    似然: x ~ N(θ, σ_obs²/w)   — 即 新观察均值=x, 方差=σ_obs²/w
    后验: θ|x ~ N(mu_n, σ_n²)

    其中:
      n_n = n_0 + w
      mu_n = (n_0 * mu_0 + w * x) / (n_0 + w)
      σ_n² = 1 / (1/σ_0² + w/σ_obs²)
      lo_n = mu_n - 1.96 * σ_n
      hi_n = mu_n + 1.96 * σ_n
    """
    beliefs = await get_beliefs(archetype)
    default_mu = DEFAULT_BELIEFS.get(param_name, 1.0)
    b = beliefs.get(param_name, {"mu": default_mu, "sigma": 0.5, "n": 0, "lo": 0.1, "hi": 5.0})

    prior_mu = float(b["mu"])
    prior_n = float(b["n"])
    prior_sigma = float(b["sigma"])

    # 获取该参数组的观测方差
    group = get_group_for_param(param_name)
    obs_variance = GROUP_OBS_VARIANCE.get(group, 0.01)

    # Normal-Normal 共轭更新: 精度相加
    prior_precision = 1.0 / (prior_sigma ** 2) if prior_sigma > 0 else 1.0
    obs_precision = weight / obs_variance
    posterior_precision = prior_precision + obs_precision
    posterior_sigma = math.sqrt(1.0 / posterior_precision) if posterior_precision > 0 else prior_sigma

    # 均值更新
    posterior_n = prior_n + weight
    posterior_mu = (prior_n * prior_mu + weight * observed_value) / posterior_n

    # 95% 可信区间: mu ± 1.96 * σ_n
    posterior_lo = posterior_mu - 1.96 * posterior_sigma
    posterior_hi = posterior_mu + 1.96 * posterior_sigma

    async with async_session_factory() as s:
        await s.execute(text("""
            UPDATE bayesian_beliefs
            SET mu=CAST(:mu AS float8), sigma=CAST(:sigma AS float8),
                n_observations=CAST(:n AS int4),
                lo=CAST(:lo AS float8), hi=CAST(:hi AS float8), last_updated=NOW()
            WHERE archetype=CAST(:a AS text) AND param_name=CAST(:p AS text)
        """), {
            "mu": float(posterior_mu), "sigma": float(posterior_sigma),
            "n": int(posterior_n),
            "lo": float(posterior_lo), "hi": float(posterior_hi),
            "a": str(archetype), "p": str(param_name),
        })
        await s.commit()

    return {
        "param": param_name,
        "archetype": archetype,
        "old_mu": round(prior_mu, 4),
        "new_mu": round(posterior_mu, 4),
        "sigma": round(posterior_sigma, 4),
        "n": int(posterior_n),
        "ci_95": [round(posterior_lo, 4), round(posterior_hi, 4)],
    }


# ── 批次更新 ─────────────────────────────────────

async def batch_update_from_prediction_results(
    results: list[dict],
    archetype: str = DEFAULT_ARCHETYPE,
) -> dict:
    """批次更新：基于一组预测结果调整参数.

    results: [{
        "symbol", "predicted_score", "actual_return_pct",
        "parameters_used": {"tech_weight": 3.0, ...}
    }, ...]

    更新逻辑:
    - 预测偏差 = actual_return - expected_return(predicted_score)
    - 对每个参数: observed = current_value * (1 + prediction_error * LR)
    - 分组应用共轭更新
    """
    if len(results) < MIN_OBSERVATIONS_FOR_ADJUSTMENT:
        return {"status": "skipped", "reason": f"需要至少 {MIN_OBSERVATIONS_FOR_ADJUSTMENT} 条结果", "count": len(results)}

    # 汇总每个参数的观察值
    updates: dict[str, list[dict]] = {}
    for r in results:
        pred = float(r.get("predicted_score", 50))
        actual = float(r.get("actual_return_pct", 0))

        # 将评分映射到预期收益: score=50 → expected 0%, score=100 → expected +20%
        expected_return = (pred - 50) * 0.4
        prediction_error = actual - expected_return

        params = r.get("parameters_used", {})
        for pname, pvalue in params.items():
            if pname not in DEFAULT_BELIEFS:
                continue
            pvalue = float(pvalue)
            # 预测偏差调整参数值: 表现超预期→提权，低于预期→降权
            # 使用 LEARNING_RATE 直接控制更新步长 (默认0.01)
            adjusted_value = pvalue * (1.0 + prediction_error * LEARNING_RATE)
            adjusted_value = max(0.05, min(5.0, adjusted_value))
            updates.setdefault(pname, []).append({
                "observed": adjusted_value,
                "weight": 1.0,
            })

    # 逐参数应用共轭更新
    results_out = []
    for pname, obs_list in updates.items():
        avg_observed = sum(o["observed"] * o["weight"] for o in obs_list) / sum(o["weight"] for o in obs_list)
        total_weight = float(len(obs_list))
        result = await update_belief(pname, avg_observed, weight=total_weight, archetype=archetype)
        results_out.append(result)

    return {
        "status": "success",
        "archetype": archetype,
        "updated_params": len(results_out),
        "observations": len(results),
        "details": results_out,
    }


# ── 分组级别的查询与更新 ──────────────────────────

async def get_group_beliefs(group: str, archetype: str = DEFAULT_ARCHETYPE) -> dict:
    """获取整组参数的当前信念."""
    all_beliefs = await get_beliefs(archetype)
    param_names = PARAM_GROUPS.get(group, [])
    return {name: all_beliefs.get(name, {"mu": DEFAULT_BELIEFS.get(name, 1.0), "sigma": 0.5, "n": 0}) for name in param_names}


async def get_learning_summary(archetype: str = DEFAULT_ARCHETYPE) -> dict:
    """学习状态摘要：每个参数组的平均 n 和可信区间宽度."""
    beliefs = await get_beliefs(archetype)
    summary = {}
    for group, members in PARAM_GROUPS.items():
        group_beliefs = [beliefs.get(m, {"n": 0, "lo": 0, "hi": 5}) for m in members]
        avg_n = sum(b["n"] for b in group_beliefs) / len(members)
        avg_width = sum(b["hi"] - b["lo"] for b in group_beliefs) / len(members)
        summary[group] = {
            "param_count": len(members),
            "avg_observations": round(avg_n, 1),
            "avg_ci_width": round(avg_width, 3),
            "convergence": "tight" if avg_width < 1.0 else "moderate" if avg_width < 3.0 else "wide",
        }
    return summary
