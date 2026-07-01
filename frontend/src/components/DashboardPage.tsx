import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, ArrowRight, BookOpen, BrainCircuit, CheckCircle2, Clock3,
  MessageSquare, Radio, Shield, ShoppingBag, Sparkles, Wifi, WifiOff,
} from 'lucide-react';

type Category = 'A' | 'B' | 'C';
interface Stats {
  total: number; A: number; B: number; C: number;
  reviewed?: number; review_accuracy?: number | null; pending_review?: number;
}
interface DashboardMessage {
  id?: number; msg_id?: string; category: Category; summary: string;
  group_name?: string; sender_name?: string; created_at?: string;
  confidence?: number | null; review_required?: boolean;
}
interface Services { napcat_webui?: boolean; napcat_ws?: boolean; llm_configured?: boolean; llm_model?: string; }
interface CalibrationSummary {
  counts?: { active?: number; gold?: number; reviewed?: number };
  strategy?: { active_prompt_version?: string };
  latest_evaluation?: { baseline_accuracy?: number | null; calibrated_accuracy?: number | null; sample_count?: number } | null;
}

const CAT = {
  A: { label: '重要信息', color: '#BB00FF', icon: Shield },
  B: { label: '校园轶事', color: '#ADFF00', icon: BookOpen },
  C: { label: '二手资讯', color: '#FF5C00', icon: ShoppingBag },
};

function timeLabel(value?: string) {
  if (!value) return '刚刚';
  const date = new Date(value.replace(' ', 'T'));
  if (Number.isNaN(date.getTime())) return value.slice(5, 16);
  const today = new Date();
  return date.toDateString() === today.toDateString()
    ? `今天 ${value.slice(11, 16)}`
    : value.slice(5, 16);
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats>({ total: 0, A: 0, B: 0, C: 0 });
  const [connected, setConnected] = useState(false);
  const [recentMsgs, setRecentMsgs] = useState<DashboardMessage[]>([]);
  const [services, setServices] = useState<Services>({});
  const [calibration, setCalibration] = useState<CalibrationSummary>({});

  const refresh = useCallback(async () => {
    const [statsResult, messagesResult, servicesResult, calibrationResult] = await Promise.allSettled([
      fetch('/api/stats').then(response => response.json()),
      fetch('/api/messages?limit=12').then(response => response.json()),
      fetch('/api/services').then(response => response.json()),
      fetch('/api/calibration').then(response => response.json()),
    ]);
    if (statsResult.status === 'fulfilled') setStats(statsResult.value);
    if (messagesResult.status === 'fulfilled') setRecentMsgs(messagesResult.value);
    if (servicesResult.status === 'fulfilled') setServices(servicesResult.value);
    if (calibrationResult.status === 'fulfilled') setCalibration(calibrationResult.value);
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data synchronization
    void refresh();
  }, [refresh]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      fetch('/api/services').then(response => response.json()).then(setServices).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    const stream = new EventSource('/api/stream');
    stream.onopen = () => setConnected(true);
    stream.onerror = () => setConnected(false);
    stream.onmessage = event => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === 'new_message') {
          const message = payload.data as DashboardMessage;
          setRecentMsgs(previous => [message, ...previous].slice(0, 12));
          void refresh();
        }
      } catch { /* malformed frames are ignored */ }
    };
    return () => stream.close();
  }, [refresh]);

  const priority = useMemo(
    () => recentMsgs.filter(message => message.category === 'A' || message.review_required).slice(0, 5),
    [recentMsgs],
  );
  const remaining = useMemo(
    () => recentMsgs.filter(message => !priority.includes(message)).slice(0, 6),
    [recentMsgs, priority],
  );

  return (
    <div className="page-shell animate-fade-in">
      <header className="page-header">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>今日「情报」</h1>
            <Sparkles size={17} style={{ color: 'var(--neon-cyan)' }} aria-hidden="true" />
          </div>
          <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>不是秘密行动，只是把今天真正要处理的校园消息排好顺序。</p>
        </div>
        <div className="flex items-center gap-2 px-3 py-2 rounded-xl self-start" style={{ background: connected ? 'rgba(0,242,255,.06)' : 'rgba(255,92,0,.06)' }}>
          {connected ? <Wifi size={15} style={{ color: 'var(--neon-cyan)' }} /> : <WifiOff size={15} style={{ color: 'var(--neon-orange)' }} />}
          <span className="text-xs font-mono" style={{ color: connected ? 'var(--neon-cyan)' : 'var(--neon-orange)' }}>{connected ? 'LIVE' : 'OFFLINE'}</span>
        </div>
      </header>

      <div className="dashboard-layout">
        <div className="space-y-5 min-w-0">
          <section className="glass rounded-2xl overflow-hidden">
            <div className="px-5 py-4 flex flex-wrap items-center gap-3" style={{ borderBottom: '1px solid var(--border)', background: 'rgba(187,0,255,.035)' }}>
              <span className="w-10 h-10 rounded-xl flex items-center justify-center" style={{ background: 'rgba(187,0,255,.1)' }}><Shield size={19} style={{ color: 'var(--neon-purple)' }} /></span>
              <div>
                <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>现在值得处理</h2>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>重要通知与需要人工确认的低置信度判断</p>
              </div>
              <span className="ml-auto font-mono text-sm" style={{ color: 'var(--neon-purple)' }}>{priority.length} 条</span>
            </div>
            {priority.length ? (
              <div>
                {priority.map((message, index) => {
                  const category = CAT[message.category] || CAT.A;
                  return (
                    <Link key={message.msg_id || index} to="/messages" className="flex items-start gap-4 px-5 py-4 transition-colors hover:bg-white/[.025]" style={{ borderTop: index ? '1px solid var(--border)' : 0 }}>
                      <span className="mt-1 w-2 h-2 rounded-full shrink-0" style={{ background: message.review_required ? 'var(--neon-orange)' : category.color }} />
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{message.summary}</span>
                        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-xs" style={{ color: 'var(--text-dim)' }}>
                          <span>{message.group_name || '本地来源'}</span><span>{timeLabel(message.created_at)}</span>
                          {typeof message.confidence === 'number' && <span className="font-mono">{Math.round(message.confidence * 100)}%</span>}
                        </span>
                      </span>
                      <span className="text-xs shrink-0" style={{ color: message.review_required ? 'var(--neon-orange)' : category.color }}>{message.review_required ? '待确认' : category.label}</span>
                    </Link>
                  );
                })}
              </div>
            ) : (
              <div className="px-6 py-12 text-center">
                <CheckCircle2 size={30} className="mx-auto mb-3" style={{ color: 'var(--neon-green)' }} />
                <p className="text-sm" style={{ color: 'var(--text-primary)' }}>现在没有必须处理的事项</p>
                <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>新的重要通知或待确认判断会优先出现在这里</p>
              </div>
            )}
          </section>

          <section className="rounded-2xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
            <div className="px-5 py-3.5 flex items-center gap-2" style={{ background: 'rgba(255,255,255,.018)' }}>
              <Activity size={16} style={{ color: 'var(--neon-cyan)' }} />
              <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>随后可以看看</h2>
              <Link to="/messages" className="ml-auto inline-flex items-center gap-1 text-xs" style={{ color: 'var(--neon-cyan)' }}>全部消息 <ArrowRight size={13} /></Link>
            </div>
            {remaining.length ? remaining.map((message, index) => {
              const category = CAT[message.category] || CAT.B;
              return (
                <div key={message.msg_id || index} className="px-5 py-3 flex items-center gap-3" style={{ borderTop: '1px solid var(--border)' }}>
                  <category.icon size={15} style={{ color: category.color }} />
                  <span className="text-sm truncate min-w-0 flex-1" style={{ color: 'var(--text-secondary)' }}>{message.summary}</span>
                  <span className="text-xs font-mono shrink-0" style={{ color: 'var(--text-dim)' }}>{timeLabel(message.created_at)}</span>
                </div>
              );
            }) : <p className="px-5 py-8 text-sm text-center" style={{ color: 'var(--text-dim)' }}>其余消息会按时间出现在这里</p>}
          </section>
        </div>

        <aside className="space-y-5 min-w-0">
          <section className="glass rounded-2xl p-5">
            <div className="flex items-center gap-3 mb-4">
              <BrainCircuit size={19} style={{ color: 'var(--neon-cyan)' }} />
              <div><h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>本地校准</h2><p className="text-xs" style={{ color: 'var(--text-dim)' }}>与用户一起成长，但不训练本地模型</p></div>
            </div>
            <div className="space-y-3 text-xs">
              <div className="flex justify-between"><span style={{ color: 'var(--text-secondary)' }}>有效复核案例</span><span className="font-mono" style={{ color: 'var(--neon-cyan)' }}>{calibration.counts?.active || 0}</span></div>
              <div className="flex justify-between"><span style={{ color: 'var(--text-secondary)' }}>金标准样本</span><span className="font-mono" style={{ color: 'var(--neon-green)' }}>{calibration.counts?.gold || 0}</span></div>
              <div className="flex justify-between"><span style={{ color: 'var(--text-secondary)' }}>低置信度待确认</span><span className="font-mono" style={{ color: 'var(--neon-orange)' }}>{stats.pending_review || 0}</span></div>
            </div>
            {calibration.latest_evaluation?.sample_count ? (
              <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
                <p className="text-xs" style={{ color: 'var(--text-dim)' }}>最近金标准回放</p>
                <p className="mt-1 font-mono text-sm"><span style={{ color: 'var(--text-secondary)' }}>{calibration.latest_evaluation.baseline_accuracy ?? '--'}%</span><span className="mx-2" style={{ color: 'var(--text-dim)' }}>→</span><span style={{ color: 'var(--neon-green)' }}>{calibration.latest_evaluation.calibrated_accuracy ?? '--'}%</span></p>
              </div>
            ) : <p className="mt-4 pt-4 text-xs" style={{ color: 'var(--text-dim)', borderTop: '1px solid var(--border)' }}>把复核案例设为金标准后，就能比较校准前后表现。</p>}
            <Link to="/settings?tab=calibration" className="mt-4 w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,.2)' }}>打开校准中心 <ArrowRight size={13} /></Link>
          </section>

          <section className="rounded-2xl p-5" style={{ border: '1px solid var(--border)' }}>
            <div className="flex items-center gap-2 mb-4"><MessageSquare size={16} style={{ color: 'var(--neon-cyan)' }} /><h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>本机收件概况</h2><span className="ml-auto text-xs font-mono" style={{ color: 'var(--text-dim)' }}>{stats.total}</span></div>
            <div className="space-y-3">{(['A', 'B', 'C'] as Category[]).map(key => { const item = CAT[key]; const count = stats[key]; const percent = stats.total ? Math.round(count / stats.total * 100) : 0; return <div key={key}><div className="flex justify-between text-xs mb-1.5"><span style={{ color: 'var(--text-secondary)' }}>{item.label}</span><span className="font-mono" style={{ color: item.color }}>{count} · {percent}%</span></div><div className="h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,.05)' }}><div className="h-full" style={{ width: `${percent}%`, background: item.color }} /></div></div>; })}</div>
          </section>

          <section className="rounded-xl px-4 py-3 text-xs space-y-2" style={{ border: '1px solid var(--border)', color: 'var(--text-dim)' }}>
            <div className="flex items-center gap-2"><Radio size={13} style={{ color: services.napcat_ws ? 'var(--neon-green)' : 'var(--neon-orange)' }} /><span>消息通道 {services.napcat_ws ? '已连接' : '等待连接'}</span></div>
            <div className="flex items-center gap-2"><BrainCircuit size={13} style={{ color: services.llm_configured ? 'var(--neon-green)' : 'var(--neon-orange)' }} /><span>{services.llm_configured ? services.llm_model : '规则引擎降级模式'}</span></div>
            <div className="flex items-center gap-2"><Clock3 size={13} /><span>最近更新 {recentMsgs[0] ? timeLabel(recentMsgs[0].created_at) : '暂无'}</span></div>
          </section>
        </aside>
      </div>
    </div>
  );
}
