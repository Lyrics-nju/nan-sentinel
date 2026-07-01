import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard, MessageSquare, Star, FileText, Settings, Zap, Search, RadioTower,
} from 'lucide-react';
import UserAvatar from './UserAvatar';
import NotificationBell from './NotificationBell';

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '仪表盘' },
  { to: '/messages', icon: MessageSquare, label: '消息中心' },
  { to: '/bookmarks', icon: Star, label: '我的收藏' },
  { to: '/reports', icon: FileText, label: '周报' },
  { to: '/mothership', icon: RadioTower, label: '情报母舰' },
  { to: '/settings', icon: Settings, label: '设置' },
];

export default function Sidebar({ onSearch }: { onSearch?: () => void }) {
  return (
    <aside className="sidebar-nav flex flex-col shrink-0 overflow-hidden"
         style={{ background: 'rgba(5,5,9,0.95)', borderRight: '1px solid var(--border)', height: '100%' }}>
      {/* Logo */}
      <div className="sidebar-brand px-5 py-5 flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl flex items-center justify-center"
             style={{ background: 'rgba(0, 242, 255, 0.12)', border: '1px solid rgba(0, 242, 255, 0.25)' }}>
          <Zap size={20} style={{ color: 'var(--neon-cyan)' }} />
        </div>
        <div className="sidebar-logo-copy">
          <h1 className="text-base font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
            Nan Sentinel 南哨
          </h1>
          <p className="text-[11px] font-mono" style={{ color: 'var(--text-dim)' }}>v0.4.0 · 哨站</p>
        </div>
      </div>

      {/* 搜索 + 通知 */}
      <div className="sidebar-tools px-3 pb-2 flex items-center gap-1">
        <button onClick={onSearch}
          className="flex-1 flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition-colors hover:bg-white/[0.04]"
          style={{ color: 'var(--text-dim)', border: '1px solid var(--border)' }}>
          <Search size={14} />
          <span className="sidebar-search-copy flex-1 text-left text-xs">搜索</span>
          <kbd className="sidebar-search-copy text-[10px] px-1.5 py-0.5 rounded font-mono" style={{ background: 'rgba(255,255,255,0.06)' }}>⌘K</kbd>
        </button>
        <NotificationBell />
      </div>

      {/* 导航 */}
      <nav className="sidebar-links flex-1 px-3 py-2 space-y-1">
        {navItems.map(item => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-3 rounded-xl text-[15px] font-medium transition-all duration-200 ${
                  isActive ? '' : 'hover:bg-white/[0.04]'
                }`
              }
              style={({ isActive }) => ({
                background: isActive ? 'rgba(0, 242, 255, 0.08)' : 'transparent',
                color: isActive ? 'var(--neon-cyan)' : 'var(--text-secondary)',
                border: isActive ? '1px solid rgba(0, 242, 255, 0.15)' : '1px solid transparent',
              })}
            >
              <Icon size={20} />
              <span className="sidebar-label">{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      {/* 底部用户区 */}
      <div className="sidebar-user px-4 py-4" style={{ borderTop: '1px solid var(--border)' }}>
        <UserAvatar />
      </div>
    </aside>
  );
}
