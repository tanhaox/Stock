import { T } from '../lib/designTokens';

type Props = {
  label: string;
  value: string | number;
  trend?: string;
  trendUp?: boolean;
  size?: 'lg' | 'md' | 'sm';
  color?: string;
};

export default function MetricCard({ label, value, trend, trendUp, size = 'md', color }: Props) {
  const f = size === 'lg' ? T.font.dataLg : size === 'sm' ? T.font.dataSm : T.font.dataMd;
  return (
    <div style={{ flex:1, minWidth:100, padding:T.space.md, background:T.card.bg, border:T.card.border, borderRadius:T.radius.card, textAlign:'center' }}>
      <div style={{ ...T.font.label, marginBottom:4 }}>{label}</div>
      <div style={{ fontSize:f.fontSize, fontWeight:f.fontWeight, color:color || f.color }}>{value}</div>
      {trend && (
        <div style={{ fontSize:10, color:trendUp===false?'#ef4444':trendUp?'#10b981':'#6e7a8a', marginTop:3 }}>
          {trendUp===false?'↓':trendUp?'↑':''}{trend}
        </div>
      )}
    </div>
  );
}
