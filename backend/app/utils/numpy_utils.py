"""统一的 NumPy/数值安全工具 (P0 系统级).

消除分散的 NaN/Inf 防御——全系统使用同一策略。

核心函数:
  safe_float(val, default=0.0)      — 安全标量提取
  safe_auc(val)                     — AUC 专用 (default=0.5)
  safe_rsi(val)                     — RSI/KDJ 中性指标专用 (default=50)
  sanitize_array(arr, fill=0.0)     — 批量数组清洗
  sanitize_for_json(obj)            — JSON 序列化边界守卫 (NaN→null)
  div0(a, b, default=0.0)          — 安全除法
  safe_corrcoef(a, b, default=0.0)  — 安全相关系数
"""
import numpy as np
import logging
from typing import Any, Union

logger = logging.getLogger("numpy_utils")


# ══════════════════════════════════════════════════════════════════════
# 标量安全提取
# ══════════════════════════════════════════════════════════════════════

def safe_float(val, default: float = 0.0) -> float:
    """安全地将任意值转为 float，自动处理 None/NaN/Inf.

    Args:
        val: 输入值 (可为 None, str, int, float, np.float64, etc.)
        default: 遇到 NaN/Inf/无法转换时的回退值

    Returns:
        float 值，保证不是 NaN 或 Inf

    Examples:
        safe_float(None)        → 0.0
        safe_float(float('nan')) → 0.0
        safe_float('3.14')      → 3.14
        safe_float(42, -1)      → 42.0
    """
    if val is None:
        return default
    try:
        f = float(val)
        if np.isnan(f) or np.isinf(f):
            return default
        return f
    except (ValueError, TypeError, OverflowError):
        return default


def safe_auc(val) -> float:
    """AUC 专用安全提取: 无法计算时返回 0.5 (随机基线)."""
    return safe_float(val, default=0.5)


def safe_rsi(val) -> float:
    """RSI/KDJ/中性技术指标专用安全提取: 无法计算时返回 50 (中性)."""
    return safe_float(val, default=50.0)


# ══════════════════════════════════════════════════════════════════════
# 数组清洗
# ══════════════════════════════════════════════════════════════════════

def sanitize_array(arr: np.ndarray, fill: float = 0.0) -> np.ndarray:
    """批量清洗数组中的 NaN 和 Inf.

    Args:
        arr: NumPy 数组
        fill: 替换值 (默认 0.0)

    Returns:
        清洗后的数组 (原地修改 + 返回引用)
    """
    mask = np.isnan(arr) | np.isinf(arr)
    if mask.any():
        arr[mask] = fill
    return arr


def sanitize_label_array(y: np.ndarray) -> np.ndarray:
    """清洗标签数组 NaN (XGBoost 训练安全).

    标签 NaN 比特征 NaN 更危险: 会使 loss 变成 NaN.
    """
    return sanitize_array(y, fill=0.0)


# ══════════════════════════════════════════════════════════════════════
# JSON 序列化边界守卫
# ══════════════════════════════════════════════════════════════════════

def sanitize_for_json(obj: Any) -> Any:
    """递归转换对象, 确保所有值对 JSON 安全 (NaN/Inf → null).

    这是 JSON 序列化的最后防线。应该在所有 json.dumps() 调用前使用。

    Args:
        obj: 任意 Python 对象 (dict, list, numpy array, scalar, etc.)

    Returns:
        JSON-safe 的等价对象
    """
    if obj is None:
        return None

    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [sanitize_for_json(v) for v in obj]

    if isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())

    if isinstance(obj, (np.floating, float)):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)

    if isinstance(obj, np.integer):
        return int(obj)

    if isinstance(obj, np.bool_):
        return bool(obj)

    if isinstance(obj, (bool, int, str)):
        return obj

    # 尝试转为基本类型
    try:
        return float(obj)
    except (TypeError, ValueError):
        pass

    return str(obj)


# ══════════════════════════════════════════════════════════════════════
# 数学安全函数
# ══════════════════════════════════════════════════════════════════════

def div0(a: float, b: float, default: float = 0.0) -> float:
    """安全除法 a / b, 分母为 0 或接近 0 时返回 default.

    Args:
        a: 分子
        b: 分母
        default: b ≈ 0 时的回退值

    Returns:
        a / b 或 default
    """
    b_safe = safe_float(b, default=0.0)
    if abs(b_safe) < 1e-9:
        return default
    return safe_float(safe_float(a, default=0.0) / b_safe, default=default)


def safe_corrcoef(a: np.ndarray, b: np.ndarray, default: float = 0.0) -> float:
    """安全计算两个数组的 Pearson 相关系数.

    处理 std=0 (常数序列) 产生的 NaN.

    Args:
        a: 第一个数组
        b: 第二个数组 (必须与 a 等长)
        default: 无法计算时的回退值 (默认 0.0 = 无相关)

    Returns:
        相关系数 [-1, 1] 或 default
    """
    if len(a) < 2 or len(b) < 2 or len(a) != len(b):
        return default
    if np.std(a) < 1e-9 or np.std(b) < 1e-9:
        return default
    try:
        c = float(np.corrcoef(a, b)[0, 1])
        return safe_float(c, default=default)
    except Exception:
        return default


def safe_corrcoef_or_half(a: np.ndarray, b: np.ndarray) -> float:
    """安全相关系数: 无法计算时返回 0.5 (弱正相关). 用于独立波动占比等场景."""
    return safe_corrcoef(a, b, default=0.5)


# ══════════════════════════════════════════════════════════════════════
# 质量检查
# ══════════════════════════════════════════════════════════════════════

def check_array_quality(arr: np.ndarray) -> dict:
    """检查数组质量: NaN 率, Inf 率, 覆盖率."""
    total = arr.size
    if total == 0:
        return {"total": 0, "nan_count": 0, "inf_count": 0, "valid_rate": 1.0}
    nan_count = int(np.isnan(arr).sum())
    inf_count = int(np.isinf(arr).sum())
    return {
        "total": total,
        "nan_count": nan_count,
        "inf_count": inf_count,
        "nan_rate": round(nan_count / total, 6),
        "inf_rate": round(inf_count / total, 6),
        "valid_rate": round(1.0 - (nan_count + inf_count) / total, 6),
    }
