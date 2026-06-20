import EmptyState from './EmptyState';

/**
 * Regime Dashboard (v7.0.34 stub)
 *
 * 历史遗留: ResultPage.tsx 自 2026-06-15 (v5.5) 起就 import 此组件, 但实际文件未提交.
 * 2026-06-20 重构: 创建占位组件, 显示占位 UI 避免 vite 启动失败.
 * TODO: 真正的三层 Regime Dashboard (bull/bear/range + market_coef + sector_coef) 由后续 PR 接入.
 */
export default function RegimeDashboard() {
  return <EmptyState icon="🐂" text="Regime Dashboard 待接入 (v7.0.34 stub)" />;
}