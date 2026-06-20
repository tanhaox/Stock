"""Phase 0 数据同步 — 申万行业指数历史数据拉取."""
import asyncio, sys
from pathlib import Path
from datetime import date, timedelta
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.database import async_session_factory, engine
from app.services.tushare_common import call_tushare
from sqlalchemy import text

async def sync_sw_index():
    # 获取所有申万一级行业代码
    classify = await call_tushare("index_classify", {"level": "L1", "src": "SW2021"}, "index_code,industry_name")
    if not classify:
        print("ERROR: 无法获取申万行业分类")
        return

    sectors = {r["index_code"]: r["industry_name"] for r in classify}
    print(f"申万一级行业: {len(sectors)} 个")

    # 拉取历史数据 (最近 2 年)
    start = (date.today() - timedelta(days=730)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")

    total_inserted = 0
    for code, name in sectors.items():
        rows = await call_tushare("sw_daily", {
            "ts_code": code,
            "start_date": start,
            "end_date": end,
        }, "ts_code,trade_date,close,pct_chg")

        if not rows:
            print(f"  {code} {name}: 无数据")
            continue

        async with async_session_factory() as s:
            for r in rows:
                td_str = r["trade_date"]
                td = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
                await s.execute(text("""
                    INSERT INTO sw_sector_index (index_code, trade_date, close, pct_chg)
                    VALUES (:c, :d, :cl, :p)
                    ON CONFLICT (index_code, trade_date) DO UPDATE SET
                        close=EXCLUDED.close, pct_chg=EXCLUDED.pct_chg
                """), {
                    "c": r["ts_code"],
                    "d": td,
                    "cl": float(r.get("close", 0) or 0),
                    "p": float(r.get("pct_chg", 0) or 0),
                })
            await s.commit()
        total_inserted += len(rows)
        print(f"  {code} {name}: {len(rows)} 条")

    print(f"\n总计: {total_inserted} 条申万行业指数数据已入库")

async def detect_market_phases():
    """牛熊阶段自动划分并写入 market_status_log."""
    # 拉取上证指数历史数据
    idx_rows = await call_tushare("index_daily", {
        "ts_code": "000001.SH",
        "start_date": (date.today() - timedelta(days=730)).strftime("%Y%m%d"),
        "end_date": date.today().strftime("%Y%m%d"),
    }, "ts_code,trade_date,close")

    if not idx_rows:
        print("ERROR: 无法获取上证指数数据")
        return

    import pandas as pd
    import numpy as np

    df = pd.DataFrame([{
        "trade_date": r["trade_date"],
        "close": float(r.get("close", 0) or 0),
    } for r in idx_rows]).sort_values("trade_date")

    # 计算 MA60
    df["ma60"] = df["close"].rolling(60).mean()

    # 计算 20日波动率 (用于自适应阈值)
    df["ret_20"] = df["close"].pct_change(20)
    df["vol_20"] = df["ret_20"].rolling(60).std()

    # 牛熊判定: 1.5σ 阈值
    phases = []
    for i, row in df.iterrows():
        if pd.isna(row["ma60"]) or pd.isna(row["vol_20"]):
            phases.append("unknown")
            continue
        ratio = row["close"] / row["ma60"]
        # 1.5σ 自适应阈值: ret_20 和 vol_20 都是小数 (pct_change 返回小数)
        sigma = row["vol_20"]
        threshold = 1.5 * sigma if not pd.isna(sigma) else 0.03

        if row["ret_20"] > max(threshold, 0.02):   # 至少 2% 防止低波动误触发
            phases.append("bull")
        elif row["ret_20"] < -max(threshold, 0.02):
            phases.append("bear")
        else:
            phases.append("range")

    df["phase"] = phases

    # 合并短阶段 (< 30 天)
    df["phase_merged"] = df["phase"]
    current_phase = None
    current_start = 0
    for i in range(len(df)):
        if df.iloc[i]["phase"] != current_phase:
            if current_phase and i - current_start < 30:
                # 合并到相邻阶段
                prev_phase = df.iloc[max(0, current_start-1)]["phase_merged"]
                for j in range(current_start, i):
                    df.iloc[j, df.columns.get_loc("phase_merged")] = prev_phase
            current_phase = df.iloc[i]["phase"]
            current_start = i

    # 计算阶段持续天数
    phase_durations = []
    current = None
    count = 0
    for p in df["phase_merged"]:
        if p == current:
            count += 1
        else:
            current = p
            count = 1
        phase_durations.append(count)
    df["phase_duration"] = phase_durations

    # 写入数据库
    async with async_session_factory() as s:
        for _, row in df.iterrows():
            if pd.isna(row["ma60"]):
                continue
            td_str = str(row["trade_date"])[:10]  # 确保是 YYYY-MM-DD
            td = date.fromisoformat(td_str)
            await s.execute(text("""
                INSERT INTO market_status_log (trade_date, index_code, status, ma5, ma10, ma20, ma60, phase, ma60_value, phase_duration, adjustment_factor)
                VALUES (:d, '000001.SH', :st, 0, 0, 0, 0, :ph, CAST(:mv AS float8), :pd, 1.0)
                ON CONFLICT (trade_date, index_code) DO UPDATE SET
                    phase=EXCLUDED.phase, ma60_value=EXCLUDED.ma60_value, phase_duration=EXCLUDED.phase_duration
            """), {
                "d": td,
                "st": row["phase_merged"],
                "ph": row["phase_merged"],
                "mv": float(row["ma60"]) if not pd.isna(row["ma60"]) else 0,
                "pd": int(row["phase_duration"]),
            })
        await s.commit()

    # 统计
    bull_days = (df["phase_merged"] == "bull").sum()
    bear_days = (df["phase_merged"] == "bear").sum()
    range_days = (df["phase_merged"] == "range").sum()
    total = len(df[df["phase_merged"] != "unknown"])
    print(f"\n牛熊划分完成: {total} 个交易日")
    print(f"  牛市: {bull_days} 天 ({bull_days/total*100:.1f}%)")
    print(f"  熊市: {bear_days} 天 ({bear_days/total*100:.1f}%)")
    print(f"  震荡: {range_days} 天 ({range_days/total*100:.1f}%)")

async def main():
    print("=== Phase 0: 申万行业指数同步 ===")
    await sync_sw_index()

    print("\n=== Phase 0: 牛熊阶段划分 ===")
    await detect_market_phases()

    print("\n=== Phase 0 完成 ===")

if __name__ == "__main__":
    asyncio.run(main())
