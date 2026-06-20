import { BrowserRouter, Routes, Route, useNavigate, useLocation } from 'react-router-dom';
import { useState } from 'react';
import { ConfigProvider } from 'antd';
import { T as TOKENS } from './lib/designTokens';
import ScanPage from './pages/ScanPage';
import NewsPage from './pages/NewsPage';
import AnalysisPage from './pages/AnalysisPage';
import ResultPage from './pages/ResultPage';
import DeepAnalysisPage from './pages/DeepAnalysisPage';
import StockSelectPage from './pages/StockSelectPage';
import AmbushPage from './pages/AmbushPage';
import HoldingsPage from './pages/HoldingsPage';
import AlphaFlowPage from './pages/AlphaFlowPage';
import LearningPage from './pages/LearningPage';
import SettingsPage from './pages/SettingsPage';
import MonitorPage from './pages/MonitorPage';
import SanxianPage from './pages/SanxianPage';
import 'antd/dist/reset.css';

const pipelineSteps = [
  { path: '/', label: '宏观看板', sub: '宏观→数据→事件', num: '1', color: '#06b6d4' },
  { path: '/scan', label: 'TG信号扫描', sub: '全市场→股票池', num: '2', color: '' },
  { path: '/analysis', label: '多维度评分', sub: '精选→LLM入口', num: '3', color: '' },
  { path: '/deep-analysis', label: 'LLM深度分析', sub: 'DeepSeek R1', num: '4', color: '', lock: false },
  { path: '/result', label: '最终推荐', sub: '信号质量→排名', num: '5', color: '', lock: false },
];

const sideSteps = [
  { path: '/alphaflow', label: '主升浪捕获', sub: 'AlphaFlow', num: '🏆', color: '#8b5cf6' },
  { path: '/holdings', label: '持仓管理', sub: '退出信号', num: 'H', color: '#f97316' },
  { path: '/ambush', label: '潜伏猎手', sub: '洗盘突破', num: 'L', color: '#ef4444' },
  { path: '/learning', label: 'AI自学习', sub: '策略优化', num: 'A', color: '#8b5cf6' },
  { path: '/monitor', label: '系统监控', sub: '回测校准', num: 'M', color: '#10b981' },
  { path: '/settings', label: '系统设置', sub: 'API配置', num: '⚙', color: '#6e7a8a' },
];

function TopBar() {
  const navigate = useNavigate();
  return (
    <div style={{ display:'flex',alignItems:'center',justifyContent:'space-between',padding:'10px 24px',background:'#111620',borderBottom:'1px solid #1e2535',position:'sticky',top:0,zIndex:100 }}>
      <div style={{ fontSize:17,fontWeight:700,cursor:'pointer' }} onClick={() => navigate('/')}>
        <span style={{ color:'#3b82f6' }}>股票分析师</span> 系统
      </div>
      <span style={{ fontSize:11,color:'#6e7a8a',background:'#161b27',padding:'3px 10px',borderRadius:4 }}>v2026.5.30</span>
    </div>
  );
}

function PipelineNav() {
  const location = useLocation();
  const navigate = useNavigate();
  const isActive = (path: string) => location.pathname === path;
  return (
    <div style={{ display:'flex',alignItems:'center',padding:'12px 24px',background:'#0b0e14',borderBottom:'1px solid #1e2535',gap:4,flexWrap:'wrap' }}>
      {pipelineSteps.map((step, i) => (
        <span key={step.path} style={{ display:'flex',alignItems:'center',gap:8 }}>
          <div onClick={() => navigate(step.path)} style={{ display:'flex',alignItems:'center',gap:10,padding:'8px 16px',borderRadius:8,cursor:'pointer',fontSize:13,fontWeight:500,color:isActive(step.path)?step.color||'#3b82f6':'#6e7a8a',background:isActive(step.path)?'rgba(59,130,246,0.08)':'transparent',border:isActive(step.path)?'1px solid #3b82f6':'1px solid transparent',transition:'all .15s' }}>
            <div style={{ width:24,height:24,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',fontSize:12,fontWeight:700,background:isActive(step.path)?'#3b82f6':'#1e2535',color:isActive(step.path)?'#fff':'#6e7a8a' }}>{step.num}</div>
            <div><div>{step.label}</div><div style={{fontSize:10,color:'#6e7a8a'}}>{step.sub}</div></div>
          </div>
          {i < pipelineSteps.length - 1 && <span style={{color:'#3d4756',fontSize:18}}>→</span>}
        </span>
      ))}
      <div style={{flex:1}}/>
      {sideSteps.map(step => (
        <div key={step.path} onClick={() => navigate(step.path)} style={{ display:'flex',alignItems:'center',gap:10,padding:'8px 14px',borderRadius:8,cursor:'pointer',fontSize:12,fontWeight:500,color:isActive(step.path)?step.color:'#6e7a8a',background:isActive(step.path)?'rgba(59,130,246,0.05)':'transparent',border:isActive(step.path)?'1px solid '+step.color:'1px solid transparent',marginLeft:8,transition:'all .15s' }}>
          <div style={{ width:22,height:22,borderRadius:'50%',display:'flex',alignItems:'center',justifyContent:'center',fontSize:10,fontWeight:700,background:step.color,color:'#fff' }}>{step.num}</div>
          <div><div>{step.label}</div><div style={{fontSize:9,color:'#6e7a8a'}}>{step.sub}</div></div>
        </div>
      ))}
    </div>
  );
}

export default function App() {
  return (
    <ConfigProvider theme={{ token: { colorPrimary: '#3b82f6', borderRadius: TOKENS.radius.card, fontSize: 13, fontFamily: 'system-ui, -apple-system, sans-serif' } }}>
    <BrowserRouter>
      <div style={{ minHeight:'100vh',background:'#0b0e14',color:'#c9d1d9',fontFamily:'system-ui' }}>
        <TopBar/>
        <PipelineNav/>
        <div style={{ padding:'20px 24px',maxWidth:1600,margin:'0 auto' }}>
          <Routes>
            <Route path="/" element={<NewsPage/>}/>
            <Route path="/scan" element={<ScanPage/>}/>
            <Route path="/analysis" element={<AnalysisPage/>}/>
            <Route path="/select" element={<StockSelectPage/>}/>
            <Route path="/deep-analysis" element={<DeepAnalysisPage/>}/>
            <Route path="/result" element={<ResultPage/>}/>
            <Route path="/holdings" element={<HoldingsPage/>}/>
            <Route path="/alphaflow" element={<AlphaFlowPage/>}/>
            <Route path="/ambush" element={<AmbushPage/>}/>
            <Route path="/learning" element={<LearningPage/>}/>
            <Route path="/monitor" element={<MonitorPage/>}/>
            <Route path="/settings" element={<SettingsPage/>}/>
            <Route path="/sanxian" element={<SanxianPage/>}/>
          </Routes>
        </div>
      </div>
    </BrowserRouter>
    </ConfigProvider>
  );
}
