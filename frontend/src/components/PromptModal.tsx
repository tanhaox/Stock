import { useState } from 'react';
import { Modal, Button } from 'antd';
import api from '../lib/api';

interface Props {
  symbols: string[];
  stockNames: Record<string, string>;
  scores: Record<string, number>;
  open: boolean;
  onClose: () => void;
}

export default function PromptModal({ symbols, scores, stockNames, open, onClose }: Props) {
  const [prompts, setPrompts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const [decisions, setDecisions] = useState<Record<string, string>>({});

  const generate = async () => {
    setLoading(true);
    try {
      const r = await api.post('/llm/generate-prompt', { symbols });
      setPrompts(r.data.data || []);
    } catch (e: any) {
      alert(e?.response?.data?.detail || '生成失败');
    }
    setLoading(false);
  };

  // Auto-generate on open
  useState(() => { if (open && symbols.length > 0) generate(); });

  const copy = async (text: string, idx: number) => {
    await navigator.clipboard.writeText(text);
    setCopiedIdx(idx);
    setTimeout(() => setCopiedIdx(null), 2000);
  };

  const decide = async (symbol: string, action: string) => {
    try {
      await api.post('/user-decisions', { symbol, action, decision_reason: '人工精选' });
      setDecisions(prev => ({ ...prev, [symbol]: action }));
    } catch {}
  };

  const actionLabel = (a: string) => a === 'buy' ? '买入' : a === 'watch' ? '观察' : '放弃';
  const actionColor = (a: string) => a === 'buy' ? '#10b981' : a === 'watch' ? '#f59e0b' : '#ef4444';

  return (
    <Modal title="人工精选 — 生成 DeepSeek 提示词" open={open} onCancel={onClose} width={900}
      footer={<Button onClick={onClose}>关闭</Button>} destroyOnClose>
      {loading && <div style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>生成提示词中...</div>}
      {!loading && prompts.length === 0 && (
        <div style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>暂无数据</div>
      )}
      {prompts.map((p: any, i: number) => (
        <div key={i} style={{ marginBottom: 20, padding: 16, background: '#0b0e14', border: '1px solid #1e2535', borderRadius: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontSize: 15, fontWeight: 600 }}>{p.symbol}</span>
              <span style={{ fontSize: 13, color: '#c9d1d9' }}>{stockNames[p.symbol] || p.name}</span>
              <span style={{ fontSize: 11, color: '#6e7a8a' }}>评分: {scores[p.symbol] || '?'}</span>
              {p.context?.archetype && (
                <span style={{ fontSize: 10, color: '#a78bfa', background: 'rgba(139,92,246,.1)', padding: '2px 6px', borderRadius: 4 }}>
                  {p.context.archetype}
                </span>
              )}
            </div>
            <Button size="small" onClick={() => copy(p.prompt, i)}
              style={{ borderColor: copiedIdx === i ? '#10b981' : '#3b82f6', color: copiedIdx === i ? '#10b981' : '#3b82f6' }}>
              {copiedIdx === i ? '✓ 已复制' : '📋 复制提示词'}
            </Button>
          </div>
          <textarea readOnly value={p.prompt}
            style={{
              width: '100%', minHeight: 200, padding: 12, borderRadius: 8,
              background: '#161b27', border: '1px solid #1e2535', color: '#c9d1d9',
              fontSize: 11, lineHeight: 1.6, fontFamily: 'system-ui', resize: 'vertical',
            }} />

          {/* 使用指引 + 决策按钮 */}
          <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontSize: 11, color: '#6e7a8a' }}>
              ① 复制提示词 → ② chat.deepseek.com 粘贴 → ③ 回复后点页面📋反哺 → ④ 下方确认决策
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              {(['buy', 'watch', 'pass'] as const).map(action => {
                const isDecided = decisions[p.symbol] === action;
                return (
                  <button key={action} onClick={() => decide(p.symbol, action)}
                    style={{
                      padding: '4px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                      border: isDecided ? `1px solid ${actionColor(action)}` : '1px solid #1e2535',
                      background: isDecided ? `${actionColor(action)}15` : 'transparent',
                      color: isDecided ? actionColor(action) : '#6e7a8a',
                    }}>
                    {isDecided ? `✓ ${actionLabel(action)}` : actionLabel(action)}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      ))}
    </Modal>
  );
}
