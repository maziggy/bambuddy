import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Loader2, Plus, Plug, AlertTriangle, RotateCcw, Bell, Download, RefreshCw, ExternalLink, Globe, Droplets, Thermometer, FileText, Edit2, Send, CheckCircle, XCircle, History, Trash2, Zap, TrendingUp, Calendar, DollarSign, Power, PowerOff, Key, Copy, Database, X, Shield, Printer, Cylinder, Wifi, Home, Video, Users, Lock, Unlock, ChevronDown, Save, Mail, Flame, Layers, ListOrdered, Code, Search, Scale, Settings as SettingsIcon, ScanEye, Cog } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { formatDateOnly } from '../utils/date';
import { getCurrencySymbol, SUPPORTED_CURRENCIES } from '../utils/currency';
import type { APIKey, AppSettings, AppSettingsUpdate, SmartPlug, SmartPlugStatus, NotificationProvider, NotificationTemplate, UpdateStatus, GitHubBackupStatus, CloudAuthStatus, UserCreate, UserUpdate, UserResponse, StorageUsageResponse } from '../api/client';
import { Card, CardContent, CardDensityProvider, CardHeader } from '../components/Card';
import { SlicerBundlesPanel } from '../components/SlicerBundlesPanel';
import { CameraTokensSection } from './CameraTokensPage';
import { Collapsible } from '../components/Collapsible';
import { Button } from '../components/Button';
import { SmartPlugCard } from '../components/SmartPlugCard';
import { AddSmartPlugModal } from '../components/AddSmartPlugModal';
import { NotificationProviderCard } from '../components/NotificationProviderCard';
import { AddNotificationModal } from '../components/AddNotificationModal';
import { NotificationTemplateEditor } from '../components/NotificationTemplateEditor';
import { NotificationLogViewer } from '../components/NotificationLogViewer';
import { ConfirmModal } from '../components/ConfirmModal';
import { CreateUserAdvancedAuthModal } from '../components/CreateUserAdvancedAuthModal';
import { SpoolmanSettings } from '../components/SpoolmanSettings';
import { SpoolCatalogSettings } from '../components/SpoolCatalogSettings';
import { ColorCatalogSettings } from '../components/ColorCatalogSettings';
import { ExternalLinksSettings } from '../components/ExternalLinksSettings';
import { VirtualPrinterList } from '../components/VirtualPrinterList';
import { SpoolBuddySettings } from '../components/SpoolBuddySettings';
import { GitHubBackupSettings } from '../components/GitHubBackupSettings';
import { FailureDetectionSettings } from '../components/FailureDetectionSettings';
import { EmailSettings } from '../components/EmailSettings';
import { LDAPSettings } from '../components/LDAPSettings';
import { TwoFactorSettings } from '../components/TwoFactorSettings';
import { OIDCProviderSettings } from '../components/OIDCProviderSettings';
import { APIBrowser } from '../components/APIBrowser';
import { Toggle } from '../components/Toggle';
import { virtualPrinterApi, spoolbuddyApi } from '../api/client';
import { defaultNavItems, getDefaultView, setDefaultView } from '../components/Layout';
import { availableLanguages } from '../i18n';
import { useToast } from '../contexts/ToastContext';
import { useTheme, type ThemeStyle, type DarkBackground, type LightBackground, type ThemeAccent } from '../contexts/ThemeContext';
import { useState, useEffect, useRef, useCallback } from 'react';
import { Palette } from 'lucide-react';
import { registerSettingsSearch, getSettingsSearchEntries } from '../lib/settingsSearch';

const validTabs = ['general', 'plugs', 'notifications', 'queue', 'filament', 'network', 'apikeys', 'virtual-printer', 'spoolbuddy', 'failure-detection', 'users', 'backup'] as const;
type TabType = typeof validTabs[number];
type UsersSubTab = 'users' | 'email' | 'ldap' | 'twofa' | 'oidc';

// Cross-tab search registrations for cards rendered inline in this file.
// Adding a new settings card? Register it here (or, if the card lives in its
// own component file, call registerSettingsSearch at that file's module scope).
registerSettingsSearch({ labelKey: 'settings.general', tab: 'general', keywords: 'language date time format printer model printers cards', anchor: 'card-general' });
registerSettingsSearch({ labelKey: 'settings.appearance', tab: 'general', keywords: 'theme dark light mode colors', anchor: 'card-appearance' });
registerSettingsSearch({ labelKey: 'settings.archiveSettings', tab: 'general', keywords: 'archive auto save thumbnails captures', anchor: 'card-archive' });
registerSettingsSearch({ labelKey: 'settings.camera', tab: 'general', keywords: 'camera external video stream', anchor: 'card-camera' });
registerSettingsSearch({ labelKey: 'settings.costTracking', tab: 'general', keywords: 'currency filament cost energy kwh price', anchor: 'card-cost' });
registerSettingsSearch({ labelKey: 'settings.fileManager', tab: 'general', keywords: 'file manager archive mode disk warning storage', anchor: 'card-filemanager' });
registerSettingsSearch({ labelKey: 'settings.updates', tab: 'general', keywords: 'updates version firmware beta check', anchor: 'card-updates' });
registerSettingsSearch({ labelKey: 'settings.dataManagement', tab: 'general', keywords: 'data reset clear logs notifications preferences', anchor: 'card-data' });
registerSettingsSearch({ labelKey: 'settings.smartPlugs', tab: 'plugs', keywords: 'smart plug energy power automation tapo kasa tplink shelly', anchor: 'card-plugs' });
registerSettingsSearch({ labelKey: 'settings.providers', tab: 'notifications', keywords: 'telegram discord email notification providers webhook', anchor: 'card-providers' });
registerSettingsSearch({ labelKey: 'settings.messageTemplates', tab: 'notifications', keywords: 'message templates notification text edit', anchor: 'card-templates' });
registerSettingsSearch({ labelKey: 'settings.defaultPrintOptions', labelFallback: 'Default Print Options', tab: 'queue', keywords: 'print bed leveling flow calibration vibration first layer timelapse', anchor: 'card-print-options' });
registerSettingsSearch({ labelKey: 'settings.staggeredStart', labelFallback: 'Staggered Start', tab: 'queue', keywords: 'staggered batch delay start queue group', anchor: 'card-staggered' });
registerSettingsSearch({ labelKey: 'settings.plateClear', labelFallback: 'Plate-Clear Confirmation', tab: 'queue', keywords: 'plate clear confirm auto queue', anchor: 'card-plate' });
registerSettingsSearch({ labelKey: 'settings.gcodeInjection', labelFallback: 'G-code Injection', tab: 'queue', keywords: 'gcode injection start end autoprint farmloop swapmod autoclear printflow', anchor: 'card-gcode' });
registerSettingsSearch({ labelKey: 'settings.slicerCard', labelFallback: 'Slicer', tab: 'queue', keywords: 'slicer orcaslicer bambustudio orca bambu api sidecar url docker preferred', anchor: 'card-slicer' });
registerSettingsSearch({ labelKey: 'settings.queueDrying', tab: 'queue', keywords: 'drying presets temperature time humidity ams', anchor: 'card-drying' });
registerSettingsSearch({ labelKey: 'settings.filamentChecks', tab: 'filament', keywords: 'filament check warning runout remaining', anchor: 'card-filamentchecks' });
registerSettingsSearch({ labelKey: 'settings.printModal', tab: 'filament', keywords: 'print modal custom mapping', anchor: 'card-printmodal' });
registerSettingsSearch({ labelKey: 'settings.amsDisplayThresholds', tab: 'filament', keywords: 'ams humidity temperature threshold history retention', anchor: 'card-amsthresholds' });
registerSettingsSearch({ labelKey: 'settings.externalUrl', tab: 'network', keywords: 'external url reverse proxy public notification link', anchor: 'card-externalurl' });
registerSettingsSearch({ labelKey: 'settings.ftpRetry', tab: 'network', keywords: 'ftp retry upload retries backoff', anchor: 'card-ftpretry' });
registerSettingsSearch({ labelKey: 'settings.homeAssistant', tab: 'network', keywords: 'home assistant ha hass mqtt integration', anchor: 'card-ha' });
registerSettingsSearch({ labelKey: 'settings.mqttPublishing', tab: 'network', keywords: 'mqtt publish broker topic', anchor: 'card-mqtt' });
registerSettingsSearch({ labelKey: 'settings.prometheusMetrics', tab: 'network', keywords: 'prometheus metrics grafana monitoring bearer token', anchor: 'card-prometheus' });
registerSettingsSearch({ labelKey: 'settings.createNewApiKey', tab: 'apikeys', keywords: 'api key create permission scope', anchor: 'card-createapi' });
registerSettingsSearch({ labelKey: 'settings.webhookEndpoints', tab: 'apikeys', keywords: 'webhook endpoint post http', anchor: 'card-webhooks' });
registerSettingsSearch({ labelKey: 'settings.apiBrowser', tab: 'apikeys', keywords: 'api browser endpoint documentation test', anchor: 'card-apibrowser' });
registerSettingsSearch({ labelKey: 'cameraTokens.title', tab: 'apikeys', keywords: 'camera token long-lived home assistant frigate kiosk stream', anchor: 'card-camera-tokens' });
registerSettingsSearch({ labelKey: 'settings.tabs.virtualPrinter', tab: 'virtual-printer', keywords: 'virtual printer proxy archive slicer bambustudio orcaslicer ip bind', anchor: 'card-vp' });
registerSettingsSearch({ labelKey: 'settings.tabs.spoolbuddy', tab: 'spoolbuddy', keywords: 'spoolbuddy device scale nfc rfid kiosk unregister', anchor: 'card-spoolbuddy' });
registerSettingsSearch({ labelKey: 'settings.currentUser', tab: 'users', subTab: 'users', keywords: 'current user profile password change', anchor: 'card-currentuser' });
registerSettingsSearch({ labelKey: 'settings.users', tab: 'users', subTab: 'users', keywords: 'users accounts list', anchor: 'card-users' });
registerSettingsSearch({ labelKey: 'settings.groups', tab: 'users', subTab: 'users', keywords: 'groups roles permissions administrators operators viewers', anchor: 'card-groups' });
registerSettingsSearch({ labelKey: 'settings.email.smtpSettings', labelFallback: 'SMTP Configuration', tab: 'users', subTab: 'email', keywords: 'smtp email send server port password auth starttls ssl', anchor: 'card-smtp' });
registerSettingsSearch({ labelKey: 'settings.ldap.title', labelFallback: 'LDAP Authentication', tab: 'users', subTab: 'ldap', keywords: 'ldap active directory ad authentication bind dn search base group mapping', anchor: 'card-ldap' });
registerSettingsSearch({ labelKey: 'settings.tabs.backup', tab: 'backup', keywords: 'backup github restore download cloud sync profiles archives', anchor: 'card-backup' });
// Sidebar Links (external links settings is rendered in the General tab)
registerSettingsSearch({ labelKey: 'externalLinks.title', labelFallback: 'Sidebar Links', tab: 'general', keywords: 'sidebar links external custom navigation url add', anchor: 'card-sidebar-links' });
// Filament tab — integrations
registerSettingsSearch({ labelKey: 'settings.filamentTracking', tab: 'filament', keywords: 'spoolman filament tracking inventory sync remote integration', anchor: 'card-spoolman' });
registerSettingsSearch({ labelKey: 'settings.catalog.spoolCatalog', labelFallback: 'Spool Catalog', tab: 'filament', keywords: 'spool catalog entries brand material reset import export', anchor: 'card-spool-catalog' });
registerSettingsSearch({ labelKey: 'settings.colorCatalog.title', labelFallback: 'Color Catalog', tab: 'filament', keywords: 'color catalog hex swatch palette sync reset', anchor: 'card-color-catalog' });
// Failure detection sub-cards
registerSettingsSearch({ labelKey: 'settings.tabs.failureDetection', labelFallback: 'Failure Detection', tab: 'failure-detection', keywords: 'failure detection ai ml obico spaghetti detect monitoring', anchor: 'card-fd-ml' });
registerSettingsSearch({ labelKey: 'failureDetection.perPrinterTitle', labelFallback: 'Per-Printer Settings', tab: 'failure-detection', keywords: 'failure detection per printer enable per-printer sensitivity', anchor: 'card-fd-perprinter' });
registerSettingsSearch({ labelKey: 'failureDetection.statusTitle', labelFallback: 'Detection Status', tab: 'failure-detection', keywords: 'failure detection status running connection', anchor: 'card-fd-status' });
registerSettingsSearch({ labelKey: 'failureDetection.historyTitle', labelFallback: 'Detection History', tab: 'failure-detection', keywords: 'failure detection history log events', anchor: 'card-fd-history' });
// Email auth sub-cards (subTab=email)
registerSettingsSearch({ labelKey: 'settings.email.advancedAuth', labelFallback: 'Advanced Email Authentication', tab: 'users', subTab: 'email', keywords: 'email authentication advanced password reset self-service forgot', anchor: 'card-email-advanced-auth' });
registerSettingsSearch({ labelKey: 'settings.email.testConnection', labelFallback: 'Test SMTP Connection', tab: 'users', subTab: 'email', keywords: 'email smtp test connection send check', anchor: 'card-email-test' });
// Two-Factor sub-cards (subTab=twofa)
registerSettingsSearch({ labelKey: 'settings.twoFa.totpTitle', labelFallback: 'Authenticator App (TOTP)', tab: 'users', subTab: 'twofa', keywords: 'two factor 2fa totp authenticator app google authy otp', anchor: 'card-2fa-totp' });
registerSettingsSearch({ labelKey: 'settings.twoFa.emailOtpTitle', labelFallback: 'Email One-Time Codes', tab: 'users', subTab: 'twofa', keywords: 'two factor 2fa email otp one time code', anchor: 'card-2fa-emailotp' });
registerSettingsSearch({ labelKey: 'settings.twoFa.linkedAccounts', labelFallback: 'Linked Accounts', tab: 'users', subTab: 'twofa', keywords: 'two factor 2fa linked accounts sso oidc provider google github', anchor: 'card-2fa-linked' });
// OIDC / SSO (subTab=oidc)
registerSettingsSearch({ labelKey: 'settings.oidc.title', labelFallback: 'Single Sign-On (OIDC)', tab: 'users', subTab: 'oidc', keywords: 'sso oidc openid single sign-on pocketid authentik keycloak google okta azure provider', anchor: 'card-oidc' });
// LDAP server config card (complements existing card-ldap)
registerSettingsSearch({ labelKey: 'settings.ldap.serverConfig', labelFallback: 'LDAP Server Configuration', tab: 'users', subTab: 'ldap', keywords: 'ldap server url bind dn user search base group filter tls', anchor: 'card-ldap-server' });
// Backup sub-cards
registerSettingsSearch({ labelKey: 'backup.githubBackup', labelFallback: 'GitHub Backup', tab: 'backup', keywords: 'github backup cloud remote sync profiles token', anchor: 'card-backup-github' });
registerSettingsSearch({ labelKey: 'backup.history', labelFallback: 'Backup History', tab: 'backup', keywords: 'backup history log runs github commits', anchor: 'card-backup-history' });
registerSettingsSearch({ labelKey: 'backup.localBackup', labelFallback: 'Local Backup', tab: 'backup', keywords: 'local backup download zip manual export', anchor: 'card-backup-local' });
registerSettingsSearch({ labelKey: 'backup.scheduledBackup', labelFallback: 'Scheduled Backups', tab: 'backup', keywords: 'scheduled backup automatic hourly daily weekly retention local path', anchor: 'card-backup-scheduled' });

const STORAGE_CATEGORY_COLORS: Record<string, string> = {
  database: 'bg-blue-600',
  library_files: 'bg-green-500',
  library_thumbnails: 'bg-teal-500',
  library_other: 'bg-emerald-700',
  archive_timelapses: 'bg-red-500',
  archive_thumbnails: 'bg-amber-500',
  archive_files: 'bg-sky-500',
  virtual_printer_uploads: 'bg-purple-500',
  virtual_printer_upload_cache: 'bg-fuchsia-500',
  virtual_printer_certs: 'bg-violet-500',
  virtual_printer_other: 'bg-purple-700',
  downloads: 'bg-cyan-500',
  plate_calibration: 'bg-lime-500',
  logs: 'bg-orange-500',
  other_data: 'bg-yellow-500',
};

const STORAGE_FALLBACK_COLORS = [
  'bg-blue-500',
  'bg-green-500',
  'bg-yellow-500',
  'bg-red-500',
  'bg-orange-500',
  'bg-teal-500',
  'bg-cyan-500',
  'bg-purple-500',
];

const getStorageColor = (key: string, index: number) =>
  STORAGE_CATEGORY_COLORS[key] || STORAGE_FALLBACK_COLORS[index % STORAGE_FALLBACK_COLORS.length];

export function SettingsPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { t, i18n } = useTranslation();
  const { showToast } = useToast();
  const { authEnabled, user, isAdmin, refreshAuth, hasPermission } = useAuth();
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
  const [templateFilter, setTemplateFilter] = useState('');
  const [settingsSearch, setSettingsSearch] = useState('');
  const [showLogViewer, setShowLogViewer] = useState(false);
  const [defaultView, setDefaultViewState] = useState<string>(getDefaultView());

  // Initialize tab from URL params (handle legacy ?tab=email → users tab + email sub-tab)
  const tabParam = searchParams.get('tab');
  const isLegacyEmailTab = tabParam === 'email';
  const initialTab = isLegacyEmailTab ? 'users' : (tabParam && validTabs.includes(tabParam as TabType) ? tabParam as TabType : 'general');
  const [activeTab, setActiveTab] = useState<TabType>(initialTab);
  const [usersSubTab, setUsersSubTab] = useState<UsersSubTab>(isLegacyEmailTab ? 'email' : 'users');

  // Update URL when tab changes
  const handleTabChange = (tab: TabType) => {
    setActiveTab(tab);
    if (tab === 'users') {
      setUsersSubTab('users');
    }
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
    can_access_cloud: false,
  });
  const [createdAPIKey, setCreatedAPIKey] = useState<string | null>(null);
  const [showDeleteAPIKeyConfirm, setShowDeleteAPIKeyConfirm] = useState<number | null>(null);
  const [testApiKey, setTestApiKey] = useState('');

  // Confirm modal states
  const [showClearLogsConfirm, setShowClearLogsConfirm] = useState(false);
  const [showClearStorageConfirm, setShowClearStorageConfirm] = useState(false);
  const [showBulkPlugConfirm, setShowBulkPlugConfirm] = useState<'on' | 'off' | null>(null);
  const [showReleaseNotes, setShowReleaseNotes] = useState(false);
  const [showDisableAuthConfirm, setShowDisableAuthConfirm] = useState(false);
  const [showChangePasswordModal, setShowChangePasswordModal] = useState(false);
  const [changePasswordData, setChangePasswordData] = useState({ currentPassword: '', newPassword: '', confirmPassword: '' });
  const [changePasswordLoading, setChangePasswordLoading] = useState(false);
  const [storageUsageRefreshing, setStorageUsageRefreshing] = useState(false);

  // User management state
  const [showCreateUserModal, setShowCreateUserModal] = useState(false);
  const [showEditUserModal, setShowEditUserModal] = useState(false);
  const [editingUserId, setEditingUserId] = useState<number | null>(null);
  const [deleteUserId, setDeleteUserId] = useState<number | null>(null);
  const [deleteUserItemCounts, setDeleteUserItemCounts] = useState<{ archives: number; queue_items: number; library_files: number } | null>(null);
  const [deleteUserLoading, setDeleteUserLoading] = useState(false);
  const [userFormData, setUserFormData] = useState<{
    username: string;
    password?: string;
    email?: string;
    confirmPassword: string;
    role: string;
    group_ids: number[];
  }>({
    username: '',
    password: '',
    email: '',
    confirmPassword: '',
    role: 'user',
    group_ids: [],
  });

  // Group management state
  const [deleteGroupId, setDeleteGroupId] = useState<number | null>(null);

  // Home Assistant test connection state
  const [haTestResult, setHaTestResult] = useState<{ success: boolean; message: string | null; error: string | null } | null>(null);
  const [haTestLoading, setHaTestLoading] = useState(false);

  // External camera test state
  const [extCameraTestResults, setExtCameraTestResults] = useState<Record<number, { success: boolean; error?: string; resolution?: string } | null>>({});
  const [extCameraTestLoading, setExtCameraTestLoading] = useState<Record<number, boolean>>({});

  const handleDefaultViewChange = (path: string) => {
    setDefaultViewState(path);
    setDefaultView(path);
    showToast(t('settings.toast.settingsSaved'), 'success');
  };

  const handleResetSidebarOrder = () => {
    localStorage.removeItem('sidebarOrder');
    window.location.reload();
  };

  const isDefaultSidebarEnabled = !!localSettings?.default_sidebar_order;

  const handleToggleDefaultSidebarOrder = async (enabled: boolean) => {
    try {
      if (enabled) {
        let orderArr: string[];
        const stored = localStorage.getItem('sidebarOrder');
        try {
          orderArr = stored ? JSON.parse(stored) : defaultNavItems.map(i => i.id);
        } catch {
          orderArr = defaultNavItems.map(i => i.id);
        }
        if (!Array.isArray(orderArr) || orderArr.length === 0) {
          orderArr = defaultNavItems.map(i => i.id);
        }
        const payload = JSON.stringify({ order: orderArr });
        await api.updateSettings({ default_sidebar_order: payload });
        setLocalSettings(prev => prev ? { ...prev, default_sidebar_order: payload } : prev);
        showToast(t('settings.sidebarDefaultSet'), 'success');
      } else {
        await api.updateSettings({ default_sidebar_order: '' });
        setLocalSettings(prev => prev ? { ...prev, default_sidebar_order: '' } : prev);
        showToast(t('settings.sidebarDefaultCleared'), 'success');
      }
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['default-sidebar-order'] });
    } catch {
      showToast(t('settings.sidebarDefaultFailed'), 'error');
    }
  };

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const {
    data: storageUsage,
    isLoading: storageUsageLoading,
    isFetching: storageUsageFetching,
  } = useQuery<StorageUsageResponse>({
    queryKey: ['storage-usage'],
    queryFn: () => api.getStorageUsage(),
    enabled: activeTab === 'general',
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

  const handleStorageUsageRefresh = async () => {
    setStorageUsageRefreshing(true);
    try {
      const data = await api.getStorageUsage({ refresh: true });
      queryClient.setQueryData(['storage-usage'], data);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Failed to refresh storage usage';
      showToast(message, 'error');
    } finally {
      setStorageUsageRefreshing(false);
    }
  };

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

      for (const { plug, status } of statuses) {
        // For MQTT plugs, consider reachable if we have power data
        const hasMqttData = plug.plug_type === 'mqtt' && (status?.energy?.power != null);
        const isReachable = (status?.reachable || hasMqttData) && status?.energy;

        if (isReachable) {
          reachableCount++;
          if (status.energy?.power != null) totalPower += status.energy.power;
          if (status.energy?.today != null) totalToday += status.energy.today;
          if (status.energy?.yesterday != null) totalYesterday += status.energy.yesterday;
          if (status.energy?.total != null) totalLifetime += status.energy.total;
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
    mutationFn: (data: { name: string; can_queue: boolean; can_control_printer: boolean; can_read_status: boolean; can_access_cloud: boolean }) =>
      api.createAPIKey(data),
    onSuccess: (data) => {
      setCreatedAPIKey(data.key || null);
      setShowCreateAPIKey(false);
      setNewAPIKeyName('');
      queryClient.invalidateQueries({ queryKey: ['api-keys'] });
      showToast(t('settings.toast.apiKeyCreated'));
    },
    onError: (error: Error) => {
      showToast(`Failed to create API key: ${error.message}`, 'error');
    },
  });

  const deleteAPIKeyMutation = useMutation({
    mutationFn: (id: number) => api.deleteAPIKey(id),
    onSuccess: (_data, deletedId) => {
      queryClient.setQueryData<APIKey[]>(['api-keys'], (old) =>
        (old ?? []).filter((key) => key.id !== deletedId)
      );
      showToast(t('settings.toast.apiKeyDeleted'));
    },
    onError: (error: Error) => {
      showToast(`Failed to delete API key: ${error.message}`, 'error');
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

  // SpoolBuddy devices for tab indicator
  const { data: spoolbuddyDevices } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 15000,
  });
  const spoolbuddyDeviceCount = spoolbuddyDevices?.length ?? 0;
  const spoolbuddyAnyOnline = spoolbuddyDevices?.some((d) => d.online) ?? false;

  // Obico failure-detection service status for tab indicator
  const { data: obicoStatus } = useQuery({
    queryKey: ['obico-status'],
    queryFn: api.getObicoStatus,
    refetchInterval: 15000,
  });
  const obicoActive = !!(obicoStatus?.is_running && obicoStatus?.enabled);

  const { data: ffmpegStatus } = useQuery({
    queryKey: ['ffmpeg-status'],
    queryFn: api.checkFfmpeg,
  });

  const { data: versionInfo } = useQuery({
    queryKey: ['version'],
    queryFn: api.getVersion,
  });

  // Library trash settings (#1008). Separate endpoint from the generic
  // /settings — persists retention window + auto-purge config. Admin-only.
  const canPurge = !authEnabled || hasPermission('library:purge');
  const { data: trashSettings } = useQuery({
    queryKey: ['library-trash-settings'],
    queryFn: () => api.getLibraryTrashSettings(),
    enabled: canPurge,
  });

  const updateTrashSettingsMutation = useMutation({
    mutationFn: (body: {
      retention_days: number;
      auto_purge_enabled: boolean;
      auto_purge_days: number;
      auto_purge_include_never_printed: boolean;
    }) => api.updateLibraryTrashSettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-trash-settings'] });
      showToast(t('settings.toast.settingsSaved'), 'success');
    },
    onError: (e: Error) => showToast(e.message || t('libraryAutoPurge.saveFailed'), 'error'),
  });

  const saveTrashSettings = (patch: Partial<{
    retention_days: number;
    auto_purge_enabled: boolean;
    auto_purge_days: number;
    auto_purge_include_never_printed: boolean;
  }>) => {
    if (!trashSettings) return;
    updateTrashSettingsMutation.mutate({
      retention_days: trashSettings.retention_days,
      auto_purge_enabled: trashSettings.auto_purge_enabled,
      auto_purge_days: trashSettings.auto_purge_days,
      auto_purge_include_never_printed: trashSettings.auto_purge_include_never_printed,
      ...patch,
    });
  };

  // Archive auto-purge (#1008 follow-up). Gated on the dedicated archives:purge
  // permission so admins can delegate bulk-delete to a role without granting
  // per-archive delete on other users' rows.
  const canPurgeArchives = !authEnabled || hasPermission('archives:purge');
  const { data: archivePurgeSettings } = useQuery({
    queryKey: ['archive-purge-settings'],
    queryFn: () => api.getArchivePurgeSettings(),
    enabled: canPurgeArchives,
  });

  const updateArchivePurgeSettingsMutation = useMutation({
    mutationFn: (body: { enabled: boolean; days: number }) => api.updateArchivePurgeSettings(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['archive-purge-settings'] });
      showToast(t('settings.toast.settingsSaved'), 'success');
    },
    onError: (e: Error) => showToast(e.message || t('archiveAutoPurge.saveFailed'), 'error'),
  });

  const saveArchivePurgeSettings = (patch: Partial<{ enabled: boolean; days: number }>) => {
    if (!archivePurgeSettings) return;
    updateArchivePurgeSettingsMutation.mutate({
      enabled: archivePurgeSettings.enabled,
      days: archivePurgeSettings.days,
      ...patch,
    });
  };

  const { data: updateCheck, refetch: refetchUpdateCheck, isRefetching: isCheckingUpdate } = useQuery({
    queryKey: ['updateCheck'],
    queryFn: api.checkForUpdates,
    enabled: settings?.check_updates !== false,
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

  // GitHub backup status for Backup tab indicator
  const { data: githubBackupStatus } = useQuery<GitHubBackupStatus>({
    queryKey: ['github-backup-status'],
    queryFn: api.getGitHubBackupStatus,
  });

  // Cloud auth status for Backup tab indicator
  const { data: cloudAuthStatus } = useQuery<CloudAuthStatus>({
    queryKey: ['cloud-status'],
    queryFn: api.getCloudStatus,
  });

  // Advanced auth status for user creation
  const { data: advancedAuthStatus = { advanced_auth_enabled: false, smtp_configured: false } } = useQuery({
    queryKey: ['advancedAuthStatus'],
    queryFn: () => api.getAdvancedAuthStatus(),
  });

  const { data: ldapStatus } = useQuery({
    queryKey: ['ldapStatus'],
    queryFn: () => api.getLDAPStatus(),
  });

  // Tab-indicator queries: green bullet when 2FA is enabled for the current
  // user, or when at least one OIDC provider is configured and enabled.
  const { data: twoFAStatus } = useQuery({
    queryKey: ['twoFAStatus'],
    queryFn: () => api.get2FAStatus(),
  });
  const { data: oidcProvidersAll = [] } = useQuery({
    queryKey: ['oidcProvidersAll'],
    queryFn: () => api.getOIDCProvidersAll(),
    enabled: isAdmin,
  });

  // User management queries and mutations
  const { data: usersData = [], isLoading: usersLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
    enabled: authEnabled && hasPermission('users:read'),
  });

  const { data: groupsData = [], isLoading: groupsLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.getGroups(),
    enabled: authEnabled && hasPermission('groups:read'),
  });

  const createUserMutation = useMutation({
    mutationFn: (data: UserCreate) => api.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowCreateUserModal(false);
      setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
      showToast(t('settings.toast.userCreated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const updateUserMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: UserUpdate }) => api.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      setShowEditUserModal(false);
      setEditingUserId(null);
      setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
      showToast(t('settings.toast.userUpdated'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const deleteUserMutation = useMutation({
    mutationFn: ({ id, deleteItems }: { id: number; deleteItems: boolean }) => api.deleteUser(id, deleteItems),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      showToast(t('settings.toast.userDeleted'));
      setDeleteUserId(null);
      setDeleteUserItemCounts(null);
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  const resetPasswordMutation = useMutation({
    mutationFn: (userId: number) => api.resetUserPassword({ user_id: userId }),
    onSuccess: (response) => {
      showToast(response.message, 'success');
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // Function to initiate user deletion with item count check
  const handleDeleteUserClick = async (userId: number) => {
    setDeleteUserId(userId);
    setDeleteUserLoading(true);
    try {
      const counts = await api.getUserItemsCount(userId);
      setDeleteUserItemCounts(counts);
    } catch {
      // If we can't get counts, just proceed without showing item options
      setDeleteUserItemCounts({ archives: 0, queue_items: 0, library_files: 0 });
    } finally {
      setDeleteUserLoading(false);
    }
  };

  const deleteGroupMutation = useMutation({
    mutationFn: (id: number) => api.deleteGroup(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      showToast(t('settings.toast.groupDeleted'));
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
  });

  // User management handlers
  const handleCreateUser = () => {
    // Use the status from the query hook
    const advancedAuthEnabled = advancedAuthStatus?.advanced_auth_enabled || false;

    if (!userFormData.username) {
      showToast(t('settings.toast.fillRequiredFields'), 'error');
      return;
    }

    // Email is required when advanced auth is enabled
    if (advancedAuthEnabled && !userFormData.email) {
      showToast('Email is required when advanced authentication is enabled', 'error');
      return;
    }

    // Password validation only when advanced auth is disabled
    if (!advancedAuthEnabled) {
      if (!userFormData.password) {
        showToast(t('settings.toast.fillRequiredFields'), 'error');
        return;
      }
      if (userFormData.password !== userFormData.confirmPassword) {
        showToast(t('settings.toast.passwordsDoNotMatch'), 'error');
        return;
      }
      if (userFormData.password.length < 6) {
        showToast(t('settings.toast.passwordTooShort'), 'error');
        return;
      }
    }

    createUserMutation.mutate({
      username: userFormData.username,
      password: advancedAuthEnabled ? undefined : userFormData.password,
      email: userFormData.email || undefined,
      role: userFormData.role,
      group_ids: userFormData.group_ids.length > 0 ? userFormData.group_ids : undefined,
    });
  };

  const handleUpdateUser = (id: number) => {
    if (userFormData.password) {
      if (userFormData.password !== userFormData.confirmPassword) {
        showToast(t('settings.toast.passwordsDoNotMatch'), 'error');
        return;
      }
      if (userFormData.password.length < 6) {
        showToast(t('settings.toast.passwordTooShort'), 'error');
        return;
      }
    }
    const updateData: UserUpdate = {
      username: userFormData.username || undefined,
      password: userFormData.password || undefined,
      email: userFormData.email || undefined,
      role: userFormData.role,
      group_ids: userFormData.group_ids,
    };
    if (!updateData.password) {
      delete updateData.password;
    }
    updateUserMutation.mutate({ id, data: updateData });
  };

  const startEditUser = (userToEdit: UserResponse) => {
    setEditingUserId(userToEdit.id);
    setUserFormData({
      username: userToEdit.username,
      password: '',
      email: userToEdit.email || '',
      confirmPassword: '',
      role: userToEdit.role,
      group_ids: userToEdit.groups?.map(g => g.id) || [],
    });
    setShowEditUserModal(true);
  };

  const toggleUserGroup = (groupId: number) => {
    setUserFormData(prev => ({
      ...prev,
      group_ids: prev.group_ids.includes(groupId)
        ? prev.group_ids.filter(id => id !== groupId)
        : [...prev.group_ids, groupId],
    }));
  };

  const applyUpdateMutation = useMutation({
    mutationFn: api.applyUpdate,
    onSuccess: (data) => {
      if (data.is_ha_addon || data.is_docker) {
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
        showToast(`All ${data.tested} providers tested successfully!`, 'success');
      } else {
        showToast(`${data.success}/${data.tested} providers succeeded`, data.failed > 0 ? 'error' : 'success');
      }
    },
    onError: (error: Error) => {
      showToast(`Failed to test providers: ${error.message}`, 'error');
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
        showToast(`All ${data.success} plugs turned ${action}`, 'success');
      } else {
        showToast(`${data.success} plugs turned ${action}, ${data.failed} failed`, 'error');
      }
    },
    onError: (error: Error) => {
      showToast(`Failed: ${error.message}`, 'error');
    },
  });

  // Ref for debounce timeout
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingGcodeSnippetsRef = useRef<string | null>(null);
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
      // Don't call setLocalSettings(data) here — it would overwrite in-progress
      // user input (e.g. typing a hostname) with the stale saved snapshot,
      // causing the text field to reset mid-typing. Instead, let the useEffect
      // re-compare the updated `settings` with current `localSettings` and
      // debounce-save any remaining differences.
      queryClient.invalidateQueries({ queryKey: ['archiveStats'] });
      showToast(t('settings.toast.settingsSaved'), 'success');
    },
    onError: (error: Error) => {
      showToast(`Failed to save: ${error.message}`, 'error');
      // No localSettings rollback here — the existing comment above (see
      // onSuccess) already flags that overwriting localSettings would discard
      // in-progress user input (e.g. typing a hostname). The no-permission
      // loop is already prevented by the up-front guards in updateSetting and
      // in the debounced-save effect, so this onError path now only fires for
      // genuine server/network failures where preserving typed-in values is
      // the right call.
    },
    onSettled: () => {
      // Reset saving flag when mutation completes (success or error)
      isSavingRef.current = false;
    },
  });

  const updatePrinterMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<{ external_camera_url: string | null; external_camera_type: string | null; external_camera_enabled: boolean; external_camera_snapshot_url: string | null; camera_rotation: number }> }) =>
      api.updatePrinter(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('settings.toast.cameraSettingsSaved'), 'success');
    },
    onError: (error: Error) => {
      showToast(`Failed to update printer: ${error.message}`, 'error');
    },
  });

  // Debounced auto-save when localSettings change
  useEffect(() => {
    // Skip if initial load or no settings
    if (isInitialLoadRef.current || !localSettings || !settings) {
      return;
    }

    // Safety net: skip auto-save entirely when the user lacks settings:update.
    // The actual user feedback (toast + revert) lives in updateSetting below,
    // which runs once per click. Doing it here as well would fire on every
    // React render since the debounced-save effect depends on non-stable refs.
    if (authEnabled && !hasPermission('settings:update')) {
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
      (settings.check_printer_firmware ?? true) !== (localSettings.check_printer_firmware ?? true) ||
      (settings.include_beta_updates ?? false) !== (localSettings.include_beta_updates ?? false) ||
      settings.notification_language !== localSettings.notification_language ||
      (settings.bed_cooled_threshold ?? 35) !== (localSettings.bed_cooled_threshold ?? 35) ||
      settings.ams_humidity_good !== localSettings.ams_humidity_good ||
      settings.ams_humidity_fair !== localSettings.ams_humidity_fair ||
      settings.ams_temp_good !== localSettings.ams_temp_good ||
      settings.ams_temp_fair !== localSettings.ams_temp_fair ||
      settings.ams_history_retention_days !== localSettings.ams_history_retention_days ||
      settings.disable_filament_warnings !== localSettings.disable_filament_warnings ||
      settings.prefer_lowest_filament !== localSettings.prefer_lowest_filament ||
      (settings.queue_drying_enabled ?? false) !== (localSettings.queue_drying_enabled ?? false) ||
      (settings.queue_drying_block ?? false) !== (localSettings.queue_drying_block ?? false) ||
      (settings.ambient_drying_enabled ?? false) !== (localSettings.ambient_drying_enabled ?? false) ||
      (settings.drying_presets ?? '') !== (localSettings.drying_presets ?? '') ||
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
      (settings.camera_view_mode ?? 'window') !== (localSettings.camera_view_mode ?? 'window') ||
      (settings.preferred_slicer ?? 'bambu_studio') !== (localSettings.preferred_slicer ?? 'bambu_studio') ||
      (settings.use_slicer_api ?? false) !== (localSettings.use_slicer_api ?? false) ||
      (settings.orcaslicer_api_url ?? '') !== (localSettings.orcaslicer_api_url ?? '') ||
      (settings.bambu_studio_api_url ?? '') !== (localSettings.bambu_studio_api_url ?? '') ||
      settings.prometheus_enabled !== localSettings.prometheus_enabled ||
      settings.prometheus_token !== localSettings.prometheus_token ||
      (settings.user_notifications_enabled ?? true) !== (localSettings.user_notifications_enabled ?? true) ||
      (settings.default_bed_levelling ?? true) !== (localSettings.default_bed_levelling ?? true) ||
      (settings.default_flow_cali ?? false) !== (localSettings.default_flow_cali ?? false) ||
      (settings.default_vibration_cali ?? true) !== (localSettings.default_vibration_cali ?? true) ||
      (settings.default_layer_inspect ?? false) !== (localSettings.default_layer_inspect ?? false) ||
      (settings.default_timelapse ?? false) !== (localSettings.default_timelapse ?? false) ||
      (settings.stagger_group_size ?? 2) !== (localSettings.stagger_group_size ?? 2) ||
      (settings.stagger_interval_minutes ?? 5) !== (localSettings.stagger_interval_minutes ?? 5) ||
      (settings.require_plate_clear ?? false) !== (localSettings.require_plate_clear ?? false);

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
        check_printer_firmware: localSettings.check_printer_firmware,
        include_beta_updates: localSettings.include_beta_updates,
        notification_language: localSettings.notification_language,
        bed_cooled_threshold: localSettings.bed_cooled_threshold,
        ams_humidity_good: localSettings.ams_humidity_good,
        ams_humidity_fair: localSettings.ams_humidity_fair,
        ams_temp_good: localSettings.ams_temp_good,
        ams_temp_fair: localSettings.ams_temp_fair,
        ams_history_retention_days: localSettings.ams_history_retention_days,
        disable_filament_warnings: localSettings.disable_filament_warnings,
        prefer_lowest_filament: localSettings.prefer_lowest_filament,
        queue_drying_enabled: localSettings.queue_drying_enabled,
        queue_drying_block: localSettings.queue_drying_block,
        ambient_drying_enabled: localSettings.ambient_drying_enabled,
        drying_presets: localSettings.drying_presets,
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
        preferred_slicer: localSettings.preferred_slicer,
        use_slicer_api: localSettings.use_slicer_api,
        orcaslicer_api_url: localSettings.orcaslicer_api_url,
        bambu_studio_api_url: localSettings.bambu_studio_api_url,
        prometheus_enabled: localSettings.prometheus_enabled,
        prometheus_token: localSettings.prometheus_token,
        user_notifications_enabled: localSettings.user_notifications_enabled,
        default_bed_levelling: localSettings.default_bed_levelling,
        default_flow_cali: localSettings.default_flow_cali,
        default_vibration_cali: localSettings.default_vibration_cali,
        default_layer_inspect: localSettings.default_layer_inspect,
        default_timelapse: localSettings.default_timelapse,
        stagger_group_size: localSettings.stagger_group_size,
        stagger_interval_minutes: localSettings.stagger_interval_minutes,
        require_plate_clear: localSettings.require_plate_clear,
      };
      updateMutation.mutate(settingsToSave);
    }, 500);

    // Cleanup on unmount or when localSettings changes again
    return () => {
      if (saveTimeoutRef.current) {
        clearTimeout(saveTimeoutRef.current);
      }
    };
  }, [localSettings, settings, updateMutation, authEnabled, hasPermission, showToast, t]);

  const updateSetting = useCallback(<K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    // Gate at the point of user interaction (not in the debounced-save effect —
    // that runs on every render and would fire the toast repeatedly). One toast
    // per attempt; no local state divergence for a read-only delegated user.
    if (authEnabled && !hasPermission('settings:update')) {
      showToast(t('settings.toast.noPermissionUpdate'), 'error');
      return;
    }
    setLocalSettings(prev => prev ? { ...prev, [key]: value } : null);
  }, [authEnabled, hasPermission, showToast, t]);

  const handleTestExternalCamera = async (printerId: number, url: string, cameraType: string) => {
    if (!url) {
      showToast(t('settings.toast.enterCameraUrl'), 'error');
      return;
    }
    setExtCameraTestLoading(prev => ({ ...prev, [printerId]: true }));
    setExtCameraTestResults(prev => ({ ...prev, [printerId]: null }));
    try {
      const result = await api.testExternalCamera(printerId, url, cameraType);
      setExtCameraTestResults(prev => ({ ...prev, [printerId]: result }));
      if (result.success) {
        showToast(t('settings.toast.cameraConnected', { resolution: result.resolution || '' }), 'success');
      } else {
        showToast(result.error || t('settings.toast.connectionFailed'), 'error');
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : t('settings.toast.testFailed');
      setExtCameraTestResults(prev => ({ ...prev, [printerId]: { success: false, error: message } }));
      showToast(message, 'error');
    } finally {
      setExtCameraTestLoading(prev => ({ ...prev, [printerId]: false }));
    }
  };

  // Local state for camera URL inputs (to avoid saving on every keystroke)
  const [localCameraUrls, setLocalCameraUrls] = useState<Record<number, string>>({});
  const cameraUrlSaveTimeoutRef = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const initializedPrinterUrlsRef = useRef<Set<number>>(new Set());
  const [localSnapshotUrls, setLocalSnapshotUrls] = useState<Record<number, string>>({});
  const snapshotUrlSaveTimeoutRef = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const initializedPrinterSnapshotUrlsRef = useRef<Set<number>>(new Set());

  // Initialize local camera URLs from printer data
  useEffect(() => {
    if (printers) {
      const urls: Record<number, string> = {};
      const snapUrls: Record<number, string> = {};
      printers.forEach(p => {
        if (p.external_camera_url && !initializedPrinterUrlsRef.current.has(p.id)) {
          urls[p.id] = p.external_camera_url;
          initializedPrinterUrlsRef.current.add(p.id);
        }
        if (p.external_camera_snapshot_url && !initializedPrinterSnapshotUrlsRef.current.has(p.id)) {
          snapUrls[p.id] = p.external_camera_snapshot_url;
          initializedPrinterSnapshotUrlsRef.current.add(p.id);
        }
      });
      if (Object.keys(urls).length > 0) {
        setLocalCameraUrls(prev => ({ ...prev, ...urls }));
      }
      if (Object.keys(snapUrls).length > 0) {
        setLocalSnapshotUrls(prev => ({ ...prev, ...snapUrls }));
      }
    }
  }, [printers]);

  const handleCameraUrlChange = (printerId: number, url: string) => {
    // Update local state immediately for responsive UI
    setLocalCameraUrls(prev => ({ ...prev, [printerId]: url }));

    // Clear existing timeout for this printer
    if (cameraUrlSaveTimeoutRef.current[printerId]) {
      clearTimeout(cameraUrlSaveTimeoutRef.current[printerId]);
    }

    // Debounce the save (800ms delay)
    cameraUrlSaveTimeoutRef.current[printerId] = setTimeout(() => {
      updatePrinterMutation.mutate({
        id: printerId,
        data: { external_camera_url: url || null }
      });
    }, 800);
  };

  const handleSnapshotUrlChange = (printerId: number, url: string) => {
    setLocalSnapshotUrls(prev => ({ ...prev, [printerId]: url }));

    if (snapshotUrlSaveTimeoutRef.current[printerId]) {
      clearTimeout(snapshotUrlSaveTimeoutRef.current[printerId]);
    }

    snapshotUrlSaveTimeoutRef.current[printerId] = setTimeout(() => {
      updatePrinterMutation.mutate({
        id: printerId,
        data: { external_camera_snapshot_url: url || null }
      });
    }, 800);
  };

  const handleUpdatePrinterCamera = (printerId: number, updates: { type?: string; enabled?: boolean; rotation?: number }) => {
    const data: Partial<{ external_camera_type: string | null; external_camera_enabled: boolean; camera_rotation: number }> = {};
    if (updates.type !== undefined) data.external_camera_type = updates.type || null;
    if (updates.enabled !== undefined) data.external_camera_enabled = updates.enabled;
    if (updates.rotation !== undefined) data.camera_rotation = updates.rotation;
    updatePrinterMutation.mutate({ id: printerId, data });
  };

  if (isLoading || !localSettings) {
    return (
      <div className="p-4 md:p-8 flex justify-center">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  // Cross-tab search is powered by the module-level registry in lib/settingsSearch.
  // Resolve i18n labels here so language changes take effect without re-registering.
  const searchIndex = getSettingsSearchEntries().map(e => ({
    ...e,
    label: t(e.labelKey, e.labelFallback ?? e.labelKey),
  }));

  const searchQuery = settingsSearch.trim().toLowerCase();
  const searchResults = searchQuery
    ? searchIndex.filter(
        e =>
          e.label.toLowerCase().includes(searchQuery) ||
          e.keywords.toLowerCase().includes(searchQuery)
      ).slice(0, 8)
    : [];

  const jumpToSetting = (entry: typeof searchIndex[number]) => {
    handleTabChange(entry.tab as TabType);
    if (entry.subTab) {
      setUsersSubTab(entry.subTab as UsersSubTab);
    }
    setSettingsSearch('');
    // Scroll to the card after the tab has rendered
    setTimeout(() => {
      const el = document.getElementById(entry.anchor);
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        el.classList.add('ring-2', 'ring-bambu-green');
        setTimeout(() => el.classList.remove('ring-2', 'ring-bambu-green'), 1500);
      }
    }, 50);
  };

  return (
    <CardDensityProvider density="dense">
    <div className="p-4 md:p-6">
      <div className="mb-4 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold text-white">{t('settings.title')}</h1>
          <p className="text-sm text-bambu-gray hidden md:block">{t('settings.configureBambuddy')}</p>
        </div>
        {/* Cross-tab search */}
        <div className="relative sm:w-72">
          <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            value={settingsSearch}
            onChange={(e) => setSettingsSearch(e.target.value)}
            placeholder={t('settings.searchPlaceholder', 'Search settings…')}
            className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
          />
          {settingsSearch && (
            <button
              onClick={() => setSettingsSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-bambu-gray hover:text-white"
              aria-label="Clear"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
          {searchResults.length > 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-30 overflow-hidden">
              {searchResults.map((entry) => (
                <button
                  key={entry.anchor}
                  onClick={() => jumpToSetting(entry)}
                  className="w-full px-3 py-2 text-left hover:bg-bambu-dark-tertiary transition-colors border-b border-bambu-dark-tertiary last:border-b-0"
                >
                  <p className="text-sm text-white">{entry.label}</p>
                  <p className="text-xs text-bambu-gray">
                    {t(`settings.tabs.${entry.tab === 'virtual-printer' ? 'virtualPrinter' : entry.tab === 'failure-detection' ? 'failureDetection' : entry.tab}`)}
                    {entry.subTab ? ` › ${t(`settings.tabs.${entry.subTab}`, entry.subTab)}` : ''}
                  </p>
                </button>
              ))}
            </div>
          )}
          {searchQuery && searchResults.length === 0 && (
            <div className="absolute top-full left-0 right-0 mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl z-30 p-3">
              <p className="text-xs text-bambu-gray italic">{t('settings.noSearchResults', 'No matching settings.')}</p>
            </div>
          )}
        </div>
      </div>

      {/* Tab Navigation + content: horizontal tabs on mobile, vertical rail on lg+ */}
      <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
      <nav className="flex flex-wrap gap-1 border-b border-bambu-dark-tertiary lg:flex-col lg:flex-nowrap lg:gap-0 lg:border-b-0 lg:border-r lg:w-48 lg:flex-shrink-0 lg:self-start lg:sticky lg:top-4">
        <button
          onClick={() => handleTabChange('general')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'general'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <SettingsIcon className="w-4 h-4" />
          {t('settings.tabs.general')}
        </button>
        <button
          onClick={() => handleTabChange('plugs')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'plugs'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Plug className="w-4 h-4" />
          {t('settings.tabs.smartPlugs')}
          {smartPlugs && smartPlugs.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {smartPlugs.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('notifications')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'notifications'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Bell className="w-4 h-4" />
          {t('settings.tabs.notifications')}
          {notificationProviders && notificationProviders.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {notificationProviders.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('queue')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'queue'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <ListOrdered className="w-4 h-4" />
          {t('settings.tabs.queue', 'Workflow')}
        </button>
        <button
          onClick={() => handleTabChange('filament')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'filament'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Cylinder className="w-4 h-4" />
          {t('settings.tabs.filament')}
        </button>
        <button
          onClick={() => handleTabChange('network')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'network'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Wifi className="w-4 h-4" />
          {t('settings.tabs.network')}
          <span className={`w-2 h-2 rounded-full ${mqttStatus?.enabled ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('apikeys')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'apikeys'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Key className="w-4 h-4" />
          {t('settings.tabs.apiKeys')}
          {apiKeys && apiKeys.length > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {apiKeys.length}
            </span>
          )}
        </button>
        <button
          onClick={() => handleTabChange('virtual-printer')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'virtual-printer'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Printer className="w-4 h-4" />
          {t('settings.tabs.virtualPrinter')}
          <span className={`w-2 h-2 rounded-full ${virtualPrinterRunning ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('spoolbuddy')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'spoolbuddy'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Scale className="w-4 h-4" />
          {t('settings.tabs.spoolbuddy')}
          {spoolbuddyDeviceCount > 0 && (
            <span className="text-xs bg-bambu-dark-tertiary px-1.5 py-0.5 rounded-full">
              {spoolbuddyDeviceCount}
            </span>
          )}
          <span className={`w-2 h-2 rounded-full ${spoolbuddyAnyOnline ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('failure-detection')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'failure-detection'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <ScanEye className="w-4 h-4" />
          {t('settings.tabs.failureDetection')}
          <span className={`w-2 h-2 rounded-full ${obicoActive ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
        <button
          onClick={() => handleTabChange('users')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'users'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Users className="w-4 h-4" />
          {t('settings.tabs.users')}
          {authEnabled && (
            <span className="w-2 h-2 rounded-full bg-green-400" />
          )}
        </button>
        <button
          onClick={() => handleTabChange('backup')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
            activeTab === 'backup'
              ? 'text-bambu-green border-bambu-green'
              : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
          }`}
        >
          <Database className="w-4 h-4" />
          {t('settings.tabs.backup')}
          <span className={`w-2 h-2 rounded-full ${cloudAuthStatus?.is_authenticated && githubBackupStatus?.configured && githubBackupStatus?.enabled ? 'bg-green-400' : 'bg-gray-500'}`} />
        </button>
      </nav>
      <div className="flex-1 min-w-0">
      {activeTab === 'general' && (
      <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
        {/* Left Column - General Settings */}
        <div className="space-y-3 flex-1 lg:max-w-xl">
          <Card id="card-general">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.general')}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  <Globe className="w-4 h-4 inline mr-1" />
                  {t('settings.language')}
                </label>
                <div className="relative">
                  <select
                    value={i18n.language}
                    onChange={(e) => {
                      const newLang = e.target.value;
                      // Block server persist if the user lacks settings:update —
                      // without this guard the fire-and-forget api.updateSettings
                      // call below would 403 silently while a success toast flashed.
                      if (authEnabled && !hasPermission('settings:update')) {
                        showToast(t('settings.toast.noPermissionUpdate'), 'error');
                        return;
                      }
                      i18n.changeLanguage(newLang);
                      updateMutation.mutate({ language: newLang });
                    }}
                    className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                  >
                    {availableLanguages.map((lang) => (
                      <option key={lang.code} value={lang.code}>
                        {lang.nativeName} ({lang.name})
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.languageDescription')}
                </p>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultView')}
                </label>
                <div className="relative">
                  <select
                    value={defaultView}
                    onChange={(e) => handleDefaultViewChange(e.target.value)}
                    className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                  >
                    {defaultNavItems.map((item) => (
                      <option key={item.id} value={item.to}>
                        {t(item.labelKey)}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.defaultViewDescription')}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('settings.dateFormat')}
                  </label>
                  <div className="relative">
                    <select
                      value={localSettings.date_format || 'system'}
                      onChange={(e) => updateSetting('date_format', e.target.value as 'system' | 'us' | 'eu' | 'iso')}
                      className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                    >
                      <option value="system">{t('settings.systemDefault')}</option>
                      <option value="us">{t('settings.dateFormatUs')}</option>
                      <option value="eu">{t('settings.dateFormatEu')}</option>
                      <option value="iso">{t('settings.dateFormatIso')}</option>
                    </select>
                    <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {t('settings.timeFormat')}
                  </label>
                  <div className="relative">
                    <select
                      value={localSettings.time_format || 'system'}
                      onChange={(e) => updateSetting('time_format', e.target.value as 'system' | '12h' | '24h')}
                      className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                    >
                      <option value="system">{t('settings.systemDefault')}</option>
                      <option value="12h">{t('settings.timeFormat12')}</option>
                      <option value="24h">{t('settings.timeFormat24')}</option>
                    </select>
                    <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                  </div>
                </div>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultPrinter')}
                </label>
                <div className="relative">
                  <select
                    value={localSettings.default_printer_id ?? ''}
                    onChange={(e) => updateSetting('default_printer_id', e.target.value ? Number(e.target.value) : null)}
                    className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                  >
                    <option value="">{t('settings.noDefaultPrinter')}</option>
                    {printers?.map((printer) => (
                      <option key={printer.id} value={printer.id}>
                        {printer.name}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.defaultPrinterDescription')}
                </p>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.sidebarOrder')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.sidebarOrderDescription')}
                    {authEnabled && hasPermission('settings:update') && ` ${t('settings.sidebarOrderSetDefaultHint')}`}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleResetSidebarOrder}
                  >
                    <RotateCcw className="w-4 h-4" />
                    {t('settings.reset')}
                  </Button>
                  {authEnabled && hasPermission('settings:update') && (
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-bambu-gray whitespace-nowrap">{t('settings.setDefault')}</span>
                      <Toggle
                        checked={isDefaultSidebarEnabled}
                        onChange={handleToggleDefaultSidebarOrder}
                        disabled={isLoading}
                      />
                    </div>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card id="card-appearance">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Palette className="w-5 h-5" />
                {t('settings.appearance')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
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
                      onChange={(e) => { setDarkBackground(e.target.value as DarkBackground); showToast(t('settings.toast.settingsSaved'), 'success'); }}
                      className="w-full px-2 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    >
                      <option value="neutral">{t('settings.bgNeutral')}</option>
                      <option value="warm">{t('settings.bgWarm')}</option>
                      <option value="cool">{t('settings.bgCool')}</option>
                      <option value="oled">{t('settings.bgOled')}</option>
                      <option value="slate">{t('settings.bgSlate')}</option>
                      <option value="forest">{t('settings.bgForest')}</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-bambu-gray mb-1">{t('settings.accent')}</label>
                    <select
                      value={darkAccent}
                      onChange={(e) => { setDarkAccent(e.target.value as ThemeAccent); showToast(t('settings.toast.settingsSaved'), 'success'); }}
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
                      onChange={(e) => { setDarkStyle(e.target.value as ThemeStyle); showToast(t('settings.toast.settingsSaved'), 'success'); }}
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
                      onChange={(e) => { setLightBackground(e.target.value as LightBackground); showToast(t('settings.toast.settingsSaved'), 'success'); }}
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
                      onChange={(e) => { setLightAccent(e.target.value as ThemeAccent); showToast(t('settings.toast.settingsSaved'), 'success'); }}
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
                      onChange={(e) => { setLightStyle(e.target.value as ThemeStyle); showToast(t('settings.toast.settingsSaved'), 'success'); }}
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

          <Card id="card-archive">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.archiveSettings')}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.autoArchivePrints')}</p>
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
                      {t('settings.ffmpegRequired')}
                    </p>
                  </div>
                </div>
              )}

              {/* Archive auto-purge (#1008 follow-up). Admin-only — gated on
                  archives:delete_all. Hard-deletes archives older than the
                  configured age threshold once per 24h. */}
              {canPurgeArchives && archivePurgeSettings && (
                <div className="border-t border-bambu-dark-tertiary pt-3 mt-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-white">{t('archiveAutoPurge.enableLabel')}</p>
                      <p className="text-sm text-bambu-gray">{t('archiveAutoPurge.enableDescription')}</p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={archivePurgeSettings.enabled}
                        onChange={(e) => saveArchivePurgeSettings({ enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                    </label>
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('archiveAutoPurge.ageLabel')}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={7}
                        max={3650}
                        disabled={!archivePurgeSettings.enabled}
                        value={archivePurgeSettings.days}
                        onChange={(e) =>
                          saveArchivePurgeSettings({
                            days: Math.max(7, Math.min(3650, parseInt(e.target.value || '0', 10) || 0)),
                          })
                        }
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
                      />
                      <span className="text-bambu-gray">{t('archiveAutoPurge.days')}</span>
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('archiveAutoPurge.ageDescription')}
                    </p>
                  </div>

                </div>
              )}
            </CardContent>
          </Card>

        </div>

        {/* Second Column - Camera, Cost, AMS & Spoolman */}
        <div className="space-y-3 flex-1 lg:max-w-md">
          {/* Camera Settings */}
          <Card id="card-camera">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Video className="w-5 h-5 text-bambu-green" />
                {t('settings.camera')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.cameraViewMode')}
                </label>
                <select
                  value={localSettings.camera_view_mode ?? 'window'}
                  onChange={(e) => updateSetting('camera_view_mode', e.target.value as 'window' | 'embedded')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="window">{t('settings.newWindow')}</option>
                  <option value="embedded">{t('settings.embeddedOverlay')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {localSettings.camera_view_mode === 'embedded'
                    ? t('settings.cameraOverlayDescription')
                    : t('settings.cameraWindowDescription')}
                </p>
              </div>

              {/* External Cameras Section */}
              <div className="border-t border-bambu-dark-tertiary pt-4 mt-4">
                <h3 className="text-sm font-medium text-white mb-2">{t('settings.externalCameras')}</h3>
                <p className="text-xs text-bambu-gray mb-3">
                  {t('settings.externalCamerasDescription')}
                </p>

                {printers && printers.length > 0 ? (
                  <div className="space-y-3">
                    {printers.map(printer => (
                      <div key={printer.id} className="p-3 bg-bambu-dark rounded-lg">
                        <div className="flex items-center justify-between mb-2">
                          <span className="text-white font-medium text-sm">{printer.name}</span>
                          <label className="relative inline-flex items-center cursor-pointer">
                            <input
                              type="checkbox"
                              checked={printer.external_camera_enabled}
                              onChange={(e) => handleUpdatePrinterCamera(printer.id, { enabled: e.target.checked })}
                              className="sr-only peer"
                            />
                            <div className="w-9 h-5 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bambu-green"></div>
                          </label>
                        </div>

                        {printer.external_camera_enabled && (
                          <div className="space-y-2 mt-2">
                            <input
                              type="text"
                              placeholder={printer.external_camera_type === 'usb' ? t('settings.cameraPlaceholderUsb') : t('settings.cameraPlaceholderUrl')}
                              value={localCameraUrls[printer.id] ?? printer.external_camera_url ?? ''}
                              onChange={(e) => handleCameraUrlChange(printer.id, e.target.value)}
                              className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                            />
                            <div className="flex gap-2">
                              <select
                                value={printer.external_camera_type || 'mjpeg'}
                                onChange={(e) => handleUpdatePrinterCamera(printer.id, { type: e.target.value })}
                                className="flex-1 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                              >
                                <option value="mjpeg">{t('settings.cameraTypeMjpeg')}</option>
                                <option value="rtsp">{t('settings.cameraTypeRtsp')}</option>
                                <option value="snapshot">{t('settings.cameraTypeSnapshot')}</option>
                                <option value="usb">{t('settings.cameraTypeUsb')}</option>
                              </select>
                              <Button
                                size="sm"
                                variant="secondary"
                                onClick={() => handleTestExternalCamera(printer.id, localCameraUrls[printer.id] ?? printer.external_camera_url ?? '', printer.external_camera_type || 'mjpeg')}
                                disabled={extCameraTestLoading[printer.id] || !(localCameraUrls[printer.id] ?? printer.external_camera_url)}
                              >
                                {extCameraTestLoading[printer.id] ? (
                                  <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                  t('settings.test')
                                )}
                              </Button>
                            </div>
                            {extCameraTestResults[printer.id] && (
                              <div className={`text-xs flex items-center gap-1 ${extCameraTestResults[printer.id]?.success ? 'text-green-500' : 'text-red-500'}`}>
                                {extCameraTestResults[printer.id]?.success ? (
                                  <>
                                    <CheckCircle className="w-3 h-3" />
                                    {t('settings.connected')}{extCameraTestResults[printer.id]?.resolution && ` (${extCameraTestResults[printer.id]?.resolution})`}
                                  </>
                                ) : (
                                  <>
                                    <XCircle className="w-3 h-3" />
                                    {extCameraTestResults[printer.id]?.error || t('settings.toast.connectionFailed')}
                                  </>
                                )}
                              </div>
                            )}
                            {(printer.external_camera_type === 'mjpeg' || printer.external_camera_type === 'rtsp' || printer.external_camera_type === 'usb') && (
                              <div className="space-y-1">
                                <label className="text-xs text-bambu-gray">{t('settings.cameraSnapshotUrl', 'Snapshot URL (optional)')}</label>
                                <div className="flex gap-2">
                                  <input
                                    type="text"
                                    placeholder={t('settings.cameraSnapshotUrlPlaceholder', 'http://192.168.1.61:1984/api/frame.jpeg?src=printer')}
                                    value={localSnapshotUrls[printer.id] ?? printer.external_camera_snapshot_url ?? ''}
                                    onChange={(e) => handleSnapshotUrlChange(printer.id, e.target.value)}
                                    className="flex-1 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:border-bambu-green focus:outline-none"
                                  />
                                  <Button
                                    size="sm"
                                    variant="secondary"
                                    onClick={() => handleTestExternalCamera(printer.id, localSnapshotUrls[printer.id] ?? printer.external_camera_snapshot_url ?? '', 'snapshot')}
                                    disabled={extCameraTestLoading[printer.id] || !(localSnapshotUrls[printer.id] ?? printer.external_camera_snapshot_url)}
                                  >
                                    {extCameraTestLoading[printer.id] ? (
                                      <Loader2 className="w-4 h-4 animate-spin" />
                                    ) : (
                                      t('settings.test')
                                    )}
                                  </Button>
                                </div>
                                <p className="text-xs text-bambu-gray opacity-75">
                                  {t('settings.cameraSnapshotUrlHelp', 'Single-frame URL used for notification thumbnails, finish photos, timelapse and plate detection. Leave blank to capture from the live stream above. Useful for go2rtc (/api/frame.jpeg) and IP cameras with a dedicated snapshot endpoint.')}
                                </p>
                              </div>
                            )}
                            <div className="flex items-center gap-2">
                              <label className="text-xs text-bambu-gray">{t('settings.cameraRotation')}</label>
                              <select
                                value={printer.camera_rotation || 0}
                                onChange={(e) => handleUpdatePrinterCamera(printer.id, { rotation: parseInt(e.target.value) })}
                                className="px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-xs focus:border-bambu-green focus:outline-none"
                              >
                                <option value={0}>0°</option>
                                <option value={90}>90°</option>
                                <option value={180}>180°</option>
                                <option value={270}>270°</option>
                              </select>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-bambu-gray italic">{t('settings.noPrintersConfigured')}</p>
                )}
              </div>
            </CardContent>
          </Card>


          <Card id="card-cost">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.costTracking')}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">{t('settings.currency')}</label>
                <select
                  value={localSettings.currency}
                  onChange={(e) => updateSetting('currency', e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  {SUPPORTED_CURRENCIES.map((c) => (
                    <option key={c.code} value={c.code}>{c.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.defaultFilamentCost')}
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray text-sm pointer-events-none">
                    {getCurrencySymbol(localSettings.currency)}
                  </span>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={localSettings.default_filament_cost}
                    onChange={(e) =>
                      updateSetting('default_filament_cost', parseFloat(e.target.value) || 0)
                    }
                    style={{ paddingLeft: `${Math.max(2, getCurrencySymbol(localSettings.currency).length * 0.6 + 1)}rem` }}
                    className="w-full pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.electricityCost')}
                </label>
                <div className="relative">
                  <span className="absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray text-sm pointer-events-none">
                    {getCurrencySymbol(localSettings.currency)}
                  </span>
                  <input
                    type="number"
                    step="0.001"
                    min="0"
                    value={localSettings.energy_cost_per_kwh}
                    onChange={(e) =>
                      updateSetting('energy_cost_per_kwh', parseFloat(e.target.value) || 0)
                    }
                    style={{ paddingLeft: `${Math.max(2, getCurrencySymbol(localSettings.currency).length * 0.6 + 1)}rem` }}
                    className="w-full pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
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
                  <option value="print">{t('settings.printsOnly')}</option>
                  <option value="total">{t('settings.totalConsumption')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {localSettings.energy_tracking_mode === 'print'
                    ? t('settings.energyModePrintDescription')
                    : t('settings.energyModeTotalDescription')}
                </p>
              </div>
            </CardContent>
          </Card>

          {/* File Manager Settings */}
          <Card id="card-filemanager">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <FileText className="w-5 h-5 text-bambu-green" />
                {t('settings.fileManager')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Archive Mode */}
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.createArchiveEntry')}
                </label>
                <select
                  value={localSettings.library_archive_mode ?? 'ask'}
                  onChange={(e) => updateSetting('library_archive_mode', e.target.value as 'always' | 'never' | 'ask')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="always">{t('settings.archiveMode.always')}</option>
                  <option value="never">{t('settings.archiveMode.never')}</option>
                  <option value="ask">{t('settings.archiveMode.ask')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.createArchiveEntryDescription')}
                </p>
              </div>

              {/* Disk Space Warning Threshold */}
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.lowDiskSpaceWarning')}
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
                  {t('settings.lowDiskSpaceDescription')}
                </p>
              </div>

              {/* Auto-purge (#1008). Admin-only — users without library:purge
                  don't see this section since they can't trigger a bulk purge
                  even manually. */}
              {canPurge && trashSettings && (
                <div className="border-t border-bambu-dark-tertiary pt-3 mt-3 space-y-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-white">{t('libraryAutoPurge.enableLabel')}</p>
                      <p className="text-sm text-bambu-gray">{t('libraryAutoPurge.enableDescription')}</p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={trashSettings.auto_purge_enabled}
                        onChange={(e) => saveTrashSettings({ auto_purge_enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                    </label>
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('libraryAutoPurge.ageLabel')}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="number"
                        min={7}
                        max={3650}
                        disabled={!trashSettings.auto_purge_enabled}
                        value={trashSettings.auto_purge_days}
                        onChange={(e) =>
                          saveTrashSettings({
                            auto_purge_days: Math.max(7, Math.min(3650, parseInt(e.target.value || '0', 10) || 0)),
                          })
                        }
                        className="w-24 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
                      />
                      <span className="text-bambu-gray">{t('libraryAutoPurge.days')}</span>
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('libraryAutoPurge.ageDescription')}
                    </p>
                  </div>

                  <label className="flex items-center gap-2 text-sm text-white cursor-pointer">
                    <input
                      type="checkbox"
                      disabled={!trashSettings.auto_purge_enabled}
                      checked={trashSettings.auto_purge_include_never_printed}
                      onChange={(e) => saveTrashSettings({ auto_purge_include_never_printed: e.target.checked })}
                      className="rounded border-gray-300 disabled:opacity-50"
                    />
                    {t('libraryAutoPurge.includeNeverPrinted')}
                  </label>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Third Column - Sidebar Links & Updates */}
        <div className="space-y-3 flex-1 lg:max-w-sm">
          {/* Sidebar Links */}
          <ExternalLinksSettings />

          <Card id="card-updates">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.updates')}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider">{t('settings.printerFirmware')}</p>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.checkPrinterFirmware')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.checkFirmwareDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.check_printer_firmware ?? true}
                    onChange={(e) => updateSetting('check_printer_firmware', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              <div className="border-t border-bambu-dark-tertiary pt-4">
                <p className="text-xs font-medium text-bambu-gray uppercase tracking-wider mb-4">{t('settings.bambuddySoftware')}</p>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.checkForUpdatesLabel')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.autoCheckDescription')}
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
              <div className={`flex items-center justify-between ${!localSettings.check_updates ? 'opacity-50' : ''}`}>
                <div>
                  <p className="text-white">{t('settings.includeBetaUpdates')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.includeBetaUpdatesDesc')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.include_beta_updates ?? false}
                    onChange={(e) => updateSetting('include_beta_updates', e.target.checked)}
                    disabled={!localSettings.check_updates}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              <div>
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
                          Update available: v{updateCheck.latest_version}
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
                            title={t('settings.viewReleaseOnGitHub')}
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
                    ) : updateCheck?.is_ha_addon ? (
                      <div className="mt-3 p-3 bg-bambu-dark-tertiary rounded-lg">
                        <p className="text-sm text-bambu-gray">
                          {t('settings.updateViaHomeAssistant')}
                        </p>
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
                    {t('settings.failedToCheckUpdates', { error: updateCheck.error })}
                  </div>
                ) : updateCheck && !updateCheck.update_available ? (
                  <p className="mt-2 text-sm text-bambu-gray">
                    {t('settings.latestVersionRunning')}
                  </p>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {/* Data Management */}
          <Card id="card-data">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white">{t('settings.dataManagement')}</h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.clearNotificationLogs')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.clearNotificationLogsDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setShowClearLogsConfirm(true)}
                >
                  <Trash2 className="w-4 h-4" />
                  {t('common.clear')}
                </Button>
              </div>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.resetUiPreferences')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.resetUiPreferencesDescription')}
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
              <div className="pt-4 border-t border-bambu-dark-tertiary">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white">{t('settings.storageUsage', 'Storage Usage')}</p>
                    <p className="text-sm text-bambu-gray">
                      {t('settings.storageUsageDescription', 'Breakdown of data usage by category')}
                    </p>
                  </div>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleStorageUsageRefresh}
                    disabled={storageUsageFetching || storageUsageRefreshing}
                  >
                    <RefreshCw
                      className={`w-4 h-4 ${storageUsageFetching || storageUsageRefreshing ? 'animate-spin' : ''}`}
                    />
                    {t('common.refresh', 'Refresh')}
                  </Button>
                </div>
                <div className="mt-3">
                  {storageUsageLoading ? (
                    <div className="flex items-center gap-2 text-sm text-bambu-gray">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('common.loading', 'Loading')}
                    </div>
                  ) : storageUsage ? (
                    <>
                      <div className="w-full h-3 bg-bambu-dark rounded-full overflow-hidden flex">
                        {storageUsage.categories
                          .filter((category) => category.bytes > 0)
                          .map((category, index) => (
                            <div
                              key={category.key}
                              className={`${getStorageColor(category.key, index)} h-full`}
                              style={{ width: `${category.percent_of_total}%` }}
                              title={`${category.label}: ${category.formatted}`}
                            />
                          ))}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-3">
                        {storageUsage.categories
                          .filter((category) => category.bytes > 0)
                          .map((category, index) => (
                            <div key={category.key} className="flex items-center gap-2 text-xs">
                              <span
                                className={`w-3 h-3 rounded-full ${getStorageColor(category.key, index)}`}
                              />
                              <span className="text-bambu-gray">{category.label}</span>
                              <span className="text-white">{category.formatted}</span>
                              <span className="text-bambu-gray">({category.percent_of_total.toFixed(1)}%)</span>
                            </div>
                          ))}
                      </div>
                      <div className="mt-2 text-xs text-bambu-gray">
                        {t('settings.storageUsageTotal', 'Total')}: <span className="text-white">{storageUsage.total_formatted}</span>
                        {storageUsage.scan_errors > 0 && (
                          <span className="ml-2 text-amber-400">
                            {t('settings.storageUsageErrors', 'Scan errors')}: {storageUsage.scan_errors}
                          </span>
                        )}
                      </div>
                      {storageUsage.other_breakdown?.length > 0 && (
                        <div className="mt-4">
                          <p className="text-xs text-bambu-gray mb-2">
                            {t('settings.storageUsageOtherBreakdown', 'Other breakdown')}
                          </p>
                          <div className="space-y-2">
                            {storageUsage.other_breakdown.map((item) => (
                              <div key={`${item.bucket}-${item.kind}`} className="flex items-center justify-between text-xs">
                                <div className="flex items-center gap-2">
                                  <span className="text-white">{item.label}</span>
                                  <span
                                    className={`px-2 py-0.5 rounded-full border ${
                                      item.kind === 'system'
                                        ? 'border-slate-500 text-slate-300'
                                        : 'border-bambu-green text-bambu-green'
                                    }`}
                                  >
                                    {item.kind === 'system'
                                      ? t('settings.storageUsageSystem', 'System')
                                      : t('settings.storageUsageData', 'Data')}
                                  </span>
                                </div>
                                <div className="flex items-center gap-2 text-bambu-gray">
                                  <span className="text-white">{item.formatted}</span>
                                  <span>({item.percent_of_total.toFixed(1)}%)</span>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-bambu-gray">
                      {t('settings.storageUsageUnavailable', 'Storage usage data is unavailable')}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center justify-between pt-4 border-t border-bambu-dark-tertiary">
                <div>
                  <p className="text-white">{t('settings.backupRestore')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.backupRestoreDescription')}
                  </p>
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => handleTabChange('backup')}
                >
                  <Database className="w-4 h-4" />
                  {t('settings.goToBackup')}
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
        <div className="flex-1 lg:max-w-xl space-y-3">
          {/* External URL */}
          <Card id="card-externalurl">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <Globe className="w-5 h-5 text-blue-400" />
                {t('settings.externalUrl')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
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
                  placeholder="http://192.168.1.100:8000"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.externalUrlHint')}
                </p>
              </div>
            </CardContent>
          </Card>

          <Card id="card-ftpretry">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <RefreshCw className="w-5 h-5 text-blue-400" />
                {t('settings.ftpRetry')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-bambu-gray">
                {t('settings.ftpRetryDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableRetry')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.autoRetryDescription')}
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
                <div className="space-y-3 pt-2 border-t border-bambu-dark-tertiary">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.retryAttempts')}
                    </label>
                    <div className="relative w-44">
                      <select
                        value={localSettings.ftp_retry_count ?? 3}
                        onChange={(e) => updateSetting('ftp_retry_count', parseInt(e.target.value))}
                        className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                      >
                        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(n => (
                          <option key={n} value={n}>{t('settings.time', { count: n })}</option>
                        ))}
                      </select>
                      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.retryDelay')}
                    </label>
                    <div className="relative w-44">
                      <select
                        value={localSettings.ftp_retry_delay ?? 2}
                        onChange={(e) => updateSetting('ftp_retry_delay', parseInt(e.target.value))}
                        className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                      >
                        {[1, 2, 3, 5, 10, 15, 20, 30].map(n => (
                          <option key={n} value={n}>{t('settings.second', { count: n })}</option>
                        ))}
                      </select>
                      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                    </div>
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.connectionTimeout')}
                    </label>
                    <div className="relative w-44">
                      <select
                        value={localSettings.ftp_timeout ?? 30}
                        onChange={(e) => updateSetting('ftp_timeout', parseInt(e.target.value))}
                        className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                      >
                        {[10, 15, 20, 30, 45, 60, 90, 120, 180, 300].map(n => (
                          <option key={n} value={n}>{t('settings.nSeconds', { count: n })}</option>
                        ))}
                      </select>
                      <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                    </div>
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.increaseForWeakWifi')}
                    </p>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

        </div>

        {/* Right Column - Home Assistant & MQTT Publishing */}
        <div className="flex-1 lg:max-w-xl space-y-3">
          {/* Home Assistant Integration */}
          <Card id="card-ha">
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
            <CardContent className="space-y-3">
              <p className="text-sm text-bambu-gray">
                {t('settings.homeAssistantFullDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div className="flex-1">
                  <p className="text-white">{t('settings.enableHomeAssistant')}</p>
                  <p className="text-xs text-bambu-gray">{t('settings.homeAssistantDescription')}</p>
                  {localSettings.ha_env_managed && (
                    <div className="flex items-center gap-1 mt-1">
                      <Lock className="w-3 h-3 text-bambu-green" />
                      <span className="text-xs text-bambu-green">
                        {t('settings.autoEnabledViaEnv')}
                      </span>
                    </div>
                  )}
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.ha_enabled ?? false}
                    onChange={(e) => updateSetting('ha_enabled', e.target.checked)}
                    disabled={localSettings.ha_env_managed}
                    className="sr-only peer"
                  />
                  <div className={`w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green ${
                    localSettings.ha_env_managed ? 'opacity-60 cursor-not-allowed' : ''
                  }`}></div>
                </label>
              </div>

              {localSettings.ha_enabled && (
                <>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.homeAssistantUrl')}
                      {localSettings.ha_url_from_env && (
                        <span className="ml-2 text-xs text-bambu-green">
                          {t('settings.environmentManagedLabel')}
                        </span>
                      )}
                    </label>
                    <div className="relative">
                      <input
                        type="text"
                        value={localSettings.ha_url ?? ''}
                        onChange={(e) => updateSetting('ha_url', e.target.value)}
                        placeholder="http://192.168.1.100:8123"
                        disabled={localSettings.ha_url_from_env}
                        className={`w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none ${
                          localSettings.ha_url_from_env ? 'opacity-60 cursor-not-allowed' : ''
                        }`}
                      />
                      {localSettings.ha_url_from_env && (
                        <Lock className="absolute right-3 top-2.5 w-4 h-4 text-bambu-gray" />
                      )}
                    </div>
                    {localSettings.ha_url_from_env && (
                      <p className="text-xs text-bambu-gray mt-1">
                        {t('settings.urlFromEnvReadOnly')}
                      </p>
                    )}
                  </div>

                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.longLivedAccessToken')}
                      {localSettings.ha_token_from_env && (
                        <span className="ml-2 text-xs text-bambu-green">
                          {t('settings.environmentManagedLabel')}
                        </span>
                      )}
                    </label>
                    <div className="relative">
                      <input
                        type="password"
                        value={localSettings.ha_token ?? ''}
                        onChange={(e) => updateSetting('ha_token', e.target.value)}
                        placeholder="eyJ0eXAiOiJKV1QiLC..."
                        disabled={localSettings.ha_token_from_env}
                        className={`w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none ${
                          localSettings.ha_token_from_env ? 'opacity-60 cursor-not-allowed' : ''
                        }`}
                      />
                      {localSettings.ha_token_from_env && (
                        <Lock className="absolute right-3 top-2.5 w-4 h-4 text-bambu-gray" />
                      )}
                    </div>
                    {localSettings.ha_token_from_env ? (
                      <p className="text-xs text-bambu-gray mt-1">
                        {t('settings.tokenFromEnvReadOnly')}
                      </p>
                    ) : (
                      <p className="text-xs text-bambu-gray mt-1">
                        {t('settings.haTokenHint')}
                      </p>
                    )}
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
                            setHaTestResult({ success: false, message: null, error: e instanceof Error ? e.message : t('common.unknownError') });
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
          <Card id="card-mqtt">
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
            <CardContent className="space-y-3">
              <p className="text-sm text-bambu-gray">
                {t('settings.mqttDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableMqtt')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.mqttEnableDescription')}
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
                <div className="space-y-3 pt-2 border-t border-bambu-dark-tertiary">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.brokerHostname')}
                    </label>
                    <input
                      type="text"
                      value={localSettings.mqtt_broker ?? ''}
                      onChange={(e) => updateSetting('mqtt_broker', e.target.value)}
                      placeholder="mqtt.example.com or 192.168.1.100"
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
                      placeholder={t('settings.leaveEmptyForAnonymous')}
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
                      placeholder={t('settings.leaveEmptyForAnonymous')}
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
                      placeholder="bambuddy"
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.topicPrefixHint', { prefix: localSettings.mqtt_topic_prefix || 'bambuddy' })}
                    </p>
                  </div>

                  {/* Connection Info */}
                  {mqttStatus && (
                    <div className="pt-3 mt-3 border-t border-bambu-dark-tertiary">
                      <div className="flex items-center gap-2 text-sm">
                        <span className={`w-2 h-2 rounded-full ${mqttStatus.connected ? 'bg-green-400' : 'bg-red-400'}`} />
                        <span className="text-bambu-gray">
                          {mqttStatus.connected ? (
                            <>{t('settings.mqttConnectedTo')} <span className="text-white">{mqttStatus.broker}:{mqttStatus.port}</span></>
                          ) : (
                            t('settings.spoolmanDisconnected')
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

        {/* Third Column - Prometheus Metrics */}
        <div className="flex-1 lg:max-w-md space-y-3">
          <Card id="card-prometheus">
            <CardHeader>
              <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                <TrendingUp className="w-5 h-5 text-orange-400" />
                {t('settings.prometheusMetrics')}
              </h2>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-bambu-gray">
                {t('settings.prometheusEndpointDescription')}
              </p>

              <div className="flex items-center justify-between">
                <div>
                  <p className="text-white">{t('settings.enableMetricsEndpoint')}</p>
                  <p className="text-xs text-bambu-gray">{t('settings.prometheusDescription')}</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.prometheus_enabled ?? false}
                    onChange={(e) => updateSetting('prometheus_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {localSettings.prometheus_enabled && (
                <div className="space-y-3 pt-2 border-t border-bambu-dark-tertiary">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">
                      {t('settings.bearerTokenOptional')}
                    </label>
                    <input
                      type="password"
                      value={localSettings.prometheus_token ?? ''}
                      onChange={(e) => updateSetting('prometheus_token', e.target.value)}
                      placeholder={t('settings.leaveEmptyForNoAuth')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                    <p className="text-xs text-bambu-gray mt-1">
                      {t('settings.bearerTokenHint')}
                    </p>
                  </div>

                  <div className="pt-2 border-t border-bambu-dark-tertiary">
                    <p className="text-sm text-white mb-2">{t('settings.availableMetrics')}</p>
                    <div className="text-xs text-bambu-gray space-y-1">
                      <p><code className="text-orange-400">bambuddy_printer_connected</code> - {t('settings.metricsConnectionStatus')}</p>
                      <p><code className="text-orange-400">bambuddy_printer_state</code> - {t('settings.metricsPrinterState')}</p>
                      <p><code className="text-orange-400">bambuddy_print_progress</code> - {t('settings.metricsPrintProgress')}</p>
                      <p><code className="text-orange-400">bambuddy_bed_temp_celsius</code> - {t('settings.metricsBedTemp')}</p>
                      <p><code className="text-orange-400">bambuddy_nozzle_temp_celsius</code> - {t('settings.metricsNozzleTemp')}</p>
                      <p><code className="text-orange-400">bambuddy_prints_total</code> - {t('settings.metricsPrintsTotal')}</p>
                      <p className="text-bambu-gray/70 italic">{t('settings.metricsMore')}</p>
                    </div>
                  </div>
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
                {t('settings.ok')}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Smart Plugs Tab */}
      {activeTab === 'plugs' && (
        <div id="card-plugs">
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
                        {t('settings.plugsOnline', { reachable: plugEnergySummary.reachableCount, total: plugEnergySummary.totalPlugs })}
                      </div>
                    </div>

                    {/* Today */}
                    <div className="bg-bambu-dark rounded-lg p-3">
                      <div className="flex items-center gap-2 text-bambu-gray text-xs mb-1">
                        <Calendar className="w-3 h-3" />
                        {t('settings.today')}
                      </div>
                      <div className="text-xl font-bold text-white">
                        {plugEnergySummary.totalToday.toFixed(3)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">kWh</span>
                      </div>
                      {(localSettings?.energy_cost_per_kwh ?? 0) > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalToday * (localSettings?.energy_cost_per_kwh ?? 0)).toFixed(2)} {getCurrencySymbol(localSettings?.currency || 'USD')}
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
                        {plugEnergySummary.totalYesterday.toFixed(3)}
                        <span className="text-sm font-normal text-bambu-gray ml-1">kWh</span>
                      </div>
                      {(localSettings?.energy_cost_per_kwh ?? 0) > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalYesterday * (localSettings?.energy_cost_per_kwh ?? 0)).toFixed(2)} {getCurrencySymbol(localSettings?.currency || 'USD')}
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
                      {(localSettings?.energy_cost_per_kwh ?? 0) > 0 && (
                        <div className="text-xs text-bambu-gray mt-1">
                          ~{(plugEnergySummary.totalLifetime * (localSettings?.energy_cost_per_kwh ?? 0)).toFixed(2)} {getCurrencySymbol(localSettings?.currency || 'USD')}
                        </div>
                      )}
                    </div>
                  </div>
                ) : !energyLoading ? (
                  <p className="text-sm text-bambu-gray">
                    {t('settings.enablePlugsForSummary')}
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
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
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
                  <p className="text-lg font-medium text-white mb-2">{t('settings.noSmartPlugsTitle')}</p>
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
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Left Column: Providers */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2" id="card-providers">
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
                  {t('settings.addNotificationProvider')}
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

            {/* Bed Cooled Threshold Setting */}
            <Card className="mb-4">
              <CardContent className="py-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white text-sm font-medium">{t('settings.bedCooledThreshold')}</p>
                    <p className="text-xs text-bambu-gray">{t('settings.bedCooledThresholdDescription')}</p>
                  </div>
                  <div className="flex items-center gap-1">
                    <input
                      type="number"
                      min={20}
                      max={80}
                      step={1}
                      value={localSettings.bed_cooled_threshold ?? 35}
                      onChange={(e) => updateSetting('bed_cooled_threshold', Number(e.target.value))}
                      className="w-16 px-2 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm text-center focus:outline-none focus:ring-1 focus:ring-bambu-green"
                    />
                    <span className="text-sm text-bambu-gray">°C</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* User Notifications Toggle */}
            <Card className="mb-4">
              <CardContent className="py-3">
                <div className={`flex items-center justify-between ${!advancedAuthStatus?.advanced_auth_enabled ? 'opacity-50' : ''}`}>
                  <div>
                    <p className="text-white text-sm font-medium">{t('settings.userNotificationsEnabled')}</p>
                    <p className="text-xs text-bambu-gray">
                      {!advancedAuthStatus?.advanced_auth_enabled
                        ? t('settings.userNotificationsDisabledHint')
                        : t('settings.userNotificationsEnabledDescription')}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={localSettings.user_notifications_enabled ?? true}
                      disabled={!advancedAuthStatus?.advanced_auth_enabled}
                      onChange={(e) => updateSetting('user_notifications_enabled', e.target.checked)}
                    />
                    <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green peer-disabled:cursor-not-allowed"></div>
                  </label>
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
                      {t('common.dismiss')}
                    </button>
                  </div>
                  <div className="flex items-center gap-4 text-sm mb-2">
                    <span className="flex items-center gap-1 text-bambu-green">
                      <CheckCircle className="w-4 h-4" />
                      {t('settings.testPassedCount', { count: testAllResult.success })}
                    </span>
                    {testAllResult.failed > 0 && (
                      <span className="flex items-center gap-1 text-red-400">
                        <XCircle className="w-4 h-4" />
                        {t('settings.testFailedCount', { count: testAllResult.failed })}
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
                    <p className="text-sm font-medium text-white mb-2">{t('settings.noProvidersTitle')}</p>
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
            <h2 className="text-lg font-semibold text-white flex items-center gap-2 mb-2" id="card-templates">
              <FileText className="w-5 h-5 text-bambu-green" />
              {t('settings.messageTemplates')}
            </h2>
            <p className="text-sm text-bambu-gray mb-3">
              {t('settings.messageTemplatesDescription')}
            </p>

            {/* Filter input */}
            <div className="relative mb-3">
              <Search className="w-4 h-4 text-bambu-gray absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={templateFilter}
                onChange={(e) => setTemplateFilter(e.target.value)}
                placeholder={t('settings.filterTemplates', 'Filter templates…')}
                className="w-full pl-9 pr-8 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
              />
              {templateFilter && (
                <button
                  onClick={() => setTemplateFilter('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-bambu-gray hover:text-white"
                  aria-label="Clear filter"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            {templatesLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : notificationTemplates && notificationTemplates.length > 0 ? (
              (() => {
                const filter = templateFilter.trim().toLowerCase();
                const filtered = [...notificationTemplates]
                  .sort((a, b) => a.name.localeCompare(b.name))
                  .filter(tpl =>
                    !filter ||
                    tpl.name.toLowerCase().includes(filter) ||
                    (tpl.title_template || '').toLowerCase().includes(filter)
                  );
                if (filtered.length === 0) {
                  return (
                    <p className="text-sm text-bambu-gray italic text-center py-6">
                      {t('settings.noTemplatesMatch', 'No templates match your filter.')}
                    </p>
                  );
                }
                return (
              <div className="space-y-2">
                {filtered.map((template) => (
                  <Card
                    key={template.id}
                    className="cursor-pointer hover:border-bambu-green/50 transition-colors"
                    onClick={() => setEditingTemplate(template)}
                  >
                    <CardContent className="py-2.5 px-3">
                      <div className="flex items-center justify-between">
                        <div className="min-w-0 flex-1">
                          <p className="text-white font-medium text-sm truncate">{template.name}</p>
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
                );
              })()
            ) : (
              <Card>
                <CardContent className="py-8">
                  <div className="text-center text-bambu-gray">
                    <FileText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p className="text-sm">{t('settings.noTemplatesAvailable')}</p>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      )}

      {/* API Keys Tab */}
      {activeTab === 'apikeys' && (
        <div className={hasPermission('api_keys:read')
          ? 'grid grid-cols-1 xl:grid-cols-2 gap-4'
          : 'grid grid-cols-1 gap-4'}>
          {/* Left Column - API Keys Management. Admin-gated content
              (webhook keys, webhook docs) is hidden from users without
              api_keys:read; the Camera Tokens panel is always shown so
              users with camera:view can self-manage their own tokens. */}
          <div>
            {hasPermission('api_keys:read') && <>
            <div className="flex items-start justify-between gap-4 mb-6">
              <div className="flex-1">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2" id="card-createapi">
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
                      <p className="text-white font-medium mb-1">{t('settings.apiKeyCreated')}</p>
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
                              showToast(t('settings.toast.keyCopied'));
                            } catch {
                              showToast(t('settings.toast.copyFailed'), 'error');
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
                            showToast(t('settings.toast.keyAddedToBrowser'));
                          }}
                        >
                          {t('settings.useInApiBrowser')}
                        </Button>
                        <Button
                          variant="secondary"
                          size="sm"
                          onClick={() => setCreatedAPIKey(null)}
                        >
                          {t('common.dismiss')}
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
                <CardContent className="space-y-3">
                  <div>
                    <label className="block text-sm text-bambu-gray mb-1">{t('settings.keyName')}</label>
                    <input
                      type="text"
                      value={newAPIKeyName}
                      onChange={(e) => setNewAPIKeyName(e.target.value)}
                      placeholder={t('settings.keyNamePlaceholder')}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-sm text-bambu-gray mb-2">{t('common.permissions')}</label>
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
                      <label className="flex items-center gap-3 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={newAPIKeyPermissions.can_access_cloud}
                          onChange={(e) => setNewAPIKeyPermissions(prev => ({ ...prev, can_access_cloud: e.target.checked }))}
                          className="w-4 h-4 text-bambu-green rounded border-bambu-dark-tertiary bg-bambu-dark focus:ring-bambu-green"
                        />
                        <div>
                          <span className="text-white">{t('settings.cloudAccess', 'Allow cloud access')}</span>
                          <p className="text-xs text-bambu-gray">{t('settings.cloudAccessDescription', 'Read Bambu Cloud presets and filaments on your behalf. Requires you to be signed into Bambu Cloud.')}</p>
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
                      {t('common.cancel')}
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
                              {key.last_used && ` · ${t('settings.lastUsed')}: ${formatDateOnly(key.last_used)}`}
                            </p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="flex gap-1 text-xs flex-wrap justify-end">
                            {key.can_read_status && (
                              <span className="px-1.5 py-0.5 bg-blue-500/20 text-blue-400 rounded">{t('settings.read')}</span>
                            )}
                            {key.can_queue && (
                              <span className="px-1.5 py-0.5 bg-green-500/20 text-green-400 rounded">{t('queue.title')}</span>
                            )}
                            {key.can_control_printer && (
                              <span className="px-1.5 py-0.5 bg-orange-500/20 text-orange-400 rounded">{t('settings.control')}</span>
                            )}
                            {key.can_access_cloud && (
                              <span className="px-1.5 py-0.5 bg-purple-500/20 text-purple-400 rounded">{t('settings.cloudBadge', 'Cloud')}</span>
                            )}
                            {key.user_id === null && (
                              <span
                                className="px-1.5 py-0.5 bg-yellow-500/20 text-yellow-400 rounded"
                                title={t('settings.legacyKeyTooltip', 'Created before per-user ownership; recreate to use cloud access')}
                              >
                                {t('settings.legacyKey', 'Legacy')}
                              </span>
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
                    <p className="text-lg font-medium text-white mb-2">{t('settings.apiKeysEmptyTitle')}</p>
                    <p className="text-sm mb-4">{t('settings.apiKeysEmptyDescription')}</p>
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
                <h3 className="text-base font-semibold text-white" id="card-webhooks">{t('settings.webhookEndpoints')}</h3>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <p className="text-bambu-gray">
                  {t('settings.webhookApiKeyHint')}
                </p>
                <div className="space-y-2 font-mono text-xs">
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-blue-400">GET</span>{' '}
                    <span className="text-white">/api/v1/webhook/status</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.getAllStatus')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-blue-400">GET</span>{' '}
                    <span className="text-white">/api/v1/webhook/status/:id</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.getSpecificStatus')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-green-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/queue</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.addToQueue')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-orange-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/pause</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.pausePrint')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-orange-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/resume</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.resumePrint')}</span>
                  </div>
                  <div className="p-2 bg-bambu-dark rounded">
                    <span className="text-red-400">POST</span>{' '}
                    <span className="text-white">/api/v1/webhook/printer/:id/stop</span>
                    <span className="text-bambu-gray"> - {t('settings.webhook.stopPrint')}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
            </>}

            {/* Long-lived camera-stream tokens (#1108) */}
            <Card className="mt-6">
              <CardHeader>
                <h3 className="text-base font-semibold text-white flex items-center gap-2" id="card-camera-tokens">
                  <Video className="w-4 h-4 text-bambu-green" />
                  {t('cameraTokens.title', 'Camera API Tokens')}
                </h3>
              </CardHeader>
              <CardContent>
                <CameraTokensSection />
              </CardContent>
            </Card>
          </div>

          {/* Right Column - API Browser. Hidden from users without
              api_keys:read since the API Browser is the testing surface
              for those keys; non-admins land in this tab only for the
              Camera Tokens panel and don't need the browser. */}
          {hasPermission('api_keys:read') && <div>
            <div className="mb-6">
              <h2 className="text-lg font-semibold text-white flex items-center gap-2" id="card-apibrowser">
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
                  placeholder={t('settings.apiKeyPlaceholder')}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white font-mono text-sm focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-2">
                  {t('settings.apiKeyHint')}
                </p>
              </CardContent>
            </Card>

            <APIBrowser apiKey={testApiKey} />
          </div>}
        </div>
      )}

      {/* Virtual Printer Tab */}
      {activeTab === 'virtual-printer' && (
        <div id="card-vp">
          <VirtualPrinterList />
        </div>
      )}

      {/* SpoolBuddy Tab */}
      {activeTab === 'spoolbuddy' && (
        <div id="card-spoolbuddy">
          <SpoolBuddySettings />
        </div>
      )}

      {/* Filament Tab */}
      {/* Queue Tab */}
      {activeTab === 'queue' && localSettings && (
        <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
          {/* Left Column */}
          <div className="lg:w-1/2 space-y-3">
          {/* Default Print Options */}
          <Card id="card-print-options">
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <ListOrdered className="w-4 h-4 text-bambu-green" />
                {t('settings.defaultPrintOptions', 'Default Print Options')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-bambu-gray">
                {t('settings.defaultPrintOptionsDescription', 'Set default values for print options when starting new prints. These can be overridden per print in the print dialog.')}
              </p>
              {[
                { key: 'default_bed_levelling' as const, label: t('settings.defaultBedLevelling', 'Bed Levelling'), desc: t('settings.defaultBedLevellingDesc', 'Auto-level bed before print'), fallback: true },
                { key: 'default_flow_cali' as const, label: t('settings.defaultFlowCali', 'Flow Calibration'), desc: t('settings.defaultFlowCaliDesc', 'Calibrate extrusion flow'), fallback: false },
                { key: 'default_vibration_cali' as const, label: t('settings.defaultVibrationCali', 'Vibration Calibration'), desc: t('settings.defaultVibrationCaliDesc', 'Reduce ringing artifacts'), fallback: true },
                { key: 'default_layer_inspect' as const, label: t('settings.defaultLayerInspect', 'First Layer Inspection'), desc: t('settings.defaultLayerInspectDesc', 'AI inspection of first layer'), fallback: false },
                { key: 'default_timelapse' as const, label: t('settings.defaultTimelapse', 'Timelapse'), desc: t('settings.defaultTimelapseDesc', 'Record timelapse video'), fallback: false },
              ].map(({ key, label, desc, fallback }) => (
                <div key={key} className="flex items-center justify-between">
                  <div className="flex-1 mr-4">
                    <p className="text-sm text-white">{label}</p>
                    <p className="text-xs text-bambu-gray mt-0.5">{desc}</p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={localSettings[key] ?? fallback}
                      onChange={(e) => updateSetting(key, e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                  </label>
                </div>
              ))}
            </CardContent>
          </Card>

          {/* Staggered Batch Start */}
          <Card id="card-staggered">
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <Layers className="w-4 h-4 text-bambu-green" />
                {t('settings.staggeredStart', 'Staggered Start')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-bambu-gray">
                {t('settings.staggeredStartDescription', 'Default group size and interval when staggering multi-printer batch starts. Can be overridden per batch in the print modal.')}
              </p>
              <div className="flex gap-4">
                <div className="flex-1">
                  <label className="block text-xs text-bambu-gray mb-1">
                    {t('settings.staggerGroupSize', 'Group size')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={localSettings.stagger_group_size ?? 2}
                    onChange={(e) => updateSetting('stagger_group_size', Math.max(1, Math.min(50, parseInt(e.target.value) || 1)))}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('settings.staggerGroupSizeHelp', 'Printers to start simultaneously per group')}
                  </p>
                </div>
                <div className="flex-1">
                  <label className="block text-xs text-bambu-gray mb-1">
                    {t('settings.staggerInterval', 'Interval (minutes)')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={localSettings.stagger_interval_minutes ?? 5}
                    onChange={(e) => updateSetting('stagger_interval_minutes', Math.max(1, Math.min(60, parseInt(e.target.value) || 1)))}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('settings.staggerIntervalHelp', 'Delay between each group starting')}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Plate-Clear Confirmation */}
          <Card id="card-plate">
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <Shield className="w-4 h-4 text-bambu-green" />
                {t('settings.plateClear', 'Plate-Clear Confirmation')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex-1 mr-4">
                  <p className="text-sm text-white">
                    {t('settings.requirePlateClear', 'Require plate-clear confirmation')}
                  </p>
                  <p className="text-xs text-bambu-gray mt-1">
                    {t('settings.requirePlateClearDescription', 'When enabled, the scheduler waits for per-printer plate-clear confirmation before starting queued prints on printers with finished jobs. Disabling this also hides the plate status badge and the "Mark plate as cleared" button on printer cards.')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.require_plate_clear ?? false}
                    onChange={(e) => updateSetting('require_plate_clear', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
            </CardContent>
          </Card>

          {/* G-code Injection (#422) */}
          <Card id="card-gcode">
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <Code className="w-4 h-4 text-bambu-green" />
                {t('settings.gcodeInjection', 'G-code Injection')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-bambu-gray">
                {t('settings.gcodeInjectionDescription', 'Configure custom G-code to inject at the start and/or end of prints for auto-print systems like Farmloop, SwapMod, AutoClear, and Printflow 3D. Snippets are configured per printer model and applied when "Inject G-code" is enabled on a queue item.')}
              </p>
              {(() => {
                const gcodeSnippets: Record<string, { start_gcode: string; end_gcode: string }> = (() => {
                  try {
                    return localSettings.gcode_snippets ? JSON.parse(localSettings.gcode_snippets) : {};
                  } catch {
                    return {};
                  }
                })();
                const printerModels = [...new Set((printers || []).filter((p) => p.model).map((p) => p.model as string))].sort();

                const updateSnippet = (model: string, field: 'start_gcode' | 'end_gcode', value: string) => {
                  const updated = { ...gcodeSnippets };
                  if (!updated[model]) {
                    updated[model] = { start_gcode: '', end_gcode: '' };
                  }
                  updated[model][field] = value;
                  // Remove model entry if both fields are empty
                  if (!updated[model].start_gcode && !updated[model].end_gcode) {
                    delete updated[model];
                  }
                  const newValue = Object.keys(updated).length > 0 ? JSON.stringify(updated) : '';
                  // Update local state for immediate UI feedback, save on blur
                  setLocalSettings(prev => prev ? { ...prev, gcode_snippets: newValue } : null);
                  pendingGcodeSnippetsRef.current = newValue;
                };

                const saveGcodeSnippets = () => {
                  if (pendingGcodeSnippetsRef.current !== null) {
                    updateMutation.mutate({ gcode_snippets: pendingGcodeSnippetsRef.current });
                    pendingGcodeSnippetsRef.current = null;
                  }
                };

                if (printerModels.length === 0) {
                  return (
                    <p className="text-sm text-bambu-gray italic">
                      {t('settings.gcodeInjectionNoPrinters', 'No printers found. Add printers to configure G-code snippets.')}
                    </p>
                  );
                }

                return printerModels.map((model) => {
                  const snippet = gcodeSnippets[model] || { start_gcode: '', end_gcode: '' };
                  const hasContent = !!(snippet.start_gcode || snippet.end_gcode);
                  return (
                    <Collapsible
                      key={model}
                      defaultOpen={hasContent}
                      className="border border-bambu-dark-tertiary rounded-lg px-3 py-2"
                      summary={
                        <div className="flex items-center gap-2">
                          <h4 className="text-sm font-medium text-white">{model}</h4>
                          {hasContent && (
                            <span className="text-xs px-1.5 py-0.5 rounded bg-bambu-green/20 text-bambu-green">
                              {t('settings.gcodeConfigured', 'Configured')}
                            </span>
                          )}
                        </div>
                      }
                    >
                      <div className="space-y-2">
                        <div>
                          <label className="block text-xs text-bambu-gray mb-1">
                            {t('settings.gcodeStartLabel', 'Start G-code')}
                          </label>
                          <textarea
                            value={snippet.start_gcode}
                            onChange={(e) => updateSnippet(model, 'start_gcode', e.target.value)}
                            onBlur={saveGcodeSnippets}
                            placeholder={t('settings.gcodeStartPlaceholder', 'G-code prepended before the print starts...')}
                            rows={3}
                            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-xs font-mono focus:outline-none focus:border-bambu-green resize-y"
                          />
                        </div>
                        <div>
                          <label className="block text-xs text-bambu-gray mb-1">
                            {t('settings.gcodeEndLabel', 'End G-code')}
                          </label>
                          <textarea
                            value={snippet.end_gcode}
                            onChange={(e) => updateSnippet(model, 'end_gcode', e.target.value)}
                            onBlur={saveGcodeSnippets}
                            placeholder={t('settings.gcodeEndPlaceholder', 'G-code appended after the print ends...')}
                            rows={3}
                            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-xs font-mono focus:outline-none focus:border-bambu-green resize-y"
                          />
                        </div>
                      </div>
                    </Collapsible>
                  );
                });
              })()}
            </CardContent>
          </Card>

          </div>
          {/* Right Column */}
          <div className="lg:w-1/2 space-y-3">
          {/* Slicer */}
          <Card id="card-slicer">
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2">
                <Cog className="w-4 h-4 text-bambu-green" />
                {t('settings.slicerCard', 'Slicer')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('settings.preferredSlicer')}
                </label>
                <div className="relative">
                  <select
                    value={localSettings.preferred_slicer ?? 'bambu_studio'}
                    onChange={(e) => updateSetting('preferred_slicer', e.target.value as 'bambu_studio' | 'orcaslicer')}
                    className="w-full px-3 py-2 pr-10 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none appearance-none cursor-pointer"
                  >
                    <option value="bambu_studio">{t('settings.slicerBambuStudio')}</option>
                    <option value="orcaslicer">{t('settings.slicerOrcaSlicer')}</option>
                  </select>
                  <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
                </div>
                <p className="text-xs text-bambu-gray mt-1">
                  {t('settings.preferredSlicerDescription')}
                </p>
                {/* Upstream OrcaSlicer 2.3.2 / 2.4.0-dev have two known
                    CLI bugs that block slicing many Bambu-authored 3MFs:
                    a SIGSEGV on painted multi-extruder 3MFs (#12426) and
                    a strict range-check on sentinel parameter values
                    BambuStudio writes by default. Until the upstream
                    fixes land, surface a clear warning when a user has
                    OrcaSlicer selected so they know what to expect; we
                    don't auto-switch them in case they're testing. */}
                {(localSettings.preferred_slicer ?? 'bambu_studio') === 'orcaslicer' && (
                  <div
                    role="alert"
                    className="text-xs text-amber-200 bg-amber-900/20 border border-amber-700/40 rounded p-2 mt-2"
                  >
                    {t(
                      'settings.orcaslicerKnownIssuesWarning',
                      'OrcaSlicer 2.3.2 / 2.4.0-dev have known CLI bugs that block slicing many Bambu-authored 3MFs — see upstream issues #12426 (segfault on painted multi-extruder files) and #13386 (parameter-range strict-validation reject). Bambu Studio is recommended until the upstream fixes land.',
                    )}
                  </div>
                )}
              </div>
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-white">{t('settings.useSlicerApi')}</p>
                  <p className="text-sm text-bambu-gray">
                    {t('settings.useSlicerApiDescription')}
                  </p>
                </div>
                <label className="flex items-center cursor-pointer shrink-0">
                  <input
                    type="checkbox"
                    checked={localSettings.use_slicer_api ?? false}
                    onChange={(e) => updateSetting('use_slicer_api', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="relative w-11 h-6 bg-bambu-dark-tertiary rounded-full peer peer-checked:bg-bambu-green peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-bambu-green/50 transition-colors after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-transform peer-checked:after:translate-x-5"></div>
                </label>
              </div>
              {(localSettings.use_slicer_api ?? false) && (
                <div>
                  <label className="block text-sm text-bambu-gray mb-1">
                    {(localSettings.preferred_slicer ?? 'bambu_studio') === 'orcaslicer'
                      ? t('settings.orcaslicerApiUrl', 'OrcaSlicer sidecar URL')
                      : t('settings.bambuStudioApiUrl', 'Bambu Studio sidecar URL')}
                  </label>
                  <input
                    type="text"
                    value={
                      ((localSettings.preferred_slicer ?? 'bambu_studio') === 'orcaslicer'
                        ? localSettings.orcaslicer_api_url
                        : localSettings.bambu_studio_api_url) ?? ''
                    }
                    onChange={(e) =>
                      updateSetting(
                        (localSettings.preferred_slicer ?? 'bambu_studio') === 'orcaslicer'
                          ? 'orcaslicer_api_url'
                          : 'bambu_studio_api_url',
                        e.target.value,
                      )
                    }
                    placeholder={
                      (localSettings.preferred_slicer ?? 'bambu_studio') === 'orcaslicer'
                        ? 'http://localhost:3003'
                        : 'http://localhost:3001'
                    }
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none placeholder:text-bambu-gray/40"
                  />
                  <p className="text-xs text-bambu-gray mt-1">
                    {t(
                      'settings.slicerApiUrlDescription',
                      'URL of the slicer-API sidecar container. Leave blank to use the SLICER_API_URL / BAMBU_STUDIO_API_URL env var defaults.',
                    )}
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Slicer Preset Bundles — only meaningful when the sidecar is in use,
              since uploads / lists round-trip through it. Hide it entirely when
              use_slicer_api is off so the Settings page doesn't show a panel that
              can't do anything. */}
          {(localSettings.use_slicer_api ?? false) && <SlicerBundlesPanel />}

          {/* Auto-Drying */}
          <Card>
            <CardHeader>
              <h3 className="text-base font-semibold text-white flex items-center gap-2" id="card-drying">
                <Flame className="w-4 h-4 text-amber-400" />
                {t('settings.queueDrying')}
              </h3>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-xs text-bambu-gray">
                {t('settings.queueDryingDescription')}
              </p>
              <div className="flex items-center justify-between">
                <div>
                  <label className="block text-sm text-white">
                    {t('settings.queueDryingEnabled')}
                  </label>
                  <p className="text-xs text-bambu-gray mt-0.5">
                    {t('settings.queueDryingEnabledDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.queue_drying_enabled ?? false}
                    onChange={(e) => updateSetting('queue_drying_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              {localSettings.queue_drying_enabled && (
                <div className="flex items-center justify-between">
                  <div>
                    <label className="block text-sm text-white">
                      {t('settings.queueDryingBlock')}
                    </label>
                    <p className="text-xs text-bambu-gray mt-0.5">
                      {t('settings.queueDryingBlockDescription')}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={localSettings.queue_drying_block ?? false}
                      onChange={(e) => updateSetting('queue_drying_block', e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-amber-500"></div>
                  </label>
                </div>
              )}
              <div className="flex items-center justify-between">
                <div>
                  <label className="block text-sm text-white">
                    {t('settings.ambientDryingEnabled')}
                  </label>
                  <p className="text-xs text-bambu-gray mt-0.5">
                    {t('settings.ambientDryingEnabledDescription')}
                  </p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={localSettings.ambient_drying_enabled ?? false}
                    onChange={(e) => updateSetting('ambient_drying_enabled', e.target.checked)}
                    className="sr-only peer"
                  />
                  <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>
              {/* Drying Presets Table */}
              <div className="space-y-2">
                <p className="text-sm text-white font-medium">{t('settings.dryingPresets')}</p>
                <p className="text-xs text-bambu-gray">{t('settings.dryingPresetsDescription')}</p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-bambu-gray border-b border-bambu-dark-tertiary">
                        <th className="text-left py-1.5">{t('settings.dryingFilament')}</th>
                        <th className="text-center py-1.5" colSpan={2}>AMS 2 Pro</th>
                        <th className="text-center py-1.5" colSpan={2}>AMS-HT</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(() => {
                        const defaults: Record<string, { n3f: number; n3s: number; n3f_hours: number; n3s_hours: number }> = {
                          PLA: { n3f: 45, n3s: 45, n3f_hours: 12, n3s_hours: 12 },
                          PETG: { n3f: 65, n3s: 65, n3f_hours: 12, n3s_hours: 12 },
                          TPU: { n3f: 65, n3s: 75, n3f_hours: 12, n3s_hours: 18 },
                          ABS: { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
                          ASA: { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
                          PA: { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 12 },
                          PC: { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
                          PVA: { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 18 },
                        };
                        let presets = { ...defaults };
                        try {
                          if (localSettings.drying_presets) {
                            const parsed = JSON.parse(localSettings.drying_presets);
                            if (typeof parsed === 'object' && parsed !== null) {
                              presets = { ...defaults, ...parsed };
                            }
                          }
                        } catch { /* use defaults */ }

                        const updatePreset = (fil: string, key: string, value: number) => {
                          const updated = { ...presets, [fil]: { ...presets[fil], [key]: value } };
                          updateSetting('drying_presets', JSON.stringify(updated));
                        };

                        return Object.entries(presets).map(([fil, preset]) => (
                          <tr key={fil} className="border-b border-bambu-dark-tertiary/50">
                            <td className="py-1.5 pr-2 text-white font-medium">{fil}</td>
                            <td className="py-1 px-1">
                              <div className="flex items-center justify-end gap-1">
                                <input type="number" min={30} max={65} value={preset.n3f}
                                  onChange={e => updatePreset(fil, 'n3f', Math.max(1, parseInt(e.target.value) || 0))}
                                  className="w-14 px-1.5 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-center text-xs focus:border-amber-500/50 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <span className="text-bambu-gray">°C</span>
                              </div>
                            </td>
                            <td className="py-1 px-1">
                              <div className="flex items-center gap-1">
                                <input type="number" min={1} max={24} value={preset.n3f_hours}
                                  onChange={e => updatePreset(fil, 'n3f_hours', Math.max(1, parseInt(e.target.value) || 0))}
                                  className="w-14 px-1.5 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-center text-xs focus:border-amber-500/50 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <span className="text-bambu-gray">h</span>
                              </div>
                            </td>
                            <td className="py-1 px-1">
                              <div className="flex items-center justify-end gap-1">
                                <input type="number" min={30} max={85} value={preset.n3s}
                                  onChange={e => updatePreset(fil, 'n3s', Math.max(1, parseInt(e.target.value) || 0))}
                                  className="w-14 px-1.5 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-center text-xs focus:border-amber-500/50 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <span className="text-bambu-gray">°C</span>
                              </div>
                            </td>
                            <td className="py-1 px-1">
                              <div className="flex items-center gap-1">
                                <input type="number" min={1} max={24} value={preset.n3s_hours}
                                  onChange={e => updatePreset(fil, 'n3s_hours', Math.max(1, parseInt(e.target.value) || 0))}
                                  className="w-14 px-1.5 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-center text-xs focus:border-amber-500/50 focus:outline-none [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                                />
                                <span className="text-bambu-gray">h</span>
                              </div>
                            </td>
                          </tr>
                        ));
                      })()}
                    </tbody>
                  </table>
                </div>
              </div>
            </CardContent>
          </Card>
          </div>
        </div>
      )}

      {activeTab === 'filament' && localSettings && (
        <>
        <div className="flex flex-col lg:flex-row gap-4 lg:gap-6">
          {/* Left Column (1/3) - Mode Selector + AMS Thresholds */}
          <div className="lg:w-1/3 space-y-3">
            <SpoolmanSettings />

            <Card id="card-filamentchecks">
              <CardHeader>
                <h2 className="text-lg font-semibold text-white">{t('settings.filamentChecks')}</h2>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white">{t('settings.disableFilamentWarnings')}</p>
                    <p className="text-sm text-bambu-gray">
                      {t('settings.disableFilamentWarningsDesc')}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={localSettings.disable_filament_warnings}
                      onChange={(e) => updateSetting('disable_filament_warnings', e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                  </label>
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white">{t('settings.preferLowestFilament')}</p>
                    <p className="text-sm text-bambu-gray">
                      {t('settings.preferLowestFilamentDesc')}
                    </p>
                  </div>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={localSettings.prefer_lowest_filament}
                      onChange={(e) => updateSetting('prefer_lowest_filament', e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-11 h-6 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bambu-green"></div>
                  </label>
                </div>
              </CardContent>
            </Card>

            {/* Per-Printer Mapping Default */}
            <Card id="card-printmodal">
              <CardHeader>
                <h2 className="text-lg font-semibold text-white">{t('settings.printModal')}</h2>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-white">{t('settings.expandCustomMapping')}</p>
                    <p className="text-sm text-bambu-gray">
                      {t('settings.expandCustomMappingDescription')}
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
              </CardContent>
            </Card>

            <Card id="card-amsthresholds">
              <CardHeader>
                <h2 className="text-lg font-semibold text-white">{t('settings.amsDisplayThresholds')}</h2>
              </CardHeader>
              <CardContent className="space-y-3">
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
                        {t('settings.goodGreen')} ≤
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
                        {t('settings.fairOrange')} ≤
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
                  <p className="text-xs text-amber-400/70">
                    {t('settings.fairAlsoDryingThreshold')}
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
                        {t('settings.goodBlue')} ≤
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
                        {t('settings.fairOrange')} ≤
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
                      {t('settings.keepSensorHistory')}
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
                      <span className="text-bambu-gray">{t('common.days')}</span>
                    </div>
                  </div>
                  <p className="text-xs text-bambu-gray">
                    {t('settings.historyRetentionDescription')}
                  </p>
                </div>

              </CardContent>
            </Card>
          </div>

          {/* Right Column (2/3) - Spool Catalog + Color Catalog */}
          <div className="lg:w-2/3 space-y-3">
            <SpoolCatalogSettings />
            <ColorCatalogSettings />
          </div>
        </div>
        </>
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
          title={t('settings.clearNotificationLogs')}
          message={t('settings.clearLogsMessage')}
          confirmText={t('settings.clearLogs')}
          variant="warning"
          onConfirm={async () => {
            setShowClearLogsConfirm(false);
            try {
              const result = await api.clearNotificationLogs(30);
              showToast(result.message, 'success');
            } catch {
              showToast(t('settings.toast.clearLogsFailed'), 'error');
            }
          }}
          onCancel={() => setShowClearLogsConfirm(false)}
        />
      )}

      {/* Confirm Modal: Clear Local Storage */}
      {showClearStorageConfirm && (
        <ConfirmModal
          title={t('settings.resetUiPreferences')}
          message={t('settings.resetUiPreferencesMessage')}
          confirmText={t('settings.resetPreferences')}
          variant="default"
          onConfirm={() => {
            setShowClearStorageConfirm(false);
            localStorage.clear();
            showToast(t('settings.toast.uiPreferencesReset'), 'success');
            setTimeout(() => window.location.reload(), 1000);
          }}
          onCancel={() => setShowClearStorageConfirm(false)}
        />
      )}

      {/* Confirm Modal: Bulk Plug Action */}
      {showBulkPlugConfirm && (
        <ConfirmModal
          title={`Turn All Plugs ${showBulkPlugConfirm === 'on' ? 'On' : 'Off'}`}
          message={`This will turn ${showBulkPlugConfirm === 'on' ? 'ON' : 'OFF'} all ${smartPlugs?.filter(p => p.enabled).length || 0} enabled smart plugs. ${showBulkPlugConfirm === 'off' ? 'Any running printers may be affected!' : ''}`}
          confirmText={`Turn All ${showBulkPlugConfirm === 'on' ? 'On' : 'Off'}`}
          variant={showBulkPlugConfirm === 'off' ? 'danger' : 'warning'}
          onConfirm={() => {
            const action = showBulkPlugConfirm;
            setShowBulkPlugConfirm(null);
            bulkPlugActionMutation.mutate(action);
          }}
          onCancel={() => setShowBulkPlugConfirm(null)}
        />
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
                  Release Notes - v{updateCheck.latest_version}
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
                    View on GitHub
                  </Button>
                </a>
              )}
              <Button
                onClick={() => setShowReleaseNotes(false)}
                className="flex-1"
              >
                Close
              </Button>
            </div>
          </Card>
        </div>
      )}

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="space-y-3">
          {/* Sub-tab Navigation */}
          <div className="flex gap-1 border-b border-bambu-dark-tertiary">
            <button
              onClick={() => setUsersSubTab('users')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
                usersSubTab === 'users'
                  ? 'text-bambu-green border-bambu-green'
                  : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
              }`}
            >
              <Users className="w-4 h-4" />
              {t('settings.tabs.users')}
            </button>
            <button
              onClick={() => setUsersSubTab('email')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
                usersSubTab === 'email'
                  ? 'text-bambu-green border-bambu-green'
                  : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
              }`}
            >
              <Mail className="w-4 h-4" />
              {t('settings.tabs.emailAuth') || 'Email Authentication'}
              {advancedAuthStatus?.advanced_auth_enabled && (
                <span className="w-2 h-2 rounded-full bg-green-400" />
              )}
            </button>
            <button
              onClick={() => setUsersSubTab('ldap')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px lg:border-b-0 lg:border-l-2 lg:-ml-px lg:mb-0 lg:justify-start flex items-center gap-2 ${
                usersSubTab === 'ldap'
                  ? 'text-bambu-green border-bambu-green'
                  : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
              }`}
            >
              <Shield className="w-4 h-4" />
              {t('settings.tabs.ldap') || 'LDAP'}
              {ldapStatus?.ldap_enabled && (
                <span className="w-2 h-2 rounded-full bg-green-400" />
              )}
            </button>
            <button
              onClick={() => setUsersSubTab('twofa')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
                usersSubTab === 'twofa'
                  ? 'text-bambu-green border-bambu-green'
                  : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
              }`}
            >
              <Shield className="w-4 h-4" />
              {t('settings.tabs.twoFa')}
              <span
                className={`w-2 h-2 rounded-full ${
                  twoFAStatus?.totp_enabled || twoFAStatus?.email_otp_enabled
                    ? 'bg-green-400'
                    : 'bg-bambu-gray/40'
                }`}
              />
            </button>
            {isAdmin && (
              <button
                onClick={() => setUsersSubTab('oidc')}
                className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 -mb-px flex items-center gap-2 ${
                  usersSubTab === 'oidc'
                    ? 'text-bambu-green border-bambu-green'
                    : 'text-bambu-gray hover:text-gray-900 dark:hover:text-white border-transparent'
                }`}
              >
                <Globe className="w-4 h-4" />
                {t('settings.tabs.oidc')}
                <span
                  className={`w-2 h-2 rounded-full ${
                    oidcProvidersAll.some((p) => p.is_enabled)
                      ? 'bg-green-400'
                      : 'bg-bambu-gray/40'
                  }`}
                />
              </button>
            )}
          </div>

          {/* Users Sub-tab */}
          {usersSubTab === 'users' && (
          <>
          {/* Auth Toggle Header */}
          <Card>
            <CardContent className="py-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`w-10 h-10 rounded-full flex items-center justify-center ${authEnabled ? 'bg-green-500/20' : 'bg-gray-500/20'}`}>
                    {authEnabled ? (
                      <Lock className="w-5 h-5 text-green-400" />
                    ) : (
                      <Unlock className="w-5 h-5 text-gray-400" />
                    )}
                  </div>
                  <div>
                    <h3 className="text-white font-medium">{t('settings.authentication')}</h3>
                    <p className="text-sm text-bambu-gray">
                      {authEnabled
                        ? t('settings.authEnabledDescription')
                        : t('settings.authDisabledDescription')}
                    </p>
                  </div>
                </div>
                {!authEnabled ? (
                  <Button onClick={() => navigate('/setup')}>
                    <Lock className="w-4 h-4" />
                    {t('common.enable')}
                  </Button>
                ) : user?.is_admin && (
                  <Button variant="secondary" onClick={() => setShowDisableAuthConfirm(true)}>
                    <Unlock className="w-4 h-4" />
                    {t('common.disable')}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>

          {/* Advanced Authentication Notice Box */}
          {advancedAuthStatus?.advanced_auth_enabled && (
            <Card className="border-blue-500/30 bg-blue-500/5">
              <CardContent className="py-4">
                <div className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-full flex items-center justify-center bg-blue-500/20 flex-shrink-0">
                    <Mail className="w-5 h-5 text-blue-400" />
                  </div>
                  <div>
                    <h3 className="text-white font-medium">{t('settings.email.advancedAuthEnabled')}</h3>
                    <p className="text-sm text-bambu-gray mt-1">
                      {t('settings.email.advancedAuthEnabledDesc')}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          )}

          {authEnabled && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              {/* Left Column: Current User + User List */}
              <div className="space-y-3">
                {/* Current User Card */}
                {user && (
                  <Card>
                    <CardHeader>
                      <div className="flex items-center justify-between">
                        <h3 className="text-lg font-semibold text-white flex items-center gap-2" id="card-currentuser">
                          <Users className="w-5 h-5 text-bambu-green" />
                          {t('settings.currentUser')}
                        </h3>
                        {user.auth_source !== 'ldap' && (
                        <Button size="sm" variant="ghost" onClick={() => setShowChangePasswordModal(true)}>
                          <Key className="w-4 h-4" />
                          {t('settings.changePassword')}
                        </Button>
                        )}
                      </div>
                    </CardHeader>
                    <CardContent>
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-white font-medium text-lg">{user.username}</p>
                          <div className="flex flex-wrap gap-1 mt-2">
                            {user.is_admin && (
                              <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-500/20 text-purple-300">
                                {t('settings.admin')}
                              </span>
                            )}
                            {user.groups?.map(group => (
                              <span
                                key={group.id}
                                className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                  group.name === 'Administrators'
                                    ? 'bg-purple-500/20 text-purple-300'
                                    : group.name === 'Operators'
                                    ? 'bg-blue-500/20 text-blue-300'
                                    : group.name === 'Viewers'
                                    ? 'bg-green-500/20 text-green-300'
                                    : 'bg-gray-500/20 text-gray-300'
                                }`}
                              >
                                {group.name}
                              </span>
                            ))}
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* User List */}
                <Card>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <h3 className="text-lg font-semibold text-white flex items-center gap-2" id="card-users">
                        <Users className="w-5 h-5 text-bambu-green" />
                        {t('settings.users')}
                      </h3>
                      {hasPermission('users:create') && (
                        <Button
                          size="sm"
                          onClick={() => {
                            setShowCreateUserModal(true);
                            setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                          }}
                        >
                          <Plus className="w-4 h-4" />
                          {t('settings.addUser')}
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    {usersLoading ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
                      </div>
                    ) : usersData.length === 0 ? (
                      <p className="text-center text-bambu-gray py-8">{t('settings.noUsersFound')}</p>
                    ) : (
                      <div className="divide-y divide-bambu-dark-tertiary">
                        {usersData.map((userItem) => (
                          <div key={userItem.id} className="py-3 flex items-center justify-between">
                            <div className="flex-1 min-w-0">
                              <p className="text-white font-medium truncate">{userItem.username}</p>
                              <div className="flex flex-wrap gap-1 mt-1">
                                {userItem.auth_source === 'ldap' && (
                                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-cyan-500/20 text-cyan-300">
                                    LDAP
                                  </span>
                                )}
                                {userItem.is_admin && (
                                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-purple-500/20 text-purple-300">
                                    {t('settings.admin')}
                                  </span>
                                )}
                                {userItem.groups?.map(group => (
                                  <span
                                    key={group.id}
                                    className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                                      group.name === 'Administrators'
                                        ? 'bg-purple-500/20 text-purple-300'
                                        : group.name === 'Operators'
                                        ? 'bg-blue-500/20 text-blue-300'
                                        : group.name === 'Viewers'
                                        ? 'bg-green-500/20 text-green-300'
                                        : 'bg-gray-500/20 text-gray-300'
                                    }`}
                                  >
                                    {group.name}
                                  </span>
                                ))}
                              </div>
                            </div>
                            <div className="flex items-center gap-1 ml-4">
                              {hasPermission('users:update') && (
                                <Button size="sm" variant="ghost" onClick={() => startEditUser(userItem)}>
                                  <Edit2 className="w-4 h-4" />
                                </Button>
                              )}
                              {hasPermission('users:delete') && userItem.id !== user?.id && (
                                <Button size="sm" variant="ghost" onClick={() => handleDeleteUserClick(userItem.id)}>
                                  <Trash2 className="w-4 h-4" />
                                </Button>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Right Column: Groups */}
              <div>
                <Card>
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <h3 className="text-lg font-semibold text-white flex items-center gap-2" id="card-groups">
                        <Shield className="w-5 h-5 text-bambu-green" />
                        {t('settings.groups')}
                      </h3>
                      {hasPermission('groups:create') && (
                        <Button
                          size="sm"
                          onClick={() => navigate('/groups/new')}
                        >
                          <Plus className="w-4 h-4" />
                          {t('settings.addGroup')}
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent>
                    {groupsLoading ? (
                      <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
                      </div>
                    ) : groupsData.length === 0 ? (
                      <p className="text-center text-bambu-gray py-8">{t('settings.noGroupsFound')}</p>
                    ) : (
                      <div className="divide-y divide-bambu-dark-tertiary">
                        {groupsData.map((group) => (
                          <div key={group.id} className="py-3">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <Shield
                                  className={`w-4 h-4 ${
                                    group.name === 'Administrators'
                                      ? 'text-purple-400'
                                      : group.name === 'Operators'
                                      ? 'text-blue-400'
                                      : group.name === 'Viewers'
                                      ? 'text-green-400'
                                      : 'text-bambu-gray'
                                  }`}
                                />
                                <span className="text-white font-medium">{group.name}</span>
                                {group.is_system && (
                                  <span className="px-2 py-0.5 rounded text-xs bg-yellow-500/20 text-yellow-400">
                                    {t('settings.system')}
                                  </span>
                                )}
                              </div>
                              <div className="flex items-center gap-1">
                                {hasPermission('groups:update') && (
                                  <Button size="sm" variant="ghost" onClick={() => navigate(`/groups/${group.id}/edit`)}>
                                    <Edit2 className="w-4 h-4" />
                                  </Button>
                                )}
                                {hasPermission('groups:delete') && !group.is_system && (
                                  <Button size="sm" variant="ghost" onClick={() => setDeleteGroupId(group.id)}>
                                    <Trash2 className="w-4 h-4" />
                                  </Button>
                                )}
                              </div>
                            </div>
                            <p className="text-sm text-bambu-gray mt-1 ml-6">
                              {group.description || t('settings.noDescription')}
                            </p>
                            <div className="flex items-center gap-4 mt-2 ml-6 text-xs text-bambu-gray">
                              <span>{t('settings.userCount', { count: group.user_count })}</span>
                              <span>{t('settings.permissionCount', { count: group.permissions.length })}</span>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}

          {/* Auth Disabled Info */}
          {!authEnabled && (
            <Card>
              <CardContent className="py-6">
                <div className="text-center">
                  <Unlock className="w-12 h-12 text-bambu-gray mx-auto mb-4" />
                  <h3 className="text-lg font-medium text-white mb-2">{t('settings.authDisabledTitle')}</h3>
                  <p className="text-sm text-bambu-gray mb-4 max-w-md mx-auto">
                    {t('settings.authDisabledMessage')}
                  </p>
                  <ul className="space-y-2 text-sm text-bambu-gray mb-6 text-left max-w-xs mx-auto">
                    <li className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                      <span>{t('settings.authDisabledFeature1')}</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                      <span>{t('settings.authDisabledFeature2')}</span>
                    </li>
                    <li className="flex items-start gap-2">
                      <CheckCircle className="w-4 h-4 text-bambu-green mt-0.5 flex-shrink-0" />
                      <span>{t('settings.authDisabledFeature3')}</span>
                    </li>
                  </ul>
                  <Button onClick={() => navigate('/setup')}>
                    <Lock className="w-4 h-4" />
                    {t('settings.enableAuthentication')}
                  </Button>
                </div>
              </CardContent>
            </Card>
          )}
          </>
          )}

          {/* Email Auth Sub-tab */}
          {usersSubTab === 'email' && (
            <div className="max-w-5xl" id="card-smtp">
              <EmailSettings />
            </div>
          )}

          {usersSubTab === 'ldap' && (
            <div className="max-w-5xl" id="card-ldap">
              <LDAPSettings />
            </div>
          )}

          {usersSubTab === 'twofa' && (
            <div className="max-w-2xl">
              <TwoFactorSettings />
            </div>
          )}

          {usersSubTab === 'oidc' && isAdmin && (
            <div className="max-w-3xl">
              <OIDCProviderSettings />
            </div>
          )}
        </div>
      )}

      {/* Create User Modal */}
      {showCreateUserModal && !advancedAuthStatus?.advanced_auth_enabled && (
        <div
          className="fixed inset-0 bg-black flex items-center justify-center z-50 p-4"
          onClick={() => {
            setShowCreateUserModal(false);
            setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
          }}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Users className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('settings.createUser')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowCreateUserModal(false);
                    setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                  }}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-white mb-2">{t('settings.username')}</label>
                  <input
                    type="text"
                    value={userFormData.username}
                    onChange={(e) => setUserFormData({ ...userFormData, username: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('settings.enterUsername')}
                    autoComplete="username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">{t('settings.password')}</label>
                  <input
                    type="password"
                    value={userFormData.password}
                    onChange={(e) => setUserFormData({ ...userFormData, password: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('settings.enterPassword')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">{t('settings.confirmPassword')}</label>
                  <input
                    type="password"
                    value={userFormData.confirmPassword}
                    onChange={(e) => setUserFormData({ ...userFormData, confirmPassword: e.target.value })}
                    className={`w-full px-4 py-3 bg-bambu-dark-secondary border rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors ${
                      userFormData.confirmPassword && userFormData.password !== userFormData.confirmPassword
                        ? 'border-red-500'
                        : 'border-bambu-dark-tertiary'
                    }`}
                    placeholder={t('settings.confirmPasswordPlaceholder')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                  {userFormData.confirmPassword && userFormData.password !== userFormData.confirmPassword && (
                    <p className="text-red-400 text-xs mt-1">{t('settings.passwordsDoNotMatch')}</p>
                  )}
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">{t('settings.groups')}</label>
                  <div className="space-y-2 max-h-40 overflow-y-auto p-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                    {groupsData.map(group => (
                      <label
                        key={group.id}
                        className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={userFormData.group_ids.includes(group.id)}
                          onChange={() => toggleUserGroup(group.id)}
                          className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
                        />
                        <span className="text-sm text-white">{group.name}</span>
                        {group.is_system && (
                          <span className="text-xs text-yellow-400">{t('settings.systemBadge')}</span>
                        )}
                      </label>
                    ))}
                    {groupsData.length === 0 && (
                      <p className="text-sm text-bambu-gray">{t('settings.noGroupsAvailable')}</p>
                    )}
                  </div>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowCreateUserModal(false);
                    setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                  }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleCreateUser}
                  disabled={createUserMutation.isPending || !userFormData.username || !userFormData.password || userFormData.password !== userFormData.confirmPassword || userFormData.password.length < 6}
                >
                  {createUserMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('settings.creating')}
                    </>
                  ) : (
                    <>
                      <Plus className="w-4 h-4" />
                      {t('settings.createUser')}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Create User Modal - Advanced Authentication */}
      {showCreateUserModal && advancedAuthStatus?.advanced_auth_enabled && (
        <CreateUserAdvancedAuthModal
          formData={userFormData}
          setFormData={setUserFormData}
          groups={groupsData}
          onClose={() => {
            setShowCreateUserModal(false);
            setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
          }}
          onCreate={handleCreateUser}
          isCreating={createUserMutation.isPending}
          isCreateButtonDisabled={createUserMutation.isPending || !userFormData.username || !userFormData.email}
        />
      )}

      {/* Edit User Modal */}
      {showEditUserModal && editingUserId !== null && (
        <div
          className="fixed inset-0 bg-black flex items-center justify-center z-50 p-4"
          onClick={() => {
            setShowEditUserModal(false);
            setEditingUserId(null);
            setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
          }}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Edit2 className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('settings.editUser')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowEditUserModal(false);
                    setEditingUserId(null);
                    setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                  }}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {/* Username Field */}
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('settings.username')} {advancedAuthStatus?.advanced_auth_enabled && <span className="text-red-400">*</span>}
                  </label>
                  <input
                    type="text"
                    value={userFormData.username}
                    onChange={(e) => setUserFormData({ ...userFormData, username: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('settings.enterUsername')}
                    autoComplete="username"
                  />
                </div>

                {/* Email Field */}
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    {t('users.form.email') || 'Email'} {advancedAuthStatus?.advanced_auth_enabled ? <span className="text-red-400">*</span> : <span className="text-bambu-gray font-normal">({t('users.form.optional') || 'optional'})</span>}
                  </label>
                  <input
                    type="email"
                    value={userFormData.email}
                    onChange={(e) => setUserFormData({ ...userFormData, email: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('users.form.emailPlaceholder') || 'user@example.com'}
                    required={advancedAuthStatus?.advanced_auth_enabled}
                  />
                </div>

                {/* Password Fields - only show when Advanced Auth is disabled */}
                {!advancedAuthStatus?.advanced_auth_enabled && (
                  <>
                    <div>
                      <label className="block text-sm font-medium text-white mb-2">
                        {t('users.form.password') || 'Password'} <span className="text-bambu-gray font-normal">({t('users.form.leaveBlankToKeep') || 'leave blank to keep current'})</span>
                      </label>
                      <input
                        type="password"
                        value={userFormData.password}
                        onChange={(e) => setUserFormData({ ...userFormData, password: e.target.value, confirmPassword: '' })}
                        className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                        placeholder={t('settings.enterNewPassword')}
                        autoComplete="new-password"
                        minLength={6}
                      />
                    </div>
                    {userFormData.password && (
                      <div>
                        <label className="block text-sm font-medium text-white mb-2">{t('settings.confirmPassword')}</label>
                        <input
                          type="password"
                          value={userFormData.confirmPassword}
                          onChange={(e) => setUserFormData({ ...userFormData, confirmPassword: e.target.value })}
                          className={`w-full px-4 py-3 bg-bambu-dark-secondary border rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors ${
                            userFormData.confirmPassword && userFormData.password !== userFormData.confirmPassword
                              ? 'border-red-500'
                              : 'border-bambu-dark-tertiary'
                          }`}
                          placeholder={t('settings.confirmNewPassword')}
                          autoComplete="new-password"
                          minLength={6}
                        />
                        {userFormData.confirmPassword && userFormData.password !== userFormData.confirmPassword && (
                          <p className="text-red-400 text-xs mt-1">{t('settings.passwordsDoNotMatch')}</p>
                        )}
                      </div>
                    )}
                  </>
                )}

                {/* Info box about auto-generated password when Advanced Auth is enabled */}
                {advancedAuthStatus?.advanced_auth_enabled && (
                  <div className="bg-bambu-dark-secondary/50 border border-bambu-green/20 rounded-lg p-3 space-y-3">
                    <p className="text-sm text-bambu-gray">
                      {t('users.form.passwordManagedByAdvancedAuth') || 'Password is managed by Advanced Authentication. Use "Reset Password" to send a new password to the user via email.'}
                    </p>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => editingUserId && resetPasswordMutation.mutate(editingUserId)}
                      disabled={resetPasswordMutation.isPending || !userFormData.email}
                      className="w-full"
                    >
                      {resetPasswordMutation.isPending ? (
                        <>
                          <Loader2 className="w-4 h-4 animate-spin" />
                          {t('users.form.resettingPassword') || 'Resetting Password...'}
                        </>
                      ) : (
                        <>
                          <RotateCcw className="w-4 h-4" />
                          {t('users.form.resetPassword') || 'Reset Password'}
                        </>
                      )}
                    </Button>
                  </div>
                )}

                {/* Groups Field */}
                <div>
                  <label className="block text-sm font-medium text-white mb-2">{t('users.form.groups') || 'Groups'}</label>
                  <div className="space-y-2 max-h-40 overflow-y-auto p-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg">
                    {groupsData.map(group => (
                      <label
                        key={group.id}
                        className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={userFormData.group_ids.includes(group.id)}
                          onChange={() => toggleUserGroup(group.id)}
                          className="w-4 h-4 rounded border-bambu-gray text-bambu-green focus:ring-bambu-green focus:ring-offset-0 bg-bambu-dark"
                        />
                        <span className="text-sm text-white">{group.name}</span>
                        {group.is_system && (
                          <span className="text-xs text-yellow-400">({t('users.system') || 'System'})</span>
                        )}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowEditUserModal(false);
                    setEditingUserId(null);
                    setUserFormData({ username: '', password: '', email: '', confirmPassword: '', role: 'user', group_ids: [] });
                  }}
                >
                  {t('users.modal.cancel') || 'Cancel'}
                </Button>
                <Button
                  onClick={() => handleUpdateUser(editingUserId)}
                  disabled={
                    updateUserMutation.isPending ||
                    !userFormData.username ||
                    (advancedAuthStatus?.advanced_auth_enabled && !userFormData.email) ||
                    Boolean(!advancedAuthStatus?.advanced_auth_enabled && userFormData.password && (userFormData.password !== userFormData.confirmPassword || userFormData.password.length < 6))
                  }
                >
                  {updateUserMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('users.modal.saving') || 'Saving...'}
                    </>
                  ) : (
                    <>
                      <Save className="w-4 h-4" />
                      {t('users.modal.saveChanges') || 'Save Changes'}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete User Confirmation Modal */}
      {deleteUserId !== null && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4"
          onClick={() => {
            setDeleteUserId(null);
            setDeleteUserItemCounts(null);
          }}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center gap-2 text-red-400">
                <Trash2 className="w-5 h-5" />
                <h3 className="text-lg font-semibold">{t('settings.deleteUserTitle')}</h3>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {deleteUserLoading ? (
                <div className="flex items-center justify-center py-4">
                  <div className="animate-spin rounded-full h-6 w-6 border-2 border-bambu-green border-t-transparent" />
                </div>
              ) : deleteUserItemCounts && (deleteUserItemCounts.archives + deleteUserItemCounts.queue_items + deleteUserItemCounts.library_files > 0) ? (
                <>
                  <p className="text-white">{t('settings.userHasCreated')}</p>
                  <ul className="list-disc list-inside text-bambu-gray space-y-1">
                    {deleteUserItemCounts.archives > 0 && (
                      <li>{deleteUserItemCounts.archives} archive{deleteUserItemCounts.archives !== 1 ? 's' : ''}</li>
                    )}
                    {deleteUserItemCounts.queue_items > 0 && (
                      <li>{deleteUserItemCounts.queue_items} queue item{deleteUserItemCounts.queue_items !== 1 ? 's' : ''}</li>
                    )}
                    {deleteUserItemCounts.library_files > 0 && (
                      <li>{deleteUserItemCounts.library_files} library file{deleteUserItemCounts.library_files !== 1 ? 's' : ''}</li>
                    )}
                  </ul>
                  <p className="text-bambu-gray text-sm">{t('settings.userItemsQuestion')}</p>
                  <div className="flex flex-col gap-2">
                    <Button
                      variant="danger"
                      onClick={() => deleteUserMutation.mutate({ id: deleteUserId, deleteItems: true })}
                      disabled={deleteUserMutation.isPending}
                      className="justify-center"
                    >
                      {t('settings.deleteUserAndItems')}
                    </Button>
                    <Button
                      variant="secondary"
                      onClick={() => deleteUserMutation.mutate({ id: deleteUserId, deleteItems: false })}
                      disabled={deleteUserMutation.isPending}
                      className="justify-center"
                    >
                      {t('settings.deleteUserKeepItems')}
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setDeleteUserId(null);
                        setDeleteUserItemCounts(null);
                      }}
                      disabled={deleteUserMutation.isPending}
                      className="justify-center"
                    >
                      {t('common.cancel')}
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <p className="text-white">{t('settings.deleteUserConfirm')}</p>
                  <p className="text-bambu-gray text-sm">{t('settings.actionCannotBeUndone')}</p>
                  <div className="flex gap-2 justify-end">
                    <Button
                      variant="ghost"
                      onClick={() => {
                        setDeleteUserId(null);
                        setDeleteUserItemCounts(null);
                      }}
                      disabled={deleteUserMutation.isPending}
                    >
                      {t('common.cancel')}
                    </Button>
                    <Button
                      variant="danger"
                      onClick={() => deleteUserMutation.mutate({ id: deleteUserId, deleteItems: false })}
                      disabled={deleteUserMutation.isPending}
                    >
                      {t('settings.deleteUserTitle')}
                    </Button>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete Group Confirmation Modal */}
      {deleteGroupId !== null && (
        <ConfirmModal
          title={t('settings.deleteGroupTitle')}
          message={t('settings.deleteGroupMessage')}
          confirmText={t('settings.deleteGroup')}
          variant="danger"
          onConfirm={() => {
            deleteGroupMutation.mutate(deleteGroupId);
            setDeleteGroupId(null);
          }}
          onCancel={() => setDeleteGroupId(null)}
        />
      )}

      {/* Backup Tab */}
      {activeTab === 'failure-detection' && (
        <div id="card-failure-detection">
          <FailureDetectionSettings />
        </div>
      )}

      {activeTab === 'backup' && (
        <div id="card-backup">
          <GitHubBackupSettings />
        </div>
      )}

      {/* Disable Authentication Confirmation Modal */}
      {showDisableAuthConfirm && (
        <ConfirmModal
          title={t('settings.disableAuthenticationTitle')}
          message={t('settings.disableAuthenticationMessage')}
          confirmText={t('settings.disableAuthentication')}
          variant="danger"
          onConfirm={async () => {
            try {
              await api.disableAuth();
              showToast(t('settings.toast.authDisabled'), 'success');
              await refreshAuth();
              setShowDisableAuthConfirm(false);
              // Refresh the page to ensure all protected routes are accessible
              window.location.href = '/';
            } catch (error: unknown) {
              const message = error instanceof Error ? error.message : t('settings.toast.authDisableFailed');
              showToast(message, 'error');
            }
          }}
          onCancel={() => setShowDisableAuthConfirm(false)}
        />
      )}

      {/* Change Password Modal */}
      {showChangePasswordModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => {
            setShowChangePasswordModal(false);
            setChangePasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
          }}
        >
          <Card
            className="w-full max-w-md"
            onClick={(e: React.MouseEvent) => e.stopPropagation()}
          >
            <CardHeader>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Key className="w-5 h-5 text-bambu-green" />
                  <h2 className="text-lg font-semibold text-white">{t('settings.changePassword')}</h2>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setShowChangePasswordModal(false);
                    setChangePasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
                  }}
                >
                  <X className="w-5 h-5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    Current Password
                  </label>
                  <input
                    type="password"
                    value={changePasswordData.currentPassword}
                    onChange={(e) => setChangePasswordData({ ...changePasswordData, currentPassword: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('settings.enterCurrentPassword')}
                    autoComplete="current-password"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    New Password
                  </label>
                  <input
                    type="password"
                    value={changePasswordData.newPassword}
                    onChange={(e) => setChangePasswordData({ ...changePasswordData, newPassword: e.target.value })}
                    className="w-full px-4 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                    placeholder={t('settings.enterNewPasswordMin6')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-white mb-2">
                    Confirm New Password
                  </label>
                  <input
                    type="password"
                    value={changePasswordData.confirmPassword}
                    onChange={(e) => setChangePasswordData({ ...changePasswordData, confirmPassword: e.target.value })}
                    className={`w-full px-4 py-3 bg-bambu-dark-secondary border rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors ${
                      changePasswordData.confirmPassword && changePasswordData.newPassword !== changePasswordData.confirmPassword
                        ? 'border-red-500'
                        : 'border-bambu-dark-tertiary'
                    }`}
                    placeholder={t('settings.confirmNewPassword')}
                    autoComplete="new-password"
                    minLength={6}
                  />
                  {changePasswordData.confirmPassword && changePasswordData.newPassword !== changePasswordData.confirmPassword && (
                    <p className="text-red-400 text-xs mt-1">{t('settings.passwordsDoNotMatch')}</p>
                  )}
                </div>
              </div>
              <div className="mt-6 flex justify-end gap-3">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setShowChangePasswordModal(false);
                    setChangePasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
                  }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={async () => {
                    if (changePasswordData.newPassword !== changePasswordData.confirmPassword) {
                      showToast(t('settings.toast.passwordsDoNotMatch'), 'error');
                      return;
                    }
                    if (changePasswordData.newPassword.length < 6) {
                      showToast(t('settings.toast.passwordTooShort'), 'error');
                      return;
                    }
                    setChangePasswordLoading(true);
                    try {
                      await api.changePassword(changePasswordData.currentPassword, changePasswordData.newPassword);
                      showToast(t('settings.toast.passwordChanged'), 'success');
                      setShowChangePasswordModal(false);
                      setChangePasswordData({ currentPassword: '', newPassword: '', confirmPassword: '' });
                    } catch (error: unknown) {
                      const message = error instanceof Error ? error.message : 'Failed to change password';
                      showToast(message, 'error');
                    } finally {
                      setChangePasswordLoading(false);
                    }
                  }}
                  disabled={changePasswordLoading || !changePasswordData.currentPassword || !changePasswordData.newPassword || changePasswordData.newPassword !== changePasswordData.confirmPassword || changePasswordData.newPassword.length < 6}
                >
                  {changePasswordLoading ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('settings.changing')}
                    </>
                  ) : (
                    <>
                      <Key className="w-4 h-4" />
                      {t('settings.changePassword')}
                    </>
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
      </div>
      </div>
    </div>
    </CardDensityProvider>
  );
}
