import { useEffect, useState } from 'react';
import api from '../lib/api';
import { useSearchParams } from 'react-router-dom';

const R = { stock: '#60a5fa', sector: '#f87171', market: '#fbbf24',
           ma5: '#22d3ee', ma10: '#a78bfa', ma20: '#fb923c', ma60: '#f472b6', price: '#9ca3af' };
const L = { stock: '个股收益', sector: '板块收益', market: '大盘收益',
           ma5: 'MA5', ma10: 'MA10', ma20: 'MA20', ma60: 'MA60', price: '价格' };

export default function SanxianPage() {
  const [searchParams] = useSearchParams();
  const symbol = searchParams.get('symbol') || '002594.SZ';
  const days = parseInt(searchParams.get('days') || '60');
  const [data, setData] = useState<any>(null);
  const [idata, setIdata] = useState<any>(null);
  const [iLoading, setILoading] = useState(false);
  const [iError, setIError] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const lookback = parseInt(searchParams.get('lookback') || '5');

  useEffect(() => {
    setLoading(true);
    api.get('/sanxian', { params: { symbol, days } })
      .then(r => { setData(r.data); setError(''); })
      .catch(e => setError(e?.response?.data?.detail || '加载失败'))
      .finally(() => setLoading(false));
    // 同时加载分时数据
    setILoading(true); setIError('');
    api.get('/sanxian/intraday', { params: { symbol, lookback } })
      .then(r => { setIdata(r.data); setIError(''); })
      .catch(e => setIError(e?.response?.data?.detail || e?.message || '加载超时'))
      .finally(() => setILoading(false));
  }, [symbol, days, lookback]);

  if (loading) return <div style={{ padding: 40, color: '#6e7a8a', textAlign: 'center' }}>加载中...</div>;
  if (error) return <div style={{ padding: 40, color: '#ef4444', textAlign: 'center' }}>{error}</div>;
  if (!data?.status) return <div style={{ padding: 40, color: '#ef4444', textAlign: 'center' }}>数据异常</div>;

  const { stock, sector, market, dates, name, sector_name, tags, prices, ma5, ma10, ma20, ma60 } = data;

  // ── 价格和MA转为累计收益率（与stock同Y轴） ──
  const toRet = (arr: (number|null)[], base: number) =>
    arr.map(v => v !== null ? (v / base - 1) * 100 : null);

  const priceRet = prices ? toRet(prices, prices[0]) : null;
  const m5r = ma5 ? toRet(ma5, prices?.[0] ?? 1) : null;
  const m10r = ma10 ? toRet(ma10, prices?.[0] ?? 1) : null;
  const m20r = ma20 ? toRet(ma20, prices?.[0] ?? 1) : null;
  const m60r = ma60 ? toRet(ma60, prices?.[0] ?? 1) : null;

  // ── Y轴范围 covering all series ──
  const allVals = [...stock, ...market, ...(sector||[]),
    ...(priceRet?.filter(v => v !== null) as number[] ?? []),
    ...(m5r?.filter(v => v !== null) as number[] ?? []), ...(m20r?.filter(v => v !== null) as number[] ?? [])];
  const yMin = Math.min(0, ...allVals) - 2;
  const yMax = Math.max(0, ...allVals) + 2;
  const yRng = yMax - yMin || 1;

  const CH = 460; const CW = 960; const p = { t: 25, r: 40, b: 40, l: 60 };
  const w = CW - p.l - p.r; const h = CH - p.t - p.b;
  const xStep = w / Math.max(1, stock.length - 1);
  const X = (i: number) => p.l + i * xStep;
  const Y = (v: number) => p.t + h - ((v - yMin) / yRng) * h;

  const mkPath = (arr: number[]) => arr.map((v, i) => `${i === 0 ? 'M' : 'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const mkNullPath = (arr: (number|null)[]) => {
    const pts = arr.map((v, i) => v !== null ? { i, v } : null).filter(Boolean) as { i: number, v: number }[];
    if (pts.length < 2) return '';
    return pts.map((p, j) => `${j === 0 ? 'M' : 'L'}${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  };
  const yTicks = Array.from({ length: 6 }, (_, i) => yMin + (yRng / 5) * i);

  const lines = [
    { k: 'stock', arr: stock, w: 2.5, dash: '', op: 1 },
    { k: 'sector', arr: sector, w: 1.5, dash: '6,3', op: 0.85 },
    { k: 'market', arr: market, w: 1.5, dash: '3,3', op: 0.7 },
    { k: 'price', arr: priceRet, w: 1, dash: '', op: 0.5 },
    { k: 'ma5', arr: m5r, w: 1.2, dash: '', op: 1 },
    { k: 'ma10', arr: m10r, w: 1.2, dash: '', op: 1 },
    { k: 'ma20', arr: m20r, w: 1.5, dash: '', op: 1 },
    { k: 'ma60', arr: m60r, w: 1.2, dash: '', op: 1 },
  ];

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto', padding: '16px 24px', background: '#0b0e14', minHeight: '100vh', color: '#c9d1d9', fontFamily: 'system-ui' }}>
      <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 2 }}>
        {symbol} {name || symbol} — 三线+均线 <span style={{ fontSize: 12, fontWeight: 400, color: '#6b7280' }}>· {stock.length} 个交易日</span>
      </h3>
      <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 10, display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        <span>板块: {sector_name || '未知'}</span>
        <span>市值: {tags?.market_cap || '—'} | {tags?.board || '—'}</span>
        <span>现价: {prices?.[prices.length - 1]?.toFixed(2)}</span>
        {prices && <span>MA20: {ma20?.filter(v=>v!==null).pop()?.toFixed(2) || '—'}</span>}
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', gap: 14, marginBottom: 8, flexWrap: 'wrap', fontSize: 11 }}>
        {lines.map(l => l.arr && (
          <span key={l.k} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 16, height: 2.5, borderRadius: 2, background: (R as any)[l.k] || '#666' }} />
            <span style={{ color: l.k.startsWith('ma') || l.k === 'price' ? '#9ca3af' : '#c9d1d9' }}>{(L as any)[l.k]}</span>
          </span>
        ))}
      </div>

      {/* ── Single unified chart ── */}
      <svg viewBox={`0 0 ${CW} ${CH}`} style={{ width: '100%', background: '#111620', borderRadius: 8 }}>
        {/* Grid */}
        {yTicks.map((t, i) => (
          <g key={i}>
            <line x1={p.l} x2={CW - p.r} y1={Y(t)} y2={Y(t)} stroke="#1e2535" strokeWidth={i===0?1:0.5} />
            <text x={p.l - 6} y={Y(t) + 4} fill="#4b5563" fontSize={10} textAnchor="end">{t.toFixed(1)}%</text>
          </g>
        ))}
        {/* Zero */}
        <line x1={p.l} x2={CW - p.r} y1={Y(0)} y2={Y(0)} stroke="#374151" strokeWidth={1.2} strokeDasharray="5,3" />

        {/* Lines */}
        {lines.map(l => l.arr && (
          <path key={l.k} d={l.k.startsWith('ma') || l.k === 'price' ? mkNullPath(l.arr!) : mkPath(l.arr!)}
            fill="none" stroke={(R as any)[l.k]} strokeWidth={l.w} strokeLinejoin="round"
            strokeDasharray={l.dash || undefined} opacity={l.op} />
        ))}

        {/* End dots for top 3 */}
        {['stock','sector','market'].map(k => {
          const arr = k === 'sector' ? sector : k === 'market' ? market : stock;
          if (!arr) return null;
          return <circle key={k} cx={X(arr.length - 1)} cy={Y(arr[arr.length - 1])} r={3.5} fill={(R as any)[k]} stroke="#111620" strokeWidth={1.5} />;
        })}

        {/* Date labels */}
        {dates.map((d: string, i: number) => {
          const show = i === 0 || i === dates.length - 1 || i % Math.ceil(dates.length / 8) === 0;
          return show ? (
            <g key={i}>
              <line x1={X(i)} x2={X(i)} y1={p.t} y2={p.t + h} stroke="#1e2535" strokeWidth={0.5} strokeDasharray="2,4" />
              <text x={X(i)} y={CH - 10} fill="#6b7280" fontSize={9} textAnchor="middle">{d.slice(5)}</text>
            </g>
          ) : null;
        })}
      </svg>

      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 20, marginTop: 10, fontSize: 12 }}>
        {(['stock','sector','market'] as const).map(k => {
          const arr = k === 'sector' ? sector : k === 'market' ? market : stock;
          if (!arr) return null;
          const v = arr[arr.length - 1];
          return <div key={k}><span style={{ color: '#6e7a8a' }}>{(L as any)[k]}: </span><span style={{ fontWeight: 700, color: (R as any)[k] }}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</span></div>;
        })}
        {prices && <div><span style={{ color: '#6e7a8a' }}>现价: </span><span style={{ fontWeight: 700, color: '#9ca3af' }}>{prices[prices.length - 1].toFixed(2)}</span></div>}
      </div>

      {/* ── 第二图: 分时叠加 ── */}
      {iLoading && (
        <div style={{ marginTop:16, padding:'16px 20px', borderRadius:6, background:'#161b27', textAlign:'center' }}>
          <span style={{ color:'#f59e0b',fontSize:13 }}>⏳ 加载分时数据中... (个股分钟线需调用Tushare API)</span>
          <div style={{ fontSize:10, color:'#6e7a8a', marginTop:4 }}>近{lookback}日 · 预计 {lookback*3}~{lookback*10} 秒</div>
        </div>
      )}
      {iError && (
        <div style={{ marginTop:16, padding:'12px 16px', borderRadius:6, background:'rgba(239,68,68,0.06)', border:'1px solid rgba(239,68,68,0.15)', textAlign:'center' }}>
          <span style={{ color:'#f87171',fontSize:12 }}>分时数据加载失败: {iError}</span>
          <button onClick={() => { setIError(''); setILoading(true); api.get('/sanxian/intraday',{params:{symbol,lookback}}).then(r=>{setIdata(r.data);setILoading(false)}).catch(e=>{setIError(e?.message||'重试失败');setILoading(false)}); }}
            style={{ marginLeft:10, padding:'2px 10px', borderRadius:4, border:'1px solid #f87171', background:'transparent', color:'#f87171', cursor:'pointer', fontSize:11 }}>重试</button>
        </div>
      )}
      {!iLoading && !iError && idata && idata.status === 'success' && (
        <IntradayChart idata={idata} />
      )}

      {stock.length > 0 && data.analysis && (
        <InsightsCard analysis={data.analysis} dates={dates} prices={prices} />
      )}
    </div>
  );
}

function InsightsCard({ analysis: a, dates, prices }: { analysis: any; dates: string[]; prices: number[] }) {
  const posColors: Record<string, string> = {
    '领涨龙头': '#059669', '独立走强': '#059669', '逆势拉升': '#059669',
    '跟涨': '#6b7280', '逆势抗跌': '#f59e0b', '抗跌': '#6b7280',
    '主力出货': '#ef4444', '领跌': '#ef4444',
  };
  const st = a.sector_trend || {};

  return (
    <div style={{ marginTop: 10, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
      {/* Card 1: 综合位置 */}
      <div style={{ padding: '10px 12px', borderRadius: 6, background: '#161b27', fontSize: 11, lineHeight: 1.7 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9', marginBottom: 6 }}>🎯 综合位置</div>
        <div style={{ fontSize: 18, fontWeight: 800, color: posColors[a.position] || '#9ca3af' }}>{a.position}</div>
        <div style={{ color: '#6e7a8a', marginTop: 2 }}>
          {a.dates}天 | 现价在前{a.price_position}%分位
          {a.trend_5d > 0 ? <span style={{ color: '#10b981' }}> ↑{a.trend_5d}%</span>
                           : <span style={{ color: '#ef4444' }}> ↓{Math.abs(a.trend_5d)}%</span>}
        </div>
      </div>

      {/* Card 2: 三线强弱 */}
      <div style={{ padding: '10px 12px', borderRadius: 6, background: '#161b27', fontSize: 11, lineHeight: 1.7 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9', marginBottom: 6 }}>📈 三线强弱</div>
        <div style={{ color: a.alpha > 0 ? '#10b981' : '#ef4444' }}>
          相对{a.sector_name || '板块'}: {a.alpha >= 0 ? '+' : ''}{a.alpha}%
          {a.alpha > 5 ? ' ⬆独立' : a.alpha > 0 ? ' ⬆跑赢' : a.alpha < -5 ? ' ⬇跑输' : ' →持平'}
        </div>
        <div style={{ color: a.beta > 0 ? '#10b981' : '#ef4444' }}>
          相对大盘: {a.beta >= 0 ? '+' : ''}{a.beta}%
          {a.beta > 5 ? ' ⬆超额' : a.beta > 0 ? ' ⬆跑赢' : a.beta < -5 ? ' ⬇跑输' : ' →持平'}
        </div>
        <div style={{ color: '#6e7a8a', marginTop: 3 }}>
          个股 {a.stock_ret >= 0 ? '+' : ''}{a.stock_ret}% | 板块 {a.sector_ret >= 0 ? '+' : ''}{a.sector_ret}% | 大盘 {a.market_ret >= 0 ? '+' : ''}{a.market_ret}%
        </div>
      </div>

      {/* Card 3: MA多空矩阵 */}
      <div style={{ padding: '10px 12px', borderRadius: 6, background: '#161b27', fontSize: 11, lineHeight: 1.7 }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9', marginBottom: 6 }}>
          📏 均线多空
          <span style={{ marginLeft: 6, fontSize: 10, color: a.mult_head_score >= 70 ? '#10b981' : a.mult_head_score <= 30 ? '#ef4444' : '#f59e0b' }}>
            {a.mult_head_score}%多头
          </span>
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 8px' }}>
          {(a.ma_matrix || []).map((m: any) => (
            <span key={m.label} style={{
              color: m.above === null ? '#4b5563' : m.above ? '#10b981' : '#ef4444',
              fontSize: 10, fontFamily: 'JetBrains Mono, monospace',
            }}>
              {m.label} {m.above === null ? '·' : m.above ? '▲' : '▼'}
              {m.dist !== null && m.dist !== undefined ? m.dist.toFixed(1) + '%' : ''}
            </span>
          ))}
        </div>
      </div>

      {/* Row 2: 板块趋势 + 八种位置含义 */}
      <div style={{ padding: '10px 12px', borderRadius: 6, background: '#161b27', fontSize: 11, lineHeight: 1.7, gridColumn: 'span 2' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9', marginBottom: 6 }}>🏭 板块背景</div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
          <span>方向: {st.direction === '上升' ? '📈↑' : st.direction === '下降' ? '📉↓' : '➡→'} {st.direction || '—'}</span>
          <span>阶段: <span style={{ color: st.lifecycle === '退潮' ? '#ef4444' : st.lifecycle === '高潮' ? '#f59e0b' : st.lifecycle === '发酵' ? '#10b981' : '#6e7a8a' }}>{st.lifecycle || '—'}</span></span>
          <span>5日: {st.pct_5d != null ? (st.pct_5d >= 0 ? '+' : '') + st.pct_5d + '%' : '—'}</span>
          <span>20日: {st.pct_20d != null ? (st.pct_20d >= 0 ? '+' : '') + st.pct_20d + '%' : '—'}</span>
          <span>行业排名: #{st.rank_5d || '—'}/32</span>
          <span>量比: {st.vol_ratio != null ? st.vol_ratio.toFixed(1) + 'x' : '—'}</span>
        </div>
      </div>

      {/* Position explanation */}
      <div style={{ padding: '10px 12px', borderRadius: 6, background: '#161b27', fontSize: 10, lineHeight: 1.6, color: '#6e7a8a' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9', marginBottom: 4 }}>💡 交易含义</div>
        {(() => {
          const m: Record<string, string> = {
            '领涨龙头': '板块+大盘双涨，个股领涨。主力资金确认，可追但注意高位。',
            '独立走强': '板块下跌但个股上涨。最强信号——主力独立控盘，无视板块拖累。',
            '逆势拉升': '板块+大盘双跌，个股逆势拉升。可能是庄股或重大利好。',
            '逆势抗跌': '板块跌但大盘涨，个股跟大盘走。板块拖累是暂时的。',
            '跟涨': '板块+大盘双涨，个股跟随但未跑赢。被动上涨，注意区分真龙头。',
            '抗跌': '板块+大盘双跌，个股跌幅小于板块。相对抗跌但非逆势。',
            '主力出货': '板块+大盘双涨，唯独该股下跌。危险信号——主力借板块热度出货。',
            '领跌': '板块+大盘双跌，个股跌得比板块还多。最弱信号，远离。',
          };
          return <span style={{ color: '#9ca3af' }}>{m[a.position] || ''}</span>;
        })()}
      </div>
    </div>
  );
}

// ── 5日分时叠加图 ──
function IntradayChart({ idata }: { idata: any }) {
  const { stock, sector, lookback: lb, name } = idata;
  const CH = 300; const CW = 960; const pp = { t: 25, r: 40, b: 40, l: 60 };
  const pw = CW - pp.l - pp.r; const ph = CH - pp.t - pp.b;
  const allTimes = [...new Set([...(stock?.times||[]), ...(sector?.times||[])])].sort();
  if (allTimes.length < 2) return <div style={{ marginTop:16, padding:12, background:'#161b27', borderRadius:6, textAlign:'center', color:'#6e7a8a',fontSize:12 }}>分时数据加载中...</div>;
  const xStep = pw / Math.max(1, allTimes.length - 1);
  const X = (i: number) => pp.l + i * xStep;
  const interp = (bars: any, times: string[]) => {
    if (!bars?.vals?.length) return null;
    const map = new Map(bars.times.map((t:string,i:number) => [t, bars.vals[i]]));
    return times.map((t: string) => map.get(t) ?? null);
  };
  const sv = interp(stock, allTimes); const scv = interp(sector, allTimes);
  const allVals = [...(sv?.filter((v:any)=>v!==null) as number[]||[]), ...(scv?.filter((v:any)=>v!==null) as number[]||[])];
  const yMin = Math.min(-0.5, ...allVals) - 0.3; const yMax = Math.max(0.5, ...allVals) + 0.3;
  const yRng = yMax - yMin || 1; const Y = (v: number) => pp.t + ph - ((v - yMin) / yRng) * ph;
  const yTicks = Array.from({length:5}, (_,i) => yMin + (yRng/4)*i);
  const mk = (arr: (number|null)[]) => {
    const pts = arr.map((v,i) => v !== null ? {i,v} : null).filter(Boolean) as {i:number,v:number}[];
    if (pts.length < 2) return '';
    return pts.map((p,j) => `${j===0?'M':'L'}${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  };
  const IC = { stock: '#60a5fa', sector: '#f87171' } as any;
  const IL = { stock: '个股', sector: '板块' } as any;
  const dateSep = new Map<number, string>();
  for (let i = 1; i < allTimes.length; i++) {
    const d1 = allTimes[i-1]?.slice(0,10), d2 = allTimes[i]?.slice(0,10);
    if (d1 && d2 && d1 !== d2) dateSep.set(i, d1);
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div style={{ fontSize: 14, fontWeight: 700, color: '#c9d1d9', marginBottom: 2 }}>📉 分时叠加 — {name || '—'} 个股 vs 板块 近{lb}日</div>
      <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>每根K线 = 5分钟 | 百分比以首根为基准 (0%) | 黄竖线=日期分隔</div>
      <svg viewBox={`0 0 ${CW} ${CH}`} style={{ width: '100%', background: '#111620', borderRadius: 8 }}>
        {yTicks.map((t,i)=>(<g key={i}><line x1={pp.l} x2={CW-pp.r} y1={Y(t)} y2={Y(t)} stroke="#1e2535" strokeWidth={i===0?1:0.5}/><text x={pp.l-6} y={Y(t)+4} fill="#4b5563" fontSize={10} textAnchor="end">{t.toFixed(2)}%</text></g>))}
        <line x1={pp.l} x2={CW-pp.r} y1={Y(0)} y2={Y(0)} stroke="#374151" strokeWidth={1} strokeDasharray="4,3"/>
        {Array.from(dateSep.entries()).map(([i,d])=>(<g key={i}><line x1={X(i)} x2={X(i)} y1={pp.t} y2={pp.t+ph} stroke="#f59e0b" strokeWidth={0.5} strokeDasharray="2,2" opacity={0.3}/><text x={X(i)} y={pp.t+12} fill="#f59e0b" fontSize={8} textAnchor="middle" opacity={0.6}>{d?.slice(5)}</text></g>))}
        {(['stock','sector'] as const).map(k => {const arr = k==='stock'?sv:scv; if(!arr) return null; return <path key={k} d={mk(arr)} fill="none" stroke={IC[k]} strokeWidth={k==='stock'?2:1.3} strokeLinejoin="round" strokeDasharray={k==='sector'?'6,3':''} opacity={k==='sector'?0.8:1}/>;})}
        {(['stock','sector'] as const).map((k,i)=>(<text key={k} x={CW-pp.r-120+i*70} y={pp.t+14} fill={IC[k]} fontSize={10} fontWeight={600}>{IL[k]}</text>))}
        {allTimes.map((t,i)=>{const show=i===0||i===allTimes.length-1||(dateSep.has(i)&&i%Math.ceil(allTimes.length/12)===0);return show?<text key={i} x={X(i)} y={CH-8} fill="#6b7280" fontSize={8} textAnchor="middle">{t?.slice(11,16)}</text>:null;})}
      </svg>
      {/* ── 日内分析 ── */}
      <IntradayInsights stock={stock} sector={sector} allTimes={allTimes} sv={sv} scv={scv} />
    </div>
  );
}

function IntradayInsights({ stock, sector, allTimes, sv, scv }: { stock: any; sector: any; allTimes: string[]; sv: (number|null)[]; scv: (number|null)[] }) {
  if (!sv || !scv || sv.length < 10) return null;

  // 1. Per-day deviation (each day's closing diff)
  const dayDiffs: {date:string, stockEnd:number, sectorEnd:number, gap:number, aboveCount:number, totalCount:number}[] = [];
  let curDate='', curStockVals:number[]=[], curSectorVals:number[]=[];
  for (let i=0; i<allTimes.length; i++) {
    const d = allTimes[i]?.slice(0,10); const s = sv[i]; const c = scv[i];
    if (!d) continue;
    if (d !== curDate) {
      if (curDate) dayDiffs.push({ date:curDate, stockEnd:curStockVals[curStockVals.length-1]||0, sectorEnd:curSectorVals[curSectorVals.length-1]||0, gap:(curStockVals[curStockVals.length-1]||0)-(curSectorVals[curSectorVals.length-1]||0), aboveCount:curStockVals.filter((v,i)=>v!==null&&curSectorVals[i]!==null&&v>curSectorVals[i]!).length, totalCount:curStockVals.filter((v,i)=>v!==null&&curSectorVals[i]!==null).length });
      curDate=d; curStockVals=[]; curSectorVals=[];
    }
    if (s!==null) curStockVals.push(s);
    if (c!==null) curSectorVals.push(c);
  }
  if (curDate) dayDiffs.push({ date:curDate, stockEnd:curStockVals[curStockVals.length-1]||0, sectorEnd:curSectorVals[curSectorVals.length-1]||0, gap:(curStockVals[curStockVals.length-1]||0)-(curSectorVals[curSectorVals.length-1]||0), aboveCount:curStockVals.filter((v,i)=>v!==null&&curSectorVals[i]!==null&&v>curSectorVals[i]!).length, totalCount:curStockVals.filter((v,i)=>v!==null&&curSectorVals[i]!==null).length });

  // 2. Aggregate metrics
  const avgGap = dayDiffs.length ? dayDiffs.reduce((s,d)=>s+d.gap,0)/dayDiffs.length : 0;
  const beatsDays = dayDiffs.filter(d=>d.gap>0).length;
  const totalAbove = dayDiffs.reduce((s,d)=>s+d.aboveCount,0);
  const totalBars = dayDiffs.reduce((s,d)=>s+d.totalCount,0);
  const abovePct = totalBars ? (totalAbove/totalBars*100) : 50;

  // 3. Last 30 min (last 6 bars) bias
  const last6 = sv.slice(-6).filter(v=>v!==null); const last6sc = scv.slice(-6).filter(v=>v!==null);
  const last6Gap = last6.length && last6sc.length ? (last6[last6.length-1]||0) - (last6sc[last6sc.length-1]||0) : 0;

  // 4. Gap trend: widening or narrowing
  const gaps = dayDiffs.map(d=>d.gap);
  const gapTrend = gaps.length>=3 ? (gaps[gaps.length-1] - gaps[0]) : 0;

  const C = { green:'#10b981', red:'#ef4444', yellow:'#f59e0b', gray:'#6e7a8a', cyan:'#22d3ee' };

  return (
    <div style={{ marginTop: 8, display:'grid', gridTemplateColumns:'1fr 1fr', gap: 8 }}>
      <div style={{ padding:'8px 12px', borderRadius:6, background:'#161b27', fontSize:11, lineHeight:1.7 }}>
        <div style={{ fontSize:11, fontWeight:700, color:'#c9d1d9', marginBottom:4 }}>📊 日内跟随度</div>
        <div>日均偏离: <span style={{fontWeight:700, color: avgGap>0?C.green:C.red}}>{avgGap>=0?'+':''}{avgGap.toFixed(3)}%</span></div>
        <div>个股在板块上方时间: <span style={{fontWeight:700, color: abovePct>=50?C.green:abovePct>=40?C.yellow:C.red}}>{abovePct.toFixed(0)}%</span></div>
        <div>跑赢天数: <span style={{fontWeight:700}}>{beatsDays}/{dayDiffs.length}</span></div>
        <div>尾盘偏向: <span style={{fontWeight:700, color: last6Gap>0?C.green:C.red}}>{last6Gap>=0?'+':''}{last6Gap.toFixed(3)}% {last6Gap>0.01?'→尾盘走强':last6Gap<-0.01?'→尾盘走弱':'→持平'}</span></div>
      </div>
      <div style={{ padding:'8px 12px', borderRadius:6, background:'#161b27', fontSize:11, lineHeight:1.7 }}>
        <div style={{ fontSize:11, fontWeight:700, color:'#c9d1d9', marginBottom:4 }}>🔍 逐日明细</div>
        {dayDiffs.map((d,i) => (
          <div key={i} style={{ color: d.gap>0?C.green:C.red, fontFamily:'JetBrains Mono,monospace', fontSize:10 }}>
            {d.date.slice(5)}: {d.gap>=0?'+':''}{d.gap.toFixed(3)}% | 个股在板上 {d.totalCount?(d.aboveCount/d.totalCount*100).toFixed(0):'—'}%
          </div>
        ))}
        {gapTrend!==0 && (
          <div style={{ marginTop:4, paddingTop:4, borderTop:'1px solid #1e2535', color: gapTrend>0?C.green:C.red }}>
            {gapTrend>0?'📈 差距在扩大(个股走强)':gapTrend<-0.03?'📉 差距在扩大(个股走弱)':'→ 差距稳定'}
            <span style={{ color:'#6e7a8a',marginLeft:4 }}>{Math.abs(gapTrend)>0.05?`偏离已累计${Math.abs(gapTrend).toFixed(2)}%`:''}</span>
          </div>
        )}
      </div>
    </div>
  );
}
