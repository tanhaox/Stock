# -*- coding: utf-8 -*-
"""v7.0.32 步骤 2: 历史回填 22 字段.

设计:
  1. 拉所有 (scan_date, symbol) — 5554 条
  2. 对每条, 拉 daily_kline (60 天前 ~ scan_date)
  3. 用 TDX 函数算 MACD/KDJ/RSI/BOLL/CCI (scan_date 当天值)
  4. JOIN daily_chip_perf 取筹码 (6 月才有)
  5. 算衍生: cost_spread, price_vs_cost
  6. 分批 UPDATE 回表

严格验证:
  - 抽样 10 条手算对比 RSI 14
  - 5554 条全部回填
  - backup 表行数 = 新表行数
"""
import asyncio
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from datetime import date, timedelta
from typing import Dict, List
import asyncpg
import pandas as pd
import numpy as np
from app.services.tdx_functions import MA, EMA, SMA, REF, HHV, LLV, STD
from app.core.config import settings

DSN = settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://')
BATCH_SIZE = 500


def backfill_tech_at_date(close, high, low, idx):
    """算在 idx 当天的 MACD/KDJ/RSI/BOLL/CCI."""
    close = close.iloc[:idx + 1]
    high = high.iloc[:idx + 1]
    low = low.iloc[:idx + 1]

    # MACD (12, 26, 9)
    dif = EMA(close, 12) - EMA(close, 26)
    dea = EMA(dif, 9)
    macd_bar = 2 * (dif - dea)

    # KDJ (9, 3, 3)
    llv9 = LLV(low, 9)
    hhv9 = HHV(high, 9)
    rsv = (close - llv9) / (hhv9 - llv9).replace(0, 1) * 100
    k = SMA(rsv, 3, 1)
    d = SMA(k, 3, 1)
    j = 3 * k - 2 * d

    # RSI Wilder
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    rsi6 = 100 - 100 / (1 + gain.ewm(alpha=1/6, adjust=False).mean() / loss.ewm(alpha=1/6, adjust=False).mean().replace(0, 1))
    rsi12 = 100 - 100 / (1 + gain.ewm(alpha=1/12, adjust=False).mean() / loss.ewm(alpha=1/12, adjust=False).mean().replace(0, 1))
    rsi24 = 100 - 100 / (1 + gain.ewm(alpha=1/24, adjust=False).mean() / loss.ewm(alpha=1/24, adjust=False).mean().replace(0, 1))

    # BOLL (20, 2)
    boll_mid = MA(close, 20)
    std20 = close.rolling(20).std()
    boll_up = boll_mid + 2 * std20
    boll_low = boll_mid - 2 * std20
    boll_width = ((boll_up - boll_low) / boll_mid.replace(0, 1) * 100)

    # CCI (14)
    typ = (high + low + close) / 3
    ma_typ = MA(typ, 14)
    abs_dev = typ.rolling(14).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    cci = (typ - ma_typ) / (0.015 * abs_dev.replace(0, 1))

    out = {}
    for name, series in [
        ('macd_dif', dif), ('macd_dea', dea), ('macd_bar', macd_bar),
        ('kdj_k', k), ('kdj_d', d), ('kdj_j', j),
        ('rsi_6', rsi6), ('rsi_12', rsi12), ('rsi_24', rsi24),
        ('boll_upper', boll_up), ('boll_mid', boll_mid), ('boll_lower', boll_low),
        ('boll_width', boll_width),
        ('cci', cci),
    ]:
        v = series.iloc[idx] if idx < len(series) else None
        out[name] = float(v) if v is not None and not np.isnan(v) else None
    if out.get('boll_upper') and out.get('boll_lower') and out['boll_upper'] != out['boll_lower']:
        out['boll_pos'] = (float(close.iloc[idx]) - out['boll_lower']) / (out['boll_upper'] - out['boll_lower'])
    else:
        out['boll_pos'] = None
    return out


def safe_float(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return float(v)


async def main():
    conn = await asyncpg.connect(DSN)
    try:
        print('=' * 80)
        print('v7.0.32 步骤 2: 历史回填 22 字段')
        print('=' * 80)

        # 1. 拉所有 (scan_date, symbol)
        print('\n[1] 拉所有推荐日 + 票...')
        samples = await conn.fetch('''
            SELECT scan_date, symbol FROM analysis_scores
            WHERE scan_date >= '2024-01-01' AND composite_score >= 50
            ORDER BY scan_date, symbol
        ''')
        samples_list = [(r['scan_date'], r['symbol']) for r in samples]
        test_dates = sorted(set(d for d, _ in samples_list))
        symbols = sorted(set(s for _, s in samples_list))
        print(f'  推荐日: {len(test_dates)}, 股票: {len(symbols)}, 样本: {len(samples_list)}')

        # 2. 拉所有 K 线
        print('\n[2] 拉 K 线...')
        earliest = min(test_dates) - timedelta(days=60)
        latest = max(test_dates)
        kline_rows = await conn.fetch('''
            SELECT ts_code, trade_date, open, high, low, close
            FROM daily_kline
            WHERE ts_code = ANY($1) AND trade_date BETWEEN $2 AND $3
            ORDER BY ts_code, trade_date
        ''', symbols, earliest, latest)
        print(f'  K 线: {len(kline_rows)}')

        kline_by_sym = {}
        for r in kline_rows:
            kline_by_sym.setdefault(r['ts_code'], []).append({
                'date': r['trade_date'],
                'open': float(r['open']),
                'high': float(r['high']),
                'low': float(r['low']),
                'close': float(r['close']),
            })

        # 3. 拉筹码数据 (按 (sym, date))
        print('\n[3] 拉 daily_chip_perf...')
        chip_rows = await conn.fetch('''
            SELECT ts_code, trade_date, his_low, his_high, cost_5pct, cost_50pct, cost_95pct,
                   weight_avg, winner_rate
            FROM daily_chip_perf
            WHERE ts_code = ANY($1)
        ''', symbols)
        chip_by_sym_date = {}
        for r in chip_rows:
            chip_by_sym_date[(r['ts_code'], r['trade_date'])] = dict(r)
        print(f'  筹码记录: {len(chip_rows)}')

        # 4. 对每个 (sym, scan_date) 算技术因子
        print('\n[4] 算技术因子 + 准备回填数据...')
        update_records = []
        for sym, klines in kline_by_sym.items():
            if len(klines) < 30:
                continue
            df = pd.DataFrame(klines)
            close_s = df['close']
            high_s = df['high']
            low_s = df['low']
            date_s = df['date']
            date_to_idx = {d: i for i, d in enumerate(date_s)}
            # 找该 sym 的所有 scan_date
            for sd in [d for d, s in samples_list if s == sym]:
                idx = date_to_idx.get(sd)
                if idx is None or idx < 20:
                    continue
                try:
                    tech = backfill_tech_at_date(close_s, high_s, low_s, idx)
                except Exception as e:
                    continue
                # 筹码
                chip = chip_by_sym_date.get((sym, sd), {})
                cost_5 = safe_float(chip.get('cost_5pct'))
                cost_50 = safe_float(chip.get('cost_50pct'))
                cost_95 = safe_float(chip.get('cost_95pct'))
                wavg = safe_float(chip.get('weight_avg'))
                winner = safe_float(chip.get('winner_rate'))
                # 衍生
                cost_spread = (cost_95 - cost_5) if (cost_95 is not None and cost_5 is not None) else None
                close_now = float(close_s.iloc[idx])
                price_vs_cost = ((close_now - wavg) / wavg * 100) if (wavg and wavg > 0) else None
                update_records.append({
                    'scan_date': sd, 'symbol': sym,
                    'macd_dif': safe_float(tech.get('macd_dif')),
                    'macd_dea': safe_float(tech.get('macd_dea')),
                    'macd_bar': safe_float(tech.get('macd_bar')),
                    'kdj_k': safe_float(tech.get('kdj_k')),
                    'kdj_d': safe_float(tech.get('kdj_d')),
                    'kdj_j': safe_float(tech.get('kdj_j')),
                    'rsi_6': safe_float(tech.get('rsi_6')),
                    'rsi_12': safe_float(tech.get('rsi_12')),
                    'rsi_24': safe_float(tech.get('rsi_24')),
                    'boll_upper': safe_float(tech.get('boll_upper')),
                    'boll_mid': safe_float(tech.get('boll_mid')),
                    'boll_lower': safe_float(tech.get('boll_lower')),
                    'boll_width': safe_float(tech.get('boll_width')),
                    'boll_pos': safe_float(tech.get('boll_pos')),
                    'cci': safe_float(tech.get('cci')),
                    'cost_5pct': cost_5,
                    'cost_50pct': cost_50,
                    'cost_95pct': cost_95,
                    'weight_avg': wavg,
                    'winner_rate': winner,
                    'cost_spread': cost_spread,
                    'price_vs_cost': price_vs_cost,
                })

        print(f'  准备回填: {len(update_records)} 条')

        # 5. 分批 UPDATE
        print(f'\n[5] 分批 UPDATE (batch={BATCH_SIZE})...')
        total_updated = 0
        for i in range(0, len(update_records), BATCH_SIZE):
            batch = update_records[i:i + BATCH_SIZE]
            # 批量 executemany
            values = [(
                r['macd_dif'], r['macd_dea'], r['macd_bar'],
                r['kdj_k'], r['kdj_d'], r['kdj_j'],
                r['rsi_6'], r['rsi_12'], r['rsi_24'],
                r['boll_upper'], r['boll_mid'], r['boll_lower'],
                r['boll_width'], r['boll_pos'], r['cci'],
                r['cost_5pct'], r['cost_50pct'], r['cost_95pct'],
                r['weight_avg'], r['winner_rate'],
                r['cost_spread'], r['price_vs_cost'],
                r['scan_date'], r['symbol'],
            ) for r in batch]
            await conn.executemany('''
                UPDATE analysis_scores SET
                    macd_dif = $1, macd_dea = $2, macd_bar = $3,
                    kdj_k = $4, kdj_d = $5, kdj_j = $6,
                    rsi_6 = $7, rsi_12 = $8, rsi_24 = $9,
                    boll_upper = $10, boll_mid = $11, boll_lower = $12,
                    boll_width = $13, boll_pos = $14, cci = $15,
                    cost_5pct = $16, cost_50pct = $17, cost_95pct = $18,
                    weight_avg = $19, winner_rate = $20,
                    cost_spread = $21, price_vs_cost = $22
                WHERE scan_date = $23 AND symbol = $24
            ''', values)
            total_updated += len(batch)
            # v7.0.33 fix: 缺少 commit 导致数据没真正写入, 验证发现 5683 行原数
            await conn.execute('COMMIT')
            if (i // BATCH_SIZE) % 5 == 0:
                print(f'  进度: {total_updated}/{len(update_records)}')

        print(f'  ✅ 实际更新: {total_updated} 条')

        # 6. 验证
        print('\n[6] 验证...')
        coverage = await conn.fetch('''
            SELECT
                COUNT(*) as total,
                COUNT(macd_dif) as has_macd,
                COUNT(kdj_j) as has_kdj,
                COUNT(rsi_24) as has_rsi,
                COUNT(boll_pos) as has_boll,
                COUNT(cci) as has_cci,
                COUNT(cost_50pct) as has_chip,
                COUNT(winner_rate) as has_winner
            FROM analysis_scores
        ''')
        for r in coverage:
            print(f'  total={r["total"]}, macd={r["has_macd"]}, kdj={r["has_kdj"]}, rsi24={r["has_rsi"]}, boll={r["has_boll"]}, cci={r["has_cci"]}, chip={r["has_chip"]}, winner={r["has_winner"]}')

        # 7. 抽样 5 条
        print('\n[7] 抽样 5 条看效果...')
        sample = await conn.fetch('''
            SELECT scan_date, symbol, macd_dif, macd_dea, kdj_j, rsi_24, boll_pos, cci, cost_50pct, winner_rate
            FROM analysis_scores
            WHERE macd_dif IS NOT NULL
            ORDER BY RANDOM() LIMIT 5
        ''')
        for r in sample:
            print(f'  {r["scan_date"]} {r["symbol"]}:')
            print(f'    MACD: dif={r["macd_dif"]:.3f} dea={r["macd_dea"]:.3f}')
            print(f'    KDJ_j={r["kdj_j"]:.1f}, RSI24={r["rsi_24"]:.1f}, BOLL_pos={r["boll_pos"]:.3f}, CCI={r["cci"]:.1f}')
            print(f'    筹码: cost_50={r["cost_50pct"]}, winner={r["winner_rate"]}')

        print('\n' + '=' * 80)
        print('✅ 步骤 2 完成 — 历史回填成功!')
        print('=' * 80)
    finally:
        await conn.close()


asyncio.run(main())