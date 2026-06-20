import { useEffect, useState, useCallback, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '../lib/api';
import { useDeepAnalysis } from '../lib/useDeepAnalysis';
import DeepAnalysisModal from '../components/DeepAnalysisModal';
import FeedbackModal from '../components/FeedbackModal';
import PromptModal from '../components/PromptModal';
import MarketGatingCard from '../components/MarketGatingCard';
import HotSectorPanel from '../components/HotSectorPanel';
import CuratedRankingView from '../components/CuratedRankingView';
import MetricCard from '../components/MetricCard';
import StatusBadge from '../components/StatusBadge';
import ProgressBar from '../components/ProgressBar';
import SectionHeader from '../components/SectionHeader';
import EmptyState from '../components/EmptyState';
import SkeletonRow from '../components/SkeletonRow';
import RegimeDashboard from '../components/RegimeDashboard';
import { getActionSignal, getSignalStyle } from '../lib/signalColor';
import { T, TH as Th, TD as Td } from '../lib/designTokens';
import { useMediaQuery } from '../lib/useMediaQuery';

const C = T.c;
const ARCH_CN: Record<string,string> = {
  '主板_large_bluechip':'主板大盘蓝筹','主板_small_speculative':'主板小盘题材','主板_growth_tech':'主板科技成长','主板_value_defensive':'主板价值防御','主板_cyclical_resource':'主板周期资源',
  '创业板_large_bluechip':'创业板蓝筹','创业板_small_speculative':'创业板小盘','创业板_growth_tech':'创业板科技','创业板_value_defensive':'创业板防御','创业板_cyclical_resource':'创业板周期',
  large_bluechip:'大盘蓝筹',small_speculative:'小盘题材',growth_tech:'科技成长',value_defensive:'价值防御',cyclical_resource:'周期资源',
};

export default function ResultPage() {
  const navigate = useNavigate();
  const [data,setData]=useState<any[]>([]);
  const [loading,setLoading]=useState(true);
  const [scanDate,setScanDate]=useState('');
  const [fusionData,setFusionData]=useState<any[]>([]);
  const [ambushData,setAmbushData]=useState<any[]>([]);
  const [activeTab,setActiveTab]=useState<'final'|'fusion'|'ambush'>('final');
  const deep=useDeepAnalysis();
  const [fbModal,setFbModal]=useState<{symbol:string;name:string;score:number}|null>(null);
  const [promptSelected,setPromptSelected]=useState<Set<string>>(new Set());
  const [promptOpen,setPromptOpen]=useState(false);
  const [batchScores,setBatchScores]=useState<Record<string,any>>({});
  const [scoringBatch,setScoringBatch]=useState(false);
  // ★ v7.0.33: 防止 doBatchScore 重复触发 (修复"评分失败"alert 每次刷新都跳的 bug)
  const hasTriedBatchRef = useRef<boolean>(false);
  const [gateInfo,setGateInfo]=useState<any>(null);
  const [marketWarning,setMarketWarning]=useState<any>(null);
  const [timingCap,setTimingCap]=useState<any>(null);
  const [watchlist,setWatchlist]=useState<any[]>([]);
  const [marketFilter,setMarketFilter]=useState('全部');
  const [history,setHistory]=useState<any[]>([]);
  const [loadError,setLoadError]=useState(false);
  const [marketThemes,setMarketThemes]=useState<any[]>([]);
  const [gateFilteredCount,setGateFilteredCount]=useState(0);
  const [searchParams]=useSearchParams();
  const curatedSymbols=searchParams.get('symbols')||'';
  const curatedDate=searchParams.get('date')||'';
  const isCurated=!!curatedSymbols;
  const [hasFeedback,setHasFeedback]=useState(false);
  const [showWatchlist,setShowWatchlist]=useState(false);
  const [sortKey,setSortKey]=useState('rec_index');
  const [sortDir,setSortDir]=useState<'asc'|'desc'>('desc');
  const [highlightIdx,setHighlightIdx]=useState(-1);
  const isMobile=useMediaQuery('(max-width:768px)');
  const [curatedExpanded,setCuratedExpanded]=useState<Set<number>>(new Set([0]));

  const togglePrompt=(sym:string)=>{setPromptSelected(prev=>{const n=new Set(prev);n.has(sym)?n.delete(sym):n.size<10&&n.add(sym);return n})};
  const loadHistory=async()=>{try{const r=await api.get('/result/history',{params:{limit:3}});setHistory(r.data.data||[])}catch{}};
  const load=async()=>{setLoading(true);const params:any={limit:50};if(curatedSymbols)params.symbols=curatedSymbols;if(curatedDate)params.date=curatedDate;
    try{const r=await api.get('/result/final',{params});const d=r.data.data||[];setData(d);setLoadError(false);setScanDate(r.data.scan_date||'');setHasFeedback(r.data.has_feedback||false);setGateInfo(r.data.gate||null);setGateFilteredCount(r.data.gate_filtered??0);setMarketWarning(r.data.market_warning||null);setTimingCap(r.data.timing_cap||null);setWatchlist(r.data.watchlist||[]);setMarketThemes(r.data.market_themes||[])}catch(e:any){console.error(e);setLoadError(true);if(isCurated)setData([])}setLoading(false);
    if(!curatedSymbols){try{const f=await api.get('/result/fusion',{params:{limit:50}});setFusionData(f.data.data||[])}catch{}try{const a=await api.get('/ambush-signals',{params:{limit:20}});setAmbushData(a.data.data||[])}catch{}loadHistory()}};
  // v5.5: 页面加载时滚动到顶部
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
  }, []);

  useEffect(()=>{load()},[curatedSymbols,curatedDate]);
  // ★ v7.0.33: 用 hasTriedBatchRef 防止每次 [data] 变化都重跑 batch-score
  //   之前 useEffect dep [data, isCurated] 会在 data 变化时重跑, 失败时 alert 一直跳
  useEffect(() => {
    if (!isCurated || data.length === 0) return;
    if (hasTriedBatchRef.current) return;  // 每次挂载只跑一次
    if (Object.keys(batchScores).length > 0) return;  // 已有结果不重跑
    hasTriedBatchRef.current = true;
    doBatchScore();
  }, [data, isCurated, batchScores]);

  // Keyboard nav
  const handleKeyDown=useCallback((e:KeyboardEvent)=>{
    if(activeTab!=='final'||isCurated)return;
    const rows=data.length;
    if(e.key==='ArrowDown'){e.preventDefault();setHighlightIdx(prev=>Math.min(prev+1,rows-1))}
    else if(e.key==='ArrowUp'){e.preventDefault();setHighlightIdx(prev=>Math.max(prev-1,0))}
    else if(e.key==='Escape'){setHighlightIdx(-1)}
  },[data.length,activeTab,isCurated]);
  useEffect(()=>{window.addEventListener('keydown',handleKeyDown);return()=>window.removeEventListener('keydown',handleKeyDown)},[handleKeyDown]);

  const getScore=(r:any)=>r.composite_score||r.fusion_score||r.tg_score||0;
  const getName=(r:any)=>r.name||r.symbol||'';
  // v7.0.34: 表格总列数 (colSpan 需要, 区分 final/fusion vs ambush)
  const totalCols = activeTab === 'ambush' ? 12 : 21;
  // v6.0.6: 评分前先查哪些股有反哺文本, 弹具体说明而非笼统"评分失败"
  // ★ v7.0.33: 修复"评分失败"alert 一直跳的 bug
  //   1. 改用 console.error 静默记录 (失败不再弹 alert, 用户体验更好)
  //   2. 反哺文本缺失时, 不再弹 alert, 改为在 scoringBatch 状态显示 (已经有 ⏳ 标识)
  //   3. 错误时记录到 hasTriedBatchRef, 防止 useEffect 重跑
  const doBatchScore=async()=>{
    setScoringBatch(true);
    try {
      const allSymbols=data.map((r:any)=>r.symbol);
      if(!allSymbols.length){setScoringBatch(false);return}

      const fdRes=await api.get('/feedback/check-batch',{params:{symbols:allSymbols.join(',')}});
      const fdMap:Record<string,any>=fdRes.data?.data||{};
      const hasText=new Set(Object.keys(fdMap).filter((s:string)=>fdMap[s]?.raw_response&&fdMap[s].raw_response.length>50));
      const missing=allSymbols.filter((s:string)=>!hasText.has(s));
      const t:Record<string,string>={};
      allSymbols.forEach((s:string)=>{if(hasText.has(s))t[s]=''});

      if(!Object.keys(t).length){
        // ★ 不再 alert, 静默 (LLM 反哺可能未生成, 让用户去深度分析即可)
        console.warn(`[batch-score] ${missing.length} 只股没有反哺文本, 跳过评分: ${missing.slice(0,5).join(', ')}`);
        return;
      }

      const res=await api.post('/feedback/batch-score',{symbol_texts:t});
      if(res.data.scores){
        setBatchScores(res.data.scores);
        // ★ 把"已评分"提示改成 console.log, 不弹 alert
        console.log(`[batch-score] 成功评分 ${Object.keys(t).length} 只, 跳过 ${missing.length} 只 (无反哺)`);
      }
    } catch(e: any) {
      // ★ 关键: 失败时不再 alert, 只 console.error
      //   原因: LLM 调用慢/超时/限流很常见, 不应该打扰用户
      console.error('[batch-score] 评分失败:', e?.response?.data?.detail || e?.message || '未知错误');
    } finally {
      setScoringBatch(false);
    }
  };
  const toggleSort=(key:string)=>{if(sortKey===key)setSortDir(d=>d==='desc'?'asc':'desc');else{setSortKey(key);setSortDir('desc')}};
  const sortArrow=(key:string)=>sortKey===key?(sortDir==='desc'?' ▼':' ▲'):'';

  if(isCurated)return<CuratedRankingView data={data} batchScores={batchScores} scanDate={scanDate} curatedDate={curatedDate} expandedCards={curatedExpanded} setExpandedCards={setCuratedExpanded} scoringBatch={scoringBatch} loadError={loadError}/>;

  const stockRows=activeTab==='fusion'?fusionData:activeTab==='ambush'?ambushData:data;
  const sortedRows=hasFeedback&&activeTab==='final'?[...stockRows].sort((a,b)=>(b.llm_score||b.composite_score||0)-(a.llm_score||a.composite_score||0))
    :[...stockRows].sort((a,b)=>{const av=a[sortKey]||0,bv=b[sortKey]||0;return sortDir==='desc'?bv-av:av-bv});
  const weeklyCount=data.filter((r:any)=>r.resonance_type==='weekly_resonance').length;
  const gateRegime=gateInfo?.market_regime||'—';
  const gateRisk=gateInfo?.market_risk||'—';
  const marketM5d = data[0]?.market_5d ?? 0;
  const stickyTH={position:'sticky' as const,top:0,zIndex:10,background:'#1a2030'};

  return (
    <div style={{maxWidth:T.page.maxW,margin:'0 auto',padding:isMobile?'8px 12px':'16px 24px',background:C.pageBg,minHeight:'100vh',color:C.text,fontFamily:T.page.font}}>
      <div style={{display:'flex',gap:10,marginBottom:12,flexWrap:'wrap'}}>
        <MetricCard label="推荐数" value={`${data.length} 只`} size="lg" color={C.blue}/>
        <MetricCard label="周线共振" value={weeklyCount} size="md" color={C.violet}/>
        <MetricCard label="已过滤" value={gateFilteredCount||data.length} size="md" color={C.amber}/>
        <MetricCard label="市场" value={marketM5d!==0?`${marketM5d>0?'+':''}${marketM5d}%`:gateRegime} trend={marketM5d!==0?(marketM5d>0?'上涨':'下跌'):(gateRisk==='high'?'高危':gateRisk==='elevated'?'偏高':'正常')} trendUp={marketM5d!==0?marketM5d>0:(gateRisk!=='high'&&gateRisk!=='elevated')} size="md" color={gateRisk==='high'?C.red:gateRisk==='elevated'?C.amber:C.green}/>
      </div>

      {!isCurated&&<MarketGatingCard gateInfo={gateInfo}/>}

      {/* ★ v4.9: 三层 Regime Dashboard */}
      {!isCurated&&<RegimeDashboard/>}

      {/* ★ Phase 31: 自适应阈值信息 */}
      {gateInfo?.adaptive?.status==='adaptive'&&(
        <div style={{fontSize:10,color:C.textDim,marginBottom:8,display:'flex',gap:8,alignItems:'center'}}>
          ⚙ 自适应阈值: 推荐≥{gateInfo.adaptive.min_score}分 | 强买≥{gateInfo.adaptive.strong_buy}分
          <span style={{color:C.textMuted}}>(来自{gateInfo.adaptive.total_verified}条真实验证, {gateInfo.adaptive.buckets}个分数段)</span>
        </div>
      )}

      {(typeof marketWarning==='object'&&(marketWarning as any)?.action==='empty')?(
        <div style={{marginBottom:12,padding:'16px',borderRadius:8,background:`${C.red}12`,border:`2px solid ${C.red}40`,textAlign:'center'}}>
          <div style={{fontSize:32,marginBottom:4}}>🛑</div><div style={{fontSize:18,fontWeight:800,color:C.red}}>建议完全空仓</div>
          <div style={{fontSize:12,color:'#fca5a5',marginTop:4}}>{(marketWarning as any).message}</div>
        </div>
      ):typeof marketWarning==='string'&&marketWarning&&data.length===0?(
        <div style={{marginBottom:12,padding:'12px 16px',borderRadius:8,background:`${C.red}08`,border:`1px solid ${C.red}25`}}>
          <div style={{fontSize:14,fontWeight:700,color:C.red}}>⛔ {marketWarning}</div>
          {timingCap&&<div style={{fontSize:10,color:C.body,marginTop:4}}>严选: 综合分≥{timingCap.min_score} | 胜率≥{(timingCap.min_prob*100).toFixed(0)}% | 最多{timingCap.max_stocks}只</div>}
        </div>
      ):null}

      {watchlist.length>0&&data.length===0&&<div style={{marginBottom:12}}>
        <button onClick={()=>setShowWatchlist(!showWatchlist)} style={{padding:'4px 12px',borderRadius:4,border:`1px solid ${C.gray}`,background:'transparent',color:C.body,cursor:'pointer',fontSize:11}}>📋 {showWatchlist?'收起':'展开'} 观察列表 ({watchlist.length}只)</button>
        {showWatchlist&&<div style={{display:'flex',flexWrap:'wrap',gap:4,marginTop:6}}>{watchlist.map((w:any)=><span key={w.symbol} style={{fontSize:10,padding:'2px 8px',borderRadius:4,background:`${C.body}08`,color:C.body,fontFamily:'monospace'}}>{w.symbol} {w.name} sc={w.composite_score?.toFixed(0)}</span>)}</div>}
      </div>}

      <SectionHeader title="推荐列表" count={data.length} badge={hasFeedback?'LLM已反哺':undefined}/>
      <div style={{display:'flex',gap:6,marginBottom:10,flexWrap:'wrap',alignItems:'center'}}>
        {!isCurated&&history.map((h:any)=><button key={h.date} onClick={()=>navigate(`/result?symbols=${h.symbols.join(',')}&date=${h.date}`)} style={{padding:'4px 12px',borderRadius:4,border:`1px solid ${C.violet}`,background:`${C.violet}08`,color:C.purple,cursor:'pointer',fontSize:11,fontWeight:600}}>{h.label}({h.count})</button>)}
        {!isCurated&&<HotSectorPanel themes={marketThemes} setMarketFilter={setMarketFilter}/>}
        {!isCurated&&(['final','fusion','ambush']as const).map(t=><button key={t} onClick={()=>setActiveTab(t)} style={{padding:'4px 14px',borderRadius:14,fontSize:11,fontWeight:600,border:activeTab===t?`2px solid ${C.blue}`:`1px solid ${C.border}`,background:activeTab===t?`${C.blue}10`:'transparent',color:activeTab===t?C.blue:C.textDim,cursor:'pointer'}}>{t==='final'?`推荐 (${data.length})`:t==='fusion'?`融合 (${fusionData.length})`:`潜伏 (${ambushData.length})`}</button>)}
        <span style={{flex:1}}/>
        {!isCurated&&['全部','主板','中小板','创业板'].map(m=><button key={m} onClick={()=>setMarketFilter(m)} style={{padding:'2px 10px',borderRadius:12,fontSize:10,fontWeight:500,border:'1px solid',background:marketFilter===m?`${C.blue}08`:'transparent',color:marketFilter===m?C.blue:C.textDim,borderColor:marketFilter===m?`${C.blue}30`:C.border,cursor:'pointer'}}>{m}</button>)}
        {!isCurated&&<>
          <button onClick={deep.start} disabled={deep.selected.size===0} style={{padding:'5px 14px',borderRadius:4,border:deep.selected.size>0?`1px solid ${C.violet}`:`1px solid ${C.border}`,background:deep.selected.size>0?`${C.violet}10`:'transparent',color:deep.selected.size>0?C.purple:C.textMuted,cursor:deep.selected.size>0?'pointer':'not-allowed',fontWeight:600,fontSize:12}}>🔬 深度 {deep.selected.size>0?`(${deep.selected.size})`:''}</button>
          <button onClick={()=>setPromptOpen(true)} disabled={promptSelected.size===0} style={{padding:'5px 14px',borderRadius:4,border:promptSelected.size>0?`1px solid ${C.green}`:`1px solid ${C.border}`,background:promptSelected.size>0?`${C.green}08`:'transparent',color:promptSelected.size>0?C.green:C.textMuted,cursor:promptSelected.size>0?'pointer':'not-allowed',fontWeight:600,fontSize:12}}>📋 提示词 {promptSelected.size>0?`(${promptSelected.size})`:''}</button>
        </>}
      </div>

      <table style={{width:'100%',borderCollapse:'collapse',background:C.panelBg,borderRadius:8,overflow:'hidden'}}>
        <thead><tr style={{...stickyTH,height:36}}>
          <th style={{...Th,width:40}}></th>
          <th style={{...Th,width:80,textAlign:'center'}}>操作建议</th>
          <th style={{...Th,width:60,textAlign:'center',cursor:'pointer'}} onClick={()=>toggleSort('rec_index')}>推荐指数{sortArrow('rec_index')}</th>
          <th style={{...Th,width:70,textAlign:'center'}}>位置</th>
          {activeTab==='ambush'?['代码','名称','涨停日期','涨停涨幅','最大回撤','缩量比','启动量比','综合评分'].map(h=><th key={h} style={Th}>{h}</th>)
          :<> <th style={Th}>代码</th><th style={Th}>名称</th>
            <th style={{...Th,cursor:'pointer'}} onClick={()=>toggleSort('composite_score')}>评分{sortArrow('composite_score')}</th>
            {hasFeedback&&<th style={Th}>LLM</th>}
            <th style={{...Th,width:72}}>策略</th>
            <th style={{...Th,width:70}}>板块温度</th>
            {!isMobile&&<th style={Th}>技术面</th>}
            {!isMobile&&<th style={Th}>资金面</th>}
            <th style={Th}>板块</th>
            {!isMobile&&<th style={Th}>原型</th>}
            <th style={{...Th,width:60}}>同组排名</th>
            <th style={{...Th,cursor:'pointer',width:55}} onClick={()=>toggleSort('predicted_return')}>T+5预测{sortArrow('predicted_return')}</th>
            <th style={{...Th,cursor:'pointer',width:65}} onClick={()=>toggleSort('best_horizon')}>持仓期{sortArrow('best_horizon')}</th>
            <th style={{...Th,cursor:'pointer',width:45}} onClick={()=>toggleSort('rank_score')}>排序{sortArrow('rank_score')}</th>
            <th style={{...Th,cursor:'pointer',width:52}} onClick={()=>toggleSort('win_probability')}>胜率{sortArrow('win_probability')}</th>
            <th style={Th}>级别</th>
            <th style={Th}>现价</th>
          </>}
        </tr></thead>
        <tbody>
          {loading?Array.from({length:5}).map((_,i)=><SkeletonRow key={i} cols={totalCols}/>):
           stockRows.length===0?<tr><td colSpan={totalCols}><EmptyState icon="📊" text="暂无数据"/></td></tr>:
           (activeTab==='final'&&hasFeedback?sortedRows:sortedRows).filter((r:any)=>{
             if(marketFilter==='全部')return true;const c=r.symbol||'';
             if(marketFilter==='创业板')return c.startsWith('300')||c.startsWith('301')||c.startsWith('688');
             if(marketFilter==='中小板')return c.startsWith('002')||c.startsWith('003');
             return !(c.startsWith('300')||c.startsWith('301')||c.startsWith('688')||c.startsWith('002')||c.startsWith('003'));
           }).map((r:any,i:number)=>{
            const score=getScore(r);const act=getActionSignal(r);
            return <><tr key={i} style={{borderTop:`1px solid ${C.border}`,height:42,background:i===highlightIdx?`${C.blue}08`:act==='buy_strong'?`${C.green}04`:act==='avoid'?`${C.darkRed}04`:act==='caution'?`${C.amber}04`:'transparent'}}
              onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.background=T.card.hoverBg}}
              onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.background=i===highlightIdx?`${C.blue}08`:act==='buy_strong'?`${C.green}04`:act==='avoid'?`${C.darkRed}04`:act==='caution'?`${C.amber}04`:'transparent'}}>
              {!isCurated&&<td style={{padding:'4px 6px',textAlign:'center'}}>
                <input type="checkbox" checked={deep.selected.has(r.symbol)||promptSelected.has(r.symbol)} disabled={(!deep.selected.has(r.symbol)&&deep.selected.size>=3)&&(!promptSelected.has(r.symbol)&&promptSelected.size>=10)} onChange={()=>{deep.toggle(r.symbol);togglePrompt(r.symbol)}} style={{width:15,height:15,cursor:'pointer',accentColor:C.violet}}/>
                <span onClick={(e)=>{e.stopPropagation();setFbModal({symbol:r.symbol,name:getName(r),score})}} style={{cursor:'pointer',marginLeft:4,fontSize:13,opacity:0.5}} title="反哺">📋</span>
              </td>}
              <td style={{...Td,textAlign:'center'}}><StatusBadge type={act}/></td>
              <td style={{...Td,textAlign:'center'}}>
                {(()=>{const ri=r.rec_index;if(ri==null)return<span style={{color:C.textMuted,fontSize:10}}>—</span>;
                  const color=ri>=80?C.red:ri>=60?C.orange:ri>=40?C.amber:C.green;
                  return<span title={r.rec_index_detail||''} style={{fontWeight:700,fontSize:13,color,whiteSpace:'nowrap'}}>{ri}</span>;})()}
              </td>
              <td style={{...Td,textAlign:'center'}}>
                {(()=>{const p=r.relative_position;if(!p)return<span style={{color:C.textMuted,fontSize:10}}>—</span>;
                  const isUp=p.includes('涨')||p.includes('强')||p.includes('拉升');const isDown=p.includes('跌')||p.includes('出货');
                  return <><StatusBadge type={isUp?'buy_strong':isDown?'sell':'neutral'} label={p}/>
                    {r.predicted_return!=null&&<div style={{fontSize:9,color:r.predicted_return>0?C.red:C.green,marginTop:1}}>预{r.predicted_return>0?'+':''}{r.predicted_return}%</div>}
                  </>;})()}
              </td>
              {activeTab==='ambush'?<>
                <td style={Td}><code style={{color:C.code}}>{r.symbol}</code></td>
                <td style={{...Td,fontWeight:600}}>{getName(r)}</td>
                <td style={Td}>{r.limit_up_date}</td><td style={{...Td,color:C.red}}>+{r.limit_up_gain}%</td>
                <td style={Td}>-{r.max_drawdown}%</td><td style={Td}>{r.vol_shrink_ratio?.toFixed(2)}</td><td style={Td}>{r.launch_vol_ratio?.toFixed(2)}</td>
              </>:<>
                <td style={Td}><code style={{color:C.code}}>{r.symbol}</code></td>
                <td style={{...Td,fontWeight:600}}>{getName(r)}
                  {r.resonance_type==='weekly_resonance'&&<StatusBadge type="resonance"/>}
                  {r.risk_label==='dead'?<span style={{marginLeft:4,fontSize:13}}>💀</span>:r.risk_label==='danger'?<span style={{marginLeft:4,fontSize:13}}>🔴</span>:r.risk_label==='warn'?<span style={{marginLeft:4,fontSize:13}}>⚠</span>:null}
                </td>
                <td style={Td}><span style={{padding:'2px 6px',borderRadius:3,fontWeight:700,fontSize:11,background:score>=55?`${C.green}10`:score>=45?`${C.amber}10`:`${C.body}10`,color:score>=55?C.green:score>=45?C.amber:C.body}}>{score}</span></td>
                {hasFeedback&&<td style={Td}>{r.llm_score?<span style={{fontWeight:700,fontSize:12,color:r.llm_score>=80?C.red:r.llm_score>=60?C.amber:C.body}}>{r.llm_score}</span>:<span style={{color:C.textMuted}}>—</span>}</td>}
                {/* A1: 策略标签 */}
                <td style={{...Td,textAlign:'center'}}>
                  {(()=>{const sl=r.strategy_label;if(!sl)return<span style={{color:C.textMuted,fontSize:10}}>—</span>;
                    const isHot=sl.includes('超短'),isWarm=sl.includes('短中'),isCold=sl.includes('中线');
                    return<span style={{padding:'2px 7px',borderRadius:3,fontSize:10,fontWeight:600,
                      background:isHot?`${C.amber}15`:isWarm?`${C.green}12`:`${C.blue}10`,
                      color:isHot?C.amber:isWarm?C.green:C.blue,
                      border:`1px solid ${isHot?C.amber+'30':isWarm?C.green+'25':C.blue+'20'}`,
                    }}>{isHot?'⚡':isWarm?'📈':'🏛'} {sl}</span>;})()}
                </td>
                {/* A2: 板块温度 */}
                <td style={{...Td,textAlign:'center'}}>
                  {(()=>{const t=r.sector_tier;const rk=r.sector_rank;
                    if(!t)return<span style={{color:C.textMuted,fontSize:10}}>—</span>;
                    const color=t==='hot'?C.red:t==='warm'?C.amber:C.textDim;
                    const icon=t==='hot'?'🔥':t==='warm'?'🌡':'⚪';
                    const label=t==='hot'?'热点':t==='warm'?'温区':'冰点';
                    return<span title={`${label} #${rk}/32`} style={{fontSize:10,fontWeight:600,color}}>{icon} {label}<span style={{fontSize:9,color:C.textMuted}}> {rk}/32</span></span>;})()}
                </td>
                {!isMobile&&<td style={Td}>{r.tech_score?.toFixed(1)||'-'}</td>}
                {!isMobile&&<td style={Td}>{r.fund_score?.toFixed(1)||'-'}</td>}
                {!isCurated&&<td style={Td}>{r.sector_bonus>0?'🔥':'-'}</td>}
                {!isMobile&&<td style={Td}>{r.archetype&&r.archetype!=='unknown'?<span style={{padding:'1px 5px',borderRadius:3,fontSize:9,background:`${C.purple}12`,color:C.purple}}>{ARCH_CN[r.archetype]||r.archetype.replace(/_/g,' ')}</span>:'-'}
                  {r.relative_position&&<span style={{fontSize:9,color:C.textDim,marginLeft:4}}>
                    {r.sector_direction==='上升'?'📈':r.sector_direction==='下降'?'📉':'➡'}
                    {r.sector_lifecycle&&r.sector_lifecycle!=='正常'?` ${r.sector_lifecycle}`:''}
                    {r.sector_rank_5d?<span style={{color:r.sector_rank_5d<=5?C.green:r.sector_rank_5d<=10?C.amber:C.textDim}}> #{r.sector_rank_5d}/32</span>:''}
                  </span>}
                </td>}
                {/* A3: 同组排名 */}
                <td style={{...Td,textAlign:'center'}}>
                  {(()=>{const pr=r.peer_rank;if(!pr)return<span style={{color:C.textMuted,fontSize:10}}>—</span>;
                    const parts=pr.split('#');
                    const label=parts.length>1?`#${parts[1]}`:pr;
                    const numStr=parts[1]?.split('/')[0]||'99';const num=parseInt(numStr)||99;
                    return<span title={r.score_label||pr} style={{fontWeight:num<=3?700:500,fontSize:num<=3?12:10,
                      color:num<=3?C.red:num<=5?C.amber:C.textDim}}>{label}</span>;})()}
                </td>
                <td style={Td}>
                  {r.predicted_return != null ? (
                    <span style={{fontWeight:600, fontSize:11, color: r.predicted_return>0 ? C.red : C.green}}>
                      {r.predicted_return>0?'+':''}{r.predicted_return}%
                    </span>
                  ) : <span style={{color:C.textMuted, fontSize:10}}>—</span>}
                </td>
                {/* v7.0.11: v2 推荐持仓期列 (4 horizon × 2 model 独立训练) */}
                <td style={{...Td,textAlign:'center', cursor:'help'}} title={r.v2_advice || 'v2 训练中无数据'}>
                  {r.best_horizon != null ? (
                    <div>
                      <span style={{
                        display:'inline-block', padding:'2px 8px', borderRadius:4,
                        background: r.v2_active ? '#3b82f620' : 'transparent',
                        color: r.v2_active ? C.green : C.textMuted,
                        fontWeight:600, fontSize:11,
                      }}>T+{r.best_horizon}</span>
                      <div style={{fontSize:9, color:C.textMuted, marginTop:1}}>
                        {r.best_strategy || (r.v2_active ? '—' : 'v2 关')}
                      </div>
                    </div>
                  ) : <span style={{color:C.textMuted, fontSize:10}}>{r.v2_active ? '无数据' : 'v2 关'}</span>}
                </td>
                <td style={{...Td,textAlign:'center'}}>
                  {r.rank_score != null ? (
                    <span style={{fontWeight:600, fontSize:11, color: r.rank_score>=0.7 ? C.green : r.rank_score>=0.5 ? C.amber : C.textDim}}
                      title={`排序分 ${(r.rank_score*100).toFixed(0)}/100 — 0.7+强推 0.3-弱`}>
                      {(r.rank_score*100).toFixed(0)}
                    </span>
                  ) : <span style={{color:C.textMuted, fontSize:10}}>—</span>}
                </td>
                <td style={Td}>{r.win_probability!=null?<span style={{fontWeight:600,fontSize:11,color:r.win_probability>=0.45?C.green:r.win_probability>=0.35?C.amber:C.red}}
                  title={`贝叶斯校准胜率\n${r.predicted_return!=null?'T+5预测: '+(r.predicted_return>0?'+':'')+r.predicted_return+'%\n':''}${r.predicted_win_prob!=null?'模型置信: '+(r.predicted_win_prob*100).toFixed(0)+'%\n':''}月推次数: ${r.monthly_pushes||1}次${r.drill_summary?'\n'+r.drill_summary:''}`}
                >{(r.win_probability*100).toFixed(0)}%</span>:<span style={{color:C.textMuted}}>—</span>}
                  {r.predicted_win_prob!=null&&<span style={{fontSize:8,color:C.textMuted,marginLeft:2}}>M{(r.predicted_win_prob*100).toFixed(0)}</span>}</td>
                <td style={Td}><span style={{padding:'1px 5px',borderRadius:3,fontSize:10,fontWeight:600,background:r.level==='L3'?`${C.amber}15`:r.level==='L2'?`${C.blue}15`:`${C.body}12`,color:r.level==='L3'?C.amber:r.level==='L2'?'#60a5fa':C.body}}>{r.level||'-'}</span></td>
                <td style={Td}>{r.close_price?.toFixed(2)||'-'}</td>
              </>}
            </tr>
            {((r.hidden_risks||[]).length>0||(r.catalysts||[]).length>0||r.drill_signal_effectiveness||r.dimension_scores)&&
            <tr style={{borderTop:`1px solid rgba(30,37,53,0.5)`,background:'rgba(16,22,37,0.5)'}}>
              <td colSpan={totalCols} style={{padding:'4px 12px'}}>
                <div style={{display:'flex',gap:12,flexWrap:'wrap',alignItems:'center'}}>
                  {(r.hidden_risks||[]).slice(0,3).map((s:any,j:number)=><span key={'hr'+j} style={{fontSize:9,padding:'1px 5px',borderRadius:3,background:getSignalStyle('sell').bg,color:C.red,border:`1px solid ${C.red}15`}}>⚠{typeof s==='string'?s:s.description||s.label||''}</span>)}
                  {(r.catalysts||[]).slice(0,3).map((s:any,j:number)=><span key={'cat'+j} style={{fontSize:9,padding:'1px 5px',borderRadius:3,background:getSignalStyle('buy').bg,color:C.green,border:`1px solid ${C.green}15`}}>✓{typeof s==='string'?s:s.description||s.label||''}</span>)}
                  {r.drill_signal_effectiveness?.win_rate_5d!=null&&<ProgressBar label="胜率" percent={(r.drill_signal_effectiveness.win_rate_5d||0)*100} color={r.drill_signal_effectiveness.win_rate_5d>=0.5?C.green:C.amber} height={3}/>}
                  {/* 评分明细简表 */}
                  {r.dimension_scores&&Object.keys(r.dimension_scores).length>0&&
                    Object.entries(r.dimension_scores).filter(([k])=>['fundamentals','kline_game','fund_flow','technical'].includes(k)).map(([k,v]:any)=><span key={'dim'+k} style={{fontSize:9,color:v?.raw!=null?(v.raw>0?C.green:C.red):C.textMuted}}>{k.replace('_',' ')}: {v?.score!=null?Number(v.score).toFixed(1):'?'}</span>)
                  }
                  {r.sector_bonus>0&&<span style={{fontSize:9,color:C.amber}}>板块:+{r.sector_bonus}</span>}
                  {/* ★ v4.9: Regime 系数信息 */}
                  {r.market_coef!=null&&r.sector_coef!=null&&<span style={{fontSize:9,padding:'1px 4px',borderRadius:2,background:'rgba(139,92,246,0.12)',color:'#a78bfa'}}>
                    系数:{(r.market_coef*r.sector_coef).toFixed(2)}
                    {r.regime_signal_cn&&<span style={{marginLeft:3}}>{r.regime_signal_cn}</span>}
                  </span>}
                </div>
              </td>
            </tr>}
          </>})}
        </tbody>
      </table>

      <DeepAnalysisModal symbols={[...deep.selected]} scores={(()=>{const m:Record<string,number>={};for(const s of deep.selected){const row=[...fusionData,...ambushData,...data].find((x:any)=>x.symbol===s);m[s]=getScore(row)||50}return m})()} stockNames={(()=>{const m:Record<string,string>={};for(const s of deep.selected){const row=[...fusionData,...ambushData,...data].find((x:any)=>x.symbol===s);if(row)m[s]=getName(row)}return m})()} open={deep.open} onClose={deep.close} onComplete={()=>{deep.complete();load()}}/>
      {fbModal&&<FeedbackModal symbol={fbModal.symbol} name={fbModal.name} score={fbModal.score} tradeDate={scanDate||new Date().toISOString().slice(0,10)} open={!!fbModal} onClose={()=>setFbModal(null)}/>}
      <PromptModal symbols={[...promptSelected]} scores={(()=>{const m:Record<string,number>={};for(const s of promptSelected){const row=[...fusionData,...ambushData,...data].find((x:any)=>x.symbol===s);m[s]=getScore(row)||50}return m})()} stockNames={(()=>{const m:Record<string,string>={};for(const s of promptSelected){const row=[...fusionData,...ambushData,...data].find((x:any)=>x.symbol===s);if(row)m[s]=getName(row)}return m})()} open={promptOpen} onClose={()=>setPromptOpen(false)}/>
    </div>
  );
}
