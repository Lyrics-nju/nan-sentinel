import { useState, useEffect, useRef } from 'react';
import { Bell, X, Shield, BookOpen, ShoppingBag, type LucideIcon } from 'lucide-react';

interface NotificationMessage {
  category: 'A' | 'B' | 'C';
  summary: string;
  group_name: string;
  sender_name: string;
}

const CAT: Record<string, { label: string; color: string; icon: LucideIcon }> = {
  A: { label: '重要信息', color: '#BB00FF', icon: Shield },
  B: { label: '校园轶事', color: '#ADFF00', icon: BookOpen },
  C: { label: '二手资讯', color: '#FF5C00', icon: ShoppingBag },
};

function readEnabledCategories(): Set<string> {
  try {
    const saved = JSON.parse(localStorage.getItem('notificationCategories') || '[]') as string[];
    return new Set(saved.length ? saved : ['A']);
  } catch {
    return new Set(['A']);
  }
}

export default function NotificationBell() {
  const [count, setCount] = useState(0);
  const [notifications, setNotifications] = useState<NotificationMessage[]>([]);
  const [open, setOpen] = useState(false);
  const enabledRef = useRef(readEnabledCategories());
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const syncPreferences = () => { enabledRef.current = readEnabledCategories(); };
    window.addEventListener('notification-preferences-changed', syncPreferences);
    const es = new EventSource('/api/stream');
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data);
        if (evt.type === 'new_message' && enabledRef.current.has(evt.data.category)) {
          setNotifications(prev => [evt.data, ...prev].slice(0, 20));
          setCount(prev => prev + 1);
        }
      } catch { /* ignore malformed SSE events */ }
    };
    return () => {
      es.close();
      window.removeEventListener('notification-preferences-changed', syncPreferences);
    };
  }, []);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  return (
    <div ref={ref} className="relative">
      <button onClick={() => { setOpen(!open); if (!open) setCount(0); }}
        className="relative p-2 rounded-xl hover:bg-white/[0.06] transition-colors" aria-label="打开应用内提醒">
        <Bell size={20} style={{ color: 'var(--text-secondary)' }} />
        {count > 0 && (
          <span className="absolute -top-0.5 -right-0.5 w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center"
                style={{ background: 'var(--neon-orange)', color: '#000' }}>
            {count > 9 ? '9+' : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-80 glass rounded-2xl overflow-hidden animate-scale-in z-50">
          <div className="px-4 py-3 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>通知</span>
            <button onClick={() => setOpen(false)} className="p-1 rounded hover:bg-white/[0.06]" aria-label="关闭提醒">
              <X size={14} style={{ color: 'var(--text-dim)' }} />
            </button>
          </div>
          <div className="max-h-[300px] overflow-y-auto divide-y" style={{ borderColor: 'var(--border)' }}>
            {notifications.length === 0 ? (
              <div className="px-4 py-8 text-center">
                <Bell size={24} style={{ color: 'var(--text-dim)' }} className="mx-auto mb-2" />
                <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>暂无新通知</p>
              </div>
            ) : notifications.map((msg, i) => {
              const c = CAT[msg.category] || CAT.A;
              const Icon = c.icon;
              return (
                <div key={i} className="px-4 py-3 flex items-start gap-2.5 hover:bg-white/[0.02] transition-colors">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
                       style={{ background: `${c.color}12` }}>
                    <Icon size={13} style={{ color: c.color }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{msg.summary}</p>
                    <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-dim)' }}>{msg.group_name} · {msg.sender_name}</p>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
