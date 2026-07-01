import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Zap, Loader2, AlertCircle } from 'lucide-react';

export default function RegisterPage() {
  const navigate = useNavigate();
  const [nickname, setNickname] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleRegister = async () => {
    if (!nickname.trim()) {
      setError('请输入一个昵称');
      return;
    }
    if (nickname.trim().length > 20) {
      setError('昵称不能超过 20 个字符');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const res = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ nickname: nickname.trim() }),
      });
      const data = await res.json();
      if (data.status === 'ok') {
        navigate('/login');
      } else {
        setError(data.detail || '注册失败');
      }
    } catch {
      setError('网络错误，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="h-screen overflow-y-auto flex items-start justify-center p-6 py-8"
         style={{ background: 'linear-gradient(180deg, #000000 0%, #050509 100%)' }}>
      {/* 背景光晕 */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-1/3 left-1/5 w-[500px] h-[500px] rounded-full opacity-[0.04]"
             style={{ background: 'radial-gradient(circle, #00F2FF, transparent)' }} />
      </div>

      <div className="relative w-full max-w-md animate-fade-in">
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
            设置你的昵称，开始使用
          </p>
        </div>

        {/* 注册卡片 */}
        <div className="glass rounded-2xl p-7">
          <div className="mb-6">
            <label htmlFor="local-nickname" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              你的昵称
            </label>
            <input
              id="local-nickname"
              type="text"
              value={nickname}
              onChange={e => { setNickname(e.target.value); setError(''); }}
              onKeyDown={e => { if (e.key === 'Enter') handleRegister(); }}
              placeholder="例如：张三、小明"
              maxLength={20}
              className="w-full px-3.5 py-2.5 rounded-lg text-sm outline-none transition-colors"
              style={{
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid var(--border)',
                color: 'var(--text-primary)',
              }}
              onFocus={e => e.target.style.borderColor = 'rgba(0, 242, 255, 0.3)'}
              onBlur={e => e.target.style.borderColor = 'var(--border)'}
              autoFocus
            />
          </div>

          {error && (
            <div className="mb-4 px-3 py-2 rounded-lg text-xs flex items-start gap-2"
                 style={{ background: 'rgba(255, 80, 80, 0.06)', border: '1px solid rgba(255, 80, 80, 0.15)', color: '#ff6b6b' }}>
              <AlertCircle size={13} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            onClick={handleRegister}
            disabled={!nickname.trim() || loading}
            className="w-full py-2.5 rounded-lg text-sm font-medium transition-all duration-200 flex items-center justify-center gap-2"
            style={{
              background: 'rgba(0, 242, 255, 0.15)',
              color: 'var(--neon-cyan)',
              border: '1px solid rgba(0, 242, 255, 0.3)',
              opacity: (!nickname.trim() || loading) ? 0.4 : 1,
              cursor: (!nickname.trim() || loading) ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? <><Loader2 size={14} className="animate-spin" /> 注册中</> : '开始使用'}
          </button>

          <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
               style={{ background: 'rgba(0, 242, 255, 0.04)', border: '1px solid rgba(0, 242, 255, 0.08)', color: 'var(--text-secondary)' }}>
            <AlertCircle size={13} className="mt-0.5 shrink-0" style={{ color: 'var(--neon-cyan)' }} />
            <span>昵称仅保存在这台电脑，用于界面显示；不会上传，也不会作为云端账号或数据隔离依据。</span>
          </div>
        </div>
      </div>
    </div>
  );
}
