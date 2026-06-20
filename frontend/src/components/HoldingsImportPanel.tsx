import React from 'react';
import api from '../lib/api';

interface Props {
  data: any[];
  summary: any;
  account: any;
  alerts: any[];
  showAdd: boolean;
  setShowAdd: (v: boolean) => void;
  importText: string;
  setImportText: (v: string) => void;
  importing: boolean;
  importResult: any;
  doImport: () => void;
  setImportResult: (v: any) => void;
  loadAccount: () => void;
}

export default function HoldingsImportPanel({
  data, summary, account, alerts, showAdd, setShowAdd,
  importText, setImportText, importing, importResult,
  doImport, setImportResult, loadAccount,
}: Props) {
  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>持仓管理</h2>
        <button onClick={() => { setShowAdd(!showAdd); setImportResult(null); }}
          style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid #10b981', background: 'rgba(16,185,129,0.1)', color: '#10b981', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
          {showAdd ? '取消' : '📥 更新持仓'}
        </button>
      </div>

      {showAdd && (
        <div style={{ marginBottom: 16, padding: 16, background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
          <div style={{ fontSize: 13, color: '#c9d1d9', marginBottom: 8 }}>粘贴券商导出的持仓数据（同花顺/东方财富等导出格式）</div>
          <textarea value={importText} onChange={e => setImportText(e.target.value)}
            placeholder="粘贴持仓数据...&#10;支持制表符分隔的券商导出格式，LLM自动识别字段"
            style={{ width: '100%', minHeight: 180, padding: 12, borderRadius: 8, background: '#0b0e14', border: '1px solid #1e2535', color: '#c9d1d9', fontSize: 11, fontFamily: 'monospace', resize: 'vertical' }} />
          <div style={{ display: 'flex', gap: 10, marginTop: 10, alignItems: 'center' }}>
            <button onClick={doImport} disabled={importing}
              style={{ padding: '8px 24px', borderRadius: 6, border: 'none', background: importing ? '#374151' : '#10b981', color: '#fff', cursor: importing ? 'not-allowed' : 'pointer', fontSize: 13, fontWeight: 600 }}>
              {importing ? 'LLM解析中...' : '解析并导入'}
            </button>
            {importResult && (
              <div style={{ fontSize: 12 }}>
                <span style={{ color: importResult.status === 'success' ? '#10b981' : '#ef4444' }}>
                  {importResult.status === 'success'
                    ? `✓ 导入 ${importResult.imported} 只`
                    : `✗ ${importResult.detail}`}
                  {(importResult.auto_closed || 0) > 0 && (
                    <span style={{ marginLeft: 8, color: '#10b981' }}>
                      | 🧹 自动识别清仓 {importResult.auto_closed} 只
                      {importResult.auto_closed_list?.map((c:any) => (
                        <span key={c.symbol} style={{ marginLeft: 4, color: c.pnl >= 0 ? '#ef4444' : '#10b981' }}>
                          {c.symbol}({c.pnl >= 0 ? '+' : ''}¥{c.pnl.toFixed(0)})
                        </span>
                      ))}
                    </span>
                  )}
                  {(importResult.pending_close || 0) > 0 && (
                    <span style={{ marginLeft: 8, color: '#f59e0b' }}>
                      | ⚠ {importResult.pending_close} 只待手动清仓
                    </span>
                  )}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* alers */}
      {alerts.length > 0 && (
        <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 8, padding: 12, marginBottom: 16 }}>
          <strong style={{ color: '#ef4444', fontSize: 13 }}>⚠ 浮亏超5%:</strong>
          {alerts.map((a: any, i: number) => (
            <span key={i} style={{ marginLeft: 12, fontSize: 12, color: '#f87171' }}>{a.symbol} {a.name} {a.pnl_pct?.toFixed(1)}%</span>
          ))}
        </div>
      )}
    </>
  );
}
