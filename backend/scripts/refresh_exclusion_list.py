"""Exclusion List 刷新脚本 (v7.0.34).

提供 admin.py 调用的纯函数:
  - fetch_st_from_tushare()  →  真实 ST/*ST/PT 名单
  - fetch_pe_loss_from_tushare()  →  PE 亏损股
  - fetch_insolvent_from_tushare()  →  资不抵债股
  - refresh_tech_bj_baseline()  →  科创板/北交所基础名单

可独立运行:  python -m scripts.refresh_exclusion_list
"""
import asyncio
import logging
from datetime import date, datetime
from typing import List, Tuple, Optional

from sqlalchemy import text

from app.core.config import settings
from app.services.tushare_common import call_tushare
from app.core.database import async_session_factory

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 1. Tushare ST 名单 (实时)
# ════════════════════════════════════════════════════════════════

def fetch_st_from_tushare() -> List[Tuple[str, str, str]]:
    """拉取 Tushare stock_st 全部当前 ST/*ST/PT 名单.

    Returns: list of (ts_code, name, type) — 同步阻塞函数.
    """
    async def _fetch():
        rows = await call_tushare(
            "stock_st",
            {},
            fields="ts_code,name,type"
        )
        return [(r["ts_code"], r.get("name", ""), r.get("type", "ST"))
                for r in rows if r.get("ts_code")]

    return asyncio.run(_fetch())


# ════════════════════════════════════════════════════════════════
# 2. Tushare PE 亏损 (income_vip 季度)
# ════════════════════════════════════════════════════════════════

def fetch_pe_loss_from_tushare(
    period: str,
    fallback_period: Optional[str] = None,
) -> Tuple[List[str], Optional[str], Optional[dict]]:
    """从 income_vip 拉取 n_income < 0 的 ts_code 列表.

    Args:
        period: 期望的报告期 e.g. "20250331"
        fallback_period: 备选报告期 (上季度)

    Returns: (loss_codes, used_period, sample_row)
    """
    async def _fetch():
        # 1) 尝试当期
        rows = await call_tushare(
            "income_vip",
            {"period": period, "fields": "ts_code,n_income"},
            fields="ts_code,n_income"
        )
        used = period
        if not rows and fallback_period:
            logger.info(f"income_vip {period} 无数据, 退回 {fallback_period}")
            rows = await call_tushare(
                "income_vip",
                {"period": fallback_period, "fields": "ts_code,n_income"},
                fields="ts_code,n_income"
            )
            used = fallback_period if rows else None

        if not rows:
            return [], None, None

        loss_codes = [r["ts_code"] for r in rows
                      if r.get("n_income") is not None and r["n_income"] < 0]
        sample = rows[0] if rows else None
        return loss_codes, used, sample

    return asyncio.run(_fetch())


# ════════════════════════════════════════════════════════════════
# 3. Tushare 资不抵债 (balancesheet_vip 季度)
# ════════════════════════════════════════════════════════════════

def fetch_insolvent_from_tushare(
    period: str,
    fallback_period: Optional[str] = None,
) -> Tuple[List[Tuple[str, str, float, float]], Optional[str]]:
    """从 balancesheet_vip 拉取 total_liab > total_assets 的资不抵债股.

    Returns: list of (ts_code, name, total_assets, total_liab), used_period
    """
    async def _fetch():
        rows = await call_tushare(
            "balancesheet_vip",
            {"period": period, "fields": "ts_code,name,total_assets,total_liab"},
            fields="ts_code,name,total_assets,total_liab"
        )
        used = period
        if not rows and fallback_period:
            rows = await call_tushare(
                "balancesheet_vip",
                {"period": fallback_period, "fields": "ts_code,name,total_assets,total_liab"},
                fields="ts_code,name,total_assets,total_liab"
            )
            used = fallback_period if rows else None

        if not rows:
            return [], None

        insolv = []
        for r in rows:
            ta = r.get("total_assets")
            tl = r.get("total_liab")
            if ta and tl and tl > ta:
                insolv.append((r["ts_code"], r.get("name", ""), float(ta), float(tl)))
        return insolv, used

    return asyncio.run(_fetch())


# ════════════════════════════════════════════════════════════════
# 4. 基础结构: 科创板 (688) + 北交所 (920/83/87)
# ════════════════════════════════════════════════════════════════

async def refresh_tech_bj_baseline(s, asf) -> Tuple[int, int]:
    """维护 TECH_BOARD (688) + BJ_BOARD (920/83/87) 全市场基础名单.

    Returns: (tech_inserted, bj_inserted)
    """
    tech_count = 0
    bj_count = 0

    # 1) 科创板: stock_basic list_status='L', ts_code startswith '688'
    try:
        rows = await call_tushare(
            "stock_basic",
            {"list_status": "L", "exchange": "SSE",
             "fields": "ts_code,name,list_status,exchange"},
            fields="ts_code,name,list_status,exchange"
        )
        for r in rows:
            if r.get("ts_code", "").startswith("688"):
                await s.execute(text("""
                    INSERT INTO exclusion_list (symbol, reason_code, added_at, expires_at, note)
                    VALUES (:sym, 'TECH_BOARD', NOW(), NULL, :note)
                    ON CONFLICT (symbol) DO UPDATE
                    SET reason_code = 'TECH_BOARD', expires_at = NULL, added_at = NOW()
                """), {"sym": r["ts_code"], "note": f"科创板 {r.get('name','')}"})
                tech_count += 1
        await s.commit()
    except Exception as e:
        logger.warning(f"TECH_BOARD 同步失败: {e}")

    # 2) 北交所: stock_basic exchange='BSE'
    try:
        rows = await call_tushare(
            "stock_basic",
            {"list_status": "L", "exchange": "BSE",
             "fields": "ts_code,name,list_status,exchange"},
            fields="ts_code,name,list_status,exchange"
        )
        for r in rows:
            await s.execute(text("""
                INSERT INTO exclusion_list (symbol, reason_code, added_at, expires_at, note)
                VALUES (:sym, 'BJ_BOARD', NOW(), NULL, :note)
                ON CONFLICT (symbol) DO UPDATE
                SET reason_code = 'BJ_BOARD', expires_at = NULL, added_at = NOW()
            """), {"sym": r["ts_code"], "note": f"北交所 {r.get('name','')}"})
            bj_count += 1
        await s.commit()
    except Exception as e:
        logger.warning(f"BJ_BOARD 同步失败: {e}")

    return tech_count, bj_count


# ════════════════════════════════════════════════════════════════
# 独立运行入口
# ════════════════════════════════════════════════════════════════

async def main_async():
    """直接跑这个脚本可以一次性做完所有踢出名单维护."""
    t0 = datetime.now()
    print(f"=== refresh_exclusion_list @ {t0.isoformat()} ===")

    # 1) TECH_BJ 基础
    async with async_session_factory() as s:
        tech_n, bj_n = await refresh_tech_bj_baseline(s, async_session_factory)
    print(f"  TECH_BOARD: {tech_n}, BJ_BOARD: {bj_n}")

    # 2) ST
    try:
        st_list = fetch_st_from_tushare()
        print(f"  ST_NAME: {len(st_list)} from Tushare")
    except Exception as e:
        print(f"  ST_NAME 失败: {e}")

    print(f"=== 完成 ({(datetime.now() - t0).total_seconds():.1f}s) ===")


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
