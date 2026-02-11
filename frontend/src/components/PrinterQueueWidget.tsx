import { useQuery } from '@tanstack/react-query';
import { Clock, Calendar, ChevronRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { parseUTCDate } from '../utils/date';

interface PrinterQueueWidgetProps {
  printerId: number;
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

export function PrinterQueueWidget({ printerId }: PrinterQueueWidgetProps) {
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
            <p className="text-xs text-bambu-gray">Next in queue</p>
            <p className="text-sm text-white truncate">
              {nextItem?.archive_name || nextItem?.library_file_name || `Archive #${nextItem?.archive_id}`}
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
