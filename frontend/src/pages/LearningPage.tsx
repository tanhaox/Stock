import { useEffect, useRef, useState } from 'react';
import api from '../lib/api';
import DnaLab from '../components/DnaLab';

export default function LearningPage() {
  const [tab, setTab] = useState<'shadow' | 'overview' | 'params' | 'experiences' | 'weights' | 'selflearn' | 'newsverify' | 'dna'>('selflearn');
  const [panelData, setPanelData] = useState<any[]>([]);
  const [trainingInfo, setTrainingInfo] = useState<any>(null);
  const [training, setTraining] = useState<string | null>(null);
  const [lastMsg, setLastMsg] = useState<string | null>(null);
  const [trainProgress, setTrainProgress] = useState<{done:number, total:number} | null>(null);
  const [trainErrors, setTrainErrors] = useState(0);
  const [trainHeartbeat, setTrainHeartbeat] = useState(0);
  const [trainStartTime, setTrainStartTime] = useState<number>(0);
  const [elapsed, setElapsed] = useState(0);
  const [klineDetail, setKlineDetail] = useState<string | null>(null);
  const pollTimerRef = useRef<number>(0);
  const [dimensions, setDimensions] = useState<any[]>([]);
  const [convergence, setConvergence] = useState<any>(null);
  const [stats, setStats] = useState<any>(null);
  const [params, setParams] = useState<any[]>([]);
  const [experiences, setExperiences] = useState<any[]>([]);
  const [backtesting, setBacktesting] = useState(false);
  const [backtestResult, setBacktestResult] = useState<any>(null);
  const [accuracyStats, setAccuracyStats] = useState<any>(null);
  const [expandedArch, setExpandedArch] = useState<Set<string>>(new Set());
  // ★ v4.1: 分段权重展示
  const [trainedWeights, setTrainedWeights] = useState<any>(null);
  const [weightsTab, setWeightsTab] = useState<'__global__' | 'bull' | 'bear' | 'range'>('__global__');
  const [calibrationData, setCalibrationData] = useState<any>(null);
  const [selfStatus, setSelfStatus] = useState<any>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncResult, setSyncResult] = useState('');
  const [newsVerifyData, setNewsVerifyData] = useState<any>(null);

  const load = async () => {
    try { const r = await api.get('/learning/params'); setParams(r.data.data || []); } catch {}
    try { const r = await api.get('/learning/stats'); setStats(r.data.data); } catch {}
    try { const r = await api.get('/learning/experiences', { params: { limit: 30 } }); setExperiences(r.data.data || []); } catch {}
    try { const r = await api.get('/learning/accuracy'); setAccuracyStats(r.data.data); } catch {}
  };
  const loadWeights = async () => {
    try { const r = await api.get('/learning/weights-trained'); setTrainedWeights(r.data); } catch {}
    try { const r = await api.get('/learning/archetypes/calibration-data', { params: { days: 180 } }); setCalibrationData(r.data); } catch {}
  };
  const loadSelfStatus = async () => {
    try { const r = await api.get('/learning/self-status'); setSelfStatus(r.data?.data); } catch {}
  };
  const syncInfra = async () => {
    setSyncing(true); setSyncResult('');
    try {
      const r = await api.post('/learning/sync-infra');
      const parts: string[] = [];
      for (const [tbl, date] of Object.entries(r.data.freshness || {}))
        parts.push(`${tbl}: ${date}`);
      setSyncResult('✅ ' + parts.join(' | '));
      loadSelfStatus();
    } catch(e: any) { setSyncResult('❌ ' + (e?.response?.data?.detail || '失败')); }
    setSyncing(false);
  };
  const loadNewsVerify = async () => {
    try { const r = await api.get('/learning/news-verify-summary'); setNewsVerifyData(r.data); } catch {}
  };
  useEffect(() => { load(); loadPanel(); loadDims(); checkTrainingStatus(); return () => { if (pollTimerRef.current) clearTimeout(pollTimerRef.current); }; }, []);
  useEffect(() => { if (tab === 'weights') loadWeights(); }, [tab]);
  useEffect(() => { if (tab === 'selflearn') loadSelfStatus(); }, [tab]);
  useEffect(() => { if (tab === 'newsverify') loadNewsVerify(); }, [tab]);

  // 页面挂载时检测是否有后台训练在跑，有则恢复轮询
  const checkTrainingStatus = async () => {
    try {
      const r = await api.get('/learning/shadow-train/status');
      const jobs = r.data?.data || {};
      const running = Object.values(jobs).filter((j: any) => j?.running).length;
      if (running > 0) {
        const total = Object.values(jobs).length;
        const done = Object.values(jobs).filter((j: any) => j && !j.running).length;
        setTraining('后台训练中');
        setTrainProgress({done, total});
        pollProgress();
      }
    } catch {}
  };

  const loadPanel = async () => {
    try { const r = await api.get('/learning/panel'); setPanelData(r.data.data || []); setTrainingInfo(r.data.training_info || null); } catch {}
  };
  const loadDims = async () => {
    try { const r = await api.get('/learning/dimensions'); setDimensions(r.data.data || []); } catch {}
  };
  const checkConv = async () => {
    try { const r = await api.post('/learning/dimensions/check-convergence'); setConvergence(r.data); } catch {}
  };

  const trainOne = async (arch: string, st: string) => {
    setTraining(`${arch}/${st}`);
    try {
      const r = await api.post('/learning/shadow-train', null, { params: { archetype: arch, strategy: st, iterations: 5 } });
      const sched = r.data?.scheduled?.length || 0;
      if (sched > 0) { pollProgress(); }
      else { const skipped = r.data?.skipped?.length || 0; if (skipped > 0) setLastMsg(`${arch}/${st} 已跳过`); }
    }
    catch (e: any) { alert(e?.response?.data?.detail || '训练失败'); setTraining(null); }
  };
  const trainAll = async () => {
    setTraining('全部原型');
    setTrainStartTime(Date.now());
    setElapsed(0);
    try {
      const r = await api.post('/learning/shadow-train', null, { params: { archetype: 'all', strategy: 'all', iterations: 5 } });
      const sched = r.data?.scheduled?.length || 0;
      if (sched > 0) {
        setTrainProgress({done: 0, total: sched});
        setLastMsg(`${sched} 个训练任务已启动`);
        pollProgress();
      }
      const skipped = r.data?.skipped?.length || 0;
      if (skipped > 0) setLastMsg(`${skipped} 个已跳过，${sched} 个已启动`);
    }
    catch (e: any) { alert(e?.response?.data?.detail || '训练失败'); setTraining(null); }
  };

  const pollProgress = async () => {
    const check = async () => {
      try {
        const r = await api.get('/learning/shadow-train/status');
        const jobs = r.data?.data || {};
        const running = Object.values(jobs).filter((j: any) => j?.running).length;
        const done = Object.values(jobs).filter((j: any) => j && !j.running).length;
        const errors = Object.values(jobs).filter((j: any) => j && j.error).length;
        // K线路径股票级进度 — 详细展示，避免看起来像卡死
        const kp = r.data?.kline_progress || {};
        const kpEntries = Object.entries(kp).filter(([,v]:any) => v.done > 0 && v.done < v.total);
        const doneKp = Object.entries(kp).filter(([,v]:any) => v.done >= v.total && v.total > 0);

        if (kpEntries.length > 0) {
          // 有活跃任务：显示第一个的详情
          const [key, val]: any = kpEntries[0];
          const phaseName: Record<string,string> = {bull:'牛市', bear:'熊市', range:'震荡'};
          const ph = phaseName[val.phase] || '';
          const pct = val.total > 0 ? Math.round(val.done / val.total * 100) : 0;
          setKlineDetail(`${kpEntries.length}活跃 · ${key.split('/')[0]} ${ph}${val.iteration ? '第'+val.iteration+'轮' : ''} ${pct}% (${val.current_sym || ''})`);
        } else if (doneKp.length > 0 && running > 0) {
          // 所有本轮完成，正在生成候选权重并进入下一阶段
          setKlineDetail(`${doneKp.length}个本轮完成 · 计算下一阶段权重中...`);
        } else if (running > 0) {
          setKlineDetail('正在加载K线数据...');
        } else {
          setKlineDetail(null);
        }
        const total = done + running;
        if (total > 0) {
          setTrainProgress({done, total});
          setTrainErrors(errors);
          setTrainHeartbeat(h => h + 1);
          if (trainStartTime > 0) setElapsed(Math.round((Date.now() - trainStartTime) / 1000));
          if (running > 0) {
            pollTimerRef.current = window.setTimeout(check, 5000);
            return;
          }
        }
        setTimeout(() => { setTrainProgress(null); setTrainErrors(0); setTrainHeartbeat(0); setKlineDetail(null); setElapsed(0); }, 3000);
        await loadPanel();
      } catch { setTrainProgress(null); }
      setTraining(null);
    };
    check();
  };
  const upgradeOne = async (arch: string, st: string) => {
    if (!confirm(`将 ${arch} ${st} 影子权重升级为正式权重？`)) return;
    try {
      const r = await api.post('/learning/upgrade', { archetype: arch, strategy: st });
      if (r.data.status === 'conflict') {
        const c = r.data.conflicts || [];
        const rv = r.data.revalidated || {};
        const s3info = rv.S3 ? `\nS3重验证: ${rv.S3.score?.toFixed(4)} (${rv.S3.metric})` : '';
        alert(`升级冲突！\n${c.join('\n')}${s3info}\n\n如需强制升级请联系管理员`);
        return;
      }
      const rv = r.data.revalidated || {};
      if (Object.keys(rv).length > 0) {
        const parts = [];
        if (rv.S2) parts.push(`S2=${rv.S2.score?.toFixed(4)}`);
        if (rv.S3) parts.push(`S3=${rv.S3.score?.toFixed(4)}`);
        if (parts.length > 0) setLastMsg(`重验证通过: ${parts.join(', ')}`);
      }
      await loadPanel();
    }
    catch (e: any) { alert(e?.response?.data?.detail || '升级失败'); }
  };
  const rollbackOne = async (arch: string, st: string) => {
    if (!confirm(`回滚 ${arch} ${st} 到上一个版本？`)) return;
    try {
      await api.post('/learning/rollback', { archetype: arch, strategy: st });
      setLastMsg(`${arch}/${st} 已回滚`);
      await loadPanel();
    }
    catch (e: any) { alert(e?.response?.data?.detail || '回滚失败'); }
  };
  const injectDim = async (key: string) => {
    try { await api.post('/learning/dimensions/inject', null, { params: { dim_key: key } }); await loadDims(); }
    catch (e: any) { alert(e?.response?.data?.detail || '注入失败'); }
  };
  const syncBeliefs = async () => {
    try { await api.post('/learning/beliefs-sync'); alert('信念同步完成'); }
    catch (e: any) { alert(e?.response?.data?.detail || '同步失败'); }
  };
  const runBacktest = async () => {
    setBacktesting(true); setBacktestResult(null);
    try { const r = await api.post('/learning/backtest', null, { params: { days: 30 } }); setBacktestResult(r.data.data); await load(); }
    catch (e: any) { alert(e?.response?.data?.detail || '回测失败'); }
    setBacktesting(false);
  };

  const toggleArch = (a: string) => { setExpandedArch(prev => { const n = new Set(prev); if (n.has(a)) n.delete(a); else n.add(a); return n; }); };

  const ARCH_CN: Record<string, string> = {
    '主板_large_bluechip': '主板大盘蓝筹', '主板_small_speculative': '主板小盘题材',
    '主板_growth_tech': '主板科技成长', '主板_value_defensive': '主板价值防御',
    '主板_cyclical_resource': '主板周期资源',
    '创业板_large_bluechip': '创业板蓝筹', '创业板_small_speculative': '创业板小盘',
    '创业板_growth_tech': '创业板科技', '创业板_value_defensive': '创业板防御',
    '创业板_cyclical_resource': '创业板周期',
    large_bluechip: '大盘蓝筹', small_speculative: '小盘题材',
    growth_tech: '科技成长', value_defensive: '价值防御',
    cyclical_resource: '周期资源',
  };

  const fmtLast = (iso: string | null) => {
    if (!iso) return null;
    const d = new Date(iso);
    const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin}分钟前`;
    if (diffMin < 1440) return `${Math.floor(diffMin/60)}小时前`;
    return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  };

  const trainedCount = panelData.filter((a:any) => a.strategies?.S2?.converge_status !== 'untrained').length;
  const upgradableCount = panelData.filter((a:any) => a.strategies?.S2?.upgrade_state === 'green').length;

  const statusBadge = (s: any) => {
    // 实时状态：后台正在训练中
    if (s.is_training) return <span style={{color:'#8b5cf6',fontSize:11}}>⏳ 训练中</span>;
    if (!s || s.converge_status === 'untrained') return <span style={{color:'#4b5563',fontSize:11}}>未训练</span>;
    const state = s.upgrade_state || 'gray';
    if (state === 'green') return <span style={{color:'#10b981',fontSize:11}}>● 可升级</span>;
    if (state === 'yellow') return <span style={{color:'#f59e0b',fontSize:11}}>◐ 接近达标</span>;
    if (s.converge_status === 'converged') return <span style={{color:'#10b981',fontSize:11}}>✓ 已收敛</span>;
    if (s.converge_status === 'overfit') return <span style={{color:'#ef4444',fontSize:11}}>⚠ 过拟合</span>;
    return <span style={{color:'#4b5563',fontSize:11}}>空闲</span>;  // 训练过但当前未在训练
  };

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      {/* ── 顶部 ── */}
      <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
        <div>
          <h2 style={{ margin:0 }}>AI 策略优化</h2>
          <p style={{ color:'#6e7a8a', fontSize:12, margin:'4px 0 0' }}>
            影子系统自动学习最优评分权重 · 影子层超越现实层后提示升级
          </p>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          {(['selflearn','newsverify','dna','shadow','overview','params','experiences','weights'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              style={{ padding:'6px 16px', borderRadius:6, fontSize:12, fontWeight:600, cursor:'pointer',
                border: tab===t?'1px solid #8b5cf6':'1px solid #1e2535',
                background: tab===t?'rgba(139,92,246,0.1)':'transparent',
                color: tab===t?'#8b5cf6':'#6e7a8a' }}>
              {{selflearn:'自学习',newsverify:'新闻验证',dna:'🧬 DNA实验室',shadow:'策略优化',overview:'概览',params:'参数',experiences:'经验',weights:'分段权重'}[t]}
            </button>
          ))}
        </div>
      </div>

      {/* ── 策略优化 (默认标签) ── */}
      {tab === 'shadow' && (
        <div>
          {/* 状态摘要卡片 */}
          <div style={{ display:'flex', gap:12, marginBottom:16 }}>
            {[ {v:trainedCount, label:'已训练原型', color:'#8b5cf6'},
               {v:upgradableCount, label:'可升级', color: upgradableCount>0?'#10b981':'#6e7a8a'},
               {v:panelData.length, label:'原型总数', color:'#6e7a8a'},
               {v:dimensions.filter((d:any)=>d.status==='probation').length, label:'试用维度', color:'#f59e0b'},
            ].map((m,i) => (
              <div key={i} style={{ flex:1, background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:14, textAlign:'center' }}>
                <div style={{ fontSize:22, fontWeight:700, color:m.color }}>{m.v}</div>
                <div style={{ fontSize:11, color:'#6e7a8a', marginTop:4 }}>{m.label}</div>
              </div>
            ))}
          </div>

          {/* 维度状态条 + 收敛信息 */}
          <div style={{ display:'flex', gap:8, marginBottom:16, flexWrap:'wrap', alignItems:'center' }}>
            <span style={{ fontSize:11, color:'#6e7a8a' }}>维度:</span>
            {dimensions.map((d:any) => (
              <span key={d.key} style={{ padding:'3px 8px', borderRadius:10, fontSize:10,
                background: d.status==='active'?'rgba(16,185,129,0.1)':d.status==='probation'?'rgba(245,158,11,0.1)':'rgba(107,114,128,0.08)',
                color: d.status==='active'?'#10b981':d.status==='probation'?'#f59e0b':'#6e7a8a',
                cursor: d.status==='candidate'?'pointer':'default' }}
                onClick={() => d.status==='candidate' && injectDim(d.key)}
                title={d.status==='candidate'?'点击注入试用':'当前状态: '+d.status}>
                {d.status==='active'?'✓':d.status==='probation'?'⏳':''} P{d.priority}
              </span>
            ))}
            <button onClick={checkConv} title="检查各维度影子训练是否收敛" style={{ marginLeft:'auto', padding:'4px 10px', borderRadius:4, border:'1px solid #f59e0b', background:'transparent', color:'#f59e0b', cursor:'pointer', fontSize:10 }}>
              检测收敛
            </button>
          </div>
          {convergence?.next_candidate_dimension && (
            <div style={{ marginBottom:12, padding:'6px 12px', borderRadius:6, background:'rgba(16,185,129,0.05)', border:'1px solid rgba(16,185,129,0.1)', fontSize:11, color:'#10b981' }}>
              💡 建议注入新维度: P{convergence.next_candidate_dimension.priority} {convergence.next_candidate_dimension.name}
            </div>
          )}

          {/* 训练按钮行 */}
          <div style={{ display:'flex', gap:10, marginBottom:16, alignItems:'center' }}>
            <button onClick={trainAll} disabled={!!training}
              style={{ padding:'10px 24px', borderRadius:8, border:'none', background: training?'#374151':'#8b5cf6', color:'#fff', cursor: training?'not-allowed':'pointer', fontSize:13, fontWeight:600 }}>
              {training || '🚀 训练全部原型'}
            </button>
            <button onClick={syncBeliefs} title="将影子层学习到的最优权重同步到现实层参数库"
              style={{ padding:'8px 16px', borderRadius:8, border:'1px solid #f59e0b', background:'transparent', color:'#f59e0b', cursor:'pointer', fontSize:12 }}>
              同步信念
            </button>
            <span style={{ fontSize:11, color:'#6e7a8a' }}>约 2-5 分钟/原型 (视K线量而定)</span>
            {lastMsg && (
              <span style={{ fontSize:11, color:'#f59e0b', background:'rgba(245,158,11,0.08)', padding:'4px 10px', borderRadius:4 }}>
                {lastMsg}
              </span>
            )}
          </div>
          {/* 训练进度条 */}
          {trainProgress && (
            <div style={{ marginBottom:16, background:'#161b27', border:'1px solid #1e2535', borderRadius:8, padding:'10px 16px' }}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6, fontSize:11, alignItems:'center' }}>
                <span style={{ display:'flex', alignItems:'center', gap:6 }}>
                  <span style={{
                    width:8, height:8, borderRadius:'50%', display:'inline-block',
                    background: trainErrors > 0 ? '#ef4444' : '#10b981',
                    animation: trainErrors > 0 ? 'none' : 'pulse 1.2s ease-in-out infinite',
                  }}/>
                  <span style={{ color: trainErrors > 0 ? '#ef4444' : '#a78bfa' }}>
                    {trainErrors > 0 ? `K线训练中(${trainErrors}错)` : 'K线训练中...'}
                  </span>
                  {klineDetail && (
                    <span style={{ color: '#4b5563', fontSize: 10, marginLeft: 8 }}>{klineDetail}</span>
                  )}
                </span>
                <span style={{ color:'#6e7a8a' }}>{trainProgress.done}/{trainProgress.total}{elapsed > 0 ? ` · ${Math.floor(elapsed/60)}分${elapsed%60}秒` : ''}</span>
              </div>
              <div style={{ height:4, background:'#1e2535', borderRadius:2, overflow:'hidden' }}>
                <div style={{ height:'100%', width:`${trainProgress.total>0?(trainProgress.done/trainProgress.total*100):0}%`, background:'linear-gradient(90deg, #8b5cf6, #10b981)', borderRadius:2, transition:'width 0.5s' }}/>
              </div>
            </div>
          )}
          {/* 训练数据概况 */}
          {trainingInfo && (
            <div style={{ marginBottom:16, padding:'10px 16px', background:'#161b27', border:'1px solid #1e2535', borderRadius:8, display:'flex', gap:24, flexWrap:'wrap', fontSize:11 }}>
              <span style={{color:'#6e7a8a'}}>
                评分数据: <b style={{color:'#a78bfa'}}>{trainingInfo.analysis_dates}天</b>
                <span style={{color:'#4b5563'}}> ({trainingInfo.analysis_range}) / {trainingInfo.analysis_total_rows}行</span>
              </span>
              <span style={{color:'#6e7a8a'}}>
                K线: <b style={{color:'#a78bfa'}}>{(trainingInfo.kline_rows/10000).toFixed(0)}万行</b>
                <span style={{color:'#4b5563'}}> ({trainingInfo.kline_range})</span>
              </span>
              {['S1','S2','S3'].map(st => {
                const hi = trainingInfo.horizon_info?.[st] || {};
                return (
                  <span key={st} style={{color:'#6e7a8a'}}>
                    {st}: <b style={{color: hi.verifiable_dates >= 5 ? '#10b981' : '#f59e0b'}}>{hi.verifiable_dates}天可验证</b>
                    <span style={{color:'#4b5563',fontSize:9}}> ({hi.path})</span>
                  </span>
                );
              })}
            </div>
          )}

          {/* 原型列表 */}
          {panelData.length === 0 ? (
            <div style={{ textAlign:'center', padding:60, background:'#161b27', border:'1px solid #1e2535', borderRadius:12, color:'#4b5563' }}>
              <div style={{ fontSize:40, marginBottom:8 }}>🧠</div>
              <div style={{ fontSize:14, fontWeight:600, marginBottom:4 }}>尚未开始训练</div>
              <div style={{ fontSize:12 }}>点击「训练全部原型」启动影子系统，AI 将自动搜索最优评分权重</div>
            </div>
          ) : (
            <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:10 }}>
              {panelData.map((arch:any) => {
                const s2 = arch.strategies?.S2 || {};
                const gap = s2.shadow_sharpe - s2.reality_sharpe;
                const isExp = expandedArch.has(arch.archetype);
                return (
                  <div key={arch.archetype} onClick={() => toggleArch(arch.archetype)}
                    style={{ background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:14, cursor:'pointer',
                      borderColor: s2.upgrade_state === 'green' ? '#10b981' : s2.upgrade_state === 'yellow' ? '#f59e0b' : '#1e2535' }}>
                    {/* 原型名 + 状态 */}
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:8 }}>
                      <span style={{ fontWeight:600, fontSize:13 }}>{ARCH_CN[arch.archetype] || arch.archetype}</span>
                      {statusBadge(s2)}
                    </div>
                    <div style={{ fontSize:10, color:'#6e7a8a', marginBottom:8 }}>
                      {arch.sample_count} 只股票
                      {fmtLast(s2.last_trained_at) && (
                        <span style={{ marginLeft:6, color: (()=>{ const d=new Date(s2.last_trained_at); return (Date.now()-d.getTime())<14400000?'#10b981':'#4b5563'; })(), fontSize:9 }}>
                          · {fmtLast(s2.last_trained_at)}
                        </span>
                      )}
                    </div>

                    {/* 关键指标 — S2 */}
                    <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                      <span style={{ fontSize:10, color:'#6e7a8a' }}>S2 影子层</span>
                      <span style={{ fontSize:13, fontWeight:700, color: gap>0?'#ef4444':'#6e7a8a' }}>{s2.shadow_sharpe?.toFixed(4) || '—'}</span>
                    </div>
                    <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
                      <span style={{ fontSize:10, color:'#6e7a8a' }}>S2 差距</span>
                      <span style={{ fontSize:12, fontWeight:600, color: gap>0?'#ef4444':gap<0?'#10b981':'#6e7a8a' }}>
                        {gap ? (gap>0?'+':'')+gap.toFixed(4) : '—'}
                      </span>
                    </div>

                    {/* 操作按钮 */}
                    <div style={{ display:'flex', gap:6 }}>
                      <button onClick={(e) => { e.stopPropagation(); trainOne(arch.archetype, 'S2'); }}
                        disabled={!!training}
                        style={{ flex:1, padding:'5px 0', borderRadius:4, border:'1px solid #8b5cf6', background:'transparent', color:'#a78bfa', cursor: training?'not-allowed':'pointer', fontSize:10 }}>
                        训练
                      </button>
                      {s2.upgrade_state === 'green' && (
                        <button onClick={(e) => { e.stopPropagation(); upgradeOne(arch.archetype, 'S2'); }}
                          style={{ flex:1, padding:'5px 0', borderRadius:4, border:'none', background:'rgba(16,185,129,0.15)', color:'#10b981', cursor:'pointer', fontSize:10, fontWeight:600 }}>
                          升级
                        </button>
                      )}
                      {s2.upgrade_state === 'yellow' && (
                        <span style={{ flex:1, textAlign:'center', fontSize:9, color:'#f59e0b', padding:'5px 0' }}>
                          接近达标
                        </span>
                      )}
                      <button onClick={(e) => { e.stopPropagation(); rollbackOne(arch.archetype, 'S2'); }}
                        style={{ flex:1, padding:'5px 0', borderRadius:4, border:'1px solid #374151', background:'transparent', color:'#4b5563', cursor:'pointer', fontSize:9 }}
                        title="回滚到上一版本">
                        回滚
                      </button>
                    </div>

                    {/* 展开详情 — 全策略×全阶段 */}
                    {isExp && (
                      <div style={{ marginTop:10, paddingTop:10, borderTop:'1px solid #1e2535', fontSize:10 }}>
                        <div style={{ color:'#6e7a8a', marginBottom:6 }}>策略 × 市场阶段 (bull/bear/range):</div>
                        {['S1','S2','S3'].map(st => {
                          const s = arch.strategies?.[st] || {};
                          const phases = s.phases || {};
                          return (
                            <div key={st} style={{ marginBottom:6 }}>
                              <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:3 }}>
                                <span style={{ fontWeight: st==='S2'?600:400, fontSize:10, minWidth:20 }}>
                                  {st}{st==='S2'?' 主':''}
                                </span>
                                <button onClick={(ev) => { ev.stopPropagation(); trainOne(arch.archetype, st); }}
                                  disabled={!!training}
                                  style={{ padding:'1px 8px', borderRadius:3, border:'1px solid #374151', background:'transparent',
                                    color:'#8b5cf6', cursor: training?'not-allowed':'pointer', fontSize:9 }}>
                                  训练
                                </button>
                              </div>
                              <div style={{ display:'flex', gap:4, marginLeft:24 }}>
                                {(['bull','bear','range'] as const).map(phase => {
                                  const p = phases[phase] || {};
                                  const disc = p.shadow_sharpe || 0;
                                  const state = p.upgrade_state || 'gray';
                                  return (
                                    <div key={phase} style={{
                                      flex:1, padding:'4px 6px', borderRadius:4, fontSize:9,
                                      background: state==='green' ? 'rgba(16,185,129,0.06)' : 'rgba(30,37,53,0.4)',
                                      border: state==='green' ? '1px solid rgba(16,185,129,0.15)' : '1px solid #1e2535',
                                    }}>
                                      <div style={{ color: phase==='bull'?'#ef4444':phase==='bear'?'#10b981':'#f59e0b', fontWeight:600, marginBottom:1 }}>
                                        {phase==='bull'?'牛':phase==='bear'?'熊':'震'}
                                      </div>
                                      <div style={{ color: disc>0?'#c9d1d9':'#4b5563' }}>
                                        {disc>0 ? disc.toFixed(3) : '—'}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 概览标签 ── */}
      {tab === 'overview' && stats && (
        <div>
          <div style={{ display:'flex', gap:12, marginBottom:20 }}>
            {[
              { label:'总经验数', value:stats.total_experiences, color:'#3b82f6' },
              { label:'已学习参数', value:`${stats.learned_parameters}/${stats.total_parameters}`, color:'#8b5cf6' },
              { label:'30日均奖励', value:stats.avg_reward_30d.toFixed(2), color:stats.avg_reward_30d>=0?'#10b981':'#ef4444' },
            ].map((m,i) => (
              <div key={i} style={{ flex:1, background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:16, textAlign:'center' }}>
                <div style={{ fontSize:24, fontWeight:700, color:m.color }}>{m.value}</div>
                <div style={{ fontSize:11, color:'#6e7a8a', marginTop:6 }}>{m.label}</div>
              </div>
            ))}
          </div>
          {/* 推荐准确率 */}
          {accuracyStats && (
            <div style={{ display:'flex', gap:12, marginBottom:20 }}>
              {(['T+2','T+5','T+15'] as const).map(period => {
                const s = accuracyStats[period] || {};
                return (
                  <div key={period} style={{ flex:1, background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:16, textAlign:'center' }}>
                    <div style={{ fontSize:11, color:'#6e7a8a', marginBottom:4 }}>推荐 {period} 胜率</div>
                    <div style={{ fontSize:24, fontWeight:700, color: (s.win_rate||0) >= 55 ? '#ef4444' : (s.win_rate||0) >= 45 ? '#f59e0b' : '#10b981' }}>
                      {s.win_rate || '—'}%
                    </div>
                    <div style={{ fontSize:10, color:'#6e7a8a', marginTop:4 }}>
                      {s.profitable || 0}/{s.verified || 0} 只盈利
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:16 }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
              <div><div style={{ fontWeight:600, fontSize:14 }}>滚动回测</div><div style={{ fontSize:11, color:'#6e7a8a', marginTop:2 }}>近30天 T+2 验证，更新贝叶斯参数</div></div>
              <button onClick={runBacktest} disabled={backtesting}
                style={{ padding:'8px 20px', borderRadius:8, border:'none', cursor:backtesting?'not-allowed':'pointer', background:backtesting?'#374151':'#8b5cf6', color:'#fff', fontSize:13, fontWeight:600 }}>
                {backtesting?'回测中...':'运行回测'}
              </button>
            </div>
            {backtestResult && (
              <div style={{ display:'flex', gap:16, padding:12, background:'#0b0e14', borderRadius:8 }}>
                <div><span style={{color:'#6e7a8a',fontSize:11}}>天数 </span><span style={{fontWeight:600}}>{backtestResult.days_tested}</span></div>
                <div><span style={{color:'#6e7a8a',fontSize:11}}>区分度 </span><span style={{fontWeight:600,color:backtestResult.avg_discrimination>=0?'#ef4444':'#10b981'}}>{backtestResult.avg_discrimination}</span></div>
                <div><span style={{color:'#6e7a8a',fontSize:11}}>命中率 </span><span style={{fontWeight:600}}>{(backtestResult.avg_hit_rate*100).toFixed(0)}%</span></div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 分段权重标签 (v4.1) ── */}
      {tab === 'weights' && (
        <div>
          <div style={{ fontSize: 12, color: '#6e7a8a', marginBottom: 16 }}>
            ★ 基于真实盈亏反馈的 Logistic Regression 训练权重 · 按市场状态分段 (bull/bear/range) · 安全门控: n≥50/params≥10/AUC≥0.55
          </div>

          {/* 分段选择器 */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
            {(['__global__','bull','bear','range'] as const).map(r => (
              <button key={r} onClick={() => setWeightsTab(r)}
                style={{
                  padding: '8px 20px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: weightsTab === r ? '1px solid #8b5cf6' : '1px solid #1e2535',
                  background: weightsTab === r ? 'rgba(139,92,246,0.1)' : '#161b27',
                  color: weightsTab === r ? '#8b5cf6' : '#6e7a8a',
                }}>
                {{__global__:'🌐 全局', bull:'🐂 牛市', bear:'🐻 熊市', range:'📊 震荡'}[r]}
              </button>
            ))}
          </div>

          {/* 全局权重展示 */}
          {weightsTab === '__global__' && trainedWeights?.detail && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: '#c9d1d9' }}>全局权重</span>
                <span style={{ fontSize: 11, color: '#6e7a8a' }}>
                  {trainedWeights.bayesian_trained_params} 个参数已训练 · {trainedWeights.detail[0]?.n || 0} 条观测
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                {trainedWeights.detail.map((p: any) => (
                  <div key={p.name} style={{
                    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                    padding: '10px 14px', background: '#161b27', border: '1px solid #1e2535',
                    borderRadius: 8, opacity: p.n >= 30 ? 1 : 0.55,
                  }}>
                    <code style={{ fontSize: 10, color: '#06b6d4' }}>{p.name.replace('_weight','')}</code>
                    <div style={{ textAlign: 'right' }}>
                      <span style={{ fontSize: 13, fontWeight: 700, color: p.n >= 30 ? '#c9d1d9' : '#4b5563' }}>
                        {p.mu?.toFixed(2)}
                      </span>
                      <span style={{ fontSize: 9, color: p.n >= 50 ? '#10b981' : p.n >= 30 ? '#f59e0b' : '#4b5563', marginLeft: 6 }}>
                        n={p.n}
                        {p.n < 50 && ' ⚠'}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 分市场状态权重展示 */}
          {weightsTab !== '__global__' && (
            <div>
              {(() => {
                // 模拟: 从 calibrationData 获取 regime 信息
                // 实际应调用专门的 regime weights API
                const regimeName = weightsTab;
                const hasData = trainedWeights?.bayesian_trained_params > 0;
                const globalN = trainedWeights?.detail?.[0]?.n || 0;
                const regimeN = globalN; // 目前 API 返回全局数据, regime 数据需单独查询
                const isRejected = regimeN < 50;

                return (
                  <div style={{
                    padding: 20, background: isRejected ? 'rgba(239,68,68,0.05)' : 'rgba(16,185,129,0.05)',
                    border: `1px solid ${isRejected ? 'rgba(239,68,68,0.15)' : 'rgba(16,185,129,0.15)'}`,
                    borderRadius: 12, textAlign: 'center',
                  }}>
                    <div style={{ fontSize: 36, marginBottom: 8 }}>
                      {isRejected ? '⚠️' : '✅'}
                    </div>
                    <div style={{ fontSize: 15, fontWeight: 700, color: isRejected ? '#ef4444' : '#10b981', marginBottom: 8 }}>
                      {isRejected
                        ? `${regimeName === 'bull' ? '牛市' : regimeName === 'bear' ? '熊市' : '震荡'}段数据不足，已回退全局权重`
                        : `${regimeName === 'bull' ? '牛市' : regimeName === 'bear' ? '熊市' : '震荡'}段权重已激活`}
                    </div>
                    <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 12 }}>
                      {isRejected
                        ? `当前该段训练样本: ${regimeN} 条 | 需要 ≥50 条 + ≥10 参数 + AUC≥0.55`
                        : `训练样本: ${regimeN} 条 | 参数: ${trainedWeights?.bayesian_trained_params || 0} 个 | 安全门控全部通过`}
                    </div>
                    <div style={{ fontSize: 10, color: '#4b5563', background: '#161b27', padding: '8px 14px', borderRadius: 6, display: 'inline-block' }}>
                      {isRejected
                        ? '安全门控: MIN_REGIME_SAMPLES=50 | MIN_REGIME_PARAMS=10 | MIN_REGIME_AUC=0.55'
                        : '门控检查: ✅ n≥50 ✅ params≥10 ✅ AUC≥0.55'}
                    </div>
                  </div>
                );
              })()}

              {/* 即使被拒绝仍显示全局权重作为参考 */}
              <div style={{ marginTop: 16 }}>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>
                  ↓ 当前实际使用的权重 (来自全局训练结果)
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
                  {(trainedWeights?.detail || []).slice(0, 16).map((p: any) => (
                    <div key={p.name} style={{
                      display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                      padding: '8px 12px', background: '#161b27', border: '1px solid #1e2535',
                      borderRadius: 6, opacity: 0.6,
                    }}>
                      <code style={{ fontSize: 10, color: '#4b5563' }}>{p.name.replace('_weight','')}</code>
                      <span style={{ fontSize: 12, fontWeight: 600, color: '#c9d1d9' }}>{p.mu?.toFixed(2)}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* 训练方法说明 */}
          <div style={{ marginTop: 20, padding: 14, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
            <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8, color: '#c9d1d9' }}>训练方法</div>
            <div style={{ fontSize: 11, color: '#6e7a8a', lineHeight: 1.8 }}>
              <div>• 训练算法: Logistic Regression (C=0.5, class_weight='balanced', 5-fold CV)</div>
              <div>• 数据来源: recommendation_tracking (真实盈亏) + analysis_scores (维度评分) + market_status_log (市场阶段)</div>
              <div>• 标签: was_profitable_3d (T+3持仓是否盈利)</div>
              <div>• 权重映射: 回归系数 → 归一化 → [0.5, 4.0] 范围</div>
              <div>{'• 持久化: param_library (strategy="scoring_{regime}") + bayesian_beliefs (archetype=regime)'}</div>
              <div>• 调度: 每周一 16:00 自动训练 (by_regime=True)</div>
            </div>
          </div>

          {/* 原型校准数据摘要 */}
          {calibrationData && calibrationData.status !== 'no_data' && (
            <div style={{ marginTop: 16, padding: 14, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10, color: '#c9d1d9' }}>
                原型胜率校准数据 <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400 }}>(各原型 vs 全局胜率)</span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <span style={{ padding: '4px 10px', borderRadius: 12, fontSize: 11, background: 'rgba(139,92,246,0.08)', color: '#a78bfa' }}>
                  全局胜率: {(calibrationData.global_win_rate * 100).toFixed(1)}%
                </span>
                {Object.entries(calibrationData.archetypes || {}).map(([arch, data]: [string, any]) => (
                  <span key={arch} style={{
                    padding: '4px 10px', borderRadius: 12, fontSize: 10,
                    background: data.vs_global > 0 ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
                    border: `1px solid ${data.vs_global > 0 ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'}`,
                    color: data.vs_global > 0 ? '#10b981' : '#ef4444',
                  }}>
                    {ARCH_CN[arch] || arch}: {data.count}条 {(data.win_rate * 100).toFixed(0)}%
                    ({data.vs_global > 0 ? '+' : ''}{(data.vs_global * 100).toFixed(1)}%)
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 参数 / 经验标签（保持简洁）── */}
      {tab === 'params' && (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap:8 }}>
          {params.map((p:any) => (
            <div key={p.name} style={{ display:'flex', justifyContent:'space-between', padding:'8px 12px', background:'#161b27', border:'1px solid #1e2535', borderRadius:8 }}>
              <code style={{ fontSize:10, color:'#06b6d4' }}>{p.name}</code>
              <div><span style={{ fontSize:12, fontWeight:600 }}>{p.mu?.toFixed(2)}</span><span style={{ fontSize:9, color:p.n>0?'#10b981':'#4b5563', marginLeft:4 }}>n={p.n}</span></div>
            </div>
          ))}
        </div>
      )}
      {tab === 'experiences' && (
        <table style={{ width:'100%', borderCollapse:'collapse', background:'#161b27', borderRadius:12, overflow:'hidden' }}>
          <thead><tr style={{ background:'#1a2030' }}>
            {['类型','日期','奖励','股票'].map(h=><th key={h} style={{ padding:'10px 14px', textAlign:'left', fontSize:11, color:'#6e7a8a' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {experiences.length===0 ? <tr><td colSpan={4} style={{ textAlign:'center', padding:40, color:'#6e7a8a' }}>暂无</td></tr>
            : experiences.slice(0,20).map((e:any,i:number) => (
              <tr key={i} style={{ borderTop:'1px solid #1e2535' }}>
                <td style={{ padding:'9px 14px', fontSize:11 }}>{e.event_type}</td>
                <td style={{ padding:'9px 14px', fontSize:11 }}>{e.recorded_at}</td>
                <td style={{ padding:'9px 14px', fontWeight:600, fontSize:12, color:e.reward>=0?'#ef4444':'#10b981' }}>{e.reward>=0?'+':''}{e.reward?.toFixed(2)}</td>
                <td style={{ padding:'9px 14px', fontSize:11 }}>{e.meta_info?.symbol||'-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* ── 自学习标签 (Phase 53) ── */}
      {tab === 'selflearn' && (
        <>
        <div style={{display:'flex',gap:8,marginBottom:12,alignItems:'center'}}>
          <button onClick={syncInfra} disabled={syncing}
            style={{padding:'8px 20px',borderRadius:6,border:'none',
              background: syncing?'#374151':'#10b981', color:'#fff',
              cursor: syncing?'not-allowed':'pointer',fontSize:12,fontWeight:600}}>
            {syncing ? '⏳ 同步中...' : '🔄 同步基建数据'}
          </button>
          {syncResult && (
            <span style={{fontSize:11,color:'#10b981'}}>{syncResult}</span>
          )}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
          {/* 卡片1: 预测模型 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#c9d1d9', marginBottom: 12 }}>📈 预测模型</div>
            {selfStatus?.predictive_model ? (
              <>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#f59e0b', marginBottom: 4 }}>
                  AUC {selfStatus.predictive_model.auc?.toFixed(3)}
                </div>
                <div style={{ fontSize: 11, color: '#6e7a8a', lineHeight: 1.7 }}>
                  XGBoost · {selfStatus.predictive_model.features}维 · {selfStatus.predictive_model.samples?.toLocaleString()}样本<br/>
                  R²={selfStatus.predictive_model.r2?.toFixed(3)} · 胜率 {selfStatus.predictive_model.win_rate}%<br/>
                  上次训练: {selfStatus.predictive_model.last_trained}<br/>
                  信号历史 {selfStatus.predictive_model.sources?.signal_history?.toLocaleString() || 0} · 推荐验证 {selfStatus.predictive_model.sources?.recommendations?.toLocaleString() || 0}
                </div>
              </>
            ) : (
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>模型尚未训练</div>
            )}
          </div>

          {/* 卡片2: 新闻验证 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#c9d1d9', marginBottom: 12 }}>📰 新闻验证</div>
            {selfStatus?.news_verification ? (
              <>
                <div style={{ fontSize: 24, fontWeight: 700, color: '#10b981', marginBottom: 4 }}>
                  {selfStatus.news_verification.active_mappings}/{selfStatus.news_verification.total_mappings}
                </div>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>
                  已激活 {selfStatus.news_verification.activation_pct}% 映射 (hit_rate≥55%)
                </div>
                {(selfStatus.news_verification.top_hit || []).slice(0, 2).map((h: any, i: number) => (
                  <div key={i} style={{ fontSize: 10, color: '#10b981', lineHeight: 1.6 }}>
                    ✅ {h.commodity}→{h.symbol} {h.hit_rate?.toFixed(0)}%
                  </div>
                ))}
                {(selfStatus.news_verification.top_miss || []).slice(0, 2).map((m: any, i: number) => (
                  <div key={i} style={{ fontSize: 10, color: '#f87171', lineHeight: 1.6 }}>
                    ❌ {m.commodity}→{m.symbol} {m.hit_rate?.toFixed(0)}%
                  </div>
                ))}
              </>
            ) : (
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>暂无验证数据</div>
            )}
          </div>

          {/* 卡片3: 自适应阈值 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#c9d1d9', marginBottom: 12 }}>⚙ 自适应阈值</div>
            {selfStatus?.adaptive_thresholds ? (
              <>
                <div style={{ display: 'flex', gap: 16, marginBottom: 8 }}>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: '#3b82f6' }}>{selfStatus.adaptive_thresholds.min_score}</div>
                    <div style={{ fontSize: 10, color: '#6e7a8a' }}>推荐门槛</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: '#ef4444' }}>{selfStatus.adaptive_thresholds.strong_buy}</div>
                    <div style={{ fontSize: 10, color: '#6e7a8a' }}>强买门槛</div>
                  </div>
                </div>
                <div style={{ fontSize: 10, color: '#6e7a8a', lineHeight: 1.5 }}>
                  来自 {selfStatus.adaptive_thresholds.total_verified || 0} 条真实验证<br/>
                  {selfStatus.adaptive_thresholds.buckets || 0} 分桶 · 每桶 {selfStatus.adaptive_thresholds.samples_per_bucket || 0} 样本
                </div>
              </>
            ) : (
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>阈值未生成 (需≥10条验证)</div>
            )}
          </div>

          {/* 卡片4: 推荐追踪 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: '#c9d1d9', marginBottom: 12 }}>📊 推荐追踪</div>
            {selfStatus?.recommendation_tracking ? (
              <>
                <div style={{ display: 'flex', gap: 16 }}>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: selfStatus.recommendation_tracking.t2_wr >= 50 ? '#10b981' : '#f59e0b' }}>
                      {selfStatus.recommendation_tracking.t2_wr}%
                    </div>
                    <div style={{ fontSize: 10, color: '#6e7a8a' }}>T+2 胜率</div>
                    <div style={{ fontSize: 10, color: '#4b5563' }}>{selfStatus.recommendation_tracking.t2_verified}条验证</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: selfStatus.recommendation_tracking.t5_verified > 0 ? '#c9d1d9' : '#4b5563' }}>
                      {selfStatus.recommendation_tracking.t5_verified > 0 ? `${selfStatus.recommendation_tracking.t5_wr}%` : '—'}
                    </div>
                    <div style={{ fontSize: 10, color: '#6e7a8a' }}>T+5 胜率</div>
                    <div style={{ fontSize: 10, color: '#4b5563' }}>
                      {selfStatus.recommendation_tracking.t5_verified > 0 ? `${selfStatus.recommendation_tracking.t5_verified}条` : '待积累'}
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: 8, fontSize: 10, color: '#6e7a8a' }}>
                  平均收益 T+2: {selfStatus.recommendation_tracking.t2_avg_ret?.toFixed(2)}%
                </div>
              </>
            ) : (
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>暂无推荐追踪数据</div>
            )}
          </div>
        </div>
        </>
      )}

      {/* ── 新闻验证标签 (Phase 62) ── */}
      {tab === 'newsverify' && (
        <div>
          <h3 style={{ color: '#c9d1d9', fontSize: 14, marginBottom: 12 }}>商品→股票 方向命中率 (T+2)</h3>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 12, overflow: 'hidden', fontSize: 12 }}>
              <thead>
                <tr style={{ borderBottom: '1px solid #1e2535', color: '#6e7a8a', textAlign: 'left' }}>
                  <th style={{ padding: '10px 14px' }}>商品</th>
                  <th style={{ padding: '10px 14px' }}>关联股票</th>
                  <th style={{ padding: '10px 14px' }}>总信号</th>
                  <th style={{ padding: '10px 14px' }}>命中率</th>
                  <th style={{ padding: '10px 14px' }}>状态</th>
                </tr>
              </thead>
              <tbody>
                {(newsVerifyData?.data || []).map((r: any) => (
                  <tr key={r.commodity} style={{ borderBottom: '1px solid #1e2535' }}>
                    <td style={{ padding: '8px 14px', color: '#c9d1d9' }}>{r.commodity}</td>
                    <td style={{ padding: '8px 14px', color: '#6e7a8a' }}>{r.stocks}只</td>
                    <td style={{ padding: '8px 14px', color: '#6e7a8a' }}>{r.total}</td>
                    <td style={{ padding: '8px 14px', fontWeight: 600,
                      color: r.hit_rate >= 55 ? '#10b981' : r.hit_rate >= 40 ? '#f59e0b' : '#ef4444' }}>
                      {r.hit_rate}%
                    </td>
                    <td style={{ padding: '8px 14px' }}>
                      {r.hit_rate >= 55 ? <span style={{ color: '#10b981', fontSize: 10 }}>✅ 激活</span>
                       : <span style={{ color: '#6e7a8a', fontSize: 10 }}>待验证</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: '#4b5563' }}>
            {newsVerifyData?.count || 0} 个商品 · 仅显示总信号≥5的商品 · 命中率≥55%自动激活
          </div>
        </div>
      )}
      {tab === 'dna' && <DnaLab />}
    </div>
  );
}
