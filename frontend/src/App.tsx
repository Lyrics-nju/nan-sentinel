import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import DashboardPage from './components/DashboardPage';
import MessagesPage from './components/MessagesPage';
import BookmarksPage from './components/BookmarksPage';
import ReportsPage from './components/ReportsPage';
import SettingsPage from './components/SettingsPage';
import MothershipPage from './components/MothershipPage';

function App() {
  // 初始化主题
  useEffect(() => {
    try {
      const theme = localStorage.getItem('theme');
      if (theme) document.documentElement.setAttribute('data-theme', theme);
    } catch { /* localStorage may be unavailable in restricted webviews. */ }
  }, []);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/messages" element={<MessagesPage />} />
          <Route path="/bookmarks" element={<BookmarksPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/mothership" element={<MothershipPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
