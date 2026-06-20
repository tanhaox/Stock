import { useEffect, useState } from 'react';
import api from '../lib/api';
import MetricCard from '../components/MetricCard';

export default function MonitorPage() {
  // ── 老兵回测 ──
  const [vetBacktest, setVetBacktest] = useState<any>(null);
  const [vetLoading, setVetLoading] = useState(false);
  // ── 原型校准 ──
  const [calibration, setCalibration] = useState<any>(null);
  const [calLoading, setCalLoading] = useState(false);
  // ── 权重训练 ──
  const [weights, setWeights] = useState<any>(null);
  const [wtLoading, setWtLoading] = useState(false);
  // ── 系统状态 ──
  const [sysStatus, setSysStatus] = useState<any>(null);
  const [ssLoading, setSsLoading] = useState(false);
  // ★ v4.3: 组件就绪状态
  const [readiness, setReadiness] = useState<any>(null);
  const [rdLoading, setRdLoading] = useState(false);
  // ── 最后刷新 ──
  const [lastRefresh, setLastRefresh] = useState<string>('');

  const loadAll = async () => {
    setVetLoading(true); setCalLoading(true); setWtLoading(true); setSsLoading(true); setRdLoading(true);
    try { const r = await api.get('/alphaflow/veteran-backtest', { params: { days: 180 } }); setVetBacktest(r.data); } catch {}
    setVetLoading(false);
    try { const r = await api.get('/learning/archetypes/calibration-data', { params: { days: 180 } }); setCalibration(r.data); } catch {}
    setCalLoading(false);
    try { const r = await api.get('/learning/weights-trained'); setWeights(r.data); } catch {}
    setWtLoading(false);
    try { const r = await api.get('/alphaflow/status'); setSysStatus(r.data); } catch {}
    setSsLoading(false);
    try { const r = await api.get('/learning/system-readiness'); setReadiness(r.data?.data); } catch {}
    setRdLoading(false);
    setLastRefresh(new Date().toLocaleTimeString());
  };

  useEffect(() => { loadAll(); }, []);

  return (
    <div style={{ maxWidth: 1400, margin: '0 auto' }}>
      {/* ── 顶部 ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <div>
          <h2 style={{ margin: 0 }}>📊 系统监控面板</h2>
          <p style={{ color: '#6e7a8a', fontSize: 12, margin: '4px 0 0' }}>
            老兵突破率 · 原型校准 · 权重训练状态 · 模型版本
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {lastRefresh && <span style={{ fontSize: 10, color: '#4b5563' }}>刷新: {lastRefresh}</span>}
          <button onClick={loadAll}
            style={{ padding: '8px 20px', borderRadius: 8, border: '1px solid #3b82f6', background: 'transparent', color: '#3b82f6', cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
            🔄 全部刷新
          </button>
        </div>
      </div>

      {/* ── 快速状态卡片 ── */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <MetricCard label="AlphaFlow 池" value={sysStatus?`${sysStatus.total_pool||0} 只`:'—'} trend={sysStatus?`今日更新 ${sysStatus.updated_today||0}`:''} color="#3b82f6"/>
        <MetricCard label="XGBoost 模型" value={sysStatus?.model_version?`v${sysStatus.model_version}`:'—'} trend={sysStatus?.model_version==='fallback_rules'?'⚠ 降级规则':sysStatus?.model_version?'✅ 正常':''} trendUp={sysStatus?.model_version&&sysStatus.model_version!=='fallback_rules'} color={sysStatus?.model_version&&sysStatus.model_version!=='fallback_rules'?'#10b981':'#ef4444'}/>
        <MetricCard label="已训练参数" value={weights?`${weights.bayesian_trained_params||0} 个`:'—'} trend={weights?.detail?.[0]?.n?`${weights.detail[0].n} 条观测`:''} color={(weights?.bayesian_trained_params||0)>0?'#10b981':'#f59e0b'}/>
        <MetricCard label="老兵突破率 T+20" value={vetBacktest?.breakout_rate_20d!=null?`${vetBacktest.breakout_rate_20d}%`:'—'} trend={vetBacktest?.total_veterans?`${vetBacktest.total_veterans} 样本`:''} color={(vetBacktest?.breakout_rate_20d||0)>=30?'#10b981':(vetBacktest?.breakout_rate_20d||0)>0?'#f59e0b':'#6e7a8a'}/>
      </div>

      {/* ── 主内容: 两栏布局 ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>
        {/* ── 老兵突破率回测 ── */}
        <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, color: '#c9d1d9' }}>
            🎖 老兵突破率回测
            <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 8 }}>近180天</span>
          </div>
          {vetLoading ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>加载中...</div>
          ) : !vetBacktest ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#4b5563' }}>数据加载失败</div>
          ) : vetBacktest.status === 'skipped' ? (
            <div style={{ textAlign: 'center', padding: 30, color: '#f59e0b' }}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>📭</div>
              <div style={{ fontSize: 13 }}>{vetBacktest.reason || '无历史 veteran 数据'}</div>
              <div style={{ fontSize: 10, color: '#6e7a8a', marginTop: 6 }}>需要 alphaflow_pool 表积累 veteran 记录</div>
            </div>
          ) : (
            <div>
              {/* T+5 / T+20 对比 */}
              <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
                <div style={{ flex: 1, textAlign: 'center', padding: '12px 0', background: '#0b0e14', borderRadius: 8 }}>
                  <div style={{ fontSize: 10, color: '#6e7a8a', marginBottom: 4 }}>T+5 突破率</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: '#3b82f6' }}>{vetBacktest.breakout_rate_5d}%</div>
                  <div style={{ fontSize: 9, color: '#4b5563', marginTop: 2 }}>平均盈 {vetBacktest.avg_gain_5d}%</div>
                </div>
                <div style={{ flex: 1, textAlign: 'center', padding: '12px 0', background: '#0b0e14', borderRadius: 8 }}>
                  <div style={{ fontSize: 10, color: '#6e7a8a', marginBottom: 4 }}>T+20 突破率</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: '#8b5cf6' }}>{vetBacktest.breakout_rate_20d}%</div>
                  <div style={{ fontSize: 9, color: '#4b5563', marginTop: 2 }}>平均盈 {vetBacktest.avg_gain_20d}%</div>
                </div>
              </div>

              {/* 按级别分段 */}
              {vetBacktest.by_level && Object.keys(vetBacktest.by_level).length > 0 && (
                <div>
                  <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>按老兵级别:</div>
                  {Object.entries(vetBacktest.by_level).map(([level, data]: [string, any]) => (
                    <div key={level} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderRadius: 6, marginBottom: 4, background: 'rgba(30,37,53,0.4)' }}>
                      <span style={{ fontSize: 11, fontWeight: 600, color: '#c9d1d9' }}>
                        {level === 'pre_breakout' ? '🔥 预突破' : level === 'late_stage' ? '⏳ 后期' : '👀 监控'}
                      </span>
                      <div style={{ display: 'flex', gap: 16 }}>
                        <span style={{ fontSize: 10, color: '#6e7a8a' }}>{data.count}只</span>
                        <span style={{ fontSize: 11, fontWeight: 600, color: '#3b82f6' }}>T+5: {data.breakout_rate_5d}%</span>
                        <span style={{ fontSize: 11, fontWeight: 600, color: '#8b5cf6' }}>T+20: {data.breakout_rate_20d}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 阈值分析 */}
              {vetBacktest.threshold_analysis && vetBacktest.threshold_analysis.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 6 }}>分阈值突破率:</div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    {vetBacktest.threshold_analysis.map((t: any) => (
                      <div key={t.score_threshold} style={{
                        flex: 1, textAlign: 'center', padding: '6px 4px', borderRadius: 6,
                        background: t.breakout_rate >= 30 ? 'rgba(16,185,129,0.06)' : 'rgba(30,37,53,0.4)',
                        border: `1px solid ${t.breakout_rate >= 30 ? 'rgba(16,185,129,0.12)' : '#1e2535'}`,
                      }}>
                        <div style={{ fontSize: 9, color: '#4b5563' }}>≥{t.score_threshold}分</div>
                        <div style={{ fontSize: 11, fontWeight: 600, color: t.breakout_rate >= 30 ? '#10b981' : '#6e7a8a' }}>
                          {t.breakout_rate}%
                        </div>
                        <div style={{ fontSize: 8, color: '#4b5563' }}>{t.stocks_above}只</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div style={{ marginTop: 12, padding: '8px 12px', borderRadius: 6, background: 'rgba(30,37,53,0.3)', fontSize: 10, color: '#6e7a8a' }}>
                {vetBacktest.verdict}
              </div>
            </div>
          )}
        </div>

        {/* ── 原型校准数据 ── */}
        <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, color: '#c9d1d9' }}>
            🎯 原型校准数据
            <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 8 }}>近180天推荐</span>
          </div>
          {calLoading ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#6e7a8a' }}>加载中...</div>
          ) : !calibration ? (
            <div style={{ textAlign: 'center', padding: 40, color: '#4b5563' }}>数据加载失败</div>
          ) : calibration.status === 'no_data' ? (
            <div style={{ textAlign: 'center', padding: 30, color: '#f59e0b' }}>
              <div style={{ fontSize: 28, marginBottom: 8 }}>📭</div>
              <div style={{ fontSize: 13 }}>无校准数据</div>
              <div style={{ fontSize: 10, color: '#6e7a8a', marginTop: 6 }}>需要更多 recommendation_tracking 记录</div>
            </div>
          ) : (
            <div>
              {/* 全局胜率 */}
              <div style={{ textAlign: 'center', padding: 12, background: '#0b0e14', borderRadius: 8, marginBottom: 16 }}>
                <div style={{ fontSize: 10, color: '#6e7a8a', marginBottom: 4 }}>全局 T+3 胜率</div>
                <div style={{ fontSize: 32, fontWeight: 700, color: (calibration.global_win_rate * 100) >= 55 ? '#10b981' : '#f59e0b' }}>
                  {(calibration.global_win_rate * 100).toFixed(1)}%
                </div>
                <div style={{ fontSize: 9, color: '#4b5563', marginTop: 2 }}>{calibration.total_samples} 条推荐</div>
              </div>

              {/* 各原型vs全局 */}
              <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>各原型胜率 vs 全局:</div>
              {Object.entries(calibration.archetypes || {}).map(([arch, data]: [string, any]) => {
                const ARCH_CN: Record<string, string> = {
                  large_bluechip: '大盘蓝筹', small_speculative: '小盘题材',
                  growth_tech: '科技成长', value_defensive: '价值防御',
                  cyclical_resource: '周期资源',
                };
                const barPct = Math.min(100, Math.max(5, (data.win_rate || 0) * 100));
                return (
                  <div key={arch} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, marginBottom: 3 }}>
                      <span style={{ color: '#c9d1d9' }}>{ARCH_CN[arch] || arch}</span>
                      <div>
                        <span style={{ fontWeight: 600, color: data.win_rate >= calibration.global_win_rate ? '#10b981' : '#ef4444' }}>
                          {(data.win_rate * 100).toFixed(0)}%
                        </span>
                        <span style={{ color: '#4b5563', marginLeft: 6 }}>
                          ({data.count}条 {(data.vs_global > 0 ? '+' : '')}{(data.vs_global * 100).toFixed(1)}%)
                        </span>
                      </div>
                    </div>
                    <div style={{ height: 4, background: '#1e2535', borderRadius: 2 }}>
                      <div style={{
                        height: '100%', width: `${barPct}%`, borderRadius: 2,
                        background: data.win_rate >= calibration.global_win_rate
                          ? 'linear-gradient(90deg, #10b981, #34d399)'
                          : 'linear-gradient(90deg, #ef4444, #f87171)',
                      }}/>
                    </div>
                  </div>
                );
              })}

              {/* 全局胜率参考线 */}
              <div style={{ marginTop: 8, paddingLeft: `${Math.min(100, calibration.global_win_rate * 100)}%`, borderLeft: '1px dashed #f59e0b', height: 4, position: 'relative' }}>
                <span style={{ position: 'absolute', top: -14, left: -16, fontSize: 8, color: '#f59e0b' }}>{'← 全局平均'}</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── 权重训练状态 ── */}
      <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20, marginBottom: 20 }}>
        <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12, color: '#c9d1d9' }}>
          🏋️ 评分权重训练状态
          <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 8 }}>Logistic Regression · 每周一自动训练</span>
        </div>
        {wtLoading ? (
          <div style={{ textAlign: 'center', padding: 30, color: '#6e7a8a' }}>加载中...</div>
        ) : !weights ? (
          <div style={{ textAlign: 'center', padding: 30, color: '#4b5563' }}>数据加载失败 — 后端可能未运行</div>
        ) : (
          <div>
            <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
              <div style={{ flex: 1, textAlign: 'center', padding: '10px 0', background: '#0b0e14', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#6e7a8a' }}>已训练参数</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: weights.bayesian_trained_params > 0 ? '#10b981' : '#f59e0b' }}>
                  {weights.bayesian_trained_params}
                </div>
              </div>
              <div style={{ flex: 1, textAlign: 'center', padding: '10px 0', background: '#0b0e14', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#6e7a8a' }}>训练观测数</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: (weights.detail?.[0]?.n || 0) >= 50 ? '#10b981' : '#f59e0b' }}>
                  {weights.detail?.[0]?.n || 0}
                </div>
              </div>
              <div style={{ flex: 1, textAlign: 'center', padding: '10px 0', background: '#0b0e14', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#6e7a8a' }}>训练方法</div>
                <div style={{ fontSize: 13, fontWeight: 600, color: '#8b5cf6' }}>Logistic Regression</div>
              </div>
              <div style={{ flex: 1, textAlign: 'center', padding: '10px 0', background: '#0b0e14', borderRadius: 8 }}>
                <div style={{ fontSize: 10, color: '#6e7a8a' }}>安全门控</div>
                <div style={{ fontSize: 11, fontWeight: 600, color: (weights.detail?.[0]?.n || 0) >= 50 ? '#10b981' : '#ef4444' }}>
                  {(weights.detail?.[0]?.n || 0) >= 50 ? '✅ 已通过' : '⚠ 样本不足'}
                </div>
              </div>
            </div>

            {/* Top 权重展示 */}
            <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>Top-8 训练权重:</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6 }}>
              {(weights.detail || []).slice(0, 8).map((p: any) => (
                <div key={p.name} style={{
                  display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                  padding: '8px 12px', background: '#0b0e14', borderRadius: 6,
                  border: '1px solid #1e2535',
                }}>
                  <code style={{ fontSize: 10, color: '#06b6d4' }}>{p.name.replace('_weight','')}</code>
                  <div>
                    <span style={{ fontSize: 12, fontWeight: 700, color: '#c9d1d9' }}>{p.mu?.toFixed(2)}</span>
                    <span style={{ fontSize: 8, color: p.n >= 50 ? '#10b981' : '#f59e0b', marginLeft: 4 }}>
                      n{p.n}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 12, fontSize: 10, color: '#4b5563' }}>
              {weights.message || '权重由 real profit/loss data 训练, deep_scorer 通过 get_beliefs() 自动加载'}
            </div>
          </div>
        )}
      </div>

      {/* ── ★ v4.3: 组件就绪状态 ── */}
      {readiness && (
        <div style={{ background: '#161b27', border: '1px solid #1e2535', borderRadius: 12, padding: 20, marginBottom: 20 }}>
          <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 16, color: '#c9d1d9' }}>
            🔧 组件就绪状态
            <span style={{ fontSize: 10, color: '#6e7a8a', fontWeight: 400, marginLeft: 8 }}>自动检测 + 达标自动激活</span>
          </div>

          {/* 分段权重 */}
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 11, color: '#6e7a8a', marginBottom: 8 }}>分段权重:</div>
            {['bull','bear','range'].map(regime => {
              const d = readiness.regime_weights?.[regime];
              const pct = d?.pct || 0;
              const ready = d?.ready;
              return (
                <div key={regime} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <span style={{ fontSize: 10, width: 40, color: regime==='bull'?'#ef4444':regime==='bear'?'#10b981':'#f59e0b', fontWeight:600 }}>
                    {regime==='bull'?'牛':regime==='bear'?'熊':'震'}
                  </span>
                  <div style={{ flex:1, height:6, background:'#1e2535', borderRadius:3 }}>
                    <div style={{ height:'100%', width:`${pct}%`, borderRadius:3,
                      background: ready ? 'linear-gradient(90deg,#10b981,#34d399)' : '#374151',
                      transition:'width 0.5s' }}/>
                  </div>
                  <span style={{ fontSize:9, color:'#6e7a8a', minWidth:70 }}>
                    {d?.samples || 0}/{d ? 50 : '?'}样本
                  </span>
                  <span style={{ fontSize:8, fontWeight:600, color: ready?'#10b981':'#4b5563', minWidth:40 }}>
                    {ready ? '✅ 就绪' : pct>0 ? '⏳ 积累中' : '—'}
                  </span>
                </div>
              );
            })}
          </div>

          {/* 校准器 + 原型 */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:16 }}>
            <div>
              <div style={{ fontSize:11, color:'#6e7a8a', marginBottom:6 }}>分段校准器:</div>
              {['bull','bear','range'].map(regime => {
                const d = readiness.regime_calibration?.[regime];
                return (
                  <div key={regime} style={{ fontSize:10, color:'#6e7a8a', marginBottom:2 }}>
                    <span style={{color: regime==='bull'?'#ef4444':regime==='bear'?'#10b981':'#f59e0b'}}>{regime==='bull'?'牛':regime==='bear'?'熊':'震'}</span>
                    : {d?.samples || 0}样本 {d?.ready ? '✅' : ''}
                  </div>
                );
              })}
            </div>
            <div>
              <div style={{ fontSize:11, color:'#6e7a8a', marginBottom:6 }}>原型偏移校准:</div>
              {readiness.archetype_offsets && Object.entries(readiness.archetype_offsets).slice(0,6).map(([arch, d]:[string,any]) => (
                <div key={arch} style={{ fontSize:10, color:'#6e7a8a', marginBottom:2 }}>
                  {arch}: {d.samples}条 {d.ready ? '✅' : ''}
                </div>
              ))}
            </div>
          </div>

          {/* 训练数据 */}
          <div style={{ marginTop:12, padding:'8px 12px', borderRadius:6, background:'rgba(30,37,53,0.4)', display:'flex', gap:20 }}>
            <span style={{ fontSize:10, color:'#6e7a8a' }}>
              训练数据: <b style={{color:'#c9d1d9'}}>{readiness.training_data?.total || 0}条</b>
            </span>
            <span style={{ fontSize:10, color:'#6e7a8a' }}>
              已验证: <b style={{color:'#10b981'}}>{readiness.training_data?.verified || 0}条</b>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
