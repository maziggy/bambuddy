import { useQuery } from '@tanstack/react-query';
import { Clock, Calendar, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import { formatRelativeTime } from '../utils/relativeTime';

interface PrinterQueueWidgetProps {
  printerId: number;
}

export function PrinterQueueWidget({ printerId }: PrinterQueueWidgetProps) {
  const { t } = useTranslation();
  const { data: queue } = useQuery({
    queryKey: ['queue', printerId, 'pending'],
    queryFn: () => api.getQueue(printerId, 'pending'),
    refetchInterval: 30000,
  });

  const nextItem = queue?.[0];
  const totalPending = queue?.length || 0;

  if (totalPending === 0) {
    return null;
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
            <p className="text-xs text-bambu-gray">{t('printerQueue.nextInQueue')}</p>
            <p className="text-sm text-white truncate">
              {nextItem?.archive_name || `Archive #${nextItem?.archive_id}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span className="text-xs text-bambu-gray flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {formatRelativeTime(nextItem?.scheduled_time || null, { t })}
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
