# -*- coding: utf-8 -*-
"""v7.0.32 步骤 1: 跑 ALTER TABLE 加字段 + 创建 backup + 索引."""
import asyncio
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import asyncpg
from app.core.config import settings

DSN = settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://')


async def main():
    conn = await asyncpg.connect(DSN)
    try:
        print('=' * 80)
        print('v7.0.32 数据库迁移 — Step 1: 加字段 + 备份 + 索引')
        print('=' * 80)

        # 1. 备份 (DROP IF EXISTS 防止重跑报错)
        print('\n[1] 备份当前 analysis_scores → analysis_scores_backup_v7031...')
        await conn.execute('DROP TABLE IF EXISTS analysis_scores_backup_v7031')
        await conn.execute('CREATE TABLE analysis_scores_backup_v7031 AS SELECT * FROM analysis_scores')
        cnt = await conn.fetchval('SELECT COUNT(*) FROM analysis_scores_backup_v7031')
        total = await conn.fetchval('SELECT COUNT(*) FROM analysis_scores')
        print(f'  backup 行数: {cnt}, 现表行数: {total}, 一致: {cnt == total}')

        # 2. 加字段
        print('\n[2] 加 22 个新字段...')
        await conn.execute('''
            ALTER TABLE analysis_scores
                -- MACD
                ADD COLUMN IF NOT EXISTS macd_dif double precision,
                ADD COLUMN IF NOT EXISTS macd_dea double precision,
                ADD COLUMN IF NOT EXISTS macd_bar double precision,
                -- KDJ
                ADD COLUMN IF NOT EXISTS kdj_k double precision,
                ADD COLUMN IF NOT EXISTS kdj_d double precision,
                ADD COLUMN IF NOT EXISTS kdj_j double precision,
                -- RSI 多周期
                ADD COLUMN IF NOT EXISTS rsi_6 double precision,
                ADD COLUMN IF NOT EXISTS rsi_12 double precision,
                ADD COLUMN IF NOT EXISTS rsi_24 double precision,
                -- BOLL
                ADD COLUMN IF NOT EXISTS boll_upper double precision,
                ADD COLUMN IF NOT EXISTS boll_mid double precision,
                ADD COLUMN IF NOT EXISTS boll_lower double precision,
                ADD COLUMN IF NOT EXISTS boll_width double precision,
                ADD COLUMN IF NOT EXISTS boll_pos double precision,
                -- CCI
                ADD COLUMN IF NOT EXISTS cci double precision,
                -- 筹码
                ADD COLUMN IF NOT EXISTS cost_5pct double precision,
                ADD COLUMN IF NOT EXISTS cost_50pct double precision,
                ADD COLUMN IF NOT EXISTS cost_95pct double precision,
                ADD COLUMN IF NOT EXISTS weight_avg double precision,
                ADD COLUMN IF NOT EXISTS winner_rate double precision,
                -- 衍生
                ADD COLUMN IF NOT EXISTS cost_spread double precision,
                ADD COLUMN IF NOT EXISTS price_vs_cost double precision
        ''')
        print('  ✅ 22 字段已加')

        # 3. 索引
        print('\n[3] 加 4 个部分索引...')
        for sql, name in [
            ('CREATE INDEX IF NOT EXISTS idx_as_macd ON analysis_scores (scan_date, macd_dif) WHERE macd_dif IS NOT NULL', 'idx_as_macd'),
            ('CREATE INDEX IF NOT EXISTS idx_as_kdj ON analysis_scores (scan_date, kdj_j) WHERE kdj_j IS NOT NULL', 'idx_as_kdj'),
            ('CREATE INDEX IF NOT EXISTS idx_as_chip ON analysis_scores (scan_date, cost_50pct) WHERE cost_50pct IS NOT NULL', 'idx_as_chip'),
            ('CREATE INDEX IF NOT EXISTS idx_as_winner ON analysis_scores (scan_date, winner_rate) WHERE winner_rate IS NOT NULL', 'idx_as_winner'),
        ]:
            try:
                await conn.execute(sql)
                print(f'  ✅ {name}')
            except Exception as e:
                print(f'  ⚠️ {name}: {e}')

        # 4. 验证
        print('\n[4] 验证...')
        new_cols = await conn.fetch('''
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'analysis_scores'
            AND (column_name LIKE 'macd_%'
                 OR column_name LIKE 'kdj_%'
                 OR column_name LIKE 'rsi_%'
                 OR column_name LIKE 'boll_%'
                 OR column_name LIKE 'cci'
                 OR column_name LIKE 'cost_%'
                 OR column_name IN ('weight_avg', 'winner_rate', 'price_vs_cost'))
            ORDER BY column_name
        ''')
        print(f'  新字段: {len(new_cols)} 个')
        for c in new_cols:
            print(f'    {c["column_name"]:<25} {c["data_type"]}')

        # 5. 抽样 3 条, 确认老数据完整
        print('\n[5] 抽样老数据完整性...')
        sample = await conn.fetch('''
            SELECT scan_date, symbol, composite_score, tech_score, macd_dif, kdj_j, cost_50pct
            FROM analysis_scores LIMIT 3
        ''')
        for r in sample:
            print(f'  {r["scan_date"]} {r["symbol"]}: composite={r["composite_score"]}, tech={r["tech_score"]}, macd={r["macd_dif"]}, kdj={r["kdj_j"]}, chip={r["cost_50pct"]}')

        # 6. 索引验证
        print('\n[6] 索引验证...')
        idxs = await conn.fetch('''
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'analysis_scores' AND indexname LIKE 'idx_as_%'
        ''')
        for i in idxs:
            print(f'  ✅ {i["indexname"]}')

        print('\n' + '=' * 80)
        print('✅ 步骤 1 完成 — 数据库迁移成功!')
        print('=' * 80)
    finally:
        await conn.close()


asyncio.run(main())