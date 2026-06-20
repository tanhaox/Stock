/** 全局预警色配置 — 所有页面统一引用 (v4.5). */

export const SIGNAL_COLORS = {
  buy_strong:  { label: '强买',     color: '#059669', bg: '#ecfdf5', icon: '🔵' },
  buy:         { label: '可买',     color: '#10b981', bg: '#f0fdf4', icon: '🟢' },
  watch:       { label: '关注',     color: '#f59e0b', bg: '#fffbeb', icon: '🟡' },
  caution:     { label: '谨慎',     color: '#f97316', bg: '#fff7ed', icon: '🟠' },
  sell:        { label: '卖出',     color: '#ef4444', bg: '#fef2f2', icon: '🔴' },
  avoid:       { label: '规避',     color: '#7f1d1d', bg: '#fef2f2', icon: '⛔' },
  resonance:   { label: '周线共振', color: '#8b5cf6', bg: '#f5f3ff', icon: '⭐' },
  veteran:     { label: '老兵',     color: '#a855f7', bg: '#faf5ff', icon: '🎖' },
  neutral:     { label: '中性',     color: '#6b7280', bg: '#f9fafb', icon: '➖' },
} as const;

export type SignalType = keyof typeof SIGNAL_COLORS;

export function getSignalStyle(type: SignalType) {
  return SIGNAL_COLORS[type] || SIGNAL_COLORS.neutral;
}

/** 根据股票数据判定操作建议 */
export function getActionSignal(r: any): SignalType {
  const score = r.composite_score || 0;
  const sq = r.signal_quality ?? 0.5;
  const resonance = r.resonance_type || '';
  const riskLabel = r.risk_label || '';
  if (riskLabel === 'dead') return 'avoid';
  if (score >= 65 && sq >= 0.7 && resonance === 'weekly_resonance') return 'buy_strong';
  if (score >= 55 && sq >= 0.5) return 'buy';
  if ((r.hidden_risks || []).length > 0 && riskLabel === 'danger') return 'caution';
  if (sq < 0.4) return 'caution';
  return 'watch';
}

/** 退出信号数→等级 */
export function getExitSignal(count: number): SignalType {
  if (count >= 3) return 'sell';
  if (count >= 1) return 'caution';
  return 'buy';
}

/** PnL%→行背景色+border */
export function getPnlRowStyle(pnlPct: number | null | undefined) {
  if (pnlPct == null) return {};
  if (pnlPct > 0) return { background: 'rgba(16,185,129,0.03)', borderLeft: '2px solid rgba(16,185,129,0.3)' };
  if (pnlPct < -10) return { background: 'rgba(239,68,68,0.06)', borderLeft: '2px solid rgba(239,68,68,0.5)' };
  if (pnlPct < -5) return { background: 'rgba(245,158,11,0.04)', borderLeft: '2px solid rgba(245,158,11,0.3)' };
  return {};
}
