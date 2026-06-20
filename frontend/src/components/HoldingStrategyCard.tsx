import React from 'react';

interface Props {
  autoStrategy: any;
  autoStratLoading: boolean;
  dataLength: number;
  onRefresh: () => void;
}

export default function HoldingStrategyCard({ autoStrategy, autoStratLoading, dataLength, onRefresh }: Props) {
  return (
    <div style={{ marginTop: 24, marginBottom: 20, padding: 16, background: '#161b27', border: '1px solid #1e2535', borderRadius: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14, color: '#c9d1d9' }}>📊 持仓策略自动诊断</h3>
        <button onClick={onRefresh} disabled={autoStratLoading || dataLength === 0}
          style={{ padding: '4px 16px', borderRadius: 6, border: '1px solid #8b5cf6', background: 'rgba(139,92,246,0.08)', color: '#a78bfa', cursor: dataLength > 0 ? 'pointer' : 'not-allowed', fontSize: 12, fontWeight: 600 }}>
          {autoStratLoading ? '⏳ 分析中...' : autoStrategy ? '🔄 刷新分析' : '🔍 一键诊断'}
        </button>
      </div>
      {autoStrategy && (
        <div>
          {/* 板块集中度告警 */}
          {autoStrategy.concentration_warnings?.length > 0 && (
            <div style={{ marginBottom: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
              {autoStrategy.concentration_warnings.map((w: string, i: number) => (
                <div key={i} style={{ padding: '6px 10px', borderRadius: 6, fontSize: 12,
                  background: w.startsWith('⚠') ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.06)',
                  border: `1px solid ${w.startsWith('⚠') ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.1)'}`,
                  color: w.startsWith('⚠') ? '#ef4444' : '#f59e0b' }}>
                  {w}
                </div>
              ))}
            </div>
          )}
          {/* 逐只策略 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
            {(autoStrategy.stock_strategies || []).map((st: any) => (
              <div key={st.symbol} style={{ padding: 10, borderRadius: 8, background: '#0b0e14',
                border: st.has_critical ? '1px solid rgba(239,68,68,0.2)' : '1px solid #1e2535' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#c9d1d9' }}>{st.symbol} {st.name}</span>
                  <span style={{ fontSize: 11, color: st.pnl_pct >= 0 ? '#ef4444' : '#10b981' }}>{st.pnl_pct >= 0 ? '+' : ''}{st.pnl_pct?.toFixed(1)}%</span>
                </div>
                <div style={{ display: 'flex', gap: 12, marginBottom: 6, fontSize: 11, color: '#8b949e' }}>
                  {st.composite_score != null && <span>评分: {st.composite_score}</span>}
                  <span>权重: {st.weight_pct}%</span>
                  {st.win_probability != null && <span>胜率: {(st.win_probability*100).toFixed(0)}%</span>}
                </div>
                <div style={{ padding: '4px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                  background: st.suggested_action?.includes('止损') ? 'rgba(239,68,68,0.1)' :
                              st.suggested_action?.includes('加仓') ? 'rgba(16,185,129,0.1)' :
                              st.suggested_action?.includes('减仓') ? 'rgba(245,158,11,0.1)' : 'rgba(59,130,246,0.06)',
                  color: st.suggested_action?.includes('止损') ? '#ef4444' :
                         st.suggested_action?.includes('加仓') ? '#10b981' :
                         st.suggested_action?.includes('减仓') ? '#f59e0b' : '#3b82f6' }}>
                  → {st.suggested_action || '—'}
                </div>
                {st.exit_signals?.length > 0 && (
                  <div style={{ marginTop: 4, fontSize: 10, color: '#ef4444' }}>
                    ⚠ {st.exit_signals.map((s: any) => s.type.replace(/_/g, ' ')).join(', ')}
                  </div>
                )}
                {st.error && <div style={{ fontSize: 10, color: '#6e7a8a' }}>⚠ {st.error}</div>}
              </div>
            ))}
          </div>
          {autoStrategy.total_value > 0 && (
            <div style={{ marginTop: 8, fontSize: 11, color: '#6e7a8a', textAlign: 'right' }}>
              总市值: ¥{(autoStrategy.total_value / 10000).toFixed(1)}万 | {autoStrategy.holdings_count}只持仓
            </div>
          )}
        </div>
      )}
      {!autoStrategy && !autoStratLoading && dataLength > 0 && (
        <div style={{ fontSize: 12, color: '#6e7a8a' }}>点击"一键诊断"自动分析持仓风险和操作建议</div>
      )}
    </div>
  );
}
