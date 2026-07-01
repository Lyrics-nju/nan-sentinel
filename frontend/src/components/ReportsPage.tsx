import { useState, useEffect, useCallback } from 'react';
import { FileText, Calendar, ChevronRight, Loader2 } from 'lucide-react';

interface Summary {
  week_start: string;
  week_end: string;
  category: string | null;
  summary: string;
  created_at?: string;
  sources?: Array<{
    id: number;
    category: string;
    summary: string;
    group_name: string;
    sender_name: string;
    created_at: string;
    source_type?: string;
    source_name?: string;
  }>;
}

export default function ReportsPage() {
  const [summaries, setSummaries] = useState<Summary[]>([]);
  const [generating, setGenerating] = useState(false);
  const [selected, setSelected] = useState<Summary | null>(null);
  const [error, setError] = useState('');

  const fetchSummaries = useCallback(async () => {
    try {
      const res = await fetch('/api/weekly_summaries');
      const data = await res.json();
      if (Array.isArray(data)) {
        setSummaries(data);
      }
    } catch { /* Keep existing history when the local API is unavailable. */ }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data synchronization
    fetchSummaries();
  }, [fetchSummaries]);

  const generateSummary = async () => {
    setGenerating(true);
    setError('');
    try {
      const res = await fetch('/api/weekly_summary?refresh=true');
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || '周报生成失败');
      if (data && data.week_start) {
        setSelected(data);
        fetchSummaries();
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '周报生成失败');
    } finally {
      setGenerating(false);
    }
  };

  return (
    <div style={{ padding: '32px 40px 32px 32px' }} className="animate-fade-in">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>AI 周报</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>带原消息引用的每周重点摘要</p>
        </div>
        <button onClick={generateSummary} disabled={generating}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all hover:brightness-110 disabled:opacity-50"
          style={{ background: 'rgba(0,242,255,0.1)', color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.2)' }}>
          {generating ? <Loader2 size={16} className="animate-spin" /> : <FileText size={16} />}
          {generating ? '生成中...' : '生成本周周报'}
        </button>
      </div>

      {error && (
        <div className="mb-5 px-4 py-3 rounded-xl text-sm" style={{ background: 'rgba(255,80,80,0.08)', border: '1px solid rgba(255,80,80,0.2)', color: '#ff7b7b' }}>
          {error}。原有周报未被覆盖。
        </div>
      )}

      <div className="flex gap-6">
        {/* 左侧列表 */}
        <div className="w-72 shrink-0">
          <div className="glass rounded-2xl overflow-hidden">
            <div className="px-5 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
              <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>历史周报</span>
            </div>
            <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
              {summaries.length === 0 ? (
                <div className="px-5 py-12 text-center">
                  <Calendar size={32} style={{ color: 'var(--text-dim)' }} className="mx-auto mb-2" />
                  <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>暂无周报</p>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>点击上方按钮生成</p>
                </div>
              ) : summaries.map((s, i) => (
                <button key={i} onClick={() => setSelected(s)}
                  className={`w-full text-left px-5 py-3 flex items-center gap-3 transition-colors ${selected === s ? 'bg-white/[0.04]' : 'hover:bg-white/[0.02]'}`}>
                  <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                       style={{ background: 'rgba(0,242,255,0.08)' }}>
                    <FileText size={16} style={{ color: 'var(--neon-cyan)' }} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                      {s.category === 'A' ? '重要信息' : s.category === 'B' ? '校园轶事' : s.category === 'C' ? '二手资讯' : '综合'}
                    </p>
                    <p className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>{s.week_start} ~ {s.week_end}</p>
                  </div>
                  <ChevronRight size={14} style={{ color: 'var(--text-dim)' }} />
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* 右侧详情 */}
        <div className="flex-1 min-w-0">
          <div className="glass rounded-2xl overflow-hidden">
            {selected ? (
              <div className="p-8">
                <div className="flex items-center gap-3 mb-6">
                  <div className="w-12 h-12 rounded-xl flex items-center justify-center"
                       style={{ background: 'rgba(0,242,255,0.08)' }}>
                    <FileText size={24} style={{ color: 'var(--neon-cyan)' }} />
                  </div>
                  <div>
                    <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>
                      {selected.category === 'A' ? '重要信息' : selected.category === 'B' ? '校园轶事' : selected.category === 'C' ? '二手资讯' : '综合'} 周报
                    </h2>
                    <p className="text-sm font-mono" style={{ color: 'var(--text-dim)' }}>{selected.week_start} ~ {selected.week_end}</p>
                  </div>
                </div>
                <div className="prose prose-invert max-w-none">
                  <div className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>
                    {selected.summary}
                  </div>
                </div>
                {selected.sources && selected.sources.length > 0 && (
                  <div className="mt-7 pt-5" style={{ borderTop: '1px solid var(--border)' }}>
                    <h3 className="text-sm font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>引用依据</h3>
                    <div className="space-y-2">
                      {selected.sources.map(source => (
                        <div key={source.id} className="rounded-lg px-4 py-3" style={{ background: 'rgba(255,255,255,0.025)', border: '1px solid var(--border)' }}>
                          <div className="flex items-center gap-2 text-xs font-mono mb-1" style={{ color: 'var(--neon-cyan)' }}>
                            <span>[M{source.id}]</span>
                            <span style={{ color: 'var(--text-dim)' }}>{source.source_name || source.source_type || '本地消息'} · {source.group_name || '未知群聊'} · {source.created_at}</span>
                          </div>
                          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{source.summary}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="px-6 py-24 text-center">
                <FileText size={48} style={{ color: 'var(--text-dim)' }} className="mx-auto mb-3" />
                <p className="text-base" style={{ color: 'var(--text-secondary)' }}>选择一份周报查看详情</p>
                <p className="text-sm mt-1" style={{ color: 'var(--text-dim)' }}>或点击右上角生成新的周报</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
