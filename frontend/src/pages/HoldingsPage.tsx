import { useEffect, useState } from 'react';
import api from '../lib/api';
import CapitalAccountModal from '../components/CapitalAccountModal';
import HoldingStrategyCard from '../components/HoldingStrategyCard';
import ClosedPositionsPanel from '../components/ClosedPositionsPanel';
import ExitSignalsPanel from '../components/ExitSignalsPanel';
import HoldingsImportPanel from '../components/HoldingsImportPanel';
import PortfolioSummary from '../components/PortfolioSummary';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import ProgressBar from '../components/ProgressBar';
import SectionHeader from '../components/SectionHeader';
import EmptyState from '../components/EmptyState';
import SkeletonRow from '../components/SkeletonRow';
import { getExitSignal, getPnlRowStyle } from '../lib/signalColor';
import { T, TH as Th, TD as Td } from '../lib/designTokens';
import { useMediaQuery } from '../lib/useMediaQuery';

const C = T.c;
const CardBg = T.card.bg;
const CardBorder = T.card.border;
const CardHoverBg = T.card.hoverBg;
const PanelBg = T.panel.bg;
const PanelBorder = T.panel.border;

export default function HoldingsPage() {
  const [data,setData] = useState<any[]>([]);
  const [summary,setSummary] = useState<any>({});
  const [alerts,setAlerts] = useState<any[]>([]);
  const [loading,setLoading] = useState(true);
  const [showAdd,setShowAdd] = useState(false);
  const [importText,setImportText] = useState('');
  const [importing,setImporting] = useState(false);
  const [importResult,setImportResult] = useState<any>(null);
  const [analyzing,setAnalyzing] = useState<string|null>(null);
  const [pasteText,setPasteText] = useState<Record<string,string>>({});
  const [showPaste,setShowPaste] = useState<Record<string,boolean>>({});
  const [strategy,setStrategy] = useState<Record<string,string>>({});
  const [keyPoints,setKeyPoints] = useState<Record<string,string>>({});
  const [summarizing,setSummarizing] = useState<Record<string,boolean>>({});
  const [intraday,setIntraday] = useState<Record<string,any>>({});
  const [intraLoading,setIntraLoading] = useState<Record<string,boolean>>({});
  const [prompts,setPrompts] = useState<Record<string,string>>({});
  const [promptLoading,setPromptLoading] = useState<Record<string,boolean>>({});
  const [copiedSym,setCopiedSym] = useState<string|null>(null);
  const [exitSignals,setExitSignals] = useState<any[]>([]);
  const [exitLoading,setExitLoading] = useState(false);
  const [autoStrategy,setAutoStrategy] = useState<any>(null);
  const [autoStratLoading,setAutoStratLoading] = useState(false);
  const [account,setAccount] = useState<any>(null);
  const [closedPositions,setClosedPositions] = useState<any[]>([]);
  const [capitalRecords,setCapitalRecords] = useState<any[]>([]);
  const [capAmount,setCapAmount] = useState('');
  const [capNote,setCapNote] = useState('');
  const [capAdding,setCapAdding] = useState(false);
  const [showCapitalModal,setShowCapitalModal] = useState(false);
  const [closeModal,setCloseModal] = useState<{symbol:string,name:string,quantity:number,cost:number}|null>(null);
  const [closePrice,setClosePrice] = useState('');
  const [closing,setClosing] = useState(false);
  const [chipData,setChipData] = useState<Record<string,any>>({});
  const [chipLoading,setChipLoading] = useState<Record<string,boolean>>({});
  const [sortKey,setSortKey] = useState('market_value');
  const [sortDir,setSortDir] = useState<'asc'|'desc'>('desc');
  const [bigFairy,setBigFairy] = useState<Record<string,any>>({});
  const isMobile = useMediaQuery('(max-width:768px)');

  const pnlCls = (v:number)=>v>=0?C.red:C.green;
  const doClose=async()=>{if(!closeModal)return;const p=parseFloat(closePrice);if(!p||isNaN(p)){alert('请输入有效的清仓价格');return}setClosing(true);try{await api.post(`/holdings/${closeModal.symbol}/close`,{sell_price:p});setCloseModal(null);setClosePrice('');load();loadAccount()}catch(e:any){alert(e?.response?.data?.detail||'清仓失败')}setClosing(false)};
  const loadAccount=async()=>{try{const[ar,cr,cpr]=await Promise.all([api.get('/holdings/account'),api.get('/holdings/capital'),api.get('/holdings/closed')]);setAccount(ar.data.data);setCapitalRecords(cr.data.data?.records||[]);setClosedPositions(cpr.data.data||[])}catch{}};
  const loadAutoStrategy=async()=>{setAutoStratLoading(true);try{const r=await api.get('/holdings/auto-strategy');setAutoStrategy(r.data.data)}catch{setAutoStrategy(null)}setAutoStratLoading(false)};
  const checkExitSignals=async()=>{setExitLoading(true);try{const syms=data.map((h:any)=>h.symbol);if(!syms.length){setExitSignals([]);return}const r=await api.post('/holdings/exit-signals/batch',syms);const results:any[]=[];for(const[sym,sigs]of Object.entries(r.data.data||{})){for(const s of(sigs as any[])){results.push({symbol:sym,...s})}}results.sort((a,b)=>(a.priority==='critical'?-1:1)-(b.priority==='critical'?-1:1));setExitSignals(results)}catch{setExitSignals([])}setExitLoading(false)};
  const load=async()=>{setLoading(true);try{const[hr,sr,ar]=await Promise.all([api.get('/holdings'),api.get('/holdings/summary'),api.get('/holdings/alerts')]);setData(hr.data.data||[]);setSummary(sr.data.data||{});setAlerts(ar.data.data||[]);(hr.data.data||[]).forEach((h:any)=>loadChipData(h.symbol))}catch(e){console.error(e)}loadAccount();loadBigFairy();setLoading(false)};
  const loadBigFairy=async()=>{try{const r=await api.get('/holdings/big-fairy');if(r.data.data)setBigFairy(r.data.data)}catch{}};
  useEffect(()=>{load()},[]);
  const analyzeHolding=async(sym:string)=>{const t=pasteText[sym]||'';if(!t.trim())return alert('请先粘贴分析文本');setAnalyzing(sym);try{const r=await api.post('/holdings/analyze',{symbol:sym,raw_text:t});setStrategy(prev=>({...prev,[sym]:r.data.strategy||'LLM未返回策略'}))}catch(e:any){alert(e?.response?.data?.detail||'分析失败')}setAnalyzing(null)};
  const checkIntraday=async(sym:string)=>{setIntraLoading(prev=>({...prev,[sym]:true}));try{const r=await api.get(`/holdings/intraday/${sym}`);if(r.data.data){setIntraday(prev=>({...prev,[sym]:r.data.data}));await load()}else if(r.data.detail){setIntraday(prev=>({...prev,[sym]:{error:r.data.detail}}))}}catch{setIntraday(prev=>({...prev,[sym]:{error:'请求失败'}}))}setIntraLoading(prev=>({...prev,[sym]:false}))};
  const summarizeStrategy=async(sym:string)=>{const t=strategy[sym];if(!t)return;setSummarizing(prev=>({...prev,[sym]:true}));try{const r=await api.post('/holdings/analyze/summary',{symbol:sym,raw_text:t});setKeyPoints(prev=>({...prev,[sym]:r.data.summary||'提炼失败'}))}catch{alert('提炼失败')}setSummarizing(prev=>({...prev,[sym]:false}))};
  const doImport=async()=>{if(!importText.trim())return alert('请粘贴持仓数据');setImporting(true);setImportResult(null);try{const r=await api.post('/holdings/import',{raw_text:importText});const d=r.data;setImportResult(d);if(d.status==='success'){if(d.holdings)setData(d.holdings);if(d.summary)setSummary(d.summary);if(d.alerts)setAlerts(d.alerts);setImportText('');loadAccount()}}catch(e:any){alert(e?.response?.data?.detail||'导入失败')}setImporting(false)};
  const doCapital=async(amountOverride?:number)=>{const amt=amountOverride??parseFloat(capAmount);if(!amt||isNaN(amt)){alert('请输入有效金额');return}setCapAdding(true);try{await api.post('/holdings/capital',{amount:amt,note:capNote||(amt>0?'入金':'出金')});setCapAmount('');setCapNote('');loadAccount()}catch(e:any){alert(e?.response?.data?.detail||'操作失败')}setCapAdding(false)};
  const updatePrice=async(sym:string)=>{const p=prompt(`更新 ${sym} 现价:`);if(!p)return;try{await api.put(`/holdings/${sym}?current_price=${parseFloat(p)}`);load()}catch{alert('更新失败')}};
  const loadChipData=async(sym:string)=>{if(chipData[sym])return;setChipLoading(prev=>({...prev,[sym]:true}));try{const r=await api.get('/alphaflow/chip-analysis',{params:{symbol:sym}});setChipData(prev=>({...prev,[sym]:r.data.data||r.data}))}catch{}setChipLoading(prev=>({...prev,[sym]:false}))};

  const exitCnts:Record<string,number>={};exitSignals.forEach((s:any)=>{exitCnts[s.symbol]=(exitCnts[s.symbol]||0)+1});
  const sectorConcentration:Record<string,number>={};(autoStrategy?.stock_strategies||[]).forEach((st:any)=>{if(st.sector)sectorConcentration[st.sector]=(sectorConcentration[st.sector]||0)+st.weight_pct});
  const topSectors=Object.entries(sectorConcentration).sort((a,b)=>b[1]-a[1]).slice(0,3);

  const sortedData=[...data].sort((a,b)=>{
    const ac=exitCnts[a.symbol]||0,bc=exitCnts[b.symbol]||0;
    if(ac>=3&&bc<3)return -1;if(bc>=3&&ac<3)return 1;
    const av=a[sortKey]||0,bv=b[sortKey]||0;
    return sortDir==='desc'?bv-av:av-bv;
  });

  const toggleSort=(key:string)=>{if(sortKey===key)setSortDir(d=>d==='desc'?'asc':'desc');else{setSortKey(key);setSortDir('desc')}};
  const sortArrow=(key:string)=>sortKey===key?(sortDir==='desc'?' ▼':' ▲'):'';
  const stickyTH={position:'sticky' as const,top:0,zIndex:10,background:'#1a2030'};

  return (
    <div style={{maxWidth:T.page.maxW,margin:'0 auto'}}>
      <HoldingsImportPanel data={data} summary={summary} account={account} alerts={alerts} showAdd={showAdd} setShowAdd={setShowAdd} importText={importText} setImportText={setImportText} importing={importing} importResult={importResult} doImport={doImport} setImportResult={setImportResult} loadAccount={loadAccount}/>

      <div style={{display:'flex',gap:10,marginBottom:12,flexWrap:'wrap'}}>
        <MetricCard label="净值" value={`¥${((account?.net_account_value||0)/10000).toFixed(1)}万`} size="lg" color={account?.total_return_pct>=0?C.red:C.green} trend={account?.total_return_pct!=null?`${account.total_return_pct>=0?'+':''}${account.total_return_pct}%`:undefined} trendUp={account?.total_return_pct>=0}/>
        <MetricCard label="浮盈" value={`${(summary.total_pnl||0)>=0?'+':''}${(summary.total_pnl||0).toFixed(0)}`} size="md" color={pnlCls(summary.total_pnl||0)}/>
        <MetricCard label="持仓" value={`${summary.count||0} 只`} size="md" color={C.blue} trend={`市值 ¥${((summary.total_value||0)/10000).toFixed(1)}万`}/>
        {topSectors.length>0&&<MetricCard label="板块集中" value={topSectors[0][0]} size="md" color={C.amber} trend={topSectors[0][1].toFixed(0)+'%'+(topSectors[1]?' | '+topSectors[1][0]+' '+topSectors[1][1].toFixed(0)+'%':'')}/>}
        <div style={{display:'flex',gap:4,minWidth:180,flexDirection:'column',justifyContent:'center'}}>
          {Object.entries(exitCnts).length>0?Object.entries(exitCnts).slice(0,3).map(([sym,cnt])=><StatusBadge key={sym} type={getExitSignal(cnt)} label={`${sym}(${cnt})`}/>):<span style={{...T.font.caption}}>无退出信号</span>}
        </div>
      </div>

      {topSectors.length>0&&<div style={{display:'flex',gap:8,flexWrap:'wrap',marginBottom:12,padding:'6px 12px',borderRadius:6,background:CardBg,border:CardBorder}}>
        <span style={{...T.font.label,fontWeight:600}}>板块分布:</span>
        {topSectors.map(([s,w])=><div key={s} style={{display:'flex',alignItems:'center',gap:3,fontSize:10}}><span style={{color:C.text}}>{s}</span><span style={{color:w>40?C.red:w>25?C.amber:C.green,fontWeight:600}}>{w.toFixed(0)}%</span></div>)}
      </div>}

      <SectionHeader title="持仓列表" count={data.length}/>

      <table style={{width:'100%',borderCollapse:'collapse',background:C.panelBg,borderRadius:8,overflow:'hidden'}}>
        <thead><tr style={{...stickyTH,height:36}}>
          {['代码','名称','数量','成本','现价'].map(h=><th key={h} style={Th}>{h}</th>)}
          <th style={{...Th,cursor:'pointer'}} onClick={()=>toggleSort('floating_pnl')}>盈亏{sortArrow('floating_pnl')}</th>
          <th style={{...Th,cursor:'pointer'}} onClick={()=>toggleSort('pnl_pct')}>收益率{sortArrow('pnl_pct')}</th>
          <th style={{...Th,cursor:'pointer'}} onClick={()=>toggleSort('market_value')}>市值{sortArrow('market_value')}</th>
          {!isMobile&&<th style={{...Th,cursor:'pointer'}} onClick={()=>toggleSort('weight_pct')}>权重{sortArrow('weight_pct')}</th>}
          {!isMobile&&<th style={Th}>天数</th>}
          <th style={Th}>推荐</th>
          <th style={Th}>信号</th>
          <th style={Th}>操作</th>
        </tr></thead>
        <tbody>
          {loading?Array.from({length:5}).map((_,i)=><SkeletonRow key={i} cols={isMobile?9:11}/>):
           sortedData.length===0?<tr><td colSpan={isMobile?11:13}><EmptyState icon="📋" text="暂无持仓"/></td></tr>:
           sortedData.map((r:any,i:number)=>{
            const rp=getPnlRowStyle(r.pnl_pct);
            const el=getExitSignal(exitCnts[r.symbol]||0);
            const miniBarW=Math.min(100,Math.abs(r.pnl_pct||0)*3);
            return (<><tr key={i} style={{borderTop:`1px solid ${C.border}`,height:42,background:r.pending_close?`${C.amber}08`:rp.background||'transparent',borderLeft:rp.borderLeft||`3px solid transparent`}}
              onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.background=CardHoverBg}}
              onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.background=rp.background||'transparent'}}>
              <td style={Td}><code style={{color:r.pending_close?C.amber:C.code,fontSize:11}}>{r.symbol}</code>{r.pending_close&&<span style={{marginLeft:3,fontSize:9,padding:'1px 3px',borderRadius:2,background:`${C.amber}15`,color:C.amber}}>待清</span>}</td>
              <td style={{...Td,fontWeight:600}}>{r.name}</td>
              <td style={Td}>{r.quantity}</td>
              <td style={Td}>{r.cost_price?.toFixed(2)}</td>
              <td style={{...Td,cursor:'pointer'}} onClick={()=>updatePrice(r.symbol)}>{r.current_price?.toFixed(2)}</td>
              <td style={{...Td,color:pnlCls(r.floating_pnl),fontWeight:600}}>
                {r.floating_pnl>=0?'+':''}{r.floating_pnl?.toFixed(0)}
                <div style={{width:30,height:3,borderRadius:2,marginTop:2,background:C.border,overflow:'hidden'}}>
                  <div style={{height:'100%',width:`${miniBarW}%`,borderRadius:2,background:(r.pnl_pct||0)>=0?C.red:C.green}}/>
                </div>
              </td>
              <td style={{...Td,color:pnlCls(r.pnl_pct),fontWeight:600}}>{r.pnl_pct>=0?'+':''}{r.pnl_pct?.toFixed(1)}%</td>
              <td style={Td}>¥{(r.market_value/10000).toFixed(1)}万</td>
              {!isMobile&&<td style={Td}>{r.weight_pct?.toFixed(1)}%</td>}
              {!isMobile&&<td style={Td}>{r.holding_days}天</td>}
              <td style={Td}>
                {r.rec_index != null ? (
                  <span style={{fontWeight:700, fontSize:13,
                    color: r.rec_index>=80 ? C.red : r.rec_index>=60 ? C.orange :
                           r.rec_index>=40 ? C.amber : C.green}}>
                    {r.rec_index}
                  </span>
                ) : <span style={{color:C.textMuted, fontSize:11}}>—</span>}
              </td>
              <td style={{...Td, fontSize: 10}}>
                {r.news_signal && (
                  <span style={{fontSize:8, color: r.news_signal.includes('利空') ? C.red : C.green}}>
                    {'📰'}{r.news_signal}
                  </span>
                )}
                {bigFairy[r.symbol] && (
                  <span style={{fontSize:9,fontWeight:700,marginLeft:r.news_signal?4:0,
                    color:bigFairy[r.symbol].score>=3?C.red:bigFairy[r.symbol].score>=2?'#f59e0b':bigFairy[r.symbol].score>=1?C.amber:C.green}}>
                    🧙{bigFairy[r.symbol].score} {bigFairy[r.symbol].signal==='strong_sell'?'强空':bigFairy[r.symbol].signal==='sell'?'偏空':bigFairy[r.symbol].signal==='weak'?'弱':'✓'}
                  </span>
                )}
              </td>
              <td style={{padding:'4px 4px'}}>
                <button onClick={()=>checkIntraday(r.symbol)} style={{padding:'2px 5px',borderRadius:3,border:`1px solid ${C.amber}`,background:`${C.amber}10`,color:C.amber,cursor:'pointer',fontSize:9,fontWeight:600,marginRight:2}}>📈</button>
                <button onClick={async()=>{const sym=r.symbol;const o=!showPaste[sym];setShowPaste(prev=>({...prev,[sym]:o}));if(o&&!prompts[sym]){setPromptLoading(prev=>({...prev,[sym]:true}));try{const res=await api.post('/llm/generate-prompt',{symbols:[sym]});const d=res.data.data||[];if(d.length>0)setPrompts(prev=>({...prev,[sym]:d[0].prompt}))}catch{}setPromptLoading(prev=>({...prev,[sym]:false}))}}} style={{padding:'2px 6px',borderRadius:3,border:`1px solid ${C.violet}`,background:`${C.violet}10`,color:C.purple,cursor:'pointer',fontSize:9,fontWeight:600,marginRight:2}}>🔍</button>
                <button onClick={()=>setCloseModal({symbol:r.symbol,name:r.name,quantity:r.quantity,cost:r.cost_price})} style={{padding:'2px 5px',borderRadius:3,border:r.pending_close?`2px solid ${C.red}`:`1px solid ${C.red}`,background:r.pending_close?`${C.red}15`:`${C.red}05`,color:C.red,cursor:'pointer',fontSize:9,fontWeight:600}}>清仓</button>
              </td>
            </tr>
            {chipData[r.symbol]&&!chipData[r.symbol].error&&<tr key={r.symbol+'-chip'} style={{borderTop:`1px solid rgba(30,37,53,0.5)`,background:'rgba(16,22,37,0.4)'}}>
              <td colSpan={isMobile?11:13} style={{padding:'4px 12px'}}>
                <div style={{display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}>
                  <span style={{...T.font.caption}}>筹码:</span>
                  {chipData[r.symbol].ar_lock!=null&&<ProgressBar label="锁死区" percent={(chipData[r.symbol].ar_lock||0)*100} color={chipData[r.symbol].ar_lock>=0.6?C.green:chipData[r.symbol].ar_lock>=0.3?C.amber:C.red} height={3}/>}
                  {chipData[r.symbol].trend&&<span style={{fontSize:9,color:chipData[r.symbol].trend==='accelerating'?C.green:chipData[r.symbol].trend==='declining'?C.red:C.amber}}>{chipData[r.symbol].trend==='accelerating'?'↑加速':chipData[r.symbol].trend==='declining'?'↓衰减':'→稳定'}</span>}
                  {exitCnts[r.symbol]>0&&<StatusBadge type={el} label={`${exitCnts[r.symbol]}信号`}/>}
                </div>
              </td>
            </tr>}
            {chipLoading[r.symbol]&&<tr><td colSpan={isMobile?11:13} style={{padding:'3px 12px',fontSize:9,color:C.textDim}}>筹码加载中...</td></tr>}
            {showPaste[r.symbol]&&<tr key={r.symbol+'-paste'} style={{borderTop:`1px solid ${C.border}`,background:`${C.purple}04`}}>
              <td colSpan={isMobile?11:13} style={{padding:'8px 12px'}}>
                {strategy[r.symbol]?<div>
                  <div style={{whiteSpace:'pre-wrap',fontSize:11,lineHeight:1.6,color:C.text,padding:8,background:C.pageBg,borderRadius:6,maxHeight:300,overflow:'auto'}}>{strategy[r.symbol]}</div>
                  <button onClick={()=>summarizeStrategy(r.symbol)} disabled={summarizing[r.symbol]} style={{marginTop:6,padding:'4px 10px',borderRadius:4,border:`1px solid ${C.amber}`,background:summarizing[r.symbol]?C.gray:`${C.amber}10`,color:summarizing[r.symbol]?C.body:C.amber,cursor:summarizing[r.symbol]?'not-allowed':'pointer',fontSize:10}}>{summarizing[r.symbol]?'提炼中...':'📌 关键点位'}</button>
                </div>:<div>
                  <div style={{fontSize:11,fontWeight:600,color:C.text,marginBottom:4}}>📋 分析提示词</div>
                  {promptLoading[r.symbol]?<div style={{color:C.textDim,fontSize:11}}>生成中...</div>:prompts[r.symbol]?<div>
                    <textarea readOnly value={prompts[r.symbol]} style={{width:'100%',minHeight:100,padding:8,borderRadius:4,background:C.pageBg,border:`1px solid ${C.border}`,color:C.text,fontSize:10,fontFamily:'monospace',resize:'vertical'}}/>
                    <button onClick={async()=>{await navigator.clipboard.writeText(prompts[r.symbol]);setCopiedSym(r.symbol);setTimeout(()=>setCopiedSym(null),2000)}} style={{marginTop:4,padding:'3px 8px',borderRadius:3,border:`1px solid ${C.green}`,background:copiedSym===r.symbol?`${C.green}20`:`${C.green}08`,color:copiedSym===r.symbol?C.green:'#34d399',cursor:'pointer',fontSize:10}}>{copiedSym===r.symbol?'✓ 已复制':'📋 复制到DeepSeek'}</button>
                  </div>:null}
                  <div style={{fontSize:11,fontWeight:600,color:C.text,marginTop:8,marginBottom:4}}>📥 粘贴分析回复</div>
                  <textarea value={pasteText[r.symbol]||''} onChange={e=>setPasteText(prev=>({...prev,[r.symbol]:e.target.value}))} placeholder="粘贴 DeepSeek 回复..." style={{width:'100%',minHeight:70,padding:8,borderRadius:4,background:C.pageBg,border:`1px solid ${C.border}`,color:C.text,fontSize:10,fontFamily:'monospace',resize:'vertical'}}/>
                  <button onClick={()=>analyzeHolding(r.symbol)} disabled={analyzing===r.symbol} style={{marginTop:6,padding:'5px 12px',borderRadius:4,border:'none',background:analyzing===r.symbol?C.gray:C.violet,color:C.white,cursor:analyzing===r.symbol?'not-allowed':'pointer',fontSize:11,fontWeight:600}}>{analyzing===r.symbol?'分析中...':'获取操作策略'}</button>
                </div>}
              </td>
            </tr>}
            {keyPoints[r.symbol]&&<tr style={{borderTop:`1px solid ${C.amber}15`,background:`${C.amber}04`}}><td colSpan={isMobile?11:13} style={{padding:'6px 12px',whiteSpace:'pre-wrap',fontSize:11,lineHeight:1.7,color:'#fbbf24'}}>{keyPoints[r.symbol]}</td></tr>}
            {intraday[r.symbol]&&!intraday[r.symbol].error&&<tr style={{borderTop:`1px solid ${C.amber}15`,background:`${C.amber}04`}}><td colSpan={isMobile?11:13} style={{padding:'6px 12px',fontSize:11,lineHeight:1.6}}><span style={{color:C.amber,fontWeight:600}}>日内:</span> +{intraday[r.symbol].max_gain_pct}% 回撤{intraday[r.symbol].retrace_ratio} {intraday[r.symbol].volume_profile} → {intraday[r.symbol].verdict}</td></tr>}
          </>)})}
        </tbody>
      </table>

      <HoldingStrategyCard autoStrategy={autoStrategy} autoStratLoading={autoStratLoading} dataLength={data.length} onRefresh={loadAutoStrategy}/>

      <div style={{marginTop:16,padding:10,background:PanelBg,border:PanelBorder,borderRadius:8}}>
        <SectionHeader title="💰 资本账户" actions={<button onClick={()=>setShowCapitalModal(true)} style={{padding:'3px 10px',borderRadius:4,border:`1px solid ${C.blue}`,background:`${C.blue}08`,color:C.blue,cursor:'pointer',fontSize:11,fontWeight:600}}>⚙ 管理</button>}/>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:8}}>
          {account?<div style={{fontSize:11,color:C.body,display:'flex',gap:12,flexWrap:'wrap'}}>
            <span>总资金: ¥{(account.net_capital||0).toFixed(0)}</span><span style={{color:C.textDim}}>|</span>
            <span>可用: ¥{(account.cash_remaining||0).toFixed(0)}</span><span style={{color:C.textDim}}>|</span>
            <span style={{color:account.total_return_pct>=0?C.red:C.green,fontWeight:600}}>总收益: {account.total_return_pct>=0?'+':''}{account.total_return_pct}%</span>
          </div>:<span style={{fontSize:11,color:C.amber}}>⚠ 请先设置初始本金</span>}
        </div>
        {capitalRecords.length>0&&<div style={{display:'flex',gap:4,flexWrap:'wrap',marginTop:6}}>
          {capitalRecords.map((r:any,i:number)=><span key={i} style={{fontSize:9,padding:'1px 6px',borderRadius:3,background:r.amount>0?`${C.green}08`:`${C.red}08`,border:`1px solid ${r.amount>0?`${C.green}15`:`${C.red}15`}`,color:r.amount>0?C.green:C.red}}>{r.amount>0?'↓入金':'↑出金'} ¥{Math.abs(r.amount).toFixed(0)} {r.note||r.date?.slice(0,10)}</span>)}
        </div>}
      </div>

      <CapitalAccountModal visible={showCapitalModal} onClose={()=>setShowCapitalModal(false)} account={account} capitalRecords={capitalRecords} capAmount={capAmount} setCapAmount={setCapAmount} capNote={capNote} setCapNote={setCapNote} capAdding={capAdding} doCapital={doCapital}/>

      {closeModal&&<div style={{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.6)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:1001}} onClick={()=>{setCloseModal(null);setClosePrice('')}}>
        <div style={{background:PanelBg,border:PanelBorder,borderRadius:10,padding:24,width:400,maxWidth:'90vw'}} onClick={e=>e.stopPropagation()}>
          <div style={{fontSize:15,fontWeight:700,color:C.text,marginBottom:4}}>确认清仓</div>
          <div style={{...T.font.caption,marginBottom:16}}>输入实际卖出价格</div>
          <div style={{marginBottom:12,padding:10,background:C.pageBg,borderRadius:6,border:`1px solid ${C.border}`}}>
            <div style={{display:'flex',justifyContent:'space-between'}}><span style={{fontSize:12,fontWeight:600,color:C.amber}}>{closeModal.symbol}</span><span style={{fontSize:12,color:C.text}}>{closeModal.name}</span></div>
            <div style={{display:'flex',gap:16,fontSize:11,color:C.body,marginTop:4}}><span>数量:{closeModal.quantity}股</span><span>成本:¥{closeModal.cost?.toFixed(2)}</span></div>
          </div>
          <div style={{marginBottom:12}}>
            <div style={{...T.font.label,marginBottom:4}}>卖出价格（元/股）</div>
            <input value={closePrice} onChange={e=>setClosePrice(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')doClose()}} autoFocus placeholder="请输入清仓价格" style={{width:'100%',padding:'8px',borderRadius:6,border:`1px solid ${C.red}`,background:C.pageBg,color:C.text,fontSize:14,fontWeight:600}}/>
            {closePrice&&!isNaN(parseFloat(closePrice))&&<div style={{marginTop:6,fontSize:11,color:C.body}}>预估盈亏: <span style={{fontWeight:600,color:parseFloat(closePrice)>=closeModal.cost?C.red:C.green}}>{parseFloat(closePrice)>=closeModal.cost?'+':''}¥{((parseFloat(closePrice)-closeModal.cost)*closeModal.quantity).toFixed(0)}</span> ({((parseFloat(closePrice)-closeModal.cost)/closeModal.cost*100).toFixed(1)}%)</div>}
          </div>
          <div style={{display:'flex',gap:8,justifyContent:'flex-end'}}>
            <button onClick={()=>{setCloseModal(null);setClosePrice('')}} style={{padding:'6px 16px',borderRadius:6,border:`1px solid ${C.gray}`,background:'transparent',color:C.body,cursor:'pointer',fontSize:12}}>取消</button>
            <button onClick={doClose} disabled={closing||!closePrice} style={{padding:'6px 20px',borderRadius:6,border:'none',background:(closing||!closePrice)?C.gray:C.red,color:C.white,cursor:(closing||!closePrice)?'not-allowed':'pointer',fontSize:12,fontWeight:600}}>{closing?'处理中...':'确认清仓'}</button>
          </div>
        </div>
      </div>}

      <ClosedPositionsPanel closedPositions={closedPositions} account={account}/>
      <ExitSignalsPanel exitSignals={exitSignals} exitLoading={exitLoading} dataLength={data.length} onCheck={checkExitSignals}/>
    </div>
  );
}
