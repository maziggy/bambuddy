import { useQuery } from '@tanstack/react-query';
import { Clock, Calendar, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { formatRelativeTime } from '../utils/date';
import { filterCompatibleQueueItems } from '../utils/printer';

interface PrinterQueueWidgetProps {
  printerId: number;
  printerModel?: string | null;
  /** @deprecated use awaitingPlateClear — kept so existing callers/tests still compile */
  printerState?: string | null;
  awaitingPlateClear?: boolean;
  requirePlateClear?: boolean;
  loadedFilamentTypes?: Set<string>;
  loadedFilaments?: Set<string>;  // "TYPE:rrggbb" pairs for filament override color matching
}

export function PrinterQueueWidget({ printerId, printerModel, loadedFilamentTypes, loadedFilaments }: PrinterQueueWidgetProps) {
  const { t } = useTranslation();
  const { data: queue } = useQuery({
    queryKey: ['queue', printerId, 'pending', printerModel],
    queryFn: () => api.getQueue(printerId, 'pending', printerModel || undefined),
    refetchInterval: 30000,
  });

  // Filter queue to items this printer can actually print (filament type + color check)
  const compatibleQueue = queue ? filterCompatibleQueueItems(queue, loadedFilamentTypes, loadedFilaments) : undefined;

  const totalPending = compatibleQueue?.length || 0;

  if (totalPending === 0) {
    return null;
  }

  const nextItem = compatibleQueue?.[0];

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
