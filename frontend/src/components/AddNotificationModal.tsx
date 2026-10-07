import { useState, useEffect } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Save, Loader2, Send, CheckCircle, XCircle } from 'lucide-react';
import { api } from '../api/client';
import type { NotificationProvider, NotificationProviderCreate, NotificationProviderUpdate, ProviderType, TelegramVerdictMode } from '../api/client';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { isNotifyPushOnlyTarget, isNotifyPhotoUnsupported } from '../utils/notify';

interface ConfigField {
  key: string;
  label: string;
  type: string;
  required: boolean;
  placeholder?: string;
  help?: string;
  options?: { value: string; label: string }[];
  showIf?: (config: Record<string, string>) => boolean;
  advanced?: boolean;
  pattern?: string;
}

const NOTIFY_METRICS = ['progress', 'eta', 'layers', 'nozzle', 'bed', 'chamber'] as const;
type NotifyMetric = (typeof NOTIFY_METRICS)[number];

interface AddNotificationModalProps {
  provider?: NotificationProvider | null;
  onClose: () => void;
}

const PROVIDER_VALUES: ProviderType[] = ['email', 'telegram', 'discord', 'ntfy', 'pushover', 'bark', 'gotify', 'callmebot', 'webhook', 'homeassistant', 'notify'];

export function AddNotificationModal({ provider, onClose }: AddNotificationModalProps) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const isEditing = !!provider;

  const [name, setName] = useState(provider?.name || '');
  const [providerType, setProviderType] = useState<ProviderType>(provider?.provider_type || 'email');
  const [printerId, setPrinterId] = useState<number | null>(provider?.printer_id || null);
  const [attachPhoto, setAttachPhoto] = useState(
    provider?.provider_type === 'notify' && isNotifyPhotoUnsupported(String(provider.config.device_id || ''))
      ? false
      : provider?.attach_photo ?? true
  );
  const [quietHoursEnabled, setQuietHoursEnabled] = useState(provider?.quiet_hours_enabled || false);
  const [quietHoursStart, setQuietHoursStart] = useState(provider?.quiet_hours_start || '22:00');
  const [quietHoursEnd, setQuietHoursEnd] = useState(provider?.quiet_hours_end || '07:00');

  // Daily digest
  const [dailyDigestEnabled, setDailyDigestEnabled] = useState(provider?.daily_digest_enabled || false);
  const [dailyDigestTime, setDailyDigestTime] = useState(provider?.daily_digest_time || '08:00');

  // Event toggles
  const [onPrintStart, setOnPrintStart] = useState(provider?.on_print_start ?? false);
  const [onPrintComplete, setOnPrintComplete] = useState(provider?.on_print_complete ?? true);
  const [onPrintFailed, setOnPrintFailed] = useState(provider?.on_print_failed ?? true);
  const [onPrintStopped, setOnPrintStopped] = useState(provider?.on_print_stopped ?? true);
  const [onPrintProgress, setOnPrintProgress] = useState(provider?.on_print_progress ?? false);
  const [onBillingChargeFailed, setOnBillingChargeFailed] = useState(provider?.on_billing_charge_failed ?? true);
  const [onPrinterOffline, setOnPrinterOffline] = useState(provider?.on_printer_offline ?? false);
  const [onPrinterError, setOnPrinterError] = useState(provider?.on_printer_error ?? false);
  const [onAiFailureDetection, setOnAiFailureDetection] = useState(provider?.on_ai_failure_detection ?? false);
  const [onFilamentLow, setOnFilamentLow] = useState(provider?.on_filament_low ?? false);
  const [onMaintenanceDue, setOnMaintenanceDue] = useState(provider?.on_maintenance_due ?? false);
  const [onStockReorderAlert, setOnStockReorderAlert] = useState(provider?.on_stock_reorder_alert ?? false);
  const [onStockBreakAlert, setOnStockBreakAlert] = useState(provider?.on_stock_break_alert ?? false);
  const [onPlateClearRequired, setOnPlateClearRequired] = useState(provider?.on_plate_clear_required ?? false);
  // Post-print outcome confirmation (#1898). Defaults ON — it only fires for
  // prints that opted in per-job, so the toggle exists to mute a channel.
  const [onPrintConfirmRequest, setOnPrintConfirmRequest] = useState(provider?.on_print_confirm_request ?? true);
  // Telegram only (#3046): inline link buttons, a thumbs reaction, or both.
  const [telegramVerdictMode, setTelegramVerdictMode] = useState<TelegramVerdictMode>(
    provider?.telegram_verdict_mode ?? 'buttons'
  );
  const [onBedCooled, setOnBedCooled] = useState(provider?.on_bed_cooled ?? false);
  const [onHaSensorAlert, setOnHaSensorAlert] = useState(provider?.on_ha_sensor_alert ?? false);
  const [onLocationHaSensorAlert, setOnLocationHaSensorAlert] = useState(
    provider?.on_location_ha_sensor_alert ?? false
  );
  const [onFirstLayerComplete, setOnFirstLayerComplete] = useState(provider?.on_first_layer_complete ?? false);
  const [onAppMessage, setOnAppMessage] = useState(provider?.on_app_message ?? false);

  const [notifyLiveActivities, setNotifyLiveActivities] = useState(
    provider?.config?.live_activities === true && !isNotifyPushOnlyTarget(String(provider?.config?.device_id || ''))
  );

  const [notifyLockScreenWidgets, setNotifyLockScreenWidgets] = useState(
    provider?.config?.lock_screen_widgets === true && !isNotifyPushOnlyTarget(String(provider?.config?.device_id || ''))
  );

  const [notifyMetrics, setNotifyMetrics] = useState<NotifyMetric[]>(() => {
    const metrics = provider?.config?.live_activity_metrics;
    return Array.isArray(metrics) ? NOTIFY_METRICS.filter((metric) => metrics.includes(metric)) : [];
  });

  // Provider-specific config (scalar fields only — event_priorities is split out
  // into its own state because it's an object, not a string).
  const [config, setConfig] = useState<Record<string, string>>(
    provider?.config
      ? Object.fromEntries(
          Object.entries(provider.config)
            .filter(([k]) => k !== 'event_priorities' && k !== 'live_activities' && k !== 'live_activity_metrics' && k !== 'lock_screen_widgets')
            .map(([k, v]) => [k, String(v)]),
        )
      : {},
  );

  // Per-event priority for ntfy (#990) and Gotify (#2743). Map of event key →
  // 1-5. Persisted into config.event_priorities on save for those two only.
  const initialEventPriorities = (() => {
    const raw = provider?.config?.event_priorities;
    if (!raw || typeof raw !== 'object') return {} as Record<string, number>;
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      const n = Number(v);
      if (Number.isInteger(n) && n >= 1 && n <= 5) out[k] = n;
    }
    return out;
  })();
  const [eventPriorities, setEventPriorities] = useState<Record<string, number>>(initialEventPriorities);

  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch printers for linking
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const notifyPushOnlyTarget = providerType === 'notify' && isNotifyPushOnlyTarget(config.device_id || '');
  const notifyPhotosUnsupported = providerType === 'notify' && isNotifyPhotoUnsupported(config.device_id || '');
  const configForRequest = () => providerType === 'notify'
    ? {
        ...config,
        live_activities: notifyLiveActivities && !notifyPushOnlyTarget,
        lock_screen_widgets: notifyLockScreenWidgets && !notifyPushOnlyTarget,
        live_activity_privacy: config.live_activity_privacy === 'true',
        live_activity_stage: config.live_activity_stage === 'true',
        live_activity_metrics: notifyMetrics,
        time_sensitive: config.time_sensitive === 'true',
      }
    : config;

  // Test configuration mutation
  const testMutation = useMutation({
    mutationFn: () => api.testNotificationConfig({ provider_type: providerType, config: configForRequest(), attach_photo: attachPhoto && !notifyPhotosUnsupported }),
    onSuccess: (result) => {
      setTestResult(result);
      setError(null);
    },
    onError: (err: Error) => {
      setTestResult({ success: false, message: err.message });
    },
  });

  // Create mutation
  const createMutation = useMutation({
    mutationFn: (data: NotificationProviderCreate) => api.createNotificationProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: NotificationProviderUpdate) => api.updateNotificationProvider(provider!.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
      onClose();
    },
    onError: (err: Error) => {
      setError(err.message);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError(t('notifications.nameRequired'));
      return;
    }

    // Validate provider-specific config
    const requiredFields = getRequiredFields(providerType);
    for (const field of requiredFields) {
      if (!config[field.key]?.trim()) {
        setError(t('notifications.fieldRequired', { field: field.label }));
        return;
      }
    }

    // HA custom service-data must be a JSON object (#1441)
    if (providerType === 'homeassistant' && config.data?.trim()) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(config.data);
      } catch {
        setError(t('notifications.haDataInvalid'));
        return;
      }
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        setError(t('notifications.haDataInvalid'));
        return;
      }
    }

    // Telegram forum topic must be a plain integer (#1518) — type="number"
    // still lets "1e5" and "-" through, and Telegram would 400 on those.
    if (providerType === 'telegram' && config.message_thread_id?.trim()) {
      if (!/^\d+$/.test(config.message_thread_id.trim())) {
        setError(t('notifications.telegramThreadIdInvalid'));
        return;
      }
    }

    const finalConfig: Record<string, unknown> =
      (providerType === 'ntfy' || providerType === 'gotify') && Object.keys(eventPriorities).length > 0
        ? { ...config, event_priorities: eventPriorities }
        : configForRequest();

    const data = {
      name: name.trim(),
      provider_type: providerType,
      config: finalConfig,
      printer_id: printerId,
      attach_photo: attachPhoto && !notifyPhotosUnsupported,
      quiet_hours_enabled: quietHoursEnabled,
      quiet_hours_start: quietHoursEnabled ? quietHoursStart : null,
      quiet_hours_end: quietHoursEnabled ? quietHoursEnd : null,
      // Daily digest
      daily_digest_enabled: dailyDigestEnabled,
      daily_digest_time: dailyDigestEnabled ? dailyDigestTime : null,
      // Event toggles
      on_print_start: onPrintStart,
      on_print_complete: onPrintComplete,
      on_print_failed: onPrintFailed,
      on_print_stopped: onPrintStopped,
      on_print_progress: onPrintProgress,
      on_billing_charge_failed: onBillingChargeFailed,
      on_printer_offline: onPrinterOffline,
      on_printer_error: onPrinterError,
      on_ai_failure_detection: onAiFailureDetection,
      on_filament_low: onFilamentLow,
      on_maintenance_due: onMaintenanceDue,
      on_stock_reorder_alert: onStockReorderAlert,
      on_stock_break_alert: onStockBreakAlert,
      on_plate_clear_required: onPlateClearRequired,
      on_print_confirm_request: onPrintConfirmRequest,
      telegram_verdict_mode: providerType === 'telegram' ? telegramVerdictMode : 'buttons',
      on_bed_cooled: onBedCooled,
      on_ha_sensor_alert: onHaSensorAlert,
      on_location_ha_sensor_alert: onLocationHaSensorAlert,
      on_first_layer_complete: onFirstLayerComplete,
      on_app_message: onAppMessage,
    };

    if (isEditing) {
      updateMutation.mutate(data);
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  // Get config fields for each provider type
  const getConfigFields = (type: ProviderType): ConfigField[] => {
    switch (type) {
      case 'callmebot':
        return [
          { key: 'phone', label: 'Phone Number', placeholder: '+1234567890', type: 'text', required: true },
          { key: 'apikey', label: 'API Key', placeholder: 'Your CallMeBot API key', type: 'text', required: true },
        ];
      case 'ntfy':
        return [
          { key: 'server', label: 'Server URL', placeholder: 'https://ntfy.sh', type: 'text', required: false },
          { key: 'topic', label: 'Topic', placeholder: 'my-bambuddy', type: 'text', required: true },
          { key: 'auth_token', label: 'Auth Token', placeholder: 'Optional authentication', type: 'password', required: false },
        ];
      case 'pushover':
        return [
          { key: 'user_key', label: 'User Key', placeholder: 'Your Pushover user key', type: 'text', required: true },
          { key: 'app_token', label: 'App Token', placeholder: 'Your Pushover app token', type: 'text', required: true },
          { key: 'priority', label: 'Priority', placeholder: '0 (normal)', type: 'number', required: false },
          // Emergency priority (2) requires retry/expire — Pushover rejects the
          // message otherwise. Only shown when priority is set to 2.
          {
            key: 'retry',
            label: t('notifications.pushoverRetry'),
            placeholder: '60',
            type: 'number',
            required: false,
            showIf: (cfg: Record<string, string>) => cfg.priority === '2',
          },
          {
            key: 'expire',
            label: t('notifications.pushoverExpire'),
            placeholder: '3600',
            type: 'number',
            required: false,
            showIf: (cfg: Record<string, string>) => cfg.priority === '2',
          },
        ];
      case 'notify':
        return [
          { key: 'device_id', label: t('notifications.notifyDeviceId'), type: 'text', required: true },
          { key: 'token', label: t('notifications.notifyToken'), type: 'password', required: true },
          { key: 'icon_url', label: t('notifications.notifyIconUrl'), placeholder: 'https://example.com/icon.png', type: 'url', pattern: 'https://.*', required: false },
          { key: 'group_type', label: t('notifications.notifyGroupType'), type: 'text', required: false, help: t('notifications.notifyGroupTypeHelp') },
          {
            key: 'time_sensitive', label: t('notifications.notifyTimeSensitive'), type: 'select', required: false,
            options: [{ value: 'false', label: t('common.no') }, { value: 'true', label: t('common.yes') }],
            help: t('notifications.notifyTimeSensitiveHelp'),
          },
          {
            key: 'live_activity_privacy', label: t('notifications.notifyPrivacy'), type: 'select', required: false, advanced: true,
            options: [{ value: 'false', label: t('common.no') }, { value: 'true', label: t('common.yes') }],
            help: t('notifications.notifyPrivacyHelp'),
          },
          {
            key: 'live_activity_stage', label: t('notifications.notifyShowStage'), type: 'select', required: false, advanced: true,
            options: [{ value: 'false', label: t('common.no') }, { value: 'true', label: t('common.yes') }],
          },
          {
            key: 'live_activity_style', label: t('notifications.notifyStyle'), type: 'select', required: false, advanced: true,
            options: [
              { value: 'bar', label: t('notifications.notifyStyleBar') },
              { value: 'segments', label: t('notifications.notifyStyleSegments') },
              { value: 'none', label: t('notifications.notifyStyleNone') },
            ],
          },
          { key: 'live_activity_button_url', label: t('notifications.notifyDashboardUrl'), type: 'url', pattern: 'https://.*', placeholder: 'https://bambuddy.example.com', required: false, advanced: true },
          { key: 'live_activity_symbol', label: t('notifications.notifySymbol'), type: 'text', placeholder: 'printer.fill', required: false, advanced: true },
          { key: 'live_activity_tint', label: t('notifications.notifyTint'), type: 'text', placeholder: '#00AE42', pattern: '#[0-9a-fA-F]{6}', required: false, advanced: true },
        ];
      case 'telegram':
        return [
          { key: 'bot_token', label: 'Bot Token', placeholder: 'Bot token from @BotFather', type: 'password', required: true },
          { key: 'chat_id', label: 'Chat ID', placeholder: 'Your chat or group ID', type: 'text', required: true },
          // Optional forum topic (#1518). Left empty, Telegram posts to the
          // group's General topic exactly as before.
          {
            key: 'message_thread_id',
            label: t('notifications.telegramThreadId'),
            placeholder: '123',
            type: 'number',
            required: false,
            help: t('notifications.telegramThreadIdHelp'),
          },
        ];
      case 'email':
        return [
          { key: 'smtp_server', label: 'SMTP Server', placeholder: 'smtp.gmail.com', type: 'text', required: true },
          { key: 'smtp_port', label: 'SMTP Port', placeholder: '587', type: 'number', required: false },
          { key: 'security', label: 'Security', type: 'select', required: false, options: [
            { value: 'starttls', label: 'STARTTLS (Port 587)' },
            { value: 'ssl', label: 'SSL/TLS (Port 465)' },
            { value: 'none', label: 'None (Port 25)' },
          ]},
          { key: 'auth_enabled', label: 'Authentication', type: 'select', required: false, options: [
            { value: 'true', label: 'Enabled' },
            { value: 'false', label: 'Disabled' },
          ]},
          { key: 'username', label: 'Username', placeholder: 'your@email.com', type: 'text', required: false },
          { key: 'password', label: 'Password', placeholder: 'App password', type: 'password', required: false },
          { key: 'from_email', label: 'From Email', placeholder: 'your@email.com', type: 'text', required: true },
          { key: 'to_email', label: 'To Email', placeholder: 'recipient@email.com', type: 'text', required: true },
        ];
      case 'discord':
        return [
          { key: 'webhook_url', label: 'Webhook URL', placeholder: 'https://discord.com/api/webhooks/...', type: 'text', required: true },
        ];
      case 'webhook':
        return [
          { key: 'webhook_url', label: 'Webhook URL', placeholder: 'https://example.com/webhook', type: 'text', required: true },
          { key: 'payload_format', label: 'Payload Format', type: 'select', required: false, options: [
            { value: 'generic', label: 'Generic JSON' },
            { value: 'slack', label: 'Slack / Mattermost' },
          ]},
          { key: 'auth_header', label: 'Authorization', placeholder: 'Bearer token (optional)', type: 'password', required: false },
          { key: 'field_title', label: 'Title Field Name', placeholder: 'title', type: 'text', required: false, showIf: (cfg: Record<string, string>) => cfg.payload_format !== 'slack' },
          { key: 'field_message', label: 'Message Field Name', placeholder: 'message', type: 'text', required: false, showIf: (cfg: Record<string, string>) => cfg.payload_format !== 'slack' },
        ];
      case 'homeassistant':
        return [
          {
            key: 'service',
            label: 'Home Assistant Service',
            placeholder: 'notify.mobile_app_myphone',
            type: 'text',
            required: false,
            help: t('notifications.haServiceHelp'),
          },
          { key: 'data', label: 'Data (JSON, optional)', placeholder: '{"priority": "high", "ttl": 0, "channel": "3D Printing"}', type: 'textarea', required: false },
        ];
      case 'bark':
        return [
          { key: 'device_key', label: 'Device Key', placeholder: 'Your Bark device key', type: 'text', required: true },
          { key: 'server', label: 'Server URL', placeholder: 'https://api.day.app', type: 'text', required: false },
          { key: 'group', label: 'Group', placeholder: 'Bambuddy', type: 'text', required: false },
          { key: 'sound', label: 'Sound', placeholder: 'minuet', type: 'text', required: false },
          { key: 'level', label: 'Interruption Level', type: 'select', required: false, options: [
            { value: '', label: 'Default' },
            { value: 'active', label: 'Active' },
            { value: 'timeSensitive', label: 'Time Sensitive' },
            { value: 'critical', label: 'Critical (bypasses Silent/Focus)' },
            { value: 'passive', label: 'Passive (no sound)' },
          ]},
        ];
      case 'gotify':
        return [
          { key: 'server', label: 'Server URL', placeholder: 'https://gotify.example.com', type: 'text', required: true },
          { key: 'app_token', label: 'App Token', placeholder: 'Token of a Gotify application', type: 'password', required: true },
        ];
      default:
        return [];
    }
  };

  const getRequiredFields = (type: ProviderType) => {
    return getConfigFields(type).filter(f => f.required);
  };

  const configFields = getConfigFields(providerType);

  const renderConfigFields = (fields: ConfigField[]) =>
    fields
      .filter((field) => field.showIf?.(config) !== false)
      .map((field) => (
        <div key={field.key}>
          <label htmlFor={`notification-config-${field.key}`} className="block text-sm text-bambu-gray mb-1">
            {field.label} {field.required && '*'}
          </label>
          {field.type === 'select' && field.options ? (
            <select
              id={`notification-config-${field.key}`}
              value={config[field.key] || field.options[0]?.value || ''}
              onChange={(e) => {
                setConfig({ ...config, [field.key]: e.target.value });
                setTestResult(null);
              }}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              {field.options.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          ) : field.type === 'textarea' ? (
            <textarea
              id={`notification-config-${field.key}`}
              value={config[field.key] || ''}
              onChange={(e) => {
                setConfig({ ...config, [field.key]: e.target.value });
                setTestResult(null);
              }}
              placeholder={field.placeholder}
              rows={3}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none font-mono text-sm"
            />
          ) : (
            <input
              id={`notification-config-${field.key}`}
              type={field.type}
              pattern={field.pattern}
              value={config[field.key] || ''}
              onChange={(e) => {
                setConfig({ ...config, [field.key]: e.target.value });
                if (providerType === 'notify' && field.key === 'device_id' && isNotifyPushOnlyTarget(e.target.value)) {
                  setNotifyLiveActivities(false);
                  setNotifyLockScreenWidgets(false);
                }
                if (providerType === 'notify' && field.key === 'device_id' && isNotifyPhotoUnsupported(e.target.value)) {
                  setAttachPhoto(false);
                }
                setTestResult(null);
              }}
              placeholder={field.placeholder}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          )}
          {field.help && (
            <p className="text-xs text-bambu-gray mt-1">{field.help}</p>
          )}
        </div>
      ));

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4 overflow-y-auto"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg my-8 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            {isEditing ? t('notifications.editTitle') : t('notifications.addTitle')}
          </h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="p-3 bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/50 rounded-lg text-sm text-red-700 dark:text-red-400">
              {error}
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('notifications.nameLabel')}</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('notifications.namePlaceholder')}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Provider Type */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('notifications.providerTypeLabel')}</label>
            <select
              value={providerType}
              onChange={(e) => {
                setProviderType(e.target.value as ProviderType);
                setConfig({}); // Reset config when changing type
                setNotifyLiveActivities(false);
                setNotifyLockScreenWidgets(false);
                setNotifyMetrics([]);
                setTestResult(null);
              }}
              disabled={isEditing}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
            >
              {/* Sorted by the label shown, so the order holds in every language */}
              {PROVIDER_VALUES.map((value) => ({ value, label: t(`notifications.providerTypes.${value}`, value) }))
                .sort((a, b) => a.label.localeCompare(b.label, i18n.language, { sensitivity: 'base' }))
                .map(({ value, label }) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {t(`notifications.providerDescriptions.${providerType}`, '')}
            </p>
          </div>

          {/* Provider-specific configuration */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">{t('notifications.configuration')}</p>
            {renderConfigFields(configFields.filter((field) => !field.advanced))}
            {providerType === 'notify' && (
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3" role="group" aria-label={t('notifications.notifyLiveActivities')}>
                  <div>
                    <label className="text-sm text-white">{t('notifications.notifyLiveActivities')}</label>
                    <p className="text-xs text-bambu-gray">{t('notifications.notifyLiveActivitiesHelp')}</p>
                  </div>
                  <Toggle
                    checked={notifyLiveActivities && !notifyPushOnlyTarget}
                    disabled={notifyPushOnlyTarget}
                    onChange={(checked) => {
                      setNotifyLiveActivities(checked);
                      setTestResult(null);
                      setError(null);
                    }}
                  />
                </div>
                {notifyLiveActivities && (
                  <>
                    <p className="text-xs text-bambu-gray">{t('notifications.notifyLifecycleHelp')}</p>
                    <details className="rounded-lg border border-bambu-dark-tertiary p-3">
                      <summary className="cursor-pointer text-sm text-white">{t('notifications.notifyAppearance')}</summary>
                      <div className="mt-3 space-y-3">
                        {renderConfigFields(configFields.filter((field) => field.advanced))}
                        <fieldset>
                          <legend className="text-sm text-bambu-gray mb-1">{t('notifications.notifyMetrics')}</legend>
                          <p className="text-xs text-bambu-gray mb-2">{t('notifications.notifyMetricsHelp')}</p>
                          <div className="flex flex-wrap gap-3">
                            {NOTIFY_METRICS.map((metric) => (
                              <label key={metric} className="flex items-center gap-1 text-sm text-white">
                                <input
                                  type="checkbox"
                                  checked={notifyMetrics.includes(metric)}
                                  onChange={(e) => {
                                    setNotifyMetrics(e.target.checked
                                      ? [...notifyMetrics, metric]
                                      : notifyMetrics.filter((selected) => selected !== metric));
                                    setTestResult(null);
                                  }}
                                  className="accent-bambu-green"
                                />
                                {t(metric === 'progress' ? 'notifications.progress'
                                  : metric === 'eta' ? 'streamOverlay.eta'
                                  : metric === 'layers' ? 'streamOverlay.layer'
                                  : `printers.temperatures.${metric}`)}
                              </label>
                            ))}
                          </div>
                        </fieldset>
                      </div>
                    </details>
                  </>
                )}
                <div className="flex items-center justify-between gap-3" role="group" aria-label={t('notifications.notifyLockScreenWidgets')}>
                  <div>
                    <label className="text-sm text-white">{t('notifications.notifyLockScreenWidgets')}</label>
                    <p className="text-xs text-bambu-gray">{t('notifications.notifyLockScreenWidgetsHelp')}</p>
                  </div>
                  <Toggle
                    checked={notifyLockScreenWidgets && !notifyPushOnlyTarget}
                    disabled={notifyPushOnlyTarget}
                    onChange={(checked) => {
                      setNotifyLockScreenWidgets(checked);
                      setTestResult(null);
                      setError(null);
                    }}
                  />
                </div>
                {notifyPushOnlyTarget && (
                  <p className="text-xs text-bambu-gray">{t('notifications.notifyIosFeaturesUnavailable')}</p>
                )}
                <p className="text-xs text-bambu-gray">{t('notifications.notifyTestHelp')}</p>
              </div>
            )}
            {providerType === 'telegram' && (
              <div>
                <label htmlFor="telegram-verdict-mode" className="block text-sm text-bambu-gray mb-1">
                  {t('notifications.telegramVerdictMode')}
                </label>
                <select
                  id="telegram-verdict-mode"
                  value={telegramVerdictMode}
                  onChange={(e) => setTelegramVerdictMode(e.target.value as TelegramVerdictMode)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                >
                  <option value="buttons">{t('notifications.telegramVerdictModeButtons')}</option>
                  <option value="reactions">{t('notifications.telegramVerdictModeReactions')}</option>
                  <option value="both">{t('notifications.telegramVerdictModeBoth')}</option>
                </select>
                <p className="text-xs text-bambu-gray mt-1">{t('notifications.telegramVerdictModeHelp')}</p>
              </div>
            )}
          </div>

          {/* Test Button */}
          <div className="flex gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => {
                setTestResult(null);
                testMutation.mutate();
              }}
              disabled={testMutation.isPending || getRequiredFields(providerType).some((field) => !config[field.key]?.trim())}
              className="flex-1"
            >
              {testMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('notifications.testConfiguration')}
            </Button>
          </div>

          {/* Test Result */}
          {testResult && (
            <div className={`p-3 rounded-lg flex items-center gap-2 ${
              testResult.success
                ? 'bg-bambu-green/20 border border-bambu-green/50 text-bambu-green'
                : 'bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/50 text-red-700 dark:text-red-400'
            }`}>
              {testResult.success ? (
                <>
                  <CheckCircle className="w-5 h-5" />
                  <span>{testResult.message}</span>
                </>
              ) : (
                <>
                  <XCircle className="w-5 h-5" />
                  <span>{testResult.message}</span>
                </>
              )}
            </div>
          )}

          {/* Link to Printer */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('notifications.printerFilter')}</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">{t('notifications.allPrinters')}</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {t('notifications.onlyFromPrinter')}
            </p>
          </div>

          {/* Attach Photo */}
          <div className="flex items-center justify-between">
            <div>
              <label className="text-sm text-white">{t('notifications.attachPhotoLabel')}</label>
              <p className="text-xs text-bambu-gray">{t(notifyPhotosUnsupported ? 'notifications.notifyPhotoUnavailable' : 'notifications.attachPhotoDescription')}</p>
            </div>
            <Toggle
              checked={attachPhoto && !notifyPhotosUnsupported}
              disabled={notifyPhotosUnsupported}
              onChange={setAttachPhoto}
            />
          </div>

          {/* Quiet Hours */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm text-white">{t('notifications.quietHoursDnd')}</label>
              <Toggle
                checked={quietHoursEnabled}
                onChange={setQuietHoursEnabled}
              />
            </div>
            {quietHoursEnabled && (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">{t('notifications.quietStart')}</label>
                  <input
                    type="time"
                    value={quietHoursStart}
                    onChange={(e) => setQuietHoursStart(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">{t('notifications.quietEnd')}</label>
                  <input
                    type="time"
                    value={quietHoursEnd}
                    onChange={(e) => setQuietHoursEnd(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
              </div>
            )}
          </div>

          {/* Daily Digest */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <label className="text-sm text-white">{t('notifications.dailyDigestLabel')}</label>
                <p className="text-xs text-bambu-gray">{t('notifications.batchNotifications')}</p>
              </div>
              <Toggle
                checked={dailyDigestEnabled}
                onChange={setDailyDigestEnabled}
              />
            </div>
            {dailyDigestEnabled && (
              <div>
                <label className="block text-xs text-bambu-gray mb-1">{t('notifications.sendDigestAt')}</label>
                <input
                  type="time"
                  value={dailyDigestTime}
                  onChange={(e) => setDailyDigestTime(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-1">
                  {t('notifications.digestCollected')}
                </p>
              </div>
            )}
          </div>

          {/* Event Toggles */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">{t('notifications.notificationEvents')}</p>

            {/* Print Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('notifications.printEvents')}</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.start')}</span>
                  <Toggle checked={onPrintStart} onChange={setOnPrintStart} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.complete')}</span>
                  <Toggle checked={onPrintComplete} onChange={setOnPrintComplete} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.failed')}</span>
                  <Toggle checked={onPrintFailed} onChange={setOnPrintFailed} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.stopped')}</span>
                  <Toggle checked={onPrintStopped} onChange={setOnPrintStopped} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.progress')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.progressPercent')}</span>
                  </div>
                  <Toggle checked={onPrintProgress} onChange={setOnPrintProgress} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.billingChargeFailedLabel')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.billingChargeFailedDescription')}</span>
                  </div>
                  <Toggle checked={onBillingChargeFailed} onChange={setOnBillingChargeFailed} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.plateClearRequired')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.plateClearRequiredDescription')}</span>
                  </div>
                  <Toggle checked={onPlateClearRequired} onChange={setOnPlateClearRequired} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.printConfirmRequest')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.printConfirmRequestDescription')}</span>
                  </div>
                  <Toggle checked={onPrintConfirmRequest} onChange={setOnPrintConfirmRequest} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.bedCooled')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.bedCooledAfterPrint')}</span>
                  </div>
                  <Toggle checked={onBedCooled} onChange={setOnBedCooled} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.firstLayerCompleteLabel')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.firstLayerCompleteDescription')}</span>
                  </div>
                  <Toggle checked={onFirstLayerComplete} onChange={setOnFirstLayerComplete} />
                </div>
              </div>
            </div>

            {/* Printer Status Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('notifications.printerStatus')}</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.offline')}</span>
                  <Toggle checked={onPrinterOffline} onChange={setOnPrinterOffline} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.haSensorAlert')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.haSensorAlertDescription')}</span>
                  </div>
                  <Toggle checked={onHaSensorAlert} onChange={setOnHaSensorAlert} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('notifications.locationHaSensorAlert')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.locationHaSensorAlertDescription')}</span>
                  </div>
                  <Toggle checked={onLocationHaSensorAlert} onChange={setOnLocationHaSensorAlert} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.error')}</span>
                  <Toggle checked={onPrinterError} onChange={setOnPrinterError} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.aiFailureDetection')}</span>
                  <Toggle checked={onAiFailureDetection} onChange={setOnAiFailureDetection} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.lowFilament')}</span>
                  <Toggle checked={onFilamentLow} onChange={setOnFilamentLow} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('notifications.maintenance')}</span>
                  <Toggle checked={onMaintenanceDue} onChange={setOnMaintenanceDue} />
                </div>
              </div>
            </div>

            {/* Inventory Stock Alerts */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('notifications.inventoryAlerts')}</p>
              <div className="grid grid-cols-1 gap-2">
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-sm text-white">{t('notifications.stockReorderAlert')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.stockReorderAlertDescription')}</span>
                  </div>
                  <Toggle checked={onStockReorderAlert} onChange={setOnStockReorderAlert} />
                </div>
                <div className="flex items-center justify-between">
                  <div>
                    <span className="text-sm text-white">{t('notifications.stockBreakAlert')}</span>
                    <span className="text-xs text-bambu-gray ml-1">{t('notifications.stockBreakAlertDescription')}</span>
                  </div>
                  <Toggle checked={onStockBreakAlert} onChange={setOnStockBreakAlert} />
                </div>
              </div>
            </div>

            {/* Messages other applications send (POST /notifications/app-message) */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('notifications.connectedApps')}</p>
              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm text-white">{t('notifications.appMessages')}</span>
                  <span className="text-xs text-bambu-gray ml-1">{t('notifications.appMessagesDescription')}</span>
                </div>
                <Toggle checked={onAppMessage} onChange={setOnAppMessage} />
              </div>
            </div>

            {/* Per-event priority: ntfy (#990), Gotify (#2743) */}
            {(providerType === 'ntfy' || providerType === 'gotify') && (() => {
              const enabledEvents: Array<{ key: string; label: string }> = [];
              if (onPrintStart) enabledEvents.push({ key: 'on_print_start', label: t('notifications.start') });
              if (onPrintComplete) enabledEvents.push({ key: 'on_print_complete', label: t('notifications.complete') });
              if (onPrintFailed) enabledEvents.push({ key: 'on_print_failed', label: t('notifications.failed') });
              if (onPrintStopped) enabledEvents.push({ key: 'on_print_stopped', label: t('notifications.stopped') });
              if (onPrintProgress) enabledEvents.push({ key: 'on_print_progress', label: t('notifications.progress') });
              if (onBillingChargeFailed) enabledEvents.push({ key: 'on_billing_charge_failed', label: t('notifications.billingChargeFailedLabel') });
              if (onPlateClearRequired) enabledEvents.push({ key: 'on_plate_clear_required', label: t('notifications.plateClearRequired') });
              if (onPrintConfirmRequest) enabledEvents.push({ key: 'on_print_confirm_request', label: t('notifications.printConfirmRequest') });
              if (onBedCooled) enabledEvents.push({ key: 'on_bed_cooled', label: t('notifications.bedCooled') });
              if (onFirstLayerComplete) enabledEvents.push({ key: 'on_first_layer_complete', label: t('notifications.firstLayerCompleteLabel') });
              if (onPrinterOffline) enabledEvents.push({ key: 'on_printer_offline', label: t('notifications.offline') });
              if (onPrinterError) enabledEvents.push({ key: 'on_printer_error', label: t('notifications.error') });
              if (onHaSensorAlert) enabledEvents.push({ key: 'on_ha_sensor_alert', label: t('notifications.haSensorAlert') });
              if (onLocationHaSensorAlert) enabledEvents.push({ key: 'on_location_ha_sensor_alert', label: t('notifications.locationHaSensorAlert') });
              if (onAiFailureDetection) enabledEvents.push({ key: 'on_ai_failure_detection', label: t('notifications.aiFailureDetection') });
              if (onFilamentLow) enabledEvents.push({ key: 'on_filament_low', label: t('notifications.lowFilament') });
              if (onMaintenanceDue) enabledEvents.push({ key: 'on_maintenance_due', label: t('notifications.maintenance') });
              if (onStockReorderAlert) enabledEvents.push({ key: 'on_stock_reorder_alert', label: t('notifications.stockReorderAlert') });
              if (onStockBreakAlert) enabledEvents.push({ key: 'on_stock_break_alert', label: t('notifications.stockBreakAlert') });

              if (enabledEvents.length === 0) return null;

              return (
                <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
                  <p className="text-xs text-bambu-gray uppercase tracking-wide mb-1">
                    {providerType === 'gotify'
                      ? t('notifications.eventPriority.sectionTitleGotify')
                      : t('notifications.eventPriority.sectionTitle')}
                  </p>
                  <p className="text-xs text-bambu-gray mb-2">
                    {providerType === 'gotify'
                      ? t('notifications.eventPriority.helpGotify')
                      : t('notifications.eventPriority.helpNtfy')}
                  </p>
                  <div className="space-y-2">
                    {enabledEvents.map((ev) => (
                      <div key={ev.key} className="flex items-center justify-between gap-3">
                        <span className="text-sm text-white">{ev.label}</span>
                        <select
                          value={eventPriorities[ev.key] ?? 3}
                          onChange={(e) => {
                            const next = Number(e.target.value);
                            setEventPriorities((prev) => ({ ...prev, [ev.key]: next }));
                          }}
                          className="px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-sm text-white focus:border-bambu-green focus:outline-none"
                        >
                          <option value={1}>{t('notifications.eventPriority.min')}</option>
                          <option value={2}>{t('notifications.eventPriority.low')}</option>
                          <option value={3}>{t('notifications.eventPriority.default')}</option>
                          <option value={4}>{t('notifications.eventPriority.high')}</option>
                          <option value={5}>{t('notifications.eventPriority.urgent')}</option>
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              );
            })()}
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              {t('notifications.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={isPending}
              className="flex-1"
            >
              {isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Save className="w-4 h-4" />
              )}
              {isEditing ? t('notifications.save') : t('notifications.add')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
