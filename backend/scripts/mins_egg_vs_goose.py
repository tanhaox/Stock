"""5分钟线分时特征验证 — 蛋 vs 大雁."""
import asyncio, sys, numpy as np, os
from collections import defaultdict
import httpx
from dotenv import load_dotenv
load_dotenv('C:/AI-Agent-Local/Stock/backend/.env')
TOKEN = os.getenv('TUSHARE_TOKEN')
SEM = asyncio.Semaphore(3)
from datetime import date, timedelta

async def download_5min(ts_code, days=30):
    end_dt = date.today().strftime('%Y-%m-%d')
    start_dt = (date.today() - timedelta(days=days+5)).strftime('%Y-%m-%d')
    async with SEM:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                resp = await c.post('https://api.tushare.pro', json={
                    'api_name':'stk_mins','token':TOKEN,
                    'params':{'ts_code':ts_code,'freq':'5min',
                              'start_date':f'{start_dt} 09:00:00','end_date':f'{end_dt} 15:00:00'},
                    'fields':'ts_code,trade_time,open,close,high,low,vol,amount'})
                data=resp.json()
                if data.get('code')!=0: return []
                return data.get('data',{}).get('items',[]) or []
        except: return []

def extract_minute_features(bars):
    if len(bars)<200: return None
    n=len(bars)
    closes=np.array([float(b[3]) for b in bars]); opens=np.array([float(b[2]) for b in bars])
    highs=np.array([float(b[4]) for b in bars]); lows=np.array([float(b[5]) for b in bars])
    vols=np.array([float(b[6]) for b in bars])
    total_days=len(set(b[1][:10] for b in bars))

    feats={}
    # V反频率
    v_count=0
    for i in range(5,n-6):
        drop=(lows[i]-closes[i-5])/closes[i-5]*100
        if drop<-1.5:
            for j in range(i+1,min(i+7,n)):
                recovery=(highs[j]-lows[i])/lows[i]*100
                if recovery>1.0: v_count+=1; break
    feats['v_reversal_per_day']=round(v_count/max(total_days,1),2)

    # 尾盘量比
    tail_r=[]; tot_r=[]
    for d in set(b[1][:10] for b in bars):
        db=[b2 for b2 in bars if b2[1][:10]==d]
        if len(db)<20: continue
        tail=[b2 for b2 in db if b2[1]>=f'{d} 14:30']
        tot_r.append(sum(float(b2[6]) for b2 in db))
        tail_r.append(sum(float(b2[6]) for b2 in tail))
    feats['tail_vol_ratio']=round(np.mean([t/max(tot,1) for t,tot in zip(tail_r,tot_r)]),3) if tail_r else 0.25

    # 量能集中度
    concs=[]
    for d in set(b[1][:10] for b in bars):
        dv=[float(b2[6]) for b2 in bars if b2[1][:10]==d]
        if len(dv)>=20: concs.append(max(dv)/max(np.mean(dv),1))
    feats['vol_concentration']=round(np.mean(concs),2) if concs else 3.0

    # 开盘冲高率
    spike_d=0
    for d in set(b[1][:10] for b in bars):
        db=sorted([b2 for b2 in bars if b2[1][:10]==d],key=lambda x:x[1])
        if len(db)<20: continue
        do=float(db[0][2]); dc=float(db[-1][3]); fh=max(float(b2[4]) for b2 in db[:6])
        if (fh-do)/do*100>2 and (dc-do)/do*100<1: spike_d+=1
    feats['morning_spike_rate']=round(spike_d/max(total_days,1),3)

    # 日内振幅
    amps=[]
    for d in set(b[1][:10] for b in bars):
        dh=[float(b2[4]) for b2 in bars if b2[1][:10]==d]
        dl=[float(b2[5]) for b2 in bars if b2[1][:10]==d]
        if dh and dl: amps.append((max(dh)-min(dl))/max(min(dl),0.01)*100)
    feats['avg_daily_amp']=round(np.mean(amps),1) if amps else 0

    # 脉冲方向
    avg_v=np.mean(vols)
    spike_up=sum(1 for i in range(1,n) if vols[i]>avg_v*3 and closes[i]>opens[i])
    spike_tot=sum(1 for i in range(1,n) if vols[i]>avg_v*3)
    feats['spike_up_ratio']=round(spike_up/max(spike_tot,1),2)

    # 连续红量
    reds=closes>opens; max_red=0; cur=0
    for v in reds:
        if v: cur+=1; max_red=max(max_red,cur)
        else: cur=0
    feats['max_red_consec']=max_red

    # 价格稳定性
    ma_l=np.convolve(closes,np.ones(20)/20,mode='valid')
    feats['price_stability']=round(float(np.mean(np.abs(closes[-len(ma_l):]-ma_l)/ma_l*100)),2) if len(ma_l)>10 else 1.0

    return feats

async def main():
    from sqlalchemy import create_engine, text
    e=create_engine('postgresql://postgres:postgres@127.0.0.1:15432/stock_data')
    with e.connect() as c:
        egg_codes=[row[0] for row in c.execute(text('SELECT ts_code FROM alphaflow_pool ORDER BY current_prob DESC LIMIT 10')).fetchall()]
        goose_codes=[row[0] for row in c.execute(text('SELECT ts_code FROM goose_archive ORDER BY gain_pct DESC LIMIT 10')).fetchall()]
    e.dispose()

    print(f'Downloading 5-min: {len(egg_codes)} eggs + {len(goose_codes)} geese...')
    egg_feats=[]; goose_feats=[]

    for label,codes,target in [('egg',egg_codes,egg_feats),('goose',goose_codes,goose_feats)]:
        for code in codes:
            bars=await download_5min(code,30)
            if bars:
                f=extract_minute_features(bars)
                if f:
                    target.append(f)
                    print(f'  [{label}] {code}: V反={f["v_reversal_per_day"]} 尾盘={f["tail_vol_ratio"]} 振幅={f["avg_daily_amp"]}%')

    if len(egg_feats)>=5 and len(goose_feats)>=5:
        from scipy import stats
        print(f'\\n=== 蛋({len(egg_feats)}) vs 大雁({len(goose_feats)}) ===')
        for key in ['v_reversal_per_day','tail_vol_ratio','vol_concentration','morning_spike_rate','avg_daily_amp','spike_up_ratio','max_red_consec','price_stability']:
            ev=[f[key] for f in egg_feats if key in f]
            gv=[f[key] for f in goose_feats if key in f]
            if len(ev)<5 or len(gv)<5: continue
            em=np.mean(ev); gm=np.mean(gv); diff=em-gm
            t,p=stats.ttest_ind(ev,gv,equal_var=False)
            d=(em-gm)/np.sqrt((np.var(ev)+np.var(gv))/2)
            v='***显著' if p<0.05 and abs(d)>0.5 else ('*弱' if p<0.10 else '—')
            print(f'  {key:<22}: 蛋={em:.3f} 雁={gm:.3f} d={d:+.3f} p={p:.3f} {v}')

asyncio.run(main())
