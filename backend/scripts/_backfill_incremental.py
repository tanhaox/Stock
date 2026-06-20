# -*- coding: utf-8 -*-
"""v7.0.32 步骤 2b: 增量回填 — 给最近一天 analysis_scores 加技术因子.

用途: TG 扫描跑出新的 analysis_scores 后, 调这个脚本增量回填 MACD/KDJ/BOLL/CCI.
    (筹码字段 daily_chip_perf 暂无则留 NULL)
"""
import asyncio
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from datetime import date
from app.services.tdx_functions import MA, EMA, SMA, HHV, LLV
from app.core.config import settings
import asyncpg
import pandas as pd
import numpy as np

DSN = settings.DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://')
BATCH_SIZE = 500


def backfill_tech_at_date(close, high, low, idx):
    """算在 idx 当天的 MACD/KDJ/RSI/BOLL/CCI."""
    close = close.iloc[:idx + 1]
    high = high.iloc[:idx + 1]
    low = low.iloc[:idx + 1]
    dif = EMA(close, 12) - EMA(close, 26)
    dea = EMA(dif, 9)
    macd_bar = 2 * (dif - dea)
    llv9 = LLV(low, 9)
    hhv9 = HHV(high, 9)
    rsv = (close - llv9) / (hhv9 - llv9).replace(0, 1) * 100
    k = SMA(rsv, 3, 1)
    d = SMA(k, 3, 1)
    j = 3 * k - 2 * d
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    rsi6 = 100 - 100 / (1 + gain.ewm(alpha=1/6, adjust=False).mean() / loss.ewm(alpha=1/6, adjust=False).mean().replace(0, 1))
    rsi12 = 100 - 100 / (1 + gain.ewm(alpha=1/12, adjust=False).mean() / loss.ewm(alpha=1/12, adjust=False).mean().replace(0, 1))
    rsi24 = 100 - 100 / (1 + gain.ewm(alpha=1/24, adjust=False).mean() / loss.ewm(alpha=1/24, adjust=False).mean().replace(0, 1))
    boll_mid = MA(close, 20)
    std20 = close.rolling(20).std()
    boll_up = boll_mid + 2 * std20
    boll_low = boll_mid - 2 * std20
    boll_width = ((boll_up - boll_low) / boll_mid.replace(0, 1) * 100)
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
        ('boll_width', boll_width), ('cci', cci),
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
        target_date = date(2026, 6, 18)
        print(f'[1] 给 {target_date} 增量回填技术因子...')

        # 1. 找该日所有 (sym, scan_date) — 找出 macd_dif IS NULL 的
        samples = await conn.fetch('''
            SELECT scan_date, symbol FROM analysis_scores
            WHERE scan_date = $1 AND composite_score >= 50
            AND (macd_dif IS NULL OR kdj_j IS NULL)
            ORDER BY symbol
        ''', target_date)
        print(f'  待回填: {len(samples)}')

        if not samples:
            print('  ✅ 全部已回填')
            return

        symbols = list(set(r['symbol'] for r in samples))
        # 2. 拉 K 线
        earliest = target_date - pd.Timedelta(days=60).days if False else None  # python
        from datetime import timedelta
        earliest = target_date - timedelta(days=60)
        kline_rows = await conn.fetch('''
            SELECT ts_code, trade_date, open, high, low, close
            FROM daily_kline
            WHERE ts_code = ANY($1) AND trade_date BETWEEN $2 AND $3
            ORDER BY ts_code, trade_date
        ''', symbols, earliest, target_date)

        kline_by_sym = {}
        for r in kline_rows:
            kline_by_sym.setdefault(r['ts_code'], []).append({
                'date': r['trade_date'],
                'open': float(r['open']),
                'high': float(r['high']),
                'low': float(r['low']),
                'close': float(r['close']),
            })

        # 3. 拉筹码 (6-18 没数据,跳过)
        chip_rows = await conn.fetch('''
            SELECT ts_code, trade_date, cost_5pct, cost_50pct, cost_95pct, weight_avg, winner_rate
            FROM daily_chip_perf
            WHERE ts_code = ANY($1) AND trade_date = $2
        ''', symbols, target_date)
        chip_by_sym = {r['ts_code']: dict(r) for r in chip_rows}
        print(f'  筹码覆盖: {len(chip_by_sym)} / {len(symbols)} 只')

        # 4. 算 + 更新
        update_records = []
        for sym, klines in kline_by_sym.items():
            if len(klines) < 30:
                continue
            df = pd.DataFrame(klines)
            close_s, high_s, low_s = df['close'], df['high'], df['low']
            date_s = df['date']
            date_to_idx = {d: i for i, d in enumerate(date_s)}
            idx = date_to_idx.get(target_date)
            if idx is None or idx < 20:
                continue
            try:
                tech = backfill_tech_at_date(close_s, high_s, low_s, idx)
            except Exception:
                continue
            chip = chip_by_sym.get(sym, {})
            cost_5 = safe_float(chip.get('cost_5pct'))
            cost_50 = safe_float(chip.get('cost_50pct'))
            cost_95 = safe_float(chip.get('cost_95pct'))
            wavg = safe_float(chip.get('weight_avg'))
            winner = safe_float(chip.get('winner_rate'))
            cost_spread = (cost_95 - cost_5) if (cost_95 and cost_5) else None
            close_now = float(close_s.iloc[idx])
            price_vs_cost = ((close_now - wavg) / wavg * 100) if (wavg and wavg > 0) else None
            update_records.append({
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
                'sym': sym,
            })

        print(f'[2] 准备更新 {len(update_records)} 条')
        for i in range(0, len(update_records), BATCH_SIZE):
            batch = update_records[i:i + BATCH_SIZE]
            values = [(
                r['macd_dif'], r['macd_dea'], r['macd_bar'],
                r['kdj_k'], r['kdj_d'], r['kdj_j'],
                r['rsi_6'], r['rsi_12'], r['rsi_24'],
                r['boll_upper'], r['boll_mid'], r['boll_lower'],
                r['boll_width'], r['boll_pos'], r['cci'],
                r['cost_5pct'], r['cost_50pct'], r['cost_95pct'],
                r['weight_avg'], r['winner_rate'],
                r['cost_spread'], r['price_vs_cost'],
                r['sym'], target_date,
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
                WHERE symbol = $23 AND scan_date = $24
            ''', values)

        print(f'[3] 更新完成')
        # 验证
        cov = await conn.fetchrow(f'''
            SELECT COUNT(*) as n, COUNT(macd_dif) as macd, COUNT(kdj_j) as kdj, COUNT(cost_50pct) as chip
            FROM analysis_scores WHERE scan_date = $1
        ''', target_date)
        print(f'  {target_date}: n={cov["n"]}, macd={cov["macd"]}, kdj={cov["kdj"]}, chip={cov["chip"]}')
    finally:
        await conn.close()


asyncio.run(main())