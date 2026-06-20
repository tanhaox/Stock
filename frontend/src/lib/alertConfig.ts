/** 全局预警配置 — 所有页面统一引用，避免内联颜色定义 (v4.5). */

export const ALERTS = {
  BUY_STRONG:  { label: '强烈买入', color: '#059669', bg: '#ecfdf5', icon: '🔵' },
  BUY:         { label: '可以买入', color: '#10b981', bg: '#f0fdf4', icon: '🟢' },
  WATCH:       { label: '关注',     color: '#f59e0b', bg: '#fffbeb', icon: '🟡' },
  CAUTION:     { label: '谨慎',     color: '#f97316', bg: '#fff7ed', icon: '🟠' },
  SELL:        { label: '卖出信号', color: '#ef4444', bg: '#fef2f2', icon: '🔴' },
  AVOID:       { label: '规避',     color: '#7f1d1d', bg: '#fef2f2', icon: '⛔' },
} as const;

export type AlertLevel = keyof typeof ALERTS;

/** 根据状态判定操作建议等级 */
export function getActionAlert(r: any): { level: AlertLevel; label: string } {
  const score = r.composite_score || 0;
  const sq = r.signal_quality ?? 0.5;
  const resonance = r.resonance_type || '';
  const riskLabel = r.risk_label || '';
  const hasRisks = (r.hidden_risks || []).length > 0;

  if (riskLabel === 'dead') return { level: 'AVOID', label: '规避' };
  if (score >= 65 && sq >= 0.7 && resonance === 'weekly_resonance')
    return { level: 'BUY_STRONG', label: '共振买入' };
  if (score >= 55 && sq >= 0.5)
    return { level: 'BUY', label: '关注买入' };
  if (hasRisks && riskLabel === 'danger')
    return { level: 'CAUTION', label: '风险警告' };
  if (riskLabel === 'warn')
    return { level: 'WATCH', label: '待观察' };
  if (sq < 0.4)
    return { level: 'CAUTION', label: '信号偏弱' };
  return { level: 'WATCH', label: '关注' };
}

/** 退出信号严重等级 */
export function getExitSeverity(exitCount: number): { level: AlertLevel; label: string } {
  if (exitCount >= 3) return { level: 'SELL', label: '强烈退出' };
  if (exitCount >= 1) return { level: 'CAUTION', label: '关注退出' };
  return { level: 'BUY', label: '继续持有' };
}

/** PnL 染色 */
export function getPnlAlert(pnlPct: number | null | undefined): { level: AlertLevel | null; bg: string; border: string } {
  if (pnlPct == null) return { level: null, bg: 'transparent', border: 'transparent' };
  if (pnlPct > 0) return { level: 'BUY', bg: 'rgba(16,185,129,0.04)', border: '1px solid rgba(16,185,129,0.15)' };
  if (pnlPct < -10) return { level: 'SELL', bg: 'rgba(239,68,68,0.06)', border: '2px solid rgba(239,68,68,0.35)' };
  if (pnlPct < -5) return { level: 'CAUTION', bg: 'rgba(245,158,11,0.04)', border: '1px solid rgba(245,158,11,0.2)' };
  return { level: null, bg: 'transparent', border: 'transparent' };
}
