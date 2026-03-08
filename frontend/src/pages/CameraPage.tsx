import { useState, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { RefreshCw, AlertTriangle, Camera, Maximize, Minimize, WifiOff, ZoomIn, ZoomOut } from 'lucide-react';
import { api, getAuthToken } from '../api/client';
import { useCameraStopHint } from '../hooks/useCameraStopHint';
import { useCameraControls } from '../hooks/useCameraControls';
import { ChamberLight } from '../components/icons/ChamberLight';
import { SkipObjectsModal, SkipObjectsIcon } from '../components/SkipObjectsModal';
import { useMjpegStream } from '../hooks/useMjpegStream';
import { useStreamReconnect } from '../hooks/useStreamReconnect';
import { useZoomPan } from '../hooks/useZoomPan';

const MAX_RECONNECT_ATTEMPTS = 5;

export function CameraPage() {
  const { t } = useTranslation();
  const { printerId } = useParams<{ printerId: string }>();
  const id = parseInt(printerId || '0', 10);

  const {
    status,
    chamberLightMutation,
    isPrintingWithObjects,
    showSkipObjectsModal,
    setShowSkipObjectsModal,
    checkStalled,
    hasControlPermission,
  } = useCameraControls({ printerId: id });

  const [streamMode, setStreamMode] = useState<'stream' | 'snapshot'>('stream');
  const [transitioning, setTransitioning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Snapshot mode state
  const [snapshotLoading, setSnapshotLoading] = useState(true);
  const [snapshotError, setSnapshotError] = useState(false);
  const [snapshotKey, setSnapshotKey] = useState(Date.now());
  const [snapshotBlobUrl, setSnapshotBlobUrl] = useState<string | null>(null);
  const snapshotImgRef = useRef<HTMLImageElement>(null);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const {
    zoomLevel, panOffset, isPanning,
    handleZoomIn, handleZoomOut, handleWheel,
    handleMouseDown, handleMouseMove, handleMouseUp,
    handleTouchStart, handleTouchMove, handleTouchEnd,
    resetZoom,
  } = useZoomPan({ containerRef });

  // Ref to allow reconnect hook to call mjpeg restart without circular deps
  const mjpegRestartRef = useRef<() => void>(() => {});

  const isStream = streamMode === 'stream';

  // Stream URL — quality preset is resolved server-side from settings
  const streamUrl = `/printers/${id}/camera/stream`;

  // --- Stream hooks (only active in stream mode) ---
  const mjpeg = useMjpegStream({
    url: streamUrl,
    canvasRef,
    enabled: isStream && !transitioning && id > 0,
    onFirstFrame: () => reconnect.handleStreamSuccess(),
    onError: () => reconnect.handleStreamError(),
  });

  // Keep restart ref in sync
  useEffect(() => { mjpegRestartRef.current = mjpeg.restart; }, [mjpeg.restart]);

  const reconnect = useStreamReconnect({
    maxAttempts: MAX_RECONNECT_ATTEMPTS,
    onReconnect: () => mjpegRestartRef.current(),
    onGiveUp: () => {}, // hasError from mjpeg hook covers this
    stallPaused: !isStream || mjpeg.isLoading || transitioning,
    checkStalled,
  });

  // Fetch printer info for the title
  const { data: printer } = useQuery({
    queryKey: ['printer', id],
    queryFn: () => api.getPrinter(id),
    enabled: id > 0,
  });

  // Update document title
  useEffect(() => {
    if (printer) {
      document.title = `${printer.name} - ${t('camera.title')}`;
    }
    return () => {
      document.title = 'Bambuddy';
    };
  }, [printer, t]);

  // Cleanup on unmount — stop the camera stream
  useCameraStopHint(id);

  // Fullscreen change listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      const nowFullscreen = !!document.fullscreenElement;
      setIsFullscreen(nowFullscreen);
      resetZoom();

      // Refresh stream after fullscreen transition to prevent stall
      if (isStream && !transitioning) {
        mjpegRestartRef.current();
      }
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [isStream, transitioning]);

  // Save window size and position when user resizes or moves
  useEffect(() => {
    let saveTimeout: NodeJS.Timeout;
    const saveWindowState = () => {
      clearTimeout(saveTimeout);
      saveTimeout = setTimeout(() => {
        localStorage.setItem('cameraWindowState', JSON.stringify({
          width: window.outerWidth,
          height: window.outerHeight,
          left: window.screenX,
          top: window.screenY,
        }));
      }, 500);
    };

    window.addEventListener('resize', saveWindowState);

    return () => {
      clearTimeout(saveTimeout);
      window.removeEventListener('resize', saveWindowState);
    };
  }, []);

  const stopStream = () => {
    if (id > 0) {
      const headers: Record<string, string> = {};
      const token = getAuthToken();
      if (token) headers['Authorization'] = `Bearer ${token}`;
      fetch(`/api/v1/printers/${id}/camera/stop`, { method: 'POST', headers }).catch(() => {});
    }
  };

  const switchToMode = (newMode: 'stream' | 'snapshot') => {
    if (streamMode === newMode || transitioning) return;
    setTransitioning(true);
    resetZoom();

    // Stop stream when switching away from stream mode
    if (streamMode === 'stream') {
      mjpeg.stop();
      stopStream();
    }

    // Reset reconnect state
    reconnect.reset();

    // Reset snapshot state if switching to snapshot
    if (newMode === 'snapshot') {
      setSnapshotBlobUrl(null);
      setSnapshotLoading(true);
      setSnapshotError(false);
    }

    setTimeout(() => {
      setStreamMode(newMode);
      if (newMode === 'snapshot') {
        setSnapshotKey(Date.now());
      }
      setTransitioning(false);
    }, 100);
  };

  const refresh = () => {
    if (transitioning) return;
    setTransitioning(true);
    resetZoom();

    // Reset reconnect state
    reconnect.reset();

    if (isStream) {
      mjpeg.stop();
      stopStream();
    }

    setTimeout(() => {
      if (isStream) {
        mjpeg.restart();
      } else {
        setSnapshotLoading(true);
        setSnapshotError(false);
        setSnapshotKey(Date.now());
      }
      setTransitioning(false);
    }, 100);
  };

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      containerRef.current.requestFullscreen();
    }
  };

  // Derive loading/error from the appropriate source
  const loading = isStream ? mjpeg.isLoading : snapshotLoading;
  const hasError = isStream ? mjpeg.hasError : snapshotError;
  const { isReconnecting, reconnectCountdown, reconnectAttempts } = reconnect;
  const isDisabled = loading || transitioning || isReconnecting;

  // Fetch snapshot via blob URL (sends auth headers, avoids PUBLIC_API_PATTERNS)
  useEffect(() => {
    if (isStream || !id) return;
    let cancelled = false;
    let blobUrl: string | null = null;

    setSnapshotLoading(true);
    setSnapshotError(false);

    (async () => {
      try {
        const headers: Record<string, string> = {};
        const token = getAuthToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;

        const res = await fetch(`/api/v1/printers/${id}/camera/snapshot?t=${snapshotKey}`, { headers });
        if (cancelled) return;
        if (!res.ok) throw new Error(`Snapshot failed: ${res.status}`);

        const blob = await res.blob();
        if (cancelled) return;

        blobUrl = URL.createObjectURL(blob);
        setSnapshotBlobUrl(blobUrl);
        setSnapshotLoading(false);
        setSnapshotError(false);

        // Auto-resize window to fit video content (only if no saved preference)
        if (!localStorage.getItem('cameraWindowState')) {
          const img = new Image();
          img.onload = () => {
            if (img.naturalWidth > 0 && img.naturalHeight > 0) {
              const headerHeight = 45;
              const padding = 16;
              const chromeWidth = window.outerWidth - window.innerWidth;
              const chromeHeight = window.outerHeight - window.innerHeight;
              const targetWidth = img.naturalWidth + padding + chromeWidth;
              const targetHeight = img.naturalHeight + headerHeight + padding + chromeHeight;
              try {
                window.resizeTo(targetWidth, targetHeight);
              } catch {
                // resizeTo may not be allowed in all contexts
              }
            }
          };
          img.src = blobUrl;
        }
      } catch {
        if (!cancelled) {
          setSnapshotLoading(false);
          setSnapshotError(true);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [isStream, id, snapshotKey]);

  if (!id) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <p className="text-white">{t('camera.invalidPrinterId')}</p>
      </div>
    );
  }

  const transformStyle = {
    transform: `scale(${zoomLevel}) translate(${panOffset.x / zoomLevel}px, ${panOffset.y / zoomLevel}px)`,
    cursor: zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'default',
  };

  return (
    <div ref={containerRef} className="min-h-screen bg-black flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
        <h1 className="text-sm font-medium text-white flex items-center gap-2">
          <Camera className="w-4 h-4" />
          {printer?.name || `Printer ${id}`}
        </h1>
        <div className="flex items-center gap-2">
          {/* Mode toggle */}
          <div className="flex bg-bambu-dark rounded p-0.5">
            <button
              onClick={() => switchToMode('stream')}
              disabled={isDisabled}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                streamMode === 'stream'
                  ? 'bg-bambu-green text-white'
                  : 'text-bambu-gray hover:text-white disabled:opacity-50'
              }`}
            >
              {t('camera.live')}
            </button>
            <button
              onClick={() => switchToMode('snapshot')}
              disabled={isDisabled}
              className={`px-3 py-1 text-xs rounded transition-colors ${
                streamMode === 'snapshot'
                  ? 'bg-bambu-green text-white'
                  : 'text-bambu-gray hover:text-white disabled:opacity-50'
              }`}
            >
              {t('camera.snapshot')}
            </button>
          </div>
          <button
            onClick={() => chamberLightMutation.mutate(!status?.chamber_light)}
            disabled={!status?.connected || chamberLightMutation.isPending || !hasControlPermission}
            className={`p-1.5 rounded disabled:opacity-50 ${status?.chamber_light ? 'bg-yellow-500/20 hover:bg-yellow-500/30' : 'hover:bg-bambu-dark-tertiary'}`}
            title={!hasControlPermission ? t('printers.permission.noControl') : t('camera.chamberLight')}
          >
            <ChamberLight on={status?.chamber_light ?? false} className="w-4 h-4" />
          </button>
          <button
            onClick={() => setShowSkipObjectsModal(true)}
            disabled={!isPrintingWithObjects || !hasControlPermission}
            className={`p-1.5 rounded disabled:opacity-50 ${isPrintingWithObjects && hasControlPermission ? 'hover:bg-bambu-dark-tertiary' : ''}`}
            title={
              !hasControlPermission
                ? t('printers.permission.noControl')
                : !isPrintingWithObjects
                  ? t('printers.skipObjects.onlyWhilePrinting')
                  : t('printers.skipObjects.tooltip')
            }
          >
            <SkipObjectsIcon className="w-4 h-4 text-bambu-gray" />
          </button>
          <button
            onClick={refresh}
            disabled={isDisabled}
            className="p-1.5 hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
            title={streamMode === 'stream' ? t('camera.restartStream') : t('camera.refreshSnapshot')}
          >
            <RefreshCw className={`w-4 h-4 text-bambu-gray ${isDisabled ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={toggleFullscreen}
            className="p-1.5 hover:bg-bambu-dark-tertiary rounded"
            title={isFullscreen ? t('camera.exitFullscreen') : t('camera.fullscreen')}
          >
            {isFullscreen ? (
              <Minimize className="w-4 h-4 text-bambu-gray" />
            ) : (
              <Maximize className="w-4 h-4 text-bambu-gray" />
            )}
          </button>
        </div>
      </div>

      {/* Video area */}
      <div
        className="flex-1 flex items-center justify-center p-2 overflow-hidden"
        onWheel={handleWheel}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{ touchAction: 'none' }}
      >
        <div className="relative w-full h-full flex items-center justify-center">
          {(loading || transitioning) && !isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <div className="text-center">
                <RefreshCw className="w-8 h-8 text-bambu-gray animate-spin mx-auto mb-2" />
                <p className="text-sm text-bambu-gray">
                  {isStream ? t('camera.connectingToCamera') : t('camera.capturingSnapshot')}
                </p>
              </div>
            </div>
          )}
          {isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
              <div className="text-center p-4">
                <WifiOff className="w-10 h-10 text-orange-400 mx-auto mb-3" />
                <p className="text-white mb-2">{t('camera.connectionLost')}</p>
                <p className="text-sm text-bambu-gray mb-3">
                  {t('camera.reconnecting', { countdown: reconnectCountdown, attempt: reconnectAttempts + 1, max: MAX_RECONNECT_ATTEMPTS })}
                </p>
                <button
                  onClick={refresh}
                  className="px-4 py-2 bg-bambu-green text-white text-sm rounded hover:bg-bambu-green/80 transition-colors"
                >
                  {t('camera.reconnectNow')}
                </button>
              </div>
            </div>
          )}
          {hasError && !isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black z-10">
              <div className="text-center p-4">
                <AlertTriangle className="w-12 h-12 text-orange-400 mx-auto mb-3" />
                <p className="text-white mb-2">{t('camera.cameraUnavailable')}</p>
                <p className="text-xs text-bambu-gray mb-4 max-w-md">
                  {t('camera.cameraUnavailableDesc')}
                </p>
                <button
                  onClick={refresh}
                  className="px-4 py-2 bg-bambu-green text-white rounded hover:bg-bambu-green/80 transition-colors"
                >
                  {t('camera.retry')}
                </button>
              </div>
            </div>
          )}

          {/* Stream mode: canvas */}
          {isStream && (
            <canvas
              ref={canvasRef}
              className="max-w-full max-h-full object-contain select-none"
              style={transformStyle}
              onMouseDown={handleMouseDown}
            />
          )}

          {/* Snapshot mode: img */}
          {!isStream && (
            <img
              ref={snapshotImgRef}
              key={snapshotKey}
              src={snapshotBlobUrl || ''}
              alt={t('camera.cameraStream')}
              className="max-w-full max-h-full object-contain select-none"
              style={transformStyle}
              onMouseDown={handleMouseDown}
              draggable={false}
            />
          )}

          {/* Zoom controls */}
          <div className="absolute bottom-4 left-4 flex items-center gap-1.5 bg-black/60 rounded-lg px-2 py-1.5">
            <button
              onClick={handleZoomOut}
              disabled={zoomLevel <= 1}
              className="p-1.5 hover:bg-white/10 rounded disabled:opacity-30"
              title={t('camera.zoomOut')}
            >
              <ZoomOut className="w-4 h-4 text-white" />
            </button>
            <button
              onClick={resetZoom}
              className="px-2 py-1 text-sm text-white hover:bg-white/10 rounded min-w-[48px]"
              title={t('camera.resetZoom')}
            >
              {Math.round(zoomLevel * 100)}%
            </button>
            <button
              onClick={handleZoomIn}
              disabled={zoomLevel >= 4}
              className="p-1.5 hover:bg-white/10 rounded disabled:opacity-30"
              title={t('camera.zoomIn')}
            >
              <ZoomIn className="w-4 h-4 text-white" />
            </button>
          </div>
        </div>
      </div>

      {/* Skip Objects Modal */}
      <SkipObjectsModal
        printerId={id}
        isOpen={showSkipObjectsModal}
        onClose={() => setShowSkipObjectsModal(false)}
      />
    </div>
  );
}
