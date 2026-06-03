import { useEffect, useRef, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Layers, Loader2, Pause, Play, Square, Timer } from 'lucide-react';
import { api, withStreamToken } from '../../api/client';
import type { Printer, PrinterStatus } from '../../api/client';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { useStreamTokenSync } from '../../hooks/useCameraStreamToken';
import { formatPrintName } from '../../utils/printName';
import { formatDuration, formatETA } from '../../utils/date';
import { ConfirmModal } from '../ConfirmModal';

interface PrinterCameraPanelProps {
  printer: Printer;
  status: PrinterStatus | undefined;
  canControl: boolean;
}

export function PrinterCameraPanel({ printer, status, canControl }: PrinterCameraPanelProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  useStreamTokenSync();

  const [streamError, setStreamError] = useState(false);
  const [streamLoading, setStreamLoading] = useState(true);
  const [imageKey, setImageKey] = useState(Date.now());
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [showPauseConfirm, setShowPauseConfirm] = useState(false);
  const [showResumeConfirm, setShowResumeConfirm] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

  const streamUrl = withStreamToken(api.getCameraStreamUrl(printer.id, 15));

  useEffect(() => {
    if (streamLoading) {
      const timer = setTimeout(() => setStreamLoading(false), 5000);
      return () => clearTimeout(timer);
    }
  }, [streamLoading, imageKey]);

  const isPrinting = status?.state === 'RUNNING' || status?.state === 'PAUSE';
  const isPaused = status?.state === 'PAUSE';
  const isRunning = status?.state === 'RUNNING';

  const stopMutation = useMutation({
    mutationFn: () => api.stopPrint(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
      setShowStopConfirm(false);
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const pauseMutation = useMutation({
    mutationFn: () => api.pausePrint(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
      setShowPauseConfirm(false);
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });
  const resumeMutation = useMutation({
    mutationFn: () => api.resumePrint(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
      setShowResumeConfirm(false);
    },
    onError: (e: Error) => showToast(e.message, 'error'),
  });

  const printName =
    isPrinting && status
      ? formatPrintName(
          status.subtask_name || status.current_print,
          status.gcode_file,
          t
        )
      : null;

  const progress = status?.progress ?? 0;

  return (
    <div className="flex flex-col h-full min-h-[280px] rounded-xl border border-bambu-dark-tertiary bg-bambu-dark-secondary overflow-hidden">
      <div className="relative flex-1 bg-black min-h-[200px]">
        {!hasPermission('camera:view') ? (
          <div className="absolute inset-0 flex items-center justify-center text-bambu-gray text-sm">
            {t('printers.permission.noCamera')}
          </div>
        ) : streamError ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-bambu-gray text-sm gap-2">
            <span>{t('printerDetail.streamError')}</span>
            <button
              type="button"
              className="text-bambu-green text-xs"
              onClick={() => {
                setStreamError(false);
                setStreamLoading(true);
                setImageKey(Date.now());
              }}
            >
              {t('common.retry')}
            </button>
          </div>
        ) : (
          <>
            {streamLoading && (
              <div className="absolute inset-0 flex items-center justify-center z-10">
                <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
              </div>
            )}
            <img
              ref={imgRef}
              key={imageKey}
              src={streamUrl}
              alt={printer.name}
              className="w-full h-full object-contain"
              onLoad={() => setStreamLoading(false)}
              onError={() => {
                setStreamError(true);
                setStreamLoading(false);
              }}
            />
          </>
        )}
      </div>

      <div className="p-3 border-t border-bambu-dark-tertiary space-y-2">
        {printName && (
          <p className="text-sm text-white truncate" title={printName}>
            {printName}
          </p>
        )}
        {isPrinting && status && (
          <>
            <div className="h-2 rounded-full bg-bambu-dark overflow-hidden">
              <div
                className="h-full bg-bambu-green transition-all duration-500"
                style={{ width: `${Math.min(100, progress)}%` }}
              />
            </div>
            <div className="flex flex-wrap gap-3 text-xs text-bambu-gray">
              <span>{Math.round(progress)}%</span>
              {status.layer_num != null && status.total_layers != null && (
                <span className="flex items-center gap-1">
                  <Layers className="w-3 h-3" />
                  {status.layer_num}/{status.total_layers}
                </span>
              )}
              {status.remaining_time != null && status.remaining_time > 0 && (
                <span className="flex items-center gap-1">
                  <Timer className="w-3 h-3" />
                  {formatDuration(status.remaining_time * 60)} · {formatETA(status.remaining_time, 'system', t)}
                </span>
              )}
            </div>
          </>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            disabled={!isPrinting || !canControl}
            onClick={() => setShowStopConfirm(true)}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs bg-red-500/20 text-red-400 disabled:opacity-40"
          >
            <Square className="w-3 h-3" />
            {t('printers.stop')}
          </button>
          <button
            type="button"
            disabled={!isPrinting || !canControl}
            onClick={() => (isPaused ? setShowResumeConfirm(true) : setShowPauseConfirm(true))}
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs bg-amber-500/20 text-amber-400 disabled:opacity-40"
          >
            {isPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
            {isPaused ? t('printers.resume') : t('printers.pause')}
          </button>
        </div>
        {status?.stg_cur_name && isRunning && (
          <p className="text-xs text-bambu-green">{status.stg_cur_name}</p>
        )}
      </div>

      {showStopConfirm && (
        <ConfirmModal
          title={t('printers.confirm.stopTitle')}
          message={t('printers.confirm.stopMessage', { name: printer.name })}
          confirmText={t('printers.confirm.stopButton')}
          variant="danger"
          onConfirm={() => stopMutation.mutate()}
          onCancel={() => setShowStopConfirm(false)}
        />
      )}
      {showPauseConfirm && (
        <ConfirmModal
          title={t('printers.confirm.pauseTitle')}
          message={t('printers.confirm.pauseMessage', { name: printer.name })}
          onConfirm={() => pauseMutation.mutate()}
          onCancel={() => setShowPauseConfirm(false)}
        />
      )}
      {showResumeConfirm && (
        <ConfirmModal
          title={t('printers.confirm.resumeTitle')}
          message={t('printers.confirm.resumeMessage', { name: printer.name })}
          onConfirm={() => resumeMutation.mutate()}
          onCancel={() => setShowResumeConfirm(false)}
        />
      )}
    </div>
  );
}
