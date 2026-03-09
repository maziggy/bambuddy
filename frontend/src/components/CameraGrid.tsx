import { useState, useEffect, useMemo, useRef, useCallback, useSyncExternalStore, memo } from 'react';
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Clock,
  Loader2,
  Square,
  Pause,
  Play,
  Signal,
  WifiOff,
  AlertTriangle,
  AlertCircle,
  Layers,
  Grid,
  Grid2x2,
  LayoutGrid,
} from 'lucide-react';

import { api } from '../api/client';
import { formatDuration, formatETA } from '../utils/date';
import type { HMSError, CameraQuality } from '../api/client';
import { Card } from './Card';
import { ConfirmModal } from './ConfirmModal';
import { getTopHMSError } from './HMSErrorModal';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useGridStream } from '../hooks/useGridStream';
import type { GridStreamStats } from '../hooks/useGridStream';

// Grid layout types and constants
export type GridLayout = 'compact' | 'default' | 'large';

export const GRID_LAYOUT_COLS: Record<GridLayout, string> = {
  compact: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6',
  default: 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5',
  large:   'grid-cols-1 sm:grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4',
};

export const GRID_LAYOUT_ICONS: Record<GridLayout, React.ComponentType<{ className?: string }>> = {
  compact: Grid,
  default: Grid2x2,
  large: LayoutGrid,
};

/**
 * CameraGridCard — pure display component.
 * Receives a canvas ref from the parent CameraGrid.
 * Registers an IntersectionObserver to report visibility to the worker
 * so off-screen cards skip JPEG decoding entirely.
 */
const CameraGridCard = memo(function CameraGridCard({
  printerId,
  printerName,
  connected,
  state,
  progress,
  remainingTime,
  layerNum,
  totalLayers,
  canvasRef,
  loading,
  error,
  reconnecting,
  reconnectCountdown,
  reconnectAttempt,
  onPause,
  onStop,
  onResume,
  onVisibilityChange,
  onClearPlate,
  plateCleared,
  clearPlateLoading,
  layout,
  timeFormat,
  controlLoading,
  degraded,
  stale,
  hmsErrors,
  hasQueuedJobs,
  dismissedErrorDesc,
  onDismissError,
}: {
  printerId: number;
  printerName: string;
  connected: boolean;
  state: string | null;
  progress: number;
  remainingTime: number | null;
  layerNum: number | null;
  totalLayers: number | null;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  loading: boolean;
  error: boolean;
  reconnecting: boolean;
  reconnectCountdown: number;
  reconnectAttempt: number;
  onPause?: (id: number, name: string) => void;
  onStop?: (id: number, name: string) => void;
  onResume?: (id: number, name: string) => void;
  onVisibilityChange?: (printerId: number, visible: boolean) => void;
  onClearPlate?: (id: number) => void;
  plateCleared: boolean;
  clearPlateLoading?: boolean;
  layout: 'compact' | 'default' | 'large';
  timeFormat?: 'system' | '12h' | '24h';
  controlLoading?: 'pause' | 'stop' | 'resume' | null;
  degraded?: boolean;
  stale?: boolean;
  hmsErrors?: HMSError[];
  hasQueuedJobs?: boolean;
  dismissedErrorDesc?: string;
  onDismissError?: (id: number, description: string) => void;
}) {
  const { t } = useTranslation();
  const cardRef = useRef<HTMLDivElement>(null);
  // IntersectionObserver for visibility tracking
  useEffect(() => {
    const el = cardRef.current;
    if (!el || !onVisibilityChange) return;
    const observer = new IntersectionObserver(
      ([entry]) => onVisibilityChange(printerId, entry.isIntersecting),
      { threshold: 0 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [printerId, onVisibilityChange]);

  const stateKey = !connected ? 'offline' : state === 'RUNNING' ? 'printing' : state === 'PAUSE' ? 'paused' : state === 'FINISH' ? 'finished' : state === 'FAILED' ? 'failed' : 'idle';
  const stateColor = !connected ? 'text-bambu-gray/60' : state === 'RUNNING' ? 'text-bambu-green' : state === 'PAUSE' ? 'text-yellow-400' : state === 'FAILED' ? 'text-red-400' : 'text-bambu-green/60';
  const isRunning = state === 'RUNNING';
  const isPaused = state === 'PAUSE';
  const textSm = layout === 'compact' ? 'text-[10px]' : 'text-sm';
  const textXs = layout === 'compact' ? 'text-[9px]' : 'text-[11px]';
  const iconSm = layout === 'compact' ? 'w-2.5 h-2.5' : 'w-3 h-3';
  const iconCtrl = layout === 'compact' ? 'w-3 h-3' : 'w-3.5 h-3.5';
  const barH = layout === 'compact' ? 'h-1' : 'h-1.5';
  const rawTopError = hmsErrors?.length ? getTopHMSError(hmsErrors) : null;
  const topError = rawTopError && dismissedErrorDesc === rawTopError.description ? null : rawTopError;

  return (
    <Card className={`relative group ${state === 'FINISH' ? '!border-bambu-green/50' : ''}`} ref={cardRef}>
      <div className="relative w-full aspect-video bg-black overflow-hidden rounded-xl">
        {connected ? (
          <>
            <canvas
              ref={canvasRef}
              className={`w-full h-full object-cover ${loading || error || reconnecting ? 'hidden' : ''}`}
              style={{
                filter: stale && !loading && !error && !reconnecting ? 'blur(3px)' : 'none',
                transition: 'filter 0.6s ease-in-out',
              }}
            />
            {loading && !reconnecting && (
              <div className="absolute inset-0 flex items-center justify-center">
                <Loader2 className="w-8 h-8 text-white/60 animate-spin" />
              </div>
            )}
            {reconnecting && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/70 z-10">
                <div className="text-center">
                  <WifiOff className="w-6 h-6 text-white/50 mx-auto mb-1.5" />
                  <p className="text-xs text-white/70 mb-0.5">{t('printers.cameraGrid.connectionLost')}</p>
                  <p className="text-[10px] text-white/40">
                    {t('printers.cameraGrid.reconnecting', { countdown: reconnectCountdown, attempt: reconnectAttempt })}
                  </p>
                </div>
              </div>
            )}
            {error && !reconnecting && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-1">
                <AlertCircle className="w-8 h-8 text-red-400" />
                <span className="text-xs text-white/50">{t('printers.cameraGrid.cameraUnavailable')}</span>
              </div>
            )}
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1">
            <WifiOff className="w-6 h-6 text-bambu-gray/40" />
            <span className={`${textXs} text-bambu-gray/40`}>{t('printers.status.offline')}</span>
          </div>
        )}
        {/* State overlay — paused only */}
        {isPaused && (
          <div className="absolute inset-0 flex items-center justify-center transition-opacity duration-[2000ms] opacity-100">
            <div className="absolute inset-0 bg-black/50" />
            <span className={`relative ${layout === 'compact' ? 'text-xl' : 'text-3xl'} font-bold text-yellow-400 uppercase tracking-widest drop-shadow-lg`}>{t('printers.status.paused')}</span>
          </div>
        )}
        {/* Printer name (top left) + controls/state (top right) */}
        <div className="absolute top-0 left-0 right-0 bg-gradient-to-b from-black/70 to-transparent px-3 py-1.5 flex items-center justify-between">
          <span className={`${textSm} text-white font-medium drop-shadow-sm flex items-center gap-1`}>
            {printerName}
            {degraded && <span title={t('printers.cameraGrid.connectionLost')}><Signal className={`${iconSm} text-yellow-400 animate-pulse`} /></span>}
          </span>
          <div className="flex items-center gap-1">
            {(isRunning || isPaused) ? (
              <>
                {isRunning && onPause && (
                  <button
                    onClick={() => onPause(printerId, printerName)}
                    disabled={!!controlLoading}
                    className="p-1 rounded bg-white/10 hover:bg-white/40 transition-colors disabled:opacity-40"
                    title={t('printers.pause')}
                  >
                    {controlLoading === 'pause'
                      ? <Loader2 className={`${iconCtrl} text-white animate-spin`} />
                      : <Pause className={`${iconCtrl} text-white/60 hover:text-white transition-colors`} />}
                  </button>
                )}
                {isPaused && onResume && (
                  <button
                    onClick={() => onResume(printerId, printerName)}
                    disabled={!!controlLoading}
                    className="p-1 rounded bg-white/10 hover:bg-white/40 transition-colors disabled:opacity-40"
                    title={t('printers.resume')}
                  >
                    {controlLoading === 'resume'
                      ? <Loader2 className={`${iconCtrl} text-white animate-spin`} />
                      : <Play className={`${iconCtrl} text-white/60 hover:text-white transition-colors`} />}
                  </button>
                )}
                {onStop && (
                  <button
                    onClick={() => onStop(printerId, printerName)}
                    disabled={!!controlLoading}
                    className="p-1 rounded bg-white/10 hover:bg-red-500/60 transition-colors disabled:opacity-40"
                    title={t('printers.stop')}
                  >
                    {controlLoading === 'stop'
                      ? <Loader2 className={`${iconCtrl} text-white animate-spin`} />
                      : <Square className={`${iconCtrl} text-white/60 hover:text-white transition-colors`} />}
                  </button>
                )}
              </>
            ) : state !== 'FINISH' && (
              <span className={`${textXs} font-medium drop-shadow-sm uppercase ${stateColor} ${state === 'FAILED' ? 'animate-pulse' : ''}`}>{t(`printers.status.${stateKey}`)}</span>
            )}
          </div>
        </div>
        {/* HMS Error notification — click to dismiss */}
        {topError && (
          <button
            onClick={() => onDismissError?.(printerId, topError.description)}
            className={`absolute left-2 right-2 ${layout === 'compact' ? 'top-7' : 'top-8'} z-20 flex items-start gap-1.5 px-2 py-1.5 rounded-md shadow-lg border backdrop-blur-sm cursor-pointer hover:opacity-80 transition-opacity text-left ${
              topError.severity <= 2
                ? 'bg-red-900/90 border-red-500/50 text-red-100'
                : 'bg-red-900/80 border-red-500/40 text-red-200'
            }`}
            title={t('printers.clickToDismiss')}
          >
            <AlertTriangle className={`w-4 h-4 shrink-0 mt-0.5 ${topError.severity <= 2 ? 'text-red-300' : 'text-red-400'}`} />
            <span className={`${textXs} leading-tight line-clamp-2 text-red-200`}>{topError.description}</span>
          </button>
        )}
      </div>
      {/* Progress bar + details — bottom (outside overflow-hidden so tooltip can escape) */}
      <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/80 to-transparent px-3 py-2 rounded-b-xl">
        {(state === 'RUNNING' || state === 'PAUSE') && (
          <>
            <div className={`flex items-center justify-between ${textXs} text-white/80 mb-1 tabular-nums`}>
              <div className="flex items-center gap-2">
                {remainingTime != null && (
                  <span className="relative flex items-center gap-0.5 group/eta cursor-default">
                    <Clock className={iconSm} />
                    {remainingTime > 0 ? formatDuration(remainingTime * 60) : '--'}
                    {remainingTime > 0 && (
                      <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-1 rounded-md bg-bambu-dark-tertiary text-white text-[10px] font-medium whitespace-nowrap opacity-0 scale-95 group-hover/eta:opacity-100 group-hover/eta:scale-100 transition-all duration-150 shadow-lg border border-white/10 z-50">
                        ETA {formatETA(remainingTime, timeFormat, t)}
                        <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-bambu-dark-tertiary" />
                      </span>
                    )}
                  </span>
                )}
                {layerNum != null && totalLayers != null && totalLayers > 0 && (
                  <span className="flex items-center gap-0.5">
                    <Layers className={iconSm} />
                    {layerNum}/{totalLayers}
                  </span>
                )}
              </div>
              <span>{Math.round(progress)}%</span>
            </div>
            <div className={`bg-white/20 rounded-full ${barH}`}>
              <div
                className={`${state === 'PAUSE' ? 'bg-yellow-400' : 'bg-bambu-green'} ${barH} rounded-full transition-all`}
                style={{ width: `${progress}%` }}
              />
            </div>
          </>
        )}
        {(state === 'FINISH' || state === 'FAILED') && !plateCleared && hasQueuedJobs && onClearPlate && (
          <button
            onClick={() => onClearPlate(printerId)}
            disabled={clearPlateLoading}
            className="w-full py-1.5 rounded-lg bg-bambu-green text-white text-xs font-semibold hover:bg-bambu-green/80 transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
          >
            {clearPlateLoading ? (
              <Loader2 className={`${iconSm} animate-spin`} />
            ) : null}
            {t('queue.clearPlate')}
          </button>
        )}
      </div>
    </Card>
  );
});

/** StatsDisplay — subscribes to stats changes without re-rendering CameraGrid */
function StatsDisplay({ subscribeStats, getStatsSnapshot }: {
  subscribeStats: (cb: () => void) => () => void;
  getStatsSnapshot: () => GridStreamStats;
}) {
  const stats = useSyncExternalStore(subscribeStats, getStatsSnapshot);
  return (
    <>
      <span className="text-xs text-bambu-gray/60 w-20 text-right ml-auto">{stats.bw || '--'}</span>
      <span className="text-xs text-bambu-gray/60 w-12 text-right">{stats.uptime || '--'}</span>
    </>
  );
}

/**
 * CameraGrid — manages a SINGLE multiplexed HTTP connection for all cameras.
 *
 * Uses `GET /camera/grid-stream?ids=1,2,3&fps=5&quality=15&scale=0.5` which
 * returns binary-framed JPEG data:  [4B printer_id LE][4B length LE][jpeg]
 *
 * Optimisations:
 *  - Web Worker: JPEG decoding (createImageBitmap) runs off the main thread;
 *    decoded ImageBitmaps are transferred back for cheap drawImage on main thread
 *  - IntersectionObserver: off-screen cards skip decoding entirely
 *  - Exponential-backoff reconnect: auto-retries on stream drop (2s -> 30s, no cap)
 */
export function CameraGrid({
  printers,
  layout,
  timeFormat,
}: {
  printers: { id: number; name: string; connected: boolean; state: string | null; progress: number; remainingTime: number | null; layerNum: number | null; totalLayers: number | null; plateCleared: boolean; hmsErrors?: HMSError[] }[];
  layout: GridLayout;
  timeFormat?: 'system' | '12h' | '24h';
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, isAdmin } = useAuth();

  // Dismissed HMS errors per printer
  const [dismissedErrors, setDismissedErrors] = useState<Map<number, string>>(new Map());

  // Print control — confirmation modal
  const [confirmAction, setConfirmAction] = useState<{ type: 'pause' | 'stop' | 'resume'; printerId: number; printerName: string } | null>(null);

  const pauseMutation = useMutation({
    mutationFn: (id: number) => api.pausePrint(id),
    onSuccess: (_, id) => { showToast(t('printers.toast.printPaused')); queryClient.invalidateQueries({ queryKey: ['printerStatus', id] }); },
    onError: (err: Error) => showToast(err.message || t('printers.toast.failedToPausePrint'), 'error'),
  });
  const stopMutation = useMutation({
    mutationFn: (id: number) => api.stopPrint(id),
    onSuccess: (_, id) => { showToast(t('printers.toast.printStopped')); queryClient.invalidateQueries({ queryKey: ['printerStatus', id] }); },
    onError: (err: Error) => showToast(err.message || t('printers.toast.failedToStopPrint'), 'error'),
  });
  const resumeMutation = useMutation({
    mutationFn: (id: number) => api.resumePrint(id),
    onSuccess: (_, id) => { showToast(t('printers.toast.printResumed')); queryClient.invalidateQueries({ queryKey: ['printerStatus', id] }); },
    onError: (err: Error) => showToast(err.message || t('printers.toast.failedToResumePrint'), 'error'),
  });
  const clearPlateMutation = useMutation({
    mutationFn: (id: number) => api.clearPlate(id),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['queue', id] });
      queryClient.invalidateQueries({ queryKey: ['printerStatus', id] });
      showToast(t('queue.clearPlateSuccess'), 'success');
    },
    onError: (err: Error) => showToast(err.message, 'error'),
  });
  const qualityMutation = useMutation({
    mutationFn: (quality: CameraQuality) => api.updateSettings({ camera_quality: quality }),
    onSuccess: (data) => queryClient.setQueryData(['settings'], data),
    onError: (err: Error) => showToast(err.message, 'error'),
  });

  const handleConfirm = () => {
    if (!confirmAction) return;
    const { type, printerId } = confirmAction;
    if (type === 'pause') pauseMutation.mutate(printerId);
    else if (type === 'stop') stopMutation.mutate(printerId);
    else if (type === 'resume') resumeMutation.mutate(printerId);
    setConfirmAction(null);
  };

  // Stable callback props — setState setters are stable, so [] deps is correct
  const canControl = hasPermission('printers:control');
  const canClearPlate = hasPermission('printers:clear_plate');
  const handlePause = useCallback((id: number, name: string) => {
    setConfirmAction({ type: 'pause', printerId: id, printerName: name });
  }, []);
  const handleStop = useCallback((id: number, name: string) => {
    setConfirmAction({ type: 'stop', printerId: id, printerName: name });
  }, []);
  const handleResume = useCallback((id: number, name: string) => {
    setConfirmAction({ type: 'resume', printerId: id, printerName: name });
  }, []);
  const handleClearPlate = useCallback((id: number) => {
    clearPlateMutation.mutate(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const handleDismissError = useCallback((id: number, description: string) => {
    setDismissedErrors(prev => new Map(prev).set(id, description));
  }, []);

  // Fetch pending queue items for all printers to power the "Clear Plate" button
  const printerIds = printers.map(p => p.id);
  const queueQueries = useQueries({
    queries: printerIds.map(id => ({
      queryKey: ['queue', id, 'pending'],
      queryFn: () => api.getQueue(id, 'pending'),
      staleTime: 30_000,
    })),
  });
  const printersWithQueue = useMemo(() => {
    const set = new Set<number>();
    queueQueries.forEach((q, i) => {
      if (q.data?.length) set.add(printerIds[i]);
    });
    return set;
  }, [queueQueries, printerIds]);

  // Grid stream quality — preset values are resolved server-side from settings
  const { data: cameraSettings } = useQuery({ queryKey: ['settings'], queryFn: api.getSettings });
  const { data: ffmpegStatus } = useQuery({ queryKey: ['ffmpeg-status'], queryFn: api.checkFfmpeg });
  const cameraQuality = cameraSettings?.camera_quality ?? 'auto';
  const gridParamsKey = cameraQuality;

  const rawPrinterIdsKey = printers.map(p => p.id).sort((a, b) => a - b).join(',');

  // Debounce printerIdsKey so transient printer list changes don't tear down the stream
  const [printerIdsKey, setPrinterIdsKey] = useState(rawPrinterIdsKey);
  useEffect(() => {
    if (printerIdsKey === '' && rawPrinterIdsKey !== '') {
      setPrinterIdsKey(rawPrinterIdsKey);
      return;
    }
    const timer = setTimeout(() => setPrinterIdsKey(rawPrinterIdsKey), 2000);
    return () => clearTimeout(timer);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rawPrinterIdsKey]);

  // Stream management via extracted hook
  const {
    canvasRefs,
    loadingSet,
    errorSet,
    degradedSet,
    staleSet,
    reconnectingSet,
    reconnectCountdown,
    reconnectAttempt,
    subscribeStats,
    getStatsSnapshot,
    handleVisibilityChange,
  } = useGridStream({ printerIdsKey, gridParamsKey });

  return (
    <div>
      <div className="flex items-center justify-end gap-3 mb-2 tabular-nums">
        {isAdmin && (
          <select
            value={cameraQuality}
            onChange={(e) => qualityMutation.mutate(e.target.value as CameraQuality)}
            className="text-xs bg-transparent border border-bambu-dark-tertiary rounded px-1.5 py-0.5 text-bambu-gray/60 focus:border-bambu-green focus:outline-none cursor-pointer"
          >
            <option value="auto">{ffmpegStatus?.auto_resolved_grid
              ? t('settings.cameraQualityAutoWithResolved', { resolved: t(`settings.cameraQuality${ffmpegStatus.auto_resolved_grid.charAt(0).toUpperCase() + ffmpegStatus.auto_resolved_grid.slice(1)}`) })
              : t('settings.cameraQualityAuto')}</option>
            <option value="low">{t('settings.cameraQualityLow')}</option>
            <option value="medium">{t('settings.cameraQualityMedium')}</option>
            <option value="high">{t('settings.cameraQualityHigh')}</option>
          </select>
        )}
        <StatsDisplay subscribeStats={subscribeStats} getStatsSnapshot={getStatsSnapshot} />
      </div>
      <div className={`grid ${layout === 'compact' ? 'gap-2' : 'gap-4'} ${GRID_LAYOUT_COLS[layout]}`}>
        {printers.map(p => (
          <CameraGridCard
            key={p.id}
            printerId={p.id}
            printerName={p.name}
            connected={p.connected}
            state={p.state}
            progress={p.progress}
            remainingTime={p.remainingTime}
            layerNum={p.layerNum}
            totalLayers={p.totalLayers}
            canvasRef={canvasRefs.current.get(p.id) ?? { current: null }}
            loading={loadingSet.has(p.id)}
            error={errorSet.has(p.id)}
            reconnecting={reconnectingSet.has(p.id)}
            reconnectCountdown={reconnectingSet.has(p.id) ? reconnectCountdown : 0}
            reconnectAttempt={reconnectingSet.has(p.id) ? reconnectAttempt : 0}
            onPause={canControl ? handlePause : undefined}
            onStop={canControl ? handleStop : undefined}
            onResume={canControl ? handleResume : undefined}
            controlLoading={
              (pauseMutation.isPending && pauseMutation.variables === p.id) ? 'pause'
              : (stopMutation.isPending && stopMutation.variables === p.id) ? 'stop'
              : (resumeMutation.isPending && resumeMutation.variables === p.id) ? 'resume'
              : null
            }
            onVisibilityChange={handleVisibilityChange}
            onClearPlate={canClearPlate ? handleClearPlate : undefined}
            plateCleared={p.plateCleared}
            clearPlateLoading={clearPlateMutation.isPending && clearPlateMutation.variables === p.id}
            layout={layout}
            timeFormat={timeFormat}
            degraded={degradedSet.has(p.id)}
            stale={staleSet.has(p.id)}
            hmsErrors={p.hmsErrors}
            dismissedErrorDesc={dismissedErrors.get(p.id)}
            hasQueuedJobs={printersWithQueue.has(p.id)}
            onDismissError={handleDismissError}
          />
        ))}
      </div>

      {/* Print control confirmation modal */}
      {confirmAction && (
        <ConfirmModal
          title={t(`printers.confirm.${confirmAction.type}Title`)}
          message={t(`printers.confirm.${confirmAction.type}Message`, { name: confirmAction.printerName })}
          confirmText={t(`printers.confirm.${confirmAction.type}Button`)}
          variant={confirmAction.type === 'stop' ? 'danger' : 'default'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirmAction(null)}
        />
      )}

    </div>
  );
}
