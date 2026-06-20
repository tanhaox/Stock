"""Phase 0 数据库迁移 — 自学习升级前置表结构."""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.core.database import async_session_factory, engine
from sqlalchemy import text

MIGRATIONS = [
    # 1. 申万行业指数表
    """
    CREATE TABLE IF NOT EXISTS sw_sector_index (
        index_code   varchar NOT NULL,
        trade_date   date NOT NULL,
        close        double precision,
        pct_chg      double precision,
        PRIMARY KEY (index_code, trade_date)
    )
    """,
    # 2. 扩展 market_status_log
    """
    ALTER TABLE market_status_log ADD COLUMN IF NOT EXISTS phase varchar
    """,
    """
    ALTER TABLE market_status_log ADD COLUMN IF NOT EXISTS ma60_value double precision
    """,
    """
    ALTER TABLE market_status_log ADD COLUMN IF NOT EXISTS phase_duration int
    """,
    # 3. 龙虎榜同步日志
    """
    CREATE TABLE IF NOT EXISTS top_list_sync_log (
        id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        trade_date   date NOT NULL UNIQUE,
        stock_count  int,
        synced_at    timestamptz DEFAULT NOW(),
        status       varchar DEFAULT 'success'
    )
    """,
    # 4. AI 分析结果存储
    """
    CREATE TABLE IF NOT EXISTS ai_insights (
        id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        insight_type  varchar NOT NULL,
        trade_date    date NOT NULL,
        prompt_hash   varchar,
        result_json   jsonb NOT NULL,
        status        varchar DEFAULT 'success',
        tokens_used   int,
        created_at    timestamptz DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ai_type_date ON ai_insights(insight_type, trade_date)
    """,
]

async def run():
    for i, sql in enumerate(MIGRATIONS):
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            print(f"  [{i+1}/{len(MIGRATIONS)}] OK: {sql.strip()[:60]}...")
        except Exception as e:
            print(f"  [{i+1}/{len(MIGRATIONS)}] SKIP: {e}")

if __name__ == "__main__":
    asyncio.run(run())
