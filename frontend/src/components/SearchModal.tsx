import { useState, useEffect, useRef, useCallback } from 'react';
import { Search, X, Shield, BookOpen, ShoppingBag, ArrowRight, type LucideIcon } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

interface SearchMessage {
  id?: number;
  msg_id?: string;
  category: 'A' | 'B' | 'C';
  summary: string;
  group_name?: string;
  sender_name?: string;
  created_at?: string;
}

const CAT: Record<string, { label: string; color: string; icon: LucideIcon }> = {
  A: { label: '重要信息', color: '#BB00FF', icon: Shield },
  B: { label: '校园轶事', color: '#ADFF00', icon: BookOpen },
  C: { label: '二手资讯', color: '#FF5C00', icon: ShoppingBag },
};

export default function SearchModal({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const search = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return; }
    setLoading(true);
    try {
      const res = await fetch(`/api/messages?search=${encodeURIComponent(q)}&limit=20`);
      setResults(await res.json());
    } catch { /* Keep the previous results during a transient local API failure. */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => search(query), 300);
    return () => clearTimeout(timer);
  }, [query, search]);

  const goToMessage = () => {
    onClose();
    navigate('/messages');
  };

  return (
    <div className="modal-overlay" onClick={onClose} role="presentation">
      <div className="w-full max-w-2xl animate-scale-in" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="搜索消息">
        {/* 搜索框 */}
        <div className="glass rounded-2xl overflow-hidden">
          <div className="flex items-center gap-3 px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
            <Search size={20} style={{ color: 'var(--neon-cyan)' }} />
            <input ref={inputRef} value={query} onChange={e => setQuery(e.target.value)}
              placeholder="搜索消息、群组、发送者..."
              className="flex-1 bg-transparent outline-none text-base" style={{ color: 'var(--text-primary)' }} />
            <kbd className="text-xs px-2 py-0.5 rounded font-mono" style={{ background: 'rgba(255,255,255,0.06)', color: 'var(--text-dim)' }}>ESC</kbd>
            {query && (
              <button onClick={() => setQuery('')} aria-label="清空搜索" className="p-1 rounded hover:bg-white/[0.06]">
                <X size={16} style={{ color: 'var(--text-dim)' }} />
              </button>
            )}
          </div>

          {/* 搜索结果 */}
          <div className="max-h-[400px] overflow-y-auto">
            {!query ? (
              <div className="px-5 py-8 text-center">
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>输入关键词搜索</p>
                <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>支持搜索消息内容、群名、发送者</p>
              </div>
            ) : loading ? (
              <div className="px-5 py-8 text-center">
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>搜索中...</p>
              </div>
            ) : results.length === 0 ? (
              <div className="px-5 py-8 text-center">
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>未找到匹配结果</p>
              </div>
            ) : (
              <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
                {results.map((msg, i) => {
                  const c = CAT[msg.category] || CAT.A;
                  const Icon = c.icon;
                  return (
                    <button key={msg.id || i} onClick={goToMessage}
                      className="w-full text-left px-5 py-3 flex items-start gap-3 hover:bg-white/[0.03] cursor-pointer transition-colors">
                      <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
                           style={{ background: `${c.color}12` }}>
                        <Icon size={14} style={{ color: c.color }} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{msg.summary}</p>
                        <div className="flex items-center gap-2 text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>
                          <span>{msg.group_name}</span>
                          <span>{msg.sender_name}</span>
                          <span className="font-mono">{msg.created_at?.slice(5, 16)}</span>
                        </div>
                      </div>
                      <ArrowRight size={14} style={{ color: 'var(--text-dim)' }} className="mt-1.5 shrink-0" />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
