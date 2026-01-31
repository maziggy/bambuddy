import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Plug, AlertTriangle, RotateCcw, Bell, Download, RefreshCw, ExternalLink, Globe, Droplets, Thermometer, FileText, Edit2, Send, CheckCircle, XCircle, History, Trash2, Upload, Zap, TrendingUp, Calendar, DollarSign, Power, PowerOff, Key, Copy, Database, Info, X, Shield, Printer, Cylinder, Wifi, Home, Video, Users, Lock, Unlock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { formatDateOnly } from '../utils/date';
import type { AppSettings, AppSettingsUpdate, SmartPlug, SmartPlugStatus, NotificationProvider, NotificationTemplate, UpdateStatus } from '../api/client';
import { Card, CardContent, CardHeader } from '../components/Card';
import { Button } from '../components/Button';
import { SmartPlugCard } from '../components/SmartPlugCard';
import { AddSmartPlugModal } from '../components/AddSmartPlugModal';
import { NotificationProviderCard } from '../components/NotificationProviderCard';
import { AddNotificationModal } from '../components/AddNotificationModal';
import { NotificationTemplateEditor } from '../components/NotificationTemplateEditor';
import { NotificationLogViewer } from '../components/NotificationLogViewer';
import { ConfirmModal } from '../components/ConfirmModal';
import { BackupModal } from '../components/BackupModal';
import { RestoreModal } from '../components/RestoreModal';
import { SpoolmanSettings } from '../components/SpoolmanSettings';
import { ExternalLinksSettings } from '../components/ExternalLinksSettings';
import { VirtualPrinterSettings } from '../components/VirtualPrinterSettings';
import { APIBrowser } from '../components/APIBrowser';
import { virtualPrinterApi } from '../api/client';
import { defaultNavItems, getDefaultView, setDefaultView } from '../components/Layout';
import { availableLanguages } from '../i18n';
import { useToast } from '../contexts/ToastContext';
import { useTheme, type ThemeStyle, type DarkBackground, type LightBackground, type ThemeAccent } from '../contexts/ThemeContext';
import { useState, useEffect, useRef, useCallback } from 'react';
import { Palette } from 'lucide-react';

const validTabs = ['general', 'network', 'plugs', 'notifications', 'filament', 'apikeys', 'virtual-printer', 'users'] as const;
type TabType = typeof validTabs[number];

export function SettingsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, i18n } = useTranslation();
  const { showToast, showPersistentToast, dismissToast } = useToast();
  const { authEnabled, user, refreshAuth } = useAuth();
  const {
    mode,
    darkStyle, darkBackground, darkAccent,
    lightStyle, lightBackground, lightAccent,
    setDarkStyle, setDarkBackground, setDarkAccent,
    setLightStyle, setLightBackground, setLightAccent,
  } = useTheme();
  const [localSettings, setLocalSettings] = useState<AppSettings | null>(null);
  const [showPlugModal, setShowPlugModal] = useState(false);
  const [editingPlug, setEditingPlug] = useState<SmartPlug | null>(null);
  const [showNotificationModal, setShowNotificationModal] = useState(false);
  const [editingProvider, setEditingProvider] = useState<NotificationProvider | null>(null);
  const [editingTemplate, setEditingTemplate] = useState<NotificationTemplate | null>(null);
  const [showLogViewer, setShowLogViewer] = useState(false);
  const [defaultView, setDefaultViewState] = useState<string>(getDefaultView());

  // Initialize tab from URL params
  const tabParam = searchParams.get('tab');
  const initialTab = tabParam && validTabs.includes(tabParam as TabType) ? tabParam as TabType : 'general';
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);

  // Update URL when tab changes
  const handleTabChange = (tab: TabType) => {
    setActiveTab(tab);
    if (tab === 'general') {
      searchParams.delete('tab');
    } else {
      searchParams.set('tab', tab);
    }
    setSearchParams(searchParams, { replace: true });
  };
  const [showCreateAPIKey, setShowCreateAPIKey] = useState(false);
  const [newAPIKeyName, setNewAPIKeyName] = useState('');
  const [newAPIKeyPermissions, setNewAPIKeyPermissions] = useState({
    can_queue: true,
    can_control_printer: false,
    can_read_status: true,
  });
  const [createdAPIKey, setCreatedAPIKey] = useState<string | null>(null);
  const [showDeleteAPIKeyConfirm, setShowDeleteAPIKeyConfirm] = useState<number | null>(null);
  const [testApiKey, setTestApiKey] = useState('');

  // Confirm modal states
  const [showClearLogsConfirm, setShowClearLogsConfirm] = useState(false);
  const [showClearStorageConfirm, setShowClearStorageConfirm] = useState(false);
  const [showBulkPlugConfirm, setShowBulkPlugConfirm] = useState<'on' | 'off' | null>(null);
  const [showBackupModal, setShowBackupModal] = useState(false);
  const [showRestoreModal, setShowRestoreModal] = useState(false);
  const [showTelemetryInfo, setShowTelemetryInfo] = useState(false);
  const [showReleaseNotes, setShowReleaseNotes] = useState(false);
  const [showDisableAuthConfirm, setShowDisableAuthConfirm] = useState(false);

  // Home Assistant test connection state
  const [haTestResult, setHaTestResult] = useState<{ success: boolean; message: string | null; error: string | null } | null>(null);
  const [haTestLoading, setHaTestLoading] = useState(false);

  const handleDefaultViewChange = (path: string) => {
    setDefaultViewState(path);
    setDefaultView(path);
  };

  const handleResetSidebarOrder = () => {
    localStorage.removeItem('sidebarOrder');
    window.location.reload();
  };

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const { data: smartPlugs, isLoading: plugsLoading } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: api.getSmartPlugs,
  });

  // Fetch energy data for all smart plugs when on the plugs tab
  const { data: plugEnergySummary, isLoading: energyLoading } = useQuery({
    queryKey: ['smart-plugs-energy', smartPlugs?.map(p => p.id)],
    queryFn: async () => {
      if (!smartPlugs || smartPlugs.length === 0) return null;
      const statuses = await Promise.all(
        smartPlugs.filter(p => p.enabled).map(async (plug) => {
          try {
            const status = await api.getSmartPlugStatus(plug.id);
            return { plug, status };
          } catch {
            return { plug, status: null as SmartPlugStatus | null };
          }
        })
      );

      // Aggregate energy data
      let totalPower = 0;
      let totalToday = 0;
      let totalYesterday = 0;
      let totalLifetime = 0;
      let reachableCount = 0;

      for (const { status } of statuses) {
        if (status?.reachable && status.energy) {
          reachableCount++;
          if (status.energy.power != null) totalPower += status.energy.power;
          if (status.energy.today != null) totalToday += status.energy.today;
          if (status.energy.yesterday != null) totalYesterday += status.energy.yesterday;
          if (status.energy.total != null) totalLifetime += status.energy.total;
        }
      }

      return {
        totalPower,
        totalToday,
        totalYesterday,
        totalLifetime,
        reachableCount,
        totalPlugs: smartPlugs.filter(p => p.enabled).length,
      };
    },
    enabled: activeTab === 'plugs' && !!smartPlugs && smartPlugs.length > 0,
    refetchInterval: activeTab === 'plugs' ? 10000 : false, // Refresh every 10s when on plugs tab
  });

  const { data: notificationProviders, isLoading: providersLoading } = useQuery({
    queryKey: ['notification-providers'],
    queryFn: api.getNotificationProviders,
  });

  const { data: apiKeys, isLoading: apiKeysLoading } = useQuery({
    queryKey: ['api-keys'],
    queryFn: api.getAPIKeys,
  });

  const createAPIKeyMutation = useMutation({
    mutationFn: (data: { name: string; can_queue: boolean; can_control_printer: boolean; can_read_status: boolean }) =>
      api.createAPIKey(data),
    onSuccess: (data) => {
      setCreatedAPIKey(data.key || null);
      setShowCreateAPIKey(false);
      setNewAPIKeyName('');
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      showToast(t('settings.apiKeyCreated'));
    },
    onError: (error: Error) => {
      showToast(t('settings.apiKeyCreateFailed', { error: error.message }), 'error');
    },
  });

  const deleteAPIKeyMutation = useMutation({
    mutationFn: (id: number) => api.deleteAPIKey(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      showToast(t('settings.apiKeyDeleted'));
    },
    onError: (error: Error) => {
      showToast(t('settings.apiKeyDeleteFailed', { error: error.message }), 'error');
    },
  });

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: notificationTemplates, isLoading: templatesLoading } = useQuery({
    queryKey: ['notification-templates'],
    queryFn: api.getNotificationTemplates,
  });

  // Virtual printer status for tab indicator
  const { data: virtualPrinterSettings } = useQuery({
    queryKey: ['virtual-printer-settings'],
    queryFn: virtualPrinterApi.getSettings,
    refetchInterval: 10000,
  });
  const virtualPrinterRunning = virtualPrinterSettings?.status?.running ?? false;

  const { data: ffmpegStatus } = useQuery({
    queryKey: ['ffmpeg-status'],
    queryFn: api.checkFfmpeg,
  });

  const { data: versionInfo } = useQuery({
    queryKey: ['version'],
    queryFn: api.getVersion,
  });

  const { data: updateCheck, refetch: refetchUpdateCheck, isRefetching: isCheckingUpdate } = useQuery({
    queryKey: ['updateCheck'],
    queryFn: api.checkForUpdates,
    staleTime: 5 * 60 * 1000,
  });

  const { data: updateStatus, refetch: refetchUpdateStatus } = useQuery({
    queryKey: ['updateStatus'],
    queryFn: api.getUpdateStatus,
    refetchInterval: (query) => {
      const status = query.state.data as UpdateStatus | undefined;
      // Poll while update is in progress
      if (status?.status === 'downloading' || status?.status === 'installing') {
        return 1000;
      }
      return false;
    },
  });

  // MQTT status for Network tab
  const { data: mqttStatus } = useQuery({
    queryKey: ['mqtt-status'],
    queryFn: api.getMQTTStatus,
    refetchInterval: activeTab === 'network' ? 5000 : false, // Poll every 5s when on Network tab
  });

  const applyUpdateMutation = useMutation({
    mutationFn: api.applyUpdate,
    onSuccess: (data) => {
      if (data.is_docker) {
        showToast(data.message, 'error');
      } else {
        refetchUpdateStatus();
      }
    },
  });

  // Test all notification providers
  const [testAllResult, setTestAllResult] = useState<{
    tested: number;
    success: number;
    failed: number;
    results: Array<{
      provider_id: number;
      provider_name: string;
      provider_type: string;
      success: boolean;
      message: string;
    }>;
  } | null>(null);

  const testAllMutation = useMutation({
    mutationFn: api.testAllNotificationProviders,
    onSuccess: (data) => {
      setTestAllResult(data);
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
      if (data.failed === 0) {
        showToast(t('settings.testAllSuccess', { count: data.tested }), 'success');
      } else {
        showToast(t('settings.testAllPartial', { success: data.success, tested: data.tested }), data.failed > 0 ? 'error' : 'success');
      }
    },
    onError: (error: Error) => {
      showToast(t('settings.testProvidersFailed', { error: error.message }), 'error');
    },
  });

  // Bulk action for smart plugs
  const bulkPlugActionMutation = useMutation({
    mutationFn: async (action: 'on' | 'off') => {
      if (!smartPlugs) return { success: 0, failed: 0 };
      const enabledPlugs = smartPlugs.filter(p => p.enabled);
      const results = await Promise.all(
        enabledPlugs.map(async (plug) => {
          try {
            await api.controlSmartPlug(plug.id, action);
            return { success: true };
          } catch {
            return { success: false };
          }
        })
      );
      return {
        success: results.filter(r => r.success).length,
        failed: results.filter(r => !r.success).length,
      };
    },
    onSuccess: (data, action) => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      queryClient.invalidateQueries({ queryKey: ['smart-plugs-energy'] });
      if (data.failed === 0) {
        showToast(t('settings.bulkPlugSuccess', { count: data.success, action }), 'success');
      } else {
        showToast(t('settings.bulkPlugPartial', { success: data.success, action, failed: data.failed }), 'error');
      }
    },
    onError: (error: Error) => {
      showToast(t('settings.bulkPlugFailed', { error: error.message }), 'error');
    },
  });

  // Ref for debounce timeout
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isSavingRef = useRef(false);
  const isInitialLoadRef = useRef(true);

  // Sync local state when settings load
  useEffect(() => {
    if (settings && !localSettings) {
      // Auto-detect external_url from browser if not set
      const settingsWithExternalUrl = {
        ...settings,
        external_url: settings.external_url || window.location.origin,
      };
      setLocalSettings(settingsWithExternalUrl);
      // Mark initial load complete after a short delay
      setTimeout(() => {
        isInitialLoadRef.current = false;
      }, 100);
    }
  }, [settings, localSettings]);

  const updateMutation = useMutation({
    mutationFn: api.updateSettings,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings'], data);
      // Sync localSettings with the saved data to prevent re-triggering saves
      setLocalSettings(data);
      // Invalidate archive stats to reflect energy tracking mode change
      queryClient.invalidateQueries({ queryKey: ['archiveStats'] });
      showToast(t('settings.saved'), 'success');
    },
    onError: (error: Error) => {
      showToast(t('settings.saveFailed', { error: error.message }), 'error');
    },
    onSettled: () => {
      // Reset saving flag when mutation completes (success or error)
      isSavingRef.current = false;
    },
  });

  // Debounced auto-save when localSettings change
  useEffect(() => {
    // Skip if initial load or no settings
    if (isInitialLoadRef.current || !localSettings || !settings) {
      return;
    }

    // Check if there are actual changes
    const hasChanges =
      settings.auto_archive !== localSettings.auto_archive ||
      settings.save_thumbnails !== localSettings.save_thumbnails ||
      settings.capture_finish_photo !== localSettings.capture_finish_photo ||
      settings.default_filament_cost !== localSettings.default_filament_cost ||
      settings.currency !== localSettings.currency ||
      settings.energy_cost_per_kwh !== localSettings.energy_cost_per_kwh ||
      settings.energy_tracking_mode !== localSettings.energy_tracking_mode ||
      settings.check_updates !== localSettings.check_updates ||
      settings.notification_language !== localSettings.notification_language ||
      settings.telemetry_enabled !== localSettings.telemetry_enabled ||
      settings.ams_humidity_good !== localSettings.ams_humidity_good ||
      settings.ams_humidity_fair !== localSettings.ams_humidity_fair ||
      settings.ams_temp_good !== localSettings.ams_temp_good ||
      settings.ams_temp_fair !== localSettings.ams_temp_fair ||
      settings.ams_history_retention_days !== localSettings.ams_history_retention_days ||
      settings.per_printer_mapping_expanded !== localSettings.per_printer_mapping_expanded ||
      settings.date_format !== localSettings.date_format ||
      settings.time_format !== localSettings.time_format ||
      settings.default_printer_id !== localSettings.default_printer_id ||
      settings.ftp_retry_enabled !== localSettings.ftp_retry_enabled ||
      settings.ftp_retry_count !== localSettings.ftp_retry_count ||
      settings.ftp_retry_delay !== localSettings.ftp_retry_delay ||
      settings.ftp_timeout !== localSettings.ftp_timeout ||
      settings.mqtt_enabled !== localSettings.mqtt_enabled ||
      settings.mqtt_broker !== localSettings.mqtt_broker ||
      settings.mqtt_port !== localSettings.mqtt_port ||
      settings.mqtt_username !== localSettings.mqtt_username ||
      settings.mqtt_password !== localSettings.mqtt_password ||
      settings.mqtt_topic_prefix !== localSettings.mqtt_topic_prefix ||
      settings.mqtt_use_tls !== localSettings.mqtt_use_tls ||
      settings.external_url !== localSettings.external_url ||
      settings.ha_enabled !== localSettings.ha_enabled ||
      settings.ha_url !== localSettings.ha_url ||
      settings.ha_token !== localSettings.ha_token ||
      (settings.library_archive_mode ?? 'ask') !== (localSettings.library_archive_mode ?? 'ask') ||
      Number(settings.library_disk_warning_gb ?? 5) !== Number(localSettings.library_disk_warning_gb ?? 5) ||
      (settings.camera_view_mode ?? 'window') !== (localSettings.camera_view_mode ?? 'window');

    if (!hasChanges) {
      return;
    }

    // Don't queue more saves while one is in progress
    if (isSavingRef.current) {
      return;
    }

    // Clear existing timeout
    if (saveTimeoutRef.current) {
      clearTimeout(saveTimeoutRef.current);
    }

    // Set new debounced save (500ms delay)
    saveTimeoutRef.current = setTimeout(() => {
      // Skip if a save is already in progress
      if (isSavingRef.current) {
        return;
      }
      isSavingRef.current = true;
      // Only send the fields we manage on this page (exclude virtual_printer_* which are managed separately)
      const settingsToSave: AppSettingsUpdate = {
        auto_archive: localSettings.auto_archive,
        save_thumbnails: localSettings.save_thumbnails,
        capture_finish_photo: localSettings.capture_finish_photo,
        default_filament_cost: localSettings.default_filament_cost,
        currency: localSettings.currency,
        energy_cost_per_kwh: localSettings.energy_cost_per_kwh,
        energy_tracking_mode: localSettings.energy_tracking_mode,
        check_updates: localSettings.check_updates,
        notification_language: localSettings.notification_language,
        telemetry_enabled: localSettings.telemetry_enabled,
        ams_humidity_good: localSettings.ams_humidity_good,
        ams_humidity_fair: localSettings.ams_humidity_fair,
        ams_temp_good: localSettings.ams_temp_good,
        ams_temp_fair: localSettings.ams_temp_fair,
        ams_history_retention_days: localSettings.ams_history_retention_days,
        per_printer_mapping_expanded: localSettings.per_printer_mapping_expanded,
        date_format: localSettings.date_format,
        time_format: localSettings.time_format,
        default_printer_id: localSettings.default_printer_id,
        ftp_retry_enabled: localSettings.ftp_retry_enabled,
        ftp_retry_count: localSettings.ftp_retry_count,
        ftp_retry_delay: localSettings.ftp_retry_delay,
        ftp_timeout: localSettings.ftp_timeout,
        mqtt_enabled: localSettings.mqtt_enabled,
        mqtt_broker: localSettings.mqtt_broker,
        mqtt_port: localSettings.mqtt_port,
        mqtt_username: localSettings.mqtt_username,
        mqtt_password: localSettings.mqtt_password,
        mqtt_topic_prefix: localSettings.mqtt_topic_prefix,
        mqtt_use_tls: localSettings.mqtt_use_tls,
        external_url: localSettings.external_url,
        ha_enabled: localSettings.ha_enabled,
        ha_url: localSettings.ha_url,
        ha_token: localSettings.ha_token,
        library_archive_mode: localSettings.library_archive_mode,
        library_disk_warning_gb: localSettings.library_disk_warning_gb,
        camera_view_mode: localSettings.camera_view_mode,
      };
      updateMutation.mutate(settingsToSave);
    }, 500);

    // Cleanup on unmount or when localSettings changes again
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [localSettings, settings, updateMutation]);

  const updateSetting = useCallback(<K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    setLocalSettings(prev => prev ? { ...prev, [key]: value } : null);
  }, []);

  if (isLoading || !localSettings) {
    return (
      <div className="p-4 md:p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  return (
    <div className="p-4 md:p-8">
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-white">{t('settings.title')}</h1>
        <p className="text-bambu-gray">{t('settings.subtitle')}</p>
      </div>

      {/* Tab Navigation */}
      <div className="flex gap-1 mb-6 border-b border-bambu-dark-tertiary overflow-x-auto">
        <button
          onClick={() => handleTabChange('general')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
            activeTab === 'general'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          {t('settings.tabGeneral')}
        </button>
        <button
          onClick={() => handleTabChange('plugs')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'plugs'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Plug className="w-4 h-4" />
          {t('settings.tabSmartPlugs')}
          {smartPlugs && smartPlugs.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {smartPlugs.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('notifications')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'notifications'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Bell className="w-4 h-4" />
          {t('settings.tabNotifications')}
          {notificationProviders && notificationProviders.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {notificationProviders.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('filament')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'filament'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Cylinder className="w-4 h-4" />
          {t('settings.tabFilament')}
        </button>
        <button
          onClick={() => handleTabChange('network')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'network'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Wifi className="w-4 h-4" />
          {t('settings.tabNetwork')}
          <span className={`w-2 h-2 rounded-full ${mqttStatus?.enabled ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('apikeys')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'apikeys'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Key className="w-4 h-4" />
          {t('settings.tabApiKeys')}
          {apiKeys && apiKeys.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {apiKeys.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('virtual-printer')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'virtual-printer'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Printer className="w-4 h-4" />
          {t('settings.tabVirtualPrinter')}
          <span className={`w-2 h-2 rounded-full ${virtualPrinterRunning ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('users')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
            activeTab === 'users'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Users className="w-4 h-4" />
          {t('settings.tabUsers')}
          {authEnabled && (
            <span className={`w-2 h-2 rounded-full ${authEnabled ? 'bg-green-400' : 'bg-gray-500'}`} />
          )}
        </button>
      </div>

      {/* General Tab */}
      {activeTab === 'general' && (
      <div className="flex flex-col lg:flex-row gap-6 lg:gap-8">
        {/* Left Column - General Settings */}
        <div className="space-y-6 flex-1 lg:max-w-xl">
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.general')}</h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  <Globe className="w-4 h-4 inline mr-1" />
                  {t('settings.language')}
                </label>
                <select
                  value={i18n.language}
                  onChange={(e) => i18n.changeLanguage(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  {availableLanguages.map((lang) => (
                    <option key={lang.code} value={lang.code}>
                      {lang.nativeName} ({lang.name})
                    </option>
                  ))}
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.languageDescription')}
                </p>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultView')}
                </label>
                <select
                  value={defaultView}
                  onChange={(e) => handleDefaultViewChange(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  {defaultNavItems.map((item) => (
                    <option key={item.id} value={item.to}>
                      {t(item.labelKey)}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.defaultViewDescription')}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('settings.dateFormat')}
                  </label>
                  <select
                    value={localSettings.date_format || 'system'}
                    onChange={(e) => updateSetting('date_format', e.target.value as 'system' | 'us' | 'eu' | 'iso')}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  >
                    <option value="system">{t('settings.systemDefault')}</option>
                    <option value="us">{t('settings.dateFormatUS')}</option>
                    <option value="eu">{t('settings.dateFormatEU')}</option>
                    <option value="iso">{t('settings.dateFormatISO')}</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('settings.timeFormat')}
                  </label>
                  <select
                    value={localSettings.time_format || 'system'}
                    onChange={(e) => updateSetting('time_format', e.target.value as 'system' | '12h' | '24h')}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  >
                    <option value="system">{t('settings.systemDefault')}</option>
                    <option value="12h">{t('settings.timeFormat12h')}</option>
                    <option value="24h">{t('settings.timeFormat24h')}</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultPrinter')}
                </label>
                <select
                  value={localSettings.default_printer_id ?? ''}
                  onChange={(e) => updateSetting('default_printer_id', e.target.value ? Number(e.target.value) : null)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="">{t('settings.noDefaultPrinter')}</option>
                  {printers?.map((printer) => (
                    <option key={printer.id} value={printer.id}>
                      {printer.name}
                    </option>
                  ))}
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.defaultPrinterDescription')}
                </p>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.sidebarOrder')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.sidebarOrderDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleResetSidebarOrder}
                >
                  <RotateCcw className="w-4 h-4" />
                  {t('settings.reset')}
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Palette className="w-5 h-5" />
                {t('settings.appearance')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Dark Mode Settings */}
              <div className={`space-y-3 p-4 rounded-lg border ${mode === 'dark' ? 'border-bambu-green bg-bambu-green/5' : 'border-bambu-dark-tertiary'}`}>
                <h3 className="text-sm font-medium text-white flex items-center gap-2">
                  {t('settings.darkMode')}
                  {mode === 'dark' && <span className="text-xs text-bambu-green">{t('settings.active')}</span>}
                </h3>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.background')}</label>
                    <select
                      value={darkBackground}
                      onChange={(e) => { setDarkBackground(e.target.value as DarkBackground); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="neutral">{t('settings.bgNeutral')}</option>
                      <option value="warm">{t('settings.bgWarm')}</option>
                      <option value="cool">{t('settings.bgCool')}</option>
                      <option value="oled">{t('settings.bgOLED')}</option>
                      <option value="slate">{t('settings.bgSlate')}</option>
                      <option value="forest">{t('settings.bgForest')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.accent')}</label>
                    <select
                      value={darkAccent}
                      onChange={(e) => { setDarkAccent(e.target.value as ThemeAccent); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="green">{t('settings.accentGreen')}</option>
                      <option value="teal">{t('settings.accentTeal')}</option>
                      <option value="blue">{t('settings.accentBlue')}</option>
                      <option value="orange">{t('settings.accentOrange')}</option>
                      <option value="purple">{t('settings.accentPurple')}</option>
                      <option value="red">{t('settings.accentRed')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.style')}</label>
                    <select
                      value={darkStyle}
                      onChange={(e) => { setDarkStyle(e.target.value as ThemeStyle); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="classic">{t('settings.styleClassic')}</option>
                      <option value="glow">{t('settings.styleGlow')}</option>
                      <option value="vibrant">{t('settings.styleVibrant')}</option>
                    </select>
                  </div>
                </div>
              </div>

              {/* Light Mode Settings */}
              <div className={`space-y-3 p-4 rounded-lg border ${mode === 'light' ? 'border-bambu-green bg-bambu-green/5' : 'border-bambu-dark-tertiary'}`}>
                <h3 className="text-sm font-medium text-white flex items-center gap-2">
                  {t('settings.lightMode')}
                  {mode === 'light' && <span className="text-xs text-bambu-green">{t('settings.active')}</span>}
                </h3>
                <div className="grid grid-cols-3 gap-3">
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.background')}</label>
                    <select
                      value={lightBackground}
                      onChange={(e) => { setLightBackground(e.target.value as LightBackground); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="neutral">{t('settings.bgNeutral')}</option>
                      <option value="warm">{t('settings.bgWarm')}</option>
                      <option value="cool">{t('settings.bgCool')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.accent')}</label>
                    <select
                      value={lightAccent}
                      onChange={(e) => { setLightAccent(e.target.value as ThemeAccent); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="green">{t('settings.accentGreen')}</option>
                      <option value="teal">{t('settings.accentTeal')}</option>
                      <option value="blue">{t('settings.accentBlue')}</option>
                      <option value="orange">{t('settings.accentOrange')}</option>
                      <option value="purple">{t('settings.accentPurple')}</option>
                      <option value="red">{t('settings.accentRed')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.style')}</label>
                    <select
                      value={lightStyle}
                      onChange={(e) => { setLightStyle(e.target.value as ThemeStyle); showToast(t('settings.saved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="classic">{t('settings.styleClassic')}</option>
                      <option value="glow">{t('settings.styleGlow')}</option>
                      <option value="vibrant">{t('settings.styleVibrant')}</option>
                    </select>
                  </div>
                </div>
              </div>

              <p className="text-xs text-bambu-gray">
                {t('settings.themeToggleHint')}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.archiveSettings')}</h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.autoArchive')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.autoArchiveDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.auto_archive}
                    onChange={(e) => updateSetting('auto_archive', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.saveThumbnails')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.saveThumbnailsDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.save_thumbnails}
                    onChange={(e) => updateSetting('save_thumbnails', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.captureFinishPhoto')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.captureFinishPhotoDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.capture_finish_photo}
                    onChange={(e) => updateSetting('capture_finish_photo', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              {localSettings.capture_finish_photo && ffmpegStatus && !ffmpegStatus.installed && (
                <div className="flex items-start gap-2 p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
                  <AlertTriangle className="w-5 h-5 text-yellow-500 flex-shrink-0 mt-0.5" />
                  <div className="text-sm">
                    <p className="text-yellow-500 font-medium">{t('settings.ffmpegNotInstalled')}</p>
                    <p className="text-bambu-gray mt-1">
                      {t('settings.ffmpegInstruction')}
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

        </div>

        {/* Second Column - Camera, Cost, AMS & Spoolman */}
        <div className="space-y-6 flex-1 lg:max-w-md">
          {/* Camera Settings */}
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Video className="w-5 h-5 text-bambu-green" />
                {t('settings.camera')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.cameraViewMode')}
                </label>
                <select
                  value={localSettings.camera_view_mode ?? 'window'}
                  onChange={(e) => updateSetting('camera_view_mode', e.target.value as 'window' | 'embedded')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="window">{t('settings.cameraNewWindow')}</option>
                  <option value="embedded">{t('settings.cameraEmbedded')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {localSettings.camera_view_mode === 'embedded'
                    ? t('settings.cameraEmbeddedDescription')
                    : t('settings.cameraNewWindowDescription')}
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.costTracking')}</h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultFilamentCost')}
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={localSettings.default_filament_cost}
                  onChange={(e) =>
                    updateSetting('default_filament_cost', parseFloat(e.target.value) || 0)
                  }
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('settings.currency')}</label>
                <select
                  value={localSettings.currency}
                  onChange={(e) => updateSetting('currency', e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="USD">USD ($)</option>
                  <option value="EUR">EUR (€)</option>
                  <option value="GBP">GBP (£)</option>
                  <option value="CHF">CHF (Fr.)</option>
                  <option value="JPY">JPY (¥)</option>
                  <option value="CNY">CNY (¥)</option>
                  <option value="CAD">CAD ($)</option>
                  <option value="AUD">AUD ($)</option>
                </select>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.electricityCost')}
                </label>
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  value={localSettings.energy_cost_per_kwh}
                  onChange={(e) =>
                    updateSetting('energy_cost_per_kwh', parseFloat(e.target.value) || 0)
                  }
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.energyDisplayMode')}
                </label>
                <select
                  value={localSettings.energy_tracking_mode || 'total'}
                  onChange={(e) => updateSetting('energy_tracking_mode', e.target.value as 'print' | 'total')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="print">{t('settings.energyPrintsOnly')}</option>
                  <option value="total">{t('settings.energyTotalConsumption')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {localSettings.energy_tracking_mode === 'print'
                    ? t('settings.energyPrintsOnlyDescription')
                    : t('settings.energyTotalDescription')}
                </p>
              </div>
            </CardContent>
          </Card>

          {/* File Manager Settings */}
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <FileText className="w-5 h-5 text-bambu-green" />
                {t('settings.fileManager')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Archive Mode */}
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.archiveOnPrint')}
                </label>
                <select
                  value={localSettings.library_archive_mode ?? 'ask'}
                  onChange={(e) => updateSetting('library_archive_mode', e.target.value as 'always' | 'never' | 'ask')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="always">{t('settings.archiveAlways')}</option>
                  <option value="never">{t('settings.archiveNever')}</option>
                  <option value="ask">{t('settings.archiveAsk')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.archiveOnPrintDescription')}
                </p>
              </div>

              {/* Disk Space Warning Threshold */}
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.lowDiskWarning')}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0.5"
                    max="100"
                    step="0.5"
                    value={localSettings.library_disk_warning_gb ?? 5}
                    onChange={(e) => updateSetting('library_disk_warning_gb', parseFloat(e.target.value) || 5)}
                    className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                  <span className="text-bambu-gray">GB</span>
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.lowDiskWarningDescription')}
                </p>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Third Column - Sidebar Links & Updates */}
        <div className="space-y-6 flex-1 lg:max-w-sm">
          {/* Sidebar Links */}
          <ExternalLinksSettings />

          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.updates')}</h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.checkForUpdates')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.checkForUpdatesDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.check_updates}
                    onChange={(e) => updateSetting('check_updates', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-white">{t('settings.telemetry')}</p>
                    <button
                      onClick={() => setShowTelemetryInfo(true)}
                      className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-bambu-dark rounded-full text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary transition-colors"
                    >
                      <Info className="w-3 h-3" />
                      {t('settings.telemetryLearnMore')}
                    </button>
                  </div>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.telemetryDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.telemetry_enabled}
                    onChange={(e) => updateSetting('telemetry_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              <div className="border-t border-bambu-dark-tertiary pt-4">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <p className="text-white">{t('settings.currentVersion')}</p>
                    <p className="text-sm text-bambu-gray">v{versionInfo?.version || '...'}</p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => refetchUpdateCheck()}
                    disabled={isCheckingUpdate}
                  >
                    {isCheckingUpdate ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <RefreshCw className="w-4 h-4" />
                    )}
                    {t('settings.checkNow')}
                  </Button>
                </div>

                {updateCheck?.update_available ? (
                  <div className="mt-4 p-3 bg-bambu-green/10 border border-bambu-green/30 rounded-lg">
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="text-bambu-green font-medium">
                          {t('settings.updateAvailable', { version: updateCheck.latest_version })}
                        </p>
                        {updateCheck.release_name && updateCheck.release_name !== updateCheck.latest_version && (
                          <p className="text-sm text-bambu-gray mt-1">{updateCheck.release_name}</p>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {updateCheck.release_notes && (
                          <button
                            onClick={() => setShowReleaseNotes(true)}
                            className="text-bambu-gray hover:text-white transition-colors text-sm underline"
                          >
                            {t('settings.releaseNotes')}
                          </button>
                        )}
                        {updateCheck.release_url && (
                          <a
                            href={updateCheck.release_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-bambu-gray hover:text-white transition-colors"
                            title={t('settings.viewOnGitHub')}
                          >
                            <ExternalLink className="w-4 h-4" />
                          </a>
                        )}
                      </div>
                    </div>

                    {updateStatus?.status === 'downloading' || updateStatus?.status === 'installing' ? (
                      <div className="mt-3">
                        <div className="flex items-center gap-2 text-sm text-bambu-gray">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span>{updateStatus.message}</span>
                        </div>
                        <div className="mt-2 w-full bg-bambu-dark-tertiary rounded-full h-2">
                          <div
                            className="bg-bambu-green h-2 rounded-full transition-all duration-300"
                            style={{ width: `${updateStatus.progress}%` }}
                          />
                        </div>
                      </div>
                    ) : updateStatus?.status === 'complete' ? (
                      <div className="mt-3 p-2 bg-bambu-green/20 rounded text-sm text-bambu-green">
                        {updateStatus.message}
                      </div>
                    ) : updateStatus?.status === 'error' ? (
                      <div className="mt-3 p-2 bg-red-500/20 rounded text-sm text-red-400">
                        {updateStatus.error || updateStatus.message}
                      </div>
                    ) : updateCheck?.is_docker ? (
                      <div className="mt-3 p-3 bg-bambu-dark-tertiary rounded-lg">
                        <p className="text-sm text-bambu-gray mb-2">
                          {t('settings.updateViaDocker')}
                        </p>
                        <code className="block text-xs bg-bambu-dark p-2 rounded text-bambu-green font-mono">
                          docker compose pull && docker compose up -d
                        </code>
                      </div>
                    ) : (
                      <Button
                        className="mt-3"
                        onClick={() => applyUpdateMutation.mutate()}
                        disabled={applyUpdateMutation.isPending}
                      >
                        {applyUpdateMutation.isPending ? (
                          <Loader2 className="w-4 h-4 animate-spin" />
                        ) : (
                          <Download className="w-4 h-4" />
                        )}
                        {t('settings.installUpdate')}
                      </Button>
                    )}
                  </div>
                ) : updateCheck?.error ? (
                  <div className="mt-2 p-2 bg-red-500/10 border border-red-500/30 rounded text-sm text-red-400">
                    {t('settings.updateCheckFailed', { error: updateCheck.error })}
                  </div>
                ) : updateCheck && !updateCheck.update_available ? (
                  <p className="mt-2 text-sm text-bambu-gray">
                    {t('settings.latestVersion')}
                  </p>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {/* Data Management */}
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.dataManagement')}</h2>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Backup/Restore */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.backupData')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.backupDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowBackupModal(true)}
                >
                  <Download className="w-4 h-4" />
                  {t('settings.export')}
                </Button>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.restoreBackup')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.restoreDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowRestoreModal(true)}
                >
                  <Upload className="w-4 h-4" />
                  {t('settings.restore')}
                </Button>
              </div>

              <div className="border-t border-bambu-dark-tertiary pt-4">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white">{t('settings.clearLogs')}</p>
                    <p className="text-sm text-bambu-gray">
                      {t('settings.clearLogsDescription')}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setShowClearLogsConfirm(true)}
                  >
                    <Trash2 className="w-4 h-4" />
                    {t('settings.clear')}
                  </Button>
                </div>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.resetUI')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.resetUIDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowClearStorageConfirm(true)}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('settings.reset')}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
      )}

      {/* Network Tab */}
      {activeTab === 'network' && localSettings && (
      <div className="flex flex-col lg:flex-row gap-6">
        {/* Left Column - External URL & FTP Retry */}
        <div className="flex-1 lg:max-w-xl space-y-4">
          {/* External URL */}
          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Globe className="w-5 h-5 text-blue-400" />
                {t('settings.externalUrl')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-bambu-gray">
                {t('settings.externalUrlDescription')}
              </p>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.bambuddyUrl')}
                </label>
                <input
                  type="text"
                  value={localSettings.external_url ?? ''}
                  onChange={(e) => updateSetting('external_url', e.target.value)}
                  placeholder={t('settings.externalUrlPlaceholder')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.externalUrlHint')}
                </p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <RefreshCw className="w-5 h-5 text-blue-400" />
                {t('settings.ftpRetry')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-bambu-gray">
                {t('settings.ftpRetryDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableRetry')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.enableRetryDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.ftp_retry_enabled ?? true}
                    onChange={(e) => updateSetting('ftp_retry_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {localSettings.ftp_retry_enabled && (
                <div className="space-y-4 pt-2 border-t border-bambu-dark-tertiary">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.retryAttempts')}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="1"
                        max="10"
                        value={localSettings.ftp_retry_count ?? 3}
                        onChange={(e) => updateSetting('ftp_retry_count', Math.min(10, Math.max(1, parseInt(e.target.value) || 3)))}
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      />
                      <span className="text-bambu-gray">{t('settings.times')}</span>
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.retryAttemptsDescription')}
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.retryDelay')}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="1"
                        max="30"
                        value={localSettings.ftp_retry_delay ?? 2}
                        onChange={(e) => updateSetting('ftp_retry_delay', Math.min(30, Math.max(1, parseInt(e.target.value) || 2)))}
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      />
                      <span className="text-bambu-gray">{t('settings.seconds')}</span>
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.retryDelayDescription')}
                    </p>
                  </div>
                </div>
              )}

              <div className="pt-2 border-t border-bambu-dark-tertiary">
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.connectionTimeout')}
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="10"
                    max="120"
                    value={localSettings.ftp_timeout ?? 30}
                    onChange={(e) => updateSetting('ftp_timeout', Math.min(120, Math.max(10, parseInt(e.target.value) || 30)))}
                    className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                  <span className="text-bambu-gray">{t('settings.seconds')}</span>
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.connectionTimeoutDescription')}
                </p>
              </div>
            </CardContent>
          </Card>

        </div>

        {/* Right Column - Home Assistant & MQTT Publishing */}
        <div className="flex-1 lg:max-w-xl space-y-4">
          {/* Home Assistant Integration */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Home className="w-5 h-5 text-bambu-green" />
                  {t('settings.homeAssistant')}
                </h2>
                {localSettings.ha_enabled && haTestResult && (
                  <div className="flex items-center gap-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${haTestResult.success ? 'bg-green-400' : 'bg-red-400'}`} />
                    <span className={`text-sm ${haTestResult.success ? 'text-green-400' : 'text-red-400'}`}>
                      {haTestResult.success ? t('settings.connected') : t('settings.disconnected')}
                    </span>
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-bambu-gray">
                {t('settings.homeAssistantDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableHA')}</p>
                  <p className="text-xs text-bambu-gray">{t('settings.enableHADescription')}</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.ha_enabled ?? false}
                    onChange={(e) => updateSetting('ha_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {localSettings.ha_enabled && (
                <>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.haUrl')}
                    </label>
                    <input
                      type="text"
                      value={localSettings.ha_url ?? ''}
                      onChange={(e) => updateSetting('ha_url', e.target.value)}
                      placeholder={t('settings.haUrlPlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.haToken')}
                    </label>
                    <input
                      type="password"
                      value={localSettings.ha_token ?? ''}
                      onChange={(e) => updateSetting('ha_token', e.target.value)}
                      placeholder={t('settings.haTokenPlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.haTokenHint')}
                    </p>
                  </div>

                  {localSettings.ha_url && localSettings.ha_token && (
                    <div className="pt-2 border-t border-bambu-dark-tertiary">
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={haTestLoading}
                        onClick={async () => {
                          setHaTestLoading(true);
                          setHaTestResult(null);
                          try {
                            const result = await api.testHAConnection(localSettings.ha_url!, localSettings.ha_token!);
                            setHaTestResult(result);
                          } catch (e) {
                            setHaTestResult({ success: false, message: null, error: e instanceof Error ? e.message : t('settings.unknownError') });
                          } finally {
                            setHaTestLoading(false);
                          }
                        }}
                      >
                        {haTestLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Wifi className="w-4 h-4" />}
                        {t('settings.testConnection')}
                      </Button>
                    </div>
                  )}
                </>
              )}
            </CardContent>
          </Card>

          {/* MQTT Publishing */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Wifi className="w-5 h-5 text-blue-400" />
                  {t('settings.mqttPublishing')}
                </h2>
                {mqttStatus?.enabled && (
                  <div className="flex items-center gap-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${mqttStatus.connected ? 'bg-green-400' : 'bg-red-400'}`} />
                    <span className={`text-sm ${mqttStatus.connected ? 'text-green-400' : 'text-red-400'}`}>
                      {mqttStatus.connected ? t('settings.connected') : t('settings.disconnected')}
                    </span>
                  </div>
                )}
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-bambu-gray">
                {t('settings.mqttDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableMqtt')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.enableMqttDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.mqtt_enabled ?? false}
                    onChange={(e) => updateSetting('mqtt_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {localSettings.mqtt_enabled && (
                <div className="space-y-4 pt-2 border-t border-bambu-dark-tertiary">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.brokerHostname')}
                    </label>
                    <input
                      type="text"
                      value={localSettings.mqtt_broker ?? ''}
                      onChange={(e) => updateSetting('mqtt_broker', e.target.value)}
                      placeholder={t('settings.mqttHostPlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>

                  <div className="flex items-end gap-4">
                    <div className="flex-1">
                      <label className="block text-sm text-bambu-gray mb-1">
                        {t('settings.port')}
                      </label>
                      <input
                        type="number"
                        min="1"
                        max="65535"
                        value={localSettings.mqtt_port ?? 1883}
                        onChange={(e) => updateSetting('mqtt_port', Math.min(65535, Math.max(1, parseInt(e.target.value) || 1883)))}
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      />
                    </div>
                    <div className="flex items-center gap-3 pb-2">
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input
                          type="checkbox"
                          checked={localSettings.mqtt_use_tls ?? false}
                          onChange={(e) => {
                            const useTls = e.target.checked;
                            updateSetting('mqtt_use_tls', useTls);
                            // Auto-populate port based on TLS selection
                            const currentPort = localSettings.mqtt_port ?? 1883;
                            if (useTls && currentPort === 1883) {
                              updateSetting('mqtt_port', 8883);
                            } else if (!useTls && currentPort === 8883) {
                              updateSetting('mqtt_port', 1883);
                            }
                          }}
                          className="sr-only peer"
                        />
                        <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                      </label>
                      <span className="text-white text-sm">{t('settings.useTls')}</span>
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.usernameOptional')}
                    </label>
                    <input
                      type="text"
                      value={localSettings.mqtt_username ?? ''}
                      onChange={(e) => updateSetting('mqtt_username', e.target.value)}
                      placeholder={t('settings.leaveEmptyAnonymous')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.passwordOptional')}
                    </label>
                    <input
                      type="password"
                      value={localSettings.mqtt_password ?? ''}
                      onChange={(e) => updateSetting('mqtt_password', e.target.value)}
                      placeholder={t('settings.leaveEmptyAnonymous')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.topicPrefix')}
                    </label>
                    <input
                      type="text"
                      value={localSettings.mqtt_topic_prefix ?? 'bambuddy'}
                      onChange={(e) => updateSetting('mqtt_topic_prefix', e.target.value)}
                      placeholder={t('settings.mqttTopicDefault')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.mqttTopicsHint', { prefix: localSettings.mqtt_topic_prefix || 'bambuddy' })}
                    </p>
                  </div>

                  {/* Connection Info */}
                  {mqttStatus && (
                    <div className="pt-3 mt-3 border-t border-bambu-dark-tertiary">
                      <div className="flex items-center gap-2 text-sm">
                        <span className={`w-2 h-2 rounded-full ${mqttStatus.connected ? 'bg-green-400' : 'bg-red-400'}`} />
                        <span className="text-bambu-gray">
                          {mqttStatus.connected ? (
                            <>{t('settings.connectedTo')} <span className="text-white">{mqttStatus.broker}:{mqttStatus.port}</span></>
                          ) : (
                            t('settings.notConnected')
                          )}
                        </span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
      )}

      {/* Home Assistant Test Connection Modal */}
      {haTestResult && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-bambu-dark-secondary rounded-lg p-6 max-w-md w-full mx-4">
            <div className="flex items-center gap-3 mb-4">
              {haTestResult.success ? (
                <CheckCircle className="w-8 h-8 text-green-400" />
              ) : (
                <XCircle className="w-8 h-8 text-red-400" />
              )}
              <h3 className="text-lg font-medium text-white">
                {haTestResult.success ? t('settings.connectionSuccessful') : t('settings.connectionFailed')}
              </h3>
            </div>
            <p className="text-bambu-gray mb-6">
              {haTestResult.success
                ? haTestResult.message || t('settings.haConnectionSuccess')
                : haTestResult.error || t('settings.haConnectionFailed')}
            </p>
            <div className="flex justify-end">
              <Button
                variant="primary"
                onClick={() => setHaTestResult(null)}
              >
                {t('common.done')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Smart Plugs Tab */}
      {activeTab === 'plugs' && (
        <div className="max-w-4xl">
          <div className="flex items-start justify-between mb-6">
            <div>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Plug className="w-5 h-5 text-bambu-green" />
                {t('settings.smartPlugs')}
              </h2>
              <p className="text-sm text-bambu-gray mt-1">
                {t('settings.smartPlugsDescription')}
              </p>
            </div>
            <div className="flex items-center gap-2 pt-1 shrink-0">
              {smartPlugs && smartPlugs.filter(p => p.enabled).length > 1 && (
                <>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="whitespace-nowrap"
                    onClick={() => setShowBulkPlugConfirm('on')}
                    disabled={bulkPlugActionMutation.isPending}
                    title={t('settings.turnAllPlugsOn')}
                  >
                    {bulkPlugActionMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Power className="w-4 h-4 text-bambu-green" />
                    )}
                    {t('settings.allOn')}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    className="whitespace-nowrap"
                    onClick={() => setShowBulkPlugConfirm('off')}
                    disabled={bulkPlugActionMutation.isPending}
                    title={t('settings.turnAllPlugsOff')}
                  >
                    {bulkPlugActionMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <PowerOff className="w-4 h-4 text-red-400" />
                    )}
                    {t('settings.allOff')}
                  </Button>
                </>
              )}
              <Button
                className="whitespace-nowrap"
                onClick={() => {
                  setEditingPlug(null);
                  setShowPlugModal(true);
                }}
              >
                <Plus className="w-4 h-4" />
                {t('settings.addSmartPlug')}
              </Button>
            </div>
          </div>

          {/* Energy Summary Card */}
          {smartPlugs && smartPlugs.length > 0 && (
            <Card className="mb-6">
              <CardHeader>
                <h3 className="text-base font-semibold text-white flex items-center gap-2">
                  <Zap className="w-4 h-4 text-yellow-400" />
                  {t('settings.energySummary')}
                  {energyLoading && (
                    <Loader2 className="w-4 h-4 animate-spin text-bambu-gray ml-2" />
                  )}
                </h3>
              </CardHeader>
              <CardContent>
                {plugEnergySummary ? (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    {/* Current Power */}
                    <div className="bg-bambu-dark rounded-lg p-3">
                      <div className="flex items-center gap-2 text-bambu-gray text-xs mb-1">
                        <Zap className="w-3 h-3" />
                        {t('settings.currentPower')}
                      </div>
                      <div className="text-xl font-bold text-white">
                        {plugEnergySummary.totalPower.toFixed(1)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">W</span>
                      </div>
                      <div className="text-xs text-bambu-gray mt-1">
                        {t('settings.plugsOnline', { online: plugEnergySummary.reachableCount, total: plugEnergySummary.totalPlugs })}
                      </div>
                    </div>

                    {/* Today */}
                    <div className="bg-bambu-dark rounded-lg p-3">
                      <div className="flex items-center gap-2 text-bambu-gray text-xs mb-1">
                        <Calendar className="w-3 h-3" />
                        {t('settings.today')}
                      </div>
                      <div className="text-xl font-bold text-white">
                        {plugEnergySummary.totalToday.toFixed(2)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">kWh</span>
                      </div>
                      {localSettings && localSettings.energy_cost_per_kwh > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalToday * localSettings.energy_cost_per_kwh).toFixed(2)} {localSettings.currency}
                        </div>
                      )}
                    </div>

                    {/* Yesterday */}
                    <div className="bg-bambu-dark rounded-lg p-3">
                      <div className="flex items-center gap-2 text-bambu-gray text-xs mb-1">
                        <TrendingUp className="w-3 h-3" />
                        {t('settings.yesterday')}
                      </div>
                      <div className="text-xl font-bold text-white">
                        {plugEnergySummary.totalYesterday.toFixed(2)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">kWh</span>
                      </div>
                      {localSettings && localSettings.energy_cost_per_kwh > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalYesterday * localSettings.energy_cost_per_kwh).toFixed(2)} {localSettings.currency}
                        </div>
                      )}
                    </div>

                    {/* Total Lifetime */}
                    <div className="bg-bambu-dark rounded-lg p-3">
                      <div className="flex items-center gap-2 text-bambu-gray text-xs mb-1">
                        <DollarSign className="w-3 h-3" />
                        {t('settings.total')}
                      </div>
                      <div className="text-xl font-bold text-white">
                        {plugEnergySummary.totalLifetime.toFixed(1)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">kWh</span>
                      </div>
                      {localSettings && localSettings.energy_cost_per_kwh > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalLifetime * localSettings.energy_cost_per_kwh).toFixed(2)} {localSettings.currency}
                        </div>
                      )}
                    </div>
                  </div>
                ) : !energyLoading ? (
                  <p className="text-sm text-bambu-gray">
                    {t('settings.enablePlugsForEnergy')}
                  </p>
                ) : null}
              </CardContent>
            </Card>
          )}

          {plugsLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
            </div>
          ) : smartPlugs && smartPlugs.length > 0 ? (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {smartPlugs.map((plug) => (
                <SmartPlugCard
                  key={plug.id}
                  plug={plug}
                  onEdit={(p) => {
                    setEditingPlug(p);
                    setShowPlugModal(true);
                  }}
                />
              ))}
            </div>
          ) : (
            <Card>
              <CardContent className="py-12">
                <div className="text-center text-bambu-gray">
                  <Plug className="w-16 h-16 mx-auto mb-4 opacity-30" />
                  <p className="text-lg font-medium text-white mb-2">{t('settings.noSmartPlugs')}</p>
                  <p className="text-sm mb-4">{t('settings.noSmartPlugsDescription')}</p>
                  <Button
                    onClick={() => {
                      setEditingPlug(null);
                      setShowPlugModal(true);
                    }}
                  >
                    <Plus className="w-4 h-4" />
                    {t('settings.addFirstSmartPlug')}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Notifications Tab */}
      {activeTab === 'notifications' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Left Column: Providers */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Bell className="w-5 h-5 text-bambu-green" />
                {t('settings.providers')}
              </h2>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setShowLogViewer(true)}
                >
                  <History className="w-4 h-4" />
                  {t('settings.log')}
                </Button>
                {notificationProviders && notificationProviders.length > 0 && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setTestAllResult(null);
                      testAllMutation.mutate();
                    }}
                    disabled={testAllMutation.isPending}
                  >
                    {testAllMutation.isPending ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Send className="w-4 h-4" />
                    )}
                    {t('settings.testAll')}
                  </Button>
                )}
                <Button
                  size="sm"
                  onClick={() => {
                    setEditingProvider(null);
                    setShowNotificationModal(true);
                  }}
                >
                  <Plus className="w-4 h-4" />
                  {t('settings.add')}
                </Button>
              </div>
            </div>

            {/* Notification Language Setting */}
            <Card className="mb-4">
              <CardContent className="py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{t('settings.notificationLanguage')}</p>
                    <p className="text-xs text-bambu-gray">{t('settings.notificationLanguageDescription')}</p>
                  </div>
                  <select
                    value={localSettings.notification_language || 'en'}
                    onChange={(e) => updateSetting('notification_language', e.target.value)}
                    className="px-2 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:ring-1 focus:ring-bambu-green"
                  >
                    {availableLanguages.map((lang) => (
                      <option key={lang.code} value={lang.code}>
                        {lang.nativeName}
                      </option>
                    ))}
                  </select>
                </div>
              </CardContent>
            </Card>

            {/* Test All Results */}
            {testAllResult && (
              <Card className="mb-4">
                <CardContent className="py-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-medium text-white">{t('settings.testResults')}</span>
                    <button
                      onClick={() => setTestAllResult(null)}
                      className="text-bambu-gray hover:text-white text-xs"
                    >
                      {t('settings.dismiss')}
                    </button>
                  </div>
                  <div className="flex items-center gap-4 text-sm mb-2">
                    <span className="flex items-center gap-1 text-bambu-green">
                      <CheckCircle className="w-4 h-4" />
                      {t('settings.passed', { count: testAllResult.success })}
                    </span>
                    {testAllResult.failed > 0 && (
                      <span className="flex items-center gap-1 text-red-400">
                        <XCircle className="w-4 h-4" />
                        {t('settings.failed', { count: testAllResult.failed })}
                      </span>
                    )}
                  </div>
                  {testAllResult.results.filter(r => !r.success).length > 0 && (
                    <div className="space-y-1 mt-2 pt-2 border-t border-bambu-dark-tertiary">
                      {testAllResult.results.filter(r => !r.success).map((result) => (
                        <div key={result.provider_id} className="text-xs text-red-400">
                          <span className="font-medium">{result.provider_name}:</span> {result.message}
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            )}

            {providersLoading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : notificationProviders && notificationProviders.length > 0 ? (
              <div className="space-y-3">
                {notificationProviders.map((provider) => (
                  <NotificationProviderCard
                    key={provider.id}
                    provider={provider}
                    onEdit={(p) => {
                      setEditingProvider(p);
                      setShowNotificationModal(true);
                    }}
                  />
                ))}
              </div>
            ) : (
              <Card>
                <CardContent className="py-8">
                  <div className="text-center text-bambu-gray">
                    <Bell className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p className="text-sm font-medium text-white mb-2">{t('settings.noProviders')}</p>
                    <p className="text-xs mb-3">{t('settings.noProvidersDescription')}</p>
                    <Button
                      size="sm"
                      onClick={() => {
                        setEditingProvider(null);
                        setShowNotificationModal(true);
                      }}
                    >
                      <Plus className="w-4 h-4" />
                      {t('settings.addProvider')}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>

          {/* Right Column: Templates */}
          <div>
            <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-4">
              <FileText className="w-5 h-5 text-bambu-green" />
              {t('settings.messageTemplates')}
            </h2>
            <p className="text-sm text-bambu-gray mb-4">
              {t('settings.templatesDescription')}
            </p>

            {templatesLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : notificationTemplates && notificationTemplates.length > 0 ? (
              <div className="space-y-2">
                {notificationTemplates.map((template) => (
                  <Card
                    key={template.id}
                    className="cursor-pointer hover:border-bambu-green/50 transition-colors"
                    onClick={() => setEditingTemplate(template)}
                  >
                    <CardContent className="py-2.5 px-3">
                      <div className="flex items-center justify-between">
                        <div className="min-w-0 flex-1">
                          <p className="text-white font-medium text-sm truncate">{t(`notificationLog.events.${template.event_type}`, { defaultValue: template.name })}</p>
                          <p className="text-bambu-gray text-xs truncate mt-0.5">
                            {template.title_template}
                          </p>
                        </div>
                        <button
                          className="p-1.5 hover:bg-bambu-dark-tertiary rounded transition-colors shrink-0 ml-2"
                          onClick={(e) => {
                            e.stopPropagation();
                            setEditingTemplate(template);
                          }}
                        >
                          <Edit2 className="w-4 h-4 text-bambu-gray" />
                        </button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <Card>
                <CardContent className="py-8">
                  <div className="text-center text-bambu-gray">
                    <FileText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p className="text-sm">{t('settings.noTemplates')}</p>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* API Keys Tab */}
      {activeTab === 'apikeys' && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
          {/* Left Column - API Keys Management */}
          <div>
            <div className="flex items-start justify-between gap-4 mb-6">
              <div className="flex-1">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Key className="w-5 h-5 text-bambu-green" />
                  {t('settings.apiKeys')}
                </h2>
                <p className="text-sm text-bambu-gray mt-1">
                  {t('settings.apiKeysDescription')}
                </p>
              </div>
              <Button size="sm" onClick={() => setShowCreateAPIKey(true)} className="flex-shrink-0">
                <Plus className="w-4 h-4" />
                {t('settings.createKey')}
              </Button>
            </div>

            {/* Created Key Display */}
            {createdAPIKey && (
              <Card className="mb-6 border-bambu-green">
                <CardContent className="py-4">
                  <div className="flex items-start gap-3">
                    <CheckCircle className="w-5 h-5 text-bambu-green flex-shrink-0 mt-0.5" />
                    <div className="flex-1">
                      <p className="text-white font-medium mb-1">{t('settings.apiKeySuccess')}</p>
                      <p className="text-sm text-bambu-gray mb-2">
                        {t('settings.apiKeyCopyWarning')}
                      </p>
                      <div className="flex items-center gap-2 bg-bambu-dark rounded-lg p-2">
                        <code className="flex-1 text-sm text-bambu-green font-mono break-all">
                          {createdAPIKey}
                        </code>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={async () => {
                            try {
                              if (navigator.clipboard && navigator.clipboard.writeText) {
                                await navigator.clipboard.writeText(createdAPIKey);
                              } else {
                                const textArea = document.createElement('textarea');
                                textArea.value = createdAPIKey;
                                textArea.style.position = 'fixed';
                                textArea.style.left = '-999999px';
                                document.body.appendChild(textArea);
                                textArea.select();
                                document.execCommand('copy');
                                document.body.removeChild(textArea);
                              }
                              showToast(t('settings.keyCopied'));
                            } catch {
                              showToast(t('settings.keyCopyFailed'), 'error');
                            }
                          }}
                        >
                          <Copy className="w-4 h-4" />
                        </Button>
                      </div>
                      <div className="flex gap-2 mt-3">
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => {
                            setTestApiKey(createdAPIKey);
                            showToast(t('settings.keyAddedToBrowser'));
                          }}
                        >
                          {t('settings.useInBrowser')}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setCreatedAPIKey(null)}
                        >
                          {t('settings.dismiss')}
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Create Key Form */}
            {showCreateAPIKey && (
              <Card className="mb-6">
                <CardHeader>
                  <h3 className="text-base font-semibold text-white">{t('settings.createNewApiKey')}</h3>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('settings.keyName')}</label>
                    <input
                      type="text"
                      value={newAPIKeyName}
                      onChange={(e) => setNewAPIKeyName(e.target.value)}
                      placeholder={t('settings.apiKeyNamePlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-2">{t('settings.permissions')}</label>
                    <div className="space-y-2">
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={newAPIKeyPermissions.can_read_status}
                          onChange={(e) => setNewAPIKeyPermissions(prev => ({ ...prev, can_read_status: e.target.checked }))}
                          className="w-4 h-4 text-bambu-green rounded border-bambu-dark-tertiary bg-bambu-dark focus:ring-bambu-green"
                        />
                        <div>
                          <span className="text-white">{t('settings.readStatus')}</span>
                          <p className="text-xs text-bambu-gray">{t('settings.readStatusDescription')}</p>
                        </div>
                      </label>
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={newAPIKeyPermissions.can_queue}
                          onChange={(e) => setNewAPIKeyPermissions(prev => ({ ...prev, can_queue: e.target.checked }))}
                          className="w-4 h-4 text-bambu-green rounded border-bambu-dark-tertiary bg-bambu-dark focus:ring-bambu-green"
                        />
                        <div>
                          <span className="text-white">{t('settings.manageQueue')}</span>
                          <p className="text-xs text-bambu-gray">{t('settings.manageQueueDescription')}</p>
                        </div>
                      </label>
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={newAPIKeyPermissions.can_control_printer}
                          onChange={(e) => setNewAPIKeyPermissions(prev => ({ ...prev, can_control_printer: e.target.checked }))}
                          className="w-4 h-4 text-bambu-green rounded border-bambu-dark-tertiary bg-bambu-dark focus:ring-bambu-green"
                        />
                        <div>
                          <span className="text-white">{t('settings.controlPrinter')}</span>
                          <p className="text-xs text-bambu-gray">{t('settings.controlPrinterDescription')}</p>
                        </div>
                      </label>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 pt-2">
                    <Button
                      onClick={() => createAPIKeyMutation.mutate({
                        name: newAPIKeyName || t('settings.unnamedKey'),
                        ...newAPIKeyPermissions,
                      })}
                      disabled={createAPIKeyMutation.isPending}
                    >
                      {createAPIKeyMutation.isPending ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Plus className="w-4 h-4" />
                      )}
                      {t('settings.createKey')}
                    </Button>
                    <Button variant="secondary" onClick={() => setShowCreateAPIKey(false)}>
                      {t('settings.cancel')}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Existing Keys List */}
            {apiKeysLoading ? (
              <div className="flex justify-center py-12">
                <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
              </div>
            ) : apiKeys && apiKeys.length > 0 ? (
              <div className="space-y-3">
                {apiKeys.map((key) => (
                  <Card key={key.id}>
                    <CardContent className="py-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <Key className={`w-5 h-5 ${key.enabled ? 'text-bambu-green' : 'text-bambu-gray'}`} />
                          <div>
                            <p className="text-white font-medium">{key.name}</p>
                            <p className="text-xs text-bambu-gray">
                              {key.key_prefix}••••••••
                              {key.last_used && ` · ${t('settings.lastUsed')} ${formatDateOnly(key.last_used)}`}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="flex gap-1 text-xs">
                            {key.can_read_status && (
                              <span className="px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded">{t('settings.badgeRead')}</span>
                            )}
                            {key.can_queue && (
                              <span className="px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">{t('settings.badgeQueue')}</span>
                            )}
                            {key.can_control_printer && (
                              <span className="px-1.5 py-0.5 bg-orange-500/20 text-orange-400 rounded">{t('settings.badgeControl')}</span>
                            )}
                          </div>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() => setShowDeleteAPIKeyConfirm(key.id)}
                          >
                            <Trash2 className="w-4 h-4 text-red-400" />
                          </Button>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <Card>
                <CardContent className="py-12">
                  <div className="text-center text-bambu-gray">
                    <Key className="w-16 h-16 mx-auto mb-4 opacity-30" />
                    <p className="text-lg font-medium text-white mb-2">{t('settings.noApiKeys')}</p>
                    <p className="text-sm mb-4">{t('settings.noApiKeysDescription')}</p>
                    <Button onClick={() => setShowCreateAPIKey(true)}>
                      <Plus className="w-4 h-4" />
                      {t('settings.createFirstKey')}
                    </Button>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Webhook Documentation */}
            <Card className="mt-6">
              <CardHeader>
                <h3 className="text-base font-semibold text-white">{t('settings.webhookEndpoints')}</h3>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <p className="text-bambu-gray">
                  {t('settings.webhookApiKeyHint')}
                </p>
                <div className="space-y-2 font-mono text-xs">
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-blue-400">GET</span>{' '}
                    <span className="text-white">/api/v1/webhook/status</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookGetAll')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-blue-400">GET</span>{' '}
                    <span className="text-white">/api/v1/webhook/status/:id</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookGetOne')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-green-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/queue</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookQueue')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-orange-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/pause</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookPause')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-orange-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/resume</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookResume')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-red-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/stop</span>
                    <span className="text-bambu-gray"> - {t('settings.webhookStop')}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Right Column - API Browser */}
          <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Globe className="w-5 h-5 text-bambu-green" />
                {t('settings.apiBrowser')}
              </h2>
              <p className="text-sm text-bambu-gray mt-1">
                {t('settings.apiBrowserDescription')}
              </p>
            </div>

            {/* API Key Input for Testing */}
            <Card className="mb-4">
              <CardContent className="py-3">
                <label className="block text-sm text-bambu-gray mb-2">{t('settings.apiKeyForTesting')}</label>
                <input
                  type="text"
                  value={testApiKey}
                  onChange={(e) => setTestApiKey(e.target.value)}
                  placeholder={t('settings.apiKeyTestPlaceholder')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white font-mono text-sm focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-2">
                  {t('settings.apiKeyHeaderHint')}
                </p>
              </CardContent>
            </Card>

            <APIBrowser apiKey={testApiKey} />
          </div>
        </div>
      )}

      {/* Virtual Printer Tab */}
      {activeTab === 'virtual-printer' && (
        <VirtualPrinterSettings />
      )}

      {/* Filament Tab */}
      {activeTab === 'filament' && localSettings && (
        <div className="flex flex-col lg:flex-row gap-6 lg:gap-8">
          {/* Left Column - AMS Display Thresholds */}
          <div className="flex-1 lg:max-w-xl">
            <Card>
              <CardHeader>
                <h2 className="text-lg font-semibold text-white">{t('settings.amsThresholds')}</h2>
              </CardHeader>
              <CardContent className="space-y-4">
                <p className="text-sm text-bambu-gray">
                  {t('settings.amsThresholdsDescription')}
                </p>

                {/* Humidity Thresholds */}
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-white">
                    <Droplets className="w-4 h-4 text-blue-400" />
                    <span className="font-medium">{t('settings.humidity')}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm text-bambu-gray mb-1">
                        {t('settings.goodGreen')}
                      </label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="0"
                          max="100"
                          value={localSettings.ams_humidity_good ?? 40}
                          onChange={(e) => updateSetting('ams_humidity_good', parseInt(e.target.value) || 40)}
                          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                        />
                        <span className="text-bambu-gray">%</span>
                      </div>
                    </div>
                    <div>
                      <label className="block text-sm text-bambu-gray mb-1">
                        {t('settings.fairOrange')}
                      </label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min="0"
                          max="100"
                          value={localSettings.ams_humidity_fair ?? 60}
                          onChange={(e) => updateSetting('ams_humidity_fair', parseInt(e.target.value) || 60)}
                          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                        />
                        <span className="text-bambu-gray">%</span>
                      </div>
                    </div>
                  </div>
                  <p className="text-xs text-bambu-gray">
                    {t('settings.aboveFairBad')}
                  </p>
                </div>

                {/* Temperature Thresholds */}
                <div className="space-y-3 pt-2 border-t border-bambu-dark-tertiary">
                  <div className="flex items-center gap-2 text-white">
                    <Thermometer className="w-4 h-4 text-orange-400" />
                    <span className="font-medium">{t('settings.temperature')}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-sm text-bambu-gray mb-1">
                        {t('settings.goodBlue')}
                      </label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          step="0.5"
                          min="0"
                          max="60"
                          value={localSettings.ams_temp_good ?? 28}
                          onChange={(e) => updateSetting('ams_temp_good', parseFloat(e.target.value) || 28)}
                          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                        />
                        <span className="text-bambu-gray">°C</span>
                      </div>
                    </div>
                    <div>
                      <label className="block text-sm text-bambu-gray mb-1">
                        {t('settings.fairOrange')}
                      </label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          step="0.5"
                          min="0"
                          max="60"
                          value={localSettings.ams_temp_fair ?? 35}
                          onChange={(e) => updateSetting('ams_temp_fair', parseFloat(e.target.value) || 35)}
                          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                        />
                        <span className="text-bambu-gray">°C</span>
                      </div>
                    </div>
                  </div>
                  <p className="text-xs text-bambu-gray">
                    {t('settings.aboveFairHot')}
                  </p>
                </div>

                {/* History Retention */}
                <div className="space-y-3 pt-4 border-t border-bambu-dark-tertiary">
                  <div className="flex items-center gap-2 text-white">
                    <Database className="w-4 h-4 text-purple-400" />
                    <span className="font-medium">{t('settings.historyRetention')}</span>
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.keepHistoryFor')}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min="1"
                        max="365"
                        value={localSettings.ams_history_retention_days ?? 30}
                        onChange={(e) => updateSetting('ams_history_retention_days', parseInt(e.target.value) || 30)}
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                      />
                      <span className="text-bambu-gray">{t('settings.days')}</span>
                    </div>
                  </div>
                  <p className="text-xs text-bambu-gray">
                    {t('settings.historyRetentionDescription')}
                  </p>
                </div>

                {/* Per-Printer Mapping Default */}
                <div className="space-y-3 pt-4 border-t border-bambu-dark-tertiary">
                  <div className="flex items-center gap-2 text-white">
                    <Printer className="w-4 h-4 text-bambu-green" />
                    <span className="font-medium">{t('settings.printModal')}</span>
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <label className="block text-sm text-white">
                        {t('settings.expandMapping')}
                      </label>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {t('settings.expandMappingDescription')}
                      </p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={localSettings.per_printer_mapping_expanded ?? false}
                        onChange={(e) => updateSetting('per_printer_mapping_expanded', e.target.checked)}
                        className="sr-only peer"
                      />
                      <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                    </label>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          {/* Right Column - Spoolman Integration */}
          <div className="flex-1 lg:max-w-xl">
            <SpoolmanSettings />
          </div>
        </div>
      )}

      {/* Delete API Key Confirmation */}
      {showDeleteAPIKeyConfirm !== null && (
        <ConfirmModal
          title={t('settings.deleteApiKeyTitle')}
          message={t('settings.deleteApiKeyMessage')}
          confirmText={t('settings.deleteKey')}
          variant="danger"
          onConfirm={() => {
            deleteAPIKeyMutation.mutate(showDeleteAPIKeyConfirm);
            setShowDeleteAPIKeyConfirm(null);
          }}
          onCancel={() => setShowDeleteAPIKeyConfirm(null)}
        />
      )}

      {/* Smart Plug Modal */}
      {showPlugModal && (
        <AddSmartPlugModal
          plug={editingPlug}
          onClose={() => {
            setShowPlugModal(false);
            setEditingPlug(null);
          }}
        />
      )}

      {/* Notification Modal */}
      {showNotificationModal && (
        <AddNotificationModal
          provider={editingProvider}
          onClose={() => {
            setShowNotificationModal(false);
            setEditingProvider(null);
          }}
        />
      )}

      {/* Template Editor Modal */}
      {editingTemplate && (
        <NotificationTemplateEditor
          template={editingTemplate}
          onClose={() => setEditingTemplate(null)}
        />
      )}

      {/* Notification Log Viewer */}
      {showLogViewer && (
        <NotificationLogViewer
          onClose={() => setShowLogViewer(false)}
        />
      )}

      {/* Confirm Modal: Clear Notification Logs */}
      {showClearLogsConfirm && (
        <ConfirmModal
          title={t('settings.clearLogsTitle')}
          message={t('settings.clearLogsMessage')}
          confirmText={t('settings.clearLogsConfirm')}
          variant="warning"
          onConfirm={async () => {
            setShowClearLogsConfirm(false);
            try {
              const result = await api.clearNotificationLogs(30);
              showToast(result.message, 'success');
            } catch {
              showToast(t('settings.clearLogsFailed'), 'error');
            }
          }}
          onCancel={() => setShowClearLogsConfirm(false)}
        />
      )}

      {/* Confirm Modal: Clear Local Storage */}
      {showClearStorageConfirm && (
        <ConfirmModal
          title={t('settings.resetUITitle')}
          message={t('settings.resetUIMessage')}
          confirmText={t('settings.resetPreferences')}
          variant="default"
          onConfirm={() => {
            setShowClearStorageConfirm(false);
            localStorage.clear();
            showToast(t('settings.preferencesReset'), 'success');
            setTimeout(() => window.location.reload(), 1000);
          }}
          onCancel={() => setShowClearStorageConfirm(false)}
        />
      )}

      {/* Confirm Modal: Bulk Plug Action */}
      {showBulkPlugConfirm && (
        <ConfirmModal
          title={t('settings.bulkPlugTitle', { action: showBulkPlugConfirm === 'on' ? 'On' : 'Off' })}
          message={t('settings.bulkPlugMessage', { action: showBulkPlugConfirm === 'on' ? 'ON' : 'OFF', count: smartPlugs?.filter(p => p.enabled).length || 0 })}
          confirmText={t('settings.bulkPlugConfirm', { action: showBulkPlugConfirm === 'on' ? 'On' : 'Off' })}
          variant={showBulkPlugConfirm === 'off' ? 'danger' : 'warning'}
          onConfirm={() => {
            const action = showBulkPlugConfirm;
            setShowBulkPlugConfirm(null);
            bulkPlugActionMutation.mutate(action);
          }}
          onCancel={() => setShowBulkPlugConfirm(null)}
        />
      )}

      {/* Backup Modal */}
      {showBackupModal && (
        <BackupModal
          onClose={() => setShowBackupModal(false)}
          onExport={async (categories) => {
            setShowBackupModal(false);
            const toastId = 'backup-progress';
            const includesArchives = categories.archives;

            // Show persistent loading toast for archive backups (can be large)
            if (includesArchives) {
              showPersistentToast(toastId, t('backup.preparing', { defaultValue: 'Preparing backup...' }), 'loading');
            }

            try {
              const { blob, filename } = await api.exportBackup(categories);

              // Dismiss loading toast before download starts
              if (includesArchives) {
                dismissToast(toastId);
              }

              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = filename;
              a.click();
              URL.revokeObjectURL(url);
              showToast(t('backup.downloaded', { defaultValue: 'Backup downloaded' }), 'success');
            } catch {
              // Dismiss loading toast on error
              if (includesArchives) {
                dismissToast(toastId);
              }
              showToast(t('backup.failed', { defaultValue: 'Failed to create backup' }), 'error');
            }
          }}
        />
      )}

      {/* Restore Modal */}
      {showRestoreModal && (
        <RestoreModal
          onClose={() => setShowRestoreModal(false)}
          onRestore={async (file, overwrite) => {
            return await api.importBackup(file, overwrite);
          }}
          onSuccess={() => {
            // Reset local settings to force re-sync from restored data
            setLocalSettings(null);
            isInitialLoadRef.current = true;
            // Use resetQueries to clear cached data completely
            // This ensures fresh data is fetched, not stale cache
            queryClient.resetQueries({ queryKey: ['settings'] });
            // Invalidate other queries that may have changed
            queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
            queryClient.invalidateQueries({ queryKey: ['notification-templates'] });
            queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
            queryClient.invalidateQueries({ queryKey: ['external-links'] });
            queryClient.invalidateQueries({ queryKey: ['printers'] });
            queryClient.invalidateQueries({ queryKey: ['filaments'] });
            queryClient.invalidateQueries({ queryKey: ['maintenance-types'] });
            queryClient.invalidateQueries({ queryKey: ['api-keys'] });
          }}
        />
      )}

      {/* Telemetry Info Modal */}
      {showTelemetryInfo && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setShowTelemetryInfo(false)}
        >
          <Card className="w-full max-w-lg" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <CardHeader className="flex flex-row items-center justify-between">
              <div className="flex items-center gap-2">
                <Shield className="w-5 h-5 text-bambu-green" />
                <h2 className="text-lg font-semibold text-white">{t('settings.telemetryInfoTitle')}</h2>
              </div>
              <button
                onClick={() => setShowTelemetryInfo(false)}
                className="p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-bambu-gray text-sm">
                {t('settings.telemetryInfoIntro')}
              </p>

              <div className="space-y-3">
                <h3 className="text-white font-medium">{t('settings.telemetryInfoCollected')}</h3>
                <ul className="space-y-2 text-sm">
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoItem1')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoItem2')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoItem3')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoItem4')}</span>
                  </li>
                </ul>
              </div>

              <div className="space-y-3">
                <h3 className="text-white font-medium">{t('settings.telemetryInfoNotCollected')}</h3>
                <ul className="space-y-2 text-sm">
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoNotItem1')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoNotItem2')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoNotItem3')}</span>
                  </li>
                  <li className="flex items-start gap-2 text-bambu-gray">
                    <XCircle className="w-4 h-4 text-red-400 mt-0.5 shrink-0" />
                    <span>{t('settings.telemetryInfoNotItem4')}</span>
                  </li>
                </ul>
              </div>

              <p className="text-bambu-gray text-xs border-t border-bambu-dark-tertiary pt-4">
                {t('settings.telemetryInfoFooter')}
              </p>

              <Button
                onClick={() => setShowTelemetryInfo(false)}
                className="w-full"
              >
                {t('common.close')}
              </Button>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Release Notes Modal */}
      {showReleaseNotes && updateCheck?.release_notes && (
        <div
          className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
          onClick={() => setShowReleaseNotes(false)}
        >
          <Card className="w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <CardHeader className="flex flex-row items-center justify-between shrink-0">
              <div>
                <h2 className="text-lg font-semibold text-white">
                  {t('settings.releaseNotesTitle', { version: updateCheck.latest_version })}
                </h2>
                {updateCheck.release_name && updateCheck.release_name !== updateCheck.latest_version && (
                  <p className="text-sm text-bambu-gray">{updateCheck.release_name}</p>
                )}
              </div>
              <button
                onClick={() => setShowReleaseNotes(false)}
                className="p-1 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white"
              >
                <X className="w-5 h-5" />
              </button>
            </CardHeader>
            <CardContent className="overflow-y-auto flex-1">
              <pre className="text-sm text-bambu-gray whitespace-pre-wrap font-sans">
                {updateCheck.release_notes}
              </pre>
            </CardContent>
            <div className="p-4 border-t border-bambu-dark-tertiary shrink-0 flex gap-2">
              {updateCheck.release_url && (
                <a
                  href={updateCheck.release_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex-1"
                >
                  <Button variant="secondary" className="w-full">
                    <ExternalLink className="w-4 h-4" />
                    {t('settings.viewOnGitHub')}
                  </Button>
                </a>
              )}
              <Button
                onClick={() => setShowReleaseNotes(false)}
                className="flex-1"
              >
                {t('settings.close')}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
          <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Users className="w-5 h-5 text-bambu-green" />
                {t('settings.userAuth')}
              </h2>
              <p className="text-sm text-bambu-gray mt-1">
                {t('settings.userAuthDescription')}
              </p>
            </div>

            <Card>
              <CardContent className="py-6">
                {!authEnabled ? (
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <div className={`w-12 h-12 rounded-full flex items-center justify-center ${authEnabled ? 'bg-green-500/20' : 'bg-gray-500/20'}`}>
                        {authEnabled ? (
                          <Lock className="w-6 h-6 text-green-400" />
                        ) : (
                          <Unlock className="w-6 h-6 text-gray-400" />
                        )}
                      </div>
                      <div className="flex-1">
                        <h3 className="text-white font-medium">{t('settings.authDisabled')}</h3>
                        <p className="text-sm text-bambu-gray">
                          {t('settings.authDisabledDescription')}
                        </p>
                      </div>
                    </div>

                    <div className="pt-4 border-t border-bambu-dark-tertiary">
                      <p className="text-sm text-bambu-gray mb-4">
                        {t('settings.enableAuthTo')}
                      </p>
                      <ul className="space-y-2 text-sm text-bambu-gray mb-4">
                        <li className="flex items-start gap-2">
                          <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                          <span>{t('settings.authBenefitLogin')}</span>
                        </li>
                        <li className="flex items-start gap-2">
                          <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                          <span>{t('settings.authBenefitUsers')}</span>
                        </li>
                        <li className="flex items-start gap-2">
                          <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                          <span>{t('settings.authBenefitAccess')}</span>
                        </li>
                      </ul>

                      <Button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          navigate('/setup');
                        }}
                        className="w-full"
                      >
                        <Lock className="w-4 h-4" />
                        {t('settings.activateAuth')}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <div className="w-12 h-12 rounded-full flex items-center justify-center bg-green-500/20">
                        <Lock className="w-6 h-6 text-green-400" />
                      </div>
                      <div className="flex-1">
                        <h3 className="text-white font-medium">{t('settings.authEnabled')}</h3>
                        <p className="text-sm text-bambu-gray">
                          {t('settings.authEnabledDescription')}
                        </p>
                      </div>
                    </div>

                    {user && (
                      <div className="pt-4 border-t border-bambu-dark-tertiary">
                        <div className="flex items-center justify-between mb-4">
                          <div>
                            <p className="text-sm text-bambu-gray">{t('settings.currentUser')}</p>
                            <p className="text-white font-medium">{user.username}</p>
                            <p className="text-xs text-bambu-gray mt-1">
                              {t('settings.role')} <span className="capitalize">{user.role}</span>
                            </p>
                          </div>
                          <div className={`px-3 py-1 rounded-full text-xs font-medium ${
                            user.role === 'admin'
                              ? 'bg-purple-500/20 text-purple-300'
                              : 'bg-blue-500/20 text-blue-300'
                          }`}>
                            {user.role === 'admin' ? t('settings.roleAdmin') : t('settings.roleUser')}
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="pt-4 border-t border-bambu-dark-tertiary space-y-3">
                      <Button
                        onClick={() => navigate('/users')}
                        className="w-full"
                        variant="secondary"
                      >
                        <Users className="w-4 h-4" />
                        {t('settings.manageUsers')}
                      </Button>

                      {user?.role === 'admin' && (
                        <Button
                          onClick={() => setShowDisableAuthConfirm(true)}
                          className="w-full"
                          variant="secondary"
                        >
                          <Unlock className="w-4 h-4" />
                          {t('settings.disableAuth')}
                        </Button>
                      )}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {authEnabled && (
            <div>
              <div className="mb-6">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Shield className="w-5 h-5 text-bambu-green" />
                  {t('settings.rolePermissions')}
                </h2>
                <p className="text-sm text-bambu-gray mt-1">
                  {t('settings.rolePermissionsDescription')}
                </p>
              </div>

              <div className="space-y-4">
                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full bg-purple-500/20 flex items-center justify-center">
                        <Shield className="w-4 h-4 text-purple-300" />
                      </div>
                      <h3 className="text-white font-medium">{t('settings.roleAdmin')}</h3>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <ul className="space-y-2 text-sm text-bambu-gray">
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.adminPerm1')}</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.adminPerm2')}</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.adminPerm3')}</span>
                      </li>
                    </ul>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-full bg-blue-500/20 flex items-center justify-center">
                        <Users className="w-4 h-4 text-blue-300" />
                      </div>
                      <h3 className="text-white font-medium">{t('settings.roleUser')}</h3>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <ul className="space-y-2 text-sm text-bambu-gray">
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.userPerm1')}</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.userPerm2')}</span>
                      </li>
                      <li className="flex items-start gap-2">
                        <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                        <span>{t('settings.userPerm3')}</span>
                      </li>
                    </ul>
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Disable Authentication Confirmation Modal */}
      {showDisableAuthConfirm && (
        <ConfirmModal
          title={t('settings.disableAuthTitle')}
          message={t('settings.disableAuthMessage')}
          confirmText={t('settings.disableAuthConfirm')}
          variant="danger"
          onConfirm={async () => {
            try {
              await api.disableAuth();
              showToast(t('settings.authDisabledSuccess'), 'success');
              await refreshAuth();
              setShowDisableAuthConfirm(false);
              // Refresh the page to ensure all protected routes are accessible
              window.location.href = '/';
            } catch (error: unknown) {
              const message = error instanceof Error ? error.message : t('settings.disableAuthFailed');
              showToast(message, 'error');
            }
          }}
          onCancel={() => setShowDisableAuthConfirm(false)}
        />
      )}
    </div>
  );
}
