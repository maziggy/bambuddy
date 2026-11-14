import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Download, History, Loader2, Trash2, X } from 'lucide-react';
import { api } from '../api/client';
import type { MaintenanceLogEntry } from '../api/client';
import { formatDateTime, type TimeFormat } from '../utils/date';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { MaintenanceLogCleanupModal } from './MaintenanceLogCleanupModal';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { useUiSettings } from '../hooks/useUiSettings';

const PAGE_SIZE = 50;

interface MaintenanceLogModalProps {
  printerId: number;
  printerName: string;
  onClose: () => void;
}

/** A printer's maintenance log, newest first, with its CSV export and cleanup. */
export function MaintenanceLogModal({ printerId, printerName, onClose }: MaintenanceLogModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const { data: settings } = useUiSettings();
  const timeFormat: TimeFormat = settings?.time_format || 'system';
  const canDelete = hasPermission('maintenance:delete');
  const [exporting, setExporting] = useState(false);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<MaintenanceLogEntry | null>(null);
  // Escape closes the confirmation on top first, not the log under it too.
  const confirming = cleanupOpen || pendingDelete !== null;

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !confirming) onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, confirming]);

  // One page per request, each fetched once: "Load more" asks for the next
  // offset instead of a bigger first page (the API caps a page at 500).
  const { data, isLoading, isError, error, refetch, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
    queryKey: ['maintenanceLog', printerId],
    queryFn: ({ pageParam }) => api.getMaintenanceLog(printerId, { limit: PAGE_SIZE, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0);
      return lastPage.items.length > 0 && loaded < lastPage.total ? loaded : undefined;
    },
  });

  const entries = data?.pages.flatMap((page) => page.items) ?? [];
  const total = data?.pages.at(-1)?.total ?? 0;
  // Only an empty log has nothing to export or clean up; when the list failed
  // to load, both may still work.
  const logEmpty = !isError && total === 0;

  const onDeleteError = (err: Error) => showToast(t('maintenance.deleteFailed', { error: err.message }), 'error');

  const cleanupMutation = useMutation({
    mutationFn: (olderThanDays: number) => api.cleanUpMaintenanceLog(olderThanDays, printerId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceLog'] });
      setCleanupOpen(false);
      showToast(t('maintenance.cleanUpDone', { deleted: result.deleted }));
    },
    onError: onDeleteError,
  });

  const deleteEntryMutation = useMutation({
    mutationFn: (entryId: number) => api.deleteMaintenanceLogEntry(entryId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['maintenanceLog'] });
      setPendingDelete(null);
      showToast(t('maintenance.entryDeleted'));
    },
    onError: onDeleteError,
  });

  const handleExport = async () => {
    setExporting(true);
    try {
      await api.downloadMaintenanceLog(printerId);
    } catch (err) {
      showToast(t('maintenance.exportFailed', { error: (err as Error).message }), 'error');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={confirming ? undefined : onClose}>
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
          <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            {canDelete && (
              <Button size="sm" variant="secondary" onClick={() => setCleanupOpen(true)} disabled={logEmpty}>
                <Trash2 className="w-3.5 h-3.5" />
                {t('maintenance.cleanUp')}
              </Button>
            )}
            <Button size="sm" variant="secondary" onClick={handleExport} disabled={exporting || logEmpty}>
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
          ) : isError && entries.length === 0 ? (
            <div role="alert" className="flex flex-col items-center gap-3 py-12 text-center">
              <p className="flex items-center gap-2 text-red-700 dark:text-red-400">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                {t('maintenance.logLoadFailed', { error: (error as Error).message })}
              </p>
              <Button size="sm" variant="secondary" onClick={() => refetch()}>
                {t('common.retry')}
              </Button>
            </div>
          ) : entries.length === 0 ? (
            <p className="text-center text-bambu-gray py-12">{t('maintenance.logEmpty')}</p>
          ) : (
            <ul className="space-y-2">
              {entries.map((entry) => (
                <LogEntryRow
                  key={entry.id}
                  entry={entry}
                  timeFormat={timeFormat}
                  onDelete={canDelete ? () => setPendingDelete(entry) : undefined}
                />
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
            {isError && entries.length > 0 ? (
              <span role="alert" className="flex items-center gap-2 text-red-700 dark:text-red-400">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                {t('maintenance.logLoadFailed', { error: (error as Error).message })}
                <Button size="sm" variant="secondary" onClick={() => fetchNextPage()}>
                  {t('common.retry')}
                </Button>
              </span>
            ) : (
              hasNextPage && (
                <Button size="sm" variant="secondary" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
                  {isFetchingNextPage && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  {t('maintenance.loadMore')}
                </Button>
              )
            )}
          </div>
        )}

        {cleanupOpen && (
          <MaintenanceLogCleanupModal
            printerName={printerName}
            isLoading={cleanupMutation.isPending}
            onConfirm={(days) => cleanupMutation.mutate(days)}
            onCancel={() => setCleanupOpen(false)}
          />
        )}

        {pendingDelete && (
          <ConfirmModal
            title={t('maintenance.deleteEntryTitle')}
            message={t('maintenance.deleteEntryMessage', {
              task: pendingDelete.maintenance_type_name,
              date: formatDateTime(pendingDelete.performed_at, timeFormat),
            })}
            confirmText={t('common.delete')}
            variant="danger"
            overlayZIndex="z-[60]"
            isLoading={deleteEntryMutation.isPending}
            onConfirm={() => deleteEntryMutation.mutate(pendingDelete.id)}
            onCancel={() => setPendingDelete(null)}
          />
        )}
      </div>
    </div>
  );
}

interface LogEntryRowProps {
  entry: MaintenanceLogEntry;
  timeFormat: TimeFormat;
  /** Shown only to users who may delete entries. */
  onDelete?: () => void;
}

function LogEntryRow({ entry, timeFormat, onDelete }: LogEntryRowProps) {
  const { t } = useTranslation();
  return (
    <li className="rounded-lg border border-bambu-dark-tertiary bg-bambu-dark/30 px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="font-medium text-white">{entry.maintenance_type_name}</span>
        <span className="flex items-center gap-2">
          <time dateTime={entry.performed_at} className="text-xs text-bambu-gray">
            {formatDateTime(entry.performed_at, timeFormat)}
          </time>
          {onDelete && (
            <button
              onClick={onDelete}
              className="p-1 -my-1 rounded text-bambu-gray hover:text-red-600 dark:hover:text-red-400 transition-colors self-center"
              aria-label={t('maintenance.deleteEntry')}
              title={t('maintenance.deleteEntry')}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </span>
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-bambu-gray">
        {entry.performed_by_username && <span>{t('maintenance.performedBy', { user: entry.performed_by_username })}</span>}
        <span>{t('maintenance.atPrintHours', { hours: entry.hours_at_maintenance.toFixed(1) })}</span>
      </div>
      {entry.notes && <p className="mt-1.5 text-sm text-white/90 whitespace-pre-wrap break-words">{entry.notes}</p>}
    </li>
  );
}
