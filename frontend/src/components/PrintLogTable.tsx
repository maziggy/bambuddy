import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { parseUTCDate } from '../utils/date';

interface PrintLogTableProps {
  archiveId: number;
}

function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

function formatDate(isoString: string | null): string {
  if (!isoString) return '—';
  const d = parseUTCDate(isoString);
  return d ? d.toLocaleString() : '—';
}

export function PrintLogTable({ archiveId }: PrintLogTableProps) {
  const { t } = useTranslation();
  const { data, isLoading } = useQuery({
    queryKey: ['archive-runs', archiveId],
    queryFn: () => api.getArchiveRuns(archiveId),
  });

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Loader2 className="w-5 h-5 text-bambu-gray animate-spin" />
      </div>
    );
  }

  const runs = data?.items || [];
  if (runs.length === 0) {
    return (
      <p className="text-sm text-bambu-gray italic py-2">
        {t('archives.runLog.empty')}
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-bambu-gray border-b border-bambu-dark-tertiary">
            <th className="text-left py-1.5 pr-2 font-medium">{t('archives.runLog.col.date')}</th>
            <th className="text-left py-1.5 pr-2 font-medium">{t('archives.runLog.col.status')}</th>
            <th className="text-right py-1.5 pr-2 font-medium">{t('archives.runLog.col.duration')}</th>
            <th className="text-right py-1.5 pr-2 font-medium">{t('archives.runLog.col.filament')}</th>
            <th className="text-right py-1.5 font-medium">{t('archives.runLog.col.cost')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const statusClass =
              run.status === 'completed'
                ? 'text-bambu-green'
                : run.status === 'failed'
                  ? 'text-red-400'
                  : 'text-bambu-gray';
            return (
              <tr
                key={run.id}
                className="border-b border-bambu-dark-tertiary/40 last:border-0"
              >
                <td className="py-1.5 pr-2 text-bambu-gray-light">
                  {formatDate(run.started_at || run.created_at)}
                </td>
                <td className={`py-1.5 pr-2 font-medium ${statusClass}`}>
                  {t(`archives.runLog.status.${run.status}`, { defaultValue: run.status })}
                  {run.failure_reason && (
                    <span className="block text-[10px] text-bambu-gray font-normal">
                      {t(`editArchive.failureReasons.${run.failure_reason}`, { defaultValue: run.failure_reason })}
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-2 text-right text-bambu-gray-light">
                  {formatDuration(run.duration_seconds)}
                </td>
                <td className="py-1.5 pr-2 text-right text-bambu-gray-light">
                  {run.filament_used_grams != null
                    ? `${run.filament_used_grams.toFixed(1)} g`
                    : '—'}
                </td>
                <td className="py-1.5 text-right text-bambu-gray-light">
                  {run.cost != null ? run.cost.toFixed(2) : '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
