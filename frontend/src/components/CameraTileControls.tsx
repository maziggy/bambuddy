import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, Square } from 'lucide-react';
import { api, type PrinterStatus } from '../api/client';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { ConfirmModal } from './ConfirmModal';
import { ChamberLight } from './icons/ChamberLight';

interface CameraTileControlsProps {
  printerId: number;
  printerName: string;
  connected: boolean;
  printerState: string | null;
  chamberLight: boolean | null;
}

const BUTTON =
  'flex h-7 w-7 items-center justify-center rounded-md bg-black/70 text-white backdrop-blur-sm transition-colors hover:bg-black/90 focus:outline-none focus:ring-2 focus:ring-bambu-green disabled:cursor-not-allowed disabled:opacity-50';

/**
 * Pause/resume, stop and the chamber light on a camera wall tile (#3479).
 *
 * The same actions as the printer card's, behind the same printers:control
 * permission and the same confirmations: a user without the permission gets
 * no controls at all. Rendered beside the tile, not inside it, because the
 * tile itself is a button (it opens the camera).
 */
export function CameraTileControls({
  printerId,
  printerName,
  connected,
  printerState,
  chamberLight,
}: CameraTileControlsProps) {
  const { t } = useTranslation();
  const { hasPermission } = useAuth();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<'pause' | 'resume' | 'stop' | null>(null);

  const refreshStatus = () => queryClient.invalidateQueries({ queryKey: ['printerStatus', printerId] });

  const stop = useMutation({
    mutationFn: () => api.stopPrint(printerId),
    onSuccess: () => {
      showToast(t('printers.toast.printStopped'));
      refreshStatus();
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToStopPrint'), 'error'),
  });
  const pause = useMutation({
    mutationFn: () => api.pausePrint(printerId),
    onSuccess: () => {
      showToast(t('printers.toast.printPaused'));
      refreshStatus();
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToPausePrint'), 'error'),
  });
  const resume = useMutation({
    mutationFn: () => api.resumePrint(printerId),
    onSuccess: () => {
      showToast(t('printers.toast.printResumed'));
      refreshStatus();
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToResumePrint'), 'error'),
  });
  // Optimistic, as on the card: the light's state comes back over the
  // printer's next status push, which can take a few seconds.
  const light = useMutation({
    mutationFn: (on: boolean) => api.setChamberLight(printerId, on),
    onMutate: async (on) => {
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printerId] });
      const previous = queryClient.getQueryData<PrinterStatus>(['printerStatus', printerId]);
      if (previous) {
        queryClient.setQueryData<PrinterStatus>(['printerStatus', printerId], { ...previous, chamber_light: on });
      }
      return { previous };
    },
    onError: (error: Error, _on, context) => {
      if (context?.previous) queryClient.setQueryData(['printerStatus', printerId], context.previous);
      showToast(error.message || t('printers.toast.failedToControlChamberLight'), 'error');
    },
  });

  if (!hasPermission('printers:control') || !connected) return null;

  const isPaused = printerState === 'PAUSE';
  const isPrinting = printerState === 'RUNNING' || isPaused;
  const busy = stop.isPending || pause.isPending || resume.isPending;
  const lightOn = chamberLight ?? false;

  return (
    <>
      <div className="flex gap-1">
        {isPrinting && (
          <>
            <button
              type="button"
              className={BUTTON}
              disabled={busy}
              onClick={() => setConfirm(isPaused ? 'resume' : 'pause')}
              title={isPaused ? t('printers.resume') : t('printers.pause')}
              aria-label={isPaused ? t('printers.resume') : t('printers.pause')}
            >
              {isPaused ? <Play className="h-3.5 w-3.5" /> : <Pause className="h-3.5 w-3.5" />}
            </button>
            <button
              type="button"
              className={`${BUTTON} hover:text-red-400`}
              disabled={busy}
              onClick={() => setConfirm('stop')}
              title={t('printers.stop')}
              aria-label={t('printers.stop')}
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          </>
        )}
        <button
          type="button"
          className={BUTTON}
          disabled={light.isPending}
          aria-pressed={lightOn}
          onClick={() => light.mutate(!lightOn)}
          title={lightOn ? t('printers.chamberLightOff') : t('printers.chamberLightOn')}
          aria-label={lightOn ? t('printers.chamberLightOff') : t('printers.chamberLightOn')}
        >
          <ChamberLight on={lightOn} className="h-4 w-4" />
        </button>
      </div>

      {confirm === 'stop' && (
        <ConfirmModal
          title={t('printers.confirm.stopTitle')}
          message={t('printers.confirm.stopMessage', { name: printerName })}
          confirmText={t('printers.confirm.stopButton')}
          variant="danger"
          onConfirm={() => {
            stop.mutate();
            setConfirm(null);
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'pause' && (
        <ConfirmModal
          title={t('printers.confirm.pauseTitle')}
          message={t('printers.confirm.pauseMessage', { name: printerName })}
          confirmText={t('printers.confirm.pauseButton')}
          onConfirm={() => {
            pause.mutate();
            setConfirm(null);
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
      {confirm === 'resume' && (
        <ConfirmModal
          title={t('printers.confirm.resumeTitle')}
          message={t('printers.confirm.resumeMessage', { name: printerName })}
          confirmText={t('printers.confirm.resumeButton')}
          onConfirm={() => {
            resume.mutate();
            setConfirm(null);
          }}
          onCancel={() => setConfirm(null)}
        />
      )}
    </>
  );
}
