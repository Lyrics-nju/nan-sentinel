import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, BrainCircuit, Check, CheckCircle2, Clipboard, Eye, KeyRound, Loader2, Plus,
  RadioTower, RefreshCw, Server, ShieldCheck, XCircle,
} from 'lucide-react';

type Category = 'A' | 'B' | 'C';
type Disposition = 'new' | 'acknowledged' | 'assigned' | 'resolved';

interface IntelligenceItem {
  id: number;
  node_name: string;
  predicted_category: Category;
  corrected_category?: Category | null;
  effective_category: Category;
  summary: string;
  tags: string[];
  confidence?: number | null;
  source_type?: string;
  source_name?: string;
  evidence_excerpt?: string;
  occurred_at?: string;
  ingested_at: string;
  disposition: Disposition;
  review_status: 'unreviewed' | 'confirmed' | 'false_positive' | 'corrected';
}

interface DashboardData {
  nodes: { total: number; active: number };
  intelligence_24h: number;
  open_alerts: number;
  reviewed: number;
  review_accuracy: number | null;
  categories: Record<string, number>;
  sources: Record<string, number>;
  alerts: IntelligenceItem[];
  retention_days: number;
  privacy_mode: string;
  calibration?: { reporting_nodes: number; reviewed: number; accuracy: number | null; consented_examples: number };
}

interface NodeItem {
  id: string;
  name: string;
  enabled: number;
  active: number;
  created_at: string;
  last_seen?: string | null;
  last_error?: string;
  intelligence_count: number;
  reviewed: number;
  review_accuracy: number | null;
  space_name?: string;
  grant_status?: string;
  granted_categories?: Category[];
  granted_source_count?: number;
  share_evidence?: boolean;
  share_calibration_stats?: boolean;
  expires_at?: string;
}

interface SpaceItem {
  id: string;
  name: string;
  description: string;
  owner_label: string;
  member_count: number;
  active_members: number;
  intelligence_count: number;
}

interface InvitationDelivery {
  invite_key: string;
  join_url: string;
  qr_data_url: string;
  expires_at: string;
}

interface EvidenceRequest {
  id: number;
  intelligence_id: number;
  node_name: string;
  space_name: string;
  reason: string;
  status: string;
  evidence_content: string;
  requested_at: string;
  expires_at: string;
}

interface CalibrationData {
  aggregate: { reporting_nodes: number; reviewed: number; correct: number; accuracy: number | null; consented_examples: number };
  reports: { node_name: string; space_name: string; reviewed_count: number; accuracy: number | null; prompt_version: string; updated_at: string }[];
  examples: { external_id: string; node_name: string; space_name: string; predicted_category: string; corrected_category: string; confidence?: number | null; source_type: string; content_excerpt: string; prompt_version: string; consented_at: string }[];
  privacy: { statistics: string; examples: string };
}

const CATEGORY: Record<Category, { label: string; color: string }> = {
  A: { label: '重要预警', color: 'var(--neon-purple)' },
  B: { label: '校园动态', color: 'var(--neon-green)' },
  C: { label: '需求交易', color: 'var(--neon-orange)' },
};

const DISPOSITION_LABEL: Record<Disposition, string> = {
  new: '待处理', acknowledged: '已确认', assigned: '处理中', resolved: '已完成',
};

export default function MothershipPage() {
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [nodes, setNodes] = useState<NodeItem[]>([]);
  const [spaces, setSpaces] = useState<SpaceItem[]>([]);
  const [intelligence, setIntelligence] = useState<IntelligenceItem[]>([]);
  const [evidenceRequests, setEvidenceRequests] = useState<EvidenceRequest[]>([]);
  const [calibration, setCalibration] = useState<CalibrationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [newSpaceName, setNewSpaceName] = useState('');
  const [newSpaceOwner, setNewSpaceOwner] = useState('');
  const [creatingSpace, setCreatingSpace] = useState(false);
  const [invitation, setInvitation] = useState<InvitationDelivery | null>(null);
  const [requestingEvidenceId, setRequestingEvidenceId] = useState<number | null>(null);
  const [evidenceReason, setEvidenceReason] = useState('');
  const [viewingEvidenceId, setViewingEvidenceId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [busyNodeId, setBusyNodeId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [dashboardResponse, nodesResponse, spacesResponse, intelligenceResponse, evidenceResponse, calibrationResponse] = await Promise.all([
        fetch('/api/mothership/admin/dashboard'),
        fetch('/api/mothership/admin/nodes'),
        fetch('/api/mothership/admin/spaces'),
        fetch('/api/mothership/admin/intelligence?limit=100'),
        fetch('/api/mothership/admin/evidence-requests'),
        fetch('/api/mothership/admin/calibration'),
      ]);
      const [dashboardBody, nodesBody, spacesBody, intelligenceBody, evidenceBody, calibrationBody] = await Promise.all([
        dashboardResponse.json(), nodesResponse.json(), spacesResponse.json(), intelligenceResponse.json(), evidenceResponse.json(), calibrationResponse.json(),
      ]);
      if (!dashboardResponse.ok) throw new Error(dashboardBody.detail || '无法读取母舰');
      if (!nodesResponse.ok) throw new Error(nodesBody.detail || '无法读取哨站');
      if (!spacesResponse.ok) throw new Error(spacesBody.detail || '无法读取协作空间');
      if (!intelligenceResponse.ok) throw new Error(intelligenceBody.detail || '无法读取情报');
      if (!evidenceResponse.ok) throw new Error(evidenceBody.detail || '无法读取原文申请');
      if (!calibrationResponse.ok) throw new Error(calibrationBody.detail || '无法读取协作校准');
      setDashboard(dashboardBody);
      setNodes(nodesBody.nodes || []);
      setSpaces(spacesBody.spaces || []);
      setIntelligence(intelligenceBody.items || []);
      setEvidenceRequests(evidenceBody.items || []);
      setCalibration(calibrationBody);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法连接情报母舰');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial remote data synchronization
    void loadData();
  }, [loadData]);

  const createSpace = async () => {
    if (!newSpaceName.trim()) return;
    setCreatingSpace(true);
    setError('');
    try {
      const response = await fetch('/api/mothership/admin/spaces', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newSpaceName.trim(), owner_label: newSpaceOwner.trim(), invite_days: 30 }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '创建协作空间失败');
      setInvitation(data.invitation);
      setNewSpaceName('');
      await loadData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '创建协作空间失败');
    } finally {
      setCreatingSpace(false);
    }
  };

  const rotateInvitation = async (spaceId: string) => {
    setBusyNodeId(spaceId); setError('');
    try {
      const response = await fetch(`/api/mothership/admin/spaces/${spaceId}/invitations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expires_days: 30, max_uses: 0 }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '生成邀请失败');
      setInvitation(data.invitation);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '生成邀请失败'); }
    finally { setBusyNodeId(null); }
  };

  const requestEvidence = async (item: IntelligenceItem) => {
    if (evidenceReason.trim().length < 3) return setError('请说明申请原文的具体核验理由');
    setBusyId(item.id); setError('');
    try {
      const response = await fetch(`/api/mothership/admin/intelligence/${item.id}/evidence-requests`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: evidenceReason.trim() }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '发起原文申请失败');
      setRequestingEvidenceId(null); setEvidenceReason('');
      await loadData();
    } catch (reason) { setError(reason instanceof Error ? reason.message : '发起原文申请失败'); }
    finally { setBusyId(null); }
  };

  const review = async (item: IntelligenceItem, verdict: 'confirmed' | 'false_positive' | 'corrected', correctedCategory?: Category) => {
    setBusyId(item.id);
    try {
      const response = await fetch(`/api/mothership/admin/intelligence/${item.id}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict, corrected_category: correctedCategory }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '复核失败');
      await loadData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '复核失败');
    } finally {
      setBusyId(null);
    }
  };

  const setDisposition = async (item: IntelligenceItem, disposition: Disposition) => {
    setBusyId(item.id);
    try {
      const response = await fetch(`/api/mothership/admin/intelligence/${item.id}/disposition`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ disposition }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '更新处置状态失败');
      await loadData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '更新处置状态失败');
    } finally {
      setBusyId(null);
    }
  };

  const toggleNode = async (node: NodeItem) => {
    setBusyNodeId(node.id);
    setError('');
    try {
      const response = await fetch(`/api/mothership/admin/nodes/${node.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: !node.enabled }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '更新哨站状态失败');
      await loadData();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '更新哨站状态失败');
    } finally {
      setBusyNodeId(null);
    }
  };

  if (loading && !dashboard) {
    return <div className="h-full flex items-center justify-center"><Loader2 size={28} className="animate-spin" style={{ color: 'var(--neon-cyan)' }} aria-label="正在连接情报母舰" /></div>;
  }

  if (!dashboard) {
    return (
      <div className="page-shell max-w-3xl">
        <div className="glass rounded-2xl p-8">
          <RadioTower size={32} style={{ color: 'var(--neon-cyan)' }} />
          <h1 className="text-2xl font-bold mt-5" style={{ color: 'var(--text-primary)' }}>情报母舰尚未接入</h1>
          <p className="text-sm mt-2 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>{error || '在设置中填写母舰地址和管理员令牌后，即可管理多个哨站。'}</p>
          <a href="/settings" className="inline-flex mt-6 px-4 py-2 rounded-lg text-sm" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.25)' }}>前往母舰设置</a>
        </div>
      </div>
    );
  }

  return (
    <div className="page-shell animate-fade-in">
      <div className="page-header">
        <div>
          <div className="flex items-center gap-3">
            <RadioTower size={23} style={{ color: 'var(--neon-cyan)' }} />
            <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>情报母舰</h1>
            <span className="text-xs px-2 py-1 rounded-full" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,0.08)' }}>组织版</span>
          </div>
          <p className="text-sm mt-2" style={{ color: 'var(--text-secondary)' }}>跨哨站预警确认、误报复核与处置闭环</p>
        </div>
        <button onClick={() => void loadData()} disabled={loading} className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm disabled:opacity-50" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
          <RefreshCw size={15} className={loading ? 'animate-spin' : ''} /> 刷新
        </button>
      </div>

      {error && <div role="alert" className="mb-5 px-4 py-3 rounded-xl text-sm" style={{ background: 'rgba(255,80,80,0.08)', color: '#ff8a8a' }}>{error}</div>}

      <div className="glass rounded-xl px-5 py-4 mb-6 flex flex-wrap items-center gap-x-8 gap-y-3">
        <Metric label="哨站在线" value={`${dashboard.nodes.active}/${dashboard.nodes.total}`} tone="cyan" />
        <Metric label="24 小时情报" value={String(dashboard.intelligence_24h)} tone="neutral" />
        <Metric label="待处理预警" value={String(dashboard.open_alerts)} tone={dashboard.open_alerts > 0 ? 'warning' : 'neutral'} />
        <Metric label="人工复核准确率" value={dashboard.reviewed ? `${dashboard.review_accuracy ?? '--'}%` : '--'} tone="green" />
        <div className="ml-auto text-xs" style={{ color: 'var(--text-dim)' }}>结构化情报 · 留存 {dashboard.retention_days} 天</div>
      </div>

      {calibration && <section className="glass rounded-2xl overflow-hidden mb-6">
        <div className="px-5 py-4 flex flex-wrap items-start gap-3" style={{ borderBottom: '1px solid var(--border)', background: 'rgba(0,242,255,.025)' }}>
          <BrainCircuit size={17} style={{ color: 'var(--neon-cyan)' }} />
          <div><h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>协作校准</h2><p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>看总体误报趋势，不接管学生本地学习。统计匿名聚合，样本必须由学生逐条批准。</p></div>
          <span className="ml-auto text-[11px] px-2 py-1 rounded" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,.055)' }}>隐私边界已启用</span>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4" style={{ borderBottom: '1px solid var(--border)' }}>
          <CalibrationMetric label="统计哨站" value={String(calibration.aggregate.reporting_nodes)} />
          <CalibrationMetric label="累计人工复核" value={String(calibration.aggregate.reviewed)} />
          <CalibrationMetric label="加权准确率" value={calibration.aggregate.reviewed ? `${calibration.aggregate.accuracy ?? '--'}%` : '--'} accent />
          <CalibrationMetric label="逐条授权样本" value={String(calibration.aggregate.consented_examples)} />
        </div>
        <div className="grid grid-cols-1 xl:grid-cols-[.8fr_1.2fr]">
          <div className="p-4" style={{ borderRight: '1px solid var(--border)' }}>
            <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-primary)' }}>匿名统计上报</p>
            {calibration.reports.length ? <div className="space-y-1">{calibration.reports.slice(0, 6).map(report => <div key={`${report.space_name}-${report.node_name}`} className="flex items-center gap-3 py-2 text-xs"><span className="min-w-0 flex-1"><span className="block truncate" style={{ color: 'var(--text-secondary)' }}>{report.space_name || '协作空间'} · {report.node_name}</span><span className="block mt-0.5 font-mono truncate" title={report.prompt_version} style={{ color: 'var(--text-dim)' }}>{report.reviewed_count} 次复核 · {report.prompt_version.slice(0, 14)}…</span></span><span className="font-mono" style={{ color: 'var(--neon-green)' }}>{report.accuracy === null ? '--' : `${report.accuracy}%`}</span></div>)}</div> : <p className="text-xs py-5" style={{ color: 'var(--text-dim)' }}>还没有学生主动开启匿名校准统计。</p>}
          </div>
          <div className="p-4">
            <div className="flex items-center gap-2 mb-2"><p className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>学生逐条授权样本</p><span className="text-[10px]" style={{ color: 'var(--text-dim)' }}>脱敏片段 · 可由学生撤回</span></div>
            {calibration.examples.length ? <div className="space-y-2">{calibration.examples.slice(0, 5).map(example => <div key={example.external_id} className="rounded-lg px-3 py-2.5" style={{ background: 'rgba(255,255,255,.018)' }}><div className="flex items-center gap-2 text-xs"><span className="font-mono" style={{ color: 'var(--neon-purple)' }}>{example.predicted_category} → {example.corrected_category}</span><span style={{ color: 'var(--text-dim)' }}>{example.source_type || 'unknown'} · {example.space_name || example.node_name}</span>{typeof example.confidence === 'number' && <span className="ml-auto font-mono" style={{ color: 'var(--text-secondary)' }}>{Math.round(example.confidence * 100)}%</span>}</div><p className="text-xs mt-1.5 line-clamp-2" style={{ color: 'var(--text-secondary)' }}>{example.content_excerpt || '该样本未包含正文片段'}</p></div>)}</div> : <p className="text-xs py-5" style={{ color: 'var(--text-dim)' }}>还没有学生逐条批准校准样本；母舰不会自动读取本地案例。</p>}
          </div>
        </div>
      </section>}

      <section className="glass rounded-2xl overflow-hidden mb-6">
        <div className="px-5 py-4 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
          <KeyRound size={17} style={{ color: 'var(--neon-cyan)' }} />
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>协作空间与邀请密钥</h2>
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>学生凭邀请加入，但共享范围仍由学生本人确认。新邀请会让该空间的旧邀请密钥失效。</p>
          </div>
        </div>
        <div className="mothership-invite-grid p-5">
          <div className="space-y-3">
            <label className="block"><span className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>空间名称</span><input value={newSpaceName} onChange={event => setNewSpaceName(event.target.value)} placeholder="例如：2026 级计科通知协作" className="w-full px-3 py-2.5 rounded-lg text-sm outline-none" style={{ color: 'var(--text-primary)', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }} /></label>
            <label className="block"><span className="block text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>管理方标识</span><input value={newSpaceOwner} onChange={event => setNewSpaceOwner(event.target.value)} placeholder="例如：计科 2 班班委" className="w-full px-3 py-2.5 rounded-lg text-sm outline-none" style={{ color: 'var(--text-primary)', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }} /></label>
            <button type="button" onClick={() => void createSpace()} disabled={!newSpaceName.trim() || creatingSpace} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm disabled:opacity-40" style={{ color: '#001012', background: 'var(--neon-cyan)' }}>{creatingSpace ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />} 创建空间并生成邀请</button>
          </div>
          <div className="min-w-0">
            {invitation ? (
              <div className="mothership-invite-preview grid gap-5 items-center">
                <img src={invitation.qr_data_url} alt="学生加入协作空间二维码" className="w-[132px] h-[132px] rounded-lg bg-white p-2" />
                <div className="min-w-0 space-y-3">
                  <div><p className="text-xs" style={{ color: 'var(--neon-green)' }}>邀请密钥只在本次生成后展示</p><code className="block mt-1 text-sm break-all" style={{ color: 'var(--text-primary)' }}>{invitation.invite_key}</code></div>
                  <div className="flex gap-2"><button type="button" onClick={() => void navigator.clipboard.writeText(invitation.invite_key)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--neon-cyan)', border: '1px solid var(--border)' }}><Clipboard size={13} /> 复制密钥</button><button type="button" onClick={() => void navigator.clipboard.writeText(invitation.join_url)} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}><Clipboard size={13} /> 复制邀请链接</button></div>
                  <p className="text-xs" style={{ color: 'var(--text-dim)' }}>扫码、链接和密钥包含相同邀请权限 · 有效至 {new Date(invitation.expires_at).toLocaleString('zh-CN', { hour12: false })}</p>
                </div>
              </div>
            ) : spaces.length === 0 ? <EmptyState text="创建协作空间后，这里会生成密钥、链接和二维码" /> : (
              <div className="space-y-2">
                {spaces.map(space => <div key={space.id} className="flex items-center gap-3 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}><div className="min-w-0 flex-1"><p className="text-sm font-medium truncate" style={{ color: 'var(--text-primary)' }}>{space.name}</p><p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{space.member_count || 0} 个成员 · {space.active_members || 0} 个授权有效 · {space.intelligence_count || 0} 条情报</p></div><button type="button" onClick={() => void rotateInvitation(space.id)} disabled={busyNodeId === space.id} className="text-xs px-3 py-1.5 rounded-lg disabled:opacity-40" style={{ color: 'var(--neon-cyan)', border: '1px solid var(--border)' }}>生成新邀请</button></div>)}
              </div>
            )}
          </div>
        </div>
      </section>

      <div className="mothership-grid mb-6">
        <section className="glass rounded-2xl overflow-hidden">
          <div className="px-5 py-4 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <AlertTriangle size={17} style={{ color: 'var(--neon-purple)' }} />
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>待确认重要预警</h2>
            <span className="ml-auto text-xs font-mono" style={{ color: 'var(--text-dim)' }}>{dashboard.alerts.length} 条</span>
          </div>
          {dashboard.alerts.length === 0 ? (
            <EmptyState text="当前没有待确认的重要预警" />
          ) : (
            <div className="divide-y" style={{ borderColor: 'var(--border)' }}>
              {dashboard.alerts.slice(0, 8).map(item => (
                <div key={item.id} className="px-5 py-4">
                  <div className="flex items-start gap-3">
                    <span className="text-xs px-2 py-1 rounded shrink-0" style={{ color: CATEGORY.A.color, background: 'rgba(187,0,255,0.08)' }}>A</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{item.summary}</p>
                      <p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{item.node_name} · {item.source_name || item.source_type || '未知来源'} · {item.occurred_at || item.ingested_at}</p>
                      <div className="flex items-center gap-2 mt-3">
                        <button onClick={() => void review(item, 'confirmed')} disabled={busyId === item.id} className="text-xs px-2.5 py-1.5 rounded" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,0.07)' }}>确认准确</button>
                        <button onClick={() => void review(item, 'false_positive')} disabled={busyId === item.id} className="text-xs px-2.5 py-1.5 rounded" style={{ color: 'var(--neon-orange)', background: 'rgba(255,92,0,0.07)' }}>标记误报</button>
                        <button onClick={() => void setDisposition(item, 'acknowledged')} disabled={busyId === item.id} className="text-xs px-2.5 py-1.5 rounded" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.18)' }}>进入处置</button>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="glass rounded-2xl overflow-hidden">
          <div className="px-5 py-4 flex items-center gap-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <Server size={17} style={{ color: 'var(--neon-cyan)' }} />
            <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>哨站舰队</h2>
          </div>
          <div className="p-4">
            <div className="space-y-1">
              {nodes.length === 0 ? <p className="text-sm py-6 text-center" style={{ color: 'var(--text-dim)' }}>学生接受邀请后，成员哨站会出现在这里</p> : nodes.map(node => {
                const active = !!node.active;
                const stateLabel = !node.enabled ? '停用' : node.grant_status === 'paused' ? '学生暂停' : node.grant_status === 'expired' ? '已到期' : active ? '在线' : '离线';
                return (
                  <div key={node.id} className="flex items-center gap-3 py-2.5">
                    <span className="w-2 h-2 rounded-full shrink-0" style={{ background: active ? 'var(--neon-green)' : 'var(--text-dim)' }} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{node.name}</p>
                      <p className="text-xs" style={{ color: 'var(--text-dim)' }}>{node.space_name || '旧版直连'} · {(node.granted_categories || ['A', 'B', 'C']).join('/')} · {node.granted_source_count ?? '全部'} 个来源</p>
                    </div>
                    <span className="text-xs" style={{ color: active ? 'var(--neon-green)' : node.grant_status === 'active' ? 'var(--text-secondary)' : 'var(--neon-orange)' }}>{stateLabel}</span>
                    <button
                      type="button"
                      onClick={() => void toggleNode(node)}
                      disabled={busyNodeId === node.id}
                      className="text-xs px-2 py-1 rounded disabled:opacity-40"
                      style={{ color: node.enabled ? 'var(--neon-orange)' : 'var(--neon-green)', border: '1px solid var(--border)' }}
                    >
                      {node.enabled ? '停用' : '启用'}
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </section>
      </div>

      <section className="glass rounded-2xl overflow-hidden">
        <div className="px-5 py-4 flex items-center gap-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <ShieldCheck size={17} style={{ color: 'var(--neon-cyan)' }} />
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>情报处置台</h2>
          <div className="ml-auto flex items-center gap-2 text-xs" style={{ color: 'var(--text-dim)' }}>
            {Object.entries(dashboard.sources).slice(0, 4).map(([source, count]) => <span key={source}>{source} {count}</span>)}
          </div>
        </div>
        <div className="responsive-table-wrap">
          <table className="w-full text-left min-w-[980px]">
            <thead>
              <tr className="text-xs" style={{ color: 'var(--text-dim)', borderBottom: '1px solid var(--border)' }}>
                <th className="px-5 py-3 font-medium">分类</th><th className="px-3 py-3 font-medium">摘要与来源</th><th className="px-3 py-3 font-medium">哨站</th><th className="px-3 py-3 font-medium">可信度</th><th className="px-3 py-3 font-medium">复核</th><th className="px-3 py-3 font-medium">处置</th><th className="px-5 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {intelligence.map(item => {
                const category = CATEGORY[item.effective_category] || CATEGORY.B;
                const evidence = evidenceRequests.find(request => request.intelligence_id === item.id);
                return (
                  <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td className="px-5 py-3"><span className="text-xs px-2 py-1 rounded" style={{ color: category.color, background: 'rgba(255,255,255,0.035)' }}>{item.effective_category}</span></td>
                    <td className="px-3 py-3 max-w-[440px]"><p className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{item.summary}</p><p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>{item.source_name || item.source_type || '未知来源'} · {item.occurred_at || item.ingested_at}</p></td>
                    <td className="px-3 py-3 text-xs" style={{ color: 'var(--text-secondary)' }}>{item.node_name}</td>
                    <td className="px-3 py-3 text-xs font-mono" style={{ color: 'var(--text-secondary)' }}>{typeof item.confidence === 'number' ? `${Math.round(item.confidence * 100)}%` : '--'}</td>
                    <td className="px-3 py-3 text-xs" style={{ color: item.review_status === 'confirmed' ? 'var(--neon-green)' : item.review_status === 'false_positive' ? 'var(--neon-orange)' : 'var(--text-dim)' }}>{item.review_status === 'confirmed' ? '准确' : item.review_status === 'false_positive' ? '误报' : item.review_status === 'corrected' ? `纠正为 ${item.corrected_category}` : '待复核'}</td>
                    <td className="px-3 py-3"><select aria-label={`设置情报 ${item.id} 处置状态`} value={item.disposition} onChange={event => void setDisposition(item, event.target.value as Disposition)} className="text-xs rounded px-2 py-1.5 outline-none" style={{ color: 'var(--text-secondary)', background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>{(Object.keys(DISPOSITION_LABEL) as Disposition[]).map(value => <option key={value} value={value}>{DISPOSITION_LABEL[value]}</option>)}</select></td>
                    <td className="px-5 py-3"><div className="flex items-center justify-end gap-1.5"><button onClick={() => void review(item, 'confirmed')} disabled={busyId === item.id} title="确认准确" aria-label={`确认情报 ${item.id} 准确`} className="p-1.5 rounded"><CheckCircle2 size={15} style={{ color: 'var(--neon-green)' }} /></button><button onClick={() => void review(item, 'false_positive')} disabled={busyId === item.id} title="标记误报" aria-label={`标记情报 ${item.id} 为误报`} className="p-1.5 rounded"><XCircle size={15} style={{ color: 'var(--neon-orange)' }} /></button><select aria-label={`纠正情报 ${item.id} 分类`} defaultValue="" onChange={event => { if (event.target.value) void review(item, 'corrected', event.target.value as Category); }} className="text-xs rounded px-1.5 py-1 outline-none" style={{ color: 'var(--text-dim)', background: 'var(--bg-surface)', border: '1px solid var(--border)' }}><option value="">纠正</option>{(['A', 'B', 'C'] as Category[]).filter(value => value !== item.effective_category).map(value => <option key={value} value={value}>{value}</option>)}</select><button type="button" onClick={() => evidence?.status === 'approved' ? setViewingEvidenceId(item.id) : setRequestingEvidenceId(item.id)} disabled={evidence?.status === 'pending'} title={evidence?.status === 'approved' ? '查看已批准原文' : evidence?.status === 'denied' ? '学生已拒绝，可重新申请' : '向学生申请原文'} aria-label={`情报 ${item.id} 原文核验`} className="p-1.5 rounded disabled:opacity-40"><Eye size={15} style={{ color: evidence?.status === 'approved' ? 'var(--neon-green)' : evidence?.status === 'denied' ? 'var(--neon-orange)' : 'var(--neon-cyan)' }} /></button></div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {intelligence.length === 0 && <EmptyState text="哨站同步后，结构化情报会进入处置台" />}
        </div>
        {requestingEvidenceId && (() => {
          const item = intelligence.find(value => value.id === requestingEvidenceId);
          if (!item) return null;
          return <div className="px-5 py-4 flex flex-wrap items-end gap-3" style={{ borderTop: '1px solid var(--border)', background: 'rgba(0,242,255,0.035)' }}><div className="min-w-[280px] flex-1"><p className="text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>向“{item.node_name}”申请原文；理由会原样展示给学生</p><input value={evidenceReason} onChange={event => setEvidenceReason(event.target.value)} placeholder="例如：摘要中的截止日期不明确，需要核对原通知" className="w-full px-3 py-2 rounded-lg text-sm outline-none" style={{ color: 'var(--text-primary)', background: 'var(--bg-surface)', border: '1px solid var(--border)' }} /></div><button type="button" onClick={() => void requestEvidence(item)} disabled={busyId === item.id || evidenceReason.trim().length < 3} className="px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: '#001012', background: 'var(--neon-cyan)' }}>发送一次性申请</button><button type="button" onClick={() => { setRequestingEvidenceId(null); setEvidenceReason(''); }} className="px-3 py-2 rounded-lg text-xs" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>取消</button></div>;
        })()}
        {viewingEvidenceId && (() => {
          const request = evidenceRequests.find(value => value.intelligence_id === viewingEvidenceId && value.status === 'approved');
          if (!request) return null;
          return <div className="px-5 py-4" style={{ borderTop: '1px solid var(--border)', background: 'rgba(173,255,0,0.025)' }}><div className="flex items-center justify-between gap-3 mb-2"><div><p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>学生已批准本次原文核验</p><p className="text-xs mt-1" style={{ color: 'var(--text-dim)' }}>申请理由：{request.reason} · 内容将在 {new Date(request.expires_at).toLocaleString('zh-CN', { hour12: false })} 后清除</p></div><button type="button" onClick={() => setViewingEvidenceId(null)} aria-label="关闭原文" className="p-1.5 rounded"><XCircle size={16} style={{ color: 'var(--text-secondary)' }} /></button></div><div className="rounded-lg p-3 text-sm whitespace-pre-wrap max-h-56 overflow-auto" style={{ color: 'var(--text-secondary)', background: 'rgba(0,0,0,0.32)' }}>{request.evidence_content}</div></div>;
        })()}
      </section>
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone: 'cyan' | 'green' | 'warning' | 'neutral' }) {
  const color = tone === 'cyan' ? 'var(--neon-cyan)' : tone === 'green' ? 'var(--neon-green)' : tone === 'warning' ? 'var(--neon-orange)' : 'var(--text-primary)';
  return <div className="flex items-baseline gap-2"><span className="text-xs" style={{ color: 'var(--text-dim)' }}>{label}</span><span className="text-lg font-semibold font-mono" style={{ color }}>{value}</span></div>;
}

function CalibrationMetric({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return <div className="px-4 py-3" style={{ borderRight: '1px solid var(--border)' }}><p className="font-mono text-lg" style={{ color: accent ? 'var(--neon-green)' : 'var(--text-primary)' }}>{value}</p><p className="text-[11px] mt-0.5" style={{ color: 'var(--text-dim)' }}>{label}</p></div>;
}

function EmptyState({ text }: { text: string }) {
  return <div className="px-6 py-12 text-center"><Check size={26} className="mx-auto mb-2" style={{ color: 'var(--text-dim)' }} /><p className="text-sm" style={{ color: 'var(--text-secondary)' }}>{text}</p></div>;
}
