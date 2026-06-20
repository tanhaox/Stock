import { useState, useRef } from 'react';
import api from '../lib/api';

interface ScanProgress {
  phase: string;
  current: number;
  total: number;
  extra?: string;
}

export default function TailMarketPage() {
  const [results, setResults] = useState<any[]>([]);
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState<ScanProgress | null>(null);
  const [phaseMsg, setPhaseMsg] = useState('');
  const [scanTime, setScanTime] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const startScan = async () => {
    setScanning(true);
    setResults([]);
    setProgress(null);
    setPhaseMsg('');
    abortRef.current = new AbortController();

    try {
      const response = await fetch('/api/scan/tail-market', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
        signal: abortRef.current.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) return;
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const event = JSON.parse(line.slice(6));
              setProgress(event);
              if (event.extra) setPhaseMsg(event.extra);
              if (event.phase === 'done') {
                setResults(event.results || []);
                setScanning(false);
                setScanTime(new Date().toLocaleTimeString('zh-CN'));
              } else if (event.phase === 'error') {
                setScanning(false);
                alert(event.message || '扫描失败');
              }
            } catch {}
          }
        }
      }
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        setScanning(false);
        alert('连接失败，请确认后端已启动');
      }
    }
  };

  const downloadTXT = () => {
    if (results.length === 0) return;
    const lines = results.map((r: any) => r.symbol);
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tail_${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const phaseLabel = (phase: string) => {
    switch (phase) {
      case 'phase1': return '前5维筛选 (股价/涨幅/量比/换手/市值)';
      case 'phase2': return 'K线分析 (量能阶梯/均线/涨停检测)';
      case 'phase3': return '分时分析 (强于大盘/均价线)';
      default: return '';
    }
  };

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: 24, background: '#0b0e14', minHeight: '100vh', color: '#c9d1d9', fontFamily: 'system-ui' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div>
          <h2 style={{ margin: 0 }}>隔天战法 · 尾盘选股</h2>
          <p style={{ color: '#6e7a8a', fontSize: 12, marginTop: 4 }}>
            10 维筛选：股价 3~80 · 涨幅 3~5% · 量比＞1 · 换手 5~10% · 市值 50~200亿 · 量阶梯 · 均线多头 · 强于大盘 · 均价线上 · 近20日涨停
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {results.length > 0 && (
            <button onClick={downloadTXT}
              style={{
                padding: '8px 18px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                border: '1px solid #10b981', background: 'rgba(16,185,129,0.08)', color: '#34d399',
              }}>
              📥 下载TXT ({results.length}只)
            </button>
          )}
          <button onClick={startScan} disabled={scanning}
            style={{
              padding: '10px 28px', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600,
              cursor: scanning ? 'not-allowed' : 'pointer',
              background: scanning ? '#374151' : '#ef4444', color: '#fff',
            }}>
            {scanning ? '扫描中...' : '⚡ 尾盘扫描'}
          </button>
        </div>
      </div>

      {/* 进度面板 */}
      {scanning && progress && (
        <div style={{ marginBottom: 20, padding: 16, background: '#161b27', border: '1px solid #1e2535', borderRadius: 12 }}>
          <div style={{ fontSize: 12, color: '#3b82f6', marginBottom: 6 }}>
            {phaseLabel(progress.phase)}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ flex: 1, height: 6, background: '#1e2535', borderRadius: 3, overflow: 'hidden' }}>
              <div style={{
                width: progress.total > 0 ? `${Math.min(99, progress.current / progress.total * 100)}%` : '20%',
                height: '100%', borderRadius: 3,
                background: 'linear-gradient(90deg, #f59e0b, #ef4444)',
                transition: 'width 0.3s',
              }} />
            </div>
            <span style={{ fontSize: 11, color: '#6e7a8a', minWidth: 50 }}>
              {progress.current}/{progress.total}
            </span>
          </div>
          {phaseMsg && (
            <div style={{ fontSize: 11, color: '#6e7a8a', marginTop: 8 }}>{phaseMsg}</div>
          )}
        </div>
      )}

      {/* 结果表格 */}
      {results.length > 0 && (
        <div>
          <div style={{ fontSize: 12, color: '#6e7a8a', marginBottom: 12 }}>
            扫描时间: {scanTime} | 候选: {results.length} 只 | 按维度数降序 (≥8维)
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', background: '#161b27', borderRadius: 12, overflow: 'hidden' }}>
            <thead><tr style={{ background: '#1a2030' }}>
              {['代码','名称','现价','涨幅%','量比','换手%','市值(亿)','维度','通过','总分'].map(h => (
                <th key={h} style={{ padding: '10px 12px', textAlign: 'left', fontSize: 11, color: '#6e7a8a', fontWeight: 500 }}>{h}</th>
              ))}
            </tr></thead>
            <tbody>
              {results.map((r: any, i: number) => (
                <tr key={i} style={{ borderTop: '1px solid #1e2535',
                  background: r.dim_count >= 9 ? 'rgba(239,68,68,0.04)' : r.dim_count >= 8 ? 'rgba(245,158,11,0.03)' : 'transparent' }}>
                  <td style={{ padding: '9px 12px' }}><code style={{ color: '#06b6d4' }}>{r.symbol}</code></td>
                  <td style={{ padding: '9px 12px', fontWeight: 600 }}>{r.name || r.symbol}</td>
                  <td style={{ padding: '9px 12px' }}>{r.close?.toFixed(2)}</td>
                  <td style={{ padding: '9px 12px', color: '#ef4444', fontWeight: 600 }}>+{r.change_pct}%</td>
                  <td style={{ padding: '9px 12px' }}>{r.volume_ratio}</td>
                  <td style={{ padding: '9px 12px' }}>{r.turnover_rate}%</td>
                  <td style={{ padding: '9px 12px' }}>{r.circ_mv}</td>
                  <td style={{ padding: '9px 12px' }}>
                    <span style={{
                      padding: '2px 6px', borderRadius: 4, fontWeight: 700, fontSize: 11,
                      background: r.dim_count >= 10 ? 'rgba(239,68,68,0.12)' : r.dim_count >= 9 ? 'rgba(245,158,11,0.1)' : 'rgba(16,185,129,0.08)',
                      color: r.dim_count >= 10 ? '#ef4444' : r.dim_count >= 9 ? '#f59e0b' : '#10b981',
                    }}>{r.dim_count}/10</span>
                  </td>
                  <td style={{ padding: '9px 12px', fontSize: 10, color: '#6e7a8a', maxWidth: 180 }}>
                    {(r.dim_labels || []).join(' · ')}
                  </td>
                  <td style={{ padding: '9px 12px' }}>
                    <span style={{
                      padding: '2px 8px', borderRadius: 4, fontWeight: 700, fontSize: 12,
                      background: r.total_score >= 70 ? 'rgba(239,68,68,0.1)' : r.total_score >= 55 ? 'rgba(245,158,11,0.1)' : 'rgba(107,114,128,0.1)',
                      color: r.total_score >= 70 ? '#ef4444' : r.total_score >= 55 ? '#f59e0b' : '#9ca3af',
                    }}>{r.total_score}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 空状态 */}
      {!scanning && results.length === 0 && (
        <div style={{ textAlign: 'center', padding: 80, color: '#4b5563' }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>⚡</div>
          <div style={{ fontSize: 14, marginBottom: 4 }}>下午 2:30 点击「尾盘扫描」</div>
          <div style={{ fontSize: 12, marginBottom: 16 }}>10 维筛选 · 至少通过 8 维才显示 · 按维度数降序排列</div>
          <div style={{ fontSize: 11, color: '#3d4756', background: '#161b27', padding: '10px 20px', borderRadius: 8, display: 'inline-block', textAlign: 'left', lineHeight: 1.8 }}>
            可能原因：<br/>
            · 当日无符合条件的股票（市场整体偏弱）<br/>
            · 非交易时段（数据未更新，自动回退前一日）<br/>
            · 筛选条件严格（10中8），建议耐心等待机会
          </div>
        </div>
      )}
    </div>
  );
}
