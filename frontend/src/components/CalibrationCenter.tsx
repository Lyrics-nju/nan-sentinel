import { useCallback, useEffect, useState } from 'react';
import {
  Archive, BrainCircuit, Check, Database, FlaskConical, GitBranch,
  History, Layers3, Loader2, Play, RotateCcw, ShieldCheck, SlidersHorizontal,
  UploadCloud,
} from 'lucide-react';

interface Strategy {
  strategy_version: string; active_prompt_version: string; max_examples: number;
  min_similarity: number; override_similarity: number; default_threshold: number;
  retrieval_enabled: boolean;
  layers: { key: string; label: string; managed_by: string }[];
}
interface Example {
  msg_id: string; content_preview: string; original_category: string; corrected_category: string;
  source_name: string; source_key: string; confidence?: number | null; prompt_version: string;
  is_gold: boolean; active: boolean; reviewed_at: string; shared_with_mothership: boolean;
}
interface SourceSetting { source_key: string; label: string; confidence_threshold: number; }
interface Evaluation { sample_count: number; baseline_accuracy: number | null; calibrated_accuracy: number | null; created_at?: string; }
interface PromptVersion { version: string; label: string; release_note: string; status: 'active' | 'candidate' | 'archived'; created_at: string; activated_at?: string; }
interface Summary {
  strategy: Strategy;
  counts: { reviewed: number; active: number; gold: number; corrected: number };
  examples: Example[];
  sources: SourceSetting[];
  latest_evaluation?: Evaluation | null;
  prompt_versions: PromptVersion[];
  mothership: { connected: boolean; membership_status: string; space_name: string; share_calibration_stats: boolean };
}

const layerIcons = { base: ShieldCheck, memory: Database, source: SlidersHorizontal, gate: GitBranch };

export default function CalibrationCenter() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [confirmShare, setConfirmShare] = useState('');

  const load = useCallback(async () => {
    const response = await fetch('/api/calibration');
    if (!response.ok) throw new Error('无法读取校准策略');
    setSummary(await response.json());
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data synchronization
    void load().catch(error => setMessage(error instanceof Error ? error.message : '加载失败'));
  }, [load]);

  const request = async (key: string, url: string, method: string, body?: unknown, success = '已保存') => {
    setBusy(key); setMessage('');
    try {
      const response = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '操作失败');
      setMessage(success);
      await load();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '操作失败');
    } finally { setBusy(''); }
  };

  if (!summary) return <div className="min-h-64 flex items-center justify-center"><Loader2 className="animate-spin" style={{ color: 'var(--neon-cyan)' }} aria-label="正在加载校准中心" /></div>;
  const evaluation = summary.latest_evaluation;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2"><BrainCircuit size={19} style={{ color: 'var(--neon-cyan)' }} /><h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>本地校准中心</h2></div>
          <p className="text-sm mt-1 max-w-3xl" style={{ color: 'var(--text-secondary)' }}>复核案例只保存在本机。系统为新消息检索相似案例并组合提示词，不训练模型权重，也不会把原文自动同步给母舰。</p>
          <p className="text-xs mt-2" style={{ color: summary.mothership.share_calibration_stats ? 'var(--neon-green)' : 'var(--text-dim)' }}>{summary.mothership.share_calibration_stats ? `已向「${summary.mothership.space_name || '协作空间'}」授权匿名统计；样本仍需逐条批准` : '母舰校准统计未授权 · 全部复核数据留在本机'}</p>
        </div>
        <span className="font-mono text-xs px-2.5 py-1.5 rounded-lg" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,.06)' }}>{summary.strategy.strategy_version}</span>
      </div>

      {message && <div role="status" className="px-3 py-2 rounded-lg text-xs" style={{ color: message.includes('失败') || message.includes('无法') ? '#ff9b9b' : 'var(--neon-green)', background: 'rgba(255,255,255,.025)' }}>{message}</div>}

      <section className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex items-center gap-2" style={{ background: 'rgba(0,242,255,.035)' }}><Layers3 size={16} style={{ color: 'var(--neon-cyan)' }} /><h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>提示词配方</h3><span className="ml-auto text-[11px] font-mono" style={{ color: 'var(--text-dim)' }}>{summary.strategy.active_prompt_version}</span></div>
        <div className="grid grid-cols-1 lg:grid-cols-4">
          {summary.strategy.layers.map((layer, index) => {
            const Icon = layerIcons[layer.key as keyof typeof layerIcons] || Layers3;
            return <div key={layer.key} className="p-4" style={{ borderTop: '1px solid var(--border)', borderRight: index < 3 ? '1px solid var(--border)' : 0 }}><div className="flex items-center gap-2"><span className="font-mono text-xs" style={{ color: 'var(--text-dim)' }}>{index + 1}</span><Icon size={15} style={{ color: 'var(--neon-cyan)' }} /><span className="text-sm" style={{ color: 'var(--text-primary)' }}>{layer.label}</span></div><p className="text-xs mt-2" style={{ color: 'var(--text-dim)' }}>由{layer.managed_by}维护</p></div>;
          })}
        </div>
        <div className="px-4 py-4" style={{ borderTop: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 mb-3"><History size={14} style={{ color: 'var(--neon-purple)' }} /><p className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>不可变版本记录</p><span className="text-[11px] ml-auto" style={{ color: 'var(--text-dim)' }}>切换仅影响后续判断，可一键回退</span></div>
          <div className="space-y-2">
            {summary.prompt_versions.map(version => (
              <div key={version.version} className="flex flex-wrap items-center gap-3 rounded-lg px-3 py-2.5" style={{ background: version.status === 'active' ? 'rgba(0,242,255,.035)' : 'rgba(255,255,255,.015)' }}>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2"><span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{version.label || '基础分类规则'}</span><span className="text-[10px] px-1.5 py-0.5 rounded" style={{ color: version.status === 'active' ? 'var(--neon-green)' : 'var(--text-dim)', border: '1px solid var(--border)' }}>{version.status === 'active' ? '当前生效' : version.status === 'candidate' ? '候选' : '历史'}</span></span>
                  <span className="block text-[11px] mt-1 truncate" title={version.version} style={{ color: 'var(--text-dim)' }}>{version.release_note || '稳定规则快照'} · {version.version.slice(0, 18)}…</span>
                </span>
                {version.status !== 'active' && <button type="button" onClick={() => void request(`prompt-${version.version}`, `/api/calibration/prompt-versions/${encodeURIComponent(version.version)}/activate`, 'POST', undefined, '提示词版本已切换，可随时回退')} disabled={busy === `prompt-${version.version}`} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs disabled:opacity-40" style={{ color: 'var(--neon-cyan)', border: '1px solid var(--border)' }}>{busy === `prompt-${version.version}` ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}切换到此版本</button>}
              </div>
            ))}
          </div>
        </div>
        <p className="px-4 py-3 text-xs" style={{ color: 'var(--text-secondary)', borderTop: '1px solid var(--border)' }}>基础规则随应用版本发布并保留版本号；用户只管理案例、阈值和评估。这样避免一次误改提示词破坏所有分类，也便于长期回滚。</p>
      </section>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_1.1fr] gap-5">
        <section className="rounded-xl p-4 space-y-4" style={{ border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2"><SlidersHorizontal size={16} style={{ color: 'var(--neon-cyan)' }} /><h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>检索与安全门</h3></div>
          <label className="flex items-center justify-between gap-4"><span><span className="block text-sm" style={{ color: 'var(--text-primary)' }}>启用本地案例检索</span><span className="block text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>关闭后仍保留案例，但不再加入提示词</span></span><input type="checkbox" checked={summary.strategy.retrieval_enabled} onChange={event => void request('retrieval', '/api/calibration/settings', 'PATCH', { retrieval_enabled: event.target.checked })} /></label>
          <Control label="每次引用案例" value={`${summary.strategy.max_examples} 条`} description="限制为 3—5 条，防止提示词无限增长">
            <select value={summary.strategy.max_examples} onChange={event => void request('examples', '/api/calibration/settings', 'PATCH', { max_examples: Number(event.target.value) })} className="px-2 py-1.5 rounded-lg text-xs" style={{ color: 'var(--text-primary)', background: 'var(--bg-surface)', border: '1px solid var(--border)' }}><option value={3}>3 条</option><option value={4}>4 条</option><option value={5}>5 条</option></select>
          </Control>
          <Control label="默认待确认阈值" value={`${Math.round(summary.strategy.default_threshold * 100)}%`} description="置信度低于此值时不自动同步母舰">
            <input aria-label="默认待确认阈值" type="range" min="0.45" max="0.9" step="0.01" value={summary.strategy.default_threshold} onChange={event => setSummary({ ...summary, strategy: { ...summary.strategy, default_threshold: Number(event.target.value) } })} onPointerUp={event => void request('threshold', '/api/calibration/settings', 'PATCH', { default_threshold: Number((event.target as HTMLInputElement).value) })} />
          </Control>
          <Control label="近似案例强纠正" value={`${Math.round(summary.strategy.override_similarity * 100)}%`} description="只在高度相似且案例结论一致时覆盖规则引擎">
            <input aria-label="近似案例强纠正阈值" type="range" min="0.55" max="0.95" step="0.01" value={summary.strategy.override_similarity} onChange={event => setSummary({ ...summary, strategy: { ...summary.strategy, override_similarity: Number(event.target.value) } })} onPointerUp={event => void request('override', '/api/calibration/settings', 'PATCH', { override_similarity: Number((event.target as HTMLInputElement).value) })} />
          </Control>
        </section>

        <section className="rounded-xl p-4" style={{ border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 mb-4"><GitBranch size={16} style={{ color: 'var(--neon-purple)' }} /><h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>分来源阈值</h3><span className="ml-auto text-xs" style={{ color: 'var(--text-dim)' }}>{summary.sources.length} 个来源</span></div>
          {summary.sources.length ? <div className="space-y-2 max-h-64 overflow-auto">{summary.sources.map(source => <div key={source.source_key} className="flex items-center gap-3 px-3 py-2.5 rounded-lg" style={{ background: 'rgba(255,255,255,.018)' }}><span className="text-sm min-w-0 flex-1 truncate" style={{ color: 'var(--text-secondary)' }}>{source.label || source.source_key}</span><span className="font-mono text-xs" style={{ color: 'var(--neon-orange)' }}>{Math.round(source.confidence_threshold * 100)}%</span><input aria-label={`${source.label} 置信度阈值`} type="range" min="0.45" max="0.9" step="0.01" value={source.confidence_threshold} onChange={event => setSummary({ ...summary, sources: summary.sources.map(item => item.source_key === source.source_key ? { ...item, confidence_threshold: Number(event.target.value) } : item) })} onPointerUp={event => void request(`source-${source.source_key}`, '/api/calibration/source-threshold', 'PUT', { ...source, confidence_threshold: Number((event.target as HTMLInputElement).value) }, `已更新「${source.label}」阈值`)} /></div>)}</div> : <p className="text-xs py-8 text-center" style={{ color: 'var(--text-dim)' }}>产生第一条人工复核后，对应群或来源会出现在这里。</p>}
        </section>
      </div>

      <section className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3" style={{ background: 'rgba(173,255,0,.025)' }}><FlaskConical size={16} style={{ color: 'var(--neon-green)' }} /><div><h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>金标准回放</h3><p className="text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>金标准不参与案例检索，作为独立留出集比较原判断与当前校准策略。</p></div><button type="button" onClick={() => void request('evaluate', '/api/calibration/evaluate', 'POST', undefined, '评估完成')} disabled={!summary.counts.gold || busy === 'evaluate'} className="ml-auto inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: '#071000', background: 'var(--neon-green)' }}>{busy === 'evaluate' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}运行回放</button></div>
        <div className="grid grid-cols-3 divide-x" style={{ borderTop: '1px solid var(--border)', borderColor: 'var(--border)' }}>
          <Metric label="金标准样本" value={String(summary.counts.gold)} color="var(--text-primary)" />
          <Metric label="校准前准确率" value={evaluation?.sample_count ? `${evaluation.baseline_accuracy ?? '--'}%` : '--'} color="var(--text-secondary)" />
          <Metric label="校准后准确率" value={evaluation?.sample_count ? `${evaluation.calibrated_accuracy ?? '--'}%` : '--'} color="var(--neon-green)" />
        </div>
      </section>

      <section className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-4 py-3 flex flex-wrap items-center gap-2"><Database size={16} style={{ color: 'var(--neon-cyan)' }} /><h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>复核案例库</h3><span className="ml-auto text-xs" style={{ color: 'var(--text-dim)' }}>{summary.counts.active} 个有效案例 · 默认仅本地</span></div>
        {summary.examples.length ? <div>{summary.examples.map(example => {
          const confirming = confirmShare === example.msg_id;
          return <div key={example.msg_id} className="px-4 py-3" style={{ borderTop: '1px solid var(--border)', opacity: example.active ? 1 : .55 }}>
            <div className="flex flex-wrap lg:flex-nowrap items-center gap-3">
              <span className="min-w-0 flex-1"><span className="block text-sm truncate" style={{ color: 'var(--text-primary)' }}>{example.content_preview}</span><span className="flex flex-wrap gap-x-3 mt-1 text-xs" style={{ color: 'var(--text-dim)' }}><span>{example.source_name || '本地来源'}</span><span className="font-mono">{example.original_category} → {example.corrected_category}</span>{typeof example.confidence === 'number' && <span>{Math.round(example.confidence * 100)}%</span>}{example.shared_with_mothership && <span style={{ color: 'var(--neon-green)' }}>已获授权共享母舰</span>}</span></span>
              <button type="button" onClick={() => void request(`gold-${example.msg_id}`, `/api/calibration/examples/${encodeURIComponent(example.msg_id)}/gold`, 'POST', { enabled: !example.is_gold }, example.is_gold ? '已移出金标准' : '已设为金标准')} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs" style={{ color: example.is_gold ? 'var(--neon-green)' : 'var(--text-secondary)', border: '1px solid var(--border)' }}>{example.is_gold ? <Check size={13} /> : <FlaskConical size={13} />}{example.is_gold ? '金标准' : '设为金标准'}</button>
              <button type="button" onClick={() => void request(`active-${example.msg_id}`, `/api/calibration/examples/${encodeURIComponent(example.msg_id)}/active`, 'POST', { enabled: !example.active }, example.active ? '案例已停用' : '案例已恢复')} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs" style={{ color: example.active ? 'var(--text-dim)' : 'var(--neon-cyan)', border: '1px solid var(--border)' }}>{example.active ? <Archive size={13} /> : <RotateCcw size={13} />}{example.active ? '停用' : '恢复'}</button>
              <button type="button" onClick={() => example.shared_with_mothership ? void request(`share-${example.msg_id}`, `/api/calibration/examples/${encodeURIComponent(example.msg_id)}/share`, 'POST', { enabled: false }, '已从母舰撤回这条脱敏样本') : setConfirmShare(example.msg_id)} disabled={!example.active || busy === `share-${example.msg_id}`} className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs disabled:opacity-40" style={{ color: example.shared_with_mothership ? 'var(--neon-orange)' : 'var(--neon-cyan)', border: '1px solid var(--border)' }}><UploadCloud size={13} />{example.shared_with_mothership ? '撤回母舰样本' : '批准共享脱敏样本'}</button>
            </div>
            {confirming && <div className="mt-3 rounded-lg px-3 py-3 flex flex-wrap items-center gap-2" style={{ background: 'rgba(0,242,255,.035)', border: '1px solid rgba(0,242,255,.16)' }}><p className="text-xs mr-auto max-w-2xl" style={{ color: 'var(--text-secondary)' }}>只发送这条样本的 300 字脱敏片段、预测/纠正结果、置信度、来源类型与来源哈希；不发送群名或发送者。确认仅对本条生效。</p><button type="button" onClick={() => { setConfirmShare(''); void request(`share-${example.msg_id}`, `/api/calibration/examples/${encodeURIComponent(example.msg_id)}/share`, 'POST', { enabled: true }, '已授权共享这一条脱敏校准样本'); }} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: '#001012', background: 'var(--neon-cyan)' }}>确认只共享这条</button><button type="button" onClick={() => setConfirmShare('')} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>取消</button></div>}
          </div>;
        })}</div> : <p className="px-5 py-10 text-center text-sm" style={{ color: 'var(--text-dim)' }}>在消息中心确认或纠正一次判断，第一条本地校准案例就会出现在这里。</p>}
      </section>
    </div>
  );
}

function Control({ label, value, description, children }: { label: string; value: string; description: string; children: React.ReactNode }) {
  return <div className="flex items-center gap-4"><span className="min-w-0 flex-1"><span className="block text-sm" style={{ color: 'var(--text-primary)' }}>{label} <span className="font-mono text-xs ml-1" style={{ color: 'var(--neon-cyan)' }}>{value}</span></span><span className="block text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{description}</span></span>{children}</div>;
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return <div className="px-4 py-4 text-center"><p className="font-mono text-lg" style={{ color }}>{value}</p><p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{label}</p></div>;
}
