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
  FolderArchive,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  GitHubBackupConfig,
  GitHubBackupConfigCreate,
  GitHubBackupLog,
  GitHubBackupStatus,
  GitHubBackupTriggerResponse,
  LocalBackupFile,
  LocalBackupStatus,
  ScheduleType,
  CloudAuthStatus,
  Printer,
} from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { formatRelativeTime, parseUTCDate } from '../utils/date';

function formatDateTime(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = parseUTCDate(dateStr);
  if (!date) return '-';
  return date.toLocaleString();
}

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
  const [backupSpools, setBackupSpools] = useState(false);
  const [backupArchives, setBackupArchives] = useState(false);
  const [enabled, setEnabled] = useState(true);

  // Local backup state
  const [isExporting, setIsExporting] = useState(false);
  const [isRestoring, setIsRestoring] = useState(false);
  const [operationStatus, setOperationStatus] = useState<string>('');
  const [showRestoreConfirm, setShowRestoreConfirm] = useState(false);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreResult, setRestoreResult] = useState<{ success: boolean; message: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Scheduled local backup state
  const [deleteConfirmFile, setDeleteConfirmFile] = useState<string | null>(null);
  const [restoreConfirmFile, setRestoreConfirmFile] = useState<string | null>(null);
  const [localBackupPath, setLocalBackupPath] = useState('');

  const { data: localBackupStatus, refetch: refetchLocalStatus } = useQuery<LocalBackupStatus>({
    queryKey: ['local-backup-status'],
    queryFn: api.getLocalBackupStatus,
    refetchInterval: (query) => query.state.data?.is_running ? 1000 : 10000,
  });

  const { data: localBackups, refetch: refetchLocalBackups } = useQuery<LocalBackupFile[]>({
    queryKey: ['local-backup-files'],
    queryFn: api.getLocalBackups,
    refetchInterval: 30000,
  });

  // Sync local path state from server
  useEffect(() => {
    if (localBackupStatus?.path !== undefined) {
      setLocalBackupPath(localBackupStatus.path);
    }
  }, [localBackupStatus?.path]);

  const triggerLocalBackupMutation = useMutation({
    mutationFn: api.triggerLocalBackup,
    onSuccess: (data) => {
      if (data.success) {
        showToast(t('backup.scheduledBackupComplete'));
      } else {
        showToast(data.message, 'error');
      }
      refetchLocalStatus();
      refetchLocalBackups();
    },
    onError: () => showToast(t('backup.scheduledBackupFailed'), 'error'),
  });

  const deleteLocalBackupMutation = useMutation({
    mutationFn: (filename: string) => api.deleteLocalBackup(filename),
    onSuccess: () => {
      refetchLocalBackups();
      setDeleteConfirmFile(null);
    },
  });

  const restoreLocalBackupMutation = useMutation({
    mutationFn: async (filename: string) => {
      setRestoreConfirmFile(null);
      setIsRestoring(true);
      setRestoreResult(null);
      setOperationStatus(t('backup.restoring'));
      return api.restoreLocalBackup(filename);
    },
    onSuccess: (data) => {
      setIsRestoring(false);
      setOperationStatus('');
      if (data.success) {
        setRestoreResult({ success: true, message: data.message });
        showToast(t('backup.backupRestoredRestart'), 'success');
      } else {
        setRestoreResult({ success: false, message: data.message });
        showToast(data.message, 'error');
      }
    },
    onError: (e) => {
      setIsRestoring(false);
      setOperationStatus('');
      const msg = e instanceof Error ? e.message : t('backup.failedToRestore');
      setRestoreResult({ success: false, message: msg });
      showToast(msg, 'error');
    },
  });

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
      setBackupSpools(config.backup_spools);
      setBackupArchives(config.backup_archives);
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
          backup_spools: backupSpools,
          backup_archives: backupArchives,
          enabled,
        });
        setAccessToken(''); // Clear after save
        showToast(t('backup.tokenUpdated'));
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
          backup_spools: backupSpools,
          backup_archives: backupArchives,
          enabled,
        });
        showToast(t('backup.settingsSaved'));
      }
      queryClient.invalidateQueries({ queryKey: ['github-backup-config'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
    } catch (error) {
      showToast(t('backup.failedToSave', { message: (error as Error).message }), 'error');
    }
  }, [config?.has_token, repoUrl, accessToken, branch, scheduleEnabled, scheduleType, backupKProfiles, backupCloudProfiles, backupSettings, backupSpools, backupArchives, enabled, queryClient, showToast, t]);

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
  }, [repoUrl, branch, scheduleEnabled, scheduleType, backupKProfiles, backupCloudProfiles, backupSettings, backupSpools, backupArchives, enabled, autoSave, config?.has_token]);

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
      showToast(t('backup.githubBackupEnabled'));
      setAccessToken('');
      isInitializedRef.current = true;
    },
    onError: (error: Error) => {
      showToast(t('backup.failedToSave', { message: error.message }), 'error');
    },
  });

  const triggerBackupMutation = useMutation<GitHubBackupTriggerResponse, Error>({
    mutationFn: api.triggerGitHubBackup,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      if (result.success) {
        if (result.files_changed > 0) {
          showToast(t('backup.backupCompleteFiles', { count: result.files_changed }));
        } else {
          showToast(t('backup.backupSkippedNoChanges'));
        }
      } else {
        showToast(t('backup.backupFailed2', { message: result.message }), 'error');
      }
    },
    onError: (error: Error) => {
      showToast(t('backup.backupFailed2', { message: error.message }), 'error');
    },
  });

  const clearLogsMutation = useMutation<{ deleted: number; message: string }, Error>({
    mutationFn: () => api.clearGitHubBackupLogs(0),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      showToast(t('backup.clearedLogs', { count: result.deleted }));
    },
    onError: (error: Error) => {
      showToast(t('backup.failedToClearLogs', { message: error.message }), 'error');
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
          showToast(t('backup.enterRepoUrl'), 'error');
          setTestLoading(false);
          return;
        }
        result = await api.testGitHubConnection(repoUrl, accessToken);
      } else if (config?.has_token) {
        // Use stored credentials
        result = await api.testGitHubStoredConnection();
      } else {
        showToast(t('backup.enterRepoAndToken'), 'error');
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
      showToast(t('backup.repoRequired'), 'error');
      return;
    }
    if (!accessToken) {
      showToast(t('backup.tokenRequired'), 'error');
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
      backup_spools: backupSpools,
      backup_archives: backupArchives,
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
                <h2 className="text-lg font-semibold text-white">{t('backup.githubBackup')}</h2>
              </div>
              {config && (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-bambu-gray">{t('backup.enabled')}</span>
                  <Toggle
                    checked={enabled}
                    onChange={setEnabled}
                  />
                </div>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
                <p className="text-sm text-bambu-gray">
                  {t('backup.githubDescription')}
                </p>

                {/* Repository URL */}
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('backup.repositoryUrl')}
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
                    {t('backup.personalAccessToken')} {config?.has_token && <span className="text-green-400">{t('backup.tokenSaved')}</span>}
                  </label>
                  <input
                    type="password"
                    value={accessToken}
                    onChange={(e) => { setAccessToken(e.target.value); setTestResult(null); }}
                    placeholder={config?.has_token ? t('backup.enterNewToken') : 'ghp_xxxxxxxxxxxx'}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('backup.tokenHint')}
                  </p>
                </div>

            {/* Branch - inline with schedule */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('backup.branch')}</label>
                <input
                  type="text"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('backup.autoBackup')}</label>
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
                  <option value="disabled">{t('backup.manualOnly')}</option>
                  <option value="hourly">{t('backup.hourly')}</option>
                  <option value="daily">{t('backup.daily')}</option>
                  <option value="weekly">{t('backup.weekly')}</option>
                </select>
              </div>
            </div>

            {/* What to backup */}
            <div>
              <label className="block text-sm text-bambu-gray mb-2">{t('backup.includeInBackup')}</label>
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
                      <span className={`text-sm ${noPrintersConnected ? 'text-bambu-gray' : 'text-white'}`}>{t('backup.kProfiles')}</span>
                      {noPrintersConnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {t('backup.noPrintersConnected')}
                        </span>
                      )}
                      {somePrintersDisconnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {t('backup.printersConnected', { connected: connectedPrinters, total: totalPrinters })}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-bambu-gray">{t('backup.kProfilesDescription')}</p>
                  </div>
                </label>
                <label className={`flex items-start gap-2 ${!cloudStatus?.is_authenticated ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'}`}>
                  <input
                    type="checkbox"
                    checked={backupCloudProfiles}
                    onChange={(e) => setBackupCloudProfiles(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    disabled={!cloudStatus?.is_authenticated}
                  />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`text-sm ${cloudStatus?.is_authenticated ? 'text-white' : 'text-bambu-gray'}`}>{t('backup.cloudProfiles')}</span>
                      {!cloudStatus?.is_authenticated && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {t('backup.cloudLoginRequiredShort')}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-bambu-gray">{t('backup.cloudProfilesDescription')}</p>
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
                    <span className="text-white text-sm">{t('backup.appSettings')}</span>
                    <p className="text-xs text-bambu-gray">{t('backup.appSettingsDescription')}</p>
                  </div>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={backupSpools}
                    onChange={(e) => setBackupSpools(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                  />
                  <div>
                    <span className="text-white text-sm">{t('backup.spoolInventory')}</span>
                    <p className="text-xs text-bambu-gray">{t('backup.spoolInventoryDescription')}</p>
                  </div>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={backupArchives}
                    onChange={(e) => setBackupArchives(e.target.checked)}
                    className="w-4 h-4 mt-0.5 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                  />
                  <div>
                    <span className="text-white text-sm">{t('backup.printArchives')}</span>
                    <p className="text-xs text-bambu-gray">{t('backup.printArchivesDescription')}</p>
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
                        <span>{t('backup.lastBackupAt')} {formatRelativeTime(status.last_backup_at, 'system', t)}</span>
                        <StatusBadge status={status.last_backup_status} />
                      </>
                    ) : (
                      <span>{t('backup.noBackupsYet')}</span>
                    )}
                  </div>
                  {status.next_scheduled_run && (
                    <span className="text-bambu-gray">
                      <Clock className="w-3 h-3 inline mr-1" />
                      {t('backup.next')} {formatRelativeTime(status.next_scheduled_run, 'system', t)}
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
                        <span className="text-sm">{status.progress || t('backup.startingBackup')}</span>
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
                          {t('backup.backupNow')}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={handleTestConnection}
                          disabled={testLoading}
                        >
                          {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                          {t('backup.test')}
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
                      {t('backup.enableBackup')}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleTestConnection}
                      disabled={testLoading || !repoUrl || !accessToken}
                    >
                      {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                      {t('backup.testConnection')}
                    </Button>
                  </>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Backup History - only show if configured and has logs */}
        {logs && logs.length > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <History className="w-5 h-5 text-gray-400" />
                  <h2 className="text-lg font-semibold text-white">{t('backup.history')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => clearLogsMutation.mutate()}
                  disabled={clearLogsMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('backup.clear')}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-bambu-gray border-b border-bambu-dark-tertiary">
                      <th className="text-left py-2 px-2">{t('backup.date')}</th>
                      <th className="text-left py-2 px-2">{t('backup.status')}</th>
                      <th className="text-left py-2 px-2">{t('backup.commit')}</th>
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
              <h2 className="text-lg font-semibold text-white">{t('backup.localBackup')}</h2>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-bambu-gray">
              {t('backup.localBackupDescription')}
            </p>

            {/* Export */}
            <div className="flex items-center justify-between py-3 border-b border-bambu-dark-tertiary">
              <div>
                <p className="text-white">{t('backup.downloadBackupLabel')}</p>
                <p className="text-sm text-bambu-gray">
                  {t('backup.completeBackupZip')}
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                disabled={isExporting || isRestoring}
                onClick={async () => {
                  setIsExporting(true);
                  setOperationStatus(t('backup.preparingBackup'));
                  try {
                    setOperationStatus(t('backup.creatingArchive'));
                    const { blob, filename } = await api.exportBackup();
                    setOperationStatus(t('backup.downloadingFile'));
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    a.click();
                    URL.revokeObjectURL(url);
                    showToast(t('backup.backupDownloaded'));
                  } catch (e) {
                    showToast(t('backup.failedToCreateBackup', { message: e instanceof Error ? e.message : 'Unknown error' }), 'error');
                  } finally {
                    setIsExporting(false);
                    setOperationStatus('');
                  }
                }}
              >
                <Download className="w-4 h-4" />
                {t('backup.download')}
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
                {t('backup.restore')}
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
                          {t('backup.reloadNow')}
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
                  <span className="font-medium">{t('backup.restoreReplacesAll')}</span>{' '}
                  <span className="text-yellow-200/70">{t('backup.restoreReplacesAllDetail')}</span>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Scheduled Local Backups */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <FolderArchive className="w-5 h-5 text-gray-400" />
                <h2 className="text-lg font-semibold text-white">{t('backup.scheduledBackup')}</h2>
              </div>
              <Toggle
                checked={localBackupStatus?.enabled ?? false}
                onChange={async (checked) => {
                  try {
                    await api.updateSettings({ local_backup_enabled: checked });
                    showToast(t('backup.settingsSaved'));
                  } catch (e) {
                    showToast(t('backup.failedToSave', { message: e instanceof Error ? e.message : 'Unknown error' }), 'error');
                  }
                  refetchLocalStatus();
                }}
              />
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-bambu-gray">
              {t('backup.scheduledBackupDescription')}
            </p>

            {localBackupStatus?.enabled && (
              <>
                {/* Schedule + Time + Retention */}
                <div className="grid grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('backup.frequency')}</label>
                    <select
                      value={localBackupStatus?.schedule ?? 'daily'}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      onChange={async (e) => {
                        try {
                          await api.updateSettings({ local_backup_schedule: e.target.value });
                          showToast(t('backup.settingsSaved'));
                        } catch (e) {
                          showToast(t('backup.failedToSave', { message: e instanceof Error ? e.message : 'Unknown error' }), 'error');
                        }
                        refetchLocalStatus();
                      }}
                    >
                      <option value="hourly">{t('backup.hourly')}</option>
                      <option value="daily">{t('backup.daily')}</option>
                      <option value="weekly">{t('backup.weekly')}</option>
                    </select>
                  </div>
                  {(localBackupStatus?.schedule ?? 'daily') !== 'hourly' && (
                    <div>
                      <label className="block text-sm text-bambu-gray mb-1">{t('backup.backupTime')}</label>
                      <input
                        type="time"
                        value={localBackupStatus?.time ?? '03:00'}
                        className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none [color-scheme:dark]"
                        onChange={async (e) => {
                          try {
                            await api.updateSettings({ local_backup_time: e.target.value });
                            showToast(t('backup.settingsSaved'));
                          } catch (err) {
                            showToast(t('backup.failedToSave', { message: err instanceof Error ? err.message : 'Unknown error' }), 'error');
                          }
                          refetchLocalStatus();
                        }}
                      />
                      <p className="text-xs text-bambu-gray-light mt-1">{t('backup.utc')}</p>
                    </div>
                  )}
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('backup.retention')}</label>
                    <input
                      type="number"
                      min={1}
                      max={100}
                      value={localBackupStatus?.retention ?? 5}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      onChange={async (e) => {
                        const val = Math.max(1, Math.min(100, parseInt(e.target.value) || 5));
                        try {
                          await api.updateSettings({ local_backup_retention: val });
                          showToast(t('backup.settingsSaved'));
                        } catch (e) {
                          showToast(t('backup.failedToSave', { message: e instanceof Error ? e.message : 'Unknown error' }), 'error');
                        }
                        refetchLocalStatus();
                      }}
                    />
                    <p className="text-xs text-bambu-gray-light mt-1">{t('backup.retentionDescription')}</p>
                  </div>
                </div>

                {/* Output Path */}
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">{t('backup.outputPath')}</label>
                  <input
                    type="text"
                    value={localBackupPath}
                    onChange={(e) => setLocalBackupPath(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    onBlur={async () => {
                      try {
                        await api.updateSettings({ local_backup_path: localBackupPath });
                        showToast(t('backup.settingsSaved'));
                      } catch (err) {
                        showToast(t('backup.failedToSave', { message: err instanceof Error ? err.message : 'Unknown error' }), 'error');
                      }
                      refetchLocalStatus();
                      refetchLocalBackups();
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
                    }}
                  />
                  <p className="text-xs text-bambu-gray-light mt-1">
                    {localBackupPath
                      ? t('backup.outputPathDescription')
                      : <>{t('backup.defaultPathLabel')} <code className="text-bambu-gray">{localBackupStatus?.default_path || '...'}</code></>
                    }
                  </p>
                </div>

                {/* Status + Run Now */}
                <div className="flex items-center justify-between py-3 border-t border-bambu-dark-tertiary">
                  <div className="text-sm">
                    {localBackupStatus?.last_backup_at && (
                      <div className="flex items-center gap-2 text-bambu-gray">
                        <span>{t('backup.lastBackup')}:</span>
                        <StatusBadge status={localBackupStatus.last_status} />
                        <span>{formatRelativeTime(localBackupStatus.last_backup_at)}</span>
                      </div>
                    )}
                    {localBackupStatus?.next_run && (
                      <div className="text-bambu-gray mt-1">
                        <span>{t('backup.nextBackup')}: </span>
                        <span>{formatDateTime(localBackupStatus.next_run)}</span>
                      </div>
                    )}
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={localBackupStatus?.is_running || triggerLocalBackupMutation.isPending}
                    onClick={() => triggerLocalBackupMutation.mutate()}
                  >
                    {localBackupStatus?.is_running || triggerLocalBackupMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Play className="w-4 h-4" />
                    )}
                    {localBackupStatus?.is_running ? t('backup.backupRunning') : t('backup.runNow')}
                  </Button>
                </div>

                {/* Backup Files List */}
                {localBackups && localBackups.length > 0 && (
                  <div className="border-t border-bambu-dark-tertiary pt-3">
                    <h3 className="text-sm font-medium text-white mb-2">{t('backup.backupFiles')}</h3>
                    <div className="space-y-1">
                      {localBackups.map((file) => (
                        <div key={file.filename} className="flex items-center justify-between py-1.5 px-2 rounded hover:bg-bambu-dark-tertiary/50 text-sm">
                          <div className="flex-1 min-w-0">
                            <span className="text-white truncate block">{file.filename}</span>
                            <span className="text-bambu-gray text-xs">
                              {(file.size / 1024 / 1024).toFixed(1)} MB &middot; {formatDateTime(file.created_at)}
                            </span>
                          </div>
                          <div className="flex items-center gap-1 flex-shrink-0">
                            <button
                              className="text-bambu-gray hover:text-bambu-green p-1"
                              title={t('backup.download')}
                              onClick={async () => {
                                try {
                                  const { blob, filename: fname } = await api.downloadLocalBackup(file.filename);
                                  const url = URL.createObjectURL(blob);
                                  const a = document.createElement('a');
                                  a.href = url;
                                  a.download = fname;
                                  a.click();
                                  URL.revokeObjectURL(url);
                                } catch {
                                  showToast(t('backup.scheduledBackupFailed'), 'error');
                                }
                              }}
                            >
                              <Download className="w-3.5 h-3.5" />
                            </button>
                            <button
                              className="text-bambu-gray hover:text-yellow-400 p-1"
                              title={t('backup.restore')}
                              onClick={() => setRestoreConfirmFile(file.filename)}
                            >
                              <RotateCcw className="w-3.5 h-3.5" />
                            </button>
                            <button
                              className="text-bambu-gray hover:text-red-400 p-1"
                              onClick={() => setDeleteConfirmFile(file.filename)}
                              title={t('backup.deleteBackup')}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {localBackups && localBackups.length === 0 && (
                  <p className="text-sm text-bambu-gray text-center py-3 border-t border-bambu-dark-tertiary">
                    {t('backup.noScheduledBackups')}
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Delete Backup Confirmation Modal */}
      {deleteConfirmFile && (
        <ConfirmModal
          title={t('backup.deleteBackup')}
          message={t('backup.deleteBackupConfirm')}
          confirmText={t('backup.deleteBackup')}
          variant="danger"
          onConfirm={() => deleteLocalBackupMutation.mutate(deleteConfirmFile)}
          onCancel={() => setDeleteConfirmFile(null)}
        />
      )}

      {/* Restore from Scheduled Backup Confirmation Modal */}
      {restoreConfirmFile && (
        <ConfirmModal
          title={t('backup.restoreConfirmTitle')}
          message={t('backup.restoreConfirmMessage', { filename: restoreConfirmFile })}
          confirmText={t('backup.restoreConfirmButton')}
          variant="danger"
          onConfirm={() => restoreLocalBackupMutation.mutate(restoreConfirmFile)}
          onCancel={() => setRestoreConfirmFile(null)}
        />
      )}

      {/* Restore Confirmation Modal */}
      {showRestoreConfirm && restoreFile && (
        <ConfirmModal
          title={t('backup.restoreConfirmTitle')}
          message={t('backup.restoreConfirmMessage', { filename: restoreFile.name })}
          confirmText={t('backup.restoreConfirmButton')}
          variant="danger"
          onConfirm={async () => {
            setShowRestoreConfirm(false);
            setIsRestoring(true);
            setRestoreResult(null);
            try {
              setOperationStatus(t('backup.uploadingFile'));
              const result = await api.importBackup(restoreFile);
              setRestoreResult(result);
              if (result.success) {
                showToast(t('backup.backupRestoredRestart'), 'success');
              } else {
                showToast(result.message, 'error');
              }
            } catch (e) {
              const message = e instanceof Error ? e.message : t('backup.failedToRestore');
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
              {isExporting ? t('backup.creatingBackup') : t('backup.restoringBackup')}
            </h3>
            <p className="text-bambu-gray mb-4">
              {operationStatus || (isExporting ? t('backup.preparing') : t('backup.processing'))}
            </p>
            <div className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
              <div className="flex items-start gap-2 text-sm">
                <AlertTriangle className="w-4 h-4 text-yellow-400 mt-0.5 flex-shrink-0" />
                <p className="text-yellow-200 text-left">
                  {t('backup.doNotClosePage')}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
