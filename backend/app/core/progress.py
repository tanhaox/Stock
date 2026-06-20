"""统一进度回调协议 (P0 系统级).

消除 3 种不一致的回调签名:
  cb(phase, msg)              ← 2 参数 (news_pipeline)
  cb(step, total, label)      ← 3 参数 (deep_scorer 内部)
  cb(phase, current, total, extra) ← 4 参数 (标准)

统一为 TypedDict 协议: ProgressCallback(phase, current, total, message)
"""
from typing import Protocol, Callable, Awaitable


class ProgressCallback(Protocol):
    """标准进度回调: cb(phase: str, current: int, total: int, message: str = "")"""

    async def __call__(self, phase: str, current: int, total: int, message: str = "") -> None: ...


def make_progress_adapter(
    cb: Callable[..., Awaitable[None]],
    prefix: str = "",
    default_total: int = 1,
) -> ProgressCallback:
    """将任意回调包装为标准 4 参数签名.

    支持:
      - cb(phase, msg)                      → 自动补 current=0, total=1
      - cb(step, total, label)              → 重新映射为 phase=step_str
      - cb(phase, current, total, message)  → 直通
    """
    import inspect

    try:
        sig = inspect.signature(cb)
        param_count = len([p for p in sig.parameters.values()
                          if p.default is inspect.Parameter.empty])
    except (ValueError, TypeError):
        param_count = 4  # 猜测是标准签名

    if param_count <= 2:
        # 2 参数适配: cb(phase, msg) → 标准
        async def _wrapped(phase: str, current: int, total: int, message: str = "") -> None:
            full_msg = message or f"{current}/{total}"
            await cb(f"{prefix}{phase}" if prefix else phase, full_msg)
        return _wrapped

    elif param_count == 3:
        # 3 参数适配: cb(step, total, label) → 标准
        async def _wrapped(phase: str, current: int, total: int, message: str = "") -> None:
            await cb(current, total, message or phase)
        return _wrapped

    else:
        # 4 参数: 直通, 加 prefix
        async def _wrapped(phase: str, current: int, total: int, message: str = "") -> None:
            await cb(f"{prefix}{phase}" if prefix else phase, current, total, message)
        return _wrapped


class NoopProgress:
    """无操作进度回调 — 不需要进度追踪时的默认值."""

    async def __call__(self, phase: str, current: int, total: int, message: str = "") -> None:
        pass
