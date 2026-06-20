export default function ProgressBar({ label, percent, color, height }: {
  label?: string; percent: number; color: string; height?: number;
}) {
  const h = height || 4;
  const pct = Math.min(100, Math.max(0, percent));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      {label && <span style={{ fontSize: 9, color: '#6e7a8a', minWidth: 40 }}>{label}</span>}
      <div style={{ flex: 1, height: h, background: '#1e2535', borderRadius: h / 2, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, borderRadius: h / 2,
          background: `linear-gradient(90deg, ${color}, ${color}cc)` }} />
      </div>
      <span style={{ fontSize: 9, fontWeight: 600, color, width: 36, textAlign: 'right' }}>{pct.toFixed(0)}%</span>
    </div>
  );
}
