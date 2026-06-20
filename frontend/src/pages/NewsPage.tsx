import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import api from '../lib/api';
import MetricCard from '../components/MetricCard';

const snapshotHint = (key: string, val: number | null | undefined): string => {
  if (val == null) return '';
  const v = Number(val);
  switch (key) {
    case 'm2_yoy': return v > 10 ? '偏宽松' : v > 7 ? '适度' : '偏紧';
    case 'shibor_on': return v < 1.3 ? '充裕' : v < 2.0 ? '正常' : '偏紧';
    case 'shibor_3m': return v < 1.6 ? '充裕' : v < 2.5 ? '正常' : '偏紧';
    case 'cpi_yoy': return v < 0 ? '通缩风险' : v < 2 ? '温和' : v < 4 ? '正常' : '通胀压力';
    case 'ppi_yoy': return v < 0 ? '通缩' : v < 3 ? '温和上行' : v < 5 ? '扩张' : '过热';
    case 'pmi': return v > 50.5 ? '扩张' : v > 50 ? '临界' : v > 49 ? '收缩' : '衰退';
    case 'margin': return v > 16000 ? '亢奋' : v > 14000 ? '偏热' : v > 12000 ? '正常' : '谨慎';
    default: return '';
  }
};

const REFERENCE_RANGES = [
  { label: 'M2 增速', body: '>10%=宽松利好, 7-10%=适度, <7%=收紧。当前反映央行货币政策松紧' },
  { label: 'M1-M2 剪刀差', body: '正值扩大=资金活化流入实体, 利好股市。负值=存款定期化, 避险情绪' },
  { label: 'SHIBOR 隔夜/3M', body: '隔夜<1.3%=流动性充裕, 3M<1.6%=资金成本低。走高=银行惜贷' },
  { label: 'LPR', body: '贷款市场报价利率。1Y=短期融资成本, 5Y=房贷基准。下行=降息利好' },
  { label: '10年国债', body: '无风险利率锚。<2.5%=低利率环境利好成长股, >3.5%=压制估值' },
  { label: 'CPI', body: '居民消费价格。1-3%温和=良性, <0%通缩=经济疲软, >4%=通胀收紧货币' },
  { label: 'PPI', body: '工业品出厂价格。>0%企业利润改善利好周期股, <0%=需求不足' },
  { label: 'CPI-PPI 剪刀差', body: '正值扩大=下游利润空间增大(利好消费), 负值=上游挤压下游' },
  { label: 'PMI', body: '采购经理指数。>50.5=经济扩张利好, 50±0.5=临界, <49=收缩风险' },
  { label: 'GDP 增速', body: '季频。>5.5%=强劲, 4.5-5.5%=平稳, <4%=下行压力' },
  { label: '融资余额', body: '>1.6万亿=杠杆亢奋(注意风险), 1.2-1.6万亿=正常, <1.2万亿=情绪谨慎' },
  { label: '融券余额', body: '做空力量参考。大幅上升=看空情绪浓厚, 下降=市场信心恢复' },
  { label: '北向持股', body: '外资通过沪深港通持有的A股总量。持续增加=外资看好, 持续减少=流出' },
  { label: '原油', body: 'INE上海原油。>600美元/桶=成本推动型通胀, <400=需求疲弱信号' },
  { label: '沪铜', body: '铜价上涨=工业需求旺盛(经济扩张), 下跌=需求萎缩。"铜博士"领先指标' },
  { label: '螺纹钢', body: '建筑钢材主力。价格上涨=基建地产活跃, 下跌=固定资产投资放缓' },
  { label: '沪金', body: '>500元/克=避险情绪浓, <400=风险偏好回升。金价与风险偏好负相关' },
];

// ══════════════════════════════════════════════════════════════════════════
// v4.8: 统一市场分类工具函数 (解决前端重复写正则的问题)
// ══════════════════════════════════════════════════════════════════════════
export const classifyMarket = (ts_code: string | undefined): 'main' | 'chinext' | 'sme' => {
  if (!ts_code) return 'main';
  // 科创板: 688xxx / 创业板: 300xxx / 301xxx
  if (/^(688|301|300)/.test(ts_code)) return 'chinext';
  // 中小板: 002xxx / 003xxx
  if (/^00[23]/.test(ts_code)) return 'sme';
  return 'main';
};

export const filterByMarket = (
  events: any[],
  market: string
): any[] => {
  if (market === '全部') return events;
  if (market === '主板') return events.filter(e => classifyMarket(e.ts_code) === 'main');
  if (market === '中小板') return events.filter(e => classifyMarket(e.ts_code) === 'sme');
  if (market === '创业板') return events.filter(e => classifyMarket(e.ts_code) === 'chinext');
  return events;
};

export default function NewsPage() {
  const navigate = useNavigate();
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsResult, setNewsResult] = useState<any>(null);
  const [newsError, setNewsError] = useState('');
  const [newsStep, setNewsStep] = useState('');
  const [newsPct, setNewsPct] = useState(0);

  // v4.8: 新闻新鲜度状态
  const [newsFreshness, setNewsFreshness] = useState<any>(null);

  // v4.8: 聚合数据状态 (替代原有的多个状态)
  const [dashboard, setDashboard] = useState<any>(null);

  // v2.1: 龙虎榜 SSE 刷新状态
  const [toplistLoading, setToplistLoading] = useState(false);
  const [toplistPct, setToplistPct] = useState(0);
  const [toplistStep, setToplistStep] = useState('');
  const [toplistError, setToplistError] = useState('');

  // 兼容旧状态
  const [todayEvents, setTodayEvents] = useState<any>(null);
  const [lastAnalysis, setLastAnalysis] = useState<{hours_ago:number,stale:boolean}|null>(null);
  const [marginSentiment, setMarginSentiment] = useState<any>(null);
  const [freshness, setFreshness] = useState<any>(null);
  const [sectorHeat, setSectorHeat] = useState<any>(null);
  const [toplistData, setToplistData] = useState<any>(null);
  const [toplistMarket, setToplistMarket] = useState<string>('全部');
  const [macroSnapshot, setMacroSnapshot] = useState<any>(null);
  const [macroBrief, setMacroBrief] = useState<any>(null);

  const loadMacro = async () => {
    try { const r = await api.get('/macro/snapshot'); setMacroSnapshot(r.data.data); } catch {}
    try { const r = await api.get('/macro/brief'); setMacroBrief(r.data); } catch {}
  };

  // v4.8: 聚合数据加载 (替代原来的 6 个独立请求)
  const loadDashboard = useCallback(async () => {
    try {
      const r = await api.get('/scan/news-dashboard');
      const data = r.data;
      setDashboard(data);

      // 填充原有状态 (兼容现有渲染逻辑)
      // v4.9: event_aggregator 已改为扁平结构 {stock_events, sector_events, last_analysis}
      const evts = data.events;
      if (evts?.stock_events?.length > 0 || evts?.sector_events?.length > 0) {
        setTodayEvents(evts);
      }
      if (evts?.last_analysis) {
        setLastAnalysis(evts.last_analysis);
      }
      // v4.9: 兼容两层嵌套结构 (API 可能在 margin 外层再加一层 data)
      const marginData = data.margin?.data || data.margin;
      setMarginSentiment(marginData);
      setFreshness(data.freshness);
      setSectorHeat(data.sector_heat);
      // 兼容 toplist 是 list (老代码) 或 dict (新代码)
      const tl = data.toplist;
      if (Array.isArray(tl)) {
        setToplistData({ stocks: tl, sectors: [], total: tl.length, date: new Date().toISOString().split('T')[0] });
      } else {
        setToplistData(tl);
      }
    } catch {}
  }, []);

  // v4.8: 加载新闻新鲜度 (决定是否需要完整爬取)
  const loadNewsFreshness = useCallback(async () => {
    try {
      const r = await api.get('/scan/news-freshness');
      setNewsFreshness(r.data);
    } catch {}
  }, []);

  // v4.8: 并行加载所有数据 (替换原来的串行 loadAll)
  const loadAll = useCallback(async () => {
    await Promise.all([loadDashboard(), loadNewsFreshness()]);
  }, [loadDashboard, loadNewsFreshness]);

  // v5.5: 页面加载时滚动到顶部
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'smooth' });
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);
  useEffect(() => { loadMacro(); }, []);

  // v4.8: 智能爬取建议
  const getCrawlRecommendation = () => {
    if (!newsFreshness) return { action: 'unknown', label: '检查中...', color: '#6e7a8a' };
    const rec = newsFreshness.recommendation;
    if (rec === 'skip') return { action: 'skip', label: '数据新鲜', color: '#10b981' };
    if (rec === 'crawl_only') return { action: 'crawl', label: '仅爬取', color: '#f59e0b' };
    if (rec === 'analyze_only') return { action: 'analyze', label: '仅分析', color: '#f59e0b' };
    return { action: 'full', label: '完整执行', color: '#ef4444' };
  };

  // v2.1: SSE 流式刷新龙虎榜
  const refreshToplist = async () => {
    setToplistLoading(true);
    setToplistError('');
    setToplistPct(0);
    setToplistStep('连接后端...');
    try {
      const resp = await fetch('/api/scan/toplist-refresh', { method: 'POST' });
      const reader = resp.body?.getReader();
      if (!reader) { setToplistError('无法读取响应流'); setToplistLoading(false); return; }
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
              const evt = JSON.parse(line.slice(6));
              if (evt.error) { setToplistError(evt.msg || '刷新失败'); setToplistLoading(false); return; }
              if (evt.done) {
                setToplistPct(100);
                setToplistStep('完成');
                // 重新拉取数据
                loadDashboard().catch(()=>{});
                setTimeout(() => setToplistLoading(false), 1000);
                return;
              }
              if (evt.phase) {
                const phaseMap: Record<string, {pct: number, label: string}> = {
                  sync: {pct: 25, label: '同步龙虎榜数据'},
                  analyze: {pct: 60, label: '分析个股席位'},
                  sector: {pct: 90, label: '计算板块共振'},
                };
                const info = phaseMap[evt.phase] || {pct: 50, label: evt.phase};
                setToplistPct(info.pct);
                setToplistStep(`${info.label}: ${evt.msg || ''}`);
              }
            } catch {}
          }
        }
      }
    } catch (e: any) {
      setToplistError(e.message || '网络错误');
    }
    setToplistLoading(false);
  };

  const crawlNews = async () => {
    setNewsLoading(true); setNewsError(''); setNewsResult(null);
    // v4.8: 根据新鲜度显示提示
    const rec = getCrawlRecommendation();
    setNewsStep(rec.action === 'skip' ? '数据新鲜，跳过爬取...' : '正在连接...');
    setNewsPct(0);
    try {
      const resp = await fetch('/api/scan/crawl-news?force=true', { method: 'POST' });
      const reader = resp.body?.getReader();
      if (!reader) { setNewsError('无法读取响应流'); setNewsLoading(false); return; }
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
              const evt = JSON.parse(line.slice(6));
              if (evt.error) { setNewsError(evt.msg); setNewsLoading(false); return; }
              if (evt.done) {
                setNewsResult(evt.data); setNewsPct(100); setNewsStep('完成');
                // v4.8: 使用聚合接口刷新数据
                loadDashboard().catch(()=>{});
                loadAll();
              } else if (evt.progress) {
                const base = evt.phase === 'stage2_analyze' ? 50 : 30;
                const rng = evt.phase === 'stage2_analyze' ? 25 : 20;
                setNewsPct(base + Math.round((evt.current / Math.max(evt.total, 1)) * rng));
                setNewsStep(evt.msg);
              } else {
                setNewsStep(evt.msg); if (evt.pct) setNewsPct(evt.pct);
              }
            } catch {}
          }
        }
      }
    } catch (e: any) { setNewsError(e.message || '网络错误'); }
    setNewsLoading(false);
  };

  const dirColor = (d: string) => d === 'bullish' ? '#ef4444' : d === 'bearish' ? '#10b981' : '#6e7a8a';
  const dirEmoji = (d: string) => d === 'bullish' ? '📈' : d === 'bearish' ? '📉' : '➖';

  // v4.8: 使用统一的市场分类函数
  const aEvents = todayEvents?.stock_events || [];
  // 如果后端已分类则使用，否则前端用统一函数分类
  const smeEvents = todayEvents?.sme_stock_events || aEvents.filter((e:any) => /^00[23]/.test(e.ts_code || ''));
  const mainEvents = todayEvents?.main_stock_events || aEvents.filter((e:any) => classifyMarket(e.ts_code) === 'main');
  const chinextEvents = todayEvents?.chinext_stock_events || aEvents.filter((e:any) => classifyMarket(e.ts_code) === 'chinext');
  const [eventMarket, setEventMarket] = useState<string>('全部');
  const sectorEvents = (todayEvents?.sector_events || []).filter((e:any) => !(e.sector||'').startsWith('宏观-'));
  const macroEvents = (todayEvents?.sector_events || []).filter((e:any) => (e.sector||'').startsWith('宏观-'));

  // v4.8: 智能爬取建议显示
  const crawlRec = getCrawlRecommendation();

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto', padding: 24, background: '#0b0e14', minHeight: '100vh', color: '#c9d1d9', fontFamily: 'system-ui' }}>
      {macroSnapshot && (
        <div style={{ marginBottom: 28 }}>
          {/* ── 顶部结论 Banner ── */}
          <div style={{
            background: 'linear-gradient(135deg, rgba(16,185,129,0.08) 0%, rgba(139,92,246,0.06) 100%)',
            border: '1px solid rgba(16,185,129,0.15)', borderRadius: 12, padding: '16px 20px',
            marginBottom: 20,
          }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: '#cbd5e1', marginBottom: 4 }}>
              {macroBrief?.macro_summary || '宏观数据加载中...'}
            </div>
            <div style={{ fontSize: 11, color: '#4b5563' }}>
              Tushare 结构化实时数据 · 零 LLM 成本 · 自动同步
            </div>
          </div>

          {/* ── 分组数据卡片 ── */}
          {[
            { title: '货币与利率', color: '#3b82f6', items: [
              { label: 'M2 增速', value: macroSnapshot?.m2_yoy?.value, change: macroSnapshot?.m2_yoy?.change, unit: '%', hint: snapshotHint('m2_yoy', macroSnapshot?.m2_yoy?.value) },
              { label: 'M1-M2 剪刀差', value: macroSnapshot?.m1_yoy?.value != null ? (macroSnapshot?.m1_yoy?.value - macroSnapshot?.m2_yoy?.value).toFixed(1) : null, unit: '%', hint: '正=资金活化' },
              { label: 'SHIBOR 隔夜', value: macroSnapshot?.shibor_on?.value, change: macroSnapshot?.shibor_on?.change, unit: '%', hint: snapshotHint('shibor_on', macroSnapshot?.shibor_on?.value) },
              { label: 'SHIBOR 3个月', value: macroSnapshot?.shibor_3m?.value, change: macroSnapshot?.shibor_3m?.change, unit: '%', hint: snapshotHint('shibor_3m', macroSnapshot?.shibor_3m?.value) },
              { label: 'LPR 1年期', value: macroSnapshot?.lpr_1y?.value, change: macroSnapshot?.lpr_1y?.change, unit: '%', hint: '贷款基准利率' },
              { label: '10年国债', value: macroSnapshot?.bond_10y_yield?.value, change: macroSnapshot?.bond_10y_yield?.change, unit: '%', hint: '无风险利率锚' },
            ]},
            { title: '通胀与景气', color: '#f59e0b', items: [
              { label: 'CPI 同比', value: macroSnapshot?.cpi_yoy?.value, change: macroSnapshot?.cpi_yoy?.change, unit: '%', hint: snapshotHint('cpi_yoy', macroSnapshot?.cpi_yoy?.value) },
              { label: 'PPI 同比', value: macroSnapshot?.ppi_yoy?.value, change: macroSnapshot?.ppi_yoy?.change, unit: '%', hint: snapshotHint('ppi_yoy', macroSnapshot?.ppi_yoy?.value) },
              { label: 'PMI 制造业', value: macroSnapshot?.pmi?.value, change: macroSnapshot?.pmi?.change, unit: '', hint: snapshotHint('pmi', macroSnapshot?.pmi?.value) },
              { label: 'GDP 增速', value: macroSnapshot?.gdp_yoy?.value, change: macroSnapshot?.gdp_yoy?.change, unit: '%', hint: '季频, >5.5%扩张' },
              { label: 'CPI-PPI 剪刀差', value: macroSnapshot?.cpi_yoy?.value != null ? (macroSnapshot?.cpi_yoy?.value - (macroSnapshot?.ppi_yoy?.value||0)).toFixed(1) : null, unit: '%', hint: '正=下游利润空间' },
            ]},
            { title: '资金情绪', color: '#10b981', items: [
              { label: '融资余额', value: ((macroSnapshot?.margin_balance?.value||0)/1e8).toFixed(0), change: macroSnapshot?.margin_balance?.change != null ? (macroSnapshot.margin_balance.change/1e8).toFixed(0) : null, unit: '亿', hint: snapshotHint('margin', (macroSnapshot?.margin_balance?.value||0)/1e8) },
              { label: '融券余额', value: ((macroSnapshot?.short_balance?.value||0)/1e8).toFixed(0), change: macroSnapshot?.short_balance?.change != null ? (macroSnapshot.short_balance.change/1e8).toFixed(0) : null, unit: '亿', hint: '做空力量参考' },
              { label: '北向持股', value: ((macroSnapshot?.north_hold_vol?.value||0)/1e8).toFixed(1), change: macroSnapshot?.north_hold_vol?.change != null ? (macroSnapshot.north_hold_vol.change/1e8).toFixed(1) : null, unit: '亿股', hint: '外资持仓总量' },
            ]},
            { title: '商品期货', color: '#8b5cf6', items: [
              { label: '原油 (INE)', value: macroSnapshot?.['commodity:crude_oil']?.value, change: macroSnapshot?.['commodity:crude_oil']?.change, unit: '元/桶', hint: '全球通胀之锚' },
              { label: '沪铜', value: macroSnapshot?.['commodity:copper']?.value, change: macroSnapshot?.['commodity:copper']?.change, unit: '元/吨', hint: '经济晴雨表' },
              { label: '螺纹钢', value: macroSnapshot?.['commodity:rebar']?.value, change: macroSnapshot?.['commodity:rebar']?.change, unit: '元/吨', hint: '基建地产风向标' },
              { label: '沪金', value: macroSnapshot?.['commodity:gold']?.value, change: macroSnapshot?.['commodity:gold']?.change, unit: '元/克', hint: '避险情绪指标' },
            ]},
          ].map((group, gi) => (
            <div key={gi} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: group.color, marginBottom: 8, letterSpacing: 0.5 }}>
                {group.title}
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {group.items.map((item, ii) => {
                  const v = item.value;
                  const display = v != null ? (typeof v === 'number' ? (Math.abs(v) > 1000 ? v.toFixed(0) : v.toFixed(2)) : v) : '—';
                  // v4.8: 与上一期比较的涨跌箭头
                  const change = item.change;
                  const changeArrow = change != null
                    ? (change > 0 ? <span style={{fontSize:13,color:'#ef4444',marginLeft:3}}>↑</span>
                      : change < 0 ? <span style={{fontSize:13,color:'#10b981',marginLeft:3}}>↓</span>
                      : <span style={{fontSize:11,color:'#4b5563',marginLeft:3}}>→</span>)
                    : null;
                  return (
                    <div key={ii} style={{
                      flex: '1 1 130px', minWidth: 110,
                      background: '#161b27', border: '1px solid #1e2535', borderRadius: 10,
                      padding: '12px 14px', textAlign: 'center',
                    }}>
                      <div style={{ fontSize: 22, fontWeight: 800, color: '#e2e8f0', lineHeight: 1.1 }}>
                        {changeArrow}{display}<span style={{ fontSize: 12, fontWeight: 400, color: '#6e7a8a', marginLeft: 2 }}>{item.unit}</span>
                      </div>
                      <div style={{ fontSize: 11, color: '#6e7a8a', marginTop: 2 }}>{item.label}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

          {/* ── 宏观评分 + 早报板块 ── */}
          {macroBrief?.sections && macroBrief.sections.length > 0 && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>
              {macroBrief.sections.map((s: any, i: number) => (
                <div key={i} style={{ flex: '1 1 220px', padding: '12px 14px', background: '#161b27', border: '1px solid #1e2535', borderRadius: 10 }}>
                  <div style={{ fontSize: 11, fontWeight: 600, color: '#a78bfa', marginBottom: 6 }}>{s.title}</div>
                  {s.items.map((item: string, j: number) => (
                    <div key={j} style={{ fontSize: 11, color: '#6e7a8a', lineHeight: 1.5 }}>· {item}</div>
                  ))}
                </div>
              ))}
            </div>
          )}

          {/* ── 底部名词解释 ── */}
          <details style={{ marginTop: 16, cursor: 'pointer' }}>
            <summary style={{ fontSize: 11, color: '#4b5563' }}>📖 指标参考范围</summary>
            <div style={{ marginTop: 8, padding: '12px 16px', background: '#0d1117', borderRadius: 8, fontSize: 11, color: '#6e7a8a', lineHeight: 1.7, display: 'flex', flexWrap: 'wrap', gap: '4px 24px' }}>
              {REFERENCE_RANGES.map((r, i) => (
                <span key={i}>
                  <span style={{ color: '#cbd5e1', fontWeight: 600 }}>{r.label}</span>
                  ：{r.body}
                </span>
              ))}
            </div>
          </details>
        </div>
      )}

      {/* ── 热门个股 + 风险个股 (近5日龙虎榜) v2.1 ── */}
      {sectorHeat && (sectorHeat.hot_sectors?.length > 0 || sectorHeat.risk_sectors?.length > 0) && (
        <div style={{ display:'flex', gap:12, marginBottom: 16 }}>
          <div style={{ flex:1, background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:12 }}>
            <div style={{ fontSize:12, color:'#ef4444', marginBottom:6, fontWeight:600 }}>
              🔥 热门个股 (近5日龙虎榜)
              <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 6 }}>
                按总净买降序
              </span>
            </div>
            {(sectorHeat.hot_sectors || []).slice(0,10).map((s:any,i:number) => (
              <div key={i} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', fontSize:12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                  <span style={{ color:'#06b6d4', fontWeight: 600, fontSize: 12 }}>{s.ts_code}</span>
                  <span style={{ color:'#c9d1d9', fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
                  {s.count > 1 && <span style={{ fontSize: 9, padding: '1px 4px', background: 'rgba(139,92,246,0.15)', color: '#a78bfa', borderRadius: 3 }}>×{s.count}</span>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <span style={{ color: s.avg_pct>0?'#ef4444':'#10b981', fontSize: 11 }}>
                    {s.avg_pct>0?'+':''}{s.avg_pct}%
                  </span>
                  <span style={{ color: s.total_net_wan>0?'#ef4444':'#10b981', fontSize: 11, fontWeight: 600, minWidth: 60, textAlign: 'right' }}>
                    {s.total_net_wan>0?'+':''}{s.total_net_wan}万
                  </span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ flex:1, background:'#161b27', border:'1px solid #1e2535', borderRadius:10, padding:12 }}>
            <div style={{ fontSize:12, color:'#10b981', marginBottom:6, fontWeight:600 }}>
              ⚠️ 风险个股
              <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 6 }}>
                按跌幅升序
              </span>
            </div>
            {(sectorHeat.risk_sectors || []).slice(0,5).map((s:any,i:number) => (
              <div key={i} style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'4px 0', fontSize:12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                  <span style={{ color:'#06b6d4', fontWeight: 600, fontSize: 12 }}>{s.ts_code}</span>
                  <span style={{ color:'#c9d1d9', fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.name}</span>
                  {s.count > 1 && <span style={{ fontSize: 9, padding: '1px 4px', background: 'rgba(139,92,246,0.15)', color: '#a78bfa', borderRadius: 3 }}>×{s.count}</span>}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                  <span style={{ color: s.avg_pct<0?'#10b981':'#ef4444', fontSize: 11, fontWeight: 600 }}>
                    {s.avg_pct}%
                  </span>
                  <span style={{ color: s.total_net_wan>0?'#ef4444':'#10b981', fontSize: 11, minWidth: 60, textAlign: 'right' }}>
                    {s.total_net_wan>0?'+':''}{s.total_net_wan}万
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}


      {/* ══════════════════════════════════════════════════════════════════════════
          第一步：新闻采集 + 个股事件 (整体移到页面最下方)
      ══════════════════════════════════════════════════════════════════════════ */}
      <div style={{ marginTop: 32, paddingTop: 20, borderTop: '1px solid #1e2535' }}>
        <h1 style={{ fontSize: 22, marginBottom: 4 }}>📰 第一步：新闻采集</h1>
        <p style={{ color: '#6e7a8a', marginBottom: 16, fontSize: 13 }}>近7天新闻事件 · 新鲜度自然衰减 · 新扫描不覆盖历史</p>

        {/* 新鲜度/情绪卡 */}
        <div style={{ display:'flex', gap:12, marginBottom: 12, flexWrap:'wrap' }}>
          {freshness?.stale && (
            <div style={{ flex:1, minWidth:250, padding: '10px 16px', borderRadius: 8, background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.2)' }}>
              <span style={{ fontSize: 16 }}>⚠️</span>
              <span style={{ fontSize: 13, color: '#f59e0b', marginLeft: 8 }}>{freshness.message || `数据滞后 ${freshness.lag_trading_days} 天`}</span>
            </div>
          )}
          {marginSentiment && (
            <MetricCard label={marginSentiment.label || "融资情绪"}
              value={`${marginSentiment.value_yi?.toLocaleString() || 0}${marginSentiment.unit || '亿'}`}
              trend={marginSentiment.level_note || marginSentiment.detail}
              trendUp={marginSentiment.level === '正常' || marginSentiment.level === '亢奋'}
              color={marginSentiment.level_color || '#10b981'} />
          )}
          {newsFreshness && crawlRec.action !== 'skip' && (
            <div style={{ padding: '8px 14px', borderRadius: 8, background: 'rgba(245,158,11,0.06)', border: `1px solid ${crawlRec.color}33` }}>
              <span style={{ fontSize: 11, color: crawlRec.color }}>
                {crawlRec.label}
                {newsFreshness.hours_since_crawl != null && ` · 爬取${newsFreshness.hours_since_crawl.toFixed(1)}h前`}
                {newsFreshness.hours_since_analysis != null && ` · 分析${newsFreshness.hours_since_analysis.toFixed(1)}h前`}
              </span>
            </div>
          )}
        </div>

        {/* 新闻速报按钮 + SSE 进度 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <button onClick={crawlNews} disabled={newsLoading}
            style={{ padding: '10px 28px', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600,
              cursor: newsLoading ? 'not-allowed' : 'pointer', background: newsLoading ? '#374151' : '#06b6d4', color: '#fff' }}>
            {newsLoading ? '⏳ 分析中...' : '📰 新闻速报'}
          </button>
          {lastAnalysis && (
            <span style={{ fontSize: 13, color: lastAnalysis.stale ? '#f59e0b' : '#6e7a8a' }}>
              {lastAnalysis.stale ? '⚠ ' : ''}上次: {lastAnalysis.hours_ago <= 1 ? '1h内' : lastAnalysis.hours_ago < 24 ? `${lastAnalysis.hours_ago.toFixed(0)}h前` : `${(lastAnalysis.hours_ago/24).toFixed(1)}天前`}
              {lastAnalysis.stale && ' (建议更新)'}
            </span>
          )}
          {newsError && <span style={{ fontSize: 13, color: '#ef4444' }}>❌ {newsError}</span>}
        </div>

        {newsLoading && (
          <div style={{ marginBottom: 16, padding: 12, background: '#161b27', borderRadius: 8, border: '1px solid #1e2535' }}>
            <div style={{ display:'flex',justifyContent:'space-between',marginBottom:6,fontSize:13 }}>
              <span style={{color:'#06b6d4'}}>{newsStep}</span>
              <span style={{color:'#6e7a8a'}}>{newsPct}%</span>
            </div>
            <div style={{height:4,background:'#1e2535',borderRadius:2}}>
              <div style={{height:'100%',width:`${newsPct}%`,background:'linear-gradient(90deg,#06b6d4,#10b981)',borderRadius:2,transition:'width .3s'}}/>
            </div>
          </div>
        )}

        {/* 三个事件卡片: 宏观/个股/板块 */}
        {(aEvents.length > 0 || sectorEvents.length > 0 || macroEvents.length > 0) && (
          <div style={{ display:'flex', gap:12, flexWrap:'wrap' }}>
            {macroEvents.length > 0 && (
              <div style={{ flex:1, minWidth:280, padding: 14, background: '#161b27', borderRadius: 10, border: '1px solid rgba(6,182,212,0.2)' }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: '#06b6d4', marginBottom: 10 }}>🌍 宏观环境 ({macroEvents.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {macroEvents.map((e: any, i: number) => (
                    <div key={i} style={{ fontSize: 13, padding: '6px 10px', background: 'rgba(30,37,53,0.5)', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 600, color: '#06b6d4' }}>{(e.sector||'').replace('宏观-','')}</span>
                      <span style={{ color: dirColor(e.direction) }}>{dirEmoji(e.direction)}</span>
                      <span style={{ color: '#8b949e', flex: 1 }}>{e.prediction}</span>
                      <span style={{ color: '#6e7a8a' }}>影响: {e.impact?.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {aEvents.length > 0 && (
              <div style={{ flex:1, minWidth:300, padding: 14, background: '#161b27', borderRadius: 10, border: '1px solid #1e2535' }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: '#c9d1d9', marginBottom: 4, display:'flex',alignItems:'center',gap:8 }}>
                  ⚡ 个股事件 ({eventMarket==='全部' ? aEvents.length : eventMarket==='主板' ? mainEvents.length : eventMarket==='中小板' ? smeEvents.length : chinextEvents.length})
                  {['全部','主板','中小板','创业板'].map(m => (
                    <button key={m} onClick={() => setEventMarket(m)}
                      style={{ padding:'1px 8px', borderRadius:10, fontSize:10, fontWeight:500, border:'1px solid',
                        background: eventMarket===m ? 'rgba(6,182,212,0.1)' : 'transparent',
                        color: eventMarket===m ? '#06b6d4' : '#6e7a8a',
                        borderColor: eventMarket===m ? 'rgba(6,182,212,0.2)' : '#1e2535',
                        cursor:'pointer' }}>{m}</button>
                  ))}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {filterByMarket(aEvents, eventMarket).map((e: any, i: number) => (
                    <div key={i} style={{ fontSize: 13, padding: '6px 10px', background: 'rgba(30,37,53,0.5)', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 600, color: '#06b6d4', minWidth: 90 }}>{e.ts_code}</span>
                      <span style={{ color: '#10b981', fontWeight: 500, minWidth: 80 }}>{e.name || ''}</span>
                      <span style={{ color: dirColor(e.direction) }}>{dirEmoji(e.direction)}</span>
                      <span style={{ color: '#8b949e', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{e.title}</span>
                      {e.days_ago > 0 && (
                        <span style={{ fontSize: 10, color: '#4b5563', whiteSpace: 'nowrap' }}>{e.days_ago < 1 ? '今天' : `${e.days_ago.toFixed(0)}天前`}</span>
                      )}
                      <span style={{ fontSize: 10, padding: '1px 4px', borderRadius: 3,
                        background: e.freshness >= 0.8 ? 'rgba(16,185,129,0.15)' : e.freshness >= 0.4 ? 'rgba(245,158,11,0.12)' : 'rgba(239,68,68,0.12)',
                        color: e.freshness >= 0.8 ? '#10b981' : e.freshness >= 0.4 ? '#f59e0b' : '#ef4444' }}>
                        {e.freshness >= 0.8 ? '●' : e.freshness >= 0.4 ? '◐' : '○'}
                      </span>
                      <span style={{ color: '#6e7a8a', fontSize: 11 }}>{e.display_score?.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {sectorEvents.length > 0 && (
              <div style={{ flex:1, minWidth:300, padding: 14, background: '#161b27', borderRadius: 10, border: '1px solid #1e2535' }}>
                <div style={{ fontSize: 14, fontWeight: 600, color: '#c9d1d9', marginBottom: 10 }}>📊 板块影响 ({sectorEvents.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {sectorEvents.map((e: any, i: number) => (
                    <div key={i} style={{ fontSize: 13, padding: '6px 10px', background: 'rgba(30,37,53,0.5)', borderRadius: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontWeight: 600, color: '#a78bfa' }}>{e.sector}</span>
                      <span style={{ color: dirColor(e.direction) }}>{dirEmoji(e.direction)}</span>
                      <span style={{ color: '#8b949e', flex: 1 }}>{e.prediction}</span>
                      <span style={{ color: '#6e7a8a' }}>影响: {e.impact?.toFixed(1)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {!todayEvents && !newsLoading && (
          <div style={{ textAlign: 'center', padding: 40, color: '#4b5563' }}>
            <div style={{ fontSize: 40, marginBottom: 8 }}>📰</div>
            <div style={{ fontSize: 14 }}>点击「新闻速报」采集并分析最新财经资讯</div>
          </div>
        )}

        {/* 下一步按钮 (v2.1 恢复) */}
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 16, paddingTop: 12, borderTop: '1px solid #1e2535' }}>
          <button onClick={() => navigate('/scan')}
            style={{ padding: '10px 28px', border: 'none', borderRadius: 8, fontSize: 14, fontWeight: 600,
              cursor: 'pointer', background: '#3b82f6', color: '#fff' }}>
            下一步 → TG扫描
          </button>
        </div>
      </div>

      {/* 关闭最外层 div (页面容器) */}
    </div>
  );
}
