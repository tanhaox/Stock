"""AlphaFlow Step 1: SQL 窗口函数全量标注主升浪棋谱.

纯 SQL 实现, 400万行在 30 秒内完成标注.
标注后存入 trend_samples 表供后续训练.
"""
import asyncio, sys, logging
from sqlalchemy import text
from app.core.database import async_session_factory

logger = logging.getLogger("alphaflow.label")


async def create_table():
    async with async_session_factory() as s:
        await s.execute(text("""
            CREATE TABLE IF NOT EXISTS trend_samples (
                id SERIAL PRIMARY KEY,
                ts_code VARCHAR(20) NOT NULL,
                sample_date DATE NOT NULL,
                lookback_days INT DEFAULT 60,
                forward_days INT DEFAULT 60,
                forward_peak_pct DECIMAL(8,2),
                forward_max_drawdown DECIMAL(8,2),
                forward_end_pct DECIMAL(8,2),
                label VARCHAR(20),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(ts_code, sample_date)
            )
        """))
        await s.execute(text("CREATE INDEX IF NOT EXISTS idx_trend_label ON trend_samples(label)"))
        await s.execute(text("CREATE INDEX IF NOT EXISTS idx_trend_code ON trend_samples(ts_code)"))
        await s.commit()


async def label_via_sql():
    """SQL 窗口函数标注: 每只股票每个交易日 → 未来60天最大涨幅."""
    async with async_session_factory() as s:
        # 清空旧数据
        await s.execute(text("DELETE FROM trend_samples"))
        await s.commit()

        logger.info("Running SQL labeling (this may take 30-60s)...")

        # Step A: 标注所有主升浪 (≥80%) 和强势 (≥30%)
        await s.execute(text("""
            INSERT INTO trend_samples
            (ts_code, sample_date, forward_peak_pct, forward_max_drawdown, forward_end_pct, label)
            WITH ranked AS (
                SELECT
                    ts_code,
                    trade_date,
                    close,
                    high,
                    low,
                    -- 未来 60 天最大涨幅
                    (MAX(high) OVER (
                        PARTITION BY ts_code
                        ORDER BY trade_date
                        ROWS BETWEEN 1 FOLLOWING AND 60 FOLLOWING
                    ) - close) / NULLIF(close, 0) * 100 AS peak_pct,
                    -- 未来 60 天最大回撤 (从 entry close 算)
                    (MIN(low) OVER (
                        PARTITION BY ts_code
                        ORDER BY trade_date
                        ROWS BETWEEN 1 FOLLOWING AND 60 FOLLOWING
                    ) - close) / NULLIF(close, 0) * 100 AS max_dd_pct,
                    -- 未来 60 天终点收益
                    (LAST_VALUE(close) OVER (
                        PARTITION BY ts_code
                        ORDER BY trade_date
                        ROWS BETWEEN 1 FOLLOWING AND 60 FOLLOWING
                    ) - close) / NULLIF(close, 0) * 100 AS end_pct,
                    -- 确保至少有60天回看数据
                    ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date) AS day_num
                FROM daily_kline
            )
            SELECT
                ts_code,
                trade_date,
                60, 60,
                ROUND(peak_pct::numeric, 2),
                ROUND(max_dd_pct::numeric, 2),
                ROUND(end_pct::numeric, 2),
                CASE
                    WHEN peak_pct >= 80 THEN 'major_rally'
                    WHEN peak_pct >= 30 THEN 'strong'
                    WHEN peak_pct <= -15 THEN 'fail'
                    ELSE 'normal'
                END
            FROM ranked
            WHERE day_num > 60                          -- 有60天回看
              AND peak_pct IS NOT NULL                  -- 有未来数据
              AND (
                  peak_pct >= 30 OR peak_pct <= -15     -- 正样本全取: 大涨+大跌
                  OR RANDOM() < 0.02                    -- 负样本: 随机采样 2%
              )
            ON CONFLICT (ts_code, sample_date) DO NOTHING
        """))
        await s.commit()
        logger.info("SQL labeling inserted, checking counts...")

        # 统计
        r = await s.execute(text("""
            SELECT label, COUNT(*),
                   ROUND(AVG(forward_peak_pct)::numeric, 1) as avg_peak,
                   ROUND(AVG(forward_end_pct)::numeric, 1) as avg_end
            FROM trend_samples
            GROUP BY label ORDER BY COUNT(*) DESC
        """))
        stats = {}
        for row in r.fetchall():
            stats[row[0]] = {"count": row[1], "avg_peak": float(row[2]), "avg_end": float(row[3])}
            logger.info(f"  {row[0]}: {row[1]:,} samples, avg_peak={float(row[2]):.1f}%, avg_end={float(row[3]):.1f}%")

        return stats


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    await create_table()
    stats = await label_via_sql()
    total = sum(v["count"] for v in stats.values())
    print(f"\n=== AlphaFlow 棋谱标注完成 ===")
    print(f"总样本: {total:,}")
    print(f"主升浪 (major_rally): {stats.get('major_rally',{}).get('count',0):,}")
    print(f"强势   (strong):      {stats.get('strong',{}).get('count',0):,}")
    print(f"失败   (fail):        {stats.get('fail',{}).get('count',0):,}")
    print(f"普通   (normal):      {stats.get('normal',{}).get('count',0):,}")

if __name__ == "__main__":
    asyncio.run(main())
