"""Admin API - 数据维护任务路由 (v7.0.34).

提供手动触发后端数据维护脚本的入口, 前端 ScanPage 可调用.
"""
import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.core.database import async_session_factory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])


class RefreshExclusionResponse(BaseModel):
    """刷新排除名单的响应."""
    deleted: int
    new_or_updated: int
    by_reason: dict
    by_market_style: dict
    quarter: str
    latest_pe_date: str
    duration_sec: float


def quarter_str(today: date) -> str:
    q = (today.month - 1) // 3 + 1
    return f"Q{q}-{today.year}"


def quarter_end(today: date) -> date:
    q = (today.month - 1) // 3 + 1
    if q == 1: return date(today.year, 3, 31)
    if q == 2: return date(today.year, 6, 30)
    if q == 3: return date(today.year, 9, 30)
    return date(today.year, 12, 31)


@router.post("/refresh-exclusion", response_model=RefreshExclusionResponse)
async def refresh_exclusion_list():
    """刷新踢出名单 (PE 亏损 + 科创/北证/ST).

    v7.0.34: 季度手动触发 (前端 ScanPage "股票信息对齐" 按钮).
    """
    import time
    t0 = time.time()
    today = date.today()
    quarter = quarter_str(today)
    quarter_expires = datetime.combine(quarter_end(today) + timedelta(days=1), datetime.min.time())

    async with async_session_factory() as s:
        # -1. 基础结构: TECH_BOARD (688) + BJ_BOARD (920) 全市场代码
        tech_bj_inserted = 0
        try:
            from scripts.refresh_exclusion_list import refresh_tech_bj_baseline
            tech_n, bj_n = await refresh_tech_bj_baseline(s, async_session_factory)
            tech_bj_inserted = tech_n + bj_n
        except Exception as e:
            logger.warning(f"TECH_BJ 基础结构更新失败: {e}")

        # 0. 同步 ST_NAME 列表 (Tushare stock_st, 真实 ST/*ST/PT 名单)
        st_codes_now: set[str] = set()
        st_inserted = 0
        st_deleted = 0
        try:
            from scripts.refresh_exclusion_list import fetch_st_from_tushare
            st_list = fetch_st_from_tushare()
            st_codes_now = {x[0] for x in st_list}
            r = await s.execute(text("""
                DELETE FROM exclusion_list
                WHERE reason_code = 'ST_NAME'
                  AND symbol NOT IN (SELECT unnest(CAST(:syms AS text[])))
            """), {"syms": list(st_codes_now)})
            st_deleted = r.rowcount
            new_st_rows = [{"sym": x[0], "note": f"Tushare stock_st: {x[1]} ({x[2]}) @ {date.today().isoformat()}"}
                           for x in st_list]
            if new_st_rows:
                await s.execute(text("""
                    INSERT INTO exclusion_list (symbol, reason_code, added_at, expires_at, note)
                    VALUES (:sym, 'ST_NAME', NOW(), NULL, :note)
                    ON CONFLICT (symbol) DO UPDATE
                    SET reason_code = 'ST_NAME', note = EXCLUDED.note, expires_at = NULL, added_at = NOW()
                """), new_st_rows)
                st_inserted = len(new_st_rows)
            await s.commit()
        except Exception as e:
            logger.warning(f"stock_st 同步失败 (fallback 到正则在 tg_engine): {e}")

        # 季度切换计算 (在 INSOLVENT 和 PE_LOSS 之前)
        current_period = f"{today.year}{(today.month-1)//3*3 + 3:02d}31"
        prev_quarter_month = (today.month - 1) // 3 * 3
        fallback_period = f"{today.year}{prev_quarter_month:02d}31" if prev_quarter_month > 0 else None
        if current_period == fallback_period:
            fallback_period = None

        # 0.5 同步 INSOLVENT (Tushare balancesheet_vip, 资不抵债)
        try:
            from scripts.refresh_exclusion_list import fetch_insolvent_from_tushare
            insolv_list, insolv_period = fetch_insolvent_from_tushare(current_period, fallback_period)
            insolv_codes_now = {x[0] for x in insolv_list}
            r = await s.execute(text("""
                DELETE FROM exclusion_list
                WHERE reason_code = 'INSOLVENT'
                  AND symbol NOT IN (SELECT unnest(CAST(:syms AS text[])))
            """), {"syms": list(insolv_codes_now)})
            insolv_deleted = r.rowcount
            new_insolv_rows = [{"sym": x[0], "note": f"Tushare balancesheet_vip: total_liab/total_assets={x[3]:.2f} @ {insolv_period}"}
                              for x in insolv_list]
            if new_insolv_rows:
                await s.execute(text("""
                    INSERT INTO exclusion_list (symbol, reason_code, added_at, expires_at, note)
                    VALUES (:sym, 'INSOLVENT', NOW(), NULL, :note)
                    ON CONFLICT (symbol) DO UPDATE
                    SET reason_code = 'INSOLVENT', note = EXCLUDED.note, expires_at = NULL, added_at = NOW()
                """), new_insolv_rows)
                insolv_inserted = len(new_insolv_rows)
            else:
                insolv_inserted = 0
            await s.commit()
        except Exception as e:
            logger.warning(f"balancesheet_vip 同步失败 (跳过 INSOLVENT): {e}")
            insolv_codes_now = set()
            insolv_inserted = 0
            insolv_deleted = 0

        # 1. 清理老 PE_LOSS
        r = await s.execute(text("DELETE FROM exclusion_list WHERE reason_code = 'PE_LOSS'"))
        deleted_pe = r.rowcount

        # 2. Tushare income_vip 取 PE 亏损股
        loss_codes: list[str] = []
        used_period = None

        try:
            from scripts.refresh_exclusion_list import fetch_pe_loss_from_tushare
            loss_codes, used_period, _ = fetch_pe_loss_from_tushare(current_period, fallback_period)
        except Exception as e:
            logger.error(f"income_vip 失败, fallback 到 daily_basic: {e}")
            r = await s.execute(text("""
                SELECT DISTINCT ts_code FROM daily_basic
                WHERE trade_date = (SELECT MAX(trade_date) FROM daily_basic WHERE pe_ttm IS NOT NULL)
                  AND pe_ttm <= 0
            """))
            loss_codes = [row[0] for row in r.fetchall()]

        # 3. 排除已踢出的 (TECH/BJ/ST/INSOLVENT)
        r = await s.execute(text("""
            SELECT symbol FROM exclusion_list
            WHERE reason_code IN ('TECH_BOARD', 'BJ_BOARD', 'ST_NAME', 'INSOLVENT')
        """))
        already_excluded = {row[0] for row in r.fetchall()}

        new_rows = []
        for ts_code in loss_codes:
            if ts_code in already_excluded:
                continue
            new_rows.append({
                "sym": ts_code,
                "code": "PE_LOSS",
                "note": f"{quarter} n_income<0 @ {used_period}",
                "expires": quarter_expires,
            })

        if new_rows:
            await s.execute(text("""
                INSERT INTO exclusion_list (symbol, reason_code, added_at, expires_at, note)
                VALUES (:sym, :code, NOW(), :expires, :note)
                ON CONFLICT (symbol) DO UPDATE
                SET reason_code = EXCLUDED.reason_code,
                    expires_at = EXCLUDED.expires_at,
                    note = EXCLUDED.note,
                    added_at = NOW()
            """), new_rows)
            await s.commit()

        # 4. 汇总
        r = await s.execute(text("""
            SELECT reason_code, COUNT(*),
                   SUM(CASE WHEN expires_at IS NOT NULL THEN 1 ELSE 0 END) as with_expires
            FROM exclusion_list GROUP BY reason_code
        """))
        by_reason: dict = {}
        by_market_style: dict = {"永久": 0, "临时": 0}
        for row in r.fetchall():
            by_reason[row[0]] = row[1]
            by_market_style["临时"] += row[2]
            by_market_style["永久"] += (row[1] - row[2])

    duration = round(time.time() - t0, 2)
    logger.info(
        f"[admin] 踢出名单刷新完成: TECH_BJ={tech_bj_inserted}, "
        f"PE清理{deleted_pe}, 新增PE{len(new_rows)}, "
        f"ST清理{st_deleted}, 新增ST{st_inserted}, "
        f"INSOLVENT清理{insolv_deleted}, 新增{insolv_inserted}, "
        f"by_reason={by_reason}, 耗时{duration}s"
    )
    return RefreshExclusionResponse(
        deleted=deleted_pe,
        new_or_updated=len(new_rows),
        by_reason=by_reason,
        by_market_style=by_market_style,
        quarter=quarter,
        latest_pe_date=str(used_period or ""),
        duration_sec=duration,
    )


@router.get("/exclusion-stats")
async def exclusion_stats():
    """查询当前 exclusion_list 状态 (前端调试用)."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT reason_code, COUNT(*),
                   SUM(CASE WHEN expires_at IS NOT NULL THEN 1 ELSE 0 END) as with_expires,
                   MIN(added_at) as first_added,
                   MAX(added_at) as last_added
            FROM exclusion_list
            GROUP BY reason_code
        """))
        result = []
        for row in r.fetchall():
            result.append({
                "reason": row[0],
                "total": row[1],
                "with_expires": row[2],
                "permanent": row[1] - row[2],
                "first_added": str(row[3]) if row[3] else None,
                "last_added": str(row[4]) if row[4] else None,
            })
        return {"by_reason": result}