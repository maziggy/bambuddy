import { useState, useEffect, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { PowerOff, RotateCw, Monitor, ChevronDown, Loader2 } from 'lucide-react';
import { api, spoolbuddyApi, type Printer, type SmartPlug, type SmartPlugStatus } from '../../api/client';

interface SpoolBuddyQuickMenuProps {
  isOpen: boolean;
  onClose: () => void;
  deviceId: string | null;
  deviceOnline: boolean;
}

type SystemCommand = 'reboot' | 'shutdown' | 'restart_daemon' | 'restart_browser';

type PendingConfirm =
  | { type: 'system'; command: SystemCommand }
  | { type: 'plug'; plug: SmartPlug; printer: Printer; currentState: string | null };

interface PlugState {
  plug: SmartPlug;
  printer: Printer;
  status: SmartPlugStatus | null;
  loading: boolean;
}

export function SpoolBuddyQuickMenu({ isOpen, onClose, deviceId, deviceOnline }: SpoolBuddyQuickMenuProps) {
  const { t } = useTranslation();
  const [pendingConfirm, setPendingConfirm] = useState<PendingConfirm | null>(null);
  const [commandBusy, setCommandBusy] = useState(false);
  const [plugStates, setPlugStates] = useState<Map<number, { loading: boolean; state: string | null }>>(new Map());

  // Fetch printers and smart plugs
  const { data: printers = [] } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen,
  });

  const { data: smartPlugs = [] } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: () => api.getSmartPlugs(),
    enabled: isOpen,
  });

  // Build printer-plug pairs (only main power plugs linked to printers)
  const printerPlugs: PlugState[] = printers
    .map((printer) => {
      const plug = smartPlugs.find(
        (p) => p.printer_id === printer.id && p.plug_type !== 'mqtt' && p.enabled
      );
      if (!plug) return null;
      const state = plugStates.get(plug.id);
      return {
        plug,
        printer,
        status: state ? { state: state.state, reachable: true, device_name: null, energy: null } : null,
        loading: state?.loading ?? false,
      };
    })
    .filter(Boolean) as PlugState[];

  // Fetch plug statuses when menu opens
  useEffect(() => {
    if (!isOpen || smartPlugs.length === 0) return;

    const linkedPlugs = smartPlugs.filter(
      (p) => p.printer_id !== null && p.plug_type !== 'mqtt' && p.enabled
    );

    linkedPlugs.forEach(async (plug) => {
      try {
        const status = await api.getSmartPlugStatus(plug.id);
        setPlugStates((prev) => {
          const next = new Map(prev);
          next.set(plug.id, { loading: false, state: status.state });
          return next;
        });
      } catch {
        setPlugStates((prev) => {
          const next = new Map(prev);
          next.set(plug.id, { loading: false, state: null });
          return next;
        });
      }
    });
  }, [isOpen, smartPlugs]);

  // Clear state when menu closes
  useEffect(() => {
    if (!isOpen) {
      setPendingConfirm(null);
      setCommandBusy(false);
    }
  }, [isOpen]);

  const handleTogglePlug = useCallback(async (plug: SmartPlug) => {
    setPlugStates((prev) => {
      const next = new Map(prev);
      const current = next.get(plug.id);
      next.set(plug.id, { loading: true, state: current?.state ?? null });
      return next;
    });

    try {
      await api.controlSmartPlug(plug.id, 'toggle');
      const status = await api.getSmartPlugStatus(plug.id);
      setPlugStates((prev) => {
        const next = new Map(prev);
        next.set(plug.id, { loading: false, state: status.state });
        return next;
      });
    } catch {
      setPlugStates((prev) => {
        const next = new Map(prev);
        const current = next.get(plug.id);
        next.set(plug.id, { loading: false, state: current?.state ?? null });
        return next;
      });
    }
  }, []);

  const handleSystemCommand = useCallback(async (command: SystemCommand) => {
    if (!deviceId) return;
    setCommandBusy(true);
    try {
      await spoolbuddyApi.systemCommand(deviceId, command);
      // Close menu after successful command
      setTimeout(() => onClose(), 500);
    } catch {
      setCommandBusy(false);
    }
  }, [deviceId, onClose]);

  const executeConfirmed = useCallback(() => {
    if (!pendingConfirm) return;
    if (pendingConfirm.type === 'system') {
      handleSystemCommand(pendingConfirm.command);
    } else {
      handleTogglePlug(pendingConfirm.plug);
    }
    setPendingConfirm(null);
  }, [pendingConfirm, handleSystemCommand, handleTogglePlug]);

  if (!isOpen) return null;

  const isPlugOn = (state: string | null) => state === 'ON' || state === 'on';

  return (
    <>
      {/* Backdrop */}
      <div className="fixed inset-0 z-40 bg-black/50" onPointerDown={onClose} />

      {/* Slide-down panel */}
      <div className="fixed top-0 left-0 right-0 z-50 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary rounded-b-2xl shadow-2xl animate-slide-down">
        {/* Handle bar */}
        <div className="flex justify-center pt-2 pb-1">
          <div className="w-10 h-1 rounded-full bg-zinc-600" />
        </div>

        <div className="px-4 pb-4 max-h-[80vh] overflow-y-auto">
          {/* Printer Power Section */}
          {printerPlugs.length > 0 && (
            <div className="mb-4">
              <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
                {t('spoolbuddy.quickMenu.printerPower', 'Printer Power')}
              </h3>
              <div className="space-y-1">
                {printerPlugs.map(({ plug, printer, loading }) => {
                  const state = plugStates.get(plug.id);
                  const on = isPlugOn(state?.state ?? null);
                  return (
                    <button
                      key={plug.id}
                      onClick={() => setPendingConfirm({ type: 'plug', plug, printer, currentState: state?.state ?? null })}
                      disabled={loading}
                      className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg bg-zinc-800/60 hover:bg-zinc-700/60 transition-colors min-h-[36px]"
                    >
                      <div className={`w-2 h-2 rounded-full shrink-0 ${on ? 'bg-green-500' : 'bg-zinc-600'}`} />
                      {loading && <Loader2 className="w-3 h-3 animate-spin text-zinc-400 shrink-0" />}
                      <span className="flex-1 text-sm text-zinc-200 text-left truncate">{printer.name}</span>
                      <span className={`text-xs font-medium ${on ? 'text-green-400' : 'text-zinc-500'}`}>
                        {state?.state ?? '—'}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* System Controls Section */}
          <div>
            <h3 className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-2">
              {t('spoolbuddy.quickMenu.systemControls', 'System')}
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <SystemButton
                icon={<RotateCw className="w-4 h-4" />}
                label={t('spoolbuddy.quickMenu.restartDaemon', 'Restart Daemon')}
                onClick={() => setPendingConfirm({ type: 'system', command: 'restart_daemon' })}
                disabled={!deviceId || !deviceOnline || commandBusy}
              />
              <SystemButton
                icon={<Monitor className="w-4 h-4" />}
                label={t('spoolbuddy.quickMenu.restartBrowser', 'Restart Browser')}
                onClick={() => setPendingConfirm({ type: 'system', command: 'restart_browser' })}
                disabled={!deviceId || !deviceOnline || commandBusy}
              />
              <SystemButton
                icon={<RotateCw className="w-4 h-4" />}
                label={t('spoolbuddy.quickMenu.reboot', 'Reboot')}
                onClick={() => setPendingConfirm({ type: 'system', command: 'reboot' })}
                disabled={!deviceId || !deviceOnline || commandBusy}
                variant="warning"
              />
              <SystemButton
                icon={<PowerOff className="w-4 h-4" />}
                label={t('spoolbuddy.quickMenu.shutdown', 'Shutdown')}
                onClick={() => setPendingConfirm({ type: 'system', command: 'shutdown' })}
                disabled={!deviceId || !deviceOnline || commandBusy}
                variant="danger"
              />
            </div>
          </div>

          {/* Swipe hint */}
          <div className="flex justify-center mt-3">
            <div className="flex items-center gap-1 text-xs text-zinc-600">
              <ChevronDown className="w-3 h-3" />
              <span>{t('spoolbuddy.quickMenu.swipeToClose', 'Swipe down to close')}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Confirmation Dialog */}
      {pendingConfirm && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60">
          <div className="bg-zinc-800 rounded-2xl p-5 mx-4 max-w-sm w-full border border-zinc-700">
            <h3 className="text-lg font-semibold text-zinc-100 mb-2">
              {t('spoolbuddy.quickMenu.confirmTitle', 'Confirm')}
            </h3>
            <p className="text-sm text-zinc-400 mb-5">
              {pendingConfirm.type === 'plug'
                ? (isPlugOn(pendingConfirm.currentState)
                    ? t('spoolbuddy.quickMenu.confirmPlugOff', 'Turn off {{name}}?', { name: pendingConfirm.printer.name })
                    : t('spoolbuddy.quickMenu.confirmPlugOn', 'Turn on {{name}}?', { name: pendingConfirm.printer.name }))
                : pendingConfirm.command === 'shutdown'
                  ? t('spoolbuddy.quickMenu.confirmShutdown', 'Are you sure you want to shut down the SpoolBuddy? You will need physical access to turn it back on.')
                  : pendingConfirm.command === 'reboot'
                    ? t('spoolbuddy.quickMenu.confirmReboot', 'Are you sure you want to reboot the SpoolBuddy?')
                    : pendingConfirm.command === 'restart_daemon'
                      ? t('spoolbuddy.quickMenu.confirmRestartDaemon', 'Restart the SpoolBuddy daemon? NFC and scale will be temporarily unavailable.')
                      : t('spoolbuddy.quickMenu.confirmRestartBrowser', 'Restart the kiosk browser? The display will briefly go blank.')}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setPendingConfirm(null)}
                className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium bg-zinc-700 text-zinc-300 hover:bg-zinc-600 transition-colors min-h-[44px]"
              >
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                onClick={executeConfirmed}
                disabled={commandBusy}
                className={`flex-1 px-4 py-2.5 rounded-lg text-sm font-medium text-white transition-colors min-h-[44px] ${
                  pendingConfirm.type === 'plug'
                    ? (isPlugOn(pendingConfirm.currentState) ? 'bg-red-600 hover:bg-red-700' : 'bg-green-600 hover:bg-green-700')
                    : pendingConfirm.command === 'shutdown' ? 'bg-red-600 hover:bg-red-700'
                    : pendingConfirm.command === 'reboot' ? 'bg-amber-600 hover:bg-amber-700'
                    : 'bg-blue-600 hover:bg-blue-700'
                } disabled:opacity-50`}
              >
                {commandBusy ? <Loader2 className="w-4 h-4 animate-spin mx-auto" /> :
                  pendingConfirm.type === 'plug'
                    ? (isPlugOn(pendingConfirm.currentState)
                        ? t('spoolbuddy.quickMenu.turnOff', 'Turn Off')
                        : t('spoolbuddy.quickMenu.turnOn', 'Turn On'))
                    : t('spoolbuddy.quickMenu.confirm', 'Confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function SystemButton({
  icon,
  label,
  onClick,
  disabled,
  variant = 'default',
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  disabled: boolean;
  variant?: 'default' | 'warning' | 'danger';
}) {
  const variantClasses = {
    default: 'bg-zinc-800/60 hover:bg-zinc-700/60 text-zinc-300',
    warning: 'bg-amber-900/30 hover:bg-amber-900/50 text-amber-400',
    danger: 'bg-red-900/30 hover:bg-red-900/50 text-red-400',
  };

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`flex items-center gap-2.5 p-3 rounded-xl transition-colors min-h-[48px] disabled:opacity-40 ${variantClasses[variant]}`}
    >
      {icon}
      <span className="text-sm font-medium">{label}</span>
    </button>
  );
}
