import { getSignalStyle, type SignalType } from '../lib/signalColor';

export default function StatusBadge({ type, label }: { type: SignalType; label?: string }) {
  const s = getSignalStyle(type);
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 600,
      background: s.bg, color: s.color, border: `1px solid ${s.color}30`,
      display: 'inline-flex', alignItems: 'center', gap: 3,
    }}>
      <span style={{ fontSize: 11 }}>{s.icon}</span>
      {label || s.label}
    </span>
  );
}
