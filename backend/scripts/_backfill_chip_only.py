# -*- coding: utf-8 -*-
"""回填 analysis_scores 的筹码字段(从 daily_chip_perf JOIN).

用法:  python -m scripts._backfill_chip_only
"""
import asyncio
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from app.core.database import async_session_factory
from sqlalchemy import text

BATCH = 500


async def main():
    async with async_session_factory() as s:
        print('[1] 查 analysis_scores 中 cost_50pct 为 NULL 但有 daily_chip_perf 的票...')
        rows = (await s.execute(text('''
            SELECT a.scan_date, a.symbol
            FROM analysis_scores a
            INNER JOIN daily_chip_perf c ON c.ts_code = a.symbol AND c.trade_date = a.scan_date
            WHERE a.cost_50pct IS NULL
            ORDER BY a.scan_date, a.symbol
        '''))).fetchall()
        print(f'  待回填: {len(rows)}')

        if not rows:
            print('  无需回填')
            return

        print(f'[2] 分批 UPDATE 筹码字段 (batch={BATCH})...')
        total = 0
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            for r in batch:
                c = (await s.execute(text('''
                    SELECT cost_5pct, cost_50pct, cost_95pct, weight_avg, winner_rate
                    FROM daily_chip_perf
                    WHERE ts_code = :s AND trade_date = :d
                '''), {'s': r.symbol, 'd': r.scan_date})).first()
                if not c:
                    continue
                cost_5 = c.cost_5pct
                cost_50 = c.cost_50pct
                cost_95 = c.cost_95pct
                wavg = c.weight_avg
                spread = (cost_95 - cost_5) if (cost_95 is not None and cost_5 is not None) else None
                close_row = (await s.execute(text('''
                    SELECT close FROM daily_kline
                    WHERE ts_code = :s AND trade_date = :d
                '''), {'s': r.symbol, 'd': r.scan_date})).first()
                price_vs_cost = None
                if close_row and wavg and wavg > 0:
                    price_vs_cost = (float(close_row.close) - wavg) / wavg * 100

                await s.execute(text('''
                    UPDATE analysis_scores SET
                        cost_5pct = :cost_5pct,
                        cost_50pct = :cost_50pct,
                        cost_95pct = :cost_95pct,
                        weight_avg = :weight_avg,
                        winner_rate = :winner_rate,
                        cost_spread = :cost_spread,
                        price_vs_cost = :price_vs_cost
                    WHERE scan_date = :scan_date AND symbol = :symbol
                '''), {
                    'cost_5pct': cost_5, 'cost_50pct': cost_50, 'cost_95pct': cost_95,
                    'weight_avg': wavg, 'winner_rate': c.winner_rate,
                    'cost_spread': spread, 'price_vs_cost': price_vs_cost,
                    'scan_date': r.scan_date, 'symbol': r.symbol,
                })
            await s.commit()
            total += len(batch)
            if (i // BATCH) % 5 == 0:
                print(f'  进度: {total}/{len(rows)}')
        print(f'  ✅ 完成: {total} 条')


asyncio.run(main())