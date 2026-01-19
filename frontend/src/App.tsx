import { BrowserRouter, Routes, Route } from 'react-router-dom';
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
import { ExternalLinkPage } from './pages/ExternalLinkPage';
import { SystemInfoPage } from './pages/SystemInfoPage';
import { useWebSocket } from './hooks/useWebSocket';
import { ThemeProvider } from './contexts/ThemeContext';
import { ToastProvider } from './contexts/ToastContext';

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

function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <Routes>
              {/* Camera page - standalone, no layout, no WebSocket (doesn't need real-time updates) */}
              <Route path="/camera/:printerId" element={<CameraPage />} />

              {/* Main app with WebSocket for real-time updates */}
              <Route element={<WebSocketProvider><Layout /></WebSocketProvider>}>
                <Route index element={<PrintersPage />} />
                <Route path="archives" element={<ArchivesPage />} />
                <Route path="queue" element={<QueuePage />} />
                <Route path="stats" element={<StatsPage />} />
                <Route path="profiles" element={<ProfilesPage />} />
                <Route path="maintenance" element={<MaintenancePage />} />
                <Route path="projects" element={<ProjectsPage />} />
                <Route path="projects/:id" element={<ProjectDetailPage />} />
                <Route path="files" element={<FileManagerPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="system" element={<SystemInfoPage />} />
                <Route path="external/:id" element={<ExternalLinkPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </QueryClientProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}

export default App;
