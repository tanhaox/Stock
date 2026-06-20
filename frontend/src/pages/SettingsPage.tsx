import { useEffect, useState } from 'react';
import api from '../lib/api';

export default function SettingsPage() {
  const [tushareToken, setTushareToken] = useState('');
  const [deepseekKey, setDeepseekKey] = useState('');
  const [baiduKey, setBaiduKey] = useState('');
  const [tushareCookie, setTushareCookie] = useState('');
  const [status, setStatus] = useState<Record<string, boolean>>({});
  const [cookieValid, setCookieValid] = useState<boolean | null>(null);
  const [testingCookie, setTestingCookie] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get('/settings/').then(r => {
      setStatus(r.data.data || {});
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const testCookie = async () => {
    setTestingCookie(true);
    try {
      const r = await api.post('/settings/test-cookie');
      setCookieValid(r.data.valid);
    } catch { setCookieValid(false); }
    setTestingCookie(false);
  };

  const save = async () => {
    try {
      await api.put('/settings/', {
        tushare_token: tushareToken,
        deepseek_key: deepseekKey,
        baidu_key: baiduKey,
        tushare_cookie: tushareCookie,
      });
      alert('已保存');
      setTushareToken(''); setDeepseekKey(''); setBaiduKey(''); setTushareCookie('');
      const r = await api.get('/settings/');
      setStatus(r.data.data || {});
    } catch { alert('保存失败'); }
  };

  const fields = [
    { label: 'Tushare Token', value: tushareToken, set: setTushareToken, key: 'tushare_token_set', long: false },
    { label: 'DeepSeek API Key', value: deepseekKey, set: setDeepseekKey, key: 'deepseek_key_set', long: false },
    { label: '百度千帆 Key', value: baiduKey, set: setBaiduKey, key: 'baidu_key_set', long: false },
    { label: 'Tushare Cookie (新闻爬虫)', value: tushareCookie, set: setTushareCookie, key: 'tushare_cookie_set', long: true },
  ];

  if (loading) return <div style={{ padding: 24, color: '#6e7a8a' }}>加载配置中...</div>;

  return (
    <div><h2 style={{ marginBottom: 16 }}>系统设置</h2>
      <div style={{ maxWidth: 600 }}>
        {fields.map((f, i) => (
          <div key={i} style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 13, color: '#6e7a8a', display: 'block', marginBottom: 4 }}>
              {f.label}
              {f.key === 'tushare_cookie_set' ? (
                <span style={{ marginLeft: 8, fontSize: 10,
                  color: cookieValid === true ? '#10b981' : cookieValid === false ? '#ef4444' : status[f.key] ? '#10b981' : '#ef4444',
                  background: (cookieValid === true || (cookieValid === null && status[f.key])) ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                  padding: '1px 6px', borderRadius: 3 }}>
                  {cookieValid === true ? '已配置' : cookieValid === false ? '不可用' : status[f.key] ? '已配置 (未测试)' : '未配置'}
                </span>
              ) : (
                status[f.key] && <span style={{ marginLeft: 8, fontSize: 10, color: '#10b981', background: 'rgba(16,185,129,0.1)', padding: '1px 6px', borderRadius: 3 }}>已配置</span>
              )}
            </label>
            {f.long ? (
              <div>
                <textarea value={f.value} onChange={e => f.set(e.target.value)}
                  placeholder={status[f.key] ? '留空不修改' : '浏览器登录 tushare.pro 后复制 Cookie'}
                  rows={3}
                  style={{ width: '100%', padding: '10px', background: '#161b27', border: '1px solid #1e2535',
                    borderRadius: 8, color: '#c9d1d9', fontSize: 11, resize: 'vertical', fontFamily: 'monospace' }} />
                <button onClick={testCookie} disabled={testingCookie}
                  style={{ marginTop: 6, padding: '4px 12px', borderRadius: 4, border: '1px solid #f59e0b',
                    background: 'rgba(245,158,11,0.08)', color: '#f59e0b', cursor: testingCookie?'not-allowed':'pointer', fontSize: 11 }}>
                  {testingCookie ? '测试中...' : '测试连接'}
                </button>
              </div>
            ) : (
              <input type="password" value={f.value} onChange={e => f.set(e.target.value)}
                placeholder={status[f.key] ? '留空不修改' : '输入新密钥'}
                style={{ width: '100%', padding: '10px', background: '#161b27', border: '1px solid #1e2535',
                  borderRadius: 8, color: '#c9d1d9', fontSize: 13 }} />
            )}
          </div>
        ))}
        <button onClick={save} style={{ padding: '10px 28px', background: '#3b82f6', color: '#fff',
          border: 'none', borderRadius: 8, cursor: 'pointer', fontWeight: 600 }}>
          保存配置
        </button>
      </div>
    </div>
  );
}
