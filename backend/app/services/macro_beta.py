"""个股商品 Beta 计算 (M-3) — 对 500 只股票做 OLS 回归, 衡量商品价格敏感度.

stock_daily_ret ~ commodity_daily_ret, 窗口 600 交易日.
结果写入 stock_commodity_beta 表.
"""
import asyncio
import logging
import numpy as np
from datetime import date, timedelta
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("macro_beta")

COMMODITY_NAMES = [
    "crude_oil", "copper", "aluminum", "rebar", "iron_ore", "coke_coal",
    "lithium", "silicon", "gold", "natural_rubber", "methanol", "pvc",
]


async def ensure_beta_table():
    """建 stock_commodity_beta 表."""
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_commodity_beta (
                symbol VARCHAR(20) NOT NULL,
                commodity VARCHAR(30) NOT NULL,
                beta DOUBLE PRECISION,
                r_squared DOUBLE PRECISION,
                n_samples INTEGER,
                last_updated DATE DEFAULT CURRENT_DATE,
                PRIMARY KEY (symbol, commodity)
            )
        """))
        await s.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_scb_symbol ON stock_commodity_beta(symbol)"
        ))
        await s.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_scb_commodity ON stock_commodity_beta(commodity)"
        ))
        await s.commit()


async def compute_stock_commodity_betas(symbol: str, session=None) -> dict[str, dict]:
    """对 12 个商品分别做 OLS: stock_ret ~ commodity_ret, 600 交易日窗口."""
    own_s = session is None
    if own_s:
        session = async_session_factory()
        s_ctx = await session.__aenter__()
    else:
        s_ctx = session

    try:
        # 1. 加载股票日线
        r = await s_ctx.execute(text(
            "SELECT trade_date, close FROM daily_kline "
            "WHERE ts_code = :sym AND trade_date >= :cut ORDER BY trade_date"
        ), {"sym": symbol, "cut": date.today() - timedelta(days=900)})
        stock_rows = r.fetchall()
        if len(stock_rows) < 20:
            return {}

        stock_dates = [row[0] for row in stock_rows]
        stock_closes = np.array([float(row[1]) for row in stock_rows])
        stock_rets = np.diff(stock_closes) / stock_closes[:-1] * 100  # %

        # date→ret index mapping
        stock_ret_map = {stock_dates[i + 1]: stock_rets[i] for i in range(len(stock_rets))}

        # 2. 加载所有商品数据
        r = await s_ctx.execute(text(
            "SELECT indicator, period, value FROM macro_cache "
            "WHERE indicator LIKE 'commodity:%' AND period BETWEEN :lo AND :hi "
            "ORDER BY period"
        ), {"lo": stock_dates[0], "hi": date.today()})
        commodity_data: dict[str, dict[date, float]] = {}
        for row in r.fetchall():
            ind = row[0].replace("commodity:", "")
            if ind not in COMMODITY_NAMES:
                continue
            commodity_data.setdefault(ind, {})[row[1]] = float(row[2])

        # 3. 对每个商品做 OLS
        results = {}
        for name in COMMODITY_NAMES:
            prices = commodity_data.get(name, {})
            if len(prices) < 20:
                continue

            comm_dates = sorted(prices.keys())
            comm_closes = np.array([prices[d] for d in comm_dates])
            comm_rets = np.diff(comm_closes) / comm_closes[:-1] * 100
            comm_ret_map = {comm_dates[i + 1]: comm_rets[i] for i in range(len(comm_rets))}

            # 对齐日期
            common_dates = sorted(set(stock_ret_map.keys()) & set(comm_ret_map.keys()))
            if len(common_dates) < 20:
                continue

            x = np.array([comm_ret_map[d] for d in common_dates])
            y = np.array([stock_ret_map[d] for d in common_dates])

            # OLS: y = βx + α
            A = np.vstack([x, np.ones(len(x))]).T
            try:
                beta, alpha = np.linalg.lstsq(A, y, rcond=None)[0]
            except np.linalg.LinAlgError:
                continue

            y_pred = beta * x + alpha
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r_sq = 1 - ss_res / max(ss_tot, 1e-10)

            results[name] = {
                "beta": round(float(beta), 4),
                "r_squared": round(float(r_sq), 4),
                "n_samples": len(common_dates),
            }

        # 4. 写入 stock_commodity_beta
        for name, vals in results.items():
            await s_ctx.execute(text("""
                INSERT INTO stock_commodity_beta (symbol, commodity, beta, r_squared, n_samples, last_updated)
                VALUES (:sym, :com, :b, :rsq, :n, CURRENT_DATE)
                ON CONFLICT (symbol, commodity) DO UPDATE SET
                    beta=EXCLUDED.beta, r_squared=EXCLUDED.r_squared,
                    n_samples=EXCLUDED.n_samples, last_updated=CURRENT_DATE
            """), {"sym": symbol, "com": name, "b": vals["beta"],
                   "rsq": vals["r_squared"], "n": vals["n_samples"]})
        if own_s:
            await s_ctx.commit()

        return results
    finally:
        if own_s:
            await session.__aexit__(None, None, None)


async def get_sector_median_betas(sector: str) -> dict[str, float]:
    """查 stock_commodity_beta 中该板块所有股票的 beta 中位数."""
    async with async_session_factory() as s:
        r = await s.execute(text("""
            SELECT commodity, PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY beta)
            FROM stock_commodity_beta
            WHERE symbol IN (
                SELECT ts_code FROM ths_member WHERE industry = :sec AND out_date IS NULL
            )
            GROUP BY commodity
        """), {"sec": sector})
        return {row[0]: float(row[1]) if row[1] else 0.0 for row in r.fetchall()}


async def get_stock_excess_sensitivity(symbol: str, commodity: str) -> float:
    """个股 β 偏离板块中位数的程度."""
    async with async_session_factory() as s:
        r = await s.execute(text(
            "SELECT beta FROM stock_commodity_beta WHERE symbol = :sym AND commodity = :com"
        ), {"sym": symbol, "com": commodity})
        row = r.fetchone()
        if not row or row[0] is None:
            return 0.0
        stock_beta = float(row[0])

        # 取板块
        r2 = await s.execute(text(
            "SELECT industry FROM ths_member WHERE ts_code = :sym AND out_date IS NULL"
        ), {"sym": symbol})
        sec_row = r2.fetchone()
        sector = sec_row[0] if sec_row else ""
        median = await get_sector_median_betas(sector)
        return stock_beta - median.get(commodity, 0.0)


async def batch_compute_all_betas(limit: int = 500) -> dict:
    """批量计算 500 只股票的 β (按日均成交额筛选)."""
    await ensure_beta_table()

    async with async_session_factory() as s:
        # 筛选成交活跃的 N 只 (排除指数代码 000xxx.SH/399xxx.SZ 等)
        r = await s.execute(text("""
            SELECT ts_code FROM daily_kline
            WHERE trade_date >= CURRENT_DATE - 30
              AND ts_code ~ '^[0-9]{6}\\.(SZ|SH|BJ)$'
            GROUP BY ts_code
            ORDER BY AVG(amount) DESC LIMIT :lim
        """), {"lim": limit})
        symbols = [row[0] for row in r.fetchall()]

    logger.info(f"Computing betas for {len(symbols)} stocks...")
    results = {"total": len(symbols), "computed": 0, "failed": 0}

    for i, sym in enumerate(symbols):
        try:
            betas = await compute_stock_commodity_betas(sym)
            if betas:
                results["computed"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            logger.debug(f"Beta {sym} failed: {e}")
            results["failed"] += 1

        if (i + 1) % 100 == 0:
            logger.info(f"  beta progress: {i + 1}/{len(symbols)}")

    logger.info(f"Beta batch done: {results['computed']} computed, {results['failed']} failed")
    return results
