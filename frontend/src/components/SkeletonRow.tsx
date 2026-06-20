const bar = { height: 12, background: '#1e2535', borderRadius: 3, marginBottom: 4 } as const;
const pulse: React.CSSProperties = { animation: 'skeleton-pulse 1.5s ease-in-out infinite', background: '#1e2535' };

export default function SkeletonRow({ cols = 8 }: { cols?: number }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} style={{ padding: '8px 10px' }}>
          <div style={{ ...bar, width: `${50 + Math.random() * 40}%`, ...pulse }} />
        </td>
      ))}
    </tr>
  );
}
