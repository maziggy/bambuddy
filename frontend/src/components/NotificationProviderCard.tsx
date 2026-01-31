import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Bell, Trash2, Settings2, Edit2, Send, Loader2, CheckCircle, XCircle, Moon, Clock, ChevronDown, ChevronUp, Calendar } from 'lucide-react';
import { api } from '../api/client';
import { formatDateOnly, parseUTCDate } from '../utils/date';
import type { NotificationProvider, NotificationProviderUpdate } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { Toggle } from './Toggle';

interface NotificationProviderCardProps {
  provider: NotificationProvider;
  onEdit: (provider: NotificationProvider) => void;
}

export function NotificationProviderCard({ provider, onEdit }: NotificationProviderCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  // Fetch printers for linking
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const linkedPrinter = printers?.find(p => p.id === provider.printer_id);

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: NotificationProviderUpdate) => api.updateNotificationProvider(provider.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteNotificationProvider(provider.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
    },
  });

  // Test mutation
  const testMutation = useMutation({
    mutationFn: () => api.testNotificationProvider(provider.id),
    onSuccess: (result) => {
      setTestResult(result);
      queryClient.invalidateQueries({ queryKey: ['notification-providers'] });
    },
    onError: (err: Error) => {
      setTestResult({ success: false, message: err.message });
    },
  });

  // Format time for display
  const formatTime = (time: string | null) => {
    if (!time) return '';
    return time;
  };

  return (
    <>
      <Card className="relative">
        <CardContent className="p-4">
          {/* Header Row */}
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${provider.enabled ? 'bg-bambu-green/20' : 'bg-bambu-dark'}`}>
                <Bell className={`w-5 h-5 ${provider.enabled ? 'text-bambu-green' : 'text-bambu-gray'}`} />
              </div>
              <div>
                <h3 className="font-medium text-white">{provider.name}</h3>
                <p className="text-sm text-bambu-gray">{t(`providers.${provider.provider_type}`, provider.provider_type)}</p>
              </div>
            </div>

            {/* Quick enable/disable toggle + Status indicator */}
            <div className="flex items-center gap-3">
              {provider.last_success && (
                <span className="text-xs text-bambu-green hidden sm:inline">
                  {t('providerCard.lastSuccessAt', { date: formatDateOnly(provider.last_success) })}
                </span>
              )}
              {/* Only show error if it's more recent than last success */}
              {provider.last_error && provider.last_error_at && (
                !provider.last_success || (parseUTCDate(provider.last_error_at)?.getTime() || 0) > (parseUTCDate(provider.last_success)?.getTime() || 0)
              ) && (
                <span className="text-xs text-red-400" title={provider.last_error}>{t('common.error')}</span>
              )}
              <Toggle
                checked={provider.enabled}
                onChange={(checked) => updateMutation.mutate({ enabled: checked })}
              />
            </div>
          </div>

          {/* Linked Printer */}
          {linkedPrinter && (
            <div className="mb-3 px-2 py-1.5 bg-bambu-dark rounded-lg">
              <span className="text-xs text-bambu-gray">{t('providerCard.printer')}</span>
              <span className="text-sm text-white">{linkedPrinter.name}</span>
            </div>
          )}
          {!linkedPrinter && !provider.printer_id && (
            <div className="mb-3 px-2 py-1.5 bg-bambu-dark rounded-lg">
              <span className="text-xs text-bambu-gray">{t('providerCard.allPrinters')}</span>
            </div>
          )}

          {/* Event summary - show all event tags */}
          <div className="mb-3 flex flex-wrap gap-1">
            {provider.on_print_start && (
              <span className="px-2 py-0.5 bg-blue-500/20 text-blue-400 text-xs rounded">{t('providerCard.tagStart')}</span>
            )}
            {provider.on_print_complete && (
              <span className="px-2 py-0.5 bg-bambu-green/20 text-bambu-green text-xs rounded">{t('providerCard.tagComplete')}</span>
            )}
            {provider.on_print_failed && (
              <span className="px-2 py-0.5 bg-red-500/20 text-red-400 text-xs rounded">{t('providerCard.tagFailed')}</span>
            )}
            {provider.on_print_stopped && (
              <span className="px-2 py-0.5 bg-orange-500/20 text-orange-400 text-xs rounded">{t('providerCard.tagStopped')}</span>
            )}
            {provider.on_print_progress && (
              <span className="px-2 py-0.5 bg-yellow-500/20 text-yellow-400 text-xs rounded">{t('providerCard.tagProgress')}</span>
            )}
            {provider.on_printer_offline && (
              <span className="px-2 py-0.5 bg-gray-500/20 text-gray-400 text-xs rounded">{t('providerCard.tagOffline')}</span>
            )}
            {provider.on_printer_error && (
              <span className="px-2 py-0.5 bg-rose-500/20 text-rose-400 text-xs rounded">{t('common.error')}</span>
            )}
            {provider.on_filament_low && (
              <span className="px-2 py-0.5 bg-cyan-500/20 text-cyan-400 text-xs rounded">{t('providerCard.tagLowFilament')}</span>
            )}
            {provider.on_maintenance_due && (
              <span className="px-2 py-0.5 bg-purple-500/20 text-purple-400 text-xs rounded">{t('providerCard.tagMaintenance')}</span>
            )}
            {provider.on_ams_humidity_high && (
              <span className="px-2 py-0.5 bg-blue-600/20 text-blue-300 text-xs rounded">{t('providerCard.tagAmsHumidity')}</span>
            )}
            {provider.on_ams_temperature_high && (
              <span className="px-2 py-0.5 bg-orange-600/20 text-orange-300 text-xs rounded">{t('providerCard.tagAmsTemp')}</span>
            )}
            {provider.on_ams_ht_humidity_high && (
              <span className="px-2 py-0.5 bg-cyan-600/20 text-cyan-300 text-xs rounded">{t('providerCard.tagAmsHtHumidity')}</span>
            )}
            {provider.on_ams_ht_temperature_high && (
              <span className="px-2 py-0.5 bg-amber-600/20 text-amber-300 text-xs rounded">{t('providerCard.tagAmsHtTemp')}</span>
            )}
            {provider.quiet_hours_enabled && (
              <span className="px-2 py-0.5 bg-indigo-500/20 text-indigo-400 text-xs rounded flex items-center gap-1">
                <Moon className="w-3 h-3" />
                {t('providerCard.quiet')}
              </span>
            )}
            {provider.daily_digest_enabled && (
              <span className="px-2 py-0.5 bg-emerald-500/20 text-emerald-400 text-xs rounded flex items-center gap-1">
                <Calendar className="w-3 h-3" />
                {t('providerCard.digestAt', { time: provider.daily_digest_time })}
              </span>
            )}
          </div>

          {/* Test Button */}
          <div className="mb-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={testMutation.isPending}
              onClick={() => {
                setTestResult(null);
                testMutation.mutate();
              }}
              className="w-full"
            >
              {testMutation.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {t('providerCard.sendTest')}
            </Button>
          </div>

          {/* Test Result */}
          {testResult && (
            <div className={`mb-3 p-2 rounded-lg flex items-center gap-2 text-sm ${
              testResult.success
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-red-500/20 text-red-400'
            }`}>
              {testResult.success ? (
                <CheckCircle className="w-4 h-4" />
              ) : (
                <XCircle className="w-4 h-4" />
              )}
              <span>{testResult.message}</span>
            </div>
          )}

          {/* Toggle Settings Panel */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-full flex items-center justify-between py-2 text-sm text-bambu-gray hover:text-white transition-colors border-t border-bambu-dark-tertiary"
          >
            <span className="flex items-center gap-2">
              <Settings2 className="w-4 h-4" />
              {t('providerCard.eventSettings')}
            </span>
            {isExpanded ? (
              <ChevronUp className="w-4 h-4" />
            ) : (
              <ChevronDown className="w-4 h-4" />
            )}
          </button>

          {/* Expanded Settings */}
          {isExpanded && (
            <div className="pt-3 border-t border-bambu-dark-tertiary space-y-4">
              {/* Enabled Toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-white">{t('common.enabled')}</p>
                  <p className="text-xs text-bambu-gray">{t('providerCard.sendNotifications')}</p>
                </div>
                <Toggle
                  checked={provider.enabled}
                  onChange={(checked) => updateMutation.mutate({ enabled: checked })}
                />
              </div>

              {/* Print Lifecycle Events */}
              <div className="space-y-2">
                <p className="text-xs text-bambu-gray uppercase tracking-wide">{t('providerCard.printEvents')}</p>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printStart')}</p>
                  <Toggle
                    checked={provider.on_print_start}
                    onChange={(checked) => updateMutation.mutate({ on_print_start: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printComplete')}</p>
                  <Toggle
                    checked={provider.on_print_complete}
                    onChange={(checked) => updateMutation.mutate({ on_print_complete: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printFailed')}</p>
                  <Toggle
                    checked={provider.on_print_failed}
                    onChange={(checked) => updateMutation.mutate({ on_print_failed: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printStopped')}</p>
                  <Toggle
                    checked={provider.on_print_stopped}
                    onChange={(checked) => updateMutation.mutate({ on_print_stopped: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('settings.events.printProgress')}</p>
                    <p className="text-xs text-bambu-gray">{t('settings.events.printProgressDescription')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_print_progress}
                    onChange={(checked) => updateMutation.mutate({ on_print_progress: checked })}
                  />
                </div>
              </div>

              {/* Printer Status Events */}
              <div className="space-y-2">
                <p className="text-xs text-bambu-gray uppercase tracking-wide">{t('providerCard.printerStatus')}</p>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printerOffline')}</p>
                  <Toggle
                    checked={provider.on_printer_offline}
                    onChange={(checked) => updateMutation.mutate({ on_printer_offline: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.printerError')}</p>
                  <Toggle
                    checked={provider.on_printer_error}
                    onChange={(checked) => updateMutation.mutate({ on_printer_error: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <p className="text-sm text-white">{t('settings.events.filamentLow')}</p>
                  <Toggle
                    checked={provider.on_filament_low}
                    onChange={(checked) => updateMutation.mutate({ on_filament_low: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('settings.events.maintenanceDue')}</p>
                    <p className="text-xs text-bambu-gray">{t('settings.events.maintenanceDueDescription')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_maintenance_due ?? false}
                    onChange={(checked) => updateMutation.mutate({ on_maintenance_due: checked })}
                  />
                </div>
              </div>

              {/* AMS Environmental Alarms (regular AMS) */}
              <div className="space-y-2">
                <p className="text-xs text-bambu-gray uppercase tracking-wide">{t('providerCard.amsAlarms')}</p>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('providerCard.amsHumidityHigh')}</p>
                    <p className="text-xs text-bambu-gray">{t('providerCard.amsHumidityHighDesc')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_ams_humidity_high ?? false}
                    onChange={(checked) => updateMutation.mutate({ on_ams_humidity_high: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('providerCard.amsTempHigh')}</p>
                    <p className="text-xs text-bambu-gray">{t('providerCard.amsTempHighDesc')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_ams_temperature_high ?? false}
                    onChange={(checked) => updateMutation.mutate({ on_ams_temperature_high: checked })}
                  />
                </div>
              </div>

              {/* AMS-HT Environmental Alarms */}
              <div className="space-y-2">
                <p className="text-xs text-bambu-gray uppercase tracking-wide">{t('providerCard.amsHtAlarms')}</p>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('providerCard.amsHtHumidityHigh')}</p>
                    <p className="text-xs text-bambu-gray">{t('providerCard.amsHtHumidityHighDesc')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_ams_ht_humidity_high ?? false}
                    onChange={(checked) => updateMutation.mutate({ on_ams_ht_humidity_high: checked })}
                  />
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm text-white">{t('providerCard.amsHtTempHigh')}</p>
                    <p className="text-xs text-bambu-gray">{t('providerCard.amsHtTempHighDesc')}</p>
                  </div>
                  <Toggle
                    checked={provider.on_ams_ht_temperature_high ?? false}
                    onChange={(checked) => updateMutation.mutate({ on_ams_ht_temperature_high: checked })}
                  />
                </div>
              </div>

              {/* Quiet Hours */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Moon className="w-4 h-4 text-purple-400" />
                    <p className="text-sm text-white">{t('settings.quietHours')}</p>
                  </div>
                  <Toggle
                    checked={provider.quiet_hours_enabled}
                    onChange={(checked) => updateMutation.mutate({ quiet_hours_enabled: checked })}
                  />
                </div>

                {provider.quiet_hours_enabled && (
                  <div className="pl-4 border-l-2 border-bambu-dark-tertiary space-y-2">
                    <p className="text-xs text-bambu-gray">{t('providerCard.noNotificationsDuring')}</p>
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4 text-bambu-gray" />
                      <span className="text-sm text-white">
                        {formatTime(provider.quiet_hours_start) || '22:00'} - {formatTime(provider.quiet_hours_end) || '07:00'}
                      </span>
                    </div>
                    <p className="text-xs text-bambu-gray">{t('providerCard.editQuietHours')}</p>
                  </div>
                )}
              </div>

              {/* Daily Digest */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Calendar className="w-4 h-4 text-emerald-400" />
                    <p className="text-sm text-white">{t('providerCard.dailyDigest')}</p>
                  </div>
                  <Toggle
                    checked={provider.daily_digest_enabled}
                    onChange={(checked) => updateMutation.mutate({ daily_digest_enabled: checked })}
                  />
                </div>

                {provider.daily_digest_enabled && (
                  <div className="pl-4 border-l-2 border-bambu-dark-tertiary space-y-2">
                    <p className="text-xs text-bambu-gray">{t('providerCard.dailyDigestDesc')}</p>
                    <div className="flex items-center gap-2">
                      <Clock className="w-4 h-4 text-bambu-gray" />
                      <span className="text-sm text-white">
                        {t('providerCard.sendAt')} {formatTime(provider.daily_digest_time) || '08:00'}
                      </span>
                    </div>
                    <p className="text-xs text-bambu-gray">{t('providerCard.editDigestTime')}</p>
                  </div>
                )}
              </div>

              {/* Action Buttons */}
              <div className="flex gap-2 pt-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => onEdit(provider)}
                  className="flex-1"
                >
                  <Edit2 className="w-4 h-4" />
                  {t('common.edit')}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setShowDeleteConfirm(true)}
                  className="text-red-400 hover:text-red-300"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Delete Confirmation */}
      {showDeleteConfirm && (
        <ConfirmModal
          title={t('providerCard.deleteTitle')}
          message={t('providerCard.deleteConfirm', { name: provider.name })}
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate();
            setShowDeleteConfirm(false);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}
    </>
  );
}
