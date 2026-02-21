import { useState } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Plug, Power, PowerOff, Loader2, Trash2, Settings2, Thermometer, Clock, Wifi, WifiOff, Edit2, Bell, Calendar, LayoutGrid, ExternalLink, Home, Radio, Eye } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { SmartPlug, SmartPlugUpdate } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

interface SmartPlugCardProps {
  plug: SmartPlug;
  onEdit: (plug: SmartPlug) => void;
}

export function SmartPlugCard({ plug, onEdit }: SmartPlugCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [showPowerOnConfirm, setShowPowerOnConfirm] = useState(false);
  const [showPowerOffConfirm, setShowPowerOffConfirm] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  // Fetch current status
  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['smart-plug-status', plug.id],
    queryFn: () => api.getSmartPlugStatus(plug.id),
    refetchInterval: 30000, // Refresh every 30 seconds
  });

  // Fetch printers for linking
  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const linkedPrinter = printers?.find(p => p.id === plug.printer_id);

  // Control mutation with optimistic updates
  const controlMutation = useMutation({
    mutationFn: (action: 'on' | 'off' | 'toggle') => api.controlSmartPlug(plug.id, action),
    onMutate: async (action) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: ['smart-plug-status', plug.id] });

      // Snapshot the previous value
      const previousStatus = queryClient.getQueryData(['smart-plug-status', plug.id]);

      // Optimistically update to the new value
      const newState = action === 'on' ? 'ON' : action === 'off' ? 'OFF' : (status?.state === 'ON' ? 'OFF' : 'ON');
      queryClient.setQueryData(['smart-plug-status', plug.id], (old: typeof status) => ({
        ...old,
        state: newState,
      }));

      return { previousStatus };
    },
    onError: (_err, action, context) => {
      // Rollback on error
      if (context?.previousStatus) {
        queryClient.setQueryData(['smart-plug-status', plug.id], context.previousStatus);
      }
      showToast(`Failed to turn ${action} "${plug.name}"`, 'error');
    },
    onSettled: () => {
      // Refetch after a short delay to get actual state
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['smart-plug-status', plug.id] });
        queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      }, 1000);
    },
  });

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: SmartPlugUpdate) => api.updateSmartPlug(plug.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      // Also invalidate printer-specific smart plug queries to keep PrintersPage in sync
      if (plug.printer_id) {
        queryClient.invalidateQueries({ queryKey: ['smartPlugByPrinter', plug.printer_id] });
        queryClient.invalidateQueries({ queryKey: ['scriptPlugsByPrinter', plug.printer_id] });
      }
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteSmartPlug(plug.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      // Also invalidate printer card HA entity queries
      if (plug.printer_id) {
        queryClient.invalidateQueries({ queryKey: ['scriptPlugsByPrinter', plug.printer_id] });
      }
    },
  });

  const isOn = status?.state === 'ON';
  // For MQTT plugs, consider reachable if we have power data (even if backend says not reachable)
  const hasMqttData = plug.plug_type === 'mqtt' && (status?.energy?.power !== null && status?.energy?.power !== undefined);
  const isReachable = (status?.reachable ?? false) || hasMqttData;
  const isPending = controlMutation.isPending;

  // Generate admin URL with auto-login credentials (Tasmota only)
  const getAdminUrl = () => {
    if (plug.plug_type !== 'tasmota' || !plug.ip_address) return null;
    const ip = plug.ip_address;
    if (plug.username && plug.password) {
      // Use HTTP Basic Auth in URL for auto-login
      return `http://${encodeURIComponent(plug.username)}:${encodeURIComponent(plug.password)}@${ip}/`;
    }
    return `http://${ip}/`;
  };

  const adminUrl = getAdminUrl();

  return (
    <>
      <Card className="relative">
        <CardContent className="p-4">
          {/* Header Row */}
          <div className="flex items-start justify-between gap-2 mb-3">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              <div className={`p-2 rounded-lg flex-shrink-0 ${
                plug.plug_type === 'mqtt'
                  ? (isReachable ? 'bg-teal-500/20' : 'bg-red-500/20')
                  : (isReachable ? (isOn ? 'bg-bambu-green/20' : 'bg-bambu-dark') : 'bg-red-500/20')
              }`}>
                {plug.plug_type === 'mqtt' ? (
                  <Radio className={`w-5 h-5 ${isReachable ? 'text-teal-400' : 'text-red-400'}`} />
                ) : plug.plug_type === 'homeassistant' ? (
                  <Home className={`w-5 h-5 ${isReachable ? (isOn ? 'text-bambu-green' : 'text-bambu-gray') : 'text-red-400'}`} />
                ) : (
                  <Plug className={`w-5 h-5 ${isReachable ? (isOn ? 'text-bambu-green' : 'text-bambu-gray') : 'text-red-400'}`} />
                )}
              </div>
              <div className="min-w-0">
                <h3 className="font-medium text-white truncate">{plug.name}</h3>
                <p
                  className="text-sm text-bambu-gray truncate"
                  title={plug.plug_type === 'mqtt' ? plug.mqtt_topic ?? undefined : plug.plug_type === 'homeassistant' ? plug.ha_entity_id ?? undefined : plug.ip_address ?? undefined}
                >
                  {plug.plug_type === 'mqtt' ? plug.mqtt_topic : plug.plug_type === 'homeassistant' ? plug.ha_entity_id : plug.ip_address}
                </p>
              </div>
            </div>

            {/* Status indicator */}
            <div className="flex flex-col items-end gap-1 flex-shrink-0">
              {statusLoading ? (
                <Loader2 className="w-4 h-4 text-bambu-gray animate-spin" />
              ) : plug.plug_type === 'mqtt' ? (
                /* MQTT plugs - show badge and checkmark when receiving data */
                <div className="flex items-center gap-1.5 text-sm whitespace-nowrap">
                  <span className="px-1.5 py-0.5 bg-teal-500/20 text-teal-400 text-[10px] font-medium rounded flex-shrink-0">MQTT</span>
                  {isReachable && <span className="text-status-ok">✓</span>}
                </div>
              ) : plug.plug_type === 'homeassistant' ? (
                <div className="flex items-center gap-1 text-sm">
                  <span className="px-1 py-0.5 bg-blue-500/20 text-blue-400 text-[10px] font-medium rounded">HA</span>
                  <span className={isReachable ? (isOn ? 'text-status-ok' : 'text-bambu-gray') : 'text-status-error'}>
                    {isReachable ? (status?.state || '?') : 'Offline'}
                  </span>
                </div>
              ) : isReachable ? (
                <div className="flex items-center gap-1 text-sm">
                  <Wifi className="w-4 h-4 text-status-ok" />
                  <span className={isOn ? 'text-status-ok' : 'text-bambu-gray'}>{status?.state || 'Unknown'}</span>
                </div>
              ) : (
                <div className="flex items-center gap-1 text-sm text-status-error">
                  <WifiOff className="w-4 h-4" />
                  <span>{t('smartPlugs.offline')}</span>
                </div>
              )}
              {/* Admin page link - only for Tasmota */}
              {adminUrl && (
                <a
                  href={adminUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-1 px-2 py-0.5 bg-bambu-dark hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white text-xs rounded-full transition-colors"
                  title={t('smartPlugs.openPlugAdminPage')}
                >
                  <ExternalLink className="w-3 h-3" />
                  {t('smartPlugs.admin')}
                </a>
              )}
            </div>
          </div>

          {/* Linked Printer */}
          {linkedPrinter && (
            <div className="mb-3 px-2 py-1.5 bg-bambu-dark rounded-lg">
              <span className="text-xs text-bambu-gray">Linked to: </span>
              <span className="text-sm text-white">{linkedPrinter.name}</span>
            </div>
          )}

          {/* Feature Badges */}
          {(plug.power_alert_enabled || plug.schedule_enabled || plug.plug_type === 'mqtt') && (
            <div className="flex flex-wrap gap-1.5 mb-3">
              {plug.plug_type === 'mqtt' && (
                <span className="flex items-center gap-1 px-2 py-0.5 bg-teal-500/20 text-teal-400 text-xs rounded-full">
                  <Eye className="w-3 h-3" />
                  Monitor Only
                </span>
              )}
              {plug.power_alert_enabled && (
                <span className="flex items-center gap-1 px-2 py-0.5 bg-yellow-500/20 text-yellow-400 text-xs rounded-full">
                  <Bell className="w-3 h-3" />
                  Alerts
                </span>
              )}
              {plug.schedule_enabled && (
                <span className="flex items-center gap-1 px-2 py-0.5 bg-blue-500/20 text-blue-400 text-xs rounded-full">
                  <Calendar className="w-3 h-3" />
                  {plug.schedule_on_time && plug.schedule_off_time
                    ? `${plug.schedule_on_time} - ${plug.schedule_off_time}`
                    : plug.schedule_on_time
                      ? `On ${plug.schedule_on_time}`
                      : `Off ${plug.schedule_off_time}`}
                </span>
              )}
            </div>
          )}

          {/* Quick Controls - hidden for MQTT plugs (monitor-only) */}
          {plug.plug_type !== 'mqtt' && (
            <div className="flex gap-2 mb-3">
              <Button
                size="sm"
                variant={isOn ? 'primary' : 'secondary'}
                disabled={!isReachable || isPending}
                onClick={() => setShowPowerOnConfirm(true)}
                className="flex-1"
              >
                {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Power className="w-4 h-4" />}
                On
              </Button>
              <Button
                size="sm"
                variant={!isOn ? 'primary' : 'secondary'}
                disabled={!isReachable || isPending}
                onClick={() => setShowPowerOffConfirm(true)}
                className="flex-1"
              >
                {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <PowerOff className="w-4 h-4" />}
                Off
              </Button>
            </div>
          )}

          {/* Energy display for MQTT plugs */}
          {plug.plug_type === 'mqtt' && status?.energy && (
            <div className="flex gap-2 mb-3 px-3 py-2 bg-bambu-dark rounded-lg">
              {status.energy.power !== null && status.energy.power !== undefined && (
                <div className="flex-1 text-center">
                  <p className="text-lg font-semibold text-white">{Math.round(status.energy.power)}W</p>
                  <p className="text-xs text-bambu-gray">Power</p>
                </div>
              )}
              {status.energy.today !== null && status.energy.today !== undefined && (
                <div className="flex-1 text-center border-l border-bambu-dark-tertiary">
                  <p className="text-lg font-semibold text-white">{status.energy.today.toFixed(3)}</p>
                  <p className="text-xs text-bambu-gray">kWh Today</p>
                </div>
              )}
            </div>
          )}

          {/* Toggle Settings Panel */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-full flex items-center justify-between py-2 text-sm text-bambu-gray hover:text-white transition-colors"
          >
            <span className="flex items-center gap-2">
              <Settings2 className="w-4 h-4" />
              {plug.plug_type === 'mqtt' ? 'Settings' : 'Automation Settings'}
            </span>
            <span>{isExpanded ? '-' : '+'}</span>
          </button>

          {/* Expanded Settings */}
          {isExpanded && (
            <div className="pt-3 border-t border-bambu-dark-tertiary space-y-4">
              {/* Show in Switchbar Toggle */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <LayoutGrid className="w-4 h-4 text-bambu-green" />
                  <div>
                    <p className="text-sm text-white">Show in Switchbar</p>
                    <p className="text-xs text-bambu-gray">Quick access from sidebar</p>
                  </div>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={plug.show_in_switchbar}
                    onChange={(e) => updateMutation.mutate({ show_in_switchbar: e.target.checked })}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {/* Automation controls - only for controllable plugs (not MQTT) */}
              {plug.plug_type !== 'mqtt' && (
                <>
                  {/* Enabled Toggle */}
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-white">Enabled</p>
                      <p className="text-xs text-bambu-gray">Enable automation for this plug</p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={plug.enabled}
                        onChange={(e) => updateMutation.mutate({ enabled: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bambu-green"></div>
                    </label>
                  </div>

                  {/* Auto On */}
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-white">Auto On</p>
                      <p className="text-xs text-bambu-gray">Turn on when print starts</p>
                    </div>
                    <label className="relative inline-flex items-center cursor-pointer">
                      <input
                        type="checkbox"
                        checked={plug.auto_on}
                        onChange={(e) => updateMutation.mutate({ auto_on: e.target.checked })}
                        className="sr-only peer"
                      />
                      <div className="w-9 h-5 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bambu-green"></div>
                    </label>
                  </div>

                  {/* Auto Off */}
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-white">Auto Off</p>
                      <p className="text-xs text-bambu-gray">Turn off when print completes (one-shot)</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={plug.auto_off}
                    onChange={(e) => updateMutation.mutate({ auto_off: e.target.checked })}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-bambu-dark-tertiary peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-bambu-green"></div>
                </label>
              </div>

              {/* Delay Mode */}
              {plug.auto_off && (
                <div className="space-y-3 pl-4 border-l-2 border-bambu-dark-tertiary">
                  <div>
                    <p className="text-sm text-white mb-2">Turn Off Delay Mode</p>
                    <div className="flex gap-2">
                      <button
                        onClick={() => updateMutation.mutate({ off_delay_mode: 'time' })}
                        className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                          plug.off_delay_mode === 'time'
                            ? 'bg-bambu-green text-white'
                            : 'bg-bambu-dark text-bambu-gray hover:text-white'
                        }`}
                      >
                        <Clock className="w-4 h-4" />
                        Time
                      </button>
                      <button
                        onClick={() => updateMutation.mutate({ off_delay_mode: 'temperature' })}
                        className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors ${
                          plug.off_delay_mode === 'temperature'
                            ? 'bg-bambu-green text-white'
                            : 'bg-bambu-dark text-bambu-gray hover:text-white'
                        }`}
                      >
                        <Thermometer className="w-4 h-4" />
                        Temp
                      </button>
                    </div>
                  </div>

                  {plug.off_delay_mode === 'time' ? (
                    <div>
                      <label className="block text-xs text-bambu-gray mb-1">Delay (minutes)</label>
                      <input
                        type="number"
                        min="1"
                        max="60"
                        value={plug.off_delay_minutes}
                        onChange={(e) => updateMutation.mutate({ off_delay_minutes: parseInt(e.target.value) || 5 })}
                        className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      />
                    </div>
                  ) : (
                    <div>
                      <label className="block text-xs text-bambu-gray mb-1">Temperature threshold (C)</label>
                      <input
                        type="number"
                        min="30"
                        max="100"
                        value={plug.off_temp_threshold}
                        onChange={(e) => updateMutation.mutate({ off_temp_threshold: parseInt(e.target.value) || 70 })}
                        className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                      />
                      <p className="text-xs text-bambu-gray mt-1">Turns off when nozzle cools below this temperature</p>
                    </div>
                  )}
                </div>
              )}
                </>
              )}

              {/* Action Buttons */}
              <div className="flex gap-2 pt-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => onEdit(plug)}
                  className="flex-1"
                >
                  <Edit2 className="w-4 h-4" />
                  Edit
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
          title={t('smartPlugs.deleteSmartPlug')}
          message={`Are you sure you want to delete "${plug.name}"? This cannot be undone.`}
          confirmText="Delete"
          variant="danger"
          onConfirm={() => {
            deleteMutation.mutate();
            setShowDeleteConfirm(false);
          }}
          onCancel={() => setShowDeleteConfirm(false)}
        />
      )}

      {/* Power On Confirmation */}
      {showPowerOnConfirm && (
        <ConfirmModal
          title={t('smartPlugs.turnOnSmartPlug')}
          message={`Are you sure you want to turn on "${plug.name}"?`}
          confirmText={t('smartPlugs.turnOn')}
          variant="default"
          onConfirm={() => {
            controlMutation.mutate('on');
            setShowPowerOnConfirm(false);
          }}
          onCancel={() => setShowPowerOnConfirm(false)}
        />
      )}

      {/* Power Off Confirmation */}
      {showPowerOffConfirm && (
        <ConfirmModal
          title={t('smartPlugs.turnOffSmartPlug')}
          message={`Are you sure you want to turn off "${plug.name}"? This will cut power to the connected device.`}
          confirmText={t('smartPlugs.turnOff')}
          variant="danger"
          onConfirm={() => {
            controlMutation.mutate('off');
            setShowPowerOffConfirm(false);
          }}
          onCancel={() => setShowPowerOffConfirm(false)}
        />
      )}
    </>
  );
}
