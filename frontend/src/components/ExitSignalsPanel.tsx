import React from 'react';

interface ExitSignal {
  symbol: string;
  priority: 'critical' | 'high' | 'info';
  type: string;
  reason: string;
  suggested_action: string;
}

interface Props {
  exitSignals: ExitSignal[];
  exitLoading: boolean;
  dataLength: number;
  onCheck: () => void;
}

const TYPE_LABELS: Record<string, string> = {
  take_profit: '止盈', stop_loss: '止损', atr_stop_loss: 'ATR止损',
  trailing_stop: '移动止盈', time_exit: '时间退出',
  distribution_warning: '放量滞涨', gap_fill_risk: '缺口回补',
};

export default function ExitSignalsPanel({ exitSignals, exitLoading, dataLength, onCheck }: Props) {
  return (
    <div style={{ marginTop: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 16 }}>退出信号检测</h3>
        <button onClick={onCheck} disabled={exitLoading || dataLength === 0}
          style={{ padding: '4px 16px', borderRadius: 6, border: '1px solid #f59e0b', background: 'rgba(245,158,11,0.08)', color: '#f59e0b', cursor: dataLength > 0 ? 'pointer' : 'not-allowed', fontSize: 12, fontWeight: 600 }}>
          {exitLoading ? '检测中...' : '扫描退出信号'}
        </button>
      </div>
      {exitSignals.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {exitSignals.map((sig, i) => (
            <div key={i} style={{ padding: '8px 14px', borderRadius: 6, fontSize: 12,
              background: sig.priority === 'critical' ? 'rgba(239,68,68,0.1)' :
                          sig.priority === 'high' ? 'rgba(245,158,11,0.08)' : 'rgba(59,130,246,0.06)',
              border: `1px solid ${sig.priority === 'critical' ? 'rgba(239,68,68,0.25)' :
                                   sig.priority === 'high' ? 'rgba(245,158,11,0.2)' : 'rgba(59,130,246,0.12)'}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 600, color: '#c9d1d9' }}>{sig.symbol}</span>
                <span style={{ padding: '1px 6px', borderRadius: 3, fontSize: 10, fontWeight: 600,
                  background: sig.priority === 'critical' ? 'rgba(239,68,68,0.2)' :
                              sig.priority === 'high' ? 'rgba(245,158,11,0.2)' : 'rgba(59,130,246,0.15)',
                  color: sig.priority === 'critical' ? '#ef4444' :
                         sig.priority === 'high' ? '#f59e0b' : '#3b82f6' }}>
                  {sig.priority === 'critical' ? '!!' : sig.priority === 'high' ? '!' : 'i'}
                </span>
                <span style={{ color: '#8b949e' }}>{TYPE_LABELS[sig.type] || sig.type}</span>
                <span style={{ color: '#c9d1d9', flex: 1 }}>{sig.reason}</span>
                <span style={{ color: '#f59e0b', fontSize: 11 }}>{sig.suggested_action}</span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ padding: '10px 14px', borderRadius: 6, background: 'rgba(59,130,246,0.04)', border: '1px solid rgba(59,130,246,0.08)', fontSize: 12, color: '#6e7a8a' }}>
          {exitLoading ? '正在扫描...' : '点击按钮检测持仓退出信号（止盈/止损/移动止盈）'}
        </div>
      )}
    </div>
  );
}
