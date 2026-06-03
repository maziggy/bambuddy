import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Gauge, Snowflake, Sun } from 'lucide-react';
import { api, type PrinterStatus } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import type { PrinterControlCapabilities } from '../../utils/printerCapabilities';
import { ChamberLight } from '../icons/ChamberLight';

interface PrinterMiscControlsProps {
  printerId: number;
  status: PrinterStatus;
  capabilities: PrinterControlCapabilities;
  canControl: boolean;
}

export function PrinterMiscControls({
  printerId,
  status,
  capabilities,
  canControl,
}: PrinterMiscControlsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const disabled = !canControl || !status.connected;

  const speedLabels: Record<number, string> = {
    1: t('printers.speed.silent'),
    2: t('printers.speed.standard'),
    3: t('printers.speed.sport'),
    4: t('printers.speed.ludicrous'),
  };

  const speedMutation = useMutation({
    mutationFn: (mode: number) => api.setPrintSpeed(printerId, mode),
    onMutate: async (mode) => {
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printerId] });
      const prev = queryClient.getQueryData(['printerStatus', printerId]);
      queryClient.setQueryData(['printerStatus', printerId], (old: PrinterStatus | undefined) =>
        old ? { ...old, speed_level: mode } : old
      );
      return { prev };
    },
    onError: (e: Error, _, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['printerStatus', printerId], ctx.prev);
      showToast(e.message, 'error');
    },
  });

  const lightMutation = useMutation({
    mutationFn: (on: boolean) => api.setChamberLight(printerId, on),
    onMutate: async (on) => {
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printerId] });
      const prev = queryClient.getQueryData(['printerStatus', printerId]);
      queryClient.setQueryData(['printerStatus', printerId], (old: PrinterStatus | undefined) =>
        old ? { ...old, chamber_light: on } : old
      );
      return { prev };
    },
    onError: (e: Error, _, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(['printerStatus', printerId], ctx.prev);
      showToast(e.message, 'error');
    },
  });

  const airductMutation = useMutation({
    mutationFn: (mode: 'cooling' | 'heating') => api.setAirductMode(printerId, mode),
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const isPrinting = status.state === 'RUNNING' || status.state === 'PAUSE';

  const hasAny =
    isPrinting || capabilities.showChamberLight || capabilities.showAirduct;
  if (!hasAny) return null;

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
        {t('printerDetail.misc')}
      </h3>

      <div className="flex flex-wrap gap-2">
        {isPrinting && (
          <label className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-amber-500/10 text-amber-400 text-xs">
            <Gauge className="w-3.5 h-3.5 flex-shrink-0" />
            <select
              disabled={disabled || speedMutation.isPending}
              value={status.speed_level || 2}
              onChange={(e) => speedMutation.mutate(parseInt(e.target.value, 10))}
              className="bg-transparent border-none text-amber-400 text-xs focus:outline-none cursor-pointer disabled:cursor-not-allowed max-w-[140px]"
            >
              {[1, 2, 3, 4].map((m) => (
                <option key={m} value={m} className="bg-bambu-dark text-white">
                  {speedLabels[m]}
                </option>
              ))}
            </select>
          </label>
        )}

        {capabilities.showChamberLight && (
          <button
            type="button"
            disabled={disabled || lightMutation.isPending}
            onClick={() => lightMutation.mutate(!status.chamber_light)}
            className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs border ${
              status.chamber_light
                ? 'border-yellow-500/50 text-yellow-400 bg-yellow-500/10'
                : 'border-bambu-dark-tertiary text-bambu-gray bg-bambu-dark'
            }`}
          >
            <ChamberLight on={status.chamber_light} className="w-4 h-4" />
            {t('printerDetail.lamp')}
          </button>
        )}

        {capabilities.showAirduct && (
          <>
            <button
              type="button"
              disabled={disabled || airductMutation.isPending}
              onClick={() => airductMutation.mutate('cooling')}
              className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs ${
                status.airduct_mode === 0
                  ? 'bg-blue-500/20 text-blue-400'
                  : 'bg-bambu-dark text-bambu-gray border border-bambu-dark-tertiary'
              }`}
            >
              <Snowflake className="w-3.5 h-3.5" />
              {t('printers.airduct.cooling')}
            </button>
            <button
              type="button"
              disabled={disabled || airductMutation.isPending}
              onClick={() => airductMutation.mutate('heating')}
              className={`flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs ${
                status.airduct_mode === 1
                  ? 'bg-orange-500/20 text-orange-400'
                  : 'bg-bambu-dark text-bambu-gray border border-bambu-dark-tertiary'
              }`}
            >
              <Sun className="w-3.5 h-3.5" />
              {t('printers.airduct.heating')}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
