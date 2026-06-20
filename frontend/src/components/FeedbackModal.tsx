import { useState } from 'react';
import { Modal, Button, Input, Spin, Tag, Alert, Descriptions } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';
import api from '../lib/api';

interface Props { symbol: string; name: string; score: number; tradeDate: string; open: boolean; onClose: () => void; }

export default function FeedbackModal({ symbol, name, score, tradeDate, open, onClose }: Props) {
  const [rawText, setRawText] = useState('');
  const [parsing, setParsing] = useState(false);
  const [parsed, setParsed] = useState<any>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  const handleParse = async () => {
    if (!rawText.trim()) return;
    setParsing(true); setError('');
    try { const r = await api.post('/feedback/parse', { raw_text: rawText }); setParsed(r.data.data); if (r.data.data?.parse_error) setError(r.data.data.parse_error); }
    catch (e: any) { setError(e?.response?.data?.detail || '解析失败'); }
    finally { setParsing(false); }
  };

  const handleSubmit = async () => {
    if (!parsed) return;
    setSubmitting(true);
    try {
      await api.post('/feedback/submit', { ts_code: symbol, trade_date: tradeDate, raw_response: rawText, business_stage: parsed.business_stage, profit_quality: parsed.profit_quality, recurring_profit_pct: parsed.recurring_profit_pct, suggested_score: parsed.suggested_score, confidence_score: parsed.confidence_score, data_freshness: parsed.data_freshness, profit_attribution: parsed.profit_attribution||[], hidden_risks: parsed.hidden_risks||[], catalysts: parsed.catalysts||[], data_corrections: parsed.data_corrections||[], capability_gaps: parsed.capability_gaps||[], system_score_before: score });
      setDone(true); setTimeout(() => { onClose(); setDone(false); setRawText(''); setParsed(null); }, 1500);
    } catch (e: any) { setError(e?.response?.data?.detail || '提交失败'); }
    finally { setSubmitting(false); }
  };

  const handleClose = () => { setRawText(''); setParsed(null); setError(''); setDone(false); onClose(); };

  return <Modal title={<span>📋 外部分析反哺 — {symbol} {name}</span>} open={open} onCancel={handleClose} width={700} footer={<div style={{display:'flex',gap:8,justifyContent:'flex-end'}}><Button onClick={handleClose}>取消</Button>{!parsed&&<Button type="primary" onClick={handleParse} loading={parsing}>解析并预览</Button>}{parsed&&!done&&<Button type="primary" onClick={handleSubmit} loading={submitting}>确认提交</Button>}</div>} destroyOnClose>
    {done ? <Alert type="success" message="反馈已记录" showIcon/> :
     parsed ? <div>
      <Descriptions column={1} size="small" bordered style={{marginBottom:12}}>
        <Descriptions.Item label="业务阶段">{parsed.business_stage||'—'}</Descriptions.Item>
        <Descriptions.Item label="利润质量">{parsed.profit_quality||'—'}</Descriptions.Item>
        <Descriptions.Item label="建议评分">{parsed.suggested_score!=null?<Tag color={parsed.suggested_score>score?'green':parsed.suggested_score<score?'red':'blue'}>{parsed.suggested_score} (系统: {score})</Tag>:'—'}</Descriptions.Item>
        <Descriptions.Item label="置信度">{parsed.confidence_score!=null?`${(parsed.confidence_score*100).toFixed(0)}%`:'—'}</Descriptions.Item>
        <Descriptions.Item label="风险">{parsed.hidden_risks?.length>0?parsed.hidden_risks.map((r:any,i:number)=><Tag key={i} color="error">{r.label}</Tag>):'—'}</Descriptions.Item>
        <Descriptions.Item label="催化剂">{parsed.catalysts?.length>0?parsed.catalysts.map((c:any,i:number)=><Tag key={i} color="success">{c.label}</Tag>):'—'}</Descriptions.Item>
        <Descriptions.Item label="数据纠错">{parsed.data_corrections?.length>0?parsed.data_corrections.map((c:string,i:number)=><div key={i}>{c}</div>):'—'}</Descriptions.Item>
      </Descriptions>
      {error && <Alert type="warning" message={error} showIcon style={{marginTop:8}}/>}
    </div> :
     <div>
      <div style={{marginBottom:8,fontSize:12,color:'#6b7280'}}>将 DeepSeek 网页版对该股票的分析文本粘贴到下方</div>
      <Input.TextArea value={rawText} onChange={e=>setRawText(e.target.value)} placeholder="粘贴 DeepSeek 分析文本..." rows={12} style={{fontSize:13}}/>
      {parsing && <div style={{textAlign:'center',padding:24}}><Spin indicator={<LoadingOutlined style={{fontSize:24}} spin/>}/><div style={{marginTop:12,color:'#6b7280',fontSize:13}}>DeepSeek API 解析中...</div></div>}
      {error && !parsed && <Alert type="error" message={error} showIcon style={{marginTop:8}}/>}
    </div>}
  </Modal>;
}
