import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2, Trash2, X } from 'lucide-react';

import { api } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { formatFileSize } from '../utils/file';

interface PurgeArchivesModalProps {
  onClose: () => void;
  initialDays?: number;
}

const DEFAULT_DAYS = 365;

export function PurgeArchivesModal({ onClose, initialDays }: PurgeArchivesModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [days, setDays] = useState(initialDays ?? DEFAULT_DAYS);

  const [debouncedDays, setDebouncedDays] = useState(days);
  useEffect(() => {
    const handle = window.setTimeout(() => setDebouncedDays(days), 300);
    return () => window.clearTimeout(handle);
  }, [days]);

  const previewQuery = useQuery({
    queryKey: ['archive-purge-preview', debouncedDays],
    queryFn: () => api.previewArchivePurge(debouncedDays),
    enabled: debouncedDays >= 1,
  });

  const purgeMutation = useMutation({
    mutationFn: () => api.executeArchivePurge(days),
    onSuccess: (res) => {
      showToast(t('archivePurge.toast.success', { count: res.deleted }), 'success');
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['archive-stats'] });
      onClose();
    },
    onError: (e: Error) => showToast(e.message || t('archivePurge.toast.failed'), 'error'),
  });

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !purgeMutation.isPending) onClose();
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, purgeMutation.isPending]);

  const preview = previewQuery.data;
  const count = preview?.count ?? 0;
  const totalBytes = preview?.total_bytes ?? 0;
  const canConfirm = count > 0 && !purgeMutation.isPending;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white dark:bg-gray-900 rounded-lg shadow-xl max-w-lg w-full">
        <div className="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <Trash2 className="w-5 h-5" />
            {t('archivePurge.title')}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            aria-label={t('common.close')}
            disabled={purgeMutation.isPending}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-gray-600 dark:text-gray-400">
            {t('archivePurge.description')}
          </p>

          <div>
            <label htmlFor="archive-purge-days" className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              {t('archivePurge.ageLabel')}
            </label>
            <div className="flex items-center gap-3">
              <input
                id="archive-purge-days"
                type="number"
                min={1}
                max={3650}
                value={days}
                onChange={(e) => setDays(Math.max(1, Math.min(3650, parseInt(e.target.value || '0', 10) || 0)))}
                className="w-24 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-sm px-2 py-1 text-gray-900 dark:text-gray-100"
              />
              <span className="text-sm text-gray-600 dark:text-gray-400">{t('archivePurge.days')}</span>
            </div>
          </div>

          <div className="rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/30 p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-gray-600 dark:text-gray-300 mb-2">
              {t('archivePurge.effectsTitle')}
            </div>
            <ul className="text-xs text-gray-700 dark:text-gray-300 space-y-1 list-disc pl-4">
              <li>{t('archivePurge.effect1')}</li>
              <li>{t('archivePurge.effect2')}</li>
              <li>{t('archivePurge.effect3')}</li>
              <li>{t('archivePurge.effect4')}</li>
            </ul>
          </div>

          <div className="rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 p-3">
            {previewQuery.isLoading || previewQuery.isFetching ? (
              <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                <Loader2 className="w-4 h-4 animate-spin" /> {t('archivePurge.previewLoading')}
              </div>
            ) : previewQuery.isError ? (
              <div className="text-sm text-red-600 dark:text-red-400">
                {(previewQuery.error as Error | null)?.message ?? t('archivePurge.previewFailed')}
              </div>
            ) : (
              <div className="text-sm text-gray-900 dark:text-gray-100">
                <div className="font-medium">
                  {t('archivePurge.previewSummary', { count, size: formatFileSize(totalBytes) })}
                </div>
                {preview?.sample_filenames && preview.sample_filenames.length > 0 && (
                  <ul className="mt-2 text-xs text-gray-600 dark:text-gray-400 space-y-0.5 list-disc pl-4">
                    {preview.sample_filenames.map((name) => (
                      <li key={name} className="truncate">{name}</li>
                    ))}
                    {count > preview.sample_filenames.length && (
                      <li className="list-none italic text-gray-500">
                        {t('archivePurge.andMore', { count: count - preview.sample_filenames.length })}
                      </li>
                    )}
                  </ul>
                )}
              </div>
            )}
          </div>

          <div className="flex gap-2 items-start text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded px-3 py-2">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{t('archivePurge.warning')}</span>
          </div>
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          <Button variant="secondary" onClick={onClose} disabled={purgeMutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            variant="danger"
            disabled={!canConfirm}
            onClick={() => purgeMutation.mutate()}
          >
            {purgeMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin mr-1" />
                {t('archivePurge.purging')}
              </>
            ) : (
              t('archivePurge.confirmCta', { count })
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}
