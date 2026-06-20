import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../lib/api';

const PATTERN_CN: Record<string, string> = {
  three_red_soldiers: '红三兵', golden_spider: '金蜘蛛', bullish_artillery: '多方炮',
  morning_star: '早晨之星', double_firecracker: '涨停双响炮', air_refueling: '空中加油',
  single_yang_unbroken: '单阳不破', dawn_appearance: '曙光初现', golden_needle_bottom: '金针探底',
  three_black_crows: '三只乌鸦', evening_star: '黄昏之星', hanging_man: '吊颈线',
  decapitation: '断头铡刀', dark_cloud_cover: '乌云盖顶', pouring_rain: '倾盆大雨',
};
const BEAR_PATTERNS = new Set(['three_black_crows','evening_star','hanging_man','decapitation','dark_cloud_cover','pouring_rain']);
const fmtPattern = (p: string) => p.split(',').map((s: string) => PATTERN_CN[s.trim()] || s.trim()).join(',');
const isBear = (p: string) => p.split(',').some((s: string) => BEAR_PATTERNS.has(s.trim()));

const ARCH_LABELS: Record<string, string> = {
  '主板_large_bluechip': '主板大盘蓝筹', '主板_small_speculative': '主板小盘题材',
  '主板_growth_tech': '主板科技成长', '主板_value_defensive': '主板价值防御',
  '主板_cyclical_resource': '主板周期资源',
  '创业板_large_bluechip': '创业板蓝筹', '创业板_small_speculative': '创业板小盘',
  '创业板_growth_tech': '创业板科技', '创业板_value_defensive': '创业板防御',
  '创业板_cyclical_resource': '创业板周期',
  // fallbacks for unprefixed
  large_bluechip: '大盘蓝筹', small_speculative: '小盘题材',
  growth_tech: '科技成长', value_defensive: '价值防御',
  cyclical_resource: '周期资源',
};

const ARCH_COLORS: Record<string, string> = {
  '主板_large_bluechip': '#3b82f6', '主板_small_speculative': '#ef4444',
  '主板_growth_tech': '#10b981', '主板_value_defensive': '#f59e0b',
  '主板_cyclical_resource': '#8b5cf6',
  '创业板_large_bluechip': '#60a5fa', '创业板_small_speculative': '#f87171',
  '创业板_growth_tech': '#34d399', '创业板_value_defensive': '#fbbf24',
  '创业板_cyclical_resource': '#f472b6',
  large_bluechip: '#3b82f6', small_speculative: '#a855f7',
  growth_tech: '#10b981', value_defensive: '#f59e0b', cyclical_resource: '#ef4444',
};

// ★ v7.0.33: 列定义 (数据驱动, 隐藏列加 hidden: true)
//   重要: 隐藏只是前端不渲染, 后端数据仍计算并返回
//   切换隐藏只需改 hidden: true/false, 不改 td/render 逻辑
interface ColumnDef {
  key: string;
  label: string;
  hidden?: boolean;          // true = 不渲染 (前端隐藏)
  render: (r: any) => React.ReactNode;
}

const COLUMNS: ColumnDef[] = [
  { key: 'symbol', label: '代码',
    render: (r) => <code style={{ color: '#06b6d4' }}>{r.symbol}</code> },
  { key: 'name', label: '名称',
    render: (r) => (
      <span title={r.symbol}>
        {r.name && r.name !== r.symbol && !r.name.match(/\.(SH|SZ|BJ)$/)
          ? r.name
          : <span style={{color:'#f59e0b',fontStyle:'italic'}}>{r.symbol} (无名称)</span>}
        {r.ambush_score > 0 && <span style={{marginLeft:6,padding:'1px 5px',borderRadius:3,fontSize:9,background:'rgba(239,68,68,0.12)',color:'#ef4444'}}>潜伏</span>}
      </span>
    ) },
  { key: 'composite', label: '综合分',
    render: (r) => (
      <span style={{
        padding: '2px 8px', borderRadius: 4, fontWeight: 700, fontSize: 12,
        background: r.composite_score >= 55 ? 'rgba(239,68,68,0.1)' : r.composite_score >= 45 ? 'rgba(245,158,11,0.1)' : 'rgba(107,114,128,0.1)',
        color: r.composite_score >= 55 ? '#ef4444' : r.composite_score >= 45 ? '#f59e0b' : '#9ca3af',
      }}>{r.composite_score}</span>
    ) },
  { key: 'tech', label: '技术面',
    render: (r) => (
      <span style={{ color: (r.tech_score || 0) >= 7 ? '#ef4444' : (r.tech_score || 0) <= 3 ? '#10b981' : '#9ca3af', fontWeight: 600 }}>
        {r.tech_score?.toFixed(1) || '-'}
      </span>
    ) },
  { key: 'kline', label: 'K线博弈', hidden: true,  // ★ 用户要求隐藏
    render: (r) => (
      <span style={{ color: (r.kline_score || 0) >= 7 ? '#ef4444' : (r.kline_score || 0) <= 3 ? '#10b981' : '#9ca3af', fontWeight: 600 }}>
        {r.kline_score?.toFixed(1) || '-'}
      </span>
    ) },
  { key: 'fund', label: '资金面',
    render: (r) => (
      <span style={{ color: (r.fund_score || 0) >= 7 ? '#ef4444' : (r.fund_score || 0) <= 3 ? '#10b981' : '#9ca3af', fontWeight: 600 }}>
        {r.fund_score?.toFixed(1) || '-'}
      </span>
    ) },
  { key: 'fundamental', label: '基本面调整',
    render: (r) => (
      <span style={{ color: (r.fundamental_adjustment || 0) > 0 ? '#ef4444' : (r.fundamental_adjustment || 0) < 0 ? '#10b981' : '#6e7a8a', fontWeight: 600 }}>
        {r.fundamental_adjustment != null ? (r.fundamental_adjustment > 0 ? '+' : '') + r.fundamental_adjustment : '-'}
      </span>
    ) },
  { key: 'sector', label: '板块加成',
    render: (r) => <span>{r.sector_bonus > 0 ? `+${r.sector_bonus}` : '0'}</span> },
  { key: 'archetype', label: '原型',
    render: (r) => r.archetype && r.archetype !== 'unknown' ? (
      <span style={{
        padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
        background: `${ARCH_COLORS[r.archetype] || '#6e7a8a'}18`,
        color: ARCH_COLORS[r.archetype] || '#6e7a8a', cursor: 'help',
      }} title={(r.adjustment_reasons || []).join('\n')}>
        {ARCH_LABELS[r.archetype] || r.archetype}
      </span>
    ) : <span style={{ color: '#4b5563' }}>—</span> },
  { key: 'win_prob', label: 'T+2胜率', hidden: true,  // ★ 用户要求隐藏
    render: (r) => r.win_probability != null ? (
      <span style={{ fontWeight: 600, fontSize: 12,
        color: r.win_probability >= 0.45 ? '#ef4444' : r.win_probability >= 0.35 ? '#f59e0b' : '#10b981' }}>
        {(r.win_probability * 100).toFixed(0)}%
        {r.downside_risk != null && r.downside_risk < -2 && (
          <span style={{ marginLeft: 4, fontSize: 10, color: '#ef4444' }}>⚠</span>
        )}
      </span>
    ) : <span style={{ color: '#4b5563' }}>—</span> },
  { key: 'patterns', label: '形态',
    render: (r) => r.patterns ? (
      <span style={{ fontSize: 10, color: isBear(r.patterns) ? '#10b981' : '#ef4444', background: isBear(r.patterns) ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.08)', padding: '2px 6px', borderRadius: 4, maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }} title={r.patterns}>
        {fmtPattern(r.patterns).slice(0, 20)}
      </span>
    ) : <span style={{ color: '#4b5563' }}>—</span> },
  { key: 'level', label: '级别',
    render: (r) => (
      <span style={{
        padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600,
        background: r.level === 'L3' ? 'rgba(251,191,36,.15)' : r.level === 'L2' ? 'rgba(96,165,250,.15)' : 'rgba(156,163,175,.12)',
        color: r.level === 'L3' ? '#f59e0b' : r.level === 'L2' ? '#60a5fa' : '#9ca3af',
      }}>{r.level || '—'}</span>
    ) },
  { key: 'monthly', label: '30日',
    render: (r) => (
      <span style={{ color: (r.monthly_pushes||1) >= 5 ? '#ef4444' : (r.monthly_pushes||1) >= 3 ? '#f59e0b' : '#6e7a8a', fontWeight: (r.monthly_pushes||1) >= 3 ? 600 : 400, fontSize: 11 }}>
        {r.monthly_pushes||1}次
      </span>
    ) },
  { key: 'fairy', label: '大神仙空',
    render: (r) => r.big_fairy ? (
      <span style={{display:'inline-flex',alignItems:'center',gap:3}}>
        <span style={{fontSize:13,fontWeight:700,
          color:r.big_fairy.score>=3?'#ef4444':r.big_fairy.score>=2?'#f59e0b':r.big_fairy.score>=1?'#fbbf24':'#10b981'}}>
          {r.big_fairy.score}
        </span>
        <span style={{fontSize:10,color:'#8b949e'}}>
          {r.big_fairy.signal==='strong_sell'?'强空':r.big_fairy.signal==='sell'?'偏空':r.big_fairy.signal==='weak'?'弱':'正常'}
        </span>
      </span>
    ) : <span style={{color:'#4b5563',fontSize:11}}>—</span> },
  { key: 'macd_dif', label: 'MACD DIF',
    render: (r) => <span style={{ color: r.macd_dif == null ? '#6e7a8a' : (r.macd_dif > 0 ? '#ef4444' : '#10b981'), fontWeight: 600, fontSize: 11 }}>
      {r.macd_dif != null ? r.macd_dif.toFixed(2) : '-'}
    </span> },
  { key: 'macd_dea', label: 'MACD DEA',
    render: (r) => <span style={{ color: r.macd_dea == null ? '#6e7a8a' : (r.macd_dea > 0 ? '#ef4444' : '#10b981'), fontWeight: 600, fontSize: 11 }}>
      {r.macd_dea != null ? r.macd_dea.toFixed(2) : '-'}
    </span> },
  { key: 'kdj_j', label: 'KDJ J',
    render: (r) => <span style={{ color: r.kdj_j == null ? '#6e7a8a' : (r.kdj_j > 80 ? '#10b981' : r.kdj_j < 20 ? '#ef4444' : '#9ca3af'), fontWeight: 600, fontSize: 11 }}>
      {r.kdj_j != null ? r.kdj_j.toFixed(0) : '-'}
    </span> },
  { key: 'rsi_24', label: 'RSI 24', hidden: true,  // ★ 用户要求隐藏
    render: (r) => <span style={{ color: r.rsi_24 == null ? '#6e7a8a' : (r.rsi_24 > 70 ? '#10b981' : r.rsi_24 < 30 ? '#ef4444' : '#9ca3af'), fontWeight: 600, fontSize: 11 }}>
      {r.rsi_24 != null ? r.rsi_24.toFixed(0) : '-'}
    </span> },
  { key: 'boll', label: 'BOLL', hidden: true,  // ★ 用户要求隐藏
    render: (r) => <span style={{ color: r.boll_pos == null ? '#6e7a8a' : (r.boll_pos > 0.9 ? '#10b981' : r.boll_pos < 0.1 ? '#ef4444' : '#9ca3af'), fontSize: 11 }}>
      {r.boll_pos != null ? r.boll_pos.toFixed(2) : '-'}
    </span> },
  { key: 'cci', label: 'CCI', hidden: true,  // ★ 用户要求隐藏
    render: (r) => {
      const v = r.cci;
      let c = '#9ca3af';  // 默认中性
      if (v != null) {
        if (v > 200) c = '#10b981';        // 大幅超买 → 绿 (利空, A 股跌)
        else if (v < -200) c = '#ef4444';  // 大幅超卖 → 红 (利空出尽, A 股涨)
      }
      return <span style={{ color: c, fontSize: 11 }}>
        {v != null ? v.toFixed(0) : '-'}
      </span>;
    } },
  { key: 'cost', label: '成本中位',
    render: (r) => <span style={{ color: r.cost_50pct == null ? '#6e7a8a' : '#9ca3af', fontSize: 11 }}>
      {r.cost_50pct != null ? r.cost_50pct.toFixed(1) : '-'}
    </span> },
  { key: 'spread', label: '筹码宽度',
    render: (r) => <span style={{ color: r.cost_spread == null ? '#6e7a8a' : (r.cost_spread > 5 ? '#ef4444' : '#10b981'), fontSize: 11 }}>
      {r.cost_spread != null ? r.cost_spread.toFixed(1) : '-'}
    </span> },
  { key: 'gold', label: '金过滤',
    render: (r) => {
      const macdOk = r.macd_dif != null && r.macd_dif > 0;
      const kdjOk = r.kdj_j != null && r.kdj_j < 80;
      const chipOk = r.cost_50pct != null && r.cost_50pct > 20;
      const spreadOk = r.cost_spread != null && r.cost_spread > 5;
      const all = macdOk && kdjOk && chipOk && spreadOk;
      const hasData = r.macd_dif != null && r.kdj_j != null && r.cost_50pct != null && r.cost_spread != null;
      if (!hasData) return <span style={{ color: '#4b5563', fontSize: 10 }}>—</span>;
      if (all) return <span style={{ padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 700, background: 'rgba(16,185,129,0.15)', color: '#10b981' }}>✓ 金过滤</span>;
      return <span style={{ padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 600, background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>⚠ 风险</span>;
    } },
];

// ★ 过滤后的可见列 (动态渲染, 不影响后端数据)
const VISIBLE_COLUMNS = COLUMNS.filter(c => !c.hidden);
const VISIBLE_COL_COUNT = VISIBLE_COLUMNS.length;

export default function AnalysisPage() {
  const [data, setData] = useState<any[]>([]);
  const [manualStocks, setManualStocks] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [archetypeFilter, setArchetypeFilter] = useState<string>('');
  const [qualityFilter, setQualityFilter] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const navigate = useNavigate();

  const [triggering, setTriggering] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [addInput, setAddInput] = useState('');
  const [adding, setAdding] = useState(false);

  const toggleSelect = (sym: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym);
      else if (next.size < 20) next.add(sym);
      return next;
    });
  };

  const goDeepAnalysis = () => {
    if (selected.size === 0) return;
    navigate(`/deep-analysis?symbols=${[...selected].join(',')}`);
  };

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get('/analysis/results', { params: { limit: 100 } });
      setData(r.data.data || []);
      // 手动添加的股票（不在主排名中）
      const m = await api.get('/analysis/results', { params: { limit: 50, manual_only: 'true' } });
      setManualStocks(m.data.data || []);
    } catch {}
    setLoading(false);
  };

  const [triggerResult, setTriggerResult] = useState<any>(null);

  const triggerAnalysis = async () => {
    setTriggering(true);
    try {
      const r = await api.post('/analysis/trigger');
      if (r.data.status === 'success') {
        setTriggerResult(r.data);
        await load();
        startShadowTraining();
      }
    } catch (e: any) {
      alert(e?.response?.data?.detail || '评分失败');
    }
    setTriggering(false);
  };

  const doAddStock = async () => {
    let sym = addInput.trim();
    // 更宽松的校验: 接受纯数字6位 或 完整代码
    const pureCode = sym.match(/(\d{6})/);
    if (!pureCode) { alert('请输入6位数字代码，如: 600660'); return; }
    sym = pureCode[1];
    // 自动补全后缀
    if (sym.startsWith('6')) sym += '.SH';
    else if (sym.startsWith('0') || sym.startsWith('3')) sym += '.SZ';
    else if (sym.startsWith('8') || sym.startsWith('4')) sym += '.BJ';
    else { alert('无法识别交易所，请手动输入完整代码'); return; }
    setAdding(true);
    try {
      const r = await api.post('/analysis/add-stock', { symbol: sym });
      if (r.data.status === 'success') {
        setAddOpen(false); setAddInput('');
        await load();
      } else {
        alert(r.data.detail || '添加失败');
      }
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || '添加失败';
      alert('添加失败: ' + msg);
    } finally {
      setAdding(false);
    }
  };

  const removeManualStock = async (sym: string) => {
    if (!confirm(`确认删除 ${sym}？`)) return;
    try { await api.delete(`/analysis/manual-stock?symbol=${sym}`); await load(); }
    catch { alert('删除失败'); }
  };

  const startShadowTraining = async () => {
    try {
      // 静默启动，不阻塞UI
      await api.post('/learning/shadow-train', null, { params: { archetype: 'all', strategy: 'all', iterations: 3 } });
    } catch {}
  };

  // v5.5: 页面加载时滚动到顶部
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
  }, []);

  useEffect(() => { load(); }, []);

  const filtered = (() => {
    let d = archetypeFilter
      ? data.filter((r: any) => (r.archetype || 'unknown') === archetypeFilter)
      : data;
    if (qualityFilter) {
      d = d.filter((r: any) =>
        (r.signal_quality || 0) >= 0.7 && (r.win_probability || 0) >= 0.50 && (r.trend_score || 0) >= 3
      );
    }
    return d;
  })();

  const archetypes = [...new Set(data.map((r: any) => r.archetype || 'unknown'))];

  const downloadTXT = () => {
    const rows = filtered;
    if (rows.length === 0) return;
    const lines = rows.map((r: any) => r.symbol);
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `stocks_${new Date().toISOString().slice(0,10)}.txt`;
    a.click(); URL.revokeObjectURL(url);
  };

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>第三步：多维度评分 & 精选</h2>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: '#6e7a8a' }}>已选: {selected.size}/20</span>
          <button onClick={() => setAddOpen(true)}
            style={{
              padding: '6px 14px', border: '1px solid #06b6d4', borderRadius: 6, fontSize: 12, fontWeight: 600,
              cursor: 'pointer', background: 'rgba(6,182,212,0.08)', color: '#22d3ee',
            }}>
            ➕ 手动添加
          </button>
          <button onClick={downloadTXT}
            style={{
              padding: '6px 14px', border: '1px solid #10b981', borderRadius: 6, fontSize: 12, fontWeight: 600,
              cursor: 'pointer', background: 'rgba(16,185,129,0.08)', color: '#34d399',
            }}>
            📥 下载TXT ({filtered.length}只)
          </button>
          <button onClick={triggerAnalysis} disabled={triggering}
            style={{
              padding: '8px 16px', border: '1px solid #f59e0b', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: triggering ? 'not-allowed' : 'pointer',
              background: triggering ? '#374151' : 'rgba(245,158,11,0.1)', color: triggering ? '#6b7280' : '#f59e0b',
            }}>
            {triggering ? '评分中...' : '重新评分'}
          </button>
          <button onClick={goDeepAnalysis} disabled={selected.size === 0}
            style={{
              padding: '8px 20px', border: 'none', borderRadius: 8, fontSize: 13, fontWeight: 600,
              cursor: selected.size > 0 ? 'pointer' : 'not-allowed',
              background: selected.size > 0 ? '#8b5cf6' : '#374151',
              color: selected.size > 0 ? '#fff' : '#6b7280',
            }}>
            下一步 → LLM深度分析 ({selected.size})
          </button>
        </div>
      </div>
      {triggerResult && (
        <div style={{ marginBottom: 12, padding: '8px 14px', borderRadius: 8,
          background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.15)',
          display: 'flex', alignItems: 'center', gap: 16, fontSize: 12 }}>
          <span style={{ color: '#10b981', fontWeight: 600 }}>🎯 {triggerResult.recommended}只精选</span>
          <span style={{ color: '#6e7a8a' }}>|</span>
          <span style={{ color: '#6e7a8a' }}>全部 {triggerResult.count}只</span>
          <span style={{ color: '#6e7a8a' }}>|</span>
          <span style={{ color: '#4b5563' }}>门槛: 质量≥70% + 胜率≥50% + 趋势≥3⭐</span>
          <span style={{ flex:1 }} />
          <button onClick={() => setQualityFilter(!qualityFilter)}
            style={{ padding:'2px 10px', borderRadius:10, fontSize:10, border:'1px solid',
              background: qualityFilter ? 'rgba(16,185,129,0.1)' : 'transparent',
              color: qualityFilter ? '#10b981' : '#6e7a8a',
              borderColor: qualityFilter ? 'rgba(16,185,129,0.25)' : '#1e2535',
              cursor:'pointer' }}>
            {qualityFilter ? '仅精选' : '显示全部'}
          </button>
        </div>
      )}
      <p style={{ color: '#6e7a8a', marginBottom: 16, fontSize: 13 }}>
        勾选股票（最多20只）→ 点击"下一步"自动调用 DeepSeek API 深度分析
      </p>

      {/* 原型分布 */}
      {archetypes.length > 0 && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
          <button onClick={() => setArchetypeFilter('')}
            style={{
              padding: '4px 12px', borderRadius: 14, fontSize: 11, fontWeight: 600, cursor: 'pointer',
              border: !archetypeFilter ? '1px solid #6e7a8a' : '1px solid #1e2535',
              background: !archetypeFilter ? 'rgba(110,122,138,0.12)' : 'transparent',
              color: !archetypeFilter ? '#c9d1d9' : '#6e7a8a',
            }}>
            全部 ({data.length})
          </button>
          {archetypes.filter(a => a !== 'unknown').map(arch => {
            const count = data.filter((r: any) => r.archetype === arch).length;
            return (
              <button key={arch} onClick={() => setArchetypeFilter(arch)}
                style={{
                  padding: '4px 12px', borderRadius: 14, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                  border: archetypeFilter === arch ? `1px solid ${ARCH_COLORS[arch] || '#6e7a8a'}` : '1px solid #1e2535',
                  background: archetypeFilter === arch ? `${ARCH_COLORS[arch] || '#6e7a8a'}15` : 'transparent',
                  color: archetypeFilter === arch ? ARCH_COLORS[arch] || '#c9d1d9' : '#6e7a8a',
                }}>
                {ARCH_LABELS[arch] || arch} ({count})
              </button>
            );
          })}
        </div>
      )}

      {/* 手动添加股票（独立区域，不参与排名） */}
      {manualStocks.length > 0 && (
        <div style={{ marginBottom: 16, padding: '12px 16px', background: 'rgba(6,182,212,0.04)', border: '1px solid rgba(6,182,212,0.15)', borderRadius: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: '#22d3ee', marginBottom: 10 }}>
            📌 手动添加（独立于排名，可选中进入LLM分析）
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {manualStocks.map((r: any) => {
              const sel = selected.has(r.symbol);
              return (
                <label key={r.symbol} style={{
                  display: 'flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 6,
                  background: sel ? 'rgba(139,92,246,0.12)' : 'rgba(6,182,212,0.04)',
                  border: sel ? '1px solid #8b5cf6' : '1px solid rgba(6,182,212,0.12)',
                  cursor: 'pointer', fontSize: 12,
                }}>
                  <input type="checkbox" checked={sel} onChange={() => toggleSelect(r.symbol)}
                    style={{ accentColor: '#8b5cf6' }} />
                  <code style={{ color: '#06b6d4' }}>{r.symbol}</code>
                  <span style={{ color: '#c9d1d9' }}>{r.name || r.symbol}</span>
                  <span style={{ color: '#6e7a8a', fontSize: 10 }}>
                    综合{r.composite_score?.toFixed(0)} 技{r.tech_score?.toFixed(1)} K{r.kline_score?.toFixed(1)} 资{r.fund_score?.toFixed(1)}
                  </span>
                  <span onClick={(e) => { e.stopPropagation(); e.preventDefault(); removeManualStock(r.symbol); }}
                    style={{ color: '#ef4444', cursor: 'pointer', fontSize: 14, marginLeft: 4, fontWeight: 700 }}
                    title="移除">−</span>
                </label>
              );
            })}
          </div>
        </div>
      )}

      <table style={{ width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 12, overflow: 'hidden' }}>
        <thead><tr style={{ background: '#1a2030' }}>
          <th style={{ padding: '10px 8px', width: 36 }}></th>
          {VISIBLE_COLUMNS.map(c => (
            <th key={c.key} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, color: '#6e7a8a', fontWeight: 500 }}>{c.label}</th>
          ))}
        </tr></thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={VISIBLE_COL_COUNT + 1} style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>加载中...</td></tr>
          ) : filtered.length === 0 ? (
            <tr><td colSpan={VISIBLE_COL_COUNT + 1} style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>暂无评分数据，请先运行 TG 扫描触发深度评分</td></tr>
          ) : filtered.map((r: any, i: number) => {
            const isSel = selected.has(r.symbol);
            return (
              <tr key={i} onClick={() => toggleSelect(r.symbol)}
                style={{ borderTop: '1px solid #1e2535', cursor: 'pointer',
                  background: isSel ? 'rgba(139,92,246,0.06)' : (r.fundamental_adjustment || 0) <= -8 ? 'rgba(239,68,68,0.04)' : 'transparent' }}>
                <td style={{ padding: '9px 4px', textAlign: 'center' }}>
                  <input type="checkbox" checked={isSel} onChange={() => toggleSelect(r.symbol)}
                    disabled={!isSel && selected.size >= 20}
                    style={{ width: 15, height: 15, cursor: 'pointer', accentColor: '#8b5cf6' }} />
                </td>
                {VISIBLE_COLUMNS.map(c => (
                  <td key={c.key} style={{ padding: c.key === 'name' || c.key === 'symbol' ? '9px 14px' : '9px 10px', fontSize: 12 }}>
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>

      {/* 图例 */}
      <div style={{ marginTop: 16, padding: 12, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
        <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>原型图例（各原型使用不同评分权重）</div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          {Object.entries(ARCH_LABELS).map(([key, label]) => (
            <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
              <div style={{ width: 10, height: 10, borderRadius: 2, background: ARCH_COLORS[key] || '#6e7a8a' }} />
              <span style={{ color: '#c9d1d9' }}>{label}</span>
              <span style={{ color: '#6e7a8a' }}>{key}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 列说明 */}
      <div style={{ marginTop: 12, padding: 12, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
        <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>列说明</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '6px 16px', fontSize: 10, color: '#8b949e' }}>
          <div><span style={{ color: '#c9d1d9' }}>技术面</span> — 多指标综合：RSI/MACD/布林带/BBI/趋势偏离 (-10~+10)</div>
          <div><span style={{ color: '#c9d1d9' }}>K线博弈</span> — 多空K线形态/突破/连阳连阴评分</div>
          <div><span style={{ color: '#c9d1d9' }}>资金面</span> — 量价配合/涨跌量比/换手率评分</div>
          <div><span style={{ color: '#c9d1d9' }}>基本面调整</span> — ROE/增速/负债/现金流综合修正分</div>
          <div><span style={{ color: '#c9d1d9' }}>板块加成</span> — 板块热度/龙虎榜共振/TG信号等级综合加成</div>
          <div><span style={{ color: '#c9d1d9' }}>原型</span> — K-means聚类分配的10种策略原型 (5策略×2市场)</div>
          <div><span style={{ color: '#10b981' }}>T+2胜率</span> — 系统预测T+2日上涨的概率 (0~100%)</div>
          <div><span style={{ color: '#c9d1d9' }}>趋势</span> — 趋势确认 ⭐: MA多头/量增/强于大盘/活跃/MA20上 (≥3=成立)</div>
          <div><span style={{ color: '#c9d1d9' }}>形态</span> — 检测到的K线形态 (金蜘蛛/红三兵等)</div>
          <div><span style={{ color: '#c9d1d9' }}>级别</span> — TG信号强度等级 (L3最强→L1)</div>
          <div><span style={{ color: '#c9d1d9' }}>30日</span> — 30个交易日内该股被推荐的累计次数</div>
          <div><span style={{ color: '#c9d1d9' }}>质量</span> — 护法反训练信号质量 (0~100%, 高=可靠, 低=假信号)</div>
          <div style={{ gridColumn: '1 / -1', marginTop: 8, paddingTop: 8, borderTop: '1px solid #1e2535' }}><span style={{ color: '#fbbf24', fontWeight: 600 }}>⭐ v7.0.32 新增</span></div>
          <div><span style={{ color: '#c9d1d9' }}>MACD DIF/DEA</span> — MACD 指标 (&gt;0 多头绿/&lt;0 空头红)</div>
          <div><span style={{ color: '#c9d1d9' }}>KDJ J</span> — KDJ 指标 (&lt;20 超卖绿, &gt;80 超买红)</div>
          <div><span style={{ color: '#c9d1d9' }}>RSI 24</span> — 24 日 RSI (&lt;30 超卖, &gt;70 超买)</div>
          <div><span style={{ color: '#c9d1d9' }}>BOLL</span> — 布林带位置 0~1 (0.3-0.7 中性)</div>
          <div><span style={{ color: '#c9d1d9' }}>CCI</span> — 顺势指标 (±200 极值)</div>
          <div><span style={{ color: '#c9d1d9' }}>成本中位</span> — 筹码 50% 分位成本 (主力成本参考)</div>
          <div><span style={{ color: '#c9d1d9' }}>筹码宽度</span> — 90% 筹码成本跨度 (越大越分散)</div>
          <div><span style={{ color: '#10b981' }}>金过滤</span> — 4 维全通过 ✓ (MACD多头+KDJ不超买+成本适中+筹码分散)</div>
        </div>
      </div>

      {/* 手动添加弹窗 */}
      {addOpen && (
        <div style={{
          position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
          background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => setAddOpen(false)}>
          <div style={{
            background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 24,
            width: 400, maxWidth: '90vw',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 15, fontWeight: 600, color: '#c9d1d9', marginBottom: 6 }}>手动添加股票</div>
            <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 14 }}>
              输入A股代码，系统自动拉取K线数据并进行多维度评分
            </div>
            <input value={addInput} onChange={e => setAddInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doAddStock(); }}
              placeholder="如: 600660"
              style={{
                width: '100%', padding: '10px 14px', borderRadius: 8, border: '1px solid #1e2535',
                background: '#0b0e14', color: '#c9d1d9', fontSize: 14, marginBottom: 14,
              }} />
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button onClick={() => setAddOpen(false)}
                style={{ padding: '8px 20px', borderRadius: 6, border: '1px solid #374151', background: 'transparent', color: '#6e7a8a', cursor: 'pointer', fontSize: 12 }}>
                取消
              </button>
              <button onClick={doAddStock} disabled={adding}
                style={{ padding: '8px 20px', borderRadius: 6, border: 'none', background: adding ? '#374151' : '#06b6d4', color: '#fff', cursor: adding ? 'not-allowed' : 'pointer', fontSize: 12, fontWeight: 600 }}>
                {adding ? '评分中...' : '确认添加'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
