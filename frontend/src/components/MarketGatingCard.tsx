import React from 'react';

interface Props {
  gateInfo: any;
}

export default function MarketGatingCard({ gateInfo }: Props) {
  if (!gateInfo) return null;

  return (
    <div style={{ marginBottom: 12, padding: '10px 16px', borderRadius: 8,
      background: gateInfo.market_risk === 'high' ? 'rgba(239,68,68,0.1)' :
                  gateInfo.market_risk === 'elevated' ? 'rgba(245,158,11,0.08)' :
                  'rgba(59,130,246,0.06)',
      border: `1px solid ${gateInfo.market_risk === 'high' ? 'rgba(239,68,68,0.25)' :
                           gateInfo.market_risk === 'elevated' ? 'rgba(245,158,11,0.2)' :
                           'rgba(59,130,246,0.12)'}` }}>
      <div style={{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap' }}>
        <span style={{ fontSize:13, fontWeight:600,
          color: gateInfo.market_risk === 'high' ? '#ef4444' :
                 gateInfo.market_risk === 'elevated' ? '#f59e0b' : '#3b82f6' }}>
          {gateInfo.market_risk === 'high' ? '🔴' : gateInfo.market_risk === 'elevated' ? '🟡' : '🟢'} {gateInfo.market_regime || '—'}
        </span>
        <span style={{ color: '#6e7a8a', fontSize: 11 }}>|</span>
        <span style={{ color: '#8b949e', fontSize: 12 }}>
          门槛: {(gateInfo.min_probability*100).toFixed(0)}% | 上限: {gateInfo.max_stocks}只
        </span>
        {gateInfo.breadth && (
          <>
            <span style={{ color: '#6e7a8a', fontSize: 11 }}>|</span>
            <span style={{ fontSize: 11, color: (gateInfo.breadth.advance_pct || 50) < 35 ? '#ef4444' : '#8b949e' }}>
              涨跌比: {gateInfo.breadth.advance_pct || '?'}%上涨
            </span>
          </>
        )}
        {gateInfo.style?.bias && gateInfo.style.bias !== 'unknown' && (
          <>
            <span style={{ color: '#6e7a8a', fontSize: 11 }}>|</span>
            <span style={{ fontSize: 11, color: '#8b949e' }}>
              风格: {gateInfo.style.bias === 'large_cap' ? '大盘' : gateInfo.style.bias === 'small_cap' ? '小盘' : '均衡'}
            </span>
          </>
        )}
        {gateInfo.volume_trend?.direction && (
          <>
            <span style={{ color: '#6e7a8a', fontSize: 11 }}>|</span>
            <span style={{ fontSize: 11, color: gateInfo.volume_trend.direction === 'shrinking' ? '#ef4444' : '#8b949e' }}>
              成交额: {gateInfo.volume_trend.direction === 'expanding' ? '放量' : gateInfo.volume_trend.direction === 'shrinking' ? '缩量' : '平稳'}
            </span>
          </>
        )}
      </div>
      {gateInfo.suitable_strategies?.length > 0 && (
        <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: '#4b5563' }}>推荐策略:</span>
          {gateInfo.suitable_strategies.map((s: string, i: number) => (
            <span key={i} style={{ fontSize: 10, padding: '1px 6px', borderRadius: 3, background: 'rgba(139,92,246,0.1)', color: '#a78bfa' }}>{s}</span>
          ))}
        </div>
      )}
      {(gateInfo.adjustments || []).filter((r: string) => !r.startsWith('市场:') && !r.startsWith('涨跌比')).map((r: string, i: number) => (
        <div key={i} style={{ fontSize: 10, color: '#f59e0b', marginTop: 4 }}>{r}</div>
      ))}
    </div>
  );
}
