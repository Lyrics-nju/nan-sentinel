import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Check, ChevronDown, Eye, KeyRound, Loader2, Pause,
  Play, RadioTower, RefreshCw, ShieldCheck, Trash2, Unplug, X,
} from 'lucide-react';

type Category = 'A' | 'B' | 'C';

interface ConsentConfig {
  mothership_url?: string;
  mothership_node_name?: string;
  mothership_node_token_set?: boolean;
  mothership_admin_token_set?: boolean;
  mothership_space_id?: string;
  mothership_space_name?: string;
  mothership_owner_label?: string;
  mothership_membership_status?: string;
  mothership_categories?: Category[];
  mothership_source_refs?: string[];
  mothership_share_evidence?: boolean;
  mothership_share_calibration_stats?: boolean;
  mothership_expires_at?: string;
}

interface SourceOption {
  ref: string;
  label: string;
  source_type: string;
  source_name: string;
  message_count: number;
}

interface InvitationInfo {
  space_id: string;
  space_name: string;
  description: string;
  owner_label: string;
  invite_expires_at: string;
  privacy_notice: string;
}

interface PreviewItem {
  external_id: string;
  category: Category;
  summary: string;
  source_name: string;
  evidence_excerpt?: string;
}

interface PreviewData {
  count: number;
  items: PreviewItem[];
  shared_fields: string[];
  never_shared: string[];
}

interface Membership {
  status: string;
  space_id: string;
  space_name: string;
  owner_label: string;
  categories: Category[];
  source_refs: string[];
  share_evidence: boolean;
  share_calibration_stats: boolean;
  expires_at: string;
}

interface EvidenceRequest {
  id: number;
  summary: string;
  reason: string;
  status: string;
  requested_at: string;
  expires_at: string;
  local_available: boolean;
  local_content: string;
}

const CATEGORY_LABELS: Record<Category, string> = {
  A: 'A · 重要预警', B: 'B · 校园动态', C: 'C · 需求交易',
};

function invitationFromInput(value: string, fallbackUrl: string) {
  const trimmed = value.trim();
  try {
    const url = new URL(trimmed);
    return {
      inviteKey: url.searchParams.get('invite') || '',
      mothershipUrl: url.searchParams.get('mothership') || fallbackUrl,
    };
  } catch {
    return { inviteKey: trimmed, mothershipUrl: fallbackUrl };
  }
}

function formatDate(value?: string) {
  if (!value) return '未设置';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

export default function MothershipConsentPanel({
  config,
  onConfigChange,
}: {
  config: ConsentConfig;
  onConfigChange: (partial: Partial<ConsentConfig>) => void;
}) {
  const query = useMemo(() => new URLSearchParams(window.location.search), []);
  const [mothershipUrl, setMothershipUrl] = useState(query.get('mothership') || config.mothership_url || 'http://127.0.0.1:8010');
  const [inviteInput, setInviteInput] = useState(query.get('invite') || '');
  const [deviceName, setDeviceName] = useState(config.mothership_node_name || '我的学生哨站');
  const [categories, setCategories] = useState<Category[]>(config.mothership_node_token_set && config.mothership_categories?.length ? config.mothership_categories : ['A']);
  const [sourceRefs, setSourceRefs] = useState<string[]>(config.mothership_source_refs || []);
  const [shareEvidence, setShareEvidence] = useState(!!config.mothership_share_evidence);
  const [shareCalibrationStats, setShareCalibrationStats] = useState(!!config.mothership_share_calibration_stats);
  const [expiresDays, setExpiresDays] = useState(30);
  const [sources, setSources] = useState<SourceOption[]>([]);
  const [invitation, setInvitation] = useState<InvitationInfo | null>(null);
  const [preview, setPreview] = useState<PreviewData | null>(null);
  const [membership, setMembership] = useState<Membership | null>(null);
  const [evidenceRequests, setEvidenceRequests] = useState<EvidenceRequest[]>([]);
  const [confirmEvidenceId, setConfirmEvidenceId] = useState<number | null>(null);
  const [editingGrant, setEditingGrant] = useState(false);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [adminToken, setAdminToken] = useState('');

  const loadMembership = useCallback(async () => {
    if (!config.mothership_node_token_set) return;
    try {
      const [membershipResponse, requestResponse] = await Promise.all([
        fetch('/api/mothership/membership'),
        fetch('/api/mothership/evidence-requests'),
      ]);
      const member = await membershipResponse.json();
      const requests = await requestResponse.json();
      if (!membershipResponse.ok) throw new Error(member.detail || '无法读取共享授权');
      setMembership(member);
      setCategories(member.categories || []);
      setSourceRefs(member.source_refs || []);
      setShareEvidence(!!member.share_evidence);
      setShareCalibrationStats(!!member.share_calibration_stats);
      setEvidenceRequests(requestResponse.ok ? requests.items || [] : []);
      onConfigChange({
        mothership_space_id: member.space_id,
        mothership_space_name: member.space_name,
        mothership_owner_label: member.owner_label,
        mothership_membership_status: member.status,
        mothership_categories: member.categories,
        mothership_source_refs: member.source_refs,
        mothership_share_evidence: !!member.share_evidence,
        mothership_share_calibration_stats: !!member.share_calibration_stats,
        mothership_expires_at: member.expires_at,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法读取共享授权');
    }
  }, [config.mothership_node_token_set, onConfigChange]);

  useEffect(() => {
    fetch('/api/mothership/share-options')
      .then(response => response.json())
      .then(data => setSources(data.items || []))
      .catch(() => setSources([]));
    // eslint-disable-next-line react-hooks/set-state-in-effect -- synchronize the local view with remote consent state
    void loadMembership();
  }, [loadMembership]);

  const resetFeedback = () => { setError(''); setMessage(''); };
  const invalidatePreview = () => { setPreview(null); resetFeedback(); };

  const inspectInvitation = async () => {
    resetFeedback();
    const parsed = invitationFromInput(inviteInput, mothershipUrl);
    if (!parsed.inviteKey || !parsed.mothershipUrl) {
      setError('请输入邀请密钥或完整邀请链接');
      return;
    }
    setBusy('inspect');
    try {
      const response = await fetch('/api/mothership/invitations/inspect', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mothership_url: parsed.mothershipUrl, invite_key: parsed.inviteKey }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '邀请验证失败');
      setMothershipUrl(parsed.mothershipUrl);
      setInviteInput(parsed.inviteKey);
      setInvitation(data);
      setMessage(`已验证：${data.space_name}`);
    } catch (reason) {
      setInvitation(null);
      setError(reason instanceof Error ? reason.message : '邀请验证失败');
    } finally {
      setBusy('');
    }
  };

  const loadPreview = async () => {
    resetFeedback();
    if (!categories.length) return setError('至少选择一个情报分类');
    setBusy('preview');
    try {
      const response = await fetch('/api/mothership/share-preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          categories, source_refs: sourceRefs, share_evidence: shareEvidence,
          share_calibration_stats: shareCalibrationStats,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '无法生成共享预览');
      setPreview(data);
      setMessage('预览已更新，确认无误后再授权');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法生成共享预览');
    } finally {
      setBusy('');
    }
  };

  const joinSpace = async () => {
    if (!invitation || !preview) return;
    resetFeedback();
    setBusy('join');
    try {
      const response = await fetch('/api/mothership/membership/join', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mothership_url: mothershipUrl, invite_key: inviteInput, device_name: deviceName,
          categories, source_refs: sourceRefs, share_evidence: shareEvidence, expires_days: expiresDays,
          share_calibration_stats: shareCalibrationStats,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '加入协作空间失败');
      onConfigChange({
        mothership_url: mothershipUrl, mothership_node_name: deviceName,
        mothership_node_token_set: true, mothership_space_id: data.space.id,
        mothership_space_name: data.space.name, mothership_owner_label: data.space.owner_label,
        mothership_membership_status: 'active', mothership_categories: categories,
        mothership_source_refs: sourceRefs, mothership_share_evidence: shareEvidence,
        mothership_share_calibration_stats: shareCalibrationStats,
        mothership_expires_at: data.grant.expires_at,
      });
      setMembership({
        status: 'active', space_id: data.space.id, space_name: data.space.name,
        owner_label: data.space.owner_label, categories, source_refs: sourceRefs,
        share_evidence: shareEvidence, share_calibration_stats: shareCalibrationStats,
        expires_at: data.grant.expires_at,
      });
      setInvitation(null);
      setPreview(null);
      setMessage('已加入协作空间。成员密钥已安全保存在本机。');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '加入协作空间失败');
    } finally {
      setBusy('');
    }
  };

  const updateGrant = async () => {
    if (!preview) return setError('请先重新生成共享预览');
    setBusy('grant'); resetFeedback();
    try {
      const response = await fetch('/api/mothership/membership/grant', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          categories, source_refs: sourceRefs, share_evidence: shareEvidence,
          share_calibration_stats: shareCalibrationStats,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '更新授权失败');
      setMembership(data);
      onConfigChange({
        mothership_categories: categories, mothership_source_refs: sourceRefs,
        mothership_share_evidence: shareEvidence,
        mothership_share_calibration_stats: shareCalibrationStats,
      });
      setEditingGrant(false); setPreview(null); setMessage('授权范围已更新');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '更新授权失败');
    } finally { setBusy(''); }
  };

  const setMembershipState = async (state: 'active' | 'paused') => {
    setBusy(state); resetFeedback();
    try {
      const response = await fetch('/api/mothership/membership/state', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '操作失败');
      setMembership(previous => previous ? { ...previous, status: state } : previous);
      onConfigChange({ mothership_membership_status: state });
      setMessage(state === 'paused' ? '共享已暂停，本机消息不会继续上传' : '共享已恢复');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '操作失败'); }
    finally { setBusy(''); }
  };

  const syncNow = async () => {
    setBusy('sync'); resetFeedback();
    try {
      const response = await fetch('/api/mothership/sync', { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '同步失败');
      setMessage(data.status === 'ok' ? `同步完成：本次 ${data.synced || 0} 条，待重试 ${data.pending || 0} 条` : `同步未完成：${data.error || data.status}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '同步失败'); }
    finally { setBusy(''); }
  };

  const deleteSharedData = async () => {
    if (!window.confirm('删除母舰中由本哨站同步的全部情报？本机原文不会删除。')) return;
    setBusy('delete'); resetFeedback();
    try {
      const response = await fetch('/api/mothership/membership/data', { method: 'DELETE' });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '删除失败');
      setMessage(`母舰中的 ${data.deleted || 0} 条情报已删除，本机数据仍保留`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : '删除失败'); }
    finally { setBusy(''); }
  };

  const revokeMembership = async () => {
    if (!window.confirm('撤销授权并删除本哨站在母舰中的数据？撤销后需重新接受邀请才能加入。')) return;
    setBusy('revoke'); resetFeedback();
    try {
      const response = await fetch('/api/mothership/membership/revoke', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ delete_data: true }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '撤销失败');
      setMembership(null); setEvidenceRequests([]); setEditingGrant(false);
      onConfigChange({
        mothership_node_token_set: false, mothership_space_id: '', mothership_space_name: '',
        mothership_owner_label: '', mothership_membership_status: 'revoked', mothership_categories: ['A'],
        mothership_source_refs: [], mothership_share_evidence: false,
        mothership_share_calibration_stats: false, mothership_expires_at: '',
      });
      setMessage('授权与母舰数据均已撤销，本机原文仍保留');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '撤销失败'); }
    finally { setBusy(''); }
  };

  const respondEvidence = async (requestId: number, decision: 'approved' | 'denied') => {
    setBusy(`evidence-${requestId}`); resetFeedback();
    try {
      const response = await fetch(`/api/mothership/evidence-requests/${requestId}/respond`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ decision }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '处理原文申请失败');
      setEvidenceRequests(items => items.map(item => item.id === requestId ? { ...item, status: decision } : item));
      setConfirmEvidenceId(null);
      setMessage(decision === 'approved' ? '已按本次申请发送原文' : '已拒绝原文申请');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '处理原文申请失败'); }
    finally { setBusy(''); }
  };

  const saveAdminAccess = async () => {
    setBusy('admin'); resetFeedback();
    try {
      const response = await fetch('/api/config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mothership_url: mothershipUrl, mothership_admin_token: adminToken || undefined }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '管理员接入保存失败');
      onConfigChange({ mothership_url: mothershipUrl, mothership_admin_token_set: config.mothership_admin_token_set || !!adminToken });
      setAdminToken(''); setMessage('管理员工作台接入已保存');
    } catch (reason) { setError(reason instanceof Error ? reason.message : '管理员接入保存失败'); }
    finally { setBusy(''); }
  };

  const pendingEvidence = evidenceRequests.filter(item => item.status === 'pending');

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0" style={{ background: 'rgba(0,242,255,0.08)' }}>
          <ShieldCheck size={18} style={{ color: 'var(--neon-cyan)' }} />
        </div>
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>我的共享授权</h2>
          <p className="text-sm mt-1 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>加入协作空间不会交出聊天账号。你决定共享哪些来源、哪些分类以及授权多久；完整原文永远需要再次批准。</p>
        </div>
      </div>

      {error && <div role="alert" className="px-4 py-3 rounded-xl text-sm" style={{ color: '#ff9b9b', background: 'rgba(255,80,80,0.08)' }}>{error}</div>}
      {message && <div role="status" className="px-4 py-3 rounded-xl text-sm" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,0.06)' }}>{message}</div>}

      {!membership ? (
        <section className="rounded-xl p-5 space-y-4" style={{ border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2">
            <KeyRound size={17} style={{ color: 'var(--neon-cyan)' }} />
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>使用邀请密钥加入</h3>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>可粘贴邀请密钥，也可粘贴管理员分享的链接；扫描二维码打开后会自动填入。</p>
          <div className="grid grid-cols-3 gap-2" aria-label="授权进度">
            {['验证邀请', '选择范围', '预览授权'].map((label, index) => { const complete = index === 0 ? !!invitation : index === 1 ? !!preview : false; const active = index === 0 ? !invitation : index === 1 ? !!invitation && !preview : !!preview; return <div key={label} className="rounded-lg px-2 py-2 text-center text-xs" style={{ color: complete ? 'var(--neon-green)' : active ? 'var(--neon-cyan)' : 'var(--text-dim)', background: complete || active ? 'rgba(0,242,255,.035)' : 'rgba(255,255,255,.015)', border: '1px solid var(--border)' }}><span className="font-mono mr-1">{index + 1}</span>{label}</div>; })}
          </div>
          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(180px,0.55fr)] gap-3">
            <Input label="邀请密钥或链接" value={inviteInput} onChange={value => { setInviteInput(value); setInvitation(null); }} placeholder="nsi_... 或完整邀请链接" />
            <Input label="母舰地址" value={mothershipUrl} onChange={setMothershipUrl} placeholder="https://..." />
          </div>
          <button type="button" onClick={() => void inspectInvitation()} disabled={busy === 'inspect'} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm disabled:opacity-50" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.24)' }}>
            {busy === 'inspect' ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />} 验证邀请
          </button>

          {invitation && (
            <div className="pt-4 space-y-5" style={{ borderTop: '1px solid var(--border)' }}>
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs" style={{ color: 'var(--text-dim)' }}>即将加入</p>
                  <h3 className="text-lg font-semibold mt-1" style={{ color: 'var(--text-primary)' }}>{invitation.space_name}</h3>
                  <p className="text-sm mt-1 max-w-2xl" style={{ color: 'var(--text-secondary)' }}>{invitation.description || '该空间暂无补充说明'}</p>
                  <p className="text-xs mt-2" style={{ color: 'var(--text-dim)' }}>管理方：{invitation.owner_label || '未标注'} · 邀请有效至 {formatDate(invitation.invite_expires_at)}</p>
                </div>
                <span className="text-xs px-2.5 py-1.5 rounded-full" style={{ color: 'var(--neon-green)', background: 'rgba(173,255,0,0.07)' }}>邀请有效</span>
              </div>
              <Input label="本机哨站名称" value={deviceName} onChange={setDeviceName} placeholder="例如：张同学的课程哨站" />
              <GrantEditor sources={sources} categories={categories} sourceRefs={sourceRefs} shareEvidence={shareEvidence} shareCalibrationStats={shareCalibrationStats} expiresDays={expiresDays}
                onCategories={value => { setCategories(value); invalidatePreview(); }} onSourceRefs={value => { setSourceRefs(value); invalidatePreview(); }}
                onShareEvidence={value => { setShareEvidence(value); invalidatePreview(); }}
                onShareCalibrationStats={value => { setShareCalibrationStats(value); invalidatePreview(); }} onExpiresDays={setExpiresDays} />
              <Preview preview={preview} />
              <div className="flex flex-wrap items-center gap-3">
                <button type="button" onClick={() => void loadPreview()} disabled={busy === 'preview'} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm disabled:opacity-50" style={{ color: 'var(--text-primary)', border: '1px solid var(--border)' }}>
                  {busy === 'preview' ? <Loader2 size={15} className="animate-spin" /> : <Eye size={15} />} 查看共享预览
                </button>
                <button type="button" onClick={() => void joinSpace()} disabled={!preview || !deviceName.trim() || busy === 'join'} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-40" style={{ color: '#001012', background: 'var(--neon-cyan)' }}>
                  {busy === 'join' ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />} 确认授权并加入
                </button>
              </div>
            </div>
          )}
        </section>
      ) : (
        <>
          <section className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
            <div className="px-5 py-4 flex flex-wrap items-start justify-between gap-4" style={{ background: 'rgba(255,255,255,0.018)' }}>
              <div>
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full" style={{ background: membership.status === 'active' ? 'var(--neon-green)' : 'var(--neon-orange)' }} />
                  <h3 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>{membership.space_name || config.mothership_space_name}</h3>
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{membership.status === 'active' ? '共享中' : membership.status === 'paused' ? '已暂停' : '已到期'}</span>
                </div>
                <p className="text-xs mt-2" style={{ color: 'var(--text-dim)' }}>管理方：{membership.owner_label || '未标注'} · 授权至 {formatDate(membership.expires_at)}</p>
              </div>
              <div className="flex items-center gap-2">
                {membership.status === 'active' ? (
                  <button type="button" onClick={() => void setMembershipState('paused')} disabled={busy === 'paused'} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--neon-orange)', border: '1px solid var(--border)' }}><Pause size={13} /> 暂停共享</button>
                ) : membership.status === 'paused' ? (
                  <button type="button" onClick={() => void setMembershipState('active')} disabled={busy === 'active'} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--neon-green)', border: '1px solid var(--border)' }}><Play size={13} /> 恢复共享</button>
                ) : null}
                <button type="button" onClick={() => void syncNow()} disabled={membership.status !== 'active' || busy === 'sync'} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs disabled:opacity-40" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>{busy === 'sync' ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />} 立即同步</button>
                <button type="button" onClick={() => { setEditingGrant(value => !value); setPreview(null); }} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--neon-cyan)', border: '1px solid var(--border)' }}>修改授权</button>
              </div>
            </div>
            <div className="px-5 py-4 flex flex-wrap gap-x-8 gap-y-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
              <span>分类：{membership.categories.map(item => CATEGORY_LABELS[item]).join('、') || '未授权'}</span>
              <span>来源：{membership.source_refs.length} 个已选来源</span>
              <span>证据：{membership.share_evidence ? '脱敏片段' : '仅摘要'}</span>
              <span>校准：{membership.share_calibration_stats ? '仅匿名统计' : '不共享'}</span>
            </div>
            {editingGrant && (
              <div className="px-5 py-5 space-y-4" style={{ borderTop: '1px solid var(--border)' }}>
                <GrantEditor sources={sources} categories={categories} sourceRefs={sourceRefs} shareEvidence={shareEvidence} shareCalibrationStats={shareCalibrationStats}
                  onCategories={value => { setCategories(value); invalidatePreview(); }} onSourceRefs={value => { setSourceRefs(value); invalidatePreview(); }}
                  onShareEvidence={value => { setShareEvidence(value); invalidatePreview(); }}
                  onShareCalibrationStats={value => { setShareCalibrationStats(value); invalidatePreview(); }} />
                <Preview preview={preview} />
                <div className="flex gap-3">
                  <button type="button" onClick={() => void loadPreview()} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ color: 'var(--text-primary)', border: '1px solid var(--border)' }}><Eye size={14} /> 重新预览</button>
                  <button type="button" onClick={() => void updateGrant()} disabled={!preview || busy === 'grant'} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: '#001012', background: 'var(--neon-cyan)' }}><Check size={14} /> 保存新授权</button>
                </div>
              </div>
            )}
          </section>

          {pendingEvidence.length > 0 && (
            <section className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(255,92,0,0.24)' }}>
              <div className="px-5 py-4 flex items-center gap-2" style={{ background: 'rgba(255,92,0,0.05)' }}>
                <Eye size={16} style={{ color: 'var(--neon-orange)' }} />
                <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>原文核验申请</h3>
                <span className="ml-auto text-xs" style={{ color: 'var(--neon-orange)' }}>{pendingEvidence.length} 条待决定</span>
              </div>
              {pendingEvidence.map(item => (
                <div key={item.id} className="px-5 py-4 space-y-3" style={{ borderTop: '1px solid var(--border)' }}>
                  <div><p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{item.summary}</p><p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>申请理由：{item.reason}</p></div>
                  <div className="rounded-lg p-3 text-xs whitespace-pre-wrap max-h-36 overflow-auto" style={{ color: 'var(--text-secondary)', background: 'rgba(0,0,0,0.3)' }}>{item.local_available ? item.local_content : '本机已找不到对应原文'}</div>
                  <p className="text-xs" style={{ color: 'var(--text-dim)' }}>只有点击“批准本次原文”后，上述内容才会发送；授权不会自动延续到下一条。</p>
                  {confirmEvidenceId === item.id ? (
                    <div className="rounded-lg px-3 py-3 flex flex-wrap items-center gap-2" style={{ background: 'rgba(255,92,0,0.06)', border: '1px solid rgba(255,92,0,0.18)' }}><p className="text-xs mr-auto" style={{ color: 'var(--text-primary)' }}>确认只发送上方这一条原文？不会授权其他消息。</p><button type="button" onClick={() => void respondEvidence(item.id, 'approved')} disabled={busy === `evidence-${item.id}`} className="px-3 py-1.5 rounded-lg text-xs disabled:opacity-40" style={{ color: '#071000', background: 'var(--neon-green)' }}>确认发送这条原文</button><button type="button" onClick={() => setConfirmEvidenceId(null)} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>返回</button></div>
                  ) : (
                    <div className="flex gap-2"><button type="button" onClick={() => setConfirmEvidenceId(item.id)} disabled={!item.local_available || busy === `evidence-${item.id}`} className="px-3 py-1.5 rounded-lg text-xs disabled:opacity-40" style={{ color: 'var(--neon-green)', border: '1px solid var(--border)' }}>批准本次原文</button><button type="button" onClick={() => void respondEvidence(item.id, 'denied')} disabled={busy === `evidence-${item.id}`} className="px-3 py-1.5 rounded-lg text-xs" style={{ color: 'var(--neon-orange)', border: '1px solid var(--border)' }}>拒绝</button></div>
                  )}
                </div>
              ))}
            </section>
          )}

          <section className="rounded-xl p-5" style={{ border: '1px solid var(--border)' }}>
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>退出与数据控制</h3>
            <p className="text-xs mt-1 mb-4" style={{ color: 'var(--text-secondary)' }}>删除母舰数据不会删除本机消息；撤销会同时让本机成员密钥失效。</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => void deleteSharedData()} disabled={!!busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: 'var(--neon-orange)', border: '1px solid var(--border)' }}><Trash2 size={14} /> 删除已共享数据</button>
              <button type="button" onClick={() => void revokeMembership()} disabled={!!busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: '#ff9b9b', border: '1px solid rgba(255,80,80,0.22)' }}><Unplug size={14} /> 撤销授权并退出</button>
              <button type="button" onClick={() => void loadMembership()} disabled={!!busy} className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs disabled:opacity-40" style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)' }}><RefreshCw size={14} /> 刷新状态</button>
            </div>
          </section>
        </>
      )}

      <details className="rounded-xl" style={{ border: '1px solid var(--border)' }}>
        <summary className="cursor-pointer list-none px-5 py-4 flex items-center gap-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
          <RadioTower size={15} /> 管理员工作台接入 <ChevronDown size={14} className="ml-auto" />
        </summary>
        <div className="px-5 pb-5 space-y-4">
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>只有班委、社团负责人或老师等空间管理员需要填写。管理员密钥不能代替学生授权。</p>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <Input label="母舰地址" value={mothershipUrl} onChange={setMothershipUrl} placeholder="https://..." />
            <Input label={config.mothership_admin_token_set ? '管理员密钥（已配置，留空不变）' : '管理员密钥'} value={adminToken} onChange={setAdminToken} placeholder="仅保存在本机" type="password" />
          </div>
          <button type="button" onClick={() => void saveAdminAccess()} disabled={busy === 'admin'} className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm disabled:opacity-50" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.24)' }}>
            {busy === 'admin' ? <Loader2 size={15} className="animate-spin" /> : <KeyRound size={15} />} 保存管理员接入
          </button>
        </div>
      </details>
    </div>
  );
}

function GrantEditor({
  sources, categories, sourceRefs, shareEvidence, shareCalibrationStats, expiresDays,
  onCategories, onSourceRefs, onShareEvidence, onShareCalibrationStats, onExpiresDays,
}: {
  sources: SourceOption[]; categories: Category[]; sourceRefs: string[]; shareEvidence: boolean; shareCalibrationStats: boolean; expiresDays?: number;
  onCategories: (value: Category[]) => void; onSourceRefs: (value: string[]) => void;
  onShareEvidence: (value: boolean) => void; onShareCalibrationStats: (value: boolean) => void; onExpiresDays?: (value: number) => void;
}) {
  const toggleCategory = (category: Category) => onCategories(categories.includes(category) ? categories.filter(item => item !== category) : [...categories, category]);
  const toggleSource = (ref: string) => onSourceRefs(sourceRefs.includes(ref) ? sourceRefs.filter(item => item !== ref) : [...sourceRefs, ref]);
  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium mb-2" style={{ color: 'var(--text-primary)' }}>允许共享的分类</p>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(CATEGORY_LABELS) as Category[]).map(category => <Toggle key={category} active={categories.includes(category)} label={CATEGORY_LABELS[category]} onClick={() => toggleCategory(category)} />)}
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between gap-3 mb-2">
          <div><p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>允许共享的群与来源</p><p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>未勾选的来源不会上传；以后新增来源也不会自动获得授权。</p></div>
          {sources.length > 0 && <button type="button" onClick={() => onSourceRefs(sourceRefs.length === sources.length ? [] : sources.map(item => item.ref))} className="text-xs shrink-0" style={{ color: 'var(--neon-cyan)' }}>{sourceRefs.length === sources.length ? '全部取消' : '选择全部当前来源'}</button>}
        </div>
        {sources.length === 0 ? <p className="text-xs rounded-lg px-3 py-3" style={{ color: 'var(--text-dim)', background: 'rgba(255,255,255,0.025)' }}>本机还没有消息来源。可以先加入空间，但在选择来源前不会上传情报。</p> : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {sources.map(source => (
              <label key={source.ref} className="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer" style={{ border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                <input type="checkbox" checked={sourceRefs.includes(source.ref)} onChange={() => toggleSource(source.ref)} />
                <span className="min-w-0"><span className="block text-sm truncate">{source.label}</span><span className="block text-xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{source.source_type} · {source.message_count} 条本机消息</span></span>
              </label>
            ))}
          </div>
        )}
      </div>
      <label className="flex items-start justify-between gap-4 rounded-lg px-4 py-3" style={{ border: '1px solid var(--border)' }}>
        <span><span className="block text-sm" style={{ color: 'var(--text-primary)' }}>允许脱敏证据片段</span><span className="block text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>默认只共享摘要。开启后最多附带 300 字脱敏片段；完整原文仍需再次批准。</span></span>
        <input type="checkbox" checked={shareEvidence} onChange={event => onShareEvidence(event.target.checked)} />
      </label>
      <label className="flex items-start justify-between gap-4 rounded-lg px-4 py-3" style={{ border: '1px solid var(--border)' }}>
        <span><span className="block text-sm" style={{ color: 'var(--text-primary)' }}>共享匿名校准统计</span><span className="block text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>只上传复核数量、准确率、混淆矩阵与提示词版本；不包含消息正文、群名或发送者。纠错样本仍须逐条批准。</span></span>
        <input type="checkbox" checked={shareCalibrationStats} onChange={event => onShareCalibrationStats(event.target.checked)} />
      </label>
      {onExpiresDays && <label className="block"><span className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>授权有效期</span><select value={expiresDays} onChange={event => onExpiresDays(Number(event.target.value))} className="px-3 py-2 rounded-lg text-sm outline-none" style={{ color: 'var(--text-primary)', background: 'var(--bg-surface)', border: '1px solid var(--border)' }}><option value={7}>7 天</option><option value={30}>30 天</option><option value={90}>90 天</option><option value={180}>180 天</option></select></label>}
    </div>
  );
}

function Preview({ preview }: { preview: PreviewData | null }) {
  if (!preview) return null;
  return (
    <div className="rounded-xl overflow-hidden" style={{ border: '1px solid rgba(0,242,255,0.18)' }}>
      <div className="px-4 py-3 flex flex-wrap items-center gap-3" style={{ background: 'rgba(0,242,255,0.045)' }}><Eye size={15} style={{ color: 'var(--neon-cyan)' }} /><span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>母舰将看到 {preview.count} 条现有情报</span><span className="text-xs ml-auto" style={{ color: 'var(--text-secondary)' }}>字段：{preview.shared_fields.join('、')}</span></div>
      <div className="px-4 py-3 space-y-2">
        {preview.items.length === 0 ? <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>当前授权范围没有可共享的本机消息。</p> : preview.items.map(item => <div key={item.external_id} className="flex gap-3 text-xs"><span style={{ color: 'var(--neon-purple)' }}>{item.category}</span><span className="min-w-0"><span className="block truncate" style={{ color: 'var(--text-primary)' }}>{item.summary}</span><span className="block mt-0.5" style={{ color: 'var(--text-dim)' }}>{item.source_name || '未知来源'}{item.evidence_excerpt ? ` · 证据：${item.evidence_excerpt}` : ''}</span></span></div>)}
        <p className="text-xs pt-2" style={{ color: 'var(--neon-green)', borderTop: '1px solid var(--border)' }}><ShieldCheck size={12} className="inline mr-1" />始终不共享：{preview.never_shared.join('、')}</p>
      </div>
    </div>
  );
}

function Toggle({ active, label, onClick }: { active: boolean; label: string; onClick: () => void }) {
  return <button type="button" aria-pressed={active} onClick={onClick} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs" style={{ color: active ? 'var(--neon-cyan)' : 'var(--text-secondary)', background: active ? 'rgba(0,242,255,0.07)' : 'transparent', border: '1px solid var(--border)' }}>{active ? <Check size={12} /> : <X size={12} />}{label}</button>;
}

function Input({ label, value, onChange, placeholder, type = 'text' }: { label: string; value: string; onChange: (value: string) => void; placeholder: string; type?: string }) {
  return <label className="block"><span className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>{label}</span><input type={type} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder} className="w-full px-3 py-2.5 rounded-lg text-sm outline-none" style={{ color: 'var(--text-primary)', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)' }} /></label>;
}
