import { useEffect, useState } from 'react';
import api from '../lib/api';

export default function AmbushPage() {
  const [data, setData] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState('');
  const [scanDate, setScanDate] = useState('');

  const load = () => {
    setLoading(true);
    api.get('/ambush-signals', { params: { limit: 30 } })
      .then(r => { setData(r.data.data || []); setScanDate(r.data.data?.[0]?.scan_date || ''); })
      .catch(() => setError('加载失败，请检查后端服务'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const doScan = async () => {
    setScanning(true);
    try {
      const r = await api.post('/ambush-signals/trigger');
      if (r.data.status === 'success') {
        await load();
      }
    } catch (e: any) { alert(e?.response?.data?.detail || '扫描失败'); }
    setScanning(false);
  };

  const headers = ['代码', '名称', '涨停日期', '涨停涨幅', '最大回撤', '缩量比', '启动量比', '综合评分'];

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>潜伏猎手</h2>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {scanDate && <span style={{ fontSize: 11, color: '#6e7a8a' }}>会话: {scanDate}</span>}
          <button onClick={doScan} disabled={scanning}
            style={{ padding: '8px 18px', border: 'none', borderRadius: 6, fontSize: 12, fontWeight: 600,
              cursor: scanning ? 'not-allowed' : 'pointer', background: scanning ? '#374151' : '#ef4444', color: '#fff' }}>
            {scanning ? '扫描中...' : '🔍 刷新扫描'}
          </button>
        </div>
      </div>

      {loading ? <div style={{ padding: 24, color: '#6e7a8a' }}>加载潜伏数据中...</div>
       : error ? <div style={{ padding: 24, color: '#ef4444' }}>⚠ {error}</div>
       : (
        <table style={{ width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 12 }}>
          <thead><tr style={{ background: '#1a2030' }}>
            {headers.map(h => <th key={h} style={{ padding: '10px 14px', textAlign: 'left', fontSize: 11, color: '#6e7a8a' }}>{h}</th>)}
          </tr></thead>
          <tbody>
            {data.length === 0 ? (
              <tr><td colSpan={8} style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>暂无潜伏信号，点击「刷新扫描」触发</td></tr>
            ) : data.map((r: any, i: number) => (
              <tr key={i} style={{ borderTop: '1px solid #1e2535' }}>
                <td style={{ padding: '9px 14px' }}><code style={{ color: '#06b6d4' }}>{r.symbol}</code></td>
                <td style={{ padding: '9px 14px' }}>{r.name}</td>
                <td style={{ padding: '9px 14px' }}>{r.limit_up_date}</td>
                <td style={{ padding: '9px 14px', color: '#ef4444' }}>+{r.limit_up_gain}%</td>
                <td style={{ padding: '9px 14px' }}>-{r.max_drawdown}%</td>
                <td style={{ padding: '9px 14px' }}>{r.vol_shrink_ratio?.toFixed(2)}</td>
                <td style={{ padding: '9px 14px' }}>{r.launch_vol_ratio?.toFixed(2)}</td>
                <td style={{ padding: '9px 14px' }}>
                  <span style={{ padding: '2px 8px', borderRadius: 4, fontWeight: 700,
                    background: r.composite_score >= 80 ? 'rgba(239,68,68,0.1)' : 'rgba(245,158,11,0.1)',
                    color: r.composite_score >= 80 ? '#ef4444' : '#f59e0b' }}>
                    {r.composite_score}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
