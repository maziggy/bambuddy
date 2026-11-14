import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { Download, History, Loader2, X } from 'lucide-react';
import { api } from '../api/client';
import type { MaintenanceLogEntry } from '../api/client';
import { formatDateTime, type TimeFormat } from '../utils/date';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { useUiSettings } from '../hooks/useUiSettings';

const PAGE_SIZE = 50;

interface MaintenanceLogModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

/** A printer's maintenance log, newest first, with its CSV export. */
export function MaintenanceLogModal({ printerId, printerName, onClose }: MaintenanceLogModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { data: settings } = useUiSettings();
  const timeFormat: TimeFormat = settings?.time_format || 'system';
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data, isLoading, isFetching } = useQuery({
    queryKey: ['maintenanceLog', printerId, limit],
    queryFn: () => api.getMaintenanceLog(printerId, { limit }),
    placeholderData: keepPreviousData,
  });

  const entries = data?.items ?? [];
  const total = data?.total ?? 0;

  const handleExport = async () => {
    setExporting(true);
    try {
      await api.downloadMaintenanceLog(printerId);
    } catch (error) {
      showToast(t('maintenance.exportFailed', { error: (error as Error).message }), 'error');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="maintenance-log-title"
        className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg w-full max-w-3xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <History className="w-5 h-5 text-bambu-green shrink-0" />
            <h2 id="maintenance-log-title" className="text-lg font-semibold text-white truncate">
              {t('maintenance.logTitle', { printer: printerName })}
            </h2>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button size="sm" variant="secondary" onClick={handleExport} disabled={exporting || total === 0}>
              {exporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              {t('maintenance.exportCsv')}
            </Button>
            <button
              onClick={onClose}
              className="p-1 text-bambu-gray hover:text-white transition-colors"
              aria-label={t('common.close')}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        {/* Entries */}
        <div className="flex-1 overflow-y-auto p-4">
          {isLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : entries.length === 0 ? (
            <p className="text-center text-bambu-gray py-12">{t('maintenance.logEmpty')}</p>
          ) : (
            <ul className="space-y-2">
              {entries.map((entry) => (
                <LogEntryRow key={entry.id} entry={entry} timeFormat={timeFormat} />
              ))}
            </ul>
          )}
        </div>

        {/* Footer */}
        {total > 0 && (
          <div className="px-4 py-3 border-t border-bambu-dark-tertiary flex items-center justify-between gap-3 text-sm">
            <span className="text-bambu-gray">
              {t('maintenance.logShowing', { shown: entries.length, total })}
            </span>
            {entries.length < total && (
              <Button size="sm" variant="secondary" onClick={() => setLimit(limit + PAGE_SIZE)} disabled={isFetching}>
                {isFetching && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                {t('maintenance.loadMore')}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function LogEntryRow({ entry, timeFormat }: { entry: MaintenanceLogEntry; timeFormat: TimeFormat }) {
  const { t } = useTranslation();
  return (
    <li className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark/30 px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="font-medium text-white">{entry.maintenance_type_name}</span>
        <time dateTime={entry.performed_at} className="text-xs text-bambu-gray">
          {formatDateTime(entry.performed_at, timeFormat)}
        </time>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-bambu-gray">
        {entry.performed_by_username && <span>{t('maintenance.performedBy', { user: entry.performed_by_username })}</span>}
        <span>{t('maintenance.atPrintHours', { hours: entry.hours_at_maintenance.toFixed(1) })}</span>
      </div>
      {entry.notes && <p className="mt-1.5 text-sm text-white/90 whitespace-pre-wrap break-words">{entry.notes}</p>}
    </li>
  );
}
