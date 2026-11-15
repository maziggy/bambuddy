import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Printer } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useUiSettings } from '../hooks/useUiSettings';
import { useToast } from '../contexts/ToastContext';
import { BUILD_PLATES_QUERY_KEY, groupPlatesByBaseType } from '../utils/buildPlates';
import { PlateImage } from './PlateImage';
import { ConfirmModal } from './ConfirmModal';

// Printer states in which a plate is (or is about to be) printed on (#1306).
const PRINTING_STATES = new Set(['PREPARE', 'SLICING', 'RUNNING', 'PAUSE']);

/**
 * Which build plate is on this printer (#1306), as a one-click swap.
 *
 * Renders nothing unless build plate tracking is enabled in settings, so the
 * printer card is unchanged for everyone who has not opted in. Only enabled
 * plates are offered, plus whatever is currently installed even if it has
 * since been unticked, so the select never shows a blank value.
 */
export function InstalledPlateSelector({ printer }: { printer: Printer }) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const { data: settings } = useUiSettings();
  const tracking = settings?.build_plate_tracking_enabled === true;
  const { data: plates = [] } = useQuery({
    queryKey: BUILD_PLATES_QUERY_KEY,
    queryFn: api.getBuildPlates,
    enabled: tracking,
  });

  // Shares the printer card's status query, so this adds no polling of its own.
  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    enabled: tracking,
  });
  const isPrinting = PRINTING_STATES.has(status?.state ?? '');
  // A swap chosen while printing, waiting for the user to confirm it.
  const [pendingSwap, setPendingSwap] = useState<{ plateId: number | null } | null>(null);

  const mutation = useMutation({
    mutationFn: (plateId: number | null) => api.setInstalledPlate(printer.id, plateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: BUILD_PLATES_QUERY_KEY });
    },
    onError: (e: Error) => showToast(e.message || t('printers.buildPlate.swapFailed'), 'error'),
  });

  if (!tracking) return null;

  const installedId = printer.installed_plate_id ?? null;
  const installed = plates.find((p) => p.id === installedId) ?? null;
  // Unticked plates stay on offer while installed here or while a queued job
  // still waits for them — otherwise that job could never be released.
  const offered = plates.filter((p) => p.enabled || p.id === installedId || p.required_by_pending > 0);
  const canSwap = hasPermission('printers:control');

  return (
    <div className="flex items-center gap-1.5 mt-1 min-w-0" onClick={(e) => e.stopPropagation()}>
      {installed && <PlateImage plate={installed} className="w-4 h-4 rounded-sm object-cover flex-shrink-0" />}
      <select
        value={installedId ?? ''}
        disabled={!canSwap || mutation.isPending}
        onChange={(e) => {
          const plateId = e.target.value === '' ? null : Number(e.target.value);
          // Mid-print the plate on the bed is still the old one. Recording the
          // next plate early lets the queue send a job for it the moment this
          // print ends, so make sure the swap has really happened.
          if (isPrinting) setPendingSwap({ plateId });
          else mutation.mutate(plateId);
        }}
        title={canSwap ? t('printers.buildPlate.swap') : t('printers.permission.noControl')}
        aria-label={t('printers.buildPlate.label')}
        className="min-w-0 max-w-full truncate bg-transparent text-xs text-bambu-gray hover:text-white border border-bambu-dark-tertiary rounded px-1.5 py-0.5 focus:border-bambu-green focus:outline-none disabled:opacity-60"
      >
        <option value="">{t('printers.buildPlate.notTracked')}</option>
        {groupPlatesByBaseType(offered).map((group) => (
          <optgroup key={group.baseType} label={group.label}>
            {group.plates.map((plate) => (
              <option key={plate.id} value={plate.id}>
                {plate.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      {pendingSwap && (
        <ConfirmModal
          title={t('printers.buildPlate.confirmMidPrintTitle')}
          message={t('printers.buildPlate.confirmMidPrintMessage', { name: printer.name })}
          confirmText={t('printers.buildPlate.confirmMidPrintConfirm')}
          variant="warning"
          onConfirm={() => {
            mutation.mutate(pendingSwap.plateId);
            setPendingSwap(null);
          }}
          onCancel={() => setPendingSwap(null)}
        />
      )}
    </div>
  );
}
