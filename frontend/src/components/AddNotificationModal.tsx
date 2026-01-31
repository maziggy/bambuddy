import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { X, Save, Loader2, Send, CheckCircle, XCircle } from 'lucide-react';
import { api } from '../api/client';
import type { NotificationProvider, NotificationProviderCreate, NotificationProviderUpdate, ProviderType } from '../api/client';
import { Button } from './Button';
import { Toggle } from './Toggle';

interface AddNotificationModalProps {
  provider?: NotificationProvider | null;
  onClose: () => void;
}

const PROVIDER_TYPES: ProviderType[] = ['email', 'telegram', 'discord', 'ntfy', 'pushover', 'callmebot', 'webhook'];

export function AddNotificationModal({ provider, onClose }: AddNotificationModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const isEditing = !!provider;

  const [name, setName] = useState(provider?.name || '');
  const [providerType, setProviderType] = useState<ProviderType>(provider?.provider_type || 'email');
  const [printerId, setPrinterId] = useState<number | null>(provider?.printer_id || null);
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
  const [onPrinterOffline, setOnPrinterOffline] = useState(provider?.on_printer_offline ?? false);
  const [onPrinterError, setOnPrinterError] = useState(provider?.on_printer_error ?? false);
  const [onFilamentLow, setOnFilamentLow] = useState(provider?.on_filament_low ?? false);
  const [onMaintenanceDue, setOnMaintenanceDue] = useState(provider?.on_maintenance_due ?? false);

  // Provider-specific config
  const [config, setConfig] = useState<Record<string, string>>(
    provider?.config ? Object.fromEntries(Object.entries(provider.config).map(([k, v]) => [k, String(v)])) : {}
  );

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

  // Test configuration mutation
  const testMutation = useMutation({
    mutationFn: () => api.testNotificationConfig({ provider_type: providerType, config }),
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
      setError(t('addNotification.nameRequired'));
      return;
    }

    // Validate provider-specific config
    const requiredFields = getRequiredFields(providerType);
    for (const field of requiredFields) {
      if (!config[field.key]?.trim()) {
        setError(t('addNotification.fieldRequired', { field: field.label }));
        return;
      }
    }

    const data = {
      name: name.trim(),
      provider_type: providerType,
      config,
      printer_id: printerId,
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
      on_printer_offline: onPrinterOffline,
      on_printer_error: onPrinterError,
      on_filament_low: onFilamentLow,
      on_maintenance_due: onMaintenanceDue,
    };

    if (isEditing) {
      updateMutation.mutate(data);
    } else {
      createMutation.mutate(data);
    }
  };

  const isPending = createMutation.isPending || updateMutation.isPending;

  // Get config fields for each provider type
  const getConfigFields = (type: ProviderType) => {
    switch (type) {
      case 'callmebot':
        return [
          { key: 'phone', label: t('addNotification.config.callmebot.phoneNumber'), placeholder: '+1234567890', type: 'text', required: true },
          { key: 'apikey', label: t('addNotification.config.callmebot.apiKey'), placeholder: t('addNotification.config.callmebot.apiKeyPlaceholder'), type: 'text', required: true },
        ];
      case 'ntfy':
        return [
          { key: 'server', label: t('addNotification.config.ntfy.serverUrl'), placeholder: 'https://ntfy.sh', type: 'text', required: false },
          { key: 'topic', label: t('addNotification.config.ntfy.topic'), placeholder: 'my-bambuddy', type: 'text', required: true },
          { key: 'auth_token', label: t('addNotification.config.ntfy.authToken'), placeholder: t('addNotification.config.ntfy.authTokenPlaceholder'), type: 'password', required: false },
        ];
      case 'pushover':
        return [
          { key: 'user_key', label: t('addNotification.config.pushover.userKey'), placeholder: t('addNotification.config.pushover.userKeyPlaceholder'), type: 'text', required: true },
          { key: 'app_token', label: t('addNotification.config.pushover.appToken'), placeholder: t('addNotification.config.pushover.appTokenPlaceholder'), type: 'text', required: true },
          { key: 'priority', label: t('addNotification.config.pushover.priority'), placeholder: t('addNotification.config.pushover.priorityPlaceholder'), type: 'number', required: false },
        ];
      case 'telegram':
        return [
          { key: 'bot_token', label: t('addNotification.config.telegram.botToken'), placeholder: t('addNotification.config.telegram.botTokenPlaceholder'), type: 'password', required: true },
          { key: 'chat_id', label: t('addNotification.config.telegram.chatId'), placeholder: t('addNotification.config.telegram.chatIdPlaceholder'), type: 'text', required: true },
        ];
      case 'email':
        return [
          { key: 'smtp_server', label: t('addNotification.config.email.smtpServer'), placeholder: 'smtp.gmail.com', type: 'text', required: true },
          { key: 'smtp_port', label: t('addNotification.config.email.smtpPort'), placeholder: '587', type: 'number', required: false },
          { key: 'security', label: t('addNotification.config.email.security'), type: 'select', required: false, options: [
            { value: 'starttls', label: t('addNotification.config.email.starttls') },
            { value: 'ssl', label: t('addNotification.config.email.ssl') },
            { value: 'none', label: t('addNotification.config.email.securityNone') },
          ]},
          { key: 'auth_enabled', label: t('addNotification.config.email.authentication'), type: 'select', required: false, options: [
            { value: 'true', label: t('common.enabled') },
            { value: 'false', label: t('common.disabled') },
          ]},
          { key: 'username', label: t('addNotification.config.email.username'), placeholder: 'your@email.com', type: 'text', required: false },
          { key: 'password', label: t('addNotification.config.email.password'), placeholder: t('addNotification.config.email.passwordPlaceholder'), type: 'password', required: false },
          { key: 'from_email', label: t('addNotification.config.email.fromEmail'), placeholder: 'your@email.com', type: 'text', required: true },
          { key: 'to_email', label: t('addNotification.config.email.toEmail'), placeholder: 'recipient@email.com', type: 'text', required: true },
        ];
      case 'discord':
        return [
          { key: 'webhook_url', label: t('addNotification.config.discord.webhookUrl'), placeholder: 'https://discord.com/api/webhooks/...', type: 'text', required: true },
        ];
      case 'webhook':
        return [
          { key: 'webhook_url', label: t('addNotification.config.webhook.webhookUrl'), placeholder: 'https://example.com/webhook', type: 'text', required: true },
          { key: 'payload_format', label: t('addNotification.config.webhook.payloadFormat'), type: 'select', required: false, options: [
            { value: 'generic', label: t('addNotification.config.webhook.genericJson') },
            { value: 'slack', label: t('addNotification.config.webhook.slackMattermost') },
          ]},
          { key: 'auth_header', label: t('addNotification.config.webhook.authorization'), placeholder: t('addNotification.config.webhook.authorizationPlaceholder'), type: 'password', required: false },
          { key: 'field_title', label: t('addNotification.config.webhook.titleFieldName'), placeholder: 'title', type: 'text', required: false, showIf: (cfg: Record<string, string>) => cfg.payload_format !== 'slack' },
          { key: 'field_message', label: t('addNotification.config.webhook.messageFieldName'), placeholder: 'message', type: 'text', required: false, showIf: (cfg: Record<string, string>) => cfg.payload_format !== 'slack' },
        ];
      default:
        return [];
    }
  };

  const getRequiredFields = (type: ProviderType) => {
    return getConfigFields(type).filter(f => f.required);
  };

  const configFields = getConfigFields(providerType);

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
            {isEditing ? t('addNotification.editTitle') : t('addNotification.addTitle')}
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
            <div className="p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {error}
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('common.name')} *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('addNotification.namePlaceholder')}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Provider Type */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('addNotification.providerType')} *</label>
            <select
              value={providerType}
              onChange={(e) => {
                setProviderType(e.target.value as ProviderType);
                setConfig({}); // Reset config when changing type
                setTestResult(null);
              }}
              disabled={isEditing}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-50"
            >
              {PROVIDER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`providers.${type}`, type)}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {t(`providers.descriptions.${providerType}`)}
            </p>
          </div>

          {/* Provider-specific configuration */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">{t('addNotification.configuration')}</p>
            {configFields
              .filter((field) => !('showIf' in field) || (field as { showIf?: (cfg: Record<string, string>) => boolean }).showIf?.(config) !== false)
              .map((field) => (
              <div key={field.key}>
                <label className="block text-sm text-bambu-gray mb-1">
                  {field.label} {field.required && '*'}
                </label>
                {field.type === 'select' && 'options' in field && field.options ? (
                  <select
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
                ) : (
                  <input
                    type={field.type}
                    value={config[field.key] || ''}
                    onChange={(e) => {
                      setConfig({ ...config, [field.key]: e.target.value });
                      setTestResult(null);
                    }}
                    placeholder={field.placeholder}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                )}
              </div>
            ))}
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
              disabled={testMutation.isPending || !config[getRequiredFields(providerType)[0]?.key]}
              className="flex-1"
            >
              {testMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('addNotification.testConfiguration')}
            </Button>
          </div>

          {/* Test Result */}
          {testResult && (
            <div className={`p-3 rounded-lg flex items-center gap-2 ${
              testResult.success
                ? 'bg-bambu-green/20 border border-bambu-green/50 text-bambu-green'
                : 'bg-red-500/20 border border-red-500/50 text-red-400'
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
            <label className="block text-sm text-bambu-gray mb-1">{t('addNotification.printerFilter')}</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">{t('addNotification.allPrinters')}</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {t('addNotification.printerFilterDesc')}
            </p>
          </div>

          {/* Quiet Hours */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm text-white">{t('addNotification.quietHours')}</label>
              <Toggle
                checked={quietHoursEnabled}
                onChange={setQuietHoursEnabled}
              />
            </div>
            {quietHoursEnabled && (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">{t('addNotification.start')}</label>
                  <input
                    type="time"
                    value={quietHoursStart}
                    onChange={(e) => setQuietHoursStart(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">{t('addNotification.end')}</label>
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
                <label className="text-sm text-white">{t('addNotification.dailyDigest')}</label>
                <p className="text-xs text-bambu-gray">{t('addNotification.dailyDigestDesc')}</p>
              </div>
              <Toggle
                checked={dailyDigestEnabled}
                onChange={setDailyDigestEnabled}
              />
            </div>
            {dailyDigestEnabled && (
              <div>
                <label className="block text-xs text-bambu-gray mb-1">{t('addNotification.sendDigestAt')}</label>
                <input
                  type="time"
                  value={dailyDigestTime}
                  onChange={(e) => setDailyDigestTime(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-1">
                  {t('addNotification.digestDescription')}
                </p>
              </div>
            )}
          </div>

          {/* Event Toggles */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">{t('addNotification.notificationEvents')}</p>

            {/* Print Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('addNotification.printEvents')}</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.start')}</span>
                  <Toggle checked={onPrintStart} onChange={setOnPrintStart} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.complete')}</span>
                  <Toggle checked={onPrintComplete} onChange={setOnPrintComplete} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.failed')}</span>
                  <Toggle checked={onPrintFailed} onChange={setOnPrintFailed} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.stopped')}</span>
                  <Toggle checked={onPrintStopped} onChange={setOnPrintStopped} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">{t('addNotification.events.progress')}</span>
                    <span className="text-xs text-bambu-gray ml-1">(25%, 50%, 75%)</span>
                  </div>
                  <Toggle checked={onPrintProgress} onChange={setOnPrintProgress} />
                </div>
              </div>
            </div>

            {/* Printer Status Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">{t('addNotification.printerStatus')}</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.offline')}</span>
                  <Toggle checked={onPrinterOffline} onChange={setOnPrinterOffline} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.error')}</span>
                  <Toggle checked={onPrinterError} onChange={setOnPrinterError} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.lowFilament')}</span>
                  <Toggle checked={onFilamentLow} onChange={setOnFilamentLow} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">{t('addNotification.events.maintenance')}</span>
                  <Toggle checked={onMaintenanceDue} onChange={setOnMaintenanceDue} />
                </div>
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              {t('common.cancel')}
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
              {isEditing ? t('common.save') : t('common.add')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
