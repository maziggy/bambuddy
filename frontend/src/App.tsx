import { Component, type ReactNode, type ErrorInfo } from 'react';
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
import { LibraryTrashPage } from './pages/LibraryTrashPage';
import { CameraPage } from './pages/CameraPage';
import { StreamOverlayPage } from './pages/StreamOverlayPage';
import { ExternalLinkPage } from './pages/ExternalLinkPage';
import { GroupEditPage } from './pages/GroupEditPage';
import InventoryPage from './pages/InventoryPage';
import { MakerworldPage } from './pages/MakerworldPage';
import { SystemInfoPage } from './pages/SystemInfoPage';
import { LoginPage } from './pages/LoginPage';
import { SetupPage } from './pages/SetupPage';
import { NotificationsPage } from './pages/NotificationsPage';
import { GCodeViewerPage } from './pages/GCodeViewerPage';
import { useWebSocket } from './hooks/useWebSocket';
import { useStreamTokenSync } from './hooks/useCameraStreamToken';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';
import { SliceJobTrackerProvider } from './contexts/SliceJobTrackerContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ColorCatalogProvider } from './contexts/ColorCatalogContext';
import { SpoolBuddyLayout } from './components/spoolbuddy/SpoolBuddyLayout';
import { SpoolBuddyDashboard } from './pages/spoolbuddy/SpoolBuddyDashboard';
import { SpoolBuddyAmsPage } from './pages/spoolbuddy/SpoolBuddyAmsPage';
import { SpoolBuddySettingsPage } from './pages/spoolbuddy/SpoolBuddySettingsPage';
import { SpoolBuddyCalibrationPage } from './pages/spoolbuddy/SpoolBuddyCalibrationPage';
import { SpoolBuddyWriteTagPage } from './pages/spoolbuddy/SpoolBuddyWriteTagPage';
import { SpoolBuddyInventoryPage } from './pages/spoolbuddy/SpoolBuddyInventoryPage';
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null; errorInfo: ErrorInfo | null }> {
  state = { error: null as Error | null, errorInfo: null as ErrorInfo | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    this.setState({ errorInfo });
    console.error('React crash:', error, errorInfo);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 24, color: '#ef4444', backgroundColor: '#18181b', minHeight: '100vh', fontFamily: 'monospace' }}>
          <h1 style={{ fontSize: 20, marginBottom: 12 }}>UI Crash</h1>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 14 }}>{this.state.error.message}</pre>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#a1a1aa', marginTop: 12 }}>
            {this.state.error.stack}
          </pre>
          <button
            onClick={() => { this.setState({ error: null, errorInfo: null }); }}
            style={{ marginTop: 16, padding: '8px 16px', backgroundColor: '#3b82f6', color: '#fff', border: 'none', borderRadius: 8, cursor: 'pointer' }}
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60,
      retry: 1,
    },
  },
});

function StreamTokenSync() {
  useStreamTokenSync();
  return null;
}

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

function PermissionRoute({ permission, children }: { permission: string; children: React.ReactNode }) {
  // Permission-gated route: any user with the given permission can enter, not
  // just admins. Individual components below this guard apply their own
  // per-action permission checks. Used for pages where delegation is supported
  // (e.g. settings:read grants read-only access to Settings; specific tabs
  // require their own permissions like users:read, groups:update, etc.).
  const { authEnabled, loading, user, hasPermission } = useAuth();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  // Auth disabled → open access (backward compatibility)
  if (!authEnabled) {
    return <>{children}</>;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (!hasPermission(permission as Parameters<typeof hasPermission>[0])) {
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
    <ErrorBoundary>
    <ThemeProvider>
      <ToastProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <ColorCatalogProvider>
            <SliceJobTrackerProvider>
            <StreamTokenSync />
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

                {/* SpoolBuddy kiosk UI */}
                <Route element={<ProtectedRoute><WebSocketProvider><SpoolBuddyLayout /></WebSocketProvider></ProtectedRoute>}>
                  <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
                  <Route path="spoolbuddy/ams" element={<SpoolBuddyAmsPage />} />
                  <Route path="spoolbuddy/write-tag" element={<SpoolBuddyWriteTagPage />} />
                  <Route path="spoolbuddy/inventory" element={<SpoolBuddyInventoryPage />} />
                  <Route path="spoolbuddy/settings" element={<SpoolBuddySettingsPage />} />
                  <Route path="spoolbuddy/calibration" element={<SpoolBuddyCalibrationPage />} />
                </Route>

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
                  <Route path="files/trash" element={<LibraryTrashPage />} />
                  <Route path="makerworld" element={<MakerworldPage />} />
                  <Route path="settings" element={<PermissionRoute permission="settings:read"><SettingsPage /></PermissionRoute>} />
                  <Route path="groups/new" element={<PermissionRoute permission="groups:create"><GroupEditPage /></PermissionRoute>} />
                  <Route path="groups/:id/edit" element={<PermissionRoute permission="groups:update"><GroupEditPage /></PermissionRoute>} />
                  <Route path="users" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="groups" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="system" element={<SystemInfoPage />} />
                  <Route path="notifications" element={<NotificationsPage />} />
                  <Route path="gcode-viewer" element={<GCodeViewerPage />} />
                  <Route path="external/:id" element={<ExternalLinkPage />} />
                  <Route path="camera-tokens" element={<Navigate to="/settings?tab=apikeys#card-camera-tokens" replace />} />
                </Route>
              </Routes>
            </BrowserRouter>
            </SliceJobTrackerProvider>
            </ColorCatalogProvider>
          </AuthProvider>
        </QueryClientProvider>
      </ToastProvider>
    </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
