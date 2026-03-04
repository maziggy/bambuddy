import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Github,
  Play,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  ExternalLink,
  RefreshCw,
  Download,
  Upload,
  Database,
  History,
  SkipForward,
  AlertTriangle,
  Trash2,
  RotateCcw,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  GitHubBackupConfig,
  GitHubBackupConfigCreate,
  GitHubBackupLog,
  GitHubBackupStatus,
  GitHubBackupTriggerResponse,
  ScheduleType,
  CloudAuthStatus,
  Printer,
} from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { formatRelativeTime } from '../utils/date';

interface StatusBadgeProps {
  status: string | null;
}

function StatusBadge({ status }: StatusBadgeProps) {
  if (!status) return null;

  const styles: Record<string, string> = {
    success: 'bg-green-500/20 text-green-400',
    failed: 'bg-red-500/20 text-red-400',
    skipped: 'bg-yellow-500/20 text-yellow-400',
    running: 'bg-blue-500/20 text-blue-400',
  };

  const icons: Record<string, React.ReactNode> = {
    success: <CheckCircle className="w-3 h-3" />,
    failed: <XCircle className="w-3 h-3" />,
    skipped: <SkipForward className="w-3 h-3" />,
    running: <Loader2 className="w-3 h-3 animate-spin" />,
  };

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${styles[status] || 'bg-gray-500/20 text-gray-400'}`}>
      {icons[status]}
      {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  );
}

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString();
}

export function GitHubBackupSettings() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { t } = useTranslation();

  // Local state for form
  const [repoUrl, setRepoUrl] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [branch, setBranch] = useState('main');
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleType, setScheduleType] = useState<ScheduleType>('daily');
  const [backupKProfiles, setBackupKProfiles] = useState(true);
  const [backupCloudProfiles, setBackupCloudProfiles] = useState(true);
  const [backupSettings, setBackupSettings] = useState(false);
  const [enabled, setEnabled] = useState(true);

  // Local backup state
  const [isExporting, setIsExporting] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [operationStatus, setOperationStatus] = useState<string>('');
  const [showRestoreConfirm, setShowRestoreConfirm] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreResult, setRestoreResult] = useState<{ success: boolean; message: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Block navigation while backup/restore is in progress
  useEffect(() => {
    const isOperationInProgress = isExporting || isRestoring;

    if (isOperationInProgress) {
      const handleBeforeUnload = (e: BeforeUnloadEvent) => {
        e.preventDefault();
        e.returnValue = 'A backup operation is in progress. Are you sure you want to leave?';
        return e.returnValue;
      };

      window.addEventListener('beforeunload', handleBeforeUnload);
      return () => window.removeEventListener('beforeunload', handleBeforeUnload);
    }
  }, [isExporting, isRestoring]);

  // Test connection state
  const [testLoading, setTestLoading] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Auto-save debounce
  const autoSaveTimerRef = useRef<NodeJS.Timeout | null>(null);
  const isInitializedRef = useRef(false);

  // Queries
  const { data: config, isLoading: configLoading } = useQuery<GitHubBackupConfig | null>({
    queryKey: ['github-backup-config'],
    queryFn: api.getGitHubBackupConfig,
  });

  const { data: status } = useQuery<GitHubBackupStatus>({
    queryKey: ['github-backup-status'],
    queryFn: api.getGitHubBackupStatus,
    refetchInterval: (query) => query.state.data?.is_running ? 500 : 10000, // Poll fast during backup
  });

  const { data: logs } = useQuery<GitHubBackupLog[]>({
    queryKey: ['github-backup-logs'],
    queryFn: () => api.getGitHubBackupLogs(20),
  });

  const { data: cloudStatus } = useQuery<CloudAuthStatus>({
    queryKey: ['cloud-status'],
    queryFn: api.getCloudStatus,
  });

  // Fetch printers and their statuses for K-profile availability
  const { data: printers } = useQuery<Printer[]>({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch printer statuses from API (not just cache) to get accurate connection status
  const printerStatusQueries = useQueries({
    queries: (printers ?? []).map(printer => ({
      queryKey: ['printerStatus', printer.id],
      queryFn: () => api.getPrinterStatus(printer.id),
      staleTime: 10000, // Consider stale after 10s
      refetchInterval: 30000, // Refresh every 30s
    })),
  });

  const printerStatuses = (printers ?? []).map((printer, index) => ({
    printer,
    connected: printerStatusQueries[index]?.data?.connected ?? false,
  }));

  const totalPrinters = printerStatuses.length;
  const connectedPrinters = printerStatuses.filter(p => p.connected).length;
  const noPrintersConnected = totalPrinters > 0 && connectedPrinters === 0;
  const somePrintersDisconnected = connectedPrinters > 0 && connectedPrinters < totalPrinters;

  // Initialize form from config
  useEffect(() => {
    if (config) {
      setRepoUrl(config.repository_url);
      setBranch(config.branch);
      setScheduleEnabled(config.schedule_enabled);
      setScheduleType(config.schedule_type);
      setBackupKProfiles(config.backup_kprofiles);
      setBackupCloudProfiles(config.backup_cloud_profiles);
      setBackupSettings(config.backup_settings);
      setEnabled(config.enabled);
      setAccessToken(''); // Don't show stored token
      // Mark as initialized after a tick to avoid auto-save on initial load
      setTimeout(() => { isInitializedRef.current = true; }, 100);
    }
  }, [config]);

  // Auto-save function for existing configs
  const autoSave = useCallback(async (includeToken: boolean = false) => {
    if (!config?.has_token) return; // Only auto-save if config already exists

    try {
      if (includeToken && accessToken) {
        // Full save with new token
        await api.saveGitHubBackupConfig({
          repository_url: repoUrl,
          access_token: accessToken,
          branch,
          schedule_enabled: scheduleEnabled,
          schedule_type: scheduleType,
          backup_kprofiles: backupKProfiles,
          backup_cloud_profiles: backupCloudProfiles,
          backup_settings: backupSettings,
          enabled,
        });
        setAccessToken(''); // Clear after save
        showToast('Token updated');
      } else {
        // Update without token
        await api.updateGitHubBackupConfig({
          repository_url: repoUrl,
          branch,
          schedule_enabled: scheduleEnabled,
          schedule_type: scheduleType,
          backup_kprofiles: backupKProfiles,
          backup_cloud_profiles: backupCloudProfiles,
          backup_settings: backupSettings,
          enabled,
        });
        showToast('Settings saved');
      }
      queryClient.invalidateQueries({ queryKey: ['github-backup-config'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
    } catch (error) {
      showToast(`Failed to save: ${(error as Error).message}`, 'error');
    }
  }, [config?.has_token, repoUrl, accessToken, branch, scheduleEnabled, scheduleType, backupKProfiles, backupCloudProfiles, backupSettings, enabled, queryClient, showToast]);

  // Auto-save effect for existing configs (debounced)
  useEffect(() => {
    if (!isInitializedRef.current || !config?.has_token) return;

    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
    }

    autoSaveTimerRef.current = setTimeout(() => {
      autoSave(false);
    }, 500);

    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current);
      }
    };
  }, [repoUrl, branch, scheduleEnabled, scheduleType, backupKProfiles, backupCloudProfiles, backupSettings, enabled, autoSave, config?.has_token]);

  // Auto-save token when it changes (with longer debounce)
  useEffect(() => {
    if (!isInitializedRef.current || !config?.has_token || !accessToken) return;

    if (autoSaveTimerRef.current) {
      clearTimeout(autoSaveTimerRef.current);
    }

    autoSaveTimerRef.current = setTimeout(() => {
      autoSave(true);
    }, 1000);

    return () => {
      if (autoSaveTimerRef.current) {
        clearTimeout(autoSaveTimerRef.current);
      }
    };
  }, [accessToken, autoSave, config?.has_token]);

  // Mutations
  const saveConfigMutation = useMutation({
    mutationFn: (data: GitHubBackupConfigCreate) => api.saveGitHubBackupConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-config'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
      showToast('GitHub backup enabled');
      setAccessToken('');
      isInitializedRef.current = true;
    },
    onError: (error: Error) => {
      showToast(`Failed to save: ${error.message}`, 'error');
    },
  });

  const triggerBackupMutation = useMutation<GitHubBackupTriggerResponse, Error>({
    mutationFn: api.triggerGitHubBackup,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      if (result.success) {
        if (result.files_changed > 0) {
          showToast(`Backup complete - ${result.files_changed} files updated`);
        } else {
          showToast('Backup skipped - no changes');
        }
      } else {
        showToast(`Backup failed: ${result.message}`, 'error');
      }
    },
    onError: (error: Error) => {
      showToast(`Backup failed: ${error.message}`, 'error');
    },
  });

  const clearLogsMutation = useMutation<{ deleted: number; message: string }, Error>({
    mutationFn: () => api.clearGitHubBackupLogs(0),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      showToast(`Cleared ${result.deleted} logs`);
    },
    onError: (error: Error) => {
      showToast(`Failed to clear logs: ${error.message}`, 'error');
    },
  });

  const handleTestConnection = async () => {
    setTestLoading(true);
    setTestResult(null);
    try {
      let result;
      // If user entered a new token, test with those credentials
      if (accessToken) {
        if (!repoUrl) {
          showToast('Enter repository URL', 'error');
          setTestLoading(false);
          return;
        }
        result = await api.testGitHubConnection(repoUrl, accessToken);
      } else if (config?.has_token) {
        // Use stored credentials
        result = await api.testGitHubStoredConnection();
      } else {
        showToast('Enter repository URL and access token', 'error');
        setTestLoading(false);
        return;
      }
      setTestResult({ success: result.success, message: result.message });
    } catch (error) {
      setTestResult({ success: false, message: (error as Error).message });
    } finally {
      setTestLoading(false);
    }
  };

  // Initial setup save (only for new configs)
  const handleInitialSetup = () => {
    if (!repoUrl) {
      showToast('Repository URL is required', 'error');
      return;
    }
    if (!accessToken) {
      showToast('Access token is required', 'error');
      return;
    }

    saveConfigMutation.mutate({
      repository_url: repoUrl,
      access_token: accessToken,
      branch,
      schedule_enabled: scheduleEnabled,
      schedule_type: scheduleType,
      backup_kprofiles: backupKProfiles,
      backup_cloud_profiles: backupCloudProfiles,
      backup_settings: backupSettings,
      enabled,
    });
  };

  if (configLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* Left Column - GitHub Backup */}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Github className="w-5 h-5 text-gray-400" />
                <h2 className="text-lg font-semibold text-white">GitHub Backup</h2>
              </div>
              {config && cloudStatus?.is_authenticated && (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-bambu-gray">Enabled</span>
                  <Toggle
                    checked={enabled}
                    onChange={setEnabled}
                  />
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Bambu Cloud required message */}
            {!cloudStatus?.is_authenticated ? (
              <div className="flex items-start gap-2 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                <p className="text-sm text-yellow-400">
                  Bambu Cloud login required. Sign in under Profiles → Cloud Profiles to enable GitHub backup.
                </p>
              </div>
            ) : (
              <>
                <p className="text-sm text-bambu-gray">
                  Automatically sync your profiles to a private GitHub repository for backup and version history.
                </p>

                {/* Repository URL */}
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    Repository URL
                  </label>
                  <input
                    type="text"
                    value={repoUrl}
                    onChange={(e) => { setRepoUrl(e.target.value); setTestResult(null); }}
                    placeholder="https://github.com/username/bambuddy-backup"
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>

                {/* Access Token */}
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    Personal Access Token {config?.has_token && <span className="text-green-400">(saved)</span>}
                  </label>
                  <input
                    type="password"
                    value={accessToken}
                    onChange={(e) => { setAccessToken(e.target.value); setTestResult(null); }}
                    placeholder={config?.has_token ? 'Enter new token to update' : 'ghp_xxxxxxxxxxxx'}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    Fine-grained token with Contents read/write permission
                  </p>
                </div>

            {/* Branch - inline with schedule */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Branch</label>
                <input
                  type="text"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">Auto-backup</label>
                <select
                  value={scheduleEnabled ? scheduleType : 'disabled'}
                  onChange={(e) => {
                    if (e.target.value === 'disabled') {
                      setScheduleEnabled(false);
                    } else {
                      setScheduleEnabled(true);
                      setScheduleType(e.target.value as ScheduleType);
                    }
                  }}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="disabled">Manual only</option>
                  <option value="hourly">Hourly</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                </select>
              </div>
            </div>

            {/* What to backup */}
            <div>
              <label className="block text-sm text-bambu-gray mb-2">Include in backup</label>
              <div className="space-y-2">
                <label className={`flex items-start gap-2 ${noPrintersConnected ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                  <input
                    type="checkbox"
                    checked={backupKProfiles}
                    onChange={(e) => setBackupKProfiles(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    disabled={noPrintersConnected}
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`text-sm ${noPrintersConnected ? 'text-bambu-gray' : 'text-white'}`}>K-Profiles</span>
                      {noPrintersConnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          No printers connected
                        </span>
                      )}
                      {somePrintersDisconnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {connectedPrinters}/{totalPrinters} connected
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-bambu-gray">Pressure advance calibration from connected printers</p>
                  </div>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={backupCloudProfiles}
                    onChange={(e) => setBackupCloudProfiles(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    disabled={!cloudStatus?.is_authenticated}
                  />
                  <div>
                    <span className={`text-sm ${cloudStatus?.is_authenticated ? 'text-white' : 'text-bambu-gray'}`}>Cloud Profiles</span>
                    <p className="text-xs text-bambu-gray">Filament, printer, and process presets from Bambu Cloud</p>
                  </div>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={backupSettings}
                    onChange={(e) => setBackupSettings(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                  />
                  <div>
                    <span className="text-white text-sm">App Settings</span>
                    <p className="text-xs text-bambu-gray">Bambuddy configuration (complete database)</p>
                  </div>
                </label>
              </div>
            </div>

            {/* Test + Status + Actions */}
            <div className="border-t border-bambu-dark-tertiary pt-4 space-y-3">
              {/* Status line */}
              {status?.configured && (
                <div className="flex items-center justify-between text-sm">
                  <div className="flex items-center gap-2 text-bambu-gray">
                    {status.last_backup_at ? (
                      <>
                        <span>Last backup: {formatRelativeTime(status.last_backup_at, 'system', t)}</span>
                        <StatusBadge status={status.last_backup_status} />
                      </>
                    ) : (
                      <span>No backups yet</span>
                    )}
                  </div>
                  {status.next_scheduled_run && (
                    <span className="text-bambu-gray">
                      <Clock className="w-3 h-3 inline mr-1" />
                      Next: {formatRelativeTime(status.next_scheduled_run, 'system', t)}
                    </span>
                  )}
                </div>
              )}

              {/* Test result */}
              {testResult && (
                <div className={`text-sm flex items-center gap-1 ${testResult.success ? 'text-green-400' : 'text-red-400'}`}>
                  {testResult.success ? <CheckCircle className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                  {testResult.message}
                </div>
              )}

              {/* Action buttons */}
              <div className="flex flex-wrap items-center gap-2">
                {status?.configured ? (
                  <>
                    {(triggerBackupMutation.isPending || status.is_running) ? (
                      <div className="flex items-center gap-2 text-bambu-green">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        <span className="text-sm">{status.progress || 'Starting backup...'}</span>
                      </div>
                    ) : (
                      <>
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => triggerBackupMutation.mutate()}
                          disabled={!config?.enabled}
                        >
                          <Play className="w-4 h-4" />
                          Backup Now
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={handleTestConnection}
                          disabled={testLoading}
                        >
                          {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                          Test
                        </Button>
                      </>
                    )}
                  </>
                ) : (
                  <>
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={handleInitialSetup}
                      disabled={saveConfigMutation.isPending || !repoUrl || !accessToken}
                    >
                      {saveConfigMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
                      Enable Backup
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleTestConnection}
                      disabled={testLoading || !repoUrl || !accessToken}
                    >
                      {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                      Test Connection
                    </Button>
                  </>
                )}
              </div>
            </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* Backup History - only show if configured and has logs */}
        {logs && logs.length > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <History className="w-5 h-5 text-gray-400" />
                  <h2 className="text-lg font-semibold text-white">History</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => clearLogsMutation.mutate()}
                  disabled={clearLogsMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                  Clear
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-bambu-gray border-b border-bambu-dark-tertiary">
                      <th className="text-left py-2 px-2">Date</th>
                      <th className="text-left py-2 px-2">Status</th>
                      <th className="text-left py-2 px-2">Commit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {logs.slice(0, 10).map((log) => (
                      <tr key={log.id} className="border-b border-bambu-dark-tertiary/50 hover:bg-bambu-dark-secondary">
                        <td className="py-2 px-2 text-white">{formatDateTime(log.started_at)}</td>
                        <td className="py-2 px-2"><StatusBadge status={log.status} /></td>
                        <td className="py-2 px-2">
                          {log.commit_sha ? (
                            <a
                              href={`${config?.repository_url}/commit/${log.commit_sha}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-bambu-green hover:underline inline-flex items-center gap-1"
                            >
                              {log.commit_sha.substring(0, 7)}
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          ) : (
                            <span className="text-bambu-gray">-</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Right Column - Local Backup */}
      <div className="space-y-6">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Database className="w-5 h-5 text-gray-400" />
              <h2 className="text-lg font-semibold text-white">Local Backup</h2>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-bambu-gray">
              Create a complete backup of your Bambuddy data including the database, archives, uploads, and all files.
            </p>

            {/* Export */}
            <div className="flex items-center justify-between py-3 border-b border-bambu-dark-tertiary">
              <div>
                <p className="text-white">Download Backup</p>
                <p className="text-sm text-bambu-gray">
                  Complete backup: database + all files (ZIP)
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                disabled={isExporting || isRestoring}
                onClick={async () => {
                  setIsExporting(true);
                  setOperationStatus('Preparing backup...');
                  try {
                    setOperationStatus('Creating backup archive... This may take a while for large archives.');
                    const { blob, filename } = await api.exportBackup();
                    setOperationStatus('Downloading backup file...');
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                    showToast('Backup downloaded successfully');
                  } catch (e) {
                    showToast(`Failed to create backup: ${e instanceof Error ? e.message : 'Unknown error'}`, 'error');
                  } finally {
                    setIsExporting(false);
                    setOperationStatus('');
                  }
                }}
              >
                <Download className="w-4 h-4" />
                Download
              </Button>
            </div>

            {/* Import */}
            <div className="flex items-center justify-between py-3 border-b border-bambu-dark-tertiary">
              <div>
                <p className="text-white">{t('backup.restoreBackup')}</p>
                <p className="text-sm text-bambu-gray">
                  {t('backup.restoreDescription')}
                </p>
                <p className="text-xs text-bambu-gray-light mt-1">
                  {t('backup.restoreNote')}
                </p>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    setRestoreFile(file);
                    setShowRestoreConfirm(true);
                  }
                  e.target.value = '';
                }}
              />
              <Button
                variant="secondary"
                size="sm"
                disabled={isRestoring || isExporting}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="w-4 h-4" />
                Restore
              </Button>
            </div>

            {/* Restore result message */}
            {restoreResult && (
              <div className={`p-3 rounded-lg ${restoreResult.success ? 'bg-green-500/10 border border-green-500/30' : 'bg-red-500/10 border border-red-500/30'}`}>
                <div className="flex items-start gap-2 text-sm">
                  {restoreResult.success ? (
                    <CheckCircle className="w-4 h-4 text-green-400 mt-0.5 flex-shrink-0" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 flex-shrink-0" />
                  )}
                  <div className={restoreResult.success ? 'text-green-200' : 'text-red-200'}>
                    {restoreResult.message}
                    {restoreResult.success && (
                      <div className="mt-2">
                        <Button
                          size="sm"
                          onClick={() => window.location.reload()}
                        >
                          <RotateCcw className="w-3 h-3" />
                          Reload Now
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* Warning */}
            <div className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
              <div className="flex items-start gap-2 text-sm">
                <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                <div className="text-yellow-200">
                  <span className="font-medium">Restore replaces all data.</span>{' '}
                  <span className="text-yellow-200/70">Your current database and files will be completely replaced. A restart is required after restore.</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Restore Confirmation Modal */}
      {showRestoreConfirm && restoreFile && (
        <ConfirmModal
          title="Restore Backup"
          message={`Are you sure you want to restore from "${restoreFile.name}"? This will completely replace your current database and all files. The application will need to be restarted after restore.`}
          confirmText="Restore Backup"
          variant="danger"
          onConfirm={async () => {
            setShowRestoreConfirm(false);
            setIsRestoring(true);
            setRestoreResult(null);
            try {
              setOperationStatus('Uploading backup file...');
              const result = await api.importBackup(restoreFile);
              setRestoreResult(result);
              if (result.success) {
                showToast('Backup restored. Please restart Bambuddy.', 'success');
              } else {
                showToast(result.message, 'error');
              }
            } catch (e) {
              const message = e instanceof Error ? e.message : 'Failed to restore backup';
              setRestoreResult({ success: false, message });
              showToast(message, 'error');
            } finally {
              setIsRestoring(false);
              setOperationStatus('');
              setRestoreFile(null);
            }
          }}
          onCancel={() => {
            setShowRestoreConfirm(false);
            setRestoreFile(null);
          }}
        />
      )}

      {/* Blocking overlay during backup/restore operations */}
      {(isExporting || isRestoring) && (
        <div className="fixed inset-0 bg-black/80 flex items-center justify-center z-[100]">
          <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl p-8 max-w-md w-full mx-4 text-center">
            <div className="flex justify-center mb-4">
              <div className="relative">
                <div className="w-16 h-16 border-4 border-bambu-dark-tertiary rounded-full"></div>
                <div className="w-16 h-16 border-4 border-bambu-green border-t-transparent rounded-full absolute inset-0 animate-spin"></div>
              </div>
            </div>
            <h3 className="text-xl font-semibold text-white mb-2">
              {isExporting ? 'Creating Backup' : 'Restoring Backup'}
            </h3>
            <p className="text-bambu-gray mb-4">
              {operationStatus || (isExporting ? 'Preparing...' : 'Processing...')}
            </p>
            <div className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
              <div className="flex items-start gap-2 text-sm">
                <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                <p className="text-yellow-200 text-left">
                  Please do not close this page or navigate away. This operation may take several minutes for large backups.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
