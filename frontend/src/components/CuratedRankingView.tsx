import React, { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { toPng } from 'html-to-image';
import api from '../lib/api';

const ARCH_CN: Record<string, string> = {
  '主板_large_bluechip': '主板大盘蓝筹', '主板_small_speculative': '主板小盘题材',
  '主板_growth_tech': '主板科技成长', '主板_value_defensive': '主板价值防御',
  '主板_cyclical_resource': '主板周期资源',
  '创业板_large_bluechip': '创业板蓝筹', '创业板_small_speculative': '创业板小盘',
  '创业板_growth_tech': '创业板科技', '创业板_value_defensive': '创业板防御',
  '创业板_cyclical_resource': '创业板周期',
  large_bluechip: '大盘蓝筹', small_speculative: '小盘题材',
  growth_tech: '科技成长', value_defensive: '价值防御',
  cyclical_resource: '周期资源',
};

interface Props {
  data: any[];
  batchScores: Record<string, any>;
  scanDate: string;
  curatedDate: string;
  expandedCards: Set<number>;
  setExpandedCards: React.Dispatch<React.SetStateAction<Set<number>>>;
  scoringBatch: boolean;
  loadError: boolean;
}

// ── v7.0.32: 金过滤判定 (基于 5 维技术指标 + 筹码) ──
interface GoldFilterResult {
  isGold: boolean;          // 是否金过滤 (强买入信号)
  isWarn: boolean;          // 是否风险警告
  label: string;           // '✓ 金过滤' | '⚠ 风险' | ''
  details: string[];        // 命中的条件列表
}

function checkGoldFilter(r: any): GoldFilterResult {
  const details: string[] = [];
  let isGold = true;
  let isWarn = false;

  // MACD 多头 (DIF > DEA 或 bar > 0)
  const macdDif = r.macd_dif;
  const macdDea = r.macd_dea;
  if (macdDif != null && macdDea != null) {
    if (macdDif > macdDea) details.push('MACD多头');
    else { isGold = false; details.push('MACD空头'); }
  }
  // KDJ 正常 (20 < J < 80)
  const kdjJ = r.kdj_j;
  if (kdjJ != null) {
    if (kdjJ >= 20 && kdjJ <= 80) details.push('KDJ正常');
    else if (kdjJ > 80) { isGold = false; isWarn = true; details.push('KDJ超买'); }
    else details.push('KDJ超卖');
  }
  // RSI 正常 (30 < RSI24 < 70)
  const rsi24 = r.rsi_24;
  if (rsi24 != null) {
    if (rsi24 >= 30 && rsi24 <= 70) details.push('RSI正常');
    else if (rsi24 > 70) { isGold = false; isWarn = true; details.push('RSI超买'); }
    else details.push('RSI超卖');
  }
  // BOLL 中位区 (0.2 < boll_pos < 0.8)
  const bollPos = r.boll_pos;
  if (bollPos != null) {
    if (bollPos >= 0.2 && bollPos <= 0.8) details.push('BOLL中位');
    else if (bollPos > 0.9) { isWarn = true; details.push('BOLL上轨外'); }
    else if (bollPos < 0.1) details.push('BOLL下轨外');
    else isGold = false;
  }
  // CCI 正常 (-100 < CCI < 100)
  const cci = r.cci;
  if (cci != null) {
    if (cci >= -100 && cci <= 100) details.push('CCI正常');
    else if (cci > 100) { isWarn = true; details.push('CCI超买'); }
    else details.push('CCI超卖');
  }
  // 筹码: 主力成本贴近 (price_vs_cost < 20%)
  const pvc = r.price_vs_cost;
  if (pvc != null) {
    if (pvc < 20) details.push('成本贴近');
    else { isGold = false; isWarn = true; details.push('高估>20%'); }
  }

  // 金过滤条件: 全部命中 (isGold && !isWarn), 至少 5 个 details
  let label = '';
  if (details.length >= 4 && isGold && !isWarn) label = '✓ 金过滤';
  else if (isWarn) label = '⚠ 风险';

  return { isGold: isGold && !isWarn, isWarn, label, details };
}

// ── v7.0.32: 单维度格式化 (MACD/KDJ/RSI/BOLL/CCI/筹码) ──
function techCell(key: string, value: number | null | undefined, format: 'pct' | 'num' | 'pos' = 'num') {
  if (value == null) return <span style={{ fontSize: 10, color: '#4b5563' }}>—</span>;
  let color = '#9ca3af';
  let display = '';
  if (key === 'macd') {
    // DIF 0轴上下
    color = value > 0.1 ? '#10b981' : value < -0.1 ? '#ef4444' : '#9ca3af';
    display = value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2);
  } else if (key === 'kdj') {
    color = value > 80 ? '#ef4444' : value < 20 ? '#10b981' : '#9ca3af';
    display = value.toFixed(0);
  } else if (key === 'rsi') {
    color = value > 70 ? '#ef4444' : value < 30 ? '#10b981' : '#9ca3af';
    display = value.toFixed(0);
  } else if (key === 'boll') {
    color = value > 0.9 ? '#ef4444' : value < 0.1 ? '#10b981' : '#9ca3af';
    display = value.toFixed(2);
  } else if (key === 'cci') {
    color = value > 100 ? '#ef4444' : value < -100 ? '#10b981' : '#9ca3af';
    display = value > 0 ? `+${value.toFixed(0)}` : value.toFixed(0);
  } else if (key === 'chip') {
    display = `¥${value.toFixed(1)}`;
    color = '#c9d1d9';
  }
  return <span style={{ fontSize: 11, fontWeight: 600, color }}>{display}</span>;
}

export default function CuratedRankingView({
  data, batchScores, scanDate, curatedDate,
  expandedCards, setExpandedCards, scoringBatch, loadError,
}: Props) {
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    if (!containerRef.current || downloading) return;
    setDownloading(true);
    try {
      // ★ v7.0.33 修复 1: 不要强制展开第 0 张
      //   之前 setExpandedCards(new Set([0])) 会把所有展开状态重置成"只 0 展开"
      //   现在保持用户原状态, 不修改 expandedCards
      //   用户要求: "保持所有标签都是折叠的状态"
      //   解决: 用 CSS 临时强制所有 isExp 的展开区隐藏
      const target = containerRef.current;
      const prevMaxHeight = target.style.maxHeight;

      // ★ 修复截图位置: 强制滚到顶部, 避免视口位置影响截图
      const prevScrollY = window.scrollY;
      const targetRect = target.getBoundingClientRect();
      window.scrollTo(0, window.scrollY + targetRect.top - 20);
      // 等重绘 + 任何滚动收敛
      await new Promise(r => setTimeout(r, 200));

      // 临时注入 CSS: 所有展开区 (Expanded details) 强制 display:none
      const styleEl = document.createElement('style');
      styleEl.id = '__download_hide_expanded__';
      styleEl.textContent = `
        #__download_target__ [data-expanded="true"] { display: none !important; }
      `;
      document.head.appendChild(styleEl);
      target.id = '__download_target__';

      // 等待 DOM 重绘
      await new Promise(r => setTimeout(r, 100));

      // 计算完整高度: 容器 scrollHeight + 边距
      const fullHeight = target.scrollHeight + 48;  // 上下 padding 各 24px
      const fullWidth = Math.max(target.scrollWidth, 1400);

      const dataUrl = await toPng(target, {
        backgroundColor: '#0b0e14',
        pixelRatio: 2,  // 高清
        cacheBust: true,
        // 关键: 让 html-to-image 知道完整高度, 截全所有内容
        width: fullWidth,
        height: fullHeight,
        style: {
          // 强制展示完整高度, 避免被 min-height 100vh 截断
          transform: 'translateZ(0)',
          maxHeight: 'none',
          height: fullHeight + 'px',
          overflow: 'visible',
          // ★ 修复 2: 居中对齐 - 强制容器宽度固定, 避免被父级 flex 影响
          maxWidth: '1400px',
          marginLeft: 'auto',
          marginRight: 'auto',
        },
      });

      // 清理: 移除临时 style + 还原 + 滚回原位置
      document.head.removeChild(styleEl);
      target.removeAttribute('id');
      target.style.maxHeight = prevMaxHeight;
      window.scrollTo(0, prevScrollY);

      // 下载
      const link = document.createElement('a');
      const filename = `精选反哺排名_${curatedDate || scanDate || new Date().toISOString().slice(0, 10)}.png`;
      link.download = filename;
      link.href = dataUrl;
      link.click();
    } catch (e: any) {
      alert('下载失败: ' + (e?.message || '未知错误'));
    } finally {
      setDownloading(false);
    }
  };

  const sorted = [...data].sort((a, b) => {
    const ra = a.rec_index ?? 0;
    const rb = b.rec_index ?? 0;
    if (rb !== ra) return rb - ra;  // rec_index DESC
    return (b.llm_score || b.composite_score || 0) - (a.llm_score || a.composite_score || 0);
  });

  return (
    <div ref={containerRef} style={{ maxWidth: 1400, margin: '0 auto', padding: 24, background: '#0b0e14', minHeight: '100vh', color: '#c9d1d9', fontFamily: 'system-ui' }}>
      <div style={{ display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:4 }}>
        <div style={{display:'flex',alignItems:'center',gap:12}}>
          <h1 style={{fontSize:22,margin:0}}>精选反哺排名</h1>
          <button onClick={() => navigate('/result')}
            style={{padding:'3px 12px',borderRadius:4,border:'1px solid #3b82f6',background:'rgba(59,130,246,0.08)',color:'#3b82f6',cursor:'pointer',fontSize:11}}>
            ← 返回推荐榜
          </button>
          {curatedDate && (
            <span style={{fontSize:11,color:'#f59e0b',background:'rgba(245,158,11,0.08)',padding:'2px 10px',borderRadius:4}}>
              📅 {curatedDate}
            </span>
          )}
        </div>
        <div style={{display:'flex',gap:12,alignItems:'center'}}>
          {scoringBatch && <span style={{fontSize:12,color:'#f59e0b'}}>⏳ LLM横向评分中...</span>}
          <span style={{fontSize:12,color:'#6e7a8a'}}>{scanDate} | {data.length} 只 | LLM反哺后重排</span>
          {/* ★ v7.0.33: 下载按钮 — 导出当前所有票为 PNG, 文件名用日期 */}
          <button
            onClick={handleDownload}
            disabled={downloading}
            title="下载当前所有票为 PNG (文件名带日期, 如 精选反哺排名_2026-06-19.png)"
            style={{
              padding:'5px 12px', borderRadius:4, fontSize:11, fontWeight:600,
              border: '1px solid #10b981',
              background: downloading ? '#1a2030' : 'rgba(16,185,129,0.08)',
              color: downloading ? '#6b7280' : '#10b981',
              cursor: downloading ? 'wait' : 'pointer',
              display:'inline-flex', alignItems:'center', gap:4,
            }}
          >
            {downloading ? '⏳ 生成中...' : '📥 下载 PNG'}
          </button>
        </div>
      </div>
      <p style={{color:'#6e7a8a',marginBottom:20,fontSize:12}}>
        点击卡片展开/折叠 · 各维度独立评分 · 横向对比
      </p>

      {sorted.map((r: any, i: number) => {
        const isExp = expandedCards.has(i);
        const bs = batchScores[r.symbol];
        const rank = i + 1;
        const isGold = rank === 1;
        const gf = checkGoldFilter(r);  // v7.0.32: 金过滤/风险判定

        return (
          <div key={r.symbol} onClick={() => setExpandedCards(prev => { const n = new Set(prev); if (n.has(i)) n.delete(i); else n.add(i); return n; })}
            style={{
              marginBottom: 10, borderRadius: 12, cursor: 'pointer',
              border: isGold ? '1px solid rgba(245,158,11,0.3)' : gf.isWarn ? '1px solid rgba(239,68,68,0.25)' : '1px solid #1e2535',
              background: isExp ? '#161b27' : '#111620',
              transition: 'all .2s', overflow: 'hidden',
            }}>
            {/* Collapsed row */}
            <div style={{ display:'flex', alignItems:'center', padding: '12px 20px', gap: 12 }}>
              <div style={{ width: 28, height: 28, borderRadius: '50%', display:'flex', alignItems:'center', justifyContent:'center', fontWeight: 700, fontSize: 10,
                background: isGold ? 'linear-gradient(135deg, #f59e0b, #d97706)' : '#1e2535', color: isGold ? '#0b0e14' : '#6e7a8a' }}>
                {rank}
              </div>
              <code style={{ color: '#06b6d4', fontSize: 12, fontWeight: 600 }}>{r.symbol}</code>
              <span style={{ fontSize: 12, fontWeight: 600, flex: 1 }}>{r.name}
                {r.ambush_score > 0 && <span style={{marginLeft:6,padding:'1px 5px',borderRadius:3,fontSize:9,background:'rgba(239,68,68,0.12)',color:'#ef4444'}}>潜伏</span>}
                {r.resonance_type === 'weekly_resonance' && (
                  <span style={{marginLeft:6,padding:'2px 7px',borderRadius:4,fontSize:9,fontWeight:700,background:'linear-gradient(135deg,rgba(245,158,11,0.25),rgba(251,191,36,0.12))',color:'#fbbf24',border:'1px solid rgba(245,158,11,0.3)'}} title="日线+周线双周期共振信号">⭐ 周线共振</span>
                )}
                {r.resonance_type === 'weekly_driven' && (
                  <span style={{marginLeft:6,padding:'2px 7px',borderRadius:4,fontSize:9,fontWeight:600,background:'rgba(59,130,246,0.12)',color:'#60a5fa',border:'1px solid rgba(59,130,246,0.2)'}} title="仅周线信号驱动, 日线待确认">📅 周线驱动</span>
                )}
                {r.risk_label === 'dead' && <span style={{marginLeft:6,fontSize:14}} title="信号质量<0.3: 极高欺骗风险">💀</span>}
                {r.risk_label === 'danger' && <span style={{marginLeft:6,fontSize:14}} title="信号质量0.3~0.5: 高欺骗风险">🔴</span>}
                {r.risk_label === 'warn' && <span style={{marginLeft:6,fontSize:14}} title="信号质量0.5~0.7: 注意风险">⚠</span>}
              </span>
              <div style={{ textAlign: 'center', minWidth: 50 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>系统</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: r.composite_score >= 55 ? '#ef4444' : r.composite_score >= 45 ? '#f59e0b' : '#9ca3af' }}>{r.composite_score}</div>
                {(r.composite_score >= 50 && (r.signal_quality || 1) < 0.5) && (
                  <div style={{ fontSize: 8, color: '#f59e0b', marginTop: 2 }} title="高评分但低质量 — 系统内部矛盾，可能是假信号">⚠矛盾</div>
                )}
              </div>
              <div style={{ textAlign: 'center', minWidth: 50 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>推荐指数</div>
                {(()=>{const ri=r.rec_index;if(ri==null)return<div style={{fontSize:14,fontWeight:700,color:'#4b5563'}}>—</div>;
                  const color=ri>=80?'#ef4444':ri>=60?'#f97316':ri>=40?'#f59e0b':'#10b981';
                  return<div title={r.rec_index_detail||''} style={{fontSize:14,fontWeight:700,color}}>{ri}</div>;})()}
              </div>
              <div style={{ textAlign: 'center', minWidth: 50 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>LLM</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: r.llm_score ? (r.llm_score >= 80 ? '#ef4444' : r.llm_score >= 60 ? '#f59e0b' : '#9ca3af') : '#4b5563' }}>{r.llm_score || '—'}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 44 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>短期</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: bs ? (bs.short >= 7 ? '#ef4444' : bs.short >= 5 ? '#f59e0b' : '#10b981') : '#4b5563' }}>{bs ? bs.short : '—'}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 44 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>中期</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: bs ? (bs.mid >= 7 ? '#ef4444' : bs.mid >= 5 ? '#f59e0b' : '#10b981') : '#4b5563' }}>{bs ? bs.mid : '—'}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 50 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>{r.llm_score ? '✓' : '○'}</div>
                <div style={{ fontSize: 10, color: r.llm_score ? '#10b981' : '#6e7a8a' }}>{r.llm_score ? '已反哺' : '待'}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 60 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>位置</div>
                <div style={{ fontSize: 10, fontWeight: 600, color:
                  r.relative_position?.includes('涨')||r.relative_position?.includes('强')?'#10b981':
                  r.relative_position?.includes('跌')||r.relative_position?.includes('出货')?'#ef4444':'#6e7a8a' }}>
                  {r.relative_position||'—'}
                </div>
                {r.predicted_return!=null&&<div style={{fontSize:9,color:r.predicted_return>0?'#ef4444':'#10b981'}}>预{r.predicted_return>0?'+':''}{r.predicted_return}%</div>}
              </div>
              {/* ★ v7.0.32: 5 维技术指标列 */}
              <div style={{ textAlign: 'center', minWidth: 50 }} title={`MACD DIF: ${r.macd_dif?.toFixed(2) ?? '—'} | DEA: ${r.macd_dea?.toFixed(2) ?? '—'}`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>MACD</div>
                <div>{techCell('macd', r.macd_dif)}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 42 }} title={`KDJ J值: ${r.kdj_j?.toFixed(0) ?? '—'}`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>KDJ</div>
                <div>{techCell('kdj', r.kdj_j)}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 42 }} title={`RSI24: ${r.rsi_24?.toFixed(0) ?? '—'}`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>RSI</div>
                <div>{techCell('rsi', r.rsi_24)}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 42 }} title={`BOLL pos: ${r.boll_pos?.toFixed(2) ?? '—'}`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>BOLL</div>
                <div>{techCell('boll', r.boll_pos)}</div>
              </div>
              <div style={{ textAlign: 'center', minWidth: 42 }} title={`CCI: ${r.cci?.toFixed(0) ?? '—'}`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>CCI</div>
                <div>{techCell('cci', r.cci)}</div>
              </div>
              {/* ★ v7.0.32: 筹码成本中位 */}
              <div style={{ textAlign: 'center', minWidth: 50 }} title={`筹码成本中位: ¥${r.cost_50pct?.toFixed(2) ?? '—'} | 现价相对: ${r.price_vs_cost?.toFixed(1) ?? '—'}%`}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>成本</div>
                <div>{techCell('chip', r.cost_50pct)}</div>
              </div>
              {/* ★ v7.0.32: 金过滤/风险标签 */}
              {gf.label && (
                <div style={{ textAlign: 'center', minWidth: 56 }} title={gf.details.join(' | ')}>
                  <div style={{ fontSize: 9, color: '#6e7a8a' }}>信号</div>
                  <div style={{
                    fontSize: 10, fontWeight: 700,
                    padding: '2px 6px', borderRadius: 4,
                    background: gf.label.includes('金') ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)',
                    color: gf.label.includes('金') ? '#10b981' : '#ef4444',
                    border: gf.label.includes('金') ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(239,68,68,0.3)',
                  }}>{gf.label}</div>
                </div>
              )}
              <div style={{ textAlign: 'center', minWidth: 70 }}>
                <div style={{ fontSize: 9, color: '#6e7a8a' }}>策略</div>
                <div style={{ fontSize: 10, fontWeight: 600, color:
                  r.strategy_label?.includes('超短')?'#f59e0b':
                  r.strategy_label?.includes('短中')?'#10b981':
                  r.strategy_label?.includes('中线')?'#3b82f6':'#6e7a8a' }}>
                  {r.strategy_label||'—'}
                </div>
                {r.peer_rank&&<div style={{fontSize:8,color:'#6e7a8a',marginTop:1}}>{r.peer_rank.replace('#','')}</div>}
              </div>
              <span style={{ fontSize: 10, color: '#6e7a8a' }}>{isExp ? '▲' : '▼'}</span>
            </div>

            {/* Expanded details */}
            {isExp && (
              <div data-expanded="true" className="expanded-details" style={{ padding: '0 20px 16px', borderTop: '1px solid #1e2535' }}>
                <div style={{ display: 'flex', gap: 24, padding: '14px 0', flexWrap: 'wrap', borderBottom: '1px solid #1e2535' }}>
                  {(() => {
                    const ds = r.dimension_scores || {};
                    return [
                    { label: '级别', value: r.level || '—', color: r.level === 'L3' ? '#f59e0b' : r.level === 'L2' ? '#60a5fa' : '#9ca3af' },
                    { label: '收盘价', value: r.close_price ? '¥' + r.close_price.toFixed(2) : '—' },
                    { label: 'TG动量', value: r.tg_momentum?.toFixed(2) || '—' },
                    ...(r.resonance_type && r.resonance_type !== 'daily_only' ? [
                      { label: '周线动量', value: r.weekly_tg_momentum != null ? r.weekly_tg_momentum.toFixed(2) : '—', color: r.resonance_type === 'weekly_resonance' ? '#fbbf24' : '#60a5fa' },
                    ] : []),
                    { label: '技术面', value: r.tech_score?.toFixed(1) || '—', color: (r.tech_score || 0) >= 7 ? '#ef4444' : (r.tech_score || 0) >= 3 ? '#f87171' : (r.tech_score || 0) <= -7 ? '#10b981' : (r.tech_score || 0) <= -3 ? '#34d399' : '#6e7a8a' },
                    { label: '资金面', value: r.fund_score?.toFixed(1) || '—', color: (r.fund_score || 0) >= 7 ? '#ef4444' : (r.fund_score || 0) >= 3 ? '#f87171' : (r.fund_score || 0) <= -7 ? '#10b981' : (r.fund_score || 0) <= -3 ? '#34d399' : '#6e7a8a' },
                    { label: '基本面', value: r.fundamental_adjustment != null ? (r.fundamental_adjustment > 0 ? '+' : '') + r.fundamental_adjustment : '—', color: (r.fundamental_adjustment || 0) < 0 ? '#10b981' : (r.fundamental_adjustment || 0) > 0 ? '#ef4444' : '#6e7a8a' },
                    { label: 'BBI', value: ds.bbi_score != null ? ds.bbi_score.toFixed(1) : '—', color: (ds.bbi_score || 0) >= 7 ? '#ef4444' : (ds.bbi_score || 0) >= 3 ? '#f87171' : (ds.bbi_score || 0) <= -7 ? '#10b981' : (ds.bbi_score || 0) <= -3 ? '#34d399' : '#6e7a8a' },
                    { label: '趋势偏离', value: ds.trend_deviation_score != null ? ds.trend_deviation_score.toFixed(1) : '—', color: (ds.trend_deviation_score || 0) >= 7 ? '#ef4444' : (ds.trend_deviation_score || 0) >= 3 ? '#f87171' : (ds.trend_deviation_score || 0) <= -7 ? '#10b981' : (ds.trend_deviation_score || 0) <= -3 ? '#34d399' : '#6e7a8a' },
                    { label: '箱体', value: ds.box_score != null ? ds.box_score.toFixed(1) : '—', color: (ds.box_score || 0) >= 7 ? '#ef4444' : (ds.box_score || 0) >= 3 ? '#f87171' : (ds.box_score || 0) <= -7 ? '#10b981' : (ds.box_score || 0) <= -3 ? '#34d399' : '#6e7a8a' },
                    { label: '原型', value: r.archetype && r.archetype !== 'unknown' ? (ARCH_CN[r.archetype] || r.archetype.replace(/_/g, ' ')) : '—' },
                  ]; })().map((m: any, j: number) => (
                    <div key={j} style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: 9, color: '#6e7a8a', marginBottom: 2 }}>{m.label}</div>
                      <div style={{ fontSize: 12, fontWeight: 600, color: (m as any).color || '#c9d1d9' }}>{m.value}</div>
                    </div>
                  ))}
                </div>

                {/* ★ v7.0.32: 5 维技术指标 + 筹码分布 行 */}
                <div style={{ display: 'flex', gap: 14, padding: '10px 0', flexWrap: 'wrap', borderBottom: '1px solid #1e2535' }}>
                  <div style={{ fontSize: 10, color: '#06b6d4', fontWeight: 700, alignSelf: 'center', minWidth: 56 }}>📊 v7.0.32</div>
                  {/* MACD */}
                  <div style={{ textAlign: 'center', minWidth: 70 }} title={`MACD DIF=${r.macd_dif?.toFixed(3) ?? '—'} | DEA=${r.macd_dea?.toFixed(3) ?? '—'} | BAR=${r.macd_bar?.toFixed(3) ?? '—'}`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>MACD</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('macd', r.macd_dif)}</div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>DEA {r.macd_dea?.toFixed(2) ?? '—'}</div>
                  </div>
                  {/* KDJ */}
                  <div style={{ textAlign: 'center', minWidth: 80 }} title={`KDJ K=${r.kdj_k?.toFixed(1) ?? '—'} | D=${r.kdj_d?.toFixed(1) ?? '—'} | J=${r.kdj_j?.toFixed(1) ?? '—'}`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>KDJ</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('kdj', r.kdj_j)}</div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>K {r.kdj_k?.toFixed(0) ?? '—'} / D {r.kdj_d?.toFixed(0) ?? '—'}</div>
                  </div>
                  {/* RSI */}
                  <div style={{ textAlign: 'center', minWidth: 60 }} title={`RSI 6=${r.rsi_6?.toFixed(0) ?? '—'} | 12=${r.rsi_12?.toFixed(0) ?? '—'} | 24=${r.rsi_24?.toFixed(0) ?? '—'}`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>RSI</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('rsi', r.rsi_24)}</div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>24h 值</div>
                  </div>
                  {/* BOLL */}
                  <div style={{ textAlign: 'center', minWidth: 90 }} title={`BOLL 上=${r.boll_upper?.toFixed(2) ?? '—'} | 中=${r.boll_mid?.toFixed(2) ?? '—'} | 下=${r.boll_lower?.toFixed(2) ?? '—'} | pos=${r.boll_pos?.toFixed(2) ?? '—'}`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>BOLL</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('boll', r.boll_pos)}</div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>中轨 ¥{r.boll_mid?.toFixed(2) ?? '—'}</div>
                  </div>
                  {/* CCI */}
                  <div style={{ textAlign: 'center', minWidth: 60 }} title={`CCI: ${r.cci?.toFixed(1) ?? '—'}`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>CCI</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('cci', r.cci)}</div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>±100 阈值</div>
                  </div>
                  {/* 筹码成本 */}
                  <div style={{ textAlign: 'center', minWidth: 80 }} title={`筹码中位: ¥${r.cost_50pct?.toFixed(2) ?? '—'} | 主力成本: ¥${r.weight_avg?.toFixed(2) ?? '—'} | 现价相对: ${r.price_vs_cost?.toFixed(1) ?? '—'}%`}>
                    <div style={{ fontSize: 9, color: '#06b6d4', fontWeight: 700 }}>💰 筹码</div>
                    <div style={{ fontSize: 11, fontWeight: 600 }}>{techCell('chip', r.cost_50pct)}</div>
                    <div style={{ fontSize: 8, color: r.price_vs_cost > 20 ? '#ef4444' : r.price_vs_cost < -10 ? '#10b981' : '#6e7a8a' }}>
                      {r.price_vs_cost != null ? (r.price_vs_cost > 0 ? `+${r.price_vs_cost.toFixed(1)}%` : `${r.price_vs_cost.toFixed(1)}%`) : '—'}
                    </div>
                  </div>
                  {/* 获利盘 */}
                  <div style={{ textAlign: 'center', minWidth: 60 }} title={`获利盘: ${r.winner_rate?.toFixed(1) ?? '—'}%`}>
                    <div style={{ fontSize: 9, color: '#6e7a8a' }}>获利盘</div>
                    <div style={{ fontSize: 11, fontWeight: 600, color: r.winner_rate > 70 ? '#ef4444' : r.winner_rate < 30 ? '#10b981' : '#9ca3af' }}>
                      {r.winner_rate != null ? `${r.winner_rate.toFixed(0)}%` : '—'}
                    </div>
                    <div style={{ fontSize: 8, color: '#4b5563' }}>
                      {r.winner_rate > 70 ? '高位风险' : r.winner_rate < 30 ? '深套' : '正常'}
                    </div>
                  </div>
                  {/* 金过滤判定 */}
                  {gf.label && (
                    <div style={{
                      alignSelf: 'center', marginLeft: 'auto', padding: '6px 12px', borderRadius: 6,
                      background: gf.label.includes('金') ? 'rgba(16,185,129,0.08)' : 'rgba(239,68,68,0.08)',
                      border: gf.label.includes('金') ? '1px solid rgba(16,185,129,0.3)' : '1px solid rgba(239,68,68,0.3)',
                    }} title={gf.details.join(' | ')}>
                      <div style={{ fontSize: 9, color: '#6e7a8a' }}>v7.0.32 过滤</div>
                      <div style={{
                        fontSize: 13, fontWeight: 700,
                        color: gf.label.includes('金') ? '#10b981' : '#ef4444',
                      }}>{gf.label}</div>
                    </div>
                  )}
                </div>

                {(r.adjustment_reasons?.length > 0) && (
                  <div style={{ marginTop: 10, padding: '8px 12px', borderRadius: 6, background: 'rgba(139,92,246,0.03)', border: '1px solid rgba(139,92,246,0.08)' }}>
                    <div style={{ fontSize: 10, color: '#6e7a8a', marginBottom: 4 }}>🔍 评分调整明细</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px' }}>
                      {(r.adjustment_reasons || []).slice(0, 8).map((reason: string, j: number) => {
                        const isNeg = reason.includes('⚠') || reason.includes('-') || reason.includes('退潮') || reason.includes('分化');
                        const isPos = reason.includes('发酵') || reason.includes('金叉') || reason.includes('多头') || reason.includes('+');
                        return (
                          <span key={j} style={{ fontSize: 10, color: isNeg ? '#10b981' : isPos ? '#ef4444' : '#8b949e' }}>
                            {reason}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Old trader verdict */}
                {(() => {
                  const dr = r.drill_summary || '';
                  const se = r.drill_signal_effectiveness || {};
                  const cs = r.drill_chip_simulation || {};
                  const pm = r.drill_pattern_matching || {};
                  const mb = r.drill_micro_behavior || {};
                  const res = r.drill_resonance || {};
                  const cp = r.drill_critical_position || {};
                  const idxR = res?.index_resonance || {};
                  const secR = res?.sector_resonance || {};
                  const newsR = res?.news_resonance || {};
                  const chipR = res?.chip_resonance || {};

                  const seWr = se?.win_rate_5d ?? null;
                  const seCnt = se?.history_count ?? 0;
                  const csTrend = cs?.trend || '';
                  const curAr = cs?.current_ar ?? 0;
                  const pred5 = pm?.predicted_avg_return_5d ?? null;
                  const predWr = pm?.predicted_win_rate_5d ?? null;
                  const independence = idxR?.independence_rate ?? 0;
                  const pseudo = idxR?.pseudo_strength_rate ?? 0;
                  const leadRate = secR?.lead_rate ?? 0;
                  const techDriven = newsR?.tech_driven_rate ?? 0;
                  const highWr = chipR?.high_absorption_win_rate ?? 0;
                  const lowWr = chipR?.low_absorption_win_rate ?? 0;
                  const riseTriggers = mb?.fast_rise?.current_status?.active_triggers || [];
                  const fallTriggers = mb?.fast_fall?.current_status?.active_triggers || [];
                  const riseActive = !!mb?.fast_rise?.current_status?.is_any_trigger_active;
                  const fallActive = !!mb?.fast_fall?.current_status?.is_any_trigger_active;
                  const ds2 = r.dimension_scores || {};
                  const jVal = ds2?.tg_momentum_score ?? null;
                  const hasData = !!dr || seCnt > 0 || !!pm?.status || cs?.status === 'success' || Object.keys(res).length > 0;
                  if (!hasData) return null;

                  let rating: 'bullish' | 'neutral' | 'bearish' = 'neutral';
                  let posCnt = 0; let negCnt = 0;
                  const positives: {label:string;conf:number;detail:string}[] = [];
                  const negatives: {label:string;conf:number;detail:string}[] = [];

                  if (seWr != null && seCnt >= 3) {
                    if (seWr >= 0.55) { posCnt++; positives.push({label:'历史信号胜率',conf:seWr,detail:`${seCnt}次信号，T+5胜率${(seWr*100).toFixed(0)}%`}); }
                    else if (seWr < 0.40) { negCnt++; negatives.push({label:'历史信号胜率',conf:1-seWr,detail:`${seCnt}次信号，T+5胜率仅${(seWr*100).toFixed(0)}%`}); }
                  }
                  if (csTrend === 'accelerating') { posCnt++; positives.push({label:'筹码加速吸收',conf:0.8,detail:`当前吸收率${(curAr*100).toFixed(0)}%`}); }
                  if (csTrend === 'declining' || csTrend === 'stagnating') { negCnt++; negatives.push({label:'筹码吸收停滞',conf:0.6,detail:`当前吸收率${(curAr*100).toFixed(0)}%`}); }
                  if (independence >= 0.30) { posCnt++; positives.push({label:'独立行情',conf:independence,detail:`${(independence*100).toFixed(0)}%信号独立于大盘上涨`}); }
                  if (pseudo >= 0.25) { negCnt++; negatives.push({label:'伪强势风险',conf:pseudo,detail:`${(pseudo*100).toFixed(0)}%信号大盘跌时股涨，警惕假突破`}); }
                  if (leadRate >= 0.30) { posCnt++; positives.push({label:'板块龙头',conf:leadRate,detail:`${(leadRate*100).toFixed(0)}%领先板块`}); }
                  if (techDriven >= 0.50) { posCnt++; positives.push({label:'技术驱动',conf:techDriven,detail:`${(techDriven*100).toFixed(0)}%无消息面干扰`}); }
                  if (highWr >= 0.65) { posCnt++; positives.push({label:'高吸收胜率',conf:highWr,detail:`筹码高吸收时胜率${(highWr*100).toFixed(0)}%`}); }
                  if (lowWr <= 0.30 && lowWr > 0) { negCnt++; negatives.push({label:'低吸收胜率低',conf:1-lowWr,detail:`筹码低吸收时胜率仅${(lowWr*100).toFixed(0)}%`}); }
                  if (pred5 != null && pred5 > 2) { posCnt++; positives.push({label:'形态看涨',conf:predWr||0.55,detail:`形态预测T+5: ${pred5>0?'+':''}${pred5.toFixed(1)}%`}); }
                  if (pred5 != null && pred5 < -3) { negCnt++; negatives.push({label:'形态看跌',conf:predWr?1-predWr:0.5,detail:`形态预测T+5: ${pred5.toFixed(1)}%`}); }
                  if (riseActive) { posCnt++; positives.push({label:'拉升条件满足',conf:0.7,detail:riseTriggers.join('、')}); }
                  if (fallActive) { negCnt++; negatives.push({label:'砸盘条件满足',conf:0.7,detail:fallTriggers.join('、')}); }
                  (cp?.positions || []).forEach((p:any) => {
                    if (p?.history_breakout_rate != null && !p?.uncertain) {
                      if (p.history_breakout_rate >= 0.5) { posCnt++; positives.push({label:`${p.type}突破`,conf:p.history_breakout_rate,detail:`历史触碰${p.history_count||0}次，突破率${(p.history_breakout_rate*100).toFixed(0)}%`}); }
                      else if (p.history_breakout_rate < 0.35) { negCnt++; negatives.push({label:`${p.type}遇阻`,conf:1-p.history_breakout_rate,detail:`历史触碰${p.history_count||0}次，突破率仅${(p.history_breakout_rate*100).toFixed(0)}%`}); }
                    }
                  });
                  if (posCnt > negCnt && seWr != null && seWr >= 0.55) rating = 'bullish';
                  else if (negCnt > posCnt && pseudo >= 0.30) rating = 'bearish';
                  else if (posCnt > negCnt) rating = 'bullish';
                  else if (negCnt > posCnt) rating = 'bearish';
                  positives.sort((a,b) => b.conf - a.conf);
                  negatives.sort((a,b) => b.conf - a.conf);

                  let suggestion = '';
                  const seWrSafe = seWr ?? 0;
                  if (highWr >= 0.70 && curAr >= 0.60 && seWrSafe >= 0.60) suggestion = '筹码+共振双优，可逢低关注';
                  else if ((jVal != null && jVal > 8) || pseudo >= 0.30 || (pred5 != null && pred5 < -3)) suggestion = '短期风险偏高，建议等待回调';
                  else if (seWrSafe < 0.40 && seCnt > 5) suggestion = '历史信号可靠性低，不建议参与';
                  else if (posCnt + negCnt > 0) suggestion = '中性，需结合个人判断';
                  else suggestion = '暂无足够历史数据';

                  const ratingColor = rating === 'bullish' ? '#10b981' : rating === 'bearish' ? '#ef4444' : '#f59e0b';
                  const ratingLabel = rating === 'bullish' ? '偏多' : rating === 'bearish' ? '偏空' : '中性';
                  const ratingBg = rating === 'bullish' ? 'rgba(16,185,129,0.08)' : rating === 'bearish' ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.08)';
                  const ratingBorder = rating === 'bullish' ? 'rgba(16,185,129,0.2)' : rating === 'bearish' ? 'rgba(239,68,68,0.2)' : 'rgba(245,158,11,0.2)';

                  return (
                  <div style={{ marginTop: 10, padding: '14px 16px', borderRadius: 10,
                    background: 'rgba(16,185,129,0.02)', border: '1px solid rgba(16,185,129,0.08)' }}>
                    <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 12, display:'flex',alignItems:'center',gap:8 }}>
                      <span style={{fontWeight:600}}>🧠 老股民研判</span>
                      <span style={{ padding: '2px 10px', borderRadius: 10, fontSize: 10, fontWeight: 700,
                        background: ratingBg, border: `1px solid ${ratingBorder}`, color: ratingColor,
                      }}>{ratingLabel}</span>
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
                      <div style={{ padding: 10, borderRadius: 8, background: 'rgba(16,185,129,0.03)', border: '1px solid rgba(16,185,129,0.08)' }}>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#10b981', marginBottom: 6 }}>✅ 看多理由</div>
                        {positives.length === 0 ? (
                          <div style={{ fontSize: 10, color: '#4b5563' }}>—</div>
                        ) : positives.slice(0, 4).map((p, i) => (
                          <div key={i} style={{ marginBottom: 4 }}>
                            <div style={{ display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:1 }}>
                              <span style={{ fontSize: 10, color: '#c9d1d9' }}>{p.label}</span>
                              <span style={{ fontSize: 9, color: '#10b981' }}>{(p.conf*100).toFixed(0)}%</span>
                            </div>
                            <div style={{ height: 3, background: '#1e2535', borderRadius: 2 }}>
                              <div style={{ height: '100%', width: `${Math.min(100,p.conf*100)}%`, background: 'linear-gradient(90deg,#10b981,#34d399)', borderRadius: 2 }} />
                            </div>
                            <div style={{ fontSize: 9, color: '#4b5563', marginTop: 1 }}>{p.detail}</div>
                          </div>
                        ))}
                      </div>
                      <div style={{ padding: 10, borderRadius: 8, background: 'rgba(239,68,68,0.03)', border: '1px solid rgba(239,68,68,0.08)' }}>
                        <div style={{ fontSize: 10, fontWeight: 600, color: '#ef4444', marginBottom: 6 }}>⚠️ 看空理由</div>
                        {negatives.length === 0 ? (
                          <div style={{ fontSize: 10, color: '#4b5563' }}>—</div>
                        ) : negatives.slice(0, 4).map((p, i) => (
                          <div key={i} style={{ marginBottom: 4 }}>
                            <div style={{ display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:1 }}>
                              <span style={{ fontSize: 10, color: '#c9d1d9' }}>{p.label}</span>
                              <span style={{ fontSize: 9, color: '#ef4444' }}>{(p.conf*100).toFixed(0)}%</span>
                            </div>
                            <div style={{ height: 3, background: '#1e2535', borderRadius: 2 }}>
                              <div style={{ height: '100%', width: `${Math.min(100,p.conf*100)}%`, background: 'linear-gradient(90deg,#ef4444,#f87171)', borderRadius: 2 }} />
                            </div>
                            <div style={{ fontSize: 9, color: '#4b5563', marginTop: 1 }}>{p.detail}</div>
                          </div>
                        ))}
                      </div>
                    </div>
                    {(seWr != null || curAr > 0 || pred5 != null || independence > 0 || riseActive || fallActive) && (
                      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 10, padding: '8px 12px', borderRadius: 6, background: 'rgba(30,37,53,0.3)' }}>
                        {seWr != null && seCnt >= 3 && (
                          <div style={{ textAlign: 'center', minWidth: 60 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>信号胜率</div>
                            <div style={{ fontSize: 16, fontWeight: 700, color: seWr >= 0.55 ? '#ef4444' : seWr >= 0.40 ? '#f59e0b' : '#10b981' }}>{(seWr*100).toFixed(0)}%</div>
                            <div style={{ fontSize: 7, color: '#4b5563' }}>{seCnt}次</div>
                          </div>
                        )}
                        {curAr > 0 && (
                          <div style={{ textAlign: 'center', minWidth: 60 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>筹码吸收</div>
                            <div style={{ fontSize: 16, fontWeight: 700, color: curAr >= 0.6 ? '#ef4444' : curAr >= 0.4 ? '#f59e0b' : '#10b981' }}>{(curAr*100).toFixed(0)}%</div>
                            <div style={{ fontSize: 7, color: '#4b5563' }}>{csTrend === 'accelerating' ? '↑加速' : csTrend === 'slowly_improving' ? '↗改善' : csTrend || '—'}</div>
                          </div>
                        )}
                        {pred5 != null && (
                          <div style={{ textAlign: 'center', minWidth: 60 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>形态预测</div>
                            <div style={{ fontSize: 16, fontWeight: 700, color: pred5 >= 0 ? '#ef4444' : '#10b981' }}>{pred5 >= 0 ? '↑' : '↓'}{Math.abs(pred5).toFixed(1)}%</div>
                            <div style={{ fontSize: 7, color: '#4b5563' }}>T+5</div>
                          </div>
                        )}
                        {independence > 0 && (
                          <div style={{ textAlign: 'center', minWidth: 60 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>独立率</div>
                            <div style={{ fontSize: 16, fontWeight: 700, color: independence >= 0.3 ? '#10b981' : '#f59e0b' }}>{(independence*100).toFixed(0)}%</div>
                            <div style={{ fontSize: 7, color: '#4b5563' }}>vs大盘</div>
                          </div>
                        )}
                        {riseActive && (
                          <div style={{ textAlign: 'center', minWidth: 80 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>拉升触发</div>
                            <div style={{ fontSize: 11, fontWeight: 700, color: '#10b981', lineHeight:1.3 }}>{riseTriggers.slice(0,2).join('\n') || '✓'}</div>
                            <div style={{ fontSize: 7, color: '#10b981' }}>条件满足</div>
                          </div>
                        )}
                        {fallActive && (
                          <div style={{ textAlign: 'center', minWidth: 80 }}>
                            <div style={{ fontSize: 8, color: '#6e7a8a' }}>砸盘触发</div>
                            <div style={{ fontSize: 11, fontWeight: 700, color: '#ef4444', lineHeight:1.3 }}>{fallTriggers.slice(0,2).join('\n') || '✓'}</div>
                            <div style={{ fontSize: 7, color: '#ef4444' }}>条件满足</div>
                          </div>
                        )}
                      </div>
                    )}
                    <div style={{ padding: '8px 12px', borderRadius: 6,
                      background: suggestion.includes('双优') || suggestion.includes('逢低') ? 'rgba(16,185,129,0.06)' :
                                   suggestion.includes('风险') || suggestion.includes('不建议') ? 'rgba(239,68,68,0.06)' :
                                   'rgba(245,158,11,0.04)',
                      border: `1px solid ${suggestion.includes('双优') || suggestion.includes('逢低') ? 'rgba(16,185,129,0.15)' :
                                   suggestion.includes('风险') || suggestion.includes('不建议') ? 'rgba(239,68,68,0.15)' :
                                   'rgba(245,158,11,0.08)'}` }}>
                      <span style={{ fontSize: 9, color: '#6e7a8a' }}>💡 建议: </span>
                      <span style={{ fontSize: 10, fontWeight: 600, color:
                        suggestion.includes('双优') || suggestion.includes('逢低') ? '#10b981' :
                        suggestion.includes('风险') || suggestion.includes('不建议') ? '#ef4444' : '#f59e0b' }}>
                        {suggestion}
                      </span>
                    </div>
                  </div>
                  );
                })()}

                {(r.hidden_risks?.length > 0 || r.catalysts?.length > 0) && (
                  <div style={{ marginTop: 10 }}>
                    {(r.hidden_risks || []).map((s: any, j: number) => (
                      <div key={'r'+j} style={{ padding: '3px 0', fontSize: 13, color: '#ef4444', lineHeight: 1.6 }}>
                        ⚠ {typeof s === 'string' ? s : (s.description || s.label || '')}
                      </div>
                    ))}
                    {(r.catalysts || []).map((s: any, j: number) => (
                      <div key={'c'+j} style={{ padding: '3px 0', fontSize: 13, color: '#10b981', lineHeight: 1.6 }}>
                        ✓ {typeof s === 'string' ? s : (s.description || s.label || '')}
                      </div>
                    ))}
                  </div>
                )}

                {bs && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
                    <div style={{ padding: 12, background: 'rgba(245,158,11,0.04)', borderRadius: 8, border: '1px solid rgba(245,158,11,0.1)' }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#f59e0b', marginBottom: 8 }}>📈 短期操作 (1-4周)</div>
                      <div style={{ display: 'flex', gap: 20, marginBottom: bs.short_note ? 8 : 0 }}>
                        <div><div style={{ fontSize: 9, color: '#6e7a8a' }}>评分</div>
                          <div style={{ fontSize: 20, fontWeight: 700, color: bs.short >= 7 ? '#ef4444' : bs.short >= 5 ? '#f59e0b' : '#10b981' }}>{bs.short}<span style={{fontSize:12}}>/10</span></div></div>
                        <div><div style={{ fontSize: 9, color: '#6e7a8a' }}>支撑</div>
                          <div style={{ fontSize: 14, fontWeight: 600, color: '#10b981' }}>¥{bs.support || '—'}</div></div>
                        <div><div style={{ fontSize: 9, color: '#6e7a8a' }}>压力</div>
                          <div style={{ fontSize: 14, fontWeight: 600, color: '#ef4444' }}>¥{bs.resistance || '—'}</div></div>
                      </div>
                      {bs.short_note && <div style={{ fontSize: 10, color: '#9ca3af', fontStyle: 'italic' }}>💬 {bs.short_note}</div>}
                    </div>
                    <div style={{ padding: 12, background: 'rgba(59,130,246,0.04)', borderRadius: 8, border: '1px solid rgba(59,130,246,0.1)' }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: '#3b82f6', marginBottom: 8 }}>📊 中期操作 (1-3月)</div>
                      <div style={{ display: 'flex', gap: 20, marginBottom: bs.mid_note ? 8 : 0 }}>
                        <div><div style={{ fontSize: 9, color: '#6e7a8a' }}>评分</div>
                          <div style={{ fontSize: 20, fontWeight: 700, color: bs.mid >= 7 ? '#ef4444' : bs.mid >= 5 ? '#f59e0b' : '#10b981' }}>{bs.mid}<span style={{fontSize:12}}>/10</span></div></div>
                      </div>
                      {bs.mid_note ? <div style={{ fontSize: 10, color: '#9ca3af', fontStyle: 'italic' }}>💬 {bs.mid_note}</div> : <div style={{ fontSize: 10, color: '#6e7a8a' }}>中线持有，关注基本面拐点与资金面背离修复</div>}
                    </div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: 8, marginTop: 12, justifyContent: 'flex-end' }}>
                  {(['buy', 'watch', 'pass'] as const).map(action => (
                    <button key={action} onClick={async (e) => {
                      e.stopPropagation();
                      try { await api.post('/user-decisions', { symbol: r.symbol, action, decision_reason: '精选排名决策' }); } catch {}
                    }}
                      style={{ padding: '5px 16px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
                        border: '1px solid #1e2535', background: 'transparent',
                        color: action === 'buy' ? '#ef4444' : action === 'watch' ? '#f59e0b' : '#6e7a8a' }}>
                      {action === 'buy' ? '买入' : action === 'watch' ? '观察' : '放弃'}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })}

      {sorted.length === 0 && (
        <div style={{ textAlign: 'center', padding: 80, color: '#4b5563' }}>
          <div style={{ fontSize: 40, marginBottom: 12 }}>📊</div>
          {loadError ? (
            <>
              <div style={{ color: '#ef4444', fontSize: 14, fontWeight: 600 }}>后端接口异常</div>
              <div style={{ fontSize: 12, marginTop: 8 }}>
                请检查后端是否已重启 (uvicorn 重启后生效) 并按 F12 查看 Console 错误详情
              </div>
            </>
          ) : (
            <div>暂无推荐数据</div>
          )}
        </div>
      )}
    </div>
  );
}
