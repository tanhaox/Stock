"""跨股票 DNA 相似度计算。

基于 DNA 档案的多维余弦相似度。
比较维度: 表情指纹 + 周期节律 + 核心驱动因子 + 行为指纹。
"""
import json
import numpy as np
import logging
from typing import Optional
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("stock_dna.similarity")

# DNA 向量化维度
DNA_VECTOR_KEYS = [
    "emotion_entropy", "avg_lockup_days", "std_lockup_days", "cycle_cv",
    "avg_breakout_return", "avg_breakout_days",
    "best_horizon", "best_horizon_auc",
    "crash_resilience", "rally_capture", "deception_rate", "consistency", "extreme_tail",
]


def dna_to_vector(dna_profile: dict) -> np.ndarray:
    """将 DNA 档案转为可比较的固定长度向量."""
    vec = np.zeros(len(DNA_VECTOR_KEYS), dtype=np.float64)
    for i, key in enumerate(DNA_VECTOR_KEYS):
        val = dna_profile.get(key, 0.0) or 0.0
        vec[i] = float(val)
    # 归一化到 [0, 1]
    v_min, v_max = vec.min(), vec.max()
    if v_max > v_min and v_max > 0:
        vec = (vec - v_min) / (v_max - v_min)
    elif v_max <= 0:
        # 全零或全负: 用离散特征区分 (best_horizon + n_emotions)
        bh = float(dna_profile.get("best_horizon", 5) or 5)
        ne = float(dna_profile.get("n_emotions", 1) or 1)
        vec[0] = bh / 20.0
        vec[1] = ne / 10.0
        return vec
    # v_max == v_min > 0 (all identical): add perturbation from discrete features
    else:
        bh = float(dna_profile.get("best_horizon", 5) or 5)
        ne = float(dna_profile.get("n_emotions", 1) or 1)
        vec[0] = bh / 20.0
        vec[1] = ne / 10.0
        return vec
    return vec


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度."""
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    if norm < 1e-9:
        return 0.0
    return round(float(dot / norm), 4)


async def find_similar_stocks(symbol: str, top_k: int = 5) -> list[dict]:
    """找到与目标股票 DNA 最相似的 top_k 只股票。

    Args:
        symbol: 目标股票代码
        top_k: 返回前 K 名

    Returns:
        [{symbol, similarity, shared_traits}, ...]
    """
    async with async_session_factory() as s:
        # 加载目标 DNA
        r = await s.execute(text(
            "SELECT * FROM stock_dna.profiles WHERE symbol=:sym"
        ), {"sym": symbol})
        row = r.fetchone()
        if not row:
            return []

        cols = [c[0] for c in r.cursor.description]
        target = dict(zip(cols, row))
        target_vec = dna_to_vector(target)

        # 加载所有其余 DNA
        r2 = await s.execute(text(
            "SELECT * FROM stock_dna.profiles WHERE symbol!=:sym"
        ), {"sym": symbol})
        all_rows = r2.fetchall()
        cols2 = [c[0] for c in r2.cursor.description]

        similarities = []
        for row2 in all_rows:
            prof = dict(zip(cols2, row2))
            vec = dna_to_vector(prof)
            sim = cosine_similarity(target_vec, vec)

            # 找到共同特征
            shared = []
            if abs(prof.get("avg_lockup_days", 0) - target.get("avg_lockup_days", 0)) < 3:
                shared.append("锁死周期相近")
            if prof.get("best_horizon") == target.get("best_horizon"):
                shared.append("最佳窗口一致")
            if abs(prof.get("cycle_cv", 0) - target.get("cycle_cv", 0)) < 0.15:
                shared.append("周期规律相似")

            similarities.append({
                "symbol": prof["symbol"],
                "similarity": sim,
                "best_horizon": prof.get("best_horizon"),
                "cycle_cv": prof.get("cycle_cv"),
                "shared_traits": shared[:3],
            })

        similarities.sort(key=lambda x: x["similarity"], reverse=True)

    return similarities[:top_k]


async def compute_similarity_matrix(symbols: list[str]) -> dict:
    """计算一批股票的 DNA 相似度矩阵.

    Returns:
        {matrix: [[1.0, 0.87, ...], ...], symbols: [...]}
    """
    async with async_session_factory() as s:
        profiles = {}
        for sym in symbols:
            r = await s.execute(text(
                "SELECT * FROM stock_dna.profiles WHERE symbol=:sym"
            ), {"sym": sym})
            row = r.fetchone()
            if row:
                cols = [c[0] for c in r.cursor.description]
                prof = dict(zip(cols, row))
                # 过滤无效档案: cycle_cv==999 且 n_emotions<=1 → 无有效 DNA
                cv = float(prof.get("cycle_cv", 999) or 999)
                ne = int(prof.get("n_emotions", 0) or 0)
                if cv >= 999 and ne <= 1:
                    continue
                profiles[sym] = prof

    n = len(symbols)
    matrix = np.eye(n)

    for i, sym_i in enumerate(symbols):
        if sym_i not in profiles:
            continue
        vec_i = dna_to_vector(profiles[sym_i])
        for j, sym_j in enumerate(symbols):
            if i >= j or sym_j not in profiles:
                continue
            vec_j = dna_to_vector(profiles[sym_j])
            sim = cosine_similarity(vec_i, vec_j)
            matrix[i, j] = sim
            matrix[j, i] = sim

    return {
        "symbols": symbols,
        "matrix": [[round(float(v), 2) for v in row] for row in matrix],
    }
