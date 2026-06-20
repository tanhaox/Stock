import React from 'react';

interface Props {
  visible: boolean;
  onClose: () => void;
  account: any;
  capitalRecords: any[];
  capAmount: string;
  setCapAmount: (v: string) => void;
  capNote: string;
  setCapNote: (v: string) => void;
  capAdding: boolean;
  doCapital: (amountOverride?: number) => void;
}

export default function CapitalAccountModal({
  visible, onClose, account, capitalRecords,
  capAmount, setCapAmount, capNote, setCapNote, capAdding, doCapital,
}: Props) {
  if (!visible) return null;

  return (
    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
      background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }} onClick={onClose}>
      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 14, padding: 28,
        width: 460, maxWidth: '90vw',
      }} onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 16, fontWeight: 700, color: '#c9d1d9', marginBottom: 4 }}>资本账户管理</div>
        <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 20 }}>管理总资金、入金、出金</div>

        {/* 当前状态 */}
        {account && (
          <div style={{ marginBottom: 20, padding: 12, background: '#0b0e14', borderRadius: 8, border: '1px solid #1e2535' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, fontSize: 12 }}>
              <div>
                <div style={{ color: '#6e7a8a', fontSize: 10, marginBottom: 2 }}>累计投入</div>
                <div style={{ color: '#3b82f6', fontWeight: 700 }}>¥{(account.net_capital || 0).toFixed(0)}</div>
              </div>
              <div>
                <div style={{ color: '#6e7a8a', fontSize: 10, marginBottom: 2 }}>可用现金</div>
                <div style={{ color: '#c9d1d9', fontWeight: 700 }}>¥{(account.cash_remaining || 0).toFixed(0)}</div>
              </div>
              <div>
                <div style={{ color: '#6e7a8a', fontSize: 10, marginBottom: 2 }}>账户净值</div>
                <div style={{ color: (account.total_return_pct || 0) >= 0 ? '#ef4444' : '#10b981', fontWeight: 700 }}>
                  ¥{(account.net_account_value || 0).toFixed(0)}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* 初始本金 */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>初始本金（首次设置后不再修改）</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <input value={capAmount} onChange={e => setCapAmount(e.target.value)}
              placeholder="如: 500000"
              style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #1e2535',
                background: '#0b0e14', color: '#c9d1d9', fontSize: 14 }} />
            <button onClick={() => { doCapital(); }}
              disabled={capAdding || !capAmount}
              style={{ padding: '8px 20px', borderRadius: 8, border: 'none', background: (capAdding || !capAmount) ? '#374151' : '#10b981',
                color: '#fff', cursor: (capAdding || !capAmount) ? 'not-allowed' : 'pointer', fontSize: 13, fontWeight: 600, whiteSpace: 'nowrap' }}>
              {capAdding ? '...' : '设置本金'}
            </button>
          </div>
        </div>

        {/* 入金 */}
        <div style={{ marginBottom: 10 }}>
          <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 4 }}>入金 / 出金</div>
          <div style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
            <input onChange={e => setCapAmount(e.target.value)}
              placeholder="金额（+入金 / -出金）"
              style={{ flex: 1, padding: '8px 12px', borderRadius: 8, border: '1px solid #1e2535',
                background: '#0b0e14', color: '#c9d1d9', fontSize: 13 }} />
            <input onChange={e => setCapNote(e.target.value)}
              placeholder="备注"
              style={{ width: 100, padding: '8px 12px', borderRadius: 8, border: '1px solid #1e2535',
                background: '#0b0e14', color: '#c9d1d9', fontSize: 13 }} />
            <button onClick={() => { doCapital(); }}
              disabled={capAdding || !capAmount}
              style={{ padding: '8px 16px', borderRadius: 8, border: 'none',
                background: (capAdding || !capAmount) ? '#374151' :
                  (parseFloat(capAmount) > 0 ? '#10b981' : '#ef4444'),
                color: '#fff', cursor: (capAdding || !capAmount) ? 'not-allowed' : 'pointer', fontSize: 13, fontWeight: 600 }}>
              {capAdding ? '...' : parseFloat(capAmount) > 0 ? '入金' : '出金'}
            </button>
          </div>
        </div>

        {/* 历史记录 */}
        {capitalRecords.length > 0 && (
          <div style={{ marginBottom: 14, maxHeight: 150, overflowY: 'auto' }}>
            <div style={{ fontSize: 10, color: '#6e7a8a', marginBottom: 4 }}>操作记录</div>
            {capitalRecords.map((r, i) => (
              <div key={i} style={{ padding: '4px 8px', display: 'flex', justifyContent: 'space-between', fontSize: 11,
                borderTop: i > 0 ? '1px solid #1e2535' : 'none' }}>
                <span style={{ color: '#8b949e' }}>{r.date?.slice(0,10)}</span>
                <span style={{ color: '#c9d1d9' }}>{r.note || (r.amount > 0 ? '入金' : '出金')}</span>
                <span style={{ fontWeight: 600, color: r.amount > 0 ? '#10b981' : '#ef4444' }}>
                  {r.amount > 0 ? '+' : ''}¥{r.amount.toFixed(0)}
                </span>
              </div>
            ))}
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose}
            style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #374151', background: 'transparent',
              color: '#8b949e', cursor: 'pointer', fontSize: 13 }}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
