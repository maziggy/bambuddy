import { Component, Suspense, type ReactNode, type ErrorInfo } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Layout } from './components/Layout';
import { useWebSocket } from './hooks/useWebSocket';
import { usePrintProgressTitle } from './hooks/usePrintProgressTitle';
import { useStreamTokenSync } from './hooks/useCameraStreamToken';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';
import { SliceJobTrackerProvider } from './contexts/SliceJobTrackerContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ColorCatalogProvider } from './contexts/ColorCatalogContext';
import { EmbeddedCameraProvider } from './contexts/EmbeddedCameraContext';
import { SpoolBuddyLayout } from './components/spoolbuddy/SpoolBuddyLayout';
import { PageLoading } from './components/PageLoading';
import { AuthRetrying } from './components/AuthRetrying';
import { lazyPage } from './utils/lazyPage';

// Every page loads as its own file when first opened (#3175).
const PrintersPage = lazyPage(() => import('./pages/PrintersPage'), 'PrintersPage');
const ArchivesPage = lazyPage(() => import('./pages/ArchivesPage'), 'ArchivesPage');
const QueuePage = lazyPage(() => import('./pages/QueuePage'), 'QueuePage');
const StatsPage = lazyPage(() => import('./pages/StatsPage'), 'StatsPage');
const SettingsPage = lazyPage(() => import('./pages/SettingsPage'), 'SettingsPage');
const FinancePage = lazyPage(() => import('./pages/FinancePage'), 'FinancePage');
const ProfilesPage = lazyPage(() => import('./pages/ProfilesPage'), 'ProfilesPage');
const MaintenancePage = lazyPage(() => import('./pages/MaintenancePage'), 'MaintenancePage');
const ProjectsPage = lazyPage(() => import('./pages/ProjectsPage'), 'ProjectsPage');
const ProjectDetailPage = lazyPage(() => import('./pages/ProjectDetailPage'), 'ProjectDetailPage');
const FileManagerPage = lazyPage(() => import('./pages/FileManagerPage'), 'FileManagerPage');
const LibraryTrashPage = lazyPage(() => import('./pages/LibraryTrashPage'), 'LibraryTrashPage');
const CameraPage = lazyPage(() => import('./pages/CameraPage'), 'CameraPage');
const CamWallPage = lazyPage(() => import('./pages/CamWallPage'), 'CamWallPage');
const StreamOverlayPage = lazyPage(() => import('./pages/StreamOverlayPage'), 'StreamOverlayPage');
const ExternalLinkPage = lazyPage(() => import('./pages/ExternalLinkPage'), 'ExternalLinkPage');
const GroupEditPage = lazyPage(() => import('./pages/GroupEditPage'), 'GroupEditPage');
const PrinterLocationsPage = lazyPage(() => import('./pages/PrinterLocationsPage'), 'PrinterLocationsPage');
const InventoryPage = lazyPage(() => import('./pages/InventoryPage'), 'default');
const ModelSourcesPage = lazyPage(() => import('./pages/ModelSourcesPage'), 'ModelSourcesPage');
const SystemInfoPage = lazyPage(() => import('./pages/SystemInfoPage'), 'SystemInfoPage');
const LoginPage = lazyPage(() => import('./pages/LoginPage'), 'LoginPage');
const ConnectAuthorizePage = lazyPage(() => import('./pages/ConnectAuthorizePage'), 'ConnectAuthorizePage');
const SetupPage = lazyPage(() => import('./pages/SetupPage'), 'SetupPage');
const NotificationsPage = lazyPage(() => import('./pages/NotificationsPage'), 'NotificationsPage');
const GCodeViewerPage = lazyPage(() => import('./pages/GCodeViewerPage'), 'GCodeViewerPage');
const SpoolBuddyDashboard = lazyPage(() => import('./pages/spoolbuddy/SpoolBuddyDashboard'), 'SpoolBuddyDashboard');
const SpoolBuddyAmsPage = lazyPage(() => import('./pages/spoolbuddy/SpoolBuddyAmsPage'), 'SpoolBuddyAmsPage');
const SpoolBuddySettingsPage = lazyPage(() => import('./pages/spoolbuddy/SpoolBuddySettingsPage'), 'SpoolBuddySettingsPage');
const SpoolBuddyWriteTagPage = lazyPage(() => import('./pages/spoolbuddy/SpoolBuddyWriteTagPage'), 'SpoolBuddyWriteTagPage');
const SpoolBuddyInventoryPage = lazyPage(() => import('./pages/spoolbuddy/SpoolBuddyInventoryPage'), 'SpoolBuddyInventoryPage');

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
  usePrintProgressTitle();
  return <>{children}</>;
}

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { authEnabled, loading, user, authUnconfirmed } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  // A stored sign-in the server couldn't check yet: wait here, so a kiosk
  // isn't left on a login page it can't use (#3360).
  if (authUnconfirmed) {
    return <AuthRetrying />;
  }

  if (authEnabled && !user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <>{children}</>;
}

export function PermissionRoute({ permission, children }: { permission: string | string[]; children: React.ReactNode }) {
  // Permission-gated route: any user with the given permission can enter, not
  // just admins. Individual components below this guard apply their own
  // per-action permission checks. Used for pages where delegation is supported
  // (e.g. settings:read grants read-only access to Settings; specific tabs
  // require their own permissions like users:read, groups:update, etc.).
  const { authEnabled, loading, user, hasPermission, authUnconfirmed } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center">Loading...</div>;
  }

  if (authUnconfirmed) {
    return <AuthRetrying />;
  }

  // Auth disabled → open access (backward compatibility)
  if (!authEnabled) {
    return <>{children}</>;
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  // A list lets in anyone holding at least one of the permissions.
  const required = (Array.isArray(permission) ? permission : [permission]) as Parameters<typeof hasPermission>[0][];
  if (!required.some((p) => hasPermission(p))) {
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
      <ToastProvider>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            {/* ThemeProvider sits inside AuthProvider so its initial
                ``api.getSettings()`` fetch can wait for AuthContext to
                resolve — otherwise it fires unconditionally on every
                login page load and returns 401. ErrorBoundary uses
                inline styles, so a missing theme on a crash screen is
                not a regression. */}
            <ThemeProvider>
            <ColorCatalogProvider>
            <SliceJobTrackerProvider>
            <StreamTokenSync />
            <BrowserRouter>
              {/* Standalone pages (login, camera, overlay, ...) wait here; pages
                  inside a layout wait in that layout, so its frame stays up. */}
              <Suspense fallback={<PageLoading fullScreen />}>
              <Routes>
                {/* Setup page - only accessible if auth not enabled */}
                <Route path="/setup" element={<SetupRoute><SetupPage /></SetupRoute>} />

                {/* Login page */}
                <Route path="/login" element={<LoginPage />} />

                {/* "Sign in with Bambuddy" for connected apps: standalone, no layout,
                    so it also fits inside the sidebar iframe of the app asking. */}
                <Route path="/connect/authorize" element={<ProtectedRoute><ConnectAuthorizePage /></ProtectedRoute>} />

                {/* Camera page - standalone, no layout, no WebSocket (doesn't need real-time updates) */}
                <Route path="/camera/:printerId" element={<CameraPage />} />

                {/* Stream overlay page - standalone for OBS/streaming embeds, no auth required */}
                <Route path="/overlay/:printerId" element={<StreamOverlayPage />} />

                {/* Cam Wall on its own URL (#2531). Outside ProtectedRoute because a
                    ?token= kiosk has no session to protect; the page itself sends a
                    tokenless visitor to /login, and the backend gates the feed. */}
                <Route path="/camwall" element={<CamWallPage />} />

                {/* SpoolBuddy kiosk UI */}
                <Route element={<ProtectedRoute><WebSocketProvider><SpoolBuddyLayout /></WebSocketProvider></ProtectedRoute>}>
                  <Route path="spoolbuddy" element={<SpoolBuddyDashboard />} />
                  <Route path="spoolbuddy/ams" element={<SpoolBuddyAmsPage />} />
                  <Route path="spoolbuddy/write-tag" element={<SpoolBuddyWriteTagPage />} />
                  <Route path="spoolbuddy/inventory" element={<SpoolBuddyInventoryPage />} />
                  <Route path="spoolbuddy/settings" element={<SpoolBuddySettingsPage />} />
                  {/* Calibration moved into the settings page; old bookmarks land there. */}
                  <Route path="spoolbuddy/calibration" element={<Navigate to="/spoolbuddy/settings" replace />} />
                </Route>

                {/* Main app with WebSocket for real-time updates */}
                <Route element={<ProtectedRoute><WebSocketProvider><EmbeddedCameraProvider><Layout /></EmbeddedCameraProvider></WebSocketProvider></ProtectedRoute>}>
                  <Route index element={<PrintersPage />} />
                  <Route path="archives" element={<ArchivesPage />} />
                  <Route path="queue" element={<QueuePage />} />
                  {/* Slicer Pipelines (#1425) — Pipelines tab lives on the
                      Print Queue page (Queue + History + Timeline +
                      Pipelines). Old standalone URL redirects. */}
                  <Route path="pipelines/runs" element={<Navigate to="/queue?tab=pipelines" replace />} />
                  <Route path="stats" element={<StatsPage />} />
                  <Route path="profiles" element={<ProfilesPage />} />
                  <Route path="finance" element={<PermissionRoute permission="cost_centers:read_own"><FinancePage /></PermissionRoute>} />
                  <Route path="maintenance" element={<MaintenancePage />} />
                  <Route path="projects" element={<ProjectsPage />} />
                  <Route path="projects/:id" element={<ProjectDetailPage />} />
                  <Route path="inventory" element={<InventoryPage />} />
                  <Route path="files" element={<FileManagerPage />} />
                  <Route path="files/trash" element={<LibraryTrashPage />} />
                  <Route path="model-sources" element={<PermissionRoute permission={['makerworld:view', 'manyfold:view']}><ModelSourcesPage /></PermissionRoute>} />
                  {/* The page was MakerWorld-only until Manyfold joined it (#1471); old links and bookmarks still land on that tab. */}
                  <Route path="makerworld" element={<Navigate to="/model-sources?tab=makerworld" replace />} />
                  <Route path="settings" element={<PermissionRoute permission="settings:read"><SettingsPage /></PermissionRoute>} />
                  <Route path="groups/new" element={<PermissionRoute permission="groups:create"><GroupEditPage /></PermissionRoute>} />
                  <Route path="groups/:id/edit" element={<PermissionRoute permission="groups:update"><GroupEditPage /></PermissionRoute>} />
                  <Route path="printer-locations" element={<PermissionRoute permission="printers:read"><PrinterLocationsPage /></PermissionRoute>} />
                  <Route path="users" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="groups" element={<Navigate to="/settings?tab=users" replace />} />
                  <Route path="system" element={<SystemInfoPage />} />
                  <Route path="notifications" element={<NotificationsPage />} />
                  <Route path="gcode-viewer" element={<GCodeViewerPage />} />
                  <Route path="external/:id" element={<ExternalLinkPage />} />
                  <Route path="camera-tokens" element={<Navigate to="/settings?tab=camera#card-camera-tokens" replace />} />
                </Route>
              </Routes>
              </Suspense>
            </BrowserRouter>
            </SliceJobTrackerProvider>
            </ColorCatalogProvider>
            </ThemeProvider>
          </AuthProvider>
        </QueryClientProvider>
      </ToastProvider>
    </ErrorBoundary>
  );
}

export default App;
