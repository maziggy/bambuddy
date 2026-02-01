import { useState, useEffect, useRef, useCallback } from 'react';
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
import { BackupModal } from './BackupModal';
import { RestoreModal } from './RestoreModal';
import { useToast } from '../contexts/ToastContext';
import { useTranslation } from 'react-i18next';

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

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffMins = Math.round(diffMs / 60000);

  if (diffMins < 0) {
    const absMins = Math.abs(diffMins);
    if (absMins < 60) return `${absMins}m ago`;
    const hours = Math.floor(absMins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
  } else {
    if (diffMins < 60) return `in ${diffMins}m`;
    const hours = Math.floor(diffMins / 60);
    if (hours < 24) return `in ${hours}h`;
    const days = Math.floor(hours / 24);
    return `in ${days}d`;
  }
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

  // Local backup modals
  const [showBackupModal, setShowBackupModal] = useState(false);
  const [showRestoreModal, setShowRestoreModal] = useState(false);

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
        showToast(t('githubBackup.tokenUpdated'));
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
        showToast(t('githubBackup.settingsSaved'));
      }
      queryClient.invalidateQueries({ queryKey: ['github-backup-config'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
    } catch (error) {
      showToast(t('githubBackup.failedToSave', { error: (error as Error).message }), 'error');
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
      showToast(t('githubBackup.backupEnabled'));
      setAccessToken('');
      isInitializedRef.current = true;
    },
    onError: (error: Error) => {
      showToast(t('githubBackup.failedToSave', { error: error.message }), 'error');
    },
  });

  const triggerBackupMutation = useMutation<GitHubBackupTriggerResponse, Error>({
    mutationFn: api.triggerGitHubBackup,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-status'] });
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      if (result.success) {
        if (result.files_changed > 0) {
          showToast(t('githubBackup.backupComplete', { count: result.files_changed }));
        } else {
          showToast(t('githubBackup.backupSkipped'));
        }
      } else {
        showToast(t('githubBackup.backupFailed', { error: result.message }), 'error');
      }
    },
    onError: (error: Error) => {
      showToast(t('githubBackup.backupFailed', { error: error.message }), 'error');
    },
  });

  const clearLogsMutation = useMutation<{ deleted: number; message: string }, Error>({
    mutationFn: () => api.clearGitHubBackupLogs(0),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['github-backup-logs'] });
      showToast(t('githubBackup.logsCleared', { count: result.deleted }));
    },
    onError: (error: Error) => {
      showToast(t('githubBackup.failedToClearLogs', { error: error.message }), 'error');
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
          showToast(t('githubBackup.enterRepoUrl'), 'error');
          setTestLoading(false);
          return;
        }
        result = await api.testGitHubConnection(repoUrl, accessToken);
      } else if (config?.has_token) {
        // Use stored credentials
        result = await api.testGitHubStoredConnection();
      } else {
        showToast(t('githubBackup.enterRepoUrlAndToken'), 'error');
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
      showToast(t('githubBackup.repoUrlRequired'), 'error');
      return;
    }
    if (!accessToken) {
      showToast(t('githubBackup.tokenRequired'), 'error');
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
                <h2 className="text-lg font-semibold text-white">{t('githubBackup.title')}</h2>
              </div>
              {config && cloudStatus?.is_authenticated && (
                <div className="flex items-center gap-2">
                  <span className="text-sm text-bambu-gray">{t('githubBackup.enabled')}</span>
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
                  {t('githubBackup.cloudLoginRequired')}
                </p>
              </div>
            ) : (
              <>
                <p className="text-sm text-bambu-gray">
                  {t('githubBackup.description')}
                </p>

                {/* Repository URL */}
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('githubBackup.repositoryUrl')}
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
                    {t('githubBackup.personalAccessToken')} {config?.has_token && <span className="text-green-400">({t('githubBackup.saved')})</span>}
                  </label>
                  <input
                    type="password"
                    value={accessToken}
                    onChange={(e) => { setAccessToken(e.target.value); setTestResult(null); }}
                    placeholder={config?.has_token ? t('githubBackup.enterNewToken') : 'ghp_xxxxxxxxxxxx'}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('githubBackup.tokenHint')}
                  </p>
                </div>

            {/* Branch - inline with schedule */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('githubBackup.branch')}</label>
                <input
                  type="text"
                  value={branch}
                  onChange={(e) => setBranch(e.target.value)}
                  placeholder="main"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('githubBackup.autoBackup')}</label>
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
                  <option value="disabled">{t('githubBackup.manualOnly')}</option>
                  <option value="hourly">{t('githubBackup.hourly')}</option>
                  <option value="daily">{t('githubBackup.daily')}</option>
                  <option value="weekly">{t('githubBackup.weekly')}</option>
                </select>
              </div>
            </div>

            {/* What to backup */}
            <div>
              <label className="block text-sm text-bambu-gray mb-2">{t('githubBackup.includeInBackup')}</label>
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
                      <span className={`text-sm ${noPrintersConnected ? 'text-bambu-gray' : 'text-white'}`}>{t('githubBackup.kProfiles')}</span>
                      {noPrintersConnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {t('githubBackup.noPrintersConnected')}
                        </span>
                      )}
                      {somePrintersDisconnected && (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                          <AlertTriangle className="w-3 h-3" />
                          {t('githubBackup.printersConnected', { connected: connectedPrinters, total: totalPrinters })}
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-bambu-gray">{t('githubBackup.kProfilesDescription')}</p>
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
                    <span className={`text-sm ${cloudStatus?.is_authenticated ? 'text-white' : 'text-bambu-gray'}`}>{t('githubBackup.cloudProfiles')}</span>
                    <p className="text-xs text-bambu-gray">{t('githubBackup.cloudProfilesDescription')}</p>
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
                    <span className="text-white text-sm">{t('githubBackup.appSettings')}</span>
                    <p className="text-xs text-bambu-gray">{t('githubBackup.appSettingsDescription')}</p>
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
                        <span>{t('githubBackup.lastBackup')}: {formatRelativeTime(status.last_backup_at)}</span>
                        <StatusBadge status={status.last_backup_status} />
                      </>
                    ) : (
                      <span>{t('githubBackup.noBackupsYet')}</span>
                    )}
                  </div>
                  {status.next_scheduled_run && (
                    <span className="text-bambu-gray">
                      <Clock className="w-3 h-3 inline mr-1" />
                      {t('githubBackup.next')}: {formatRelativeTime(status.next_scheduled_run)}
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
                        <span className="text-sm">{status.progress || t('githubBackup.startingBackup')}</span>
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
                          {t('githubBackup.backupNow')}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={handleTestConnection}
                          disabled={testLoading}
                        >
                          {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                          {t('githubBackup.test')}
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
                      {t('githubBackup.enableBackup')}
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleTestConnection}
                      disabled={testLoading || !repoUrl || !accessToken}
                    >
                      {testLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                      {t('githubBackup.testConnection')}
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
                  <h2 className="text-lg font-semibold text-white">{t('githubBackup.history')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => clearLogsMutation.mutate()}
                  disabled={clearLogsMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('common.clear')}
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-bambu-gray border-b border-bambu-dark-tertiary">
                      <th className="text-left py-2 px-2">{t('githubBackup.date')}</th>
                      <th className="text-left py-2 px-2">{t('githubBackup.status')}</th>
                      <th className="text-left py-2 px-2">{t('githubBackup.commit')}</th>
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
              <h2 className="text-lg font-semibold text-white">{t('githubBackup.localBackup')}</h2>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-bambu-gray">
              {t('githubBackup.localDescription')}
            </p>

            <div className="flex items-center justify-between py-3 border-b border-bambu-dark-tertiary">
              <div>
                <p className="text-white">{t('githubBackup.exportData')}</p>
                <p className="text-sm text-bambu-gray">
                  {t('githubBackup.exportDescription')}
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowBackupModal(true)}
              >
                <Download className="w-4 h-4" />
                {t('common.export')}
              </Button>
            </div>

            <div className="flex items-center justify-between py-3">
              <div>
                <p className="text-white">{t('githubBackup.importBackup')}</p>
                <p className="text-sm text-bambu-gray">
                  {t('githubBackup.importDescription')}
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowRestoreModal(true)}
              >
                <Upload className="w-4 h-4" />
                {t('common.import')}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Modals */}
      {showBackupModal && (
        <BackupModal
          onClose={() => setShowBackupModal(false)}
          onExport={async (categories) => {
            setShowBackupModal(false);
            try {
              const { blob, filename } = await api.exportBackup(categories);
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = filename;
              a.click();
              URL.revokeObjectURL(url);
              showToast(t('githubBackup.backupDownloaded'));
            } catch {
              showToast(t('githubBackup.failedToCreateBackup'), 'error');
            }
          }}
        />
      )}

      {showRestoreModal && (
        <RestoreModal
          onClose={() => setShowRestoreModal(false)}
          onRestore={async (file, overwrite) => {
            return await api.importBackup(file, overwrite);
          }}
          onSuccess={() => {
            setShowRestoreModal(false);
            showToast(t('githubBackup.backupRestored'));
            queryClient.invalidateQueries();
          }}
        />
      )}
    </div>
  );
}
