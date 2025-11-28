import { useState } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Plug, Power, PowerOff, Loader2, Trash2, Settings2, Thermometer, Clock, Wifi, WifiOff, Edit2 } from 'lucide-react';
import { api } from '../api/client';
import type { SmartPlug, SmartPlugUpdate } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';

interface SmartPlugCardProps {
  plug: SmartPlug;
  onEdit: (plug: SmartPlug) => void;
}

export function SmartPlugCard({ plug, onEdit }: SmartPlugCardProps) {
  const queryClient = useQueryClient();
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);

  // Fetch current status
  const { data: status, isLoading: statusLoading, refetch: refetchStatus } = useQuery({
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

  // Control mutation
  const controlMutation = useMutation({
    mutationFn: (action: 'on' | 'off' | 'toggle') => api.controlSmartPlug(plug.id, action),
    onSuccess: () => {
      refetchStatus();
    },
  });

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (data: SmartPlugUpdate) => api.updateSmartPlug(plug.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteSmartPlug(plug.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
    },
  });

  const isOn = status?.state === 'ON';
  const isReachable = status?.reachable ?? false;
  const isPending = controlMutation.isPending;

  return (
    <>
      <Card className="relative">
        <CardContent className="p-4">
          {/* Header Row */}
          <div className="flex items-start justify-between mb-3">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${isReachable ? (isOn ? 'bg-bambu-green/20' : 'bg-bambu-dark') : 'bg-red-500/20'}`}>
                <Plug className={`w-5 h-5 ${isReachable ? (isOn ? 'text-bambu-green' : 'text-bambu-gray') : 'text-red-400'}`} />
              </div>
              <div>
                <h3 className="font-medium text-white">{plug.name}</h3>
                <p className="text-sm text-bambu-gray">{plug.ip_address}</p>
              </div>
            </div>

            {/* Status indicator */}
            <div className="flex items-center gap-2">
              {statusLoading ? (
                <Loader2 className="w-4 h-4 text-bambu-gray animate-spin" />
              ) : isReachable ? (
                <div className="flex items-center gap-1 text-sm">
                  <Wifi className="w-4 h-4 text-bambu-green" />
                  <span className={isOn ? 'text-bambu-green' : 'text-bambu-gray'}>{status?.state || 'Unknown'}</span>
                </div>
              ) : (
                <div className="flex items-center gap-1 text-sm text-red-400">
                  <WifiOff className="w-4 h-4" />
                  <span>Offline</span>
                </div>
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

          {/* Quick Controls */}
          <div className="flex gap-2 mb-3">
            <Button
              size="sm"
              variant={isOn ? 'primary' : 'secondary'}
              disabled={!isReachable || isPending}
              onClick={() => controlMutation.mutate('on')}
              className="flex-1"
            >
              {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Power className="w-4 h-4" />}
              On
            </Button>
            <Button
              size="sm"
              variant={!isOn ? 'primary' : 'secondary'}
              disabled={!isReachable || isPending}
              onClick={() => controlMutation.mutate('off')}
              className="flex-1"
            >
              {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <PowerOff className="w-4 h-4" />}
              Off
            </Button>
          </div>

          {/* Toggle Settings Panel */}
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="w-full flex items-center justify-between py-2 text-sm text-bambu-gray hover:text-white transition-colors"
          >
            <span className="flex items-center gap-2">
              <Settings2 className="w-4 h-4" />
              Automation Settings
            </span>
            <span>{isExpanded ? '-' : '+'}</span>
          </button>

          {/* Expanded Settings */}
          {isExpanded && (
            <div className="pt-3 border-t border-bambu-dark-tertiary space-y-4">
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
                  <p className="text-xs text-bambu-gray">Turn off when print completes</p>
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
          title="Delete Smart Plug"
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
    </>
  );
}
