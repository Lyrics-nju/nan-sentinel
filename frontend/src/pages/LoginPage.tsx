import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Key, Zap, QrCode, CheckCircle, Loader2, AlertCircle, Monitor, Tablet, Smartphone } from 'lucide-react';

interface LoginStatus {
  status: 'idle' | 'waiting_scan' | 'scanned' | 'logged_in' | 'offline';
  nickname?: string;
  uin?: string;
  ws_config_fixed?: boolean;
  warning?: string;
}

type Platform = 'Android' | 'iPad' | 'Windows';

const PLATFORMS: { key: Platform; label: string; icon: typeof Monitor; desc: string }[] = [
  { key: 'Windows', label: 'Windows', icon: Monitor, desc: '挤掉电脑端' },
  { key: 'iPad', label: 'iPad', icon: Tablet, desc: '可与电脑同时在线' },
  { key: 'Android', label: 'Android', icon: Smartphone, desc: '可与电脑同时在线' },
];

function getSavedPlatform(): Platform {
  try { return (localStorage.getItem('loginPlatform') as Platform) || 'iPad'; } catch { return 'iPad'; }
}

export default function LoginPage() {
  const navigate = useNavigate();

  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1');
  const [model, setModel] = useState('gpt-4o-mini');
  const [keyConfigured, setKeyConfigured] = useState(false);
  const [saving, setSaving] = useState(false);
  const [configSaved, setConfigSaved] = useState(false);
  const [configError, setConfigError] = useState('');

  const [qrCode, setQrCode] = useState<string | null>(null);
  const [qrFormat, setQrFormat] = useState<'png' | 'svg' | 'url'>('url');
  const [qrLoading, setQrLoading] = useState(false);
  const [loginStatus, setLoginStatus] = useState<LoginStatus>({ status: 'idle' });
  const [polling, setPolling] = useState(false);
  const [qrRefreshKey, setQrRefreshKey] = useState(0);

  const [platform, setPlatform] = useState<Platform>(getSavedPlatform);

  const prevStatusRef = useRef<LoginStatus['status']>('idle');

  const handleSaveConfig = useCallback(async () => {
    if (!baseUrl.trim() || !model.trim()) {
      setConfigError('请填写有效的 Base URL 和模型名称');
      return;
    }
    setSaving(true);
    setConfigError('');
    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          llm_api_key: apiKey.trim() || undefined,
          llm_base_url: baseUrl,
          llm_model: model,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '配置保存失败');
      if (apiKey.trim()) setKeyConfigured(true);
      setApiKey('');
      setConfigSaved(true);
    } catch (error) {
      setConfigError(error instanceof Error ? error.message : '配置保存失败');
    } finally {
      setSaving(false);
    }
  }, [apiKey, baseUrl, model]);

  const fetchQrCode = useCallback(async () => {
    setQrLoading(true);
    try {
      const pngRes = await fetch(`/api/qrcode?t=${Date.now()}`);
      if (pngRes.status === 503) {
        setQrLoading(false);
        return;
      }
      if (pngRes.ok) {
        setQrCode(prev => {
          if (prev && prev.startsWith('blob:')) URL.revokeObjectURL(prev);
          return prev;
        });
        const blob = await pngRes.blob();
        const url = URL.createObjectURL(blob);
        setQrCode(url);
        setQrFormat('url');
        setPolling(true);
        setQrRefreshKey(k => k + 1);
        setQrLoading(false);
        return;
      }
    } catch { /* Fall through to the JSON/SVG QR endpoint. */ }
    try {
      const res = await fetch('/api/login/qrcode');
      const data = await res.json();
      if (data.qrcode) {
        setQrCode(data.qrcode);
        setQrFormat(data.format || 'svg');
        setPolling(true);
        setQrRefreshKey(k => k + 1);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setQrLoading(false);
    }
  }, []);

  // 登录状态轮询
  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: number | undefined;
    const checkStatus = async () => {
      try {
        const res = await fetch('/api/login/status');
        const data: LoginStatus = await res.json();
        if (cancelled) return;
        setLoginStatus(data);
        if (data.status === 'logged_in') {
          setPolling(false);
          return;
        }
      } catch (e) {
        console.error(e);
      }
      if (!cancelled) timer = window.setTimeout(checkStatus, 2000);
    };
    void checkStatus();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [polling]);

  // 检测扫码失败/取消 → 自动刷新二维码
  useEffect(() => {
    const prev = prevStatusRef.current;
    const curr = loginStatus.status;
    prevStatusRef.current = curr;

    if (prev === 'scanned' && curr === 'waiting_scan') {
      fetchQrCode();
      return;
    }

    if (curr === 'waiting_scan' && !qrCode) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- QR state is synchronized from the local login service
      fetchQrCode();
    }
  }, [loginStatus.status, qrCode, fetchQrCode]);

  // 登录成功跳转（ws_config_fixed 时不跳转，等重启）
  useEffect(() => {
    if (loginStatus.status === 'logged_in' && !loginStatus.ws_config_fixed) {
      const t = setTimeout(() => navigate('/'), 1200);
      return () => clearTimeout(t);
    }
  }, [loginStatus.status, loginStatus.ws_config_fixed, navigate]);

  // 初始化：加载配置 + 获取 QR
  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(data => {
      if (data.llm_base_url) setBaseUrl(data.llm_base_url);
      if (data.llm_model) setModel(data.llm_model);
      if (data.configured) setConfigSaved(true);
      setKeyConfigured(!!data.llm_api_key_set);
    }).catch(() => {});
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial local-service synchronization
    fetchQrCode();
  }, [fetchQrCode]);

  // QR 定时刷新：每 10 秒换一个新码
  useEffect(() => {
    if (loginStatus.status === 'logged_in' || loginStatus.status === 'offline') return;
    const iv = setInterval(() => fetchQrCode(), 10000);
    return () => clearInterval(iv);
  }, [loginStatus.status, fetchQrCode]);

  const statusText = () => {
    if (loginStatus.status === 'logged_in' && loginStatus.ws_config_fixed) {
      return <span style={{ color: 'var(--neon-orange)' }} className="flex items-center gap-2 justify-center">
        <AlertCircle size={14} /> 登录成功，但消息通道未就绪
      </span>;
    }
    switch (loginStatus.status) {
      case 'idle':        return <span style={{ color: 'var(--text-secondary)' }}>正在获取二维码...</span>;
      case 'waiting_scan':return <span style={{ color: 'var(--neon-cyan)' }}>请使用 QQ 扫描二维码</span>;
      case 'scanned':     return <span style={{ color: 'var(--neon-green)' }}>已扫码，请在手机上确认</span>;
      case 'logged_in':   return <span style={{ color: 'var(--neon-cyan)' }} className="flex items-center gap-2 justify-center"><CheckCircle size={14} /> 登录成功 — {loginStatus.nickname}</span>;
      case 'offline':     return <span style={{ color: 'var(--neon-orange)' }}>等待连接 NapCat 引擎...</span>;
    }
  };

  const inputStyle: React.CSSProperties = {
    background: 'rgba(255,255,255,0.04)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
  };

  return (
    <div className="h-screen overflow-y-auto flex items-start justify-center p-6 py-8"
         style={{ background: 'linear-gradient(180deg, #000000 0%, #050509 100%)' }}>

      {/* 背景光晕 */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-1/3 left-1/5 w-[500px] h-[500px] rounded-full opacity-[0.04]"
             style={{ background: 'radial-gradient(circle, #00F2FF, transparent)' }} />
        <div className="absolute bottom-1/4 right-1/5 w-[400px] h-[400px] rounded-full opacity-[0.03]"
             style={{ background: 'radial-gradient(circle, #BB00FF, transparent)' }} />
      </div>

      <div className="relative w-full max-w-5xl animate-fade-in">

        {/* 标题 */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-3 mb-3">
            <div className="w-9 h-9 rounded-lg flex items-center justify-center"
                 style={{ background: 'rgba(0, 242, 255, 0.1)', border: '1px solid rgba(0, 242, 255, 0.2)' }}>
              <Zap size={18} style={{ color: 'var(--neon-cyan)' }} />
            </div>
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
              AI 学生消息助手
            </h1>
          </div>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            配置整理方式 · 扫码连接 QQ · 开始减少群消息负担
          </p>
        </div>

        {/* 双栏 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* 左栏：配置 */}
          <div className="glass rounded-2xl p-7">
            <div className="flex items-center gap-2.5 mb-6">
              <div className="w-7 h-7 rounded-md flex items-center justify-center"
                   style={{ background: 'rgba(0, 242, 255, 0.1)' }}>
                <Key size={14} style={{ color: 'var(--neon-cyan)' }} />
              </div>
              <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
                LLM 接入配置
              </h2>
            </div>

            <div className="mb-4">
              <label htmlFor="login-api-key" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>API Key</label>
              <input id="login-api-key" type="password" value={apiKey} onChange={e => { setApiKey(e.target.value); setConfigSaved(false); }}
                placeholder={keyConfigured ? '已安全保存，留空表示不修改' : 'sk-xxxxxxxx'} className="w-full px-3.5 py-2.5 rounded-lg text-sm outline-none transition-colors"
                style={inputStyle}
                onFocus={e => e.target.style.borderColor = 'rgba(0, 242, 255, 0.3)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'} />
            </div>

            <div className="mb-4">
              <label htmlFor="login-base-url" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>Base URL</label>
              <input id="login-base-url" type="url" value={baseUrl} onChange={e => { setBaseUrl(e.target.value); setConfigSaved(false); }}
                placeholder="https://api.openai.com/v1" className="w-full px-3.5 py-2.5 rounded-lg text-sm outline-none transition-colors"
                style={inputStyle}
                onFocus={e => e.target.style.borderColor = 'rgba(0, 242, 255, 0.3)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'} />
            </div>

            <div className="mb-5">
              <label htmlFor="login-model" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>Model</label>
              <input id="login-model" type="text" value={model} onChange={e => { setModel(e.target.value); setConfigSaved(false); }}
                placeholder="gpt-4o-mini" className="w-full px-3.5 py-2.5 rounded-lg text-sm outline-none transition-colors"
                style={inputStyle}
                onFocus={e => e.target.style.borderColor = 'rgba(0, 242, 255, 0.3)'}
                onBlur={e => e.target.style.borderColor = 'var(--border)'} />
            </div>

            {configError && (
              <div role="alert" className="mb-4 px-3 py-2 rounded-lg text-xs" style={{ color: '#ff8080', background: 'rgba(255,80,80,0.08)' }}>
                {configError}
              </div>
            )}

            <button onClick={handleSaveConfig} disabled={!baseUrl.trim() || !model.trim() || saving}
              className="w-full py-2.5 rounded-lg text-sm font-medium transition-all duration-200 flex items-center justify-center gap-2"
              style={{
                background: configSaved ? 'rgba(0, 242, 255, 0.08)' : 'rgba(0, 242, 255, 0.15)',
                color: 'var(--neon-cyan)',
                border: `1px solid ${configSaved ? 'rgba(0,242,255,0.2)' : 'rgba(0,242,255,0.3)'}`,
                opacity: (!baseUrl.trim() || !model.trim() || saving) ? 0.4 : 1,
                cursor: (!baseUrl.trim() || !model.trim() || saving) ? 'not-allowed' : 'pointer',
              }}>
              {saving ? <><Loader2 size={14} className="animate-spin" /> 保存中</>
               : configSaved ? <><CheckCircle size={14} /> 已保存</>
               : '保存配置'}
            </button>

            <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
                 style={{ background: 'rgba(0, 242, 255, 0.04)', border: '1px solid rgba(0, 242, 255, 0.08)', color: 'var(--text-secondary)' }}>
              <AlertCircle size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--neon-cyan)' }} />
              <span>本地优先 — Key 只保存在当前电脑，不会上传到我们的服务器。兼容 OpenAI 格式接口；留空时使用规则引擎。</span>
            </div>
          </div>

          {/* 右栏：QQ 扫码登录 */}
          <div className="glass rounded-2xl p-7 flex flex-col">
            <div className="flex items-center gap-2.5 mb-4">
              <div className="w-7 h-7 rounded-md flex items-center justify-center"
                   style={{ background: 'rgba(0, 242, 255, 0.1)' }}>
                <QrCode size={14} style={{ color: 'var(--neon-cyan)' }} />
              </div>
              <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
                QQ 登录
              </h2>
            </div>

            {/* 登录平台选择 */}
            <div className="mb-4">
              <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>登录平台</p>
              <div className="grid grid-cols-3 gap-2">
                {PLATFORMS.map(p => {
                  const Icon = p.icon;
                  const active = platform === p.key;
                  return (
                    <button key={p.key}
                      onClick={() => {
                        setPlatform(p.key);
                        try { localStorage.setItem('loginPlatform', p.key); } catch { /* restricted webview */ }
                        fetch('/api/login/platform', {
                          method: 'POST',
                          headers: { 'Content-Type': 'application/json' },
                          body: JSON.stringify({ platform: p.key }),
                        }).catch(() => {});
                      }}
                      className="flex flex-col items-center gap-1 py-2 rounded-lg transition-all text-xs"
                      style={{
                        background: active ? 'rgba(0,242,255,0.08)' : 'transparent',
                        border: active ? '1px solid rgba(0,242,255,0.25)' : '1px solid var(--border)',
                        color: active ? 'var(--neon-cyan)' : 'var(--text-dim)',
                      }}>
                      <Icon size={14} />
                      <span>{p.label}</span>
                    </button>
                  );
                })}
              </div>
              <p className="text-[10px] mt-1.5 text-center" style={{ color: 'var(--text-dim)' }}>
                {platform === 'Windows' ? '⚠ 会挤掉电脑端 QQ，不推荐' : '✓ 可与电脑端 QQ 同时在线'}
              </p>
            </div>

            {/* 扫码登录面板 */}
            <div className="flex-1 flex flex-col">
                <div className="flex-1 flex items-center justify-center">
                  {qrLoading ? (
                    <div className="w-56 h-56 rounded-xl flex items-center justify-center" style={{ background: 'rgba(255,255,255,0.02)' }}>
                      <Loader2 size={28} className="animate-spin" style={{ color: 'var(--neon-cyan)' }} />
                    </div>
                  ) : qrCode ? (
                    <div className="relative">
                      <div className="w-56 h-56 rounded-xl overflow-hidden p-3 flex items-center justify-center animate-pulse-glow-cyan"
                           style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(0, 242, 255, 0.1)' }}>
                        <img
                          key={qrRefreshKey}
                          src={qrFormat === 'url' ? qrCode : qrFormat === 'png' ? `data:image/png;base64,${qrCode}` : `data:image/svg+xml;base64,${qrCode}`}
                          alt="QR" className="w-full h-full object-contain" />
                      </div>
                      {loginStatus.status === 'logged_in' && (
                        <div className="absolute inset-0 rounded-xl flex items-center justify-center"
                             style={{ background: 'rgba(0, 0, 0, 0.88)' }}>
                          <div className="text-center animate-fade-in">
                            <CheckCircle size={40} style={{ color: 'var(--neon-cyan)' }} className="mx-auto mb-2" />
                            <p className="text-sm font-medium" style={{ color: 'var(--neon-cyan)' }}>登录成功</p>
                            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>{loginStatus.nickname}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="w-56 h-56 rounded-xl flex flex-col items-center justify-center gap-2"
                         style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)' }}>
                      <QrCode size={36} style={{ color: 'var(--text-dim)' }} />
                      <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>二维码加载失败</p>
                      <button onClick={fetchQrCode} className="text-xs px-3 py-1 rounded-md"
                              style={{ background: 'rgba(0,242,255,0.08)', color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.15)' }}>
                        重试
                      </button>
                    </div>
                  )}
                </div>

                {loginStatus.status !== 'idle' && loginStatus.status !== 'logged_in' && loginStatus.status !== 'offline' && (
                  <button onClick={() => { fetch('/api/login/reset', { method: 'POST' }).then(() => { setLoginStatus({ status: 'idle' }); fetchQrCode(); }); }}
                    className="mt-2 text-xs py-1.5" style={{ color: 'var(--text-secondary)' }}>
                    重新获取
                  </button>
                )}
              <div className="mt-3 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
                   style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                <AlertCircle size={13} className="mt-0.5 shrink-0" />
                <span>仅支持扫码授权，本应用不会要求或保存你的 QQ 密码。</span>
              </div>
            </div>

            {/* 状态文本 */}
            <div className="mt-4 text-center text-xs py-2.5 rounded-lg" style={{ background: 'rgba(255,255,255,0.02)' }}>
              {statusText()}
            </div>
          </div>
        </div>

        {/* 底部 */}
        <div className="mt-8 text-center">
          {loginStatus.status === 'logged_in' && loginStatus.ws_config_fixed ? (
            <div className="animate-fade-in">
              <div className="inline-flex items-center gap-2 px-5 py-3 rounded-lg mb-3"
                   style={{ background: 'rgba(255, 165, 0, 0.06)', border: '1px solid rgba(255, 165, 0, 0.15)' }}>
                <Loader2 size={14} className="animate-spin" style={{ color: 'var(--neon-orange)' }} />
                <span className="text-sm" style={{ color: 'var(--neon-orange)' }}>
                  消息通道正在配置中，NapCat 正在自动重启…
                </span>
              </div>
              <p className="text-xs mt-2" style={{ color: 'var(--text-secondary)' }}>
                请等待约 10 秒后重新扫码登录，届时消息通道将自动就绪
              </p>
            </div>
          ) : loginStatus.status === 'logged_in' ? (
            <div className="animate-fade-in inline-flex items-center gap-2 px-5 py-2.5 rounded-lg"
                 style={{ background: 'rgba(0, 242, 255, 0.06)', border: '1px solid rgba(0, 242, 255, 0.15)', color: 'var(--neon-cyan)' }}>
              <Loader2 size={14} className="animate-spin" />
              <span className="text-sm">正在进入消息助手…</span>
            </div>
          ) : (
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {!configSaved ? '可选：填写 API Key 配置 LLM' : '请完成 QQ 登录'}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
