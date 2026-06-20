"""策略原型分类器 — K-means 聚类 + 余弦相似度分配.

将 11 维指纹向量映射到有限原型(4-6 个)，每个原型对应不同的投资策略风格。

原型定义(初始):
  0: 大盘蓝筹 (large_bluechip)  — 高市值+低估值+稳定基本面
  1: 小盘题材 (small_speculative) — 低市值+高波动+事件驱动
  2: 成长科技 (growth_tech)      — 中等市值+高成长+高估值
  3: 价值防御 (value_defensive)  — 低估值+高股息+低波动
  4: 周期资源 (cyclical_resource) — 高杠杆+周期利润+商品联动
  5: 事件驱动 (event_driven)     — 业绩跳变+公告催化剂
"""
import json
import logging
import math
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

N_ARCHETYPES = 5
N_ARCHETYPES_CHINEXT = 4  # 创业板股票少, 4个原型足够

ARCHETYPE_NAMES = [
    "large_bluechip", "growth_tech", "cyclical_resource",
    "value_defensive", "small_speculative",
]

# ── 冷启动中心点(仅首次部署、指纹<50时使用)──
# 从实际数据随机采样初始化，而非手写假向量
INITIAL_CENTROIDS: dict[str, list[float]] = {}  # 运行时根据实际数据填充

def _random_centroids_from_samples(samples: dict[str, list[float]], n: int = 5) -> dict[str, list[float]]:
    """从实际指纹样本中随机选择中心点."""
    import random as _random
    if len(samples) < n:
        n = len(samples)
    keys = list(samples.keys())
    _random.shuffle(keys)
    names = ["proto_0", "proto_1", "proto_2", "proto_3", "proto_4"]
    return {names[i]: samples[keys[i]] for i in range(n)}


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """计算欧氏距离."""
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b)))


def classify_by_nearest_centroid(
    fingerprint: list[float],
    centroids: dict[str, list[float]],
) -> tuple[str, float]:
    """将指纹分配到最近的中心点(欧氏距离)，返回(原型名，距离)."""
    best_name = "large_bluechip"
    best_dist = float("inf")
    for name, centroid in centroids.items():
        dist = euclidean_distance(fingerprint, centroid)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name, round(best_dist, 4)


async def load_centroids_from_db() -> dict[str, list[float]]:
    """从 archetype_profiles 表加载中心点.

    若为空(首次启动)，自动从 stock_fingerprints 加载数据运行 K-means++ 初始化。
    """
    centroids = {}
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT archetype, centroid_vector FROM archetype_profiles ORDER BY archetype"
        ))
        for row in r.fetchall():
            name = row[0]
            vec = row[1]
            if isinstance(vec, str):
                vec = json.loads(vec)
            if vec and len(vec) >= 11:
                centroids[name] = [float(v) for v in vec]

    if centroids:
        return centroids

    # 冷启动：从指纹数据自动聚类
    logger.info("No archetype centroids found, running auto-initialization...")
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT ts_code, fingerprint_vector FROM stock_fingerprints "
            "WHERE scan_date >= CURRENT_DATE - 30 LIMIT 2000"
        ))
        rows = r.fetchall()

    if len(rows) < 50:
        logger.warning(f"Only {len(rows)} fingerprints available, using random samples as fallback")
        return _random_centroids_from_samples({row[0]: json.loads(row[1]) if isinstance(row[1], str) else row[1] for row in rows if row[1]})

    fingerprints = {}
    for row in rows:
        vec = row[1]
        if isinstance(vec, str):
            vec = json.loads(vec)
        if vec and len(vec) >= 11:
            # 使用全部可用维度(11维历史兼容，15维完整)
            n_dims = min(len(vec), 15)
            fingerprints[row[0]] = [float(v) for v in vec[:n_dims]]

    if len(fingerprints) < 50:
        return _random_centroids_from_samples(fingerprints)

    try:
        centroids = await run_kmeans_clustering(fingerprints)
        logger.info(f"Auto-initialized {len(centroids)} archetypes from {len(fingerprints)} stocks")
        return centroids
    except Exception as e:
        logger.error(f"Auto-initialization failed: {e}")
        return _random_centroids_from_samples(fingerprints)


async def save_centroids_to_db(centroids: dict[str, list[float]]):
    """保存中心点到数据库(含权重偏移持久化)."""
    from app.services.archetype_param_resolver import ARCHETYPE_OFFSETS
    async with async_session_factory() as s:
        for name, vec in centroids.items():
            overrides = ARCHETYPE_OFFSETS.get(name, {})
            await s.execute(text("""
                INSERT INTO archetype_profiles (archetype, centroid_vector, label, scoring_weight_overrides, updated_at)
                VALUES (:n, CAST(:v AS float8[]), :l, CAST(:o AS jsonb), NOW())
                ON CONFLICT (archetype)
                DO UPDATE SET centroid_vector=CAST(:v AS float8[]),
                              scoring_weight_overrides=CAST(:o AS jsonb), updated_at=NOW()
            """), {"n": name, "v": vec, "l": name.replace("_", " ").title(), "o": json.dumps(overrides)})
        await s.commit()


async def run_kmeans_clustering(
    fingerprints: dict[str, list[float]],
    n_clusters: int = N_ARCHETYPES,
    max_iterations: int = 50,
    market: str = None,
) -> dict[str, list[float]]:
    """对指纹向量运行 K-means++ 聚类.

    market='主板' 或 '创业板' 时, 原型名加前缀 (如 主板_large_bluechip).
    """
    if len(fingerprints) < n_clusters:
        logger.warning(f"Not enough fingerprints ({len(fingerprints)}) for {n_clusters} clusters")
        return INITIAL_CENTROIDS

    import random
    vecs = list(fingerprints.values())
    symbols = list(fingerprints.keys())

    # K-means++ 初始化
    centroids = [random.choice(vecs)]
    for _ in range(1, n_clusters):
        distances = [min(euclidean_distance(v, c) for c in centroids) ** 2 for v in vecs]
        total = sum(distances)
        if total == 0:
            centroids.append(random.choice(vecs))
        else:
            r = random.random() * total
            cumsum = 0
            for i, d in enumerate(distances):
                cumsum += d
                if cumsum >= r:
                    centroids.append(vecs[i])
                    break

    # Lloyd 迭代
    for iteration in range(max_iterations):
        clusters = {i: [] for i in range(n_clusters)}
        for vi, v in enumerate(vecs):
            ci = min(range(n_clusters), key=lambda i: euclidean_distance(v, centroids[i]))
            clusters[ci].append(v)

        new_centroids = []
        for i in range(n_clusters):
            if clusters[i]:
                avg = [sum(dim) / len(clusters[i]) for dim in zip(*clusters[i])]
                new_centroids.append(avg)
            else:
                new_centroids.append(centroids[i])

        max_shift = max(euclidean_distance(c, nc) for c, nc in zip(centroids, new_centroids))
        centroids = new_centroids
        if max_shift < 0.001:
            logger.info(f"K-means converged after {iteration + 1} iterations")
            break

    # 自动命名：按市值维度(dim 0)排序
    centroid_with_idx = list(enumerate(centroids))
    centroid_with_idx.sort(key=lambda x: x[1][0], reverse=True)

    archetype_names = [
        "large_bluechip", "growth_tech", "cyclical_resource",
        "value_defensive", "small_speculative",
    ][:n_clusters]

    prefix = f"{market}_" if market else ""
    result = {}
    for rank, (orig_idx, centroid) in enumerate(centroid_with_idx):
        name = prefix + archetype_names[rank]
        result[name] = [round(v, 4) for v in centroid]

    await save_centroids_to_db(result)
    return result


async def classify_stocks(
    symbols: list[str],
    fingerprints: dict[str, list[float]] | None = None,
    market: str = None,
) -> dict[str, str]:
    """对一组股票进行原型分类. market='主板'/'创业板' 时仅匹配该市场centroids."""
    from app.services.fingerprint_builder import build_fingerprints

    if fingerprints is None:
        fingerprints = await build_fingerprints(symbols)

    centroids = await load_centroids_from_db()
    if not centroids:
        centroids = INITIAL_CENTROIDS

    labels = {}
    for sym in symbols:
        fp = fingerprints.get(sym)
        if fp and len(fp) >= 11:
            # 使用全部可用维度分类 (11维历史兼容, 15维完整)
            n_dims = min(len(fp), 15)
            fp_use = fp[:n_dims]
            label, _ = classify_by_nearest_centroid(fp_use, centroids)
        else:
            label = "large_bluechip"  # default
        labels[sym] = label

    return labels


async def get_archetype_distribution(symbols: list[str]) -> dict:
    """获取一组股票的原型分布."""
    labels = await classify_stocks(symbols)
    dist = {}
    for sym, label in labels.items():
        dist[label] = dist.get(label, 0) + 1
    total = len(symbols)
    return {
        "distribution": {k: {"count": v, "pct": round(v / total * 100, 1)} for k, v in dist.items()},
        "total": total,
        "archetypes_found": len(dist),
    }
