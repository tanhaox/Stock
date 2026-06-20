import { useEffect, useState } from 'react';
import api from '../lib/api';

/** 自动补全股票代码后缀: 6开头→.SH, 0/3开头→.SZ */
function normalizeSymbol(raw: string): string {
  const s = raw.trim();
  if (!s) return s;
  if (s.includes('.')) return s; // already has suffix
  if (s.startsWith('6')) return s + '.SH';
  if (s.startsWith('0') || s.startsWith('3')) return s + '.SZ';
  return s;
}

interface DnaStatus {
  models_trained: number;
  total_samples: number;
  avg_auc_t5: number;
  horizon_distribution: Record<string, number>;
  regular_cycles: number;
  irregular_cycles: number;
}

interface DnaCard {
  symbol: string;
  best_window: { horizon: number; auc: number; all_aucs: Record<string, number> };
  emotion_fingerprint: { n_emotions: number; entropy: number; best_emotion: number; best_emotion_ret: number; names: Record<string, string> };
  cycle_rhythm: { avg_lockup_days: number; std_lockup_days: number; cv: number; avg_breakout_return: number; avg_breakout_days: number };
  drivers: { name: string; importance: number }[];
  behavior: { crash_resilience: number; rally_capture: number; deception_rate: number; consistency: number; extreme_tail: number };
  meta: { training_samples: number; archetype: string; last_trained: string; last_dna_update: string };
}

interface EmotionHistory {
  symbol: string;
  emotion_sequence: { date: string; emotion: number; emotion_name: string }[];
  transition_tomorrow: {
    most_likely: { emotion: number; name: string; prob: number };
    best_case: { emotion: number; name: string; prob: number };
    worst_case: { emotion: number; name: string; prob: number };
  } | null;
}

interface CompareData {
  comparison: { symbol: string; best_horizon: number; auc: number; cycle_cv: number; avg_lockup: number }[];
  similarity_matrix: { symbols: string[]; matrix: number[][] };
}

export default function DnaLab() {
  const [subtab, setSubtab] = useState<'overview' | 'profile' | 'compare' | 'emotion'>('overview');
  const [status, setStatus] = useState<DnaStatus | null>(null);
  const [searchSym, setSearchSym] = useState('');
  const [profile, setProfile] = useState<DnaCard | null>(null);
  const [compareData, setCompareData] = useState<CompareData | null>(null);
  const [emotionHistory, setEmotionHistory] = useState<EmotionHistory | null>(null);
  const [emotionSym, setEmotionSym] = useState('');
  const [building, setBuilding] = useState(false);
  const [training, setTraining] = useState(false);
  const [msg, setMsg] = useState('');
  const [modeledSymbols, setModeledSymbols] = useState<string[]>([]);
  const [addingStock, setAddingStock] = useState(false);
  const [newStockCode, setNewStockCode] = useState('');
  const [stockList, setStockList] = useState<any[]>([]);

  useEffect(() => { loadStatus(); loadModeledList(); }, []);

  const loadModeledList = async () => {
    try { const r = await api.get('/dna/compare'); setModeledSymbols(r.data.comparison?.map((c: any) => c.symbol) || []); setStockList(r.data.comparison || []); } catch {}
  };

  const loadStatus = async () => {
    try { const r = await api.get('/dna/status'); setStatus(r.data); } catch {}
  };

  const loadProfile = async (sym: string) => {
    try { const r = await api.get(`/dna/profile/${sym}`); setProfile(r.data.data); } catch { setMsg('未找到 DNA'); }
  };

  const loadCompare = async () => {
    try { const r = await api.get('/dna/compare'); setCompareData(r.data); } catch {}
  };

  const loadEmotionHistory = async (sym: string) => {
    try { const r = await api.get(`/dna/emotion/${sym}/history`, { params: { days: 60 } }); setEmotionHistory(r.data); } catch {}
  };

  const runScan = async () => {
    setMsg('运行中...');
    try { const r = await api.post('/dna/scan'); setMsg(`完成: ${r.data.predictions_written} 条预测`); } catch { setMsg('失败'); }
  };

  const addStock = async () => {
    const code = normalizeSymbol(newStockCode.trim());
    if (!code) { setMsg('请输入股票代码'); return; }
    setAddingStock(true); setMsg(`正在添加 ${code} ...`);
    try {
      const r = await api.post('/dna/add-stock', null, { params: { symbol: code } });
      if (r.data.status === 'success') {
        setMsg(`✅ ${code} 添加完成! ${r.data.samples} 样本, AUC_T5=${r.data.auc_t5?.toFixed(3)}`);
        setNewStockCode('');
        loadStatus(); loadModeledList();
      } else {
        setMsg(`❌ ${r.data.detail || '添加失败'}`);
      }
    } catch (e: any) { setMsg('❌ ' + (e?.response?.data?.detail || '请求失败')); }
    setAddingStock(false);
  };

  const cvLevel = (cv: number) => cv < 0.3 ? '🟢 规律' : cv < 0.6 ? '🟡 中等' : cv >= 999 ? '⚪ 无数据' : '🔴 随机';
  const cvColor = (cv: number) => cv < 0.3 ? '#10b981' : cv < 0.6 ? '#f59e0b' : '#6e7a8a';

  const tabs = [
    { key: 'overview' as const, label: '📊 概览' },
    { key: 'profile' as const, label: '🧬 单股档案' },
    { key: 'compare' as const, label: '🔗 对比矩阵' },
    { key: 'emotion' as const, label: '😊 表情历史' },
  ];

  return (
    <div>
      {/* Sub-tabs */}
      <div style={{ display: 'flex', gap: 6, marginBottom: 20 }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setSubtab(t.key)}
            style={{ padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
              border: subtab === t.key ? '1px solid #8b5cf6' : '1px solid #1e2535',
              background: subtab === t.key ? 'rgba(139,92,246,0.1)' : 'transparent',
              color: subtab === t.key ? '#8b5cf6' : '#6e7a8a' }}>
            {t.label}
          </button>
        ))}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <button onClick={async () => { setBuilding(true); setMsg('数据生成中...'); try { await api.post('/dna/scan'); setMsg('✅ 完成'); } catch { setMsg('❌ 失败'); } setBuilding(false); }}
            disabled={building}
            style={{ padding: '6px 14px', borderRadius: 6, fontSize: 11, cursor: building ? 'not-allowed' : 'pointer',
              border: '1px solid #10b981', background: 'transparent', color: '#10b981', opacity: building ? 0.5 : 1 }}>
            {building ? '⏳' : '🔬'} DNA 评分
          </button>
          <button onClick={runScan}
            style={{ padding: '6px 14px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
              border: '1px solid #f59e0b', background: 'transparent', color: '#f59e0b' }}>
            📝 写入预测
          </button>
          {addingStock ? (
            <>
              <input value={newStockCode} onChange={e => setNewStockCode(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addStock()}
                placeholder="输入代码, 如 000001"
                autoFocus
                style={{ width: 140, padding: '5px 10px', borderRadius: 4, border: '1px solid #8b5cf6', background: '#0d1117', color: '#cbd5e1', fontSize: 11 }} />
              <button onClick={addStock} disabled={addingStock}
                style={{ padding: '5px 12px', borderRadius: 4, border: 'none', background: '#8b5cf6', color: '#fff', cursor: 'pointer', fontSize: 11, whiteSpace: 'nowrap' }}>
                {addingStock ? '⏳' : '确认'}
              </button>
              <button onClick={() => { setAddingStock(false); setNewStockCode(''); }}
                style={{ padding: '5px 8px', borderRadius: 4, border: '1px solid #374151', background: 'transparent', color: '#6e7a8a', cursor: 'pointer', fontSize: 11 }}>
                ✕
              </button>
            </>
          ) : (
            <button onClick={() => setAddingStock(true)}
              style={{ padding: '6px 14px', borderRadius: 6, fontSize: 11, cursor: 'pointer',
                border: '1px solid #8b5cf6', background: 'rgba(139,92,246,0.08)', color: '#a78bfa', whiteSpace: 'nowrap' }}>
              ➕ 新增股票
            </button>
          )}
        </div>
      </div>

      {msg && <div style={{ marginBottom: 12, padding: '6px 12px', borderRadius: 6, background: 'rgba(245,158,11,0.08)', fontSize: 11, color: '#f59e0b' }}>{msg}</div>}

      {/* ── 概览 ── */}
      {subtab === 'overview' && status && (
        <div>
          <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
            {[
              { v: status.models_trained, label: '已建模', color: '#8b5cf6' },
              { v: status.total_samples?.toLocaleString(), label: '训练样本', color: '#10b981' },
              { v: status.avg_auc_t5?.toFixed(3), label: '平均 AUC', color: '#f59e0b' },
              { v: status.regular_cycles, label: '规律型', color: '#3b82f6' },
            ].map((m, i) => (
              <div key={i} style={{ flex: 1, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, textAlign: 'center' }}>
                <div style={{ fontSize: 24, fontWeight: 700, color: m.color }}>{m.v}</div>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginTop: 4 }}>{m.label}</div>
              </div>
            ))}
          </div>

          {/* 最佳窗口分布 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#cbd5e1', marginBottom: 12 }}>最佳操作窗口分布</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {Object.entries(status.horizon_distribution || {}).map(([k, v]) => (
                <div key={k} style={{ flex: 1, textAlign: 'center', padding: '10px 0', borderRadius: 8, background: 'rgba(139,92,246,0.05)' }}>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#a78bfa' }}>{v as number}</div>
                  <div style={{ fontSize: 10, color: '#6e7a8a', marginTop: 2 }}>{k}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 实验室内股票列表 */}
          <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#cbd5e1', marginBottom: 12 }}>
              实验室内股票 ({stockList.length} 只)
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ color: '#6e7a8a', textAlign: 'left', borderBottom: '1px solid #1e2535' }}>
                  <th style={{ padding: '6px 8px' }}>股票</th>
                  <th style={{ padding: '6px 8px' }}>样本</th>
                  <th style={{ padding: '6px 8px' }}>最佳窗口</th>
                  <th style={{ padding: '6px 8px' }}>AUC</th>
                  <th style={{ padding: '6px 8px' }}>周期CV</th>
                  <th style={{ padding: '6px 8px' }}>节律</th>
                </tr>
              </thead>
              <tbody>
                {stockList.map((s, i) => (
                  <tr key={s.symbol}
                    onClick={() => { setSearchSym(s.symbol); setSubtab('profile'); loadProfile(s.symbol); }}
                    style={{ borderBottom: '1px solid #1e2535', cursor: 'pointer' }}
                    onMouseEnter={e => { e.currentTarget.style.background = 'rgba(139,92,246,0.06)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}>
                    <td style={{ padding: '6px 8px', color: '#a78bfa', fontWeight: 600 }}>{s.symbol.replace('.SZ','').replace('.SH','')}</td>
                    <td style={{ padding: '6px 8px', color: '#cbd5e1' }}>{s.training_samples || '-'}</td>
                    <td style={{ padding: '6px 8px', color: '#10b981' }}>T+{s.best_horizon || '-'}</td>
                    <td style={{ padding: '6px 8px', color: (s.auc || 0) > 0.6 ? '#10b981' : (s.auc || 0) > 0.5 ? '#f59e0b' : '#6e7a8a' }}>{s.auc?.toFixed(3) || '-'}</td>
                    <td style={{ padding: '6px 8px', color: cvColor(s.cycle_cv) }}>{s.cycle_cv < 999 ? s.cycle_cv?.toFixed(3) : '—'}</td>
                    <td style={{ padding: '6px 8px', color: '#6e7a8a', fontSize: 11 }}>{cvLevel(s.cycle_cv)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {stockList.length === 0 && (
              <div style={{ textAlign: 'center', padding: '20px 0', color: '#4b5563', fontSize: 12 }}>
                暂无股票 · 点击"➕ 新增股票"开始
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 单股档案 ── */}
      {subtab === 'profile' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
            <select value={searchSym} onChange={e => { setSearchSym(e.target.value); if (e.target.value) loadProfile(normalizeSymbol(e.target.value)); }}
              style={{ padding: '8px 12px', borderRadius: 6, border: '1px solid #1e2535', background: '#0d1117', color: '#cbd5e1', fontSize: 13, minWidth: 160 }}>
              <option value="">-- 选择已建模股票 --</option>
              {modeledSymbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <span style={{ color: '#4b5563', fontSize: 11 }}>或搜索</span>
            <input value={searchSym} onChange={e => setSearchSym(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && loadProfile(normalizeSymbol(searchSym))}
              placeholder="输入代码, 如 002594"
              style={{ flex: 1, padding: '8px 14px', borderRadius: 6, border: '1px solid #1e2535', background: '#0d1117', color: '#cbd5e1', fontSize: 13 }} />
            <button onClick={() => loadProfile(normalizeSymbol(searchSym))}
              style={{ padding: '8px 20px', borderRadius: 6, border: 'none', background: '#8b5cf6', color: '#fff', cursor: 'pointer', fontSize: 13 }}>
              查询 DNA
            </button>
          </div>

          {profile && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {/* 最佳窗口 */}
              <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 4 }}>最佳操作窗口</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: '#10b981' }}>
                  T+{profile.best_window.horizon} <span style={{ fontSize: 14, fontWeight: 400, color: '#6e7a8a' }}>AUC: {profile.best_window.auc?.toFixed(3)}</span>
                </div>
                <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                  {Object.entries(profile.best_window.all_aucs || {}).map(([k, v]) => (
                    <span key={k} style={{ fontSize: 11, color: Number(v) > 0.55 ? '#10b981' : '#6e7a8a' }}>
                      {k}: {(v as number).toFixed(3)}
                    </span>
                  ))}
                </div>
              </div>

              {/* 核心驱动 */}
              <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>核心驱动因子</div>
                {profile.drivers.map((d, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                    <span style={{ fontSize: 12, color: '#cbd5e1', width: 200 }}>{d.name}</span>
                    <div style={{ flex: 1, height: 6, background: '#1e2535', borderRadius: 3, overflow: 'hidden' }}>
                      <div style={{ height: '100%', width: `${d.importance * 100}%`, background: 'linear-gradient(90deg, #8b5cf6, #10b981)', borderRadius: 3 }} />
                    </div>
                    <span style={{ fontSize: 11, color: '#6e7a8a', width: 50, textAlign: 'right' }}>{(d.importance * 100).toFixed(1)}%</span>
                  </div>
                ))}
              </div>

              {/* 周期节律 + 表情 */}
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
                  <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>周期节律</div>
                  <div style={{ fontSize: 13, color: '#cbd5e1' }}>
                    平均锁死: <b>{profile.cycle_rhythm.avg_lockup_days?.toFixed(1)}</b> 天<br />
                    σ = {profile.cycle_rhythm.std_lockup_days?.toFixed(1)} 天 &nbsp;
                    CV = <span style={{ color: cvColor(profile.cycle_rhythm.cv) }}>{profile.cycle_rhythm.cv?.toFixed(3)}</span> {cvLevel(profile.cycle_rhythm.cv)}<br />
                    爆发平均涨幅: <span style={{ color: '#10b981' }}>+{profile.cycle_rhythm.avg_breakout_return?.toFixed(1)}%</span>
                  </div>
                </div>
                <div style={{ flex: 1, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
                  <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>表情指纹</div>
                  <div style={{ fontSize: 13, color: '#cbd5e1' }}>
                    {profile.emotion_fingerprint.n_emotions} 种表情<br />
                    情绪熵: {profile.emotion_fingerprint.entropy?.toFixed(2)}<br />
                    最佳表情后平均收益: <span style={{ color: '#10b981' }}>{
                      (() => {
                        const ber = profile.emotion_fingerprint.best_emotion_ret;
                        if (typeof ber === 'number') return ber.toFixed(1) + '%';
                        if (ber && typeof ber === 'object') {
                          const vals = Object.values(ber).map(v => Number(v));
                          return vals.length ? Math.max(...vals).toFixed(1) + '%' : '—';
                        }
                        return '—';
                      })()
                    }</span>
                  </div>
                </div>
              </div>

              {/* 行为指纹 */}
              <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16 }}>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>行为指纹</div>
                <div style={{ fontSize: 12, color: '#6e7a8a', display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                  <span>抗跌性: <span style={{ color: '#cbd5e1' }}>{profile.behavior.crash_resilience || '—'}</span></span>
                  <span>弹性: <span style={{ color: '#cbd5e1' }}>{profile.behavior.rally_capture || '—'}</span></span>
                  <span>一致性σ: <span style={{ color: '#cbd5e1' }}>{profile.behavior.consistency?.toFixed(2) || '—'}</span></span>
                  <span>妖股指数: <span style={{ color: (profile.behavior.extreme_tail || 0) > 0.05 ? '#ef4444' : '#10b981' }}>{(profile.behavior.extreme_tail || 0).toFixed(3)}</span></span>
                  <span>训练样本: <span style={{ color: '#cbd5e1' }}>{profile.meta.training_samples}</span></span>
                  <span>最后训练: <span style={{ color: '#cbd5e1' }}>{profile.meta.last_trained || '—'}</span></span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 对比矩阵 ── */}
      {subtab === 'compare' && (
        <div>
          <button onClick={loadCompare} style={{ marginBottom: 16, padding: '8px 20px', borderRadius: 6, border: 'none', background: '#8b5cf6', color: '#fff', cursor: 'pointer', fontSize: 13 }}>
            加载对比
          </button>

          {compareData && (
            <div>
              {/* 摘要表 */}
              <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, marginBottom: 16, overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr style={{ color: '#6e7a8a', textAlign: 'left' }}>
                      <th style={{ padding: '6px 10px' }}>股票</th>
                      <th style={{ padding: '6px 10px' }}>最佳窗口</th>
                      <th style={{ padding: '6px 10px' }}>AUC</th>
                      <th style={{ padding: '6px 10px' }}>周期CV</th>
                      <th style={{ padding: '6px 10px' }}>平均锁死</th>
                    </tr>
                  </thead>
                  <tbody>
                    {compareData.comparison.map((c, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #1e2535' }}>
                        <td style={{ padding: '6px 10px', color: '#a78bfa' }}>{c.symbol}</td>
                        <td style={{ padding: '6px 10px', color: '#cbd5e1' }}>T+{c.best_horizon}</td>
                        <td style={{ padding: '6px 10px', color: c.auc > 0.55 ? '#10b981' : '#6e7a8a' }}>{c.auc.toFixed(3)}</td>
                        <td style={{ padding: '6px 10px', color: cvColor(c.cycle_cv) }}>{c.cycle_cv < 999 ? c.cycle_cv.toFixed(3) : '—'}</td>
                        <td style={{ padding: '6px 10px', color: '#cbd5e1' }}>{c.avg_lockup?.toFixed(1) || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 相似度矩阵 (前 10 只) */}
              {compareData.similarity_matrix && (
                <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, overflow: 'auto' }}>
                  <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>DNA 相似度矩阵</div>
                  <table style={{ fontSize: 11, borderCollapse: 'collapse' }}>
                    <thead>
                      <tr>
                        <th style={{ padding: '4px 8px', color: '#6e7a8a' }}></th>
                        {compareData.similarity_matrix.symbols.slice(0, 10).map(s => (
                          <th key={s} style={{ padding: '4px 8px', color: '#6e7a8a', fontSize: 9 }}>{s.slice(0, 6)}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {compareData.similarity_matrix.matrix.slice(0, 10).map((row, i) => (
                        <tr key={i}>
                          <td style={{ padding: '4px 8px', color: '#a78bfa', fontSize: 9 }}>{compareData.similarity_matrix.symbols[i]?.slice(0, 6)}</td>
                          {row.slice(0, 10).map((v, j) => (
                            <td key={j} style={{ padding: '4px 8px', color: v > 0.8 ? '#10b981' : v > 0.5 ? '#f59e0b' : '#6e7a8a', fontWeight: i === j ? 700 : 400 }}>
                              {v.toFixed(2)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── 表情历史 ── */}
      {subtab === 'emotion' && (
        <div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'center' }}>
            <select value={emotionSym} onChange={e => { setEmotionSym(e.target.value); if (e.target.value) loadEmotionHistory(normalizeSymbol(e.target.value)); }}
              style={{ padding: '8px 12px', borderRadius: 6, border: '1px solid #1e2535', background: '#0d1117', color: '#cbd5e1', fontSize: 13, minWidth: 160 }}>
              <option value="">-- 选择已建模股票 --</option>
              {modeledSymbols.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <span style={{ color: '#4b5563', fontSize: 11 }}>或搜索</span>
            <input value={emotionSym} onChange={e => setEmotionSym(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && loadEmotionHistory(normalizeSymbol(emotionSym))}
              placeholder="输入代码, 如 002594"
              style={{ flex: 1, padding: '8px 14px', borderRadius: 6, border: '1px solid #1e2535', background: '#0d1117', color: '#cbd5e1', fontSize: 13 }} />
            <button onClick={() => loadEmotionHistory(normalizeSymbol(emotionSym))}
              style={{ padding: '8px 20px', borderRadius: 6, border: 'none', background: '#8b5cf6', color: '#fff', cursor: 'pointer', fontSize: 13 }}>
              查看表情历史
            </button>
          </div>

          {emotionHistory && (
            <div>
              {/* 表情序列条 */}
              <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, marginBottom: 16 }}>
                <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 10 }}>
                  {emotionHistory.symbol} 近 {emotionHistory.emotion_sequence.length} 天表情序列 (最近在左)
                </div>
                <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                  {emotionHistory.emotion_sequence.map((e, i) => (
                    <span key={i} title={`${e.date}: ${e.emotion_name}`}
                      style={{ padding: '2px 6px', borderRadius: 4, fontSize: 9,
                        background: `hsl(${e.emotion * 60}, 60%, 15%)`,
                        color: `hsl(${e.emotion * 60}, 70%, 70%)`,
                        cursor: 'pointer' }}>
                      {e.emotion_name.slice(0, 3)}
                    </span>
                  ))}
                </div>
              </div>

              {/* 明日预测 */}
              {emotionHistory.transition_tomorrow && (
                <div style={{ display: 'flex', gap: 12 }}>
                  {[
                    { ...emotionHistory.transition_tomorrow.most_likely, label: '最可能', color: '#3b82f6' },
                    { ...emotionHistory.transition_tomorrow.best_case, label: '最佳', color: '#10b981' },
                    { ...emotionHistory.transition_tomorrow.worst_case, label: '最差', color: '#ef4444' },
                  ].map((item, i) => (
                    <div key={i} style={{ flex: 1, background: '#161b27', border: `1px solid ${item.color}22`, borderRadius: 10, padding: 16, textAlign: 'center' }}>
                      <div style={{ fontSize: 10, color: item.color, marginBottom: 4 }}>{item.label}</div>
                      <div style={{ fontSize: 16, fontWeight: 700, color: '#cbd5e1' }}>{item.name}</div>
                      <div style={{ fontSize: 11, color: '#6e7a8a', marginTop: 4 }}>概率: {(item.prob * 100).toFixed(0)}%</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
