"""经验回放缓冲 — 按原型分层采样，支持优先级回放."""
import json
import logging
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 64
DEFAULT_ARCHETYPE = "__global__"


async def store_experience(
    event_type: str,
    context: dict,
    action: dict,
    reward: float,
    archetype: str = DEFAULT_ARCHETYPE,
    meta: dict | None = None,
    category_tags: list[str] | None = None,
):
    """存储一条经验."""
    async with async_session_factory() as s:
        await s.execute(text("""
            INSERT INTO experience_replay
                (event_type, context_vector, action_params, reward, reward_components,
                 meta_info, recorded_at, archetype, category_tags)
            VALUES (:et, CAST(:cv AS jsonb), CAST(:ap AS jsonb), CAST(:rw AS float8),
                    CAST(:rc AS jsonb), CAST(:mi AS jsonb), :rd, :ar, CAST(:ct AS jsonb))
        """), {
            "et": event_type,
            "cv": json.dumps(context),
            "ap": json.dumps(action),
            "rw": float(reward),
            "rc": json.dumps({"reward": float(reward)}),
            "mi": json.dumps(meta or {}),
            "rd": date.today(),
            "ar": archetype,
            "ct": json.dumps(category_tags or ["general"]),
        })
        await s.commit()


async def sample_experiences(
    batch_size: int = DEFAULT_BATCH_SIZE,
    archetype: str | None = None,
    min_reward: float | None = None,
    days_back: int = 90,
) -> list[dict]:
    """从经验回放表采样.

    - archetype=None: 各原型等量混合采样
    - archetype="__global__": 仅全局经验
    - min_reward: 过滤低质量经验
    """
    async with async_session_factory() as s:
        cutoff = date.today() - timedelta(days=days_back)

        if archetype:
            r = await s.execute(text("""
                SELECT event_type, context_vector, action_params, reward,
                       reward_components, meta_info, archetype, category_tags
                FROM experience_replay
                WHERE archetype=:ar AND recorded_at >= :cut
                  AND (:mr IS NULL OR reward >= :mr)
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"ar": archetype, "cut": cutoff, "mr": min_reward, "lim": batch_size})
        else:
            r = await s.execute(text("""
                SELECT event_type, context_vector, action_params, reward,
                       reward_components, meta_info, archetype, category_tags
                FROM experience_replay
                WHERE recorded_at >= :cut
                  AND (:mr IS NULL OR reward >= :mr)
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"cut": cutoff, "mr": min_reward, "lim": batch_size * 3})

        rows = r.fetchall()
        if not rows:
            return []

        experiences = [_row_to_dict(row) for row in rows]

        if archetype is None and len(experiences) > batch_size:
            experiences = _balanced_sample(experiences, batch_size)

        return experiences


async def sample_balanced(batch_size: int = DEFAULT_BATCH_SIZE, days_back: int = 90) -> list[dict]:
    """各原型等量采样 — 确保训练数据中每个原型都有代表."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT archetype FROM experience_replay
            WHERE recorded_at >= :cut AND archetype IS NOT NULL
        """), {"cut": date.today() - timedelta(days=days_back)})
        archetypes = [row[0] for row in r.fetchall()]

    if not archetypes:
        return await sample_experiences(batch_size, archetype=DEFAULT_ARCHETYPE, days_back=days_back)

    per_archetype = max(4, batch_size // len(archetypes))
    all_exp = []
    for ar in archetypes:
        batch = await sample_experiences(per_archetype, archetype=ar, days_back=days_back)
        all_exp.extend(batch)

    if len(all_exp) < batch_size:
        remaining = batch_size - len(all_exp)
        extra = await sample_experiences(remaining, archetype=DEFAULT_ARCHETYPE, days_back=days_back)
        all_exp.extend(extra)

    return all_exp[:batch_size]


async def get_archetype_stats(days_back: int = 30) -> list[dict]:
    """各原型的经验统计."""
    async with async_session_factory() as s:
        cutoff = date.today() - timedelta(days=days_back)
        r = await s.execute(text("""
            SELECT archetype, COUNT(*) as cnt, AVG(reward) as avg_r,
                   MIN(reward) as min_r, MAX(reward) as max_r
            FROM experience_replay
            WHERE recorded_at >= :cut
            GROUP BY archetype
            ORDER BY AVG(reward) DESC
        """), {"cut": cutoff})
        return [{
            "archetype": row[0] or DEFAULT_ARCHETYPE,
            "count": row[1],
            "avg_reward": round(float(row[2]), 4) if row[2] else 0,
            "min_reward": round(float(row[3]), 4) if row[3] else 0,
            "max_reward": round(float(row[4]), 4) if row[4] else 0,
        } for row in r.fetchall()]


def _row_to_dict(row) -> dict:
    return {
        "event_type": row[0],
        "context_vector": row[1] if isinstance(row[1], dict) else json.loads(row[1]) if row[1] else {},
        "action_params": row[2] if isinstance(row[2], dict) else json.loads(row[2]) if row[2] else {},
        "reward": float(row[3]) if row[3] else 0.0,
        "reward_components": row[4] if isinstance(row[4], dict) else json.loads(row[4]) if row[4] else {},
        "meta_info": row[5] if isinstance(row[5], dict) else json.loads(row[5]) if row[5] else {},
        "archetype": row[6] or DEFAULT_ARCHETYPE,
        "category_tags": row[7] if isinstance(row[7], list) else json.loads(row[7]) if row[7] else [],
    }


def _balanced_sample(experiences: list[dict], batch_size: int) -> list[dict]:
    """按原型均衡采样."""
    from collections import defaultdict
    grouped = defaultdict(list)
    for exp in experiences:
        grouped[exp["archetype"]].append(exp)

    n_groups = len(grouped)
    per_group = max(1, batch_size // n_groups)
    result = []
    for ar, exps in grouped.items():
        exps_sorted = sorted(exps, key=lambda x: abs(x["reward"]), reverse=True)
        result.extend(exps_sorted[:per_group])

    while len(result) > batch_size:
        result.pop()

    return result
