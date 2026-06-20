import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
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

export default function StockSelectPage() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const navigate = useNavigate();

  useEffect(() => {
    api.get('/analysis/results', { params: { limit: 80 } }).then(r => {
      setData(r.data.data || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const toggle = (sym: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(sym)) next.delete(sym);
      else if (next.size < 10) next.add(sym);
      return next;
    });
  };

  const goNext = () => {
    if (selected.size === 0) return;
    navigate(`/deep-analysis?symbols=${[...selected].join(',')}`);
  };

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>精选股票</h2>
        <span style={{ fontSize: 12, color: '#6e7a8a' }}>从多维度评分结果中选择最多10只，进入LLM深度分析</span>
      </div>
      <p style={{ color: '#6e7a8a', marginBottom: 16, fontSize: 13 }}>
        勾选感兴趣的股票（最多10只），点击下一步自动带入LLM分析页面生成提示词
      </p>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center' }}>
        <span style={{ fontSize: 12, color: '#6e7a8a' }}>已选: {selected.size}/10</span>
        <button onClick={() => {
          const txt = data.map((r: any) => r.symbol).join('\n');
          const blob = new Blob([txt], { type: 'text/plain' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = `stocks_${new Date().toISOString().slice(0,10)}.txt`;
          a.click(); URL.revokeObjectURL(url);
        }} disabled={data.length === 0}
          style={{ padding: '6px 14px', borderRadius: 6, border: '1px solid #f59e0b', background: 'rgba(245,158,11,0.08)', color: '#f59e0b', cursor: data.length>0?'pointer':'not-allowed', fontSize: 12, fontWeight: 600 }}>
          📥 导出同花顺 ({data.length}只)
        </button>
        <div style={{ flex: 1 }} />
        <button onClick={goNext} disabled={selected.size === 0}
          style={{
            padding: '10px 28px', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600,
            cursor: selected.size > 0 ? 'pointer' : 'not-allowed',
            background: selected.size > 0 ? '#8b5cf6' : '#374151',
            color: selected.size > 0 ? '#fff' : '#6b7280',
          }}>
          下一步 → LLM深度分析 ({selected.size})
        </button>
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 12, overflow: 'hidden' }}>
        <thead><tr style={{ background: '#1a2030' }}>
          <th style={{ padding: '10px 8px', width: 40 }}></th>
          {['代码', '名称', '综合分', '技术面', 'K线', '资金面', '基本面', '形态', '级别', '质量'].map(h => (
            <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, color: '#6e7a8a', fontWeight: 500 }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={10} style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>加载中...</td></tr>
          ) : data.length === 0 ? (
            <tr><td colSpan={10} style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>暂无评分数据</td></tr>
          ) : data.map((r: any, i: number) => {
            const isSel = selected.has(r.symbol);
            return (
              <tr key={i} onClick={() => toggle(r.symbol)}
                style={{ borderTop: '1px solid #1e2535', cursor: 'pointer', background: isSel ? 'rgba(139,92,246,0.06)' : 'transparent' }}>
                <td style={{ padding: '9px 8px', textAlign: 'center' }}>
                  <input type="checkbox" checked={isSel} onChange={() => toggle(r.symbol)}
                    disabled={!isSel && selected.size >= 10}
                    style={{ width: 16, height: 16, cursor: 'pointer', accentColor: '#8b5cf6' }} />
                </td>
                <td style={{ padding: '9px 14px' }}><code style={{ color: '#06b6d4' }}>{r.symbol}</code></td>
                <td style={{ padding: '9px 14px', fontWeight: 600 }}>{r.name}{r.ambush_score > 0 && <span style={{marginLeft:6,padding:'1px 5px',borderRadius:3,fontSize:9,background:'rgba(239,68,68,0.12)',color:'#ef4444'}}>潜伏</span>}</td>
                <td style={{ padding: '9px 14px' }}>
                  <span style={{
                    padding: '2px 8px', borderRadius: 4, fontWeight: 700, fontSize: 12,
                    background: r.composite_score >= 55 ? 'rgba(239,68,68,0.1)' : r.composite_score >= 45 ? 'rgba(245,158,11,0.1)' : 'rgba(107,114,128,0.1)',
                    color: r.composite_score >= 55 ? '#ef4444' : r.composite_score >= 45 ? '#f59e0b' : '#9ca3af',
                  }}>{r.composite_score}</span>
                </td>
                <td style={{ padding: '9px 14px' }}>{r.tech_score?.toFixed(1) || '-'}</td>
                <td style={{ padding: '9px 14px' }}>{r.kline_score?.toFixed(1) || '-'}</td>
                <td style={{ padding: '9px 14px' }}>{r.fund_score?.toFixed(1) || '-'}</td>
                <td style={{ padding: '9px 14px', color: (r.fundamental_adjustment || 0) < 0 ? '#10b981' : (r.fundamental_adjustment || 0) > 0 ? '#ef4444' : '#6e7a8a' }}>
                  {r.fundamental_adjustment != null ? (r.fundamental_adjustment > 0 ? '+' : '') + r.fundamental_adjustment : '-'}
                </td>
                <td style={{ padding: '9px 14px' }}>
                  {r.patterns ? (
                    <span style={{ fontSize: 10, color: isBear(r.patterns) ? '#10b981' : '#ef4444', background: isBear(r.patterns) ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.08)', padding: '2px 6px', borderRadius: 4, maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'inline-block' }} title={r.patterns}>
                      {fmtPattern(r.patterns).slice(0, 20)}
                    </span>
                  ) : <span style={{ color: '#4b5563' }}>—</span>}
                </td>
                <td style={{ padding: '9px 14px' }}>
                  <span style={{
                    padding: '2px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                    background: r.level === 'L3' ? 'rgba(251,191,36,.15)' : r.level === 'L2' ? 'rgba(96,165,250,.15)' : 'rgba(156,163,175,.12)',
                    color: r.level === 'L3' ? '#f59e0b' : r.level === 'L2' ? '#60a5fa' : '#9ca3af',
                  }}>{r.level || '—'}</span>
                </td>
                <td style={{ padding: '9px 14px' }}>
                  {r.risk_label === 'dead' ? <span style={{color:'#ef4444',fontWeight:600,fontSize:12}} title="信号质量<0.3: 极高欺骗风险">💀</span>
                   : r.risk_label === 'danger' ? <span style={{color:'#ef4444',fontWeight:600,fontSize:12}} title="信号质量0.3~0.5: 高欺骗风险">🔴</span>
                   : r.risk_label === 'warn' ? <span style={{color:'#f59e0b',fontSize:12}} title="信号质量0.5~0.7: 注意风险">⚠</span>
                   : <span style={{color:'#4b5563'}}>—</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
