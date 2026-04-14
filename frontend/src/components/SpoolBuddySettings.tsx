import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Loader2,
  Trash2,
  Cpu,
  HardDrive,
  Thermometer,
  Wifi,
  WifiOff,
  AlertTriangle,
  Info,
  CheckCircle2,
  XCircle,
  Clock,
  Download,
  Monitor,
  RefreshCw,
  RotateCw,
  Power,
} from 'lucide-react';
import { spoolbuddyApi, type SpoolBuddyDevice } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { formatRelativeTime } from '../utils/date';

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const remM = m % 60;
  if (h < 24) return remM ? `${h}h ${remM}m` : `${h}h`;
  const d = Math.floor(h / 24);
  const remH = h % 24;
  return remH ? `${d}d ${remH}h` : `${d}d`;
}

function formatMB(mb?: number): string {
  if (mb === undefined || mb === null) return '—';
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

interface DeviceCardProps {
  device: SpoolBuddyDevice;
  onUnregister: (device: SpoolBuddyDevice) => void;
  isDeleting: boolean;
}

type ActionKey = 'update' | 'restart_browser' | 'restart_daemon' | 'reboot' | 'shutdown';

function DeviceCard({ device, onUnregister, isDeleting }: DeviceCardProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const stats = device.system_stats;
  const mem = stats?.memory;
  const disk = stats?.disk;
  const online = device.online;
  const [pendingAction, setPendingAction] = useState<ActionKey | null>(null);
  const [busyAction, setBusyAction] = useState<ActionKey | null>(null);

  const runAction = async (action: ActionKey) => {
    setBusyAction(action);
    try {
      if (action === 'update') {
        await spoolbuddyApi.triggerUpdate(device.device_id);
      } else {
        await spoolbuddyApi.systemCommand(device.device_id, action);
      }
      showToast(t('settings.spoolbuddy.commandQueued'), 'success');
    } catch (err) {
      const msg = err instanceof Error ? err.message : t('settings.spoolbuddy.commandError');
      showToast(msg, 'error');
    } finally {
      setBusyAction(null);
      setPendingAction(null);
    }
  };

  const actions: { key: ActionKey; label: string; icon: typeof Download; variant?: 'danger' }[] = [
    { key: 'update', label: t('settings.spoolbuddy.update'), icon: Download },
    { key: 'restart_browser', label: t('settings.spoolbuddy.restartBrowser'), icon: Monitor },
    { key: 'restart_daemon', label: t('settings.spoolbuddy.restartDaemon'), icon: RefreshCw },
    { key: 'reboot', label: t('settings.spoolbuddy.reboot'), icon: RotateCw },
    { key: 'shutdown', label: t('settings.spoolbuddy.shutdown'), icon: Power, variant: 'danger' },
  ];

  const confirmTitles: Record<ActionKey, string> = {
    update: t('settings.spoolbuddy.updateConfirmTitle'),
    restart_browser: t('settings.spoolbuddy.restartBrowserConfirmTitle'),
    restart_daemon: t('settings.spoolbuddy.restartDaemonConfirmTitle'),
    reboot: t('settings.spoolbuddy.rebootConfirmTitle'),
    shutdown: t('settings.spoolbuddy.shutdownConfirmTitle'),
  };

  const confirmBodies: Record<ActionKey, string> = {
    update: t('settings.spoolbuddy.updateConfirmBody', { hostname: device.hostname }),
    restart_browser: t('settings.spoolbuddy.restartBrowserConfirmBody', { hostname: device.hostname }),
    restart_daemon: t('settings.spoolbuddy.restartDaemonConfirmBody', { hostname: device.hostname }),
    reboot: t('settings.spoolbuddy.rebootConfirmBody', { hostname: device.hostname }),
    shutdown: t('settings.spoolbuddy.shutdownConfirmBody', { hostname: device.hostname }),
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-white truncate">{device.hostname}</h3>
              <span
                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${
                  online
                    ? 'bg-green-500/15 text-green-400 border border-green-500/40'
                    : 'bg-gray-500/15 text-gray-400 border border-gray-500/40'
                }`}
              >
                {online ? <Wifi className="w-3 h-3" /> : <WifiOff className="w-3 h-3" />}
                {online ? t('settings.spoolbuddy.online') : t('settings.spoolbuddy.offline')}
              </span>
            </div>
            <p className="text-xs text-bambu-gray font-mono mt-1 truncate">{device.device_id}</p>
          </div>
          <Button
            variant="danger"
            size="sm"
            onClick={() => onUnregister(device)}
            disabled={isDeleting}
            aria-label={t('settings.spoolbuddy.unregister')}
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">{t('settings.spoolbuddy.unregister')}</span>
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* Connection */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          <div>
            <div className="text-bambu-gray">{t('settings.spoolbuddy.ipAddress')}</div>
            <div className="text-white font-mono">{device.ip_address}</div>
          </div>
          <div>
            <div className="text-bambu-gray">{t('settings.spoolbuddy.firmware')}</div>
            <div className="text-white">{device.firmware_version ?? '—'}</div>
          </div>
          <div>
            <div className="text-bambu-gray flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {t('settings.spoolbuddy.lastSeen')}
            </div>
            <div className="text-white">
              {device.last_seen ? formatRelativeTime(device.last_seen) : t('settings.spoolbuddy.never')}
            </div>
          </div>
          <div>
            <div className="text-bambu-gray">{t('settings.spoolbuddy.daemonUptime')}</div>
            <div className="text-white">{formatUptime(device.uptime_s)}</div>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap gap-2">
          {actions.map(({ key, label, icon: Icon, variant }) => (
            <Button
              key={key}
              variant={variant ?? 'secondary'}
              size="sm"
              onClick={() => setPendingAction(key)}
              disabled={!online || busyAction !== null}
              aria-label={label}
            >
              {busyAction === key ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Icon className="w-3.5 h-3.5" />
              )}
              <span>{label}</span>
            </Button>
          ))}
        </div>

        {/* Hardware flags */}
        <div className="flex items-center gap-3 text-xs flex-wrap">
          <span className="flex items-center gap-1 text-bambu-gray">
            {device.nfc_ok ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
            ) : (
              <XCircle className="w-3.5 h-3.5 text-red-400" />
            )}
            {t('settings.spoolbuddy.nfc')}
            {device.nfc_reader_type && <span className="text-bambu-gray/70">({device.nfc_reader_type})</span>}
          </span>
          <span className="flex items-center gap-1 text-bambu-gray">
            {device.scale_ok ? (
              <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
            ) : (
              <XCircle className="w-3.5 h-3.5 text-red-400" />
            )}
            {t('settings.spoolbuddy.scale')}
          </span>
        </div>

        {/* System stats */}
        {stats && (
          <div className="pt-2 border-t border-bambu-dark-tertiary">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 text-xs">
              {stats.cpu_temp_c !== undefined && (
                <div className="flex items-center gap-2">
                  <Thermometer className="w-3.5 h-3.5 text-bambu-gray" />
                  <div>
                    <div className="text-bambu-gray">{t('settings.spoolbuddy.cpuTemp')}</div>
                    <div className="text-white">{stats.cpu_temp_c.toFixed(1)}°C</div>
                  </div>
                </div>
              )}
              {mem && mem.percent !== undefined && (
                <div className="flex items-center gap-2">
                  <Cpu className="w-3.5 h-3.5 text-bambu-gray" />
                  <div>
                    <div className="text-bambu-gray">{t('settings.spoolbuddy.memory')}</div>
                    <div className="text-white">
                      {mem.percent.toFixed(0)}% ({formatMB(mem.used_mb)} / {formatMB(mem.total_mb)})
                    </div>
                  </div>
                </div>
              )}
              {disk && disk.percent !== undefined && (
                <div className="flex items-center gap-2">
                  <HardDrive className="w-3.5 h-3.5 text-bambu-gray" />
                  <div>
                    <div className="text-bambu-gray">{t('settings.spoolbuddy.disk')}</div>
                    <div className="text-white">
                      {disk.percent.toFixed(0)}% ({disk.used_gb?.toFixed(1)} / {disk.total_gb?.toFixed(1)} GB)
                    </div>
                  </div>
                </div>
              )}
              {stats.system_uptime_s !== undefined && (
                <div className="flex items-center gap-2">
                  <Clock className="w-3.5 h-3.5 text-bambu-gray" />
                  <div>
                    <div className="text-bambu-gray">{t('settings.spoolbuddy.systemUptime')}</div>
                    <div className="text-white">{formatUptime(stats.system_uptime_s)}</div>
                  </div>
                </div>
              )}
            </div>
            {stats.os && (
              <div className="mt-3 text-xs text-bambu-gray font-mono truncate">
                {[stats.os.os, stats.os.kernel, stats.os.arch, stats.os.python && `Python ${stats.os.python}`]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
            )}
          </div>
        )}
      </CardContent>
      {pendingAction && (
        <ConfirmModal
          variant={pendingAction === 'shutdown' || pendingAction === 'reboot' ? 'danger' : 'default'}
          title={confirmTitles[pendingAction]}
          message={confirmBodies[pendingAction]}
          confirmText={t('settings.spoolbuddy.commandConfirm')}
          isLoading={busyAction !== null}
          onConfirm={() => runAction(pendingAction)}
          onCancel={() => setPendingAction(null)}
        />
      )}
    </Card>
  );
}

export function SpoolBuddySettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [pendingDelete, setPendingDelete] = useState<SpoolBuddyDevice | null>(null);

  const { data: devices = [], isLoading } = useQuery({
    queryKey: ['spoolbuddy-devices'],
    queryFn: () => spoolbuddyApi.getDevices(),
    refetchInterval: 15000,
  });

  const deleteMutation = useMutation({
    mutationFn: (deviceId: string) => spoolbuddyApi.deleteDevice(deviceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spoolbuddy-devices'] });
      showToast(t('settings.spoolbuddy.unregisterSuccess'), 'success');
      setPendingDelete(null);
    },
    onError: (err: Error) => {
      showToast(err.message || t('settings.spoolbuddy.unregisterError'), 'error');
    },
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-8 flex justify-center">
          <Loader2 className="w-6 h-6 animate-spin text-bambu-green" />
        </CardContent>
      </Card>
    );
  }

  const hasDuplicates = devices.length > 1;

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="py-3 px-4">
          <div className="flex items-start gap-2 text-xs">
            <Info className="w-4 h-4 text-blue-400 flex-shrink-0 mt-0.5" />
            <div className="text-bambu-gray">
              <p className="text-white font-medium mb-1">{t('settings.spoolbuddy.infoTitle')}</p>
              <p>{t('settings.spoolbuddy.infoBody')}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {hasDuplicates && (
        <Card className="border-l-4 border-l-yellow-500">
          <CardContent className="py-3 px-4">
            <div className="flex items-start gap-2 text-xs">
              <AlertTriangle className="w-4 h-4 text-yellow-500 flex-shrink-0 mt-0.5" />
              <div className="text-bambu-gray">
                <p className="text-white font-medium mb-1">
                  {t('settings.spoolbuddy.duplicatesTitle', { count: devices.length })}
                </p>
                <p>{t('settings.spoolbuddy.duplicatesBody')}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {devices.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-bambu-gray text-sm">
            {t('settings.spoolbuddy.empty')}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {devices.map((device) => (
            <DeviceCard
              key={device.id}
              device={device}
              onUnregister={setPendingDelete}
              isDeleting={deleteMutation.isPending && deleteMutation.variables === device.device_id}
            />
          ))}
        </div>
      )}

      {pendingDelete && (
        <ConfirmModal
          variant="danger"
          title={t('settings.spoolbuddy.confirmTitle')}
          message={t('settings.spoolbuddy.confirmBody', {
            hostname: pendingDelete.hostname,
            deviceId: pendingDelete.device_id,
          })}
          confirmText={t('settings.spoolbuddy.unregister')}
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(pendingDelete.device_id)}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}
