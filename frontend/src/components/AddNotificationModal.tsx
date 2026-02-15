import { useState, useEffect } from 'react';
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

const PROVIDER_OPTIONS: { value: ProviderType; label: string; description: string }[] = [
  { value: 'email', label: 'Email', description: 'SMTP email notifications' },
  { value: 'telegram', label: 'Telegram', description: 'Notifications via Telegram bot' },
  { value: 'discord', label: 'Discord', description: 'Send to Discord channel via webhook' },
  { value: 'ntfy', label: 'ntfy', description: 'Free, self-hostable push notifications' },
  { value: 'pushover', label: 'Pushover', description: 'Simple, reliable push notifications' },
  { value: 'callmebot', label: 'CallMeBot/WhatsApp', description: 'Free WhatsApp notifications via CallMeBot' },
  { value: 'webhook', label: 'Webhook', description: 'Generic HTTP POST to any URL' },
];

export function AddNotificationModal({ provider, onClose }: AddNotificationModalProps) {
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
  const [onBedCooled, setOnBedCooled] = useState(provider?.on_bed_cooled ?? false);

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
      setError('Name is required');
      return;
    }

    // Validate provider-specific config
    const requiredFields = getRequiredFields(providerType);
    for (const field of requiredFields) {
      if (!config[field.key]?.trim()) {
        setError(`${field.label} is required`);
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
      on_bed_cooled: onBedCooled,
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
        ];
      case 'telegram':
        return [
          { key: 'bot_token', label: 'Bot Token', placeholder: 'Bot token from @BotFather', type: 'password', required: true },
          { key: 'chat_id', label: 'Chat ID', placeholder: 'Your chat or group ID', type: 'text', required: true },
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
            {isEditing ? 'Edit Notification Provider' : 'Add Notification Provider'}
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
            <label className="block text-sm text-bambu-gray mb-1">Name *</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Notifications"
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            />
          </div>

          {/* Provider Type */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Provider Type *</label>
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
              {PROVIDER_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              {PROVIDER_OPTIONS.find(o => o.value === providerType)?.description}
            </p>
          </div>

          {/* Provider-specific configuration */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">Configuration</p>
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
              Test Configuration
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
            <label className="block text-sm text-bambu-gray mb-1">Printer Filter</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">All printers</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <p className="text-xs text-bambu-gray mt-1">
              Only send notifications for events from this printer
            </p>
          </div>

          {/* Quiet Hours */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label className="text-sm text-white">Quiet Hours (Do Not Disturb)</label>
              <Toggle
                checked={quietHoursEnabled}
                onChange={setQuietHoursEnabled}
              />
            </div>
            {quietHoursEnabled && (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">Start</label>
                  <input
                    type="time"
                    value={quietHoursStart}
                    onChange={(e) => setQuietHoursStart(e.target.value)}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                  />
                </div>
                <div>
                  <label className="block text-xs text-bambu-gray mb-1">End</label>
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
                <label className="text-sm text-white">Daily Digest</label>
                <p className="text-xs text-bambu-gray">Batch notifications into a single daily summary</p>
              </div>
              <Toggle
                checked={dailyDigestEnabled}
                onChange={setDailyDigestEnabled}
              />
            </div>
            {dailyDigestEnabled && (
              <div>
                <label className="block text-xs text-bambu-gray mb-1">Send digest at</label>
                <input
                  type="time"
                  value={dailyDigestTime}
                  onChange={(e) => setDailyDigestTime(e.target.value)}
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                />
                <p className="text-xs text-bambu-gray mt-1">
                  Events will be collected and sent as a single summary at this time
                </p>
              </div>
            )}
          </div>

          {/* Event Toggles */}
          <div className="space-y-3">
            <p className="text-sm text-bambu-gray">Notification Events</p>

            {/* Print Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">Print Events</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Start</span>
                  <Toggle checked={onPrintStart} onChange={setOnPrintStart} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Complete</span>
                  <Toggle checked={onPrintComplete} onChange={setOnPrintComplete} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Failed</span>
                  <Toggle checked={onPrintFailed} onChange={setOnPrintFailed} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Stopped</span>
                  <Toggle checked={onPrintStopped} onChange={setOnPrintStopped} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">Progress</span>
                    <span className="text-xs text-bambu-gray ml-1">(25%, 50%, 75%)</span>
                  </div>
                  <Toggle checked={onPrintProgress} onChange={setOnPrintProgress} />
                </div>
                <div className="flex items-center justify-between col-span-2">
                  <div>
                    <span className="text-sm text-white">Bed Cooled</span>
                    <span className="text-xs text-bambu-gray ml-1">(after print completes)</span>
                  </div>
                  <Toggle checked={onBedCooled} onChange={setOnBedCooled} />
                </div>
              </div>
            </div>

            {/* Printer Status Events */}
            <div className="space-y-2 p-3 bg-bambu-dark rounded-lg">
              <p className="text-xs text-bambu-gray uppercase tracking-wide mb-2">Printer Status</p>
              <div className="grid grid-cols-2 gap-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Offline</span>
                  <Toggle checked={onPrinterOffline} onChange={setOnPrinterOffline} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Error</span>
                  <Toggle checked={onPrinterError} onChange={setOnPrinterError} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Low Filament</span>
                  <Toggle checked={onFilamentLow} onChange={setOnFilamentLow} />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-white">Maintenance</span>
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
              Cancel
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
              {isEditing ? 'Save' : 'Add'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
