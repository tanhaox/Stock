import { useEffect, useState, useRef } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import api from '../lib/api';

const ARCH_LABELS: Record<string, string> = {
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

const SIGNAL_COLORS: Record<string, string> = {
  valuation: '#f59e0b', financial_risk: '#ef4444', fund_flow: '#f97316',
  technical_risk: '#eab308', sentiment_risk: '#ec4899',
  opportunity: '#10b981', financial: '#06b6d4', other: '#6b7280',
};

type StockResult = {
  symbol: string; name: string; status: 'success' | 'error';
  positive_signals?: any[]; negative_signals?: any[];
  error?: string;
};

export default function DeepAnalysisPage() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [phase, setPhase] = useState<'loading' | 'analyzing' | 'done' | 'partial' | 'error'>('loading');
  const [individual, setIndividual] = useState<StockResult[]>(() => {
    // ★ 修复: 页面跳转后恢复分析结果 (localStorage 持久化)
    try { const saved = localStorage.getItem('deep_analysis_individual'); return saved ? JSON.parse(saved) : []; } catch { return []; }
  });
  const [batchScores, setBatchScores] = useState<Record<string, any>>(() => {
    try { const saved = localStorage.getItem('deep_analysis_batch'); return saved ? JSON.parse(saved) : {}; } catch { return {}; }
  });
  const [completed, setCompleted] = useState(() => {
    try { return parseInt(localStorage.getItem('deep_analysis_completed') || '0'); } catch { return 0; }
  });
  const [activeTab, setActiveTab] = useState(0);
  const [elapsed, setElapsed] = useState(0);
  const [retrying, setRetrying] = useState<Set<string>>(new Set());
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const timerRef = useRef<any>(null);

  // ★ 持久化辅助函数
  const persistResults = (indiv: StockResult[], batch: Record<string, any>, comp: number, ph: string) => {
    try {
      localStorage.setItem('deep_analysis_individual', JSON.stringify(indiv));
      localStorage.setItem('deep_analysis_batch', JSON.stringify(batch));
      localStorage.setItem('deep_analysis_completed', String(comp));
      localStorage.setItem('deep_analysis_phase', ph);
      localStorage.setItem('deep_analysis_symbols', indiv.map(r => r.symbol).join(','));
    } catch {}
  };

  useEffect(() => {
    const syms = searchParams.get('symbols');

    // 尝试从 localStorage 恢复（在任何情况下都优先检查）
    const savedPhase = localStorage.getItem('deep_analysis_phase');
    const savedSymbols = localStorage.getItem('deep_analysis_symbols');
    const savedIndividual = localStorage.getItem('deep_analysis_individual');
    const savedBatchScores = localStorage.getItem('deep_analysis_batch');
    const savedCompleted = localStorage.getItem('deep_analysis_completed');

    const hasSavedResults = savedPhase && (savedPhase === 'done' || savedPhase === 'partial');

    if (syms && hasSavedResults && savedSymbols === syms) {
      // 恢复完全匹配的缓存 → 不重新分析
      setSymbols(syms.split(','));
      if (savedIndividual) setIndividual(JSON.parse(savedIndividual));
      if (savedBatchScores) setBatchScores(JSON.parse(savedBatchScores));
      if (savedCompleted) setCompleted(parseInt(savedCompleted));
      setPhase(savedPhase as any);
      return;
    }

    if (syms) {
      // 新的URL参数 → 清空缓存，开始新分析
      localStorage.removeItem('deep_analysis_phase');
      localStorage.removeItem('deep_analysis_symbols');
      localStorage.removeItem('deep_analysis_individual');
      localStorage.removeItem('deep_analysis_batch');
      localStorage.removeItem('deep_analysis_completed');
      const arr = syms.split(',').filter(s => s.match(/\d{6}\.(SH|SZ|BJ)/));
      if (arr.length === 0) { navigate('/analysis'); return; }
      setSymbols(arr);
      startAutoAnalyze(arr);
      return;
    }

    // 无URL参数 → 尝试从缓存恢复
    if (hasSavedResults && savedSymbols) {
      setSymbols(savedSymbols.split(','));
      if (savedIndividual) setIndividual(JSON.parse(savedIndividual));
      if (savedBatchScores) setBatchScores(JSON.parse(savedBatchScores));
      if (savedCompleted) setCompleted(parseInt(savedCompleted));
      setPhase(savedPhase as any);
      return;
    }

    // 无URL参数且无缓存 → 回到选择页
    navigate('/analysis');
  }, []);

  useEffect(() => { return () => { if (timerRef.current) clearInterval(timerRef.current); }; }, []);

  const startAutoAnalyze = async (syms: string[]) => {
    setPhase('analyzing');
    setElapsed(0);
    setCompleted(0);
    setIndividual([]);
    timerRef.current = setInterval(() => setElapsed(e => e + 1), 1000);

    try {
      const response = await fetch('/api/llm/auto-analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: syms }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response stream');

      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'progress') {
                setCompleted(event.completed);
                setIndividual(prev => [...prev, event.result]);
              } else if (event.type === 'done') {
                setIndividual(event.individual);
                setBatchScores(event.batch_scores || {});
                const failures = (event.individual || []).filter((d: any) => d.status === 'error');
                const successes = (event.individual || []).filter((d: any) => d.status === 'success');
                const finalPhase = successes.length === 0 && failures.length > 0 ? 'error' : failures.length > 0 ? 'partial' : 'done';
                setPhase(finalPhase);
                // ★ 持久化: 页面跳转后可恢复
                persistResults(event.individual, event.batch_scores || {}, event.individual?.length || 0, finalPhase);
              }
            } catch {}
          }
        }
      }
    } catch (e: any) {
      setPhase('error');
    }
    if (timerRef.current) clearInterval(timerRef.current);
  };

  const fmtTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return m > 0 ? `${m}分${sec}秒` : `${sec}秒`;
  };

  // 单只重试
  const retryOne = async (symbol: string) => {
    setRetrying(prev => { const n = new Set(prev); n.add(symbol); return n; });
    try {
      const res = await api.post('/llm/retry-one', { symbol });
      const result = res.data;
      setIndividual(prev => prev.map(r =>
        r.symbol === symbol ? { ...result, status: result.status || 'error' } : r
      ));
      // 重算 phase
      setIndividual(prev => {
        const failures = prev.filter(d => d.status === 'error');
        const successes = prev.filter(d => d.status === 'success');
        if (successes.length > 0 && failures.length === 0) setPhase('done');
        else if (successes.length > 0) setPhase('partial');
        return prev;
      });
    } catch {}
    setRetrying(prev => { const n = new Set(prev); n.delete(symbol); return n; });
  };

  const successCount = individual.filter(r => r.status === 'success').length;
  const failCount = individual.filter(r => r.status === 'error').length;

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <h2 style={{ margin: 0 }}>第四步：LLM深度分析</h2>
          {phase === 'analyzing' && (
            <span style={{ fontSize: 12, fontWeight: 600, padding: '3px 10px', borderRadius: 12,
              background: 'rgba(139,92,246,0.12)', color: '#a78bfa' }}>
              ⏳ {completed}/{symbols.length} 已接收 · {fmtTime(elapsed)}
            </span>
          )}
          {(phase === 'done' || phase === 'partial') && (
            <span style={{ fontSize: 12, fontWeight: 600, padding: '3px 10px', borderRadius: 12,
              background: successCount === individual.length ? 'rgba(16,185,129,0.12)' : 'rgba(245,158,11,0.1)',
              color: successCount === individual.length ? '#10b981' : '#f59e0b' }}>
              ✓ {successCount}/{individual.length} 完成{failCount > 0 ? ` (${failCount} 失败)` : ''}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {(phase === 'done' || phase === 'partial') && (
            <button onClick={() => startAutoAnalyze(symbols)}
              style={{ padding: '8px 16px', border: '1px solid #8b5cf6', borderRadius: 8, fontSize: 12, fontWeight: 600,
                cursor: 'pointer', background: 'rgba(139,92,246,0.08)', color: '#a78bfa' }}>
              🔄 重新分析
            </button>
          )}
          <button onClick={() => {
            const today = new Date().toISOString().slice(0, 10);
            navigate(`/result?symbols=${symbols.join(',')}&date=${today}`);
          }}
            style={{ padding: '8px 20px', border: '1px solid #3b82f6', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: 'pointer', background: 'rgba(59,130,246,0.1)', color: '#3b82f6' }}>
            下一步 → 最终结果
          </button>
        </div>
      </div>

      {/* 分析中 — 实时进度 */}
      {phase === 'analyzing' && (
        <div style={{ textAlign: 'center', padding: '40px 20px', color: '#6e7a8a' }}>
          <div style={{ fontSize: 48, marginBottom: 16 }}>🤖</div>
          <div style={{ fontSize: 16, fontWeight: 600, color: '#c9d1d9', marginBottom: 8 }}>
            正在调用 DeepSeek API 分析 {symbols.length} 只股票
          </div>
          {/* 进度条 */}
          <div style={{ maxWidth: 400, margin: '0 auto 16px', height: 6, background: '#1e2535', borderRadius: 3 }}>
            <div style={{ height: '100%', width: `${(completed/symbols.length)*100}%`,
              background: 'linear-gradient(90deg, #8b5cf6, #06b6d4)', borderRadius: 3, transition: 'width .3s' }} />
          </div>
          <div style={{ fontSize: 24, fontWeight: 700, color: '#a78bfa', marginBottom: 4 }}>
            {completed} <span style={{ fontSize: 14, color: '#6e7a8a' }}>/ {symbols.length} 已接收</span>
          </div>
          <div style={{ fontSize: 12, color: '#6e7a8a', marginBottom: 20 }}>已耗时 {fmtTime(elapsed)}</div>
          {/* 实时流入的个股状态 */}
          {individual.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'center', maxWidth: 600, margin: '0 auto' }}>
              {individual.map((r, i) => (
                <span key={r.symbol} style={{
                  padding: '3px 10px', borderRadius: 6, fontSize: 11, fontWeight: 500,
                  background: r.status === 'success' ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
                  color: r.status === 'success' ? '#10b981' : '#ef4444',
                  border: `1px solid ${r.status === 'success' ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'}`,
                }}>
                  {r.status === 'success' ? '✓' : '✗'} {r.symbol}
                </span>
              ))}
              {completed < symbols.length && <span style={{ fontSize: 11, color: '#4b5563', padding: '3px 0' }}>等待中...</span>}
            </div>
          )}
          <div style={{ fontSize: 12, lineHeight: 1.8, marginTop: 20 }}>
            系统自动完成：生成提示词 → 调用AI分析 → 解析信号 → 存储反哺 → 横向对比<br/>
            <span style={{ color: '#4b5563' }}>并发数: 20 · 预计 {symbols.length} 只约需 30-60 秒</span>
          </div>
        </div>
      )}

      {/* 部分失败 — 显示结果 + 警告 */}
      {phase === 'partial' && (
        <div style={{ marginBottom: 16, padding: '10px 16px', borderRadius: 8,
          background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
          <span style={{ color: '#f59e0b', fontSize: 13 }}>
            ⚠ {individual.filter((d: any) => d.status === 'error').length}/{individual.length} 只分析失败，已展示成功结果。
          </span>
          <button onClick={() => setPhase('done')}
            style={{ marginLeft: 12, padding: '2px 12px', borderRadius: 4, border: '1px solid #f59e0b',
              background: 'transparent', color: '#f59e0b', cursor: 'pointer', fontSize: 11 }}>
            忽略警告
          </button>
        </div>
      )}

      {/* 错误 */}
      {phase === 'error' && (
        <div style={{ textAlign: 'center', padding: 60, color: '#ef4444' }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>⚠️</div>
          <div style={{ fontSize: 14, marginBottom: 16 }}>分析过程中出现错误，请检查 DeepSeek API 配置</div>
          <button onClick={() => startAutoAnalyze(symbols)}
            style={{ padding: '8px 24px', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: 'pointer', background: '#8b5cf6', color: '#fff' }}>
            重试
          </button>
        </div>
      )}

      {/* 完成 — 结果展示 */}
      {(phase === 'done' || phase === 'partial') && individual.length > 0 && (
        <div>
          {/* 横向对比概览 */}
          {Object.keys(batchScores).length >= 2 && (
            <div style={{ marginBottom: 20, padding: 16, background: 'rgba(59,130,246,0.05)',
              border: '1px solid rgba(59,130,246,0.12)', borderRadius: 12 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#3b82f6', marginBottom: 12 }}>横向对比排名 <span style={{fontSize:10,color:'#6e7a8a',fontWeight:400}}>👇 点击卡片跳转详情</span></div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                {Object.entries(batchScores)
                  .sort(([, a]: any, [, b]: any) => (b?.short || 0) - (a?.short || 0))
                  .map(([sym, score]: any, i) => {
                    const tabIdx = individual.findIndex(s => s.symbol === sym);
                    const isActive = tabIdx === activeTab;
                    return (
                    <div key={sym} onClick={() => tabIdx >= 0 && setActiveTab(tabIdx)} style={{
                      flex: '1 1 200px', minWidth: 180, padding: 12, borderRadius: 8, cursor: tabIdx >= 0 ? 'pointer' : 'default',
                      background: isActive ? 'rgba(139,92,246,0.12)' : i === 0 ? 'rgba(16,185,129,0.08)' : 'rgba(30,37,53,0.6)',
                      border: isActive ? '1px solid rgba(139,92,246,0.3)' : i === 0 ? '1px solid rgba(16,185,129,0.2)' : '1px solid #1e2535',
                      transition: 'all .15s',
                    }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: '#c9d1d9' }}>
                        {i === 0 && '🥇'}{i === 1 && '🥈'}{i === 2 && '🥉'} {(() => { const st = individual.find(s => s.symbol === sym); return st ? `${st.name} (${sym})` : sym; })()}
                      </div>
                      <div style={{ display: 'flex', gap: 12, marginTop: 6 }}>
                        <div><span style={{ fontSize: 10, color: '#6e7a8a' }}>短期 </span>
                          <span style={{ fontSize: 16, fontWeight: 700, color: score?.short >= 7 ? '#10b981' : score?.short >= 5 ? '#f59e0b' : '#ef4444' }}>{score?.short ?? '?'}</span>
                        </div>
                        <div><span style={{ fontSize: 10, color: '#6e7a8a' }}>中期 </span>
                          <span style={{ fontSize: 16, fontWeight: 700, color: score?.mid >= 7 ? '#10b981' : score?.mid >= 5 ? '#f59e0b' : '#ef4444' }}>{score?.mid ?? '?'}</span>
                        </div>
                      </div>
                      {score?.short_note && <div style={{ fontSize: 10, color: '#9ca3af', marginTop: 4 }}>短线: {score.short_note}</div>}
                      {score?.mid_note && <div style={{ fontSize: 10, color: '#9ca3af' }}>中线: {score.mid_note}</div>}
                    </div>
                    );
                  })}
              </div>
            </div>
          )}

          {/* Tab 导航 — 逐只查看信号详情 */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 16, flexWrap: 'wrap', borderBottom: '1px solid #1e2535', paddingBottom: 8 }}>
            {individual.map((r, i) => (
              <button key={i} onClick={() => setActiveTab(i)}
                style={{
                  padding: '6px 14px', borderRadius: '6px 6px 0 0', fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: 'none', borderBottom: activeTab === i ? '2px solid #8b5cf6' : '2px solid transparent',
                  background: activeTab === i ? 'rgba(139,92,246,0.08)' : 'transparent',
                  color: activeTab === i ? '#c9d1d9' : '#6e7a8a',
                }}>
                {r.status === 'success' ? '✅' : '❌'} {r.name || r.symbol} <span style={{color:'#4b5563',fontSize:10}}>{r.symbol}</span>
                {r.status === 'error' && (
                  <span onClick={(e) => { e.stopPropagation(); retryOne(r.symbol); }}
                    title="重试此股票"
                    style={{ marginLeft: 6, cursor: retrying.has(r.symbol) ? 'not-allowed' : 'pointer',
                      opacity: retrying.has(r.symbol) ? 0.4 : 0.7, fontSize: 12 }}>
                    {retrying.has(r.symbol) ? '⏳' : '🔄'}
                  </span>
                )}
              </button>
            ))}
          </div>

          {/* 当前 Tab 信号详情 */}
          {individual[activeTab] && (
            <div>
              {individual[activeTab].status === 'error' ? (
                <div style={{ padding: 40, textAlign: 'center', color: '#ef4444', fontSize: 13 }}>
                  ⚠️ 分析失败: {individual[activeTab].error || '未知错误'}
                  <div style={{ marginTop: 14 }}>
                    <button onClick={() => retryOne(individual[activeTab].symbol)}
                      disabled={retrying.has(individual[activeTab].symbol)}
                      style={{ padding: '8px 24px', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600,
                        cursor: retrying.has(individual[activeTab].symbol) ? 'not-allowed' : 'pointer',
                        background: retrying.has(individual[activeTab].symbol) ? '#374151' : '#ef4444',
                        color: '#fff' }}>
                      {retrying.has(individual[activeTab].symbol) ? '⏳ 重试中...' : '🔄 重试此股票'}
                    </button>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  {/* 负面信号 */}
                  <div style={{ padding: 14, borderRadius: 10, background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.12)' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#ef4444', marginBottom: 10 }}>
                      ⚠ 负面信号 ({(individual[activeTab].negative_signals || []).length})
                    </div>
                    {(individual[activeTab].negative_signals || []).length === 0 ? (
                      <div style={{ fontSize: 12, color: '#6e7a8a' }}>无显著负面信号</div>
                    ) : (
                      (individual[activeTab].negative_signals || []).map((s: any, j: number) => (
                        <div key={j} style={{ marginBottom: 8, padding: '8px 10px', borderRadius: 6, background: 'rgba(239,68,68,0.06)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: (SIGNAL_COLORS[s.type] || '#6b7280') + '22',
                              color: SIGNAL_COLORS[s.type] || '#6b7280' }}>{s.type}</span>
                            <span style={{ fontSize: 10, color: '#6e7a8a' }}>置信度: {(s.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <div style={{ fontSize: 12, color: '#c9d1d9', lineHeight: 1.5 }}>{s.description}</div>
                        </div>
                      ))
                    )}
                  </div>

                  {/* 正面信号 */}
                  <div style={{ padding: 14, borderRadius: 10, background: 'rgba(16,185,129,0.04)', border: '1px solid rgba(16,185,129,0.12)' }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: '#10b981', marginBottom: 10 }}>
                      ✓ 正面信号 ({(individual[activeTab].positive_signals || []).length})
                    </div>
                    {(individual[activeTab].positive_signals || []).length === 0 ? (
                      <div style={{ fontSize: 12, color: '#6e7a8a' }}>无显著正面信号</div>
                    ) : (
                      (individual[activeTab].positive_signals || []).map((s: any, j: number) => (
                        <div key={j} style={{ marginBottom: 8, padding: '8px 10px', borderRadius: 6, background: 'rgba(16,185,129,0.06)' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                            <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: (SIGNAL_COLORS[s.type] || '#6b7280') + '22',
                              color: SIGNAL_COLORS[s.type] || '#6b7280' }}>{s.type}</span>
                            <span style={{ fontSize: 10, color: '#6e7a8a' }}>置信度: {(s.confidence * 100).toFixed(0)}%</span>
                          </div>
                          <div style={{ fontSize: 12, color: '#c9d1d9', lineHeight: 1.5 }}>{s.description}</div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              )}

              {/* 批量对比中该股票的评分 */}
              {batchScores[individual[activeTab].symbol] && (
                <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'rgba(59,130,246,0.04)', border: '1px solid rgba(59,130,246,0.1)' }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: '#3b82f6', marginBottom: 6 }}>横向对比评分</div>
                  <div style={{ display: 'flex', gap: 20, fontSize: 12 }}>
                    <div>短期: <b style={{ color: '#c9d1d9' }}>{batchScores[individual[activeTab].symbol]?.short ?? '?'}/10</b>
                      <span style={{ fontSize: 10, color: '#9ca3af', marginLeft: 6 }}>{batchScores[individual[activeTab].symbol]?.short_note || ''}</span>
                    </div>
                    <div>中期: <b style={{ color: '#c9d1d9' }}>{batchScores[individual[activeTab].symbol]?.mid ?? '?'}/10</b>
                      <span style={{ fontSize: 10, color: '#9ca3af', marginLeft: 6 }}>{batchScores[individual[activeTab].symbol]?.mid_note || ''}</span>
                    </div>
                    {batchScores[individual[activeTab].symbol]?.support && (
                      <div>支撑: <b style={{ color: '#10b981' }}>{batchScores[individual[activeTab].symbol].support}</b></div>
                    )}
                    {batchScores[individual[activeTab].symbol]?.resistance && (
                      <div>压力: <b style={{ color: '#ef4444' }}>{batchScores[individual[activeTab].symbol].resistance}</b></div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* 流程说明 */}
          <div style={{ marginTop: 24, padding: 14, background: 'rgba(139,92,246,0.04)', border: '1px solid rgba(139,92,246,0.1)', borderRadius: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: '#a78bfa', marginBottom: 6 }}>自动化流程已完成</div>
            <div style={{ fontSize: 11, color: '#6e7a8a', lineHeight: 1.8 }}>
              ✓ 系统生成提示词 → ✓ DeepSeek API 分析 → ✓ 信号提取 → ✓ 反哺存储 (stock_deep_feedback + experience_replay) → ✓ 横向对比评分<br/>
              点击「下一步 → 最终结果」查看综合推荐排名
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
