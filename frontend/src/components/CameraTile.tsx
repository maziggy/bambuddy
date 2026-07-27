import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, VideoOff, WifiOff } from 'lucide-react';
import { getAuthToken, withStreamToken } from '../api/client';
import { formatDuration } from '../utils/date';

export type CameraTileMode = 'live' | 'snapshot' | 'paused';
export type CameraTileStatusMode = 'off' | 'compact' | 'full';

interface CameraTileProps {
  printerId: number;
  printerName: string;
  cameraRotation?: number;
  mode: CameraTileMode;
  snapshotIntervalMs: number;
  connected: boolean;
  onClick?: () => void;
  // Optional status overlay — wired by CameraWall from the shared
  // ['printerStatus', id] query. All optional so existing tests don't break.
  statusMode?: CameraTileStatusMode;
  printerState?: string | null;
  progress?: number | null;
  remainingMin?: number | null;
  layerNum?: number | null;
  totalLayers?: number | null;
  printName?: string | null;
  hmsErrorCount?: number;
}

// Tiles render lighter than EmbeddedCameraViewer's full window: lower fps,
// no drag/resize/zoom shell, and snapshot fallback when off-cap. The server
// still does the MJPEG fan-out, so per-tile cost is one TLS pull on the wire.
const LIVE_FPS = 8;

type StatusBucket = 'printing' | 'paused' | 'finished' | 'error' | 'idle';

function classifyState(state: string | null | undefined, hmsErrorCount: number): StatusBucket {
  if (hmsErrorCount > 0) return 'error';
  switch (state) {
    case 'RUNNING':
      return 'printing';
    case 'PAUSE':
      return 'paused';
    case 'FINISH':
    case 'FAILED':
      return 'finished';
    default:
      return 'idle';
  }
}

const BUCKET_CHIP_CLASS: Record<StatusBucket, string> = {
  printing: 'bg-bambu-green/85 text-black',
  paused: 'bg-amber-500/85 text-black',
  finished: 'bg-sky-500/80 text-white',
  error: 'bg-red-500/85 text-white',
  idle: 'bg-bambu-dark-tertiary/80 text-bambu-gray',
};

export function CameraTile({
  printerId,
  printerName,
  cameraRotation = 0,
  mode,
  snapshotIntervalMs,
  connected,
  onClick,
  statusMode = 'off',
  printerState = null,
  progress = null,
  remainingMin = null,
  layerNum = null,
  totalLayers = null,
  printName = null,
  hmsErrorCount = 0,
}: CameraTileProps) {
  const { t } = useTranslation();
  const [bust, setBust] = useState(0);
  const [errored, setErrored] = useState(false);
  const lastModeRef = useRef<CameraTileMode>(mode);

  // Tell the backend to release its MJPEG transcoder when this tile stops
  // being live — either by unmounting or by transitioning to snapshot/paused.
  // EmbeddedCameraViewer uses the same /camera/stop with keepalive on unmount.
  useEffect(() => {
    const wasLive = lastModeRef.current === 'live';
    const isLive = mode === 'live';
    lastModeRef.current = mode;
    if (wasLive && !isLive) {
      const headers: Record<string, string> = {};
      const token = getAuthToken();
      if (token) headers['Authorization'] = `Bearer ${token}`;
      fetch(`/api/v1/printers/${printerId}/camera/stop`, {
        method: 'POST',
        keepalive: true,
        headers,
      }).catch(() => {});
    }
    setErrored(false);
    setBust((b) => b + 1);
  }, [mode, printerId]);

  useEffect(() => {
    return () => {
      if (lastModeRef.current === 'live') {
        const headers: Record<string, string> = {};
        const token = getAuthToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        fetch(`/api/v1/printers/${printerId}/camera/stop`, {
          method: 'POST',
          keepalive: true,
          headers,
        }).catch(() => {});
      }
    };
  }, [printerId]);

  useEffect(() => {
    if (mode !== 'snapshot') return;
    const interval = setInterval(() => setBust((b) => b + 1), snapshotIntervalMs);
    return () => clearInterval(interval);
  }, [mode, snapshotIntervalMs]);

  const liveUrl = withStreamToken(
    `/api/v1/printers/${printerId}/camera/stream?fps=${LIVE_FPS}&t=${bust}`,
  );
  const snapshotUrl = withStreamToken(
    `/api/v1/printers/${printerId}/camera/snapshot?t=${bust}`,
  );

  // A kiosk wall passes no onClick — there is no pointer at a TV, and the page
  // is authenticated by a token that cannot open the single-camera view. Render
  // the tile as plain, non-focusable content rather than a button that looks
  // clickable and then does nothing.
  const interactive = onClick != null;

  const transform = cameraRotation ? `rotate(${cameraRotation}deg)` : undefined;

  const bucket = classifyState(printerState, hmsErrorCount);
  // Hide chip for idle to keep cold walls clean; always show when something
  // is happening (printing/paused/finished/error).
  const showChip = connected && statusMode !== 'off' && bucket !== 'idle';
  const isPrintingOrPaused = bucket === 'printing' || bucket === 'paused';
  const showInfoStrip = connected && statusMode === 'full' && isPrintingOrPaused;
  const fileLabel = printName ?? null;
  const progressPct = progress != null ? Math.round(progress) : null;
  const hasLayers = layerNum != null && totalLayers != null && totalLayers > 0;
  const hasRemaining = remainingMin != null && remainingMin > 0;

  const rootClass = `group relative aspect-video w-full overflow-hidden rounded-lg border border-bambu-dark-tertiary bg-black text-left ${
    interactive ? 'focus:outline-none focus:ring-2 focus:ring-bambu-green' : 'cursor-default'
  }`;

  const content = (
    <>
      {!connected || mode === 'paused' ? (
        <div className="absolute inset-0 flex items-center justify-center bg-bambu-dark/60">
          {connected ? (
            <VideoOff className="h-8 w-8 text-bambu-gray/70" aria-hidden="true" />
          ) : (
            <WifiOff className="h-8 w-8 text-bambu-gray/70" aria-hidden="true" />
          )}
        </div>
      ) : errored ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 bg-black/80 text-bambu-gray">
          <VideoOff className="h-7 w-7" aria-hidden="true" />
          <span className="text-xs">{t('printers.camWall.noSignal')}</span>
        </div>
      ) : (
        <img
          key={`${mode}-${bust}`}
          src={mode === 'live' ? liveUrl : snapshotUrl}
          alt={printerName}
          draggable={false}
          loading="lazy"
          className="h-full w-full select-none object-contain"
          style={{ transform }}
          onError={() => setErrored(true)}
        />
      )}

      {/* Status chip (top-left) */}
      {showChip && (
        <span
          className={`absolute left-2 top-2 flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${BUCKET_CHIP_CLASS[bucket]}`}
        >
          {hmsErrorCount > 0 && (
            <AlertTriangle
              className="h-3 w-3"
              aria-hidden="true"
            />
          )}
          <span>{t(`printers.status.${bucket}`)}</span>
        </span>
      )}

      {/* Mode indicator (top-right) */}
      <span
        className={`absolute right-2 top-2 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
          mode === 'live'
            ? 'bg-red-500/80 text-white'
            : mode === 'snapshot'
              ? 'bg-amber-500/70 text-black'
              : 'bg-bambu-dark-tertiary/70 text-bambu-gray'
        }`}
      >
        {mode === 'live'
          ? t('printers.camWall.live')
          : mode === 'snapshot'
            ? t('printers.camWall.snap')
            : t('printers.camWall.off')}
      </span>

      {/* Bottom overlay: name + (when full) print info */}
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/55 to-transparent px-2 pb-1.5 pt-3 text-white">
        {showInfoStrip && (
          <div className="mb-0.5 space-y-0.5 text-[11px] leading-tight text-white/90">
            {fileLabel && (
              <div className="truncate" title={fileLabel}>
                {fileLabel}
              </div>
            )}
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-bambu-gray">
              {progressPct != null && (
                <span className="font-semibold text-white">{progressPct}%</span>
              )}
              {hasLayers && (
                <span>
                  {t('printers.camWall.layer', {
                    cur: layerNum,
                    total: totalLayers,
                  })}
                </span>
              )}
              {hasRemaining && (
                <span>
                  {t('printers.camWall.timeLeft', {
                    time: formatDuration((remainingMin ?? 0) * 60),
                  })}
                </span>
              )}
            </div>
          </div>
        )}
        <span className="block truncate text-xs font-medium">{printerName}</span>
      </div>
    </>
  );

  if (!interactive) {
    return (
      <div className={rootClass} title={printerName}>
        {content}
      </div>
    );
  }

  return (
    <button type="button" onClick={onClick} className={rootClass} title={printerName}>
      {content}
    </button>
  );
}
