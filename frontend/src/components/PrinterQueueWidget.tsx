import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Clock, Calendar, ChevronRight, Loader2, CircleCheck } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { parseUTCDate } from '../utils/date';

interface PrinterQueueWidgetProps {
  printerId: number;
  printerState?: string | null;
}

function formatRelativeTime(dateString: string | null): string {
  if (!dateString) return 'ASAP';
  const date = parseUTCDate(dateString);
  if (!date) return 'ASAP';
  const now = new Date();
  const diff = date.getTime() - now.getTime();

  if (diff < 0) return 'Now';
  if (diff < 60000) return 'In <1 min';
  if (diff < 3600000) return `In ${Math.round(diff / 60000)} min`;
  if (diff < 86400000) return `In ${Math.round(diff / 3600000)}h`;
  return date.toLocaleDateString();
}

export function PrinterQueueWidget({ printerId, printerState }: PrinterQueueWidgetProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const { data: queue } = useQuery({
    queryKey: ['queue', printerId, 'pending'],
    queryFn: () => api.getQueue(printerId, 'pending'),
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

  const nextItem = queue?.[0];
  const totalPending = queue?.length || 0;

  if (totalPending === 0) {
    return null;
  }

  const needsClearPlate = printerState === 'FINISH' || printerState === 'FAILED';

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
            disabled={clearPlateMutation.isPending || !hasPermission('printers:control')}
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
            {formatRelativeTime(nextItem?.scheduled_time || null)}
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
