import { useEffect, useState } from 'react';
import { Modal, Spin, Tag, Button, Alert } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';
import api from '../lib/api';

interface Props { symbols: string[]; scores: Record<string, number>; stockNames: Record<string, string>; open: boolean; onClose: () => void; onComplete: (r: any[]) => void; }

export default function DeepAnalysisModal({ symbols, scores, stockNames, open, onClose, onComplete }: Props) {
  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  const [error, setError] = useState('');
  useEffect(() => { if (!open || symbols.length === 0) return; setLoading(true); setError(''); setResults([]);
    // v4.4: 改为调用 /llm/auto-analyze SSE 流式端点 (旧 /llm/deep-analysis 路由不存在)
    const params = new URLSearchParams();
    params.set('symbols', symbols.join(','));
    const eventSource = new EventSource(`${import.meta.env.BASE_URL}api/llm/auto-analyze?symbols=${encodeURIComponent(symbols.join(','))}`);
    // SSE endpoints need POST, use fetch with streaming
    const controller = new AbortController();
    fetch('/api/llm/auto-analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbols }),
      signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No stream');
      const decoder = new TextDecoder();
      let buffer = '';
      const allResults: any[] = [];
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
              if (event.type === 'progress' && event.result) {
                const existing = allResults.find(r => r.symbol === event.result.symbol);
                if (existing) {
                  Object.assign(existing, event.result);
                } else {
                  allResults.push(event.result);
                }
                setResults([...allResults]);
              }
              if (event.type === 'done') {
                const mapped = event.individual.map((r: any) => ({
                  symbol: r.symbol,
                  name: r.name,
                  status: r.status === 'success' ? 'completed' : 'failed',
                  original_score: scores[r.symbol] || 0,
                  adjusted_score: scores[r.symbol] || 0,
                  signals: [...(r.positive_signals || []).map((s: any) => ({ direction: 'positive', description: s.description || JSON.stringify(s) })),
                           ...(r.negative_signals || []).map((s: any) => ({ direction: 'negative', description: s.description || JSON.stringify(s) }))],
                  report: '',
                }));
                setResults(mapped);
                onComplete(mapped);
              }
            } catch {}
          }
        }
      }
    }).catch(e => {
      if (e.name !== 'AbortError') setError(e?.message || '分析失败');
    }).finally(() => setLoading(false));
    return () => controller.abort();
  }, [open, symbols.length]);

  const C = { bg: '#f8f9fa', bg2: '#f0f1f3', border: '#e5e7eb', text: '#1f2937', muted: '#6b7280', accent: '#6366f1' };

  return <Modal title="AI 深度分析" open={open} onCancel={onClose} width={800} footer={<Button onClick={onClose}>{loading?'分析中...':'关闭'}</Button>} destroyOnClose>
    {loading && <div style={{textAlign:'center',padding:40}}><Spin indicator={<LoadingOutlined style={{fontSize:32}} spin/>}/><div style={{marginTop:16,color:C.muted}}>DeepSeek 分析中，约需 30-60 秒...</div></div>}
    {error && <Alert type="error" message={error} style={{marginBottom:12}}/>}
    {!loading && results.length === 0 && !error && <div style={{textAlign:'center',padding:24,color:C.muted}}>暂无结果</div>}
    {results.map((r,i) => <div key={i} style={{marginBottom:16,padding:14,borderRadius:8,border:`1px solid ${C.border}`,background:C.bg}}>
      <div style={{fontWeight:600,fontSize:14,color:C.text}}>{r.symbol} {stockNames[r.symbol]||''} {r.status==='failed'?<Tag color="error">失败</Tag>:<Tag color="success">完成</Tag>}</div>
      {r.signals?.length > 0 && <div style={{marginBottom:8}}>{r.signals.map((s:any,j:number)=><Tag key={j} color={s.direction==='positive'?'success':'error'} style={{marginBottom:4}}>{s.description}</Tag>)}</div>}
      <div style={{fontSize:13,color:C.text}}><span style={{color:C.muted}}>原始分: {r.original_score}</span> → <strong style={{color:r.adjusted_score!==r.original_score?C.accent:C.text}}>调整后: {r.adjusted_score}</strong></div>
      {r.report && <div style={{marginTop:10,padding:12,background:C.bg2,borderRadius:6,fontSize:12,lineHeight:1.7,maxHeight:300,overflow:'auto',whiteSpace:'pre-wrap',border:`1px solid ${C.border}`,color:C.text}}>{r.report.replace(/```json[\s\S]*?```/g,'').replace(/```[\s\S]*?```/g,'').replace(/\n*\{[^{}]*"negative_signals"[^{}]*"positive_signals"[^{}]*\}\s*$/g,'').trim()}</div>}
    </div>)}
  </Modal>;
}
