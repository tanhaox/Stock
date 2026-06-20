#!/usr/bin/env python3
"""历史TG信号回填 — 用 daily_kline 批量生成 signal_history 训练标签.

原理:
  每天取全市场K线 → 跑TG指标 → 生成买入信号 → 计算T+5实际收益
  → 写入 signal_history (outcome_label: strong_win/weak_win/weak_loss/strong_loss)

用法:
  PYTHONPATH=. python -m scripts.backfill_signal_labels          # 回填最近 200 天
  PYTHONPATH=. python -m scripts.backfill_signal_labels --all    # 回填全部历史 (2015至今)
  PYTHONPATH=. python -m scripts.backfill_signal_labels --days 60  # 回填最近 60 天
"""
import asyncio, logging, sys
from datetime import date, timedelta
import numpy as np
import pandas as pd
from sqlalchemy import text
from app.core.database import async_session_factory
from app.services.tg_indicator import TGIndicator, _get_board_params

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_signals")

# TG 指标计算的最小K线数
MIN_KLINES = 60
# T+5 收益标签阈值
STRONG_WIN = 8.0   # >8% = 强赢
WEAK_WIN = 0.0     # >0%  = 弱赢
WEAK_LOSS = -5.0   # >-5% = 弱亏
# STRONG_LOSS: <= -5%

# 每次处理的最大股票数 (避免内存爆炸)
BATCH_SIZE = 500


async def get_trading_days(start_date: date, end_date: date) -> list[date]:
    """获取区间内所有交易日."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT DISTINCT trade_date FROM daily_kline
            WHERE trade_date BETWEEN :d1 AND :d2
            ORDER BY trade_date
        """), {"d1": start_date, "d2": end_date})
        return [row[0] for row in r.fetchall()]


async def get_all_symbols() -> list[str]:
    """获取所有有足够K线的股票."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code FROM daily_kline
            WHERE (ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%'
               OR ts_code LIKE '4%' OR ts_code LIKE '8%' OR ts_code LIKE '9%')
            GROUP BY ts_code HAVING COUNT(*) >= :m
        """), {"m": MIN_KLINES})
        return [row[0] for row in r.fetchall()]


async def load_klines(symbols: list[str], end_date: date, lookback: int = 200) -> dict[str, pd.DataFrame]:
    """批量加载 K 线数据."""
    cutoff = end_date - timedelta(days=lookback * 2)  # 足够宽的范围
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT ts_code, trade_date, open, high, low, close, volume
            FROM daily_kline
            WHERE ts_code = ANY(:syms) AND trade_date BETWEEN :d1 AND :d2
            ORDER BY ts_code, trade_date
        """), {"syms": symbols, "d1": cutoff, "d2": end_date})
        rows = r.fetchall()

    dfs = {}
    for row in rows:
        code = row[0]
        if code not in dfs:
            dfs[code] = []
        dfs[code].append({
            "Date": row[1], "Open": float(row[2] or 0), "High": float(row[3] or 0),
            "Low": float(row[4] or 0), "Close": float(row[5] or 0), "Volume": float(row[6] or 0),
        })

    result = {}
    for code, rows in dfs.items():
        if len(rows) >= MIN_KLINES:
            df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
            # 只保留到 end_date 的数据
            df = df[df["Date"] <= end_date]
            if len(df) >= MIN_KLINES:
                result[code] = df
    return result


def classify_outcome(ret_t5: float) -> str:
    if ret_t5 > STRONG_WIN: return "strong_win"
    elif ret_t5 > WEAK_WIN: return "weak_win"
    elif ret_t5 > WEAK_LOSS: return "weak_loss"
    else: return "strong_loss"


async def backfill_day(trade_date: date, symbols: list[str], dry_run: bool = False) -> int:
    """对指定交易日批量生成信号 + T+5 标签.

    Returns: 写入的 signal_history 条数
    """
    # 分批加载 K 线
    inserted = 0
    for batch_start in range(0, len(symbols), BATCH_SIZE):
        batch_syms = symbols[batch_start:batch_start + BATCH_SIZE]
        kline_map = await load_klines(batch_syms, trade_date)
        if not kline_map:
            continue

        rows_to_insert = []
        async with async_session_factory() as s:
            # 批量加载 T+5 收益
            t5_dates = [trade_date + timedelta(days=i) for i in range(1, 11)]
            for ts_code, df in kline_map.items():
                try:
                    indicator = TGIndicator(df, tg_signal_params=_get_board_params(ts_code))
                    full_df = indicator.compute()
                except Exception:
                    continue

                last = full_df.iloc[-1]
                if not last["买方向"] or int(last["层级买终"]) < 1:
                    continue

                close_price = float(last["Close"])
                if close_price <= 0:
                    continue

                # 计算 T+5 收益
                future = df[df["Date"] > trade_date]
                if len(future) < 2:
                    continue
                t5_idx = min(5, len(future) - 1)
                t5_close = float(future.iloc[t5_idx]["Close"])
                if t5_close <= 0:
                    continue
                ret_t5 = round((t5_close - close_price) / close_price * 100, 2)

                # T+1/2/3
                ret_t1 = ret_t2 = ret_t3 = None
                if len(future) >= 2:
                    ret_t1 = round((float(future.iloc[0]["Close"]) - close_price) / close_price * 100, 2)
                if len(future) >= 3:
                    ret_t2 = round((float(future.iloc[1]["Close"]) - close_price) / close_price * 100, 2)
                if len(future) >= 4:
                    ret_t3 = round((float(future.iloc[2]["Close"]) - close_price) / close_price * 100, 2)

                # 价格区间
                prices_60 = [float(row["Close"]) for _, row in df.tail(60).iterrows()]
                price_high = max(prices_60)
                price_low = min(prices_60)
                price_width = round((price_high - price_low) / price_low * 100, 2) if price_low > 0 else 0

                outcome = classify_outcome(ret_t5)

                rows_to_insert.append({
                    "symbol": ts_code, "scan_date": trade_date,
                    "composite_score": 0,  # 历史回填无评分, 0 表示未评分
                    "archetype": "unknown", "market": "主板",
                    "push_count_30d": 1,
                    "price_zone_high": round(price_high, 2),
                    "price_zone_low": round(price_low, 2),
                    "price_zone_width_pct": price_width,
                    "ret_t1": ret_t1, "ret_t2": ret_t2, "ret_t3": ret_t3, "ret_t5": ret_t5,
                    "max_gain_pct": max(0.0, float(future["High"].max()) / close_price * 100 - 100) if len(future) > 0 else 0,
                    "max_loss_pct": min(0.0, float(future["Low"].min()) / close_price * 100 - 100) if len(future) > 0 else 0,
                    "outcome_label": outcome,
                    "deception_type": "normal",
                })

        if rows_to_insert and not dry_run:
            async with async_session_factory() as s:
                for r in rows_to_insert:
                    await s.execute(text("""INSERT INTO signal_history
                        (symbol, scan_date, composite_score, archetype, market,
                         push_count_30d, price_zone_high, price_zone_low, price_zone_width_pct,
                         ret_t1, ret_t2, ret_t3, ret_t5, max_gain_pct, max_loss_pct,
                         outcome_label, deception_type)
                        VALUES (:symbol, :scan_date, :composite_score, :archetype, :market,
                         :push_count_30d, :price_zone_high, :price_zone_low, :price_zone_width_pct,
                         :ret_t1, :ret_t2, :ret_t3, :ret_t5, :max_gain_pct, :max_loss_pct,
                         :outcome_label, :deception_type)
                        ON CONFLICT (symbol, scan_date) DO UPDATE SET
                         ret_t5=EXCLUDED.ret_t5, outcome_label=EXCLUDED.outcome_label"""),
                        r)
                await s.commit()
                inserted += len(rows_to_insert)

        if batch_start % (BATCH_SIZE * 5) == 0:
            logger.info(f"  {trade_date}: {min(batch_start + BATCH_SIZE, len(symbols))}/{len(symbols)} → {inserted} inserted")

    return inserted


async def main():
    all_syms = await get_all_symbols()
    logger.info(f"Total symbols with >= {MIN_KLINES} K-lines: {len(all_syms)}")

    if "--all" in sys.argv:
        start = date(2015, 1, 5)
        end = date.today() - timedelta(days=10)  # need T+5 future K-lines
    elif "--days" in sys.argv:
        idx = sys.argv.index("--days")
        days = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 200
        start = date.today() - timedelta(days=days + 10)
        end = date.today() - timedelta(days=10)
    else:
        start = date.today() - timedelta(days=210)
        end = date.today() - timedelta(days=10)
    trading_days = await get_trading_days(start, end)
    if not trading_days:
        logger.error(f"No trading days between {start} and {end}")
        return
    logger.info(f"Backfilling {len(trading_days)} trading days: {trading_days[0]} -> {trading_days[-1]}")

    total = 0
    dry_run = "--dry" in sys.argv
    for i, td in enumerate(trading_days):
        n = await backfill_day(td, all_syms, dry_run=dry_run)
        total += n
        if (i + 1) % 20 == 0:
            logger.info(f"Progress: {i+1}/{len(trading_days)}, total={total}")

    logger.info(f"Backfill complete: {total} signals across {len(trading_days)} days")


if __name__ == "__main__":
    asyncio.run(main())
