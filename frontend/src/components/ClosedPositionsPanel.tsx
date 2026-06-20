import React from 'react';

interface Props {
  closedPositions: any[];
  account: any;
}

export default function ClosedPositionsPanel({ closedPositions, account }: Props) {
  if (closedPositions.length === 0) return null;

  return (
    <div style={{ marginTop: 16, padding: 14, background: '#161b27', border: '1px solid #1e2535', borderRadius: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#c9d1d9', marginBottom: 8 }}>
        📋 清仓历史 ({closedPositions.length} 只)
        {account?.closed_realized_pnl != null && (
          <span style={{ marginLeft: 8, fontSize: 12, color: account.closed_realized_pnl >= 0 ? '#ef4444' : '#10b981' }}>
            合计: {account.closed_realized_pnl >= 0 ? '+' : ''}¥{account.closed_realized_pnl.toFixed(0)}
          </span>
        )}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead><tr style={{ color: '#6e7a8a' }}>
          {['代码', '名称', '数量', '买入价', '卖出价', '盈亏', '收益率', '持有天数', '清仓原因'].map(h => (
            <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontWeight: 500 }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {closedPositions.map((c, i) => {
            // 后端 pnl_pct 可能为 0 (DB 默认值), 用 buy_price/sell_price 重算
            const buy = c.buy_price || 0;
            const sell = c.sell_price || 0;
            const pnlPct = (c.pnl_pct && c.pnl_pct !== 0)
              ? c.pnl_pct
              : (buy > 0 ? ((sell - buy) / buy * 100) : 0);
            const pnl = c.realized_pnl || ((sell - buy) * (c.quantity || 1));
            const reasonLabel =
              c.buy_reason === 'broker_cleared' ? '券商清算' :
              c.buy_reason === 'manual_close' ? '手动清仓' :
              c.buy_reason === 'import_replace' ? '持仓更新时清仓' :
              (c.buy_reason || '—');
            return (
            <tr key={i} style={{ borderTop: '1px solid #1e2535' }}>
              <td style={{ padding: '6px 10px' }}><code style={{ color: '#4b5563', fontSize: 10 }}>{c.symbol}</code></td>
              <td style={{ padding: '6px 10px', color: '#8b949e' }}>{c.name}</td>
              <td style={{ padding: '6px 10px' }}>{c.quantity}</td>
              <td style={{ padding: '6px 10px' }}>{buy > 0 ? `¥${buy.toFixed(2)}` : '—'}</td>
              <td style={{ padding: '6px 10px' }}>¥{sell.toFixed(2)}</td>
              <td style={{ padding: '6px 10px', color: pnl >= 0 ? '#ef4444' : '#10b981', fontWeight: 600 }}>
                {pnl >= 0 ? '+' : ''}¥{pnl.toFixed(0)}
              </td>
              <td style={{ padding: '6px 10px', color: pnlPct >= 0 ? '#ef4444' : '#10b981' }}>
                {pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%
              </td>
              <td style={{ padding: '6px 10px', color: '#8b949e' }}>{c.holding_days}天</td>
              <td style={{ padding: '6px 10px', fontSize: 10, color: '#6e7a8a' }}>
                {reasonLabel}
              </td>
            </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
