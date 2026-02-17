import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from './components/Layout';
import { PrintersPage } from './pages/PrintersPage';
import { ArchivesPage } from './pages/ArchivesPage';
import { QueuePage } from './pages/QueuePage';
import { StatsPage } from './pages/StatsPage';
import { SettingsPage } from './pages/SettingsPage';
import { ProfilesPage } from './pages/ProfilesPage';
import { MaintenancePage } from './pages/MaintenancePage';
import { ProjectsPage } from './pages/ProjectsPage';
import { ProjectDetailPage } from './pages/ProjectDetailPage';
import { FileManagerPage } from './pages/FileManagerPage';
import { CameraPage } from './pages/CameraPage';
import { StreamOverlayPage } from './pages/StreamOverlayPage';
import { ExternalLinkPage } from './pages/ExternalLinkPage';
import InventoryPage from './pages/InventoryPage';
import { SystemInfoPage } from './pages/SystemInfoPage';
import { LoginPage } from './pages/LoginPage';
import { SetupPage } from './pages/SetupPage';
import { useWebSocket } from './hooks/useWebSocket';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,
      retry: 1,
    },
  },
});

function WebSocketProvider({ children }: { children: React.ReactNode }) {
  useWebSocket();
  return <>{children}</>;
}

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { authEnabled, loading, user } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  if (authEnabled && !user) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { authEnabled, loading, user, isAdmin } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  // If auth is not enabled, allow access (backward compatibility)
  if (!authEnabled) {
    return <>{children}</>;
  }

  // If auth is enabled but no user, redirect to login
  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // If user is not admin, redirect to home
  if (!isAdmin) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}

function SetupRoute({ children }: { children: React.ReactNode }) {
  const { authEnabled, loading } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  // If auth is already enabled, redirect to login
  // Otherwise, allow access to setup page (even if setup was completed before)
  // This allows users to enable auth later if they skipped it during initial setup
  if (authEnabled) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <BrowserRouter>
              <Routes>
                {/* Setup page - only accessible if auth not enabled */}
                <Route path="/setup" element={<SetupRoute><SetupPage /></SetupRoute>} />

                {/* Login page */}
                <Route path="/login" element={<LoginPage />} />

                {/* Camera page - standalone, no layout, no WebSocket (doesn't need real-time updates) */}
                <Route path="/camera/:printerId" element={<CameraPage />} />

                {/* Stream overlay page - standalone for OBS/streaming embeds, no auth required */}
                <Route path="/overlay/:printerId" element={<StreamOverlayPage />} />

                {/* Main app with WebSocket for real-time updates */}
                <Route element={<ProtectedRoute><WebSocketProvider><Layout /></WebSocketProvider></ProtectedRoute>}>
                  <Route index element={<PrintersPage />} />
                  <Route path="archives" element={<ArchivesPage />} />
                  <Route path="queue" element={<QueuePage />} />
                  <Route path="stats" element={<StatsPage />} />
                  <Route path="profiles" element={<ProfilesPage />} />
                  <Route path="maintenance" element={<MaintenancePage />} />
                  <Route path="projects" element={<ProjectsPage />} />
                  <Route path="projects/:id" element={<ProjectDetailPage />} />
                  <Route path="inventory" element={<InventoryPage />} />
                  <Route path="files" element={<FileManagerPage />} />
                  <Route path="settings" element={<AdminRoute><SettingsPage /></AdminRoute>} />
                  <Route path="users" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="groups" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="system" element={<SystemInfoPage />} />
                  <Route path="external/:id" element={<ExternalLinkPage />} />
                </Route>
              </Routes>
            </BrowserRouter>
          </AuthProvider>
        </QueryClientProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

export default App;
