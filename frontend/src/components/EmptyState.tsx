export default function EmptyState({ icon = '📊', text = '暂无数据' }: { icon?: string; text?: string }) {
  return (
    <div style={{ textAlign: 'center', padding: '40px 20px', color: '#4b5563' }}>
      <div style={{ fontSize: 32, marginBottom: 8 }}>{icon}</div>
      <div style={{ fontSize: 13 }}>{text}</div>
    </div>
  );
}
