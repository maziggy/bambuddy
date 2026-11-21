import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../api/client';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { QUEUE_TODO_KEY } from './useQueueTodo';

type Deficit = {
  slot_id: number;
  required_grams: number;
  remaining_grams: number | null;
  filament_type?: string | null;
};

// Start a staged queue item with the "Print anyway" confirmations for a
// filament deficit (#1496) or a filament the printer has no tray for (#2799).
// Shared by the queue page and the "To do" list (#3615). Render `modals`
// somewhere in the tree; `overlayZIndex` lifts them above a surrounding panel.
export function useStartQueueItem(overlayZIndex?: string) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [short, setShort] = useState<{ itemId: number; deficit: Deficit[] } | null>(null);
  const [missing, setMissing] = useState<{ itemId: number; missing: string[] } | null>(null);

  const startMutation = useMutation({
    mutationFn: ({ id, skipFilamentCheck }: { id: number; skipFilamentCheck?: boolean }) =>
      api.startQueueItem(id, { skipFilamentCheck }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      queryClient.invalidateQueries({ queryKey: QUEUE_TODO_KEY });
      showToast(t('queue.toast.released'));
      setShort(null);
      setMissing(null);
    },
    onError: (error: unknown, variables) => {
      if (error instanceof ApiError && error.status === 409 && error.code === 'insufficient_filament') {
        setShort({ itemId: variables.id, deficit: (error.detail?.deficit ?? []) as Deficit[] });
        return;
      }
      if (error instanceof ApiError && error.status === 409 && error.code === 'unmatched_filament') {
        setMissing({ itemId: variables.id, missing: (error.detail?.missing ?? []) as string[] });
        return;
      }
      // The translated failure, plus the backend's own reason when it sent
      // a plain-string detail with a 4xx (not a structured or server error).
      const base = t('queue.toast.startFailed');
      const reason =
        error instanceof ApiError && error.status >= 400 && error.status < 500 && !error.code && !error.detail
          ? error.message
          : '';
      showToast(reason ? `${base} (${reason})` : base, 'error');
    },
  });

  const modals = (
    <>
      {short && (
        <ConfirmModal
          title={t('queue.filamentShort.confirmTitle')}
          message={
            t('queue.filamentShort.confirmIntro') + '\n\n' +
            short.deficit
              .map((d) =>
                t('queue.filamentShort.lineItem', {
                  slot: d.slot_id,
                  required: Math.round(d.required_grams),
                  remaining:
                    d.remaining_grams == null
                      ? t('queue.filamentShort.unknown')
                      : Math.round(d.remaining_grams),
                }),
              )
              .join('\n')
          }
          confirmText={t('queue.filamentShort.printAnyway')}
          variant="warning"
          overlayZIndex={overlayZIndex}
          onConfirm={() => startMutation.mutate({ id: short.itemId, skipFilamentCheck: true })}
          onCancel={() => setShort(null)}
        />
      )}
      {missing && (
        <ConfirmModal
          title={t('queue.filamentMissing.confirmTitle')}
          message={t('queue.filamentMissing.confirmIntro') + '\n\n' + missing.missing.join('\n')}
          confirmText={t('queue.filamentShort.printAnyway')}
          variant="warning"
          overlayZIndex={overlayZIndex}
          onConfirm={() => startMutation.mutate({ id: missing.itemId, skipFilamentCheck: true })}
          onCancel={() => setMissing(null)}
        />
      )}
    </>
  );

  return { startMutation, modals };
}
