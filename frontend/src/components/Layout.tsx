import { useState, useEffect, useRef } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import SearchModal from './SearchModal';

export default function Layout() {
  const [showSearch, setShowSearch] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const location = useLocation();

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setShowSearch(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, []);

  useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [location.pathname, location.search]);

  return (
    <div className="app-shell" style={{
      width: '100%',
      height: '100%',
      display: 'flex',
      background: 'linear-gradient(180deg, #000 0%, #050509 100%)',
    }}>
      <Sidebar onSearch={() => setShowSearch(true)} />
      <main ref={mainRef} className="app-main" style={{
        flex: 1,
        overflow: 'auto',
        minWidth: 0,
      }}>
        <Outlet />
      </main>
      {showSearch && <SearchModal onClose={() => setShowSearch(false)} />}
    </div>
  );
}
