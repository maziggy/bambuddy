import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plug, Power, PowerOff, Loader2, Wifi, WifiOff, Zap, Radio, Eye } from 'lucide-react';
import { api } from '../api/client';
import type { SmartPlug } from '../api/client';
import { ConfirmModal } from './ConfirmModal';

interface SwitchbarPopoverProps {
  onClose: () => void;
}

function SwitchItem({ plug }: { plug: SmartPlug }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [confirmAction, setConfirmAction] = useState<'on' | 'off' | null>(null);

  // Fetch current status
  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['smart-plug-status', plug.id],
    queryFn: () => api.getSmartPlugStatus(plug.id),
    refetchInterval: 10000, // Refresh every 10 seconds when popover is open
  });

  // Control mutation
  const controlMutation = useMutation({
    mutationFn: (action: 'on' | 'off') => api.controlSmartPlug(plug.id, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plug-status', plug.id] });
    },
  });

  const isOn = status?.state === 'ON';
  // For MQTT plugs, consider reachable if we have power data
  const hasMqttData = plug.plug_type === 'mqtt' && (status?.energy?.power !== null && status?.energy?.power !== undefined);
  const isReachable = (status?.reachable ?? false) || hasMqttData;
  const isPending = controlMutation.isPending;
  const isMqtt = plug.plug_type === 'mqtt';

  const handleConfirm = () => {
    if (confirmAction) {
      controlMutation.mutate(confirmAction);
      setConfirmAction(null);
    }
  };

  return (
    <>
      <div className="flex items-center justify-between py-2 px-3 hover:bg-bambu-dark-tertiary rounded-lg transition-colors">
        <div className="flex items-center gap-2">
          <div className={`p-1.5 rounded ${
            isMqtt
              ? (isReachable ? 'bg-teal-500/20' : 'bg-red-500/20')
              : (isReachable ? (isOn ? 'bg-bambu-green/20' : 'bg-bambu-dark') : 'bg-red-500/20')
          }`}>
            {isMqtt ? (
              <Radio className={`w-4 h-4 ${isReachable ? 'text-teal-400' : 'text-red-400'}`} />
            ) : (
              <Plug className={`w-4 h-4 ${isReachable ? (isOn ? 'text-bambu-green' : 'text-bambu-gray') : 'text-red-400'}`} />
            )}
          </div>
          <div>
            <p className="text-sm text-white font-medium">{plug.name}</p>
            <div className="flex items-center gap-1 text-xs">
              {statusLoading ? (
                <Loader2 className="w-3 h-3 text-bambu-gray animate-spin" />
              ) : isMqtt ? (
                /* MQTT plugs show power and monitor-only indicator */
                isReachable ? (
                  <>
                    <Zap className="w-3 h-3 text-teal-400" />
                    <span className="text-teal-400">{Math.round(status?.energy?.power ?? 0)}W</span>
                    <span className="text-bambu-gray mx-1">|</span>
                    <Eye className="w-3 h-3 text-bambu-gray" />
                    <span className="text-bambu-gray">{t('smartPlug.monitor')}</span>
                  </>
                ) : (
                  <>
                    <WifiOff className="w-3 h-3 text-status-error" />
                    <span className="text-status-error">{t('smartPlug.waiting')}</span>
                  </>
                )
              ) : isReachable ? (
                <>
                  <Wifi className="w-3 h-3 text-status-ok" />
                  <span className={isOn ? 'text-status-ok' : 'text-bambu-gray'}>{status?.state || t('common.unknown')}</span>
                  {status?.energy?.power !== null && status?.energy?.power !== undefined && (
                    <>
                      <span className="text-bambu-gray mx-1">|</span>
                      <Zap className="w-3 h-3 text-yellow-400" />
                      <span className="text-yellow-400">{Math.round(status.energy.power)}W</span>
                    </>
                  )}
                </>
              ) : (
                <>
                  <WifiOff className="w-3 h-3 text-status-error" />
                  <span className="text-status-error">{t('smartPlug.offline')}</span>
                </>
              )}
            </div>
          </div>
        </div>

        {/* Hide on/off buttons for MQTT plugs (monitor-only) */}
        {!isMqtt && (
          <div className="flex gap-1">
            <button
              onClick={() => setConfirmAction('on')}
              disabled={!isReachable || isPending}
              className={`p-1.5 rounded transition-colors ${
                isOn
                  ? 'bg-bambu-green text-white'
                  : 'bg-bambu-dark text-bambu-gray hover:text-white'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
              title={t('common.turnOn')}
            >
              {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Power className="w-4 h-4" />}
            </button>
            <button
              onClick={() => setConfirmAction('off')}
              disabled={!isReachable || isPending}
              className={`p-1.5 rounded transition-colors ${
                !isOn && isReachable
                  ? 'bg-bambu-dark-tertiary text-white'
                  : 'bg-bambu-dark text-bambu-gray hover:text-white'
              } disabled:opacity-50 disabled:cursor-not-allowed`}
              title={t('common.turnOff')}
            >
              {isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <PowerOff className="w-4 h-4" />}
            </button>
          </div>
        )}
      </div>

      {confirmAction && (
        <ConfirmModal
          title={confirmAction === 'on' ? t('smartPlug.turnOnTitle') : t('smartPlug.turnOffTitle')}
          message={confirmAction === 'on'
            ? t('smartPlug.turnOnConfirm', { name: plug.name })
            : t('smartPlug.turnOffConfirm', { name: plug.name })
          }
          confirmText={confirmAction === 'on' ? t('common.turnOn') : t('common.turnOff')}
          variant={confirmAction === 'off' ? 'warning' : 'default'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}
    </>
  );
}

export function SwitchbarPopover({ onClose }: SwitchbarPopoverProps) {
  const { t } = useTranslation();
  // Fetch all smart plugs
  const { data: plugs, isLoading } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: api.getSmartPlugs,
  });

  // Filter to only show plugs with show_in_switchbar enabled
  const switchbarPlugs = plugs?.filter(p => p.show_in_switchbar) || [];

  return (
    <div
      className="absolute bottom-full left-0 mb-2 w-72 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-xl z-50"
      onMouseLeave={onClose}
    >
      {/* Header */}
      <div className="px-4 py-3 border-b border-bambu-dark-tertiary">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Plug className="w-4 h-4 text-bambu-green" />
          {t('smartPlug.smartSwitches')}
        </h3>
      </div>

      {/* Content */}
      <div className="p-2 max-h-80 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="w-6 h-6 text-bambu-gray animate-spin" />
          </div>
        ) : switchbarPlugs.length === 0 ? (
          <div className="text-center py-6 px-4">
            <Plug className="w-8 h-8 text-bambu-gray mx-auto mb-2" />
            <p className="text-sm text-bambu-gray">{t('smartPlug.noSwitchesInSwitchbar')}</p>
            <p className="text-xs text-bambu-gray mt-1">
              {t('smartPlug.enableInSettings')}
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            {switchbarPlugs.map(plug => (
              <SwitchItem key={plug.id} plug={plug} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
