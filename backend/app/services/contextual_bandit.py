"""上下文Bandit — 根据市场状态选择最优策略臂 (S1/S2/S3).

使用 Thompson Sampling 从历史奖励中采样，选择预期收益最高的策略.

Status: P2 — select_arm() implemented, update() method needed for complete feedback loop.
Currently not connected to any scheduler or API endpoint.
"""
import logging, random
import numpy as np
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

ARMS = ["S1", "S2", "S3"]


async def get_arm_rewards(days_back: int = 60) -> dict[str, list[float]]:
    """从 learning_predictions 获取各策略臂的历史奖励."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT strategy, AVG(was_correct::int) as hit_rate, AVG(excess_return) as avg_excess,
                   COUNT(*) as n
            FROM learning_predictions
            WHERE created_at >= CURRENT_DATE - :days
            GROUP BY strategy
        """), {"days": days_back})
        rewards = {}
        for row in r.fetchall():
            st = row[0] or "S2"
            hit = float(row[1] or 0.5)
            excess = float(row[2] or 0)
            n = row[3] or 0
            # 综合奖励: hit_rate * 0.6 + normalized_excess * 0.4
            reward = hit * 0.6 + max(0, excess / max(abs(excess), 1)) * 0.4
            rewards[st] = [reward] * max(1, n // 10)  # 按观测数加权
        return rewards


async def select_arm(context: dict | None = None, days_back: int = 60) -> str:
    """Thompson Sampling 选择最优策略臂.

    Args:
        context: 市场上下文 (可选，当前版本基于全局奖励)
        days_back: 回看天数
    Returns:
        策略名: "S1" / "S2" / "S3"
    """
    rewards = await get_arm_rewards(days_back)

    # 默认: 均匀先验 Beta(1,1)
    best_arm = "S2"
    best_sample = -1.0

    for arm in ARMS:
        arm_rewards = rewards.get(arm, [])
        successes = sum(1 for r in arm_rewards if r > 0.5)
        failures = len(arm_rewards) - successes

        # Thompson Sampling: 从 Beta(1+successes, 1+failures) 采样
        sample = float(np.random.beta(1 + successes, 1 + max(1, failures)))
        if sample > best_sample:
            best_sample = sample
            best_arm = arm

    return best_arm


async def get_arm_stats(days_back: int = 30) -> dict:
    """各策略臂的统计信息."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT strategy,
                   COUNT(*) as n,
                   AVG(was_correct::int) as hit_rate,
                   AVG(excess_return) as avg_excess,
                   STDDEV(excess_return) as std_excess
            FROM learning_predictions
            WHERE created_at >= CURRENT_DATE - :days
            GROUP BY strategy
        """), {"days": days_back})
        return {
            row[0] or "S2": {
                "n": row[1] or 0,
                "hit_rate": round(float(row[2] or 0), 4),
                "avg_excess": round(float(row[3] or 0), 2),
                "std_excess": round(float(row[4] or 0), 2),
            }
            for row in r.fetchall()
        }
