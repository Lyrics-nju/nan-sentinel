import { useState, useEffect } from 'react';
import { X } from 'lucide-react';

export default function ProfileModal({ onClose, nickname }: { onClose: () => void; nickname?: string }) {
  const initial = (nickname || 'U').charAt(0).toUpperCase();
  const [stats, setStats] = useState({ today: 0, total: 0, bookmarks: 0 });

  useEffect(() => {
    fetch('/api/user/stats').then(r => r.json()).then(setStats).catch(() => {});
  }, []);

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="glass rounded-2xl w-full max-w-sm animate-scale-in" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="个人信息">
        {/* 头部 */}
        <div className="relative px-6 pt-6 pb-4">
          <button onClick={onClose} aria-label="关闭个人信息" className="absolute top-4 right-4 p-1.5 rounded-lg hover:bg-white/[0.06] transition-colors">
            <X size={18} style={{ color: 'var(--text-dim)' }} />
          </button>

          <div className="flex items-center gap-4">
            <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl font-bold"
                 style={{ background: 'linear-gradient(135deg, var(--neon-cyan) 0%, var(--neon-purple) 100%)', color: '#000' }}>
              {initial}
            </div>
            <div>
              <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>{nickname || '用户'}</h2>
              <p className="text-sm font-mono" style={{ color: 'var(--text-dim)' }}>在线</p>
            </div>
          </div>
        </div>

        {/* 统计 */}
        <div className="px-6 py-4" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="grid grid-cols-3 gap-4 text-center">
            <div>
              <p className="text-lg font-bold" style={{ color: 'var(--neon-cyan)' }}>{stats.today}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>今日消息</p>
            </div>
            <div>
              <p className="text-lg font-bold" style={{ color: 'var(--neon-purple)' }}>{stats.bookmarks}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>收藏数</p>
            </div>
            <div>
              <p className="text-lg font-bold" style={{ color: 'var(--neon-green)' }}>{stats.total}</p>
              <p className="text-xs" style={{ color: 'var(--text-dim)' }}>总消息</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
