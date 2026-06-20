import React from 'react';

interface ThemeSector {
  sector: string;
  net_flow?: number;
  net_flow_mil?: number;
}

interface Theme {
  type: 'main_line' | 'outflow' | string;
  sectors: ThemeSector[];
}

interface Props {
  themes: Theme[];
  setMarketFilter: (sector: string) => void;
}

export default function HotSectorPanel({ themes, setMarketFilter }: Props) {
  if (!themes || themes.length === 0) return null;

  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', width: '100%', marginBottom: 4 }}>
      {themes.map((theme, ti) => (
        <div key={ti} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ fontSize: 10, color: '#4b5563', fontWeight: 600, whiteSpace: 'nowrap' }}>
            {theme.type === 'main_line' ? '主线' : '流出'}:
          </span>
          {(theme.sectors || []).map((s, si) => (
            <span key={si} onClick={() => setMarketFilter(s.sector)}
              style={{ padding: '2px 8px', borderRadius: 10, fontSize: 10, fontWeight: 600, cursor: 'pointer',
                background: theme.type === 'main_line' ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
                border: `1px solid ${theme.type === 'main_line' ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'}`,
                color: theme.type === 'main_line' ? '#10b981' : '#ef4444' }}
              title={`龙虎榜净${s.net_flow! > 0 ? '买入' : '卖出'} ${Math.abs(s.net_flow_mil || 0)}万`}>
              {s.sector}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}
