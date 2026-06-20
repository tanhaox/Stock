import { useEffect, useState, useMemo } from 'react';
import api from '../lib/api';
import StatusBadge from '../components/StatusBadge';
import MetricCard from '../components/MetricCard';
import SectionHeader from '../components/SectionHeader';
import EmptyState from '../components/EmptyState';
import SkeletonRow from '../components/SkeletonRow';
import ProgressBar from '../components/ProgressBar';
import { getSignalStyle } from '../lib/signalColor';
import { T, TH as Th, TD as Td } from '../lib/designTokens';
import { useMediaQuery } from '../lib/useMediaQuery';

const C = T.c;
const tierColor=(t:string)=>t==='active'?C.green:t==='observe'?C.amber:t==='veteran'?C.violet:t==='dormant'?C.textMuted:C.textDim;
const tierLabel=(t:string)=>t==='active'?'🔥活跃':t==='observe'?'👀观察':t==='veteran'?'🎖老兵':t==='dormant'?'💤休眠':t;
const gColor=(g:string)=>({'抗跌真强':C.red,'满配待启':C.amber,'待确认':C.blue,'新秀锁优':C.green,'新秀关注':C.code,'弱观察':C.textDim,'普通观察':C.textMuted} as any)[g]||C.textDim;

export default function AlphaFlowPage() {
  const [pool,setPool]=useState<any[]>([]);
  const [status,setStatus]=useState<any>(null);
  const [loading,setLoading]=useState(true);
  const [scanning,setScanning]=useState(false);
  const [tierFilter,setTierFilter]=useState('all');
  const [scanMsg,setScanMsg]=useState('');
  const [expanded,setExpanded]=useState<string|null>(null);
  const [lockDetail,setLockDetail]=useState<any>(null);
  const [detailLoading,setDetailLoading]=useState(false);
  const [compReport,setCompReport]=useState<any>(null);
  const [compLoading,setCompLoading]=useState(false);
  const [marketInfo,setMarketInfo]=useState<any>(null);
  const [modelStatus,setModelStatus]=useState<'loading'|'ok'|'missing'>('loading');
  const isMobile = useMediaQuery('(max-width:768px)');

  const CACHE_KEY = 'alphaflow_pool_cache';
  const CACHE_TTL = 300000; // 5 minutes

  const load=async()=>{
    // 1. 优先展示缓存
    let hasCache = false;
    try {
      const raw = localStorage.getItem(CACHE_KEY);
      if (raw) {
        const cached = JSON.parse(raw);
        if (Date.now() - cached.ts < CACHE_TTL && cached.tier === tierFilter) {
          setPool(cached.pool||[]);
          setMarketInfo(cached.market||null);
          setStatus(cached.status||null);
          if (cached.status) setModelStatus(cached.status.model_version&&cached.status.model_version!=='fallback_rules'?'ok':'missing');
          hasCache = true;
        }
      }
    } catch {}
    if (!hasCache) setLoading(true);

    // 2. 后台拉取
    try {
      const r = await api.get('/alphaflow/pool', { params: { limit: 500, tier: tierFilter } });
      const poolData = r.data.data || [];
      setPool(poolData);
      setMarketInfo(r.data.market || null);
      if (r.data.removed > 0) setScanMsg(`清理:${r.data.removed}只`);
      const s = await api.get('/alphaflow/status');
      setStatus(s.data);
      setModelStatus(s.data?.model_version && s.data.model_version !== 'fallback_rules' ? 'ok' : 'missing');
      try {
        localStorage.setItem(CACHE_KEY, JSON.stringify({
          pool: poolData, market: r.data.market, status: s.data, tier: tierFilter, ts: Date.now()
        }));
      } catch {}
    } catch {
      if (!hasCache) setModelStatus('missing');
    }
    setLoading(false);
  };
  const sortedPool = useMemo(() => {
    const signalRank = (s: any) => s?.signal === 'buy' ? 0 : s?.signal === 'sell' ? 2 : 1;
    return [...pool].sort((a: any, b: any) => signalRank(a.sxqs) - signalRank(b.sxqs) || (b.timing_score || b.prob || 0) - (a.timing_score || a.prob || 0));
  }, [pool]);
  useEffect(()=>{load()},[tierFilter]);
  const doScan=async()=>{
    setScanning(true);setScanMsg('启动两期扫描...');
    try {
      const response = await fetch('/api/alphaflow/scan', { method: 'POST' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No stream');
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'progress') {
                const pct = event.total > 0 ? Math.round(event.current / event.total * 100) : 0;
                setScanMsg(`[${event.phase}] ${event.msg} (${pct}%)`);
              } else if (event.type === 'done_phase1') {
                setScanMsg(`第一期完成: ${event.data?.total_pool||'?'}只 | 第二期后台扫全市场...`);
                await load(); // 立即刷新池
              } else if (event.type === 'phase') {
                setScanMsg(`${event.msg}`);
              } else if (event.type === 'done') {
                setScanMsg(`完成: 池内${event.phase1_pool||'?'}只 + 全市场${event.phase2_new||'?'}只新入池`);
                await load();
              } else if (event.type === 'error') {
                setScanMsg('失败: ' + event.msg);
              }
            } catch {}
          }
        }
      }
    } catch(e: any) {
      setScanMsg('失败: ' + (e?.message || '未知'));
    }
    setScanning(false);
  };
  const toggleDetail=async(tsCode:string)=>{if(expanded===tsCode){setExpanded(null);setLockDetail(null);setCompReport(null);return}setExpanded(tsCode);setDetailLoading(true);setCompReport(null);try{const r=await api.get('/alphaflow/lock-detail',{params:{symbol:tsCode}});const d=r.data;if(d&&typeof d==='object'&&!d.error){setLockDetail(d)}else{setLockDetail({error:d?.error||d?.detail||'无数据'})}}catch(e:any){setLockDetail({error:e?.message||'加载失败'})}setDetailLoading(false)};
  const loadComp=async(tsCode:string)=>{setCompLoading(true);setCompReport(null);try{const r=await api.get('/analysis/comprehensive',{params:{symbol:tsCode}});setCompReport(r.data.data)}catch(e:any){setCompReport({error:e?.response?.data?.detail||'Failed'})}setCompLoading(false)};

  const rowH={borderTop:`1px solid ${C.border}`,cursor:'pointer',height:40};
  const stickyTH={position:'sticky' as const,top:0,zIndex:10,background:'#1a2030'};

  return (
    <div style={{maxWidth:1400,margin:'0 auto',padding:isMobile?8:'12px 24px',background:'#0b0e14',minHeight:'100vh',color:'#c9d1d9',fontFamily:'system-ui'}}>
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:10,flexWrap:'wrap',gap:8}}>
        <div>
          <h1 style={{fontSize:isMobile?18:22,margin:0}}>🏆 AlphaFlow</h1>
          <p style={{color:C.textDim,fontSize:12,margin:'4px 0 0 0'}}>锁死·振幅≤15%·XGBoost×天时</p>
        </div>
        <div style={{display:'flex',gap:6,alignItems:'center',flexWrap:'wrap'}}>
          {marketInfo&&<span style={{padding:'3px 10px',borderRadius:12,fontSize:12,fontWeight:600,background:marketInfo.factor>=1.05?`${C.green}10`:marketInfo.factor>=0.85?`${C.amber}10`:`${C.red}10`,border:`1px solid ${marketInfo.factor>=1.05?`${C.green}30`:marketInfo.factor>=0.85?`${C.amber}30`:`${C.red}30`}`,color:marketInfo.factor>=1.05?C.green:marketInfo.factor>=0.85?C.amber:C.red}}>{marketInfo.regime} ×{marketInfo.factor}</span>}
          <StatusBadge type={modelStatus==='ok'?'buy':'caution'} label={modelStatus==='ok'?'XGBoost v2':'模型缺失'}/>
          <button onClick={doScan} disabled={scanning} style={{padding:'8px 20px',border:'none',borderRadius:6,fontSize:12,fontWeight:600,cursor:scanning?'not-allowed':'pointer',background:scanning?C.gray:C.violet,color:C.white}}>{scanning?'⏳':'🔍 扫描'}</button>
        </div>
      </div>
      {scanMsg&&<div style={{marginBottom:8,padding:'4px 10px',borderRadius:4,background:`${C.purple}10`,border:`1px solid ${C.purple}20`,fontSize:12,color:C.purple}}>{scanMsg}</div>}

      <div style={{display:'flex',gap:8,marginBottom:10,flexWrap:'wrap'}}>
        <MetricCard label="池总数" value={status?.total_pool||0} size="md" color={C.blue}/>
        <MetricCard label="活跃" value={status?.tiers?.active?.count||0} size="sm" color={C.green}/>
        <MetricCard label="观察" value={status?.tiers?.observe?.count||0} size="sm" color={C.amber}/>
        <MetricCard label="老兵" value={status?.tiers?.veteran?.count||0} size="sm" color={C.violet}/>
        <MetricCard label="休眠" value={status?.tiers?.dormant?.count||0} size="sm" color={C.textMuted}/>
      </div>

      <SectionHeader title="锁死池" count={status?.total_pool} badge={marketInfo?`${marketInfo.regime}×${marketInfo.factor}`:undefined}/>
      <div style={{display:'flex',gap:4,marginBottom:10,flexWrap:'wrap'}}>
        {['all','active','observe','veteran','dormant'].map(t=><button key={t} onClick={()=>setTierFilter(t)} style={{padding:'3px 12px',borderRadius:12,fontSize:12,fontWeight:600,border:'1px solid',background:tierFilter===t?`${tierColor(t)}15`:'transparent',color:tierFilter===t?tierColor(t):C.textDim,borderColor:tierFilter===t?`${tierColor(t)}30`:C.border,cursor:'pointer'}}>{t==='all'?'全部':tierLabel(t)}{status?.tiers?.[t]&&t!=='all'?` (${status.tiers[t].count})`:''}</button>)}
      </div>

      <table style={{width:'100%',borderCollapse:'collapse',background:C.panelBg,borderRadius:8,overflow:'hidden',marginBottom:16}}>
        <thead><tr style={{...stickyTH,height:36}}>
          {['代码','名称','概率','锁死天数','资金信号','大神仙空'].map((h:any)=><th key={h} style={Th}>{h}</th>)}
        </tr></thead>
        <tbody>
          {loading?Array.from({length:5}).map((_,i)=><SkeletonRow key={i} cols={6}/>):
           sortedPool.length===0?<tr><td colSpan={6}><EmptyState icon="🔒" text="当前市场无锁死蛋"/></td></tr>:
           sortedPool.map((r:any,i:number)=>(
            <><tr key={i} onClick={()=>toggleDetail(r.ts_code)} style={{...rowH,borderLeft:r.is_veteran?`3px solid ${C.violet}`:`3px solid transparent`,background:expanded===r.ts_code?`${C.purple}08`:'transparent'}}
              onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.background=expanded===r.ts_code?`${C.purple}08`:T.card.hoverBg}}
              onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.background=expanded===r.ts_code?`${C.purple}08`:'transparent'}}>
              <td style={Td}><code style={{color:C.code,fontSize:13}}>{r.ts_code}</code></td>
              <td style={{...Td,fontWeight:600}}>{r.name}</td>
              <td style={Td}>
                <ProgressBar percent={Math.min((r.timing_score||r.prob)*100,100)} color={(r.timing_score||r.prob)>=0.6?C.green:(r.timing_score||r.prob)>=0.4?C.amber:C.red} height={5}/>
              </td>
              <td style={Td}>{r.is_veteran?<span style={{fontSize:12,fontWeight:600,color:gColor(r.strategy_group||'')}}>{r.strategy_group||'老兵'} {r.lock_days||0}d</span>:<span style={{fontSize:13,fontWeight:600,color:(r.micro_score||0)>=30?C.green:C.amber}}>🔒{r.micro_score||0}d</span>}</td>
              <td style={Td}>{r.sxqs?<span style={{display:'inline-flex',alignItems:'center',gap:3}}>
                {r.sxqs.signal==='buy'?<span style={{fontSize:13}}>🟢</span>:r.sxqs.signal==='sell'?<span style={{fontSize:13}}>🔴</span>:<span style={{fontSize:13}}>🟡</span>}
                <StatusBadge type={r.sxqs.signal==='buy'?'buy':r.sxqs.signal==='sell'?'sell':'neutral'} label={r.sxqs.signal==='buy'?'买入':r.sxqs.signal==='sell'?'卖出':'观望'}/>
              </span>:<span style={{color:C.textMuted,fontSize:12}}>—</span>}</td>
              <td style={Td}>{r.big_fairy?<span style={{display:'inline-flex',alignItems:'center',gap:3}}>
                <span style={{fontSize:13,fontWeight:700,color:r.big_fairy.score>=3?C.red:r.big_fairy.score>=2?'#f59e0b':r.big_fairy.score>=1?C.amber:C.green}}>{r.big_fairy.score}</span>
                <StatusBadge type={r.big_fairy.signal==='strong_sell'?'sell':r.big_fairy.signal==='sell'?'sell':r.big_fairy.signal==='weak'?'neutral':'buy'} label={r.big_fairy.signal==='strong_sell'?'强空':r.big_fairy.signal==='sell'?'偏空':r.big_fairy.signal==='weak'?'弱':'正常'}/>
              </span>:<span style={{color:C.textMuted,fontSize:12}}>—</span>}</td>
            </tr>
            {expanded===r.ts_code&&(
            <tr key={r.ts_code+'_d'} style={{borderTop:`1px solid ${C.border}`,background:`${C.purple}04`}}>
              <td colSpan={6} style={{padding:0}}>
                {detailLoading?<div style={{padding:20,textAlign:'center',color:C.textDim,fontSize:14}}>加载中...</div>:
                 !lockDetail||lockDetail.error?<div style={{padding:20,textAlign:'center',color:C.textDim,fontSize:14}}>{lockDetail?.error||'暂无数据'}</div>:
                <div style={{padding:'12px 16px'}}>
                  {/* ── 锁死周期数据 ── */}
                  <div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:12}}>
                    {[
                      {label:'当前状态',v:lockDetail.in_lock?'🔒 锁死中':'🚀 主升浪中',c:lockDetail.in_lock?'#10b981':'#10b981'},
                      {label:'锁死天数',v:lockDetail.lock_days?`${lockDetail.lock_days}天`:lockDetail.current_cycle?.days?`${lockDetail.current_cycle?.days}天`:'—',c:'#cbd5e1'},
                      {label:'近30日振幅',v:lockDetail.amplitude_30d?`${lockDetail.amplitude_30d.toFixed(1)}%`:'—',c:'#a78bfa'},
                      {label:'相对大盘',v:lockDetail.relative_strength?`${lockDetail.relative_strength>0?'+':''}${lockDetail.relative_strength}%`:'—',c:lockDetail.relative_strength>0?'#10b981':'#ef4444'},
                    ].map((m,i)=><div key={i} style={{flex:'1 1 90px',minWidth:80,background:'#161b27',border:'1px solid #1e2535',borderRadius:8,padding:10,textAlign:'center'}}>
                      <div style={{fontSize:12,color:'#6e7a8a',marginBottom:4}}>{m.label}</div>
                      <div style={{fontSize:18,fontWeight:700,color:m.c}}>{m.v}</div>
                    </div>)}
                  </div>
                  {/* ── 周期历史 ── */}
                  {lockDetail.lock_cycles?.length>0&&<>
                  <div style={{fontSize:11,color:'#6e7a8a',marginBottom:6}}>锁死周期历史</div>
                  <div style={{display:'flex',gap:4,flexWrap:'wrap',marginBottom:10}}>
                    {lockDetail.lock_cycles.map((c:any,i:number)=><div key={i} style={{flex:'1 1 100px',minWidth:90,background:'#161b27',border:'1px solid #1e2535',borderRadius:6,padding:'6px 8px',textAlign:'center',opacity:i===lockDetail.lock_cycles.length-1?1:0.6}}>
                      <div style={{fontSize:11,color:'#a78bfa'}}>第{c.n||i+1}轮</div>
                      <div style={{fontSize:14,color:'#cbd5e1'}}>{c.days||0}天</div>
                      <div style={{fontSize:11,color:'#6e7a8a'}}>低¥{c.low} 高¥{c.high}</div>
                      <div style={{fontSize:11,color:'#6e7a8a'}}>振幅{c.amp}%</div>
                      {c.breakout_pct>0&&<div style={{fontSize:11,color:c.breakout_pct>15?'#10b981':'#f59e0b'}}>↑{c.breakout_pct}%</div>}
                    </div>)}
                  </div>
                  </>}
                  {/* ── 预判 ── */}
                  <div style={{fontSize:12,color:'#6e7a8a',lineHeight:1.7,marginBottom:8}}>
                    {lockDetail.lock_cycles?.length>1&&<>
                      · 历史锁死<b>{lockDetail.lock_cycles.length}</b>轮，平均锁死<b>{(()=>{const a=lockDetail.lock_cycles.filter((c:any)=>c.days).map((c:any)=>c.days);return a.length?(a.reduce((x:number,y:number)=>x+y,0)/a.length).toFixed(0):'?'})()}</b>天<br/>
                    </>}
                    {lockDetail.in_lock&&lockDetail.lock_days&&lockDetail.lock_cycles?.length>1&&<>
                      · 已锁<b>{lockDetail.lock_days}</b>天，距历史均值解锁约<b>{(()=>{const a=lockDetail.lock_cycles.filter((c:any)=>c.days).map((c:any)=>c.days);const avg=a.length?a.reduce((x:number,y:number)=>x+y,0)/a.length:999;return (avg-lockDetail.lock_days)>0?`${Math.round(avg-lockDetail.lock_days)}天`:'可能随时突破'})()}</b><br/>
                    </>}
                    {!lockDetail.in_lock&&<>
                      · 当前<b>主升浪中</b>——锁死已突破，关注力度能否持续<br/>
                    </>}
                    · 历史周期振幅: {lockDetail.lock_cycles?.filter((c:any)=>c.amp).map((c:any)=>`${c.amp}%`).join(' → ')||'—'}
                  </div>
                </div>}
              </td>
            </tr>)}
            </>)
          )}
        </tbody>
      </table>

      <SectionHeader title="规则说明"/>
      <div style={{padding:8,background:C.panelBg,border:T.panel.border,borderRadius:6,display:'flex',gap:12,flexWrap:'wrap',fontSize:11,color:C.body}}>
        <div><span style={{color:C.text}}>概率</span> = XGBoost×天时(0.55~1.15)</div>
        <div><span style={{color:C.green}}>锁死</span> = 30d振幅≤15%</div>
        <div><span style={{color:C.violet}}>老兵</span> = 多轮锁死周期股</div>
      </div>
    </div>
  );
}
