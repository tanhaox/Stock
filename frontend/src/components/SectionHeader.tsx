import type { ReactNode } from 'react';

type Props = { title: string; count?: number; badge?: string; actions?: ReactNode };

export default function SectionHeader({ title, count, badge, actions }: Props) {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:12 }}>
      <div style={{ display:'flex', alignItems:'center', gap:8 }}>
        <h3 style={{ margin:0, fontSize:14, color:'#c9d1d9', fontWeight:600 }}>{title}</h3>
        {count != null && (
          <span style={{ padding:'1px 8px', borderRadius:10, fontSize:11, fontWeight:600, background:'rgba(59,130,246,0.1)', color:'#3b82f6' }}>
            {count}
          </span>
        )}
        {badge && (
          <span style={{ padding:'2px 8px', borderRadius:4, fontSize:10, fontWeight:600, background:'rgba(139,92,246,0.1)', color:'#a78bfa' }}>
            {badge}
          </span>
        )}
      </div>
      {actions && <div style={{ display:'flex', gap:8 }}>{actions}</div>}
    </div>
  );
}
