"""TG 全市场扫描引擎 — 两阶段：下载新数据 → 本地扫描  → 周线信号叠加.

v7.0.11: TGIndicator.compute() 是纯 CPU 密集 (603 字符纯 pandas/numpy),
           5000 只股票单循环串行只用了 1/32 核. 用 ProcessPoolExecutor 并行化 8-16 倍.
"""
import logging, pandas as pd
import numpy as np
import os
import asyncio
import concurrent.futures as cf
from datetime import date, datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import async_session_factory
from app.services.tg_indicator import TGIndicator, _get_board_params


def _get_market(ts_code: str) -> str:
    """判断板块: 主板 / 中小板 / 创业板."""
    code = ts_code.replace('.SZ', '').replace('.SH', '').replace('.BJ', '')
    if code.startswith('300') or code.startswith('301') or code.startswith('688'):
        return "创业板"
    if code.startswith('002') or code.startswith('003'):
        return "中小板"
    return "主板"
from app.services.tushare_common import call_tushare

logger = logging.getLogger(__name__)


async def get_stock_list() -> pd.DataFrame:
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT DISTINCT ts_code FROM daily_kline "
            "WHERE ts_code NOT LIKE '000%SH' "
            "AND ts_code NOT LIKE '399%SZ' "
            "AND ts_code NOT LIKE '000%SZ'"
        ))
        codes = [row[0] for row in r.fetchall()]
    data = [{"ts_code": c, "name": c, "industry": ""} for c in codes]
    return pd.DataFrame(data)


async def get_latest_kline_date() -> date | None:
    """获取个股日线最新交易日期 (排除指数 .SH .SZ 避免 Phase 27 index_daily 同步污染)."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT MAX(trade_date) FROM daily_kline "
            "WHERE ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%' "
            "OR ts_code LIKE '4%' OR ts_code LIKE '8%' OR ts_code LIKE '9%'"
        ))
        row = r.fetchone()
        return row[0] if row and row[0] else None


async def get_kline_coverage(trade_date: date) -> tuple[int, int]:
    """返回 (已有该日期数据的个股数, 总个股数)，用于判断覆盖率."""
    stock_filter = "ts_code LIKE '6%' OR ts_code LIKE '0%' OR ts_code LIKE '3%' OR ts_code LIKE '4%' OR ts_code LIKE '8%' OR ts_code LIKE '9%'"
    async with async_session_factory() as s:
        total_r = await s.execute(text(
            f"SELECT COUNT(DISTINCT ts_code) FROM daily_kline WHERE {stock_filter}"
        ))
        total = total_r.scalar() or 0
        cov_r = await s.execute(text(
            f"SELECT COUNT(DISTINCT ts_code) FROM daily_kline WHERE trade_date = :d AND ({stock_filter})"
        ), {"d": trade_date})
        covered = cov_r.scalar() or 0
    return covered, total


# ── 阶段一：下载最新日线数据 ──────────────────────

async def download_latest_kline(progress_callback=None) -> tuple[int, list[str]]:
    """从 Tushare 下载所有股票的最新日线数据(智能批量模式).

    优先使用批量模式(一次 API 调用获取全部股票)，
    如数据被截断则回退到并发批量模式(15只股票并发，无单股sleep)。
    返回 (插入的新行数, 更新的股票代码列表).
    """
    latest_date = await get_latest_kline_date()
    updated_symbols: list[str] = []  # P2-6: 追踪更新的股票
    if latest_date is None:
        start_date = (date.today() - timedelta(days=365)).strftime("%Y%m%d")
    else:
        start_date = (latest_date + timedelta(days=1)).strftime("%Y%m%d")

    end_date = date.today().strftime("%Y%m%d")

    # 覆盖率检查：总是检查最新日期覆盖率 (Phase 36 修复, v4.8: 缺失时回退 1 年)
    skip_download = False
    if latest_date is not None:
        covered, total = await get_kline_coverage(latest_date)
        coverage_pct = (covered / total * 100) if total > 0 else 100
        logger.info(f"K-line coverage check: {covered}/{total} stocks ({coverage_pct:.1f}%) on {latest_date}")
        if start_date > end_date and coverage_pct >= 95.0:
            logger.info("K-line data is up to date, skipping download")
            skip_download = True
        elif coverage_pct < 95.0:
            # v4.8: 覆盖率不足, 回退到 1 年前重新下载 (避免漏掉中间缺失日)
            start_date = (date.today() - timedelta(days=365)).strftime("%Y%m%d")
            logger.warning(f"Coverage {coverage_pct:.1f}% < 95%, backfilling from {start_date}")

    if skip_download:
        if progress_callback:
            await progress_callback("download", 0, 0, extra=f"数据已是最新(覆盖率{covered}/{total})，跳过下载")
        return 0, updated_symbols

    from datetime import datetime as dt
    logger.info(f"Downloading kline from {start_date} to {end_date}")

    inserted = 0
    api_calls = 0

    # ── 获取交易日历 ──
    trading_days: list[str] = []
    try:
        cal = await call_tushare("trade_cal", {
            "exchange": "SSE", "start_date": start_date, "end_date": end_date, "is_open": "1"
        }, "cal_date")
        trading_days = sorted([r.get("cal_date", "") for r in cal if r.get("cal_date")])
    except Exception as e:
        logger.warning(f"trade_cal failed, using weekday fallback: {e}")
        from datetime import timedelta as td
        sd = dt.strptime(start_date, "%Y%m%d").date()
        ed = dt.strptime(end_date, "%Y%m%d").date()
        d = sd
        while d <= ed:
            if d.weekday() < 5:
                trading_days.append(d.strftime("%Y%m%d"))
            d += td(days=1)

    if not trading_days:
        if progress_callback:
            await progress_callback("download", 0, 0, extra="无交易日需要下载")
        return 0, []

    total_days = len(trading_days)
    if progress_callback:
        await progress_callback("download", 0, total_days, extra=f"将下载 {total_days} 个交易日数据...")

    async def _insert_rows(rows: list[dict]) -> tuple[int, list[str]]:
        """插入 K 线行，返回 (插入数, 股票代码列表)。"""
        if not rows:
            return 0, []
        symbols: list[str] = []
        async with async_session_factory() as s:
            for r in rows:
                td_str = r.get("trade_date", "")
                if not td_str:
                    continue
                try:
                    td_dt = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
                except (ValueError, IndexError):
                    continue
                await s.execute(text("""
                    INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, volume, amount)
                    VALUES (:ts, :td, :o, :h, :l, :c, :v, :a)
                    ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                        open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                        close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount
                """), {
                    "ts": r["ts_code"], "td": td_dt,
                    "o": float(r.get("open", 0) or 0),
                    "h": float(r.get("high", 0) or 0),
                    "l": float(r.get("low", 0) or 0),
                    "c": float(r.get("close", 0) or 0),
                    "v": float(r.get("vol", 0) or 0),
                    "a": float(r.get("amount", 0) or 0),
                })
                symbols.append(r["ts_code"])
            await s.commit()
        return len(rows), symbols

    # ── 尝试批量模式(一次调用获取所有股票) ──
    MAX_BULK = 6000  # Tushare 单次返回上限
    for day_i, trade_date in enumerate(trading_days):
        if progress_callback:
            await progress_callback("download", day_i, total_days,
                                    extra=f"正在下载 {trade_date} 数据...")

        # 同时下载上证指数日线 (index_daily → daily_kline)
        idx_rows = await call_tushare(
            "index_daily",
            {"ts_code": "000001.SH", "start_date": trade_date, "end_date": trade_date},
            "ts_code,trade_date,open,high,low,close,vol,amount",
        )
        if idx_rows:
            async with async_session_factory() as s:
                for r in idx_rows:
                    td_str = r.get("trade_date", "")
                    if not td_str: continue
                    try:
                        td_dt = date(int(td_str[:4]), int(td_str[4:6]), int(td_str[6:8]))
                    except (ValueError, IndexError): continue
                    await s.execute(text("""
                        INSERT INTO daily_kline (ts_code, trade_date, open, high, low, close, volume, amount)
                        VALUES (:ts, :td, :o, :h, :l, :c, :v, :a)
                        ON CONFLICT (ts_code, trade_date) DO UPDATE SET
                            open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                            close=EXCLUDED.close, volume=EXCLUDED.volume, amount=EXCLUDED.amount
                    """), {
                        "ts": r["ts_code"], "td": td_dt,
                        "o": float(r.get("open", 0) or 0), "h": float(r.get("high", 0) or 0),
                        "l": float(r.get("low", 0) or 0), "c": float(r.get("close", 0) or 0),
                        "v": float(r.get("vol", 0) or 0), "a": float(r.get("amount", 0) or 0),
                    })
                await s.commit()
            logger.info(f"Downloaded index daily for {trade_date}: {len(idx_rows)} rows")

        rows = await call_tushare(
            "daily",
            {"trade_date": trade_date},
            "ts_code,trade_date,open,high,low,close,vol,amount",
        )
        api_calls += 1

        # 全市场约 5500 只，≥5500 视为完整，< 5500 可能截断
        if rows and len(rows) >= 5500:
            n, syms = await _insert_rows(rows)
            inserted += n
            updated_symbols.extend(syms)
            logger.info(f"Bulk mode: {n} rows for {trade_date}")
        elif rows:
            logger.warning(f"Bulk mode truncated at {len(rows)} rows, falling back to batched mode")
            # 回退：并发批量下载
            stock_list = await get_stock_list()
            symbols = stock_list["ts_code"].tolist()
            logger.info(f"Batched mode: {len(symbols)} stocks for {trade_date}")

            sem = _asyncio.Semaphore(15)

            async def _fetch_one(sym: str) -> list[dict]:
                async with sem:
                    try:
                        return await call_tushare(
                            "daily",
                            {"ts_code": sym, "trade_date": trade_date},
                            "ts_code,trade_date,open,high,low,close,vol,amount",
                        )
                    except Exception as e:
                        logger.debug(f"fetch_one {sym}: {e}")
                        return []

            day_inserted = 0
            for batch_start in range(0, len(symbols), 150):
                batch = symbols[batch_start:batch_start + 150]
                api_calls += len(batch)
                results = await _asyncio.gather(*[_fetch_one(s) for s in batch])
                all_rows = [r for sub in results for r in sub if r]
                if all_rows:
                    n, syms = await _insert_rows(all_rows)
                    day_inserted += n
                    updated_symbols.extend(syms)
                if progress_callback and len(symbols) > 300:
                    await progress_callback("download", day_i, total_days,
                                            extra=f"{trade_date}: {min(batch_start+150, len(symbols))}/{len(symbols)} | 已更新 {day_inserted} 只")
            inserted += day_inserted

        if progress_callback:
            await progress_callback("download", day_i + 1, total_days,
                                    extra=f"已下载 {day_i + 1}/{total_days} 日，共 {inserted} 条")

    if progress_callback:
        await progress_callback("download", total_days, total_days,
                                extra=f"下载完成: {inserted} 条日线 | {api_calls} 次API调用")

    # P2-6: 下载完成后清除受影响的特征缓存
    try:
        from app.services.feature_cache import invalidate_cache_for_symbols
        if updated_symbols:
            await invalidate_cache_for_symbols(updated_symbols)
            logger.info(f"Feature cache invalidated for {len(updated_symbols)} symbols")
    except Exception as e:
        logger.warning(f"Feature cache invalidation failed: {e}")

    return inserted, list(set(updated_symbols))

# 避免与其他 asyncio.sleep 冲突
import asyncio as _asyncio
asyncio_sleep = _asyncio.sleep


def _compute_indicator_worker(args):
    """v7.0.11: ProcessPoolExecutor worker — 计算单只股票 TG 指标.

    必须在 module 顶层 (picklable).
    Args: (ts_code, klines_list_of_dict, board_params_dict)
    Returns: (ts_code, last_row_dict, error_str_or_None)
    """
    ts_code, klines, board_params = args
    try:
        import pandas as pd
        from app.services.tg_indicator import TGIndicator
        df = pd.DataFrame(klines)
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        indicator = TGIndicator(df, tg_signal_params=board_params)
        full_df = indicator.compute()
        # 只返回最后一行 (避免大量数据 marshal)
        last = full_df.iloc[-1].to_dict()
        # 转换 Timestamp 为 str (picklable)
        for k, v in list(last.items()):
            if hasattr(v, "isoformat"):
                last[k] = v.isoformat()
        return ts_code, last, None
    except Exception as e:
        return ts_code, None, str(e)[:200]


# ═══════════════════════════════════════════════════════════════
# 方案 B：周线独立信号叠加 — Phase 1.5
# ═══════════════════════════════════════════════════════════════

def resample_daily_to_weekly(kline_df: pd.DataFrame) -> pd.DataFrame | None:
    """将日线 DataFrame 按自然周重采样为周线.

    方案 B：取周一开盘、周最高、周最低、周五收盘、周总成交量。
    如果周五无交易（节假日），取该周最后一个交易日。

    ⭐ v7.0.31 fix: 兼容大小写列名 (Date/date, Open/open 等).
    asyncpg Record 默认列名是小写, 传入 raw record 时不爆.

    Args:
        kline_df: 含 Date/Open/High/Low/Close/Volume 的日线 DataFrame
                  (列名大小写不敏感)

    Returns:
        周线 DataFrame (索引为周五日期)，或 None（数据不足）
    """
    if len(kline_df) < 30:
        return None

    df = kline_df.copy()

    # 列名规范化 (大小写不敏感)
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if cl in ('date', 'trade_date'): col_map['Date'] = col
        elif cl == 'open': col_map['Open'] = col
        elif cl == 'high': col_map['High'] = col
        elif cl == 'low': col_map['Low'] = col
        elif cl == 'close': col_map['Close'] = col
        elif cl in ('volume', 'vol'): col_map['Volume'] = col
    if col_map:
        df = df.rename(columns={v: k for k, v in col_map.items()})

    df['Date'] = pd.to_datetime(df['Date'])
    df = df.sort_values('Date').reset_index(drop=True)

    # 生成周标签: ISO 年-周号 (周一=1, 周日=7)
    df['week_label'] = df['Date'].dt.strftime('%G-W%V')

    weekly_rows = []
    for wk, group in df.groupby('week_label'):
        if len(group) < 2:  # 至少要有 2 个交易日才能形成一根周线
            continue
        group = group.sort_values('Date')
        weekly_rows.append({
            'Date': group['Date'].iloc[-1],       # 该周最后一个交易日作为周线日期
            'Open': group['Open'].iloc[0],          # 周一开盘 (或该周首日)
            'High': group['High'].max(),
            'Low': group['Low'].min(),
            'Close': group['Close'].iloc[-1],       # 周五收盘 (或该周末日)
            'Volume': group['Volume'].sum(),         # 周总成交量
        })

    if len(weekly_rows) < 20:
        return None

    weekly_df = pd.DataFrame(weekly_rows).sort_values('Date').reset_index(drop=True)
    return weekly_df


async def scan_weekly_signals(
    session: AsyncSession, trade_date: date, symbols: list[str] | None = None
) -> dict[str, dict]:
    """对全市场股票逐股计算周线 TG 信号.

    从 daily_kline 中加载每只股票过去 200 个交易日的日线,
    重采样为周线 → 传入 TGIndicator 计算周线买卖信号。

    返回:
        {symbol: {
            "has_weekly_buy": bool,      # 周线最近一根是否有买入信号
            "weekly_tg_momentum": float, # 周线 TG 动量值
            "weekly_j_value": float,     # 周线 J 值
            "weekly_vol_ratio": float,   # 周线量比
        }}
    """
    logger.info("方案 B Phase 1.5: 开始周线信号扫描...")
    cutoff = trade_date - timedelta(days=400)  # 200 个交易日 ≈ 400 日历日

    # 如果未指定股票列表，加载全市场（上界约束防止回扫时引入未来数据）
    if symbols is None:
        r = await session.execute(text(
            "SELECT DISTINCT ts_code FROM daily_kline WHERE trade_date >= :cut AND trade_date <= :trade_date"
        ), {"cut": cutoff, "trade_date": trade_date})
        symbols = [row[0] for row in r.fetchall()]

    # 批量加载 K 线（上界约束防止回扫时引入未来数据）
    r = await session.execute(text("""
        SELECT ts_code, trade_date, open, high, low, close, volume
        FROM daily_kline WHERE ts_code = ANY(:codes) AND trade_date >= :cutoff AND trade_date <= :trade_date
        ORDER BY ts_code, trade_date
    """), {"codes": symbols, "cutoff": cutoff, "trade_date": trade_date})
    raw_rows = r.fetchall()

    df_dict: dict[str, list] = {}
    for row in raw_rows:
        df_dict.setdefault(row[0], []).append({
            "Date": row[1], "Open": row[2], "High": row[3],
            "Low": row[4], "Close": row[5], "Volume": row[6],
        })

    weekly_signals: dict[str, dict] = {}
    scanned = 0
    skipped_data = 0
    skipped_quality = 0
    buy_count = 0

    for ts_code, krows in df_dict.items():
        if len(krows) < 60:
            scanned += 1
            skipped_data += 1
            if scanned % 500 == 0:
                await asyncio_sleep(0)
            continue

        daily_df = pd.DataFrame(krows).sort_values("Date").reset_index(drop=True)
        weekly_df = resample_daily_to_weekly(daily_df)
        if weekly_df is None or len(weekly_df) < 20:
            scanned += 1
            skipped_data += 1
            if scanned % 500 == 0:
                await asyncio_sleep(0)
            continue

        try:
            indicator = TGIndicator(weekly_df, tg_signal_params=_get_board_params(ts_code))
            full_df = indicator.compute()
        except Exception:
            scanned += 1
            skipped_data += 1
            if scanned % 200 == 0:
                await asyncio_sleep(0)
            continue

        last = full_df.iloc[-1]
        close_price = float(last["Close"])
        volume = float(last["Volume"])

        # ── 质量过滤 ──
        if volume <= 0 or close_price < 2.0:
            scanned += 1
            skipped_quality += 1
            if scanned % 500 == 0:
                await asyncio_sleep(0)
            continue

        has_buy = bool(last["买方向"])
        tg_momentum = float(last["TG动量"])
        j_val = float(last["J"])
        vol_ratio = float(last["量比"])

        weekly_signals[ts_code] = {
            "has_weekly_buy": has_buy,
            "weekly_tg_momentum": round(tg_momentum, 2),
            "weekly_j_value": round(j_val, 2),
            "weekly_vol_ratio": round(vol_ratio, 2),
        }

        if has_buy:
            buy_count += 1

        scanned += 1
        if scanned % 500 == 0:
            logger.info(
                f"  周线扫描进度: {scanned}/{len(symbols)} "
                f"| 周线信号: {len(weekly_signals)} (买入:{buy_count}) "
                f"| 跳过(数据/质量): {skipped_data}/{skipped_quality}"
            )
            await asyncio_sleep(0)

    logger.info(
        f"方案 B Phase 1.5 完成: 扫描 {scanned} 只股票, "
        f"{len(weekly_signals)} 只有效周线 (其中 {buy_count} 只周线买入信号), "
        f"跳过: 数据不足 {skipped_data}, 质量过滤 {skipped_quality}"
    )
    return weekly_signals


# ── 阶段二：本地 TG 扫描 ──────────────────────────

async def save_scan_results(session: AsyncSession, results: list[dict], scan_date: date):
    if not results:
        return 0
    rows = [{"scan_date": scan_date, "symbol": r.get("symbol", ""), "name": r.get("name", ""),
        "level": r.get("level", ""), "tg_momentum": r.get("tg_momentum", 0),
        "dist_low": r.get("dist_low", 0), "j_value": r.get("j_value", 0),
        "vol_ratio": r.get("vol_ratio", 0), "buy_strength": r.get("buy_strength", 0),
        "close_price": r.get("close_price", 0), "composite_score": r.get("composite_score", 0),
        "trigger_path": r.get("trigger_path", ""), "industry": r.get("industry", ""),
        "market": r.get("market", "主板"),
        # 方案 B：周线独立信号叠加
        "resonance_type": r.get("resonance_type"), "weekly_has_buy": r.get("weekly_has_buy"),
        "weekly_tg_momentum": r.get("weekly_tg_momentum")}
        for r in results]
    try:
        await session.execute(text("""
            INSERT INTO scan_results (scan_date, scan_time, symbol, name, level, tg_momentum,
                dist_low, j_value, vol_ratio, buy_strength, close_price, composite_score,
                trigger_path, industry, market, resonance_type, weekly_has_buy, weekly_tg_momentum)
            VALUES (:scan_date, CURRENT_TIME, :symbol, :name, :level, :tg_momentum,
                :dist_low, :j_value, :vol_ratio, :buy_strength, :close_price, :composite_score,
                :trigger_path, :industry, :market, :resonance_type, :weekly_has_buy, :weekly_tg_momentum)
            ON CONFLICT (scan_date, symbol) DO UPDATE SET
                name=EXCLUDED.name, level=EXCLUDED.level, tg_momentum=EXCLUDED.tg_momentum,
                dist_low=EXCLUDED.dist_low, j_value=EXCLUDED.j_value, vol_ratio=EXCLUDED.vol_ratio,
                buy_strength=EXCLUDED.buy_strength, close_price=EXCLUDED.close_price,
                composite_score=EXCLUDED.composite_score, scan_time=CURRENT_TIME, market=EXCLUDED.market,
                resonance_type=EXCLUDED.resonance_type, weekly_has_buy=EXCLUDED.weekly_has_buy,
                weekly_tg_momentum=EXCLUDED.weekly_tg_momentum
        """), rows)
        await session.commit()
        return len(rows)
    except Exception as e:
        logger.error(f"save_scan_results failed: {e}", exc_info=True)
        await session.rollback()
        return 0


def calculate_composite_score(tg_momentum, dist_low, j_val, vol_ratio, buy_strength):
    return round(min(100, max(0, 25 + tg_momentum * 1.5 + (15 - dist_low) * 1.5 + (j_val - 50) * 0.3 + vol_ratio * 5 + buy_strength * 30)), 1)


async def scan_all_stocks(session: AsyncSession, min_level: int = 1, progress_callback=None,
                          skip_download: bool = False, scan_date_override: str | None = None) -> tuple[pd.DataFrame, date]:
    """全市场 TG 扫描(两阶段).

    阶段一 (download): 从 Tushare 下载最新日线数据
    阶段二 (scan):    本地 TG 指标计算和信号筛选

    Args:
        progress_callback: async fn(phase, current, total, extra=None)
                           phase ∈ {"download", "scan", "done", "error"}
    """
    # ── 阶段一：下载新数据 ────────────────────
    if not skip_download:
        if progress_callback:
            await progress_callback("download", 0, 1, extra="正在连接 Tushare 下载最新日线...")
        try:
            new_rows, updated_symbols = await download_latest_kline(progress_callback=progress_callback)
            logger.info(f"Download phase complete: {new_rows} rows, {len(updated_symbols)} symbols")
            if progress_callback:
                await progress_callback("download", 1, 1,
                    extra=f"下载完成: 更新 {new_rows} 条日线数据 ({len(updated_symbols)} 只股票)")
        except Exception as e:
            logger.error(f"Download phase failed: {e}", exc_info=True)
            if progress_callback:
                await progress_callback("download", 1, 1,
                    extra=f"⚠ 下载失败: {str(e)[:100]}，使用现有数据继续扫描")
    else:
        if progress_callback:
            await progress_callback("download", 0, 0, extra="已跳过下载(使用现有数据)")

    # ── 阶段二：本地扫描 ────────────────────
    if progress_callback:
        await progress_callback("scan", 0, 1, extra="正在加载股票列表...")

    stock_list = await get_stock_list()
    total = len(stock_list)
    ref_date = date.fromisoformat(scan_date_override) if scan_date_override else datetime.now().date()
    cutoff = (ref_date - timedelta(days=180))
    all_codes = stock_list["ts_code"].tolist()

    # ── v4.6: 屏蔽 ST + 当日涨停 ──
    st_excluded = 0
    limit_excluded = 0
    low_price_excluded = 0
    exclusion_excluded = 0
    try:
        # 0. v7.0.34: exclusion_list 屏蔽 (PE_LOSS / TECH_BOARD / BJ_BOARD / INSOLVENT)
        try:
            ex_r = await session.execute(text("""
                SELECT symbol FROM exclusion_list
                WHERE expires_at IS NULL OR expires_at > NOW()
            """))
            ex_codes = {row[0] for row in ex_r.fetchall()}
            if ex_codes:
                exclusion_excluded = len(ex_codes & set(all_codes))
                all_codes = [c for c in all_codes if c not in ex_codes]
                logger.info(f"Excluded {exclusion_excluded} stocks from exclusion_list")
        except Exception as e:
            logger.warning(f"exclusion_list filter failed (table may not exist): {e}")

        # 0.5 v7.0.34: 低价股过滤 (close < 5 元, 一律屏蔽)
        try:
            lp_r = await session.execute(text("""
                SELECT DISTINCT dk.ts_code FROM daily_kline dk
                WHERE dk.ts_code = ANY(:codes) AND dk.trade_date = :d
                  AND dk.close < 5.0
            """), {"codes": all_codes, "d": ref_date})
            lp_codes = {row[0] for row in lp_r.fetchall()}
            if lp_codes:
                low_price_excluded = len(lp_codes)
                all_codes = [c for c in all_codes if c not in lp_codes]
                logger.info(f"Excluded {low_price_excluded} low-price stocks (<3元)")
        except Exception as e:
            logger.warning(f"low-price filter failed: {e}")

        # 1. ST 股票过滤 (v4.8: 名称含 ST/*ST/ST退 — 修正正则, 不区分大小写)
        #    修复: 旧 `[*]?ST` 不匹配中文 "ST" 开头 (如 "ST实达")
        st_r = await session.execute(text("""
            SELECT DISTINCT symbol FROM scan_results
            WHERE (name ~* '[* ]?ST' OR name LIKE '%ST%' OR name LIKE '%退%')
              AND symbol = ANY(:codes)
        """), {"codes": all_codes})
        st_codes = {row[0] for row in st_r.fetchall()}
        if st_codes:
            st_excluded = len(st_codes)
            all_codes = [c for c in all_codes if c not in st_codes]
            logger.info(f"Excluded {st_excluded} ST stocks")

        # 2. 涨停过滤 — 用前日收盘价检测（含一字板）
        # 旧方案 (close-open)/open≥9.8% 漏掉一字板（open==close==涨停价→比值≈0）
        lu_r = await session.execute(text("""
            SELECT dk.ts_code FROM daily_kline dk
            JOIN LATERAL (
                SELECT close FROM daily_kline dk2
                WHERE dk2.ts_code = dk.ts_code AND dk2.trade_date < dk.trade_date
                ORDER BY dk2.trade_date DESC LIMIT 1
            ) prev ON true
            WHERE dk.ts_code = ANY(:codes) AND dk.trade_date = :d
              AND dk.close > 0 AND prev.close > 0
              AND (
                ((dk.ts_code LIKE '6%' OR dk.ts_code LIKE '00%') AND dk.close / prev.close - 1 >= 0.095)
                OR ((dk.ts_code LIKE '30%' OR dk.ts_code LIKE '688%') AND dk.close / prev.close - 1 >= 0.195)
                OR ((dk.ts_code LIKE '8%' OR dk.ts_code LIKE '4%') AND dk.close / prev.close - 1 >= 0.295)
              )
        """), {"codes": all_codes, "d": ref_date})
        lu_codes = {row[0] for row in lu_r.fetchall()}
        if lu_codes:
            limit_excluded = len(lu_codes)
            all_codes = [c for c in all_codes if c not in lu_codes]
            logger.info(f"Excluded {limit_excluded} limit-up stocks on {ref_date}")
    except Exception as e:
        logger.warning(f"ST/limit-up filter failed, continuing: {e}")
    total = len(all_codes)

    # 预加载行业映射(从 ths_member 取第一个概念)
    industry_map: dict[str, str] = {}
    try:
        ind_r = await session.execute(text(
            "SELECT DISTINCT ON (ts_code) ts_code, ths_name FROM ths_member WHERE out_date IS NULL"
        ))
        for row in ind_r.fetchall():
            industry_map[row[0]] = row[1] or ""
    except Exception:
        pass  # ths_member 表可能不存在

    # 批量加载 K 线数据（上界约束防止回扫时引入未来数据）
    result = await session.execute(text("""
        SELECT ts_code, trade_date, open, high, low, close, volume
        FROM daily_kline WHERE ts_code = ANY(:codes) AND trade_date >= :cutoff AND trade_date <= :ref_date
        ORDER BY ts_code, trade_date
    """), {"codes": all_codes, "cutoff": cutoff, "ref_date": ref_date})
    rows = result.fetchall()

    if progress_callback:
        filter_msg = f"已加载 {len(rows)} 条K线，开始逐股分析..."
        if st_excluded or limit_excluded or exclusion_excluded or low_price_excluded:
            filters = []
            if exclusion_excluded: filters.append(f"踢出名单:{exclusion_excluded}")
            if low_price_excluded: filters.append(f"低价股:{low_price_excluded}")
            if st_excluded: filters.append(f"屏蔽ST:{st_excluded}")
            if limit_excluded: filters.append(f"屏蔽涨停:{limit_excluded}")
            filter_msg += f" (已过滤: {', '.join(filters)})"
        await progress_callback("scan", 0, total, extra=filter_msg)

    df_dict: dict = {}
    for r in rows:
        df_dict.setdefault(r[0], []).append({
            "Date": r[1], "Open": r[2], "High": r[3], "Low": r[4],
            "Close": r[5], "Volume": r[6],
        })

    # 初始化股票名称缓存
    try:
        from app.services.stock_name_cache import _ensure_cache, get_stock_name
        await _ensure_cache()
    except Exception:
        get_stock_name = lambda x: x  # noqa

    # ── v7.0.11: ProcessPoolExecutor 并行化 TGIndicator.compute ──
    # 32 核 CPU, 5000 只股票只用了 1 核 = 4-5 分钟串行
    # TGIndicator.compute() 是纯 CPU 密集 (603 字符纯 pandas/numpy, 无 await)
    # 改成 8 worker 并行, 预计 30-60 秒完成 (8 倍加速)
    results = []
    scanned = 0
    skipped_kline = 0
    skipped_error = 0
    skipped_no_signal = 0

    # 预过滤数据不足的票 (< 60 条 K 线)
    valid_codes = [(ts, krows) for ts, krows in df_dict.items() if len(krows) >= 60]
    skipped_kline = len(df_dict) - len(valid_codes)
    if skipped_kline and progress_callback:
        await progress_callback("scan", skipped_kline, total,
                                extra=f"跳过数据不足: {skipped_kline} (共 {total} 只)")

    # 把需要并行计算的输入打包 (避免大字典重复 marshal)
    # 序列化 K 线数据为 list of dicts, 子进程反序列化
    tasks = []
    for ts_code, krows in valid_codes:
        klines_serializable = [
            {"Date": str(r["Date"]), "Open": float(r["Open"]), "High": float(r["High"]),
             "Low": float(r["Low"]), "Close": float(r["Close"]), "Volume": float(r["Volume"])}
            for r in krows
        ]
        tasks.append((ts_code, klines_serializable, _get_board_params(ts_code)))

    # ProcessPoolExecutor 并行执行 TGIndicator.compute
    # 用 os.cpu_count() 动态决定 worker 数 (32 核 → 16 worker, 留一半给 DB)
    max_workers = max(4, min(16, (os.cpu_count() or 8) - 4))
    logger.info(f"v7.0.11 ProcessPoolExecutor: {max_workers} workers, {len(tasks)} tasks")

    loop = asyncio.get_event_loop()
    with cf.ProcessPoolExecutor(max_workers=max_workers) as pool:
        # 提交所有任务
        futures = [loop.run_in_executor(pool, _compute_indicator_worker, t) for t in tasks]

        # 收集结果 (带超时, 防止单只股票卡死)
        completed = 0
        for fut in asyncio.as_completed(futures, timeout=600):
            try:
                ts_code, full_df_dict, err = await fut
            except Exception as e:
                logger.warning(f"ProcessPool task failed: {e}")
                continue
            completed += 1
            scanned += 1

            if err or full_df_dict is None:
                skipped_error += 1
                if skipped_error <= 5:
                    logger.warning(f"TGIndicator failed for {ts_code}: {err}")
                if scanned % max(1, total // 20) == 0 and progress_callback:
                    await progress_callback("scan", scanned, total,
                                            extra=f"分析中 {scanned}/{total} | 信号:{len(results)} | 跳过(异常):{skipped_error}")
                continue

            # full_df_dict 是 list of dicts (最后一行的指标)
            last = full_df_dict

            # 解析 last (TGIndicator.compute 最后一行的 Series, 转 dict)
            buy_dir = last.get("买方向")
            tier_raw = last.get("层级买终", 1)
            try:
                tier = int(tier_raw) if tier_raw is not None else 1
            except (TypeError, ValueError):
                tier = 1

            if not buy_dir or tier < min_level:
                skipped_no_signal += 1
                if scanned % max(1, total // 20) == 0 and progress_callback:
                    await progress_callback("scan", scanned, total,
                                            extra=f"分析中 {scanned}/{total} | 信号:{len(results)} | 跳过(无信号):{skipped_no_signal}")
                # L1 写为 L1, deep_analyze 侧会自动滤除但影子训练可用
                # v7.0.34: L1 兜底也用 get_stock_name 拿中文名 (避免污染 scan_results.name)
                fallback_name = ts_code
                if 'get_stock_name' in dir():
                    try: fallback_name = get_stock_name(ts_code) or ts_code
                    except Exception: pass
                results.append({
                    "symbol": ts_code, "name": fallback_name, "level": "L1",
                    "tg_momentum": 0.0, "dist_low": 0.0, "j_value": 0.0,
                    "vol_ratio": 0.0, "trigger_path": "no_signal",
                    "market": "", "industry": industry_map.get(ts_code, ""),
                })
                continue

            name = get_stock_name(ts_code) if 'get_stock_name' in dir() else ts_code

            trigger = (
                "大买刚" if last.get("大买刚", False)
                else "企稳加分" if last.get("企稳加分", False)
                else "突破升级" if last.get("突破升级有效", False)
                else "标准维度"
            )
            # 安全解析浮点字段
            def _f(v, default=0.0):
                try: return float(v) if v is not None else default
                except: return default

            results.append({
                "symbol": ts_code, "name": name, "level": f"L{tier}",
                "tg_momentum": round(_f(last.get("TG动量")), 2),
                "dist_low": round(_f(last.get("距低点")), 2),
                "j_value": round(_f(last.get("J")), 2),
                "vol_ratio": round(_f(last.get("量比")), 2),
                "buy_strength": round(_f(last.get("买入强度")), 4),
                "close_price": round(_f(last.get("Close")), 2),
                "composite_score": calculate_composite_score(
                    _f(last.get("TG动量")), _f(last.get("距低点")),
                    _f(last.get("J")), _f(last.get("量比")),
                    _f(last.get("买入强度")),
                ),
                "trigger_path": trigger, "industry": industry_map.get(ts_code, ""),
                "market": _get_market(ts_code),
            })

            if scanned % 200 == 0 and progress_callback:
                await progress_callback("scan", scanned, total,
                                        extra=f"分析中 {scanned}/{total} | 信号:{len(results)} (L3:{sum(1 for r in results if r['level']=='L3')})")


    # ═══════════════════════════════════════════════════════
    # 方案 B Phase 1.5: 周线独立信号叠加
    # 在日线扫描完成后，对全市场计算周线 TG 信号并做双周期匹配
    # ═══════════════════════════════════════════════════════
    if progress_callback:
        await progress_callback("scan", total, total,
                                extra="Phase 1.5: 周线信号扫描中...")

    weekly_signals = await scan_weekly_signals(session, ref_date)

    # 将周线信号与日线结果做匹配，生成 resonance_type
    resonance_both = 0
    resonance_daily = 0
    resonance_weekly = 0
    for r in results:
        sym = r["symbol"]
        ws = weekly_signals.get(sym)
        if ws is None:
            # 该股票无有效周线数据 → 纯日线驱动
            r["resonance_type"] = "daily_only"
            r["weekly_has_buy"] = False
            r["weekly_tg_momentum"] = 0.0
            resonance_daily += 1
        else:
            r["weekly_has_buy"] = ws["has_weekly_buy"]
            r["weekly_tg_momentum"] = ws["weekly_tg_momentum"]
            daily_buy = True  # 日线结果中的每一条都已经被日线 TG 筛选过（买方向=True）
            weekly_buy = ws["has_weekly_buy"]
            if daily_buy and weekly_buy:
                r["resonance_type"] = "weekly_resonance"
                resonance_both += 1
            elif daily_buy and not weekly_buy:
                r["resonance_type"] = "daily_only"
                resonance_daily += 1
            else:
                r["resonance_type"] = "weekly_driven"
                resonance_weekly += 1

    logger.info(
        f"方案 B 双周期匹配完成: "
        f"共振={resonance_both}, 仅日线={resonance_daily}, "
        f"周线驱动={resonance_weekly} "
        f"(总计 {len(results)} 日线信号 / {len(weekly_signals)} 周线有效)"
    )
    # ═══════════════════════════════════════════════════════

    # 完成 — scan_date 取 daily_kline 最新日期(真实数据日期)
    if scan_date_override:
        scan_date = date.fromisoformat(scan_date_override)
        logger.info(f"Using override scan_date: {scan_date}")
    else:
        latest_kline = await get_latest_kline_date()
        scan_date = latest_kline if latest_kline else date.today()
        if not latest_kline:
            logger.warning("daily_kline 为空，使用今天日期作为 scan_date")
        elif abs((date.today() - scan_date).days) >= 2:
            logger.warning(f"K线数据滞后 {(date.today() - scan_date).days} 天 (最新: {scan_date})，扫描结果可能非最新")

    if progress_callback:
        l5_count = sum(1 for r in results if r['level'] == 'L5')
        l4_count = sum(1 for r in results if r['level'] == 'L4')
        l3_count = sum(1 for r in results if r['level'] == 'L3')
        l2_count = sum(1 for r in results if r['level'] == 'L2')
        await progress_callback("scan", total, total,
                                extra=f"扫描完成: {len(results)} 信号 (L5:{l5_count} L4:{l4_count} L3:{l3_count} L2:{l2_count}) | 过滤: 数据不足{skipped_kline} 异常{skipped_error} 无信号{skipped_no_signal}")

    if results:
        await save_scan_results(session, results, scan_date)

    return pd.DataFrame(results), scan_date
