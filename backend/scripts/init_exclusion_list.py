"""Exclusion List 初始化脚本 (v7.0.34).

用途: 首次部署时一次性把 exclude 表 + exclusion_reasons 字典 + 基础数据都建好.

执行:  python -m scripts.init_exclusion_list
"""
import asyncio
import logging
import sys
from pathlib import Path

# 让脚本可独立运行: 找 backend 根目录
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text
from app.core.database import async_session_factory
from app.models.data_models import ensure_indexes

logger = logging.getLogger(__name__)


async def main_async():
    print("=" * 60)
    print("  Stock Analyst v7.0.34 — Exclusion List 初始化")
    print("=" * 60)

    # Step 1: ensure_indexes 会建表 + 写字典 (idempotent)
    print("\n[1/3] ensure_indexes() (建表 + exclusion_reasons 字典)...")
    await ensure_indexes()
    print("  ✓ 完成")

    # Step 2: 验证表是否存在 + 行数
    print("\n[2/3] 验证表与行数...")
    async with async_session_factory() as s:
        for tbl in ("exclusion_reasons", "exclusion_list"):
            r = await s.execute(text(f"SELECT COUNT(*) FROM {tbl}"))
            cnt = r.scalar()
            print(f"  {tbl}: {cnt} rows")

    # Step 3: 提示首次需要手动跑 refresh
    print("\n[3/3] 提示下一步...")
    print("  首次部署需手动触发: POST /api/admin/refresh-exclusion")
    print("  或运行: python -m scripts.refresh_exclusion_list")

    print("\n" + "=" * 60)
    print("  ✓ 初始化完成")
    print("=" * 60)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
