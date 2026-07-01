import { useCallback, useEffect, useState } from 'react';
import { Bell, BrainCircuit, Check, Cpu, Database, Loader2, Palette, RadioTower, Save, Upload } from 'lucide-react';
import MothershipConsentPanel from './MothershipConsentPanel';
import CalibrationCenter from './CalibrationCenter';

const tabs = [
  { key: 'llm', label: 'LLM 配置', icon: Cpu },
  { key: 'calibration', label: '本地校准', icon: BrainCircuit },
  { key: 'sources', label: '数据来源', icon: Database },
  { key: 'mothership', label: '情报母舰', icon: RadioTower },
  { key: 'notify', label: '应用内提醒', icon: Bell },
  { key: 'theme', label: '主题外观', icon: Palette },
] as const;

type TabKey = typeof tabs[number]['key'];
type ThemeKey = 'cyber' | 'classic';
type Category = 'A' | 'B' | 'C';

interface AppConfig {
  llm_base_url?: string;
  llm_model?: string;
  llm_api_key_set?: boolean;
  include_private?: boolean;
  mothership_enabled?: boolean;
  mothership_url?: string;
  mothership_node_name?: string;
  mothership_node_token_set?: boolean;
  mothership_admin_token_set?: boolean;
  mothership_share_evidence?: boolean;
  mothership_share_calibration_stats?: boolean;
  mothership_space_id?: string;
  mothership_space_name?: string;
  mothership_owner_label?: string;
  mothership_membership_status?: string;
  mothership_categories?: Category[];
  mothership_source_refs?: string[];
  mothership_expires_at?: string;
}

interface ImportResult {
  imported: number;
  filtered: number;
  duplicates: number;
}

const THEMES: Record<ThemeKey, { name: string; colors: string[] }> = {
  cyber: { name: '赛博深色', colors: ['#00F2FF', '#BB00FF', '#ADFF00', '#FF5C00'] },
  classic: { name: '经典暗色', colors: ['#818CF8', '#A78BFA', '#34D399', '#FB923C'] },
};

function getSavedTheme(): ThemeKey {
  try { return (localStorage.getItem('theme') as ThemeKey) || 'cyber'; } catch { return 'cyber'; }
}

function getNotificationPrefs(): Record<Category, boolean> {
  try {
    const saved = JSON.parse(localStorage.getItem('notificationCategories') || '[]') as string[];
    return { A: saved.includes('A') || saved.length === 0, B: saved.includes('B'), C: saved.includes('C') };
  } catch {
    return { A: true, B: false, C: false };
  }
}

function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let value = '';
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (char === '"' && quoted && next === '"') {
      value += '"';
      index += 1;
    } else if (char === '"') {
      quoted = !quoted;
    } else if (char === ',' && !quoted) {
      row.push(value);
      value = '';
    } else if ((char === '\n' || char === '\r') && !quoted) {
      if (char === '\r' && next === '\n') index += 1;
      row.push(value);
      if (row.some(cell => cell.trim())) rows.push(row);
      row = [];
      value = '';
    } else {
      value += char;
    }
  }
  row.push(value);
  if (row.some(cell => cell.trim())) rows.push(row);
  const headers = rows.shift()?.map(header => header.trim().replace(/^\uFEFF/, '')) || [];
  return rows.map(cells => Object.fromEntries(headers.map((header, index) => [header, cells[index]?.trim() || ''])));
}

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabKey>(() => {
    const requested = new URLSearchParams(window.location.search).get('tab');
    return tabs.some(tab => tab.key === requested) ? requested as TabKey : 'llm';
  });
  const [config, setConfig] = useState<AppConfig>({});
  const [apiKeyDraft, setApiKeyDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [theme, setTheme] = useState<ThemeKey>(getSavedTheme);
  const [notificationPrefs, setNotificationPrefs] = useState(getNotificationPrefs);
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  const handleMothershipConfigChange = useCallback((partial: Partial<AppConfig>) => {
    setConfig(previous => ({ ...previous, ...partial }));
  }, []);

  useEffect(() => {
    fetch('/api/config')
      .then(async response => {
        if (!response.ok) throw new Error('无法读取配置');
        return response.json() as Promise<AppConfig>;
      })
      .then(setConfig)
      .catch(err => setError(err instanceof Error ? err.message : '无法读取配置'))
      .finally(() => setLoading(false));
  }, []);

  const saveConfig = async () => {
    setSaving(true);
    setError('');
    try {
      const response = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          llm_api_key: apiKeyDraft.trim() || undefined,
          llm_base_url: config.llm_base_url,
          llm_model: config.llm_model,
          include_private: !!config.include_private,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || '保存失败');
      if (apiKeyDraft.trim()) setConfig(previous => ({ ...previous, llm_api_key_set: true }));
      setApiKeyDraft('');
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const switchTheme = (nextTheme: ThemeKey) => {
    setTheme(nextTheme);
    document.documentElement.setAttribute('data-theme', nextTheme);
    try { localStorage.setItem('theme', nextTheme); } catch { /* storage may be unavailable */ }
  };

  const toggleNotification = (category: Category) => {
    setNotificationPrefs(previous => {
      const next = { ...previous, [category]: !previous[category] };
      try {
        localStorage.setItem('notificationCategories', JSON.stringify((Object.keys(next) as Category[]).filter(key => next[key])));
      } catch { /* storage may be unavailable */ }
      window.dispatchEvent(new Event('notification-preferences-changed'));
      return next;
    });
  };

  const importCsv = async (file: File) => {
    setImporting(true);
    setImportResult(null);
    setError('');
    try {
      const rows = parseCsv(await file.text());
      if (!rows.length || !Object.prototype.hasOwnProperty.call(rows[0], 'content')) {
        throw new Error('CSV 必须包含 content 列');
      }
      const messages = rows.slice(0, 500).map((item, index) => ({
        id: item.id || String(index + 1),
        channel_id: item.channel_id || '',
        channel_name: item.channel_name || item.group_name || '文件导入',
        sender_id: item.sender_id || '',
        sender_name: item.sender_name || '',
        content: item.content,
        created_at: item.created_at || '',
        source_url: item.source_url || '',
      }));
      const response = await fetch('/api/sources/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_type: 'csv', source_name: file.name, messages }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '导入失败');
      setImportResult(data as ImportResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入失败');
    } finally {
      setImporting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 size={32} className="animate-spin" style={{ color: 'var(--neon-cyan)' }} aria-label="正在加载设置" />
      </div>
    );
  }

  return (
    <div className="page-shell animate-fade-in">
      <div className="page-header">
        <div>
          <h1 className="text-2xl font-bold" style={{ color: 'var(--text-primary)' }}>设置</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-secondary)' }}>配置模型、数据来源和提醒偏好</p>
        </div>
        {(activeTab === 'llm' || activeTab === 'sources') && <button onClick={saveConfig} disabled={saving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-medium transition-all hover:brightness-110 disabled:opacity-50"
          style={{ background: saved ? 'rgba(173,255,0,0.1)' : 'rgba(0,242,255,0.1)', color: saved ? 'var(--neon-green)' : 'var(--neon-cyan)', border: `1px solid ${saved ? 'rgba(173,255,0,0.2)' : 'rgba(0,242,255,0.2)'}` }}>
          {saved ? <><Check size={16} /> 已保存</> : saving ? <><Loader2 size={16} className="animate-spin" /> 保存中...</> : <><Save size={16} /> 保存设置</>}
        </button>}
      </div>

      {error && <div role="alert" className="mb-5 px-4 py-3 rounded-xl text-sm" style={{ color: '#ff8a8a', background: 'rgba(255,80,80,0.08)' }}>{error}</div>}

      <div className="settings-layout">
        <div className="settings-tabs glass rounded-2xl overflow-hidden p-2">
            {tabs.map(tab => {
              const Icon = tab.icon;
              const active = activeTab === tab.key;
              return (
                <button key={tab.key} onClick={() => { setActiveTab(tab.key); const url = new URL(window.location.href); url.searchParams.set('tab', tab.key); window.history.replaceState({}, '', url); }} aria-pressed={active}
                  className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-medium transition-colors ${active ? '' : 'hover:bg-white/[0.03]'}`}
                  style={{ color: active ? 'var(--neon-cyan)' : 'var(--text-secondary)', background: active ? 'rgba(0,242,255,0.06)' : 'transparent' }}>
                  <Icon size={18} /> {tab.label}
                </button>
              );
            })}
        </div>

        <div className="flex-1 min-w-0">
          <div className="glass rounded-2xl p-4 sm:p-6 space-y-6">
            {activeTab === 'llm' && (
              <>
                <h2 className="text-base font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>LLM 模型配置</h2>
                <Field id="settings-base-url" label="API 基础 URL" value={config.llm_base_url || ''} onChange={value => setConfig(previous => ({ ...previous, llm_base_url: value }))} placeholder="https://api.deepseek.com" type="url" />
                <Field id="settings-api-key" label="API Key" value={apiKeyDraft} onChange={setApiKeyDraft} placeholder={config.llm_api_key_set ? '已保存，留空表示不修改' : 'sk-...'} type="password" />
                <Field id="settings-model" label="模型名称" value={config.llm_model || ''} onChange={value => setConfig(previous => ({ ...previous, llm_model: value }))} placeholder="deepseek-chat" />
                <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>Key 不会从后端返回到页面，修改 URL 或模型时也不会覆盖现有 Key。</p>
              </>
            )}

            {activeTab === 'sources' && (
              <>
                <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>数据来源</h2>
                <section className="rounded-xl p-4" style={{ border: '1px solid var(--border)' }}>
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>QQ / NapCat</p>
                      <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>默认只处理群聊；私聊必须主动开启。</p>
                    </div>
                    <label className="flex items-center gap-2 text-sm" style={{ color: 'var(--text-secondary)' }}>
                      <input type="checkbox" checked={!!config.include_private} onChange={event => setConfig(previous => ({ ...previous, include_private: event.target.checked }))} />
                      包含私聊
                    </label>
                  </div>
                </section>

                <section className="rounded-xl p-4" style={{ border: '1px solid var(--border)' }}>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>CSV 历史消息导入</p>
                      <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>必需列：content。可选：id、channel_name、sender_name、created_at、source_url。</p>
                    </div>
                    <label className="cursor-pointer flex items-center gap-2 px-4 py-2 rounded-lg text-sm" style={{ color: 'var(--neon-cyan)', border: '1px solid rgba(0,242,255,0.2)' }}>
                      {importing ? <Loader2 size={15} className="animate-spin" /> : <Upload size={15} />}
                      {importing ? '导入中' : '选择文件'}
                      <input type="file" accept=".csv,text/csv" className="sr-only" disabled={importing} onChange={event => { const file = event.target.files?.[0]; if (file) void importCsv(file); event.target.value = ''; }} />
                    </label>
                  </div>
                  {importResult && <p className="text-xs mt-3" style={{ color: 'var(--neon-green)' }}>已导入 {importResult.imported} 条，过滤 {importResult.filtered} 条，重复 {importResult.duplicates} 条。</p>}
                </section>

                <section className="rounded-xl p-4" style={{ border: '1px solid var(--border)' }}>
                  <p className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>标准 Webhook</p>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>本机应用开放统一导入接口 <code>/api/sources/import</code>，可接飞书、钉钉、邮件或 RSS 转换器。桌面版仅监听本机，不会暴露到局域网。</p>
                </section>
              </>
            )}

            {activeTab === 'calibration' && <CalibrationCenter />}

            {activeTab === 'mothership' && (
              <MothershipConsentPanel
                config={config}
                onConfigChange={handleMothershipConfigChange}
              />
            )}

            {activeTab === 'notify' && (
              <>
                <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>应用内提醒</h2>
                <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>选择哪些分类进入右上角提醒中心。重要信息默认开启。</p>
                {([['A', '重要信息'], ['B', '校园讨论'], ['C', '二手与需求']] as const).map(([key, label]) => (
                  <label key={key} className="flex items-center justify-between rounded-xl px-4 py-3" style={{ border: '1px solid var(--border)', color: 'var(--text-primary)' }}>
                    <span className="text-sm">{label}</span>
                    <input type="checkbox" checked={notificationPrefs[key]} onChange={() => toggleNotification(key)} />
                  </label>
                ))}
              </>
            )}

            {activeTab === 'theme' && (
              <>
                <h2 className="text-base font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>主题外观</h2>
                <div className="grid grid-cols-2 gap-4">
                  {(Object.keys(THEMES) as ThemeKey[]).map(key => (
                    <ThemeCard key={key} name={THEMES[key].name} active={theme === key} colors={THEMES[key].colors} onClick={() => switchTheme(key)} />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ id, label, value, onChange, placeholder, type = 'text' }: {
  id: string; label: string; value: string; onChange: (value: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>{label}</label>
      <input id={id} type={type} value={value} onChange={event => onChange(event.target.value)} placeholder={placeholder}
        className="w-full px-4 py-2.5 rounded-xl text-sm outline-none transition-colors"
        style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
    </div>
  );
}

function ThemeCard({ name, active, colors, onClick }: { name: string; active: boolean; colors: string[]; onClick: () => void }) {
  return (
    <button type="button" className="rounded-xl p-4 cursor-pointer transition-all text-left" onClick={onClick} aria-pressed={active}
      style={{ border: active ? '1px solid rgba(0,242,255,0.3)' : '1px solid var(--border)', background: active ? 'rgba(0,242,255,0.04)' : 'transparent' }}>
      <span className="flex gap-1.5 mb-3" aria-hidden="true">
        {colors.map(color => <span key={color} className="w-5 h-5 rounded-full" style={{ background: color }} />)}
      </span>
      <span className="text-sm font-medium" style={{ color: active ? 'var(--neon-cyan)' : 'var(--text-secondary)' }}>{name}</span>
    </button>
  );
}
