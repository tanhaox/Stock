"""Download today's market data from Tushare."""
import asyncio
import sys
sys.path.insert(0, 'C:/AI-Agent-Local/Stock/backend')
from app.services.tushare_common import call_tushare
from app.core.database import async_session_factory
from sqlalchemy import text
from datetime import date

async def download():
    today_str = '20260525'
    today = date(2026, 5, 25)
    results = {}

    # 1. Daily K-line
    print('Downloading daily_kline...')
    rows = await call_tushare('daily', {'trade_date': today_str},
        'ts_code,trade_date,open,high,low,close,vol,amount')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO daily_kline (ts_code,trade_date,open,high,low,close,volume)
                    VALUES(:ts,:td,:o,:h,:l,:c,:v) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,volume=EXCLUDED.volume'''),
                    {'ts':r['ts_code'],'td':today,'o':float(r.get('open',0)or 0),'h':float(r.get('high',0)or 0),'l':float(r.get('low',0)or 0),'c':float(r.get('close',0)or 0),'v':float(r.get('vol',0)or 0)})
            await s.commit()
        results['daily_kline'] = len(rows)
    else:
        results['daily_kline'] = 0

    # 2. Index daily
    print('Downloading index_daily...')
    rows = await call_tushare('index_daily', {'ts_code':'000001.SH','trade_date':today_str},
        'ts_code,trade_date,open,high,low,close,vol,amount')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO daily_kline (ts_code,trade_date,open,high,low,close,volume)
                    VALUES(:ts,:td,:o,:h,:l,:c,:v) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,volume=EXCLUDED.volume'''),
                    {'ts':r['ts_code'],'td':today,'o':float(r.get('open',0)or 0),'h':float(r.get('high',0)or 0),'l':float(r.get('low',0)or 0),'c':float(r.get('close',0)or 0),'v':float(r.get('vol',0)or 0)})
            await s.commit()
        results['index_daily'] = len(rows)
    else:
        results['index_daily'] = 0

    # 3. daily_basic
    print('Downloading daily_basic...')
    rows = await call_tushare('daily_basic', {'trade_date': today_str},
        'ts_code,trade_date,total_mv,pe,pb,volume_ratio,turnover_rate')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO daily_basic (ts_code,trade_date,total_mv,pe,pb,volume_ratio,turnover_rate)
                    VALUES(:ts,:td,:mv,:pe,:pb,:vr,:tr) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    total_mv=EXCLUDED.total_mv,pe=EXCLUDED.pe,pb=EXCLUDED.pb,volume_ratio=EXCLUDED.volume_ratio,turnover_rate=EXCLUDED.turnover_rate'''),
                    {'ts':r['ts_code'],'td':today,'mv':float(r.get('total_mv',0)or 0),'pe':float(r.get('pe',0)or 0),'pb':float(r.get('pb',0)or 0),'vr':float(r.get('volume_ratio',0)or 0),'tr':float(r.get('turnover_rate',0)or 0)})
            await s.commit()
        results['daily_basic'] = len(rows)
    else:
        results['daily_basic'] = 0

    # 4. moneyflow
    print('Downloading moneyflow...')
    rows = await call_tushare('moneyflow', {'trade_date': today_str},
        'ts_code,trade_date,buy_sm_vol,sell_sm_vol,buy_md_vol,sell_md_vol,buy_lg_vol,sell_lg_vol')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO moneyflow (ts_code,trade_date,buy_sm_vol,sell_sm_vol,buy_md_vol,sell_md_vol,buy_lg_vol,sell_lg_vol)
                    VALUES(:ts,:td,:bsv,:ssv,:bmv,:smv,:blv,:slv) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    buy_sm_vol=EXCLUDED.buy_sm_vol,sell_sm_vol=EXCLUDED.sell_sm_vol,buy_md_vol=EXCLUDED.buy_md_vol,sell_md_vol=EXCLUDED.sell_md_vol,buy_lg_vol=EXCLUDED.buy_lg_vol,sell_lg_vol=EXCLUDED.sell_lg_vol'''),
                    {'ts':r['ts_code'],'td':today,'bsv':float(r.get('buy_sm_vol',0)or 0),'ssv':float(r.get('sell_sm_vol',0)or 0),'bmv':float(r.get('buy_md_vol',0)or 0),'smv':float(r.get('sell_md_vol',0)or 0),'blv':float(r.get('buy_lg_vol',0)or 0),'slv':float(r.get('sell_lg_vol',0)or 0)})
            await s.commit()
        results['moneyflow'] = len(rows)
    else:
        results['moneyflow'] = 0

    # 5. margin_trading
    print('Downloading margin_trading...')
    rows = await call_tushare('margin', {'trade_date': today_str}, 'trade_date,rzye,rzmre,rqye')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO margin_trading (ts_code,trade_date,rzye,rzmre,rqye)
                    VALUES('000001.SH',:td,:rze,:rzm,:rqe) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    rzye=EXCLUDED.rzye,rzmre=EXCLUDED.rzmre,rqye=EXCLUDED.rqye'''),
                    {'td':today,'rze':float(r.get('rzye',0)or 0)/1e8,'rzm':float(r.get('rzmre',0)or 0)/1e8,'rqe':float(r.get('rqye',0)or 0)/1e8})
            await s.commit()
        results['margin'] = len(rows)
    else:
        results['margin'] = 0

    # 6. toplist_daily
    print('Downloading toplist_daily...')
    rows = await call_tushare('top_list', {'trade_date': today_str},
        'trade_date,ts_code,name,close,pct_change,amount,l_buy,l_sell,net_amount')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO toplist_daily (trade_date,ts_code,name,close,pct_change,amount,l_buy,l_sell,net_amount)
                    VALUES(:td,:ts,:n,:c,:pc,:a,:lb,:ls,:na) ON CONFLICT (trade_date,ts_code) DO UPDATE SET
                    close=EXCLUDED.close,pct_change=EXCLUDED.pct_change,amount=EXCLUDED.amount,l_buy=EXCLUDED.l_buy,l_sell=EXCLUDED.l_sell,net_amount=EXCLUDED.net_amount'''),
                    {'td':today,'ts':r['ts_code'],'n':r.get('name',''),'c':float(r.get('close',0)or 0),'pc':float(r.get('pct_change',0)or 0),'a':float(r.get('amount',0)or 0),'lb':float(r.get('l_buy',0)or 0),'ls':float(r.get('l_sell',0)or 0),'na':float(r.get('net_amount',0)or 0)})
            await s.commit()
        results['toplist'] = len(rows)
    else:
        results['toplist'] = 0

    # 7. commodity_futures
    print('Downloading commodity_futures...')
    codes = 'CU2605.SHF,AL2605.SHF,ZN2605.SHF,RB2610.SHF,HC2610.SHF,FU2609.SHF,RU2609.SHF,AU2606.SHF,AG2606.SHF'
    rows = await call_tushare('fut_daily', {'ts_code': codes, 'trade_date': today_str},
        'ts_code,trade_date,open,high,low,close,vol')
    if rows:
        async with async_session_factory() as s:
            for r in rows:
                await s.execute(text('''INSERT INTO commodity_futures (ts_code,trade_date,open,high,low,close,volume)
                    VALUES(:ts,:td,:o,:h,:l,:c,:v) ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                    open=EXCLUDED.open,high=EXCLUDED.high,low=EXCLUDED.low,close=EXCLUDED.close,volume=EXCLUDED.volume'''),
                    {'ts':r['ts_code'],'td':today,'o':float(r.get('open',0)or 0),'h':float(r.get('high',0)or 0),'l':float(r.get('low',0)or 0),'c':float(r.get('close',0)or 0),'v':float(r.get('vol',0)or 0)})
            await s.commit()
        results['commodity'] = len(rows)
    else:
        results['commodity'] = 0

    print()
    print('=== Download Results for 2026-05-25 ===')
    total = 0
    for k, v in results.items():
        status = 'OK' if v > 0 else 'EMPTY!'
        print(f'  {k:20s}: {v:>5} rows  [{status}]')
        total += v
    print(f'  {"TOTAL":20s}: {total:>5} rows')

    if total == 0:
        print()
        print('ALL EMPTY - Tushare data not yet available.')

asyncio.run(download())
