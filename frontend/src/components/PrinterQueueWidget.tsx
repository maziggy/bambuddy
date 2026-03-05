import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Clock, Calendar, ChevronRight, Loader2, CircleCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { formatRelativeTime } from '../utils/date';

interface PrinterQueueWidgetProps {
  printerId: number;
  printerModel?: string | null;
  printerState?: string | null;
  plateCleared?: boolean;
  loadedFilamentTypes?: Set<string>;
  loadedFilaments?: Set<string>;  // "TYPE:rrggbb" pairs for filament override color matching
}

export function PrinterQueueWidget({ printerId, printerModel, printerState, plateCleared, loadedFilamentTypes, loadedFilaments }: PrinterQueueWidgetProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const { data: queue } = useQuery({
    queryKey: ['queue', printerId, 'pending', printerModel],
    queryFn: () => api.getQueue(printerId, 'pending', printerModel || undefined),
    refetchInterval: 30000,
  });

  const clearPlateMutation = useMutation({
    mutationFn: () => api.clearPlate(printerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue', printerId] });
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });
      showToast(t('queue.clearPlateSuccess'), 'success');
    },
    onError: (err: Error) => {
      showToast(err.message, 'error');
    },
  });

  // Filter queue to items this printer can actually print (filament type + color check)
  const compatibleQueue = queue?.filter(item => {
    // Type check: all required filament types must be loaded.
    // Only apply when loadedFilamentTypes is provided (not undefined).
    // An empty Set means no filaments are loaded — jobs requiring specific types are incompatible.
    if (item.required_filament_types?.length && loadedFilamentTypes !== undefined) {
      if (!item.required_filament_types.every((t: string) => loadedFilamentTypes.has(t.toUpperCase()))) {
        return false;
      }
    }
    // Color check: evaluate force_color_match per slot
    // Mirrors backend _find_idle_printer_for_model() logic.
    // Only apply when loadedFilaments is provided (not undefined).
    // An empty Set means no filaments are loaded — force-matched slots cannot match.
    if (item.filament_overrides?.length && loadedFilaments !== undefined) {
      const forceOverrides = item.filament_overrides.filter(o => o.force_color_match === true);
      const prefOverrides = item.filament_overrides.filter(o => o.force_color_match !== true);

      // All force-matched slots must have exact type+color on this printer
      if (forceOverrides.length > 0) {
        const allForceMatch = forceOverrides.every(o => {
          const oType = (o.type || '').toUpperCase();
          const oColor = (o.color || '').replace('#', '').toLowerCase().slice(0, 6);
          return loadedFilaments.has(`${oType}:${oColor}`);
        });
        if (!allForceMatch) return false;
      }

      // Preference-only overrides: at least one color must match (existing behaviour)
      if (prefOverrides.length > 0 && forceOverrides.length === 0) {
        const hasColorMatch = prefOverrides.some(o => {
          const oType = (o.type || '').toUpperCase();
          const oColor = (o.color || '').replace('#', '').toLowerCase().slice(0, 6);
          return loadedFilaments.has(`${oType}:${oColor}`);
        });
        if (!hasColorMatch) return false;
      }
    }
    return true;
  });

  const nextItem = compatibleQueue?.[0];
  const totalPending = compatibleQueue?.length || 0;

  if (totalPending === 0) {
    return null;
  }

  const needsClearPlate = (printerState === 'FINISH' || printerState === 'FAILED') && !plateCleared;

  if (needsClearPlate) {
    return (
      <div className="mb-3 p-3 bg-bambu-dark rounded-lg border border-yellow-400/30">
        <div className="flex items-center gap-3 mb-2">
          <Calendar className="w-5 h-5 text-yellow-400 flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-bambu-gray">{t('queue.nextInQueue')}</p>
            <p className="text-sm text-white truncate">
              {nextItem?.archive_name || nextItem?.library_file_name || `File #${nextItem?.archive_id || nextItem?.library_file_id}`}
            </p>
          </div>
          {totalPending > 1 && (
            <span className="text-xs px-1.5 py-0.5 bg-yellow-400/20 text-yellow-400 rounded flex-shrink-0">
              +{totalPending - 1}
            </span>
          )}
        </div>
        {clearPlateMutation.isSuccess ? (
          <div className="w-full py-2 px-3 rounded-lg bg-bambu-green/10 border border-bambu-green/20 text-bambu-green text-sm flex items-center justify-center gap-2">
            <CircleCheck className="w-4 h-4" />
            {t('queue.plateReady')}
          </div>
        ) : (
          <button
            onClick={() => clearPlateMutation.mutate()}
            disabled={clearPlateMutation.isPending || !hasPermission('printers:clear_plate')}
            className="w-full py-2 px-3 rounded-lg bg-bambu-green/20 border border-bambu-green/40 text-bambu-green hover:bg-bambu-green/30 transition-colors text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {clearPlateMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <CircleCheck className="w-4 h-4" />
            )}
            {t('queue.clearPlate')}
          </button>
        )}
      </div>
    );
  }

  return (
    <Link
      to="/queue"
      className="block mb-3 p-3 bg-bambu-dark rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <Calendar className="w-5 h-5 text-yellow-400 flex-shrink-0" />
          <div className="min-w-0 flex-1">
            <p className="text-xs text-bambu-gray">{t('queue.nextInQueue')}</p>
            <p className="text-sm text-white truncate">
              {nextItem?.archive_name || nextItem?.library_file_name || `File #${nextItem?.archive_id || nextItem?.library_file_id}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-bambu-gray flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {nextItem?.scheduled_time ? formatRelativeTime(nextItem.scheduled_time, 'system', t) : t('time.waiting')}
          </span>
          {totalPending > 1 && (
            <span className="text-xs px-1.5 py-0.5 bg-yellow-400/20 text-yellow-400 rounded">
              +{totalPending - 1}
            </span>
          )}
          <ChevronRight className="w-4 h-4 text-bambu-gray" />
        </div>
      </div>
    </Link>
  );
}
