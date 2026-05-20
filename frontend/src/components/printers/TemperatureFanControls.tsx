/**
 * Live temperature and fan speed controls on the printer card.
 */

import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Power } from 'lucide-react';
import { api } from '../../api/client';
import { useTranslation } from 'react-i18next';
import { useToast } from '../../contexts/ToastContext';

export interface PrinterControlLimits {
  bed_min: number;
  bed_max: number;
  nozzle_min: number;
  nozzle_max: number;
  chamber_min: number;
  chamber_max: number;
  fans: number[];
  dual_nozzle: boolean;
}

type TempPopoverKind = 'bed' | 'nozzle' | 'chamber' | { nozzle: 0 | 1 };

function TempPopover({
  label,
  current,
  target,
  min,
  max,
  onClose,
  onApply,
  isPending,
  canControl,
}: {
  label: string;
  current: number;
  target: number;
  min: number;
  max: number;
  onClose: () => void;
  onApply: (value: number) => void;
  isPending: boolean;
  canControl: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(target > 0 ? target : Math.min(60, max));

  useEffect(() => {
    setDraft(target > 0 ? target : Math.min(60, max));
  }, [target, max]);

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 w-[200px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg p-2.5">
        <p className="text-[10px] text-bambu-gray mb-1.5">{label}</p>
        <p className="text-[9px] text-bambu-gray/70 mb-2">
          {t('printers.temperatureControl.current', { temp: Math.round(current) })}
        </p>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-bambu-gray">{t('printers.temperatureControl.target')}</span>
          <div className="flex items-center gap-1">
            <input
              type="number"
              min={min}
              max={max}
              value={draft}
              disabled={!canControl}
              onChange={(e) => setDraft(Math.min(max, Math.max(min, Number(e.target.value) || min)))}
              className="w-12 px-1 py-0.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-[11px] text-center focus:outline-none focus:border-bambu-green/50 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
            />
            <span className="text-[10px] text-bambu-gray">°C</span>
          </div>
        </div>
        <input
          type="range"
          min={min}
          max={max}
          value={draft}
          disabled={!canControl}
          onChange={(e) => setDraft(Number(e.target.value))}
          className="w-full h-1 accent-bambu-green cursor-pointer mb-2"
        />
        <div className="flex justify-between text-[9px] text-bambu-gray/50 mb-2">
          <span>{min}°C</span>
          <span>{max}°C</span>
        </div>
        <div className="flex gap-1.5">
          <button
            type="button"
            disabled={!canControl || isPending}
            onClick={() => onApply(0)}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 rounded bg-bambu-dark-tertiary hover:bg-bambu-dark text-[10px] text-bambu-gray disabled:opacity-50"
            title={t('printers.temperatureControl.turnOff')}
          >
            <Power className="w-3 h-3" />
            {t('printers.temperatureControl.off')}
          </button>
          <button
            type="button"
            disabled={!canControl || isPending}
            onClick={() => onApply(draft)}
            className="flex-1 py-1.5 rounded bg-bambu-green/20 hover:bg-bambu-green/30 text-[10px] text-bambu-green font-medium disabled:opacity-50"
          >
            {isPending ? <Loader2 className="w-3 h-3 animate-spin mx-auto" /> : t('printers.temperatureControl.apply')}
          </button>
        </div>
      </div>
    </>
  );
}

function FanPopover({
  label,
  currentPercent,
  onClose,
  onApply,
  isPending,
  canControl,
}: {
  label: string;
  currentPercent: number;
  onClose: () => void;
  onApply: (percent: number) => void;
  isPending: boolean;
  canControl: boolean;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(currentPercent);

  useEffect(() => {
    setDraft(currentPercent);
  }, [currentPercent]);

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute bottom-full left-0 mb-1 z-50 w-[180px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg p-2.5">
        <p className="text-[10px] text-bambu-gray mb-2">{label}</p>
        <div className="flex items-center justify-between mb-1">
          <span className="text-[10px] text-bambu-gray">{t('printers.fanControl.speed')}</span>
          <span className="text-[11px] text-white tabular-nums">{draft}%</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={draft}
          disabled={!canControl}
          onChange={(e) => setDraft(Number(e.target.value))}
          className="w-full h-1 accent-cyan-500 cursor-pointer mb-2"
        />
        <button
          type="button"
          disabled={!canControl || isPending}
          onClick={() => onApply(draft)}
          className="w-full py-1.5 rounded bg-cyan-500/20 hover:bg-cyan-500/30 text-[10px] text-cyan-400 font-medium disabled:opacity-50"
        >
          {isPending ? <Loader2 className="w-3 h-3 animate-spin mx-auto" /> : t('printers.fanControl.apply')}
        </button>
      </div>
    </>
  );
}

export function usePrinterControlLimits(printerId: number, enabled: boolean) {
  return useQuery({
    queryKey: ['printerControlLimits', printerId],
    queryFn: () => api.getPrinterControlLimits(printerId),
    enabled,
    staleTime: 60_000,
  });
}

export function TemperatureControlButton({
  printerId,
  kind,
  label,
  current = 0,
  target = 0,
  min,
  max,
  connected,
  canControl,
  className = '',
  children,
}: {
  printerId: number;
  kind: TempPopoverKind;
  label: string;
  current?: number;
  target?: number;
  min: number;
  max: number;
  connected: boolean;
  canControl: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const bedMutation = useMutation({
    mutationFn: (value: number) => api.setBedTemperature(printerId, value),
    onSuccess: (res) => {
      showToast(res.message);
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const nozzleMutation = useMutation({
    mutationFn: ({ value, nozzle }: { value: number; nozzle: number }) =>
      api.setNozzleTemperature(printerId, value, nozzle),
    onSuccess: (res) => {
      showToast(res.message);
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const chamberMutation = useMutation({
    mutationFn: (value: number) => api.setChamberTemperature(printerId, value),
    onSuccess: (res) => {
      showToast(res.message);
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const disabled = !connected || !canControl;
  const isPending = bedMutation.isPending || nozzleMutation.isPending || chamberMutation.isPending;

  const handleApply = (value: number) => {
    if (kind === 'bed') {
      bedMutation.mutate(value);
    } else if (kind === 'chamber') {
      chamberMutation.mutate(value);
    } else if (kind === 'nozzle') {
      nozzleMutation.mutate({ value, nozzle: 0 });
    } else {
      nozzleMutation.mutate({ value, nozzle: kind.nozzle });
    }
  };

  return (
    <div className={`relative ${className}`}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`w-full h-full ${disabled ? 'cursor-default' : 'cursor-pointer hover:ring-1 hover:ring-white/10 rounded-lg'}`}
        title={
          disabled
            ? !canControl
              ? t('printers.permission.noControl')
              : undefined
            : t('printers.temperatureControl.adjust')
        }
      >
        {children}
      </button>
      {open && !disabled && (
        <TempPopover
          label={label}
          current={current}
          target={target}
          min={min}
          max={max}
          onClose={() => setOpen(false)}
          onApply={handleApply}
          isPending={isPending}
          canControl={canControl}
        />
      )}
    </div>
  );
}

export function FanControlButton({
  printerId,
  fan,
  label,
  currentPercent = 0,
  connected,
  canControl,
  supported,
  className = '',
  children,
}: {
  printerId: number;
  fan: 1 | 2 | 3;
  label: string;
  currentPercent?: number;
  connected: boolean;
  canControl: boolean;
  supported: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: (percent: number) => api.setFanSpeed(printerId, fan, percent),
    onSuccess: (res) => {
      showToast(res.message);
      setOpen(false);
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const badgeClass = `flex items-center gap-1 px-1.5 py-1 rounded ${className}`;

  // Read-only status badge (same layout as before fan controls were added)
  if (!supported) {
    return (
      <div className={badgeClass} title={label}>
        {children}
      </div>
    );
  }

  const disabled = !connected || !canControl;

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`${badgeClass} transition-colors ${
          disabled ? 'cursor-not-allowed' : 'hover:ring-1 hover:ring-white/15'
        }`}
        title={disabled ? label : t('printers.fanControl.adjust')}
      >
        {children}
      </button>
      {open && !disabled && (
        <FanPopover
          label={label}
          currentPercent={currentPercent}
          onClose={() => setOpen(false)}
          onApply={(p) => mutation.mutate(p)}
          isPending={mutation.isPending}
          canControl={canControl}
        />
      )}
    </div>
  );
}
