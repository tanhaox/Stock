import React from 'react';

interface Props {
  summary: any;
  account: any;
  data: any[];
}

export default function PortfolioSummary({ summary, account, data }: Props) {
  const pnlCls = (v: number) => v >= 0 ? '#ef4444' : '#10b981';
  const totalVal = data.reduce((s: number, h: any) => s + (h.market_value || 0), 0);
  const sorted = [...data].sort((a, b) => (b.market_value || 0) - (a.market_value || 0));
  const top1Wt = sorted[0] ? ((sorted[0].market_value || 0) / (totalVal || 1) * 100) : 0;
  const top3Wt = sorted.slice(0, 3).reduce((s: number, h: any) => s + (h.market_value || 0), 0) / (totalVal || 1) * 100;
  const losers = data.filter((h: any) => (h.floating_pnl || 0) < 0);
  const loserSum = losers.reduce((s: number, h: any) => s + Math.abs(h.floating_pnl || 0), 0);
  const divScore = data.length >= 5 && top1Wt < 30 ? '良好' : data.length >= 4 && top1Wt < 50 ? '一般' : '集中';

  return (
    <>
      {/* 汇总卡片 */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 20 }}>
        {[
          { label: '持仓数', value: summary.count || 0, color: '#3b82f6' },
          { label: '持仓市值', value: '¥' + ((summary.total_value || 0) / 10000).toFixed(1) + '万', color: '#f59e0b' },
          { label: '持仓浮盈', value: (summary.total_pnl || 0) >= 0 ? '+' + (summary.total_pnl || 0).toFixed(0) : (summary.total_pnl || 0).toFixed(0), color: pnlCls(summary.total_pnl || 0) },
          { label: '清仓盈亏', value: account ? ((account.closed_realized_pnl || 0) >= 0 ? '+' + (account.closed_realized_pnl || 0).toFixed(0) : (account.closed_realized_pnl || 0).toFixed(0)) : '—',
            color: (account?.closed_realized_pnl || 0) >= 0 ? '#ef4444' : '#10b981' },
          { label: '账户净值', value: account ? '¥' + ((account.net_account_value || 0) / 10000).toFixed(1) + '万' : '—',
            color: (account?.total_return_pct || 0) >= 0 ? '#ef4444' : '#10b981' },
          { label: '总收益率', value: account ? ((account.total_return_pct || 0) >= 0 ? '+' : '') + (account.total_return_pct || 0).toFixed(1) + '%' : '—',
            color: (account?.total_return_pct || 0) >= 0 ? '#ef4444' : '#10b981' },
        ].map((m, i) => (
          <div key={i} style={{ flex: 1, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10, padding: 16, textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: m.color }}>{m.value}</div>
            <div style={{ fontSize: 11, color: '#6e7a8a', marginTop: 6 }}>{m.label}</div>
          </div>
        ))}
      </div>

      {/* 组合分析面板 */}
      {data.length > 0 && (
        <div style={{ marginBottom: 20, padding: 16, background: '#161b27', border: '1px solid #1e2535', borderRadius: 12 }}>
          <h3 style={{ fontSize: 14, marginBottom: 12, color: '#c9d1d9' }}>组合分析</h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
            <div style={{ padding: 10, background: '#0b0e14', borderRadius: 8 }}>
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>最大单只权重</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: top1Wt > 30 ? '#10b981' : '#ef4444' }}>
                {top1Wt.toFixed(1)}% <span style={{ fontSize: 10, color: '#6e7a8a' }}>{sorted[0]?.symbol}</span>
              </div>
            </div>
            <div style={{ padding: 10, background: '#0b0e14', borderRadius: 8 }}>
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>前3集中度</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: top3Wt > 60 ? '#10b981' : '#f59e0b' }}>
                {top3Wt.toFixed(1)}%
              </div>
            </div>
            <div style={{ padding: 10, background: '#0b0e14', borderRadius: 8 }}>
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>分散化</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: divScore === '良好' ? '#10b981' : divScore === '一般' ? '#f59e0b' : '#ef4444' }}>
                {divScore}
              </div>
            </div>
            <div style={{ padding: 10, background: '#0b0e14', borderRadius: 8 }}>
              <div style={{ fontSize: 11, color: '#6e7a8a' }}>浮亏股票</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: losers.length > 0 ? '#ef4444' : '#10b981' }}>
                {losers.length}只 / ¥{(loserSum / 10000).toFixed(1)}万
              </div>
            </div>
          </div>
          {top1Wt > 30 && (
            <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 6, background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', fontSize: 11, color: '#f87171' }}>
              ⚠ {sorted[0]?.symbol} 权重超过30%，建议分散以降低单只风险
            </div>
          )}
        </div>
      )}
    </>
  );
}
