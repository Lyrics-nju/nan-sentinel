import { useState, useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';

export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const [checking, setChecking] = useState(true);
  const [registered, setRegistered] = useState<boolean | null>(null);
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const checkAll = async () => {
      if (cancelled) return;

      // 1) 检查是否已注册
      try {
        const userRes = await fetch('/api/user');
        if (cancelled) return;
        const userData = await userRes.json();
        if (!userData.registered) {
          setRegistered(false);
          setChecking(false);
          return;
        }
        setRegistered(true);
      } catch {
        // 后端未就绪，继续重试
        if (!cancelled) setTimeout(checkAll, 1500);
        return;
      }

      // 2) 检查登录状态
      const startTime = Date.now();
      const maxDuration = 15000;

      const checkLogin = async () => {
        if (cancelled) return;
        try {
          const res = await fetch('/api/login/status');
          if (!res.ok) {
            if (!cancelled && Date.now() - startTime < maxDuration) {
              setTimeout(checkLogin, 1500);
            } else if (!cancelled) {
              setLoggedIn(false);
              setChecking(false);
            }
            return;
          }
          const data = await res.json();
          if (cancelled) return;

          if (data.status === 'logged_in') {
            setLoggedIn(true);
            setChecking(false);
            return;
          }
          if (data.status === 'offline' || data.status === 'waiting_scan') {
            if (Date.now() - startTime < 5000) {
              setTimeout(checkLogin, 2000);
            } else {
              setLoggedIn(false);
              setChecking(false);
            }
            return;
          }
        } catch {
          // 网络错误，重试
        }
        if (!cancelled && Date.now() - startTime < maxDuration) {
          setTimeout(checkLogin, 1500);
        } else if (!cancelled) {
          setLoggedIn(false);
          setChecking(false);
        }
      };

      checkLogin();
    };

    checkAll();
    return () => { cancelled = true; };
  }, []);

  if (checking) {
    return (
      <div className="min-h-screen flex items-center justify-center"
           style={{ background: 'linear-gradient(180deg, #000000 0%, #050509 100%)' }}>
        <div className="flex flex-col items-center gap-4">
          <Loader2 size={32} className="animate-spin" style={{ color: 'var(--neon-cyan)' }} />
          <p className="text-sm font-mono" style={{ color: 'var(--text-secondary)' }}>正在验证身份...</p>
        </div>
      </div>
    );
  }

  // 未注册 → 跳转注册页
  if (registered === false) {
    return <Navigate to="/register" replace />;
  }

  // 未登录 → 跳转登录页
  if (!loggedIn) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
