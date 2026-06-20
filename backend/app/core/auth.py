"""极简认证依赖 — 从 X-User-ID Header 提取用户标识.

过渡方案。后续升级为 JWT 中间件时替换此模块即可，调用方无需改动.
"""
from fastapi import Header, HTTPException


async def get_current_user(x_user_id: str = Header(default=None)) -> str:
    """从请求头提取用户ID，拒绝硬编码占位符."""
    if not x_user_id:
        raise HTTPException(status_code=401, detail="缺少 X-User-ID 请求头")
    # 拒绝已知的硬编码占位符(审计发现的遗留值)
    if x_user_id in ("344b28da-55ca-4ca3-a9f8-ca3c4c69545b", "user_paste"):
        raise HTTPException(status_code=401, detail="请使用真实用户ID，不接受硬编码占位符")
    return x_user_id
