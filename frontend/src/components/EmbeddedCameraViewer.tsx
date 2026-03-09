import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, RefreshCw, AlertTriangle, Maximize2, Minimize2, GripVertical, WifiOff, ZoomIn, ZoomOut, Fullscreen, Minimize } from 'lucide-react';
import { api } from '../api/client';
import { useCameraStopHint } from '../hooks/useCameraStopHint';
import { useCameraControls } from '../hooks/useCameraControls';
import { ChamberLight } from './icons/ChamberLight';
import { SkipObjectsModal, SkipObjectsIcon } from './SkipObjectsModal';
import { useMjpegStream } from '../hooks/useMjpegStream';
import { useStreamReconnect } from '../hooks/useStreamReconnect';
import { useZoomPan } from '../hooks/useZoomPan';

interface EmbeddedCameraViewerProps {
  printerId: number;
  printerName: string;
  viewerIndex?: number;  // Used to offset multiple viewers
  onClose: () => void;
}

import { getDefaultState, type CameraState } from './cameraDefaults';

const STORAGE_KEY_PREFIX = 'embeddedCameraState_';

export function EmbeddedCameraViewer({ printerId, printerName, viewerIndex = 0, onClose }: EmbeddedCameraViewerProps) {
  const { t } = useTranslation();

  const {
    status,
    chamberLightMutation,
    isPrintingWithObjects,
    showSkipObjectsModal,
    setShowSkipObjectsModal,
    checkStalled,
    hasControlPermission,
  } = useCameraControls({ printerId });

  // Printer-specific storage key
  const storageKey = `${STORAGE_KEY_PREFIX}${printerId}`;

  // Load saved state or use defaults (offset for new viewers without saved state)
  const loadState = (): CameraState => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (saved) {
        const state = JSON.parse(saved);
        // Validate state is on screen
        return {
          x: Math.min(Math.max(0, state.x), window.innerWidth - 100),
          y: Math.min(Math.max(0, state.y), window.innerHeight - 100),
          width: Math.max(200, Math.min(state.width, window.innerWidth - 20)),
          height: Math.max(150, Math.min(state.height, window.innerHeight - 20)),
        };
      }
    } catch {
      // Ignore parse errors
    }
    // Offset new viewers so they don't stack exactly on top of each other
    const offset = viewerIndex * 30;
    const defaults = getDefaultState();
    return {
      ...defaults,
      x: Math.max(0, defaults.x - offset),
      y: Math.max(0, defaults.y + offset),
    };
  };

  const [state, setState] = useState<CameraState>(loadState);
  const [isDragging, setIsDragging] = useState(false);
  const [isResizing, setIsResizing] = useState(false);
  const dragOffsetRef = useRef({ x: 0, y: 0 });
  const [isMinimized, setIsMinimized] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  const [streamError, setStreamError] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mjpegRestartRef = useRef<() => void>(() => {});

  const {
    zoomLevel,
    handleZoomIn, handleZoomOut, handleWheel,
    handleMouseDown: handleCanvasMouseDown,
    handleMouseMove: handleImageMouseMove,
    handleMouseUp: handleImageMouseUp,
    handleTouchStart, handleTouchMove, handleTouchEnd,
    resetZoom, transformStyle,
  } = useZoomPan({ containerRef, defaultMaxPan: { x: 200, y: 150 } });

  // Fetch printer info
  const { data: printer } = useQuery({
    queryKey: ['printer', printerId],
    queryFn: () => api.getPrinter(printerId),
    enabled: printerId > 0,
  });

  // Reconnect logic
  const reconnect = useStreamReconnect({
    onReconnect: () => mjpegRestartRef.current(),
    onGiveUp: () => setStreamError(true),
    stallPaused: isMinimized,
    checkStalled,
  });

  // Stream URL — quality preset is resolved server-side from settings
  const streamUrl = `/printers/${printerId}/camera/stream`;
  const mjpeg = useMjpegStream({
    url: streamUrl,
    canvasRef,
    enabled: !isMinimized,
    onFirstFrame: () => {
      setStreamError(false);
      reconnect.handleStreamSuccess();
    },
    onError: () => reconnect.handleStreamError(),
  });
  mjpegRestartRef.current = mjpeg.restart;

  // Save state to localStorage (printer-specific)
  useEffect(() => {
    const saveTimeout = setTimeout(() => {
      localStorage.setItem(storageKey, JSON.stringify(state));
    }, 500);
    return () => clearTimeout(saveTimeout);
  }, [state, storageKey]);

  // Cleanup on unmount — send stop hint
  useCameraStopHint(printerId);

  // Fullscreen change listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      const nowFullscreen = !!document.fullscreenElement;
      setIsFullscreen(nowFullscreen);
      if (!nowFullscreen) {
        resetZoom();
      }
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [resetZoom]);

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      containerRef.current.requestFullscreen();
    }
  };

  const refresh = () => {
    setStreamError(false);
    reconnect.reset();

    api.stopCameraStream(printerId).catch(() => {});

    setTimeout(() => mjpeg.restart(), 100);
  };

  // Drag handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.no-drag')) return;
    setIsDragging(true);
    dragOffsetRef.current = {
      x: e.clientX - state.x,
      y: e.clientY - state.y,
    };
  };

  // Resize handlers
  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsResizing(true);
  };

  useEffect(() => {
    let rafId = 0;
    const handleMouseMove = (e: MouseEvent) => {
      if (rafId) return; // Already scheduled
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        if (isDragging) {
          const offset = dragOffsetRef.current;
          setState((prev) => ({
            ...prev,
            x: Math.max(0, Math.min(e.clientX - offset.x, window.innerWidth - prev.width)),
            y: Math.max(0, Math.min(e.clientY - offset.y, window.innerHeight - prev.height)),
          }));
        } else if (isResizing && containerRef.current) {
          const rect = containerRef.current.getBoundingClientRect();
          setState((prev) => ({
            ...prev,
            width: Math.max(200, Math.min(e.clientX - rect.left, window.innerWidth - prev.x - 10)),
            height: Math.max(150, Math.min(e.clientY - rect.top, window.innerHeight - prev.y - 10)),
          }));
        }
      });
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      setIsResizing(false);
    };

    if (isDragging || isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      return () => {
        document.removeEventListener('mousemove', handleMouseMove);
        document.removeEventListener('mouseup', handleMouseUp);
        if (rafId) cancelAnimationFrame(rafId);
      };
    }
  }, [isDragging, isResizing]);

  const streamLoading = mjpeg.isLoading;

  return (
    <div
      ref={containerRef}
      className={`${isFullscreen ? 'fixed inset-0 z-[100]' : 'fixed z-50 rounded-lg shadow-2xl border border-bambu-dark-tertiary'} bg-bambu-dark-secondary overflow-hidden`}
      style={isFullscreen ? undefined : {
        left: state.x,
        top: state.y,
        width: isMinimized ? 200 : state.width,
        height: isMinimized ? 40 : state.height,
        cursor: isDragging ? 'grabbing' : 'default',
      }}
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 py-2 bg-bambu-dark border-b border-bambu-dark-tertiary cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
      >
        <div className="flex items-center gap-2 text-sm text-white truncate">
          <GripVertical className="w-4 h-4 text-bambu-gray flex-shrink-0" />
          <span className="truncate">{printer?.name || printerName}</span>
        </div>
        <div className="flex items-center gap-1 no-drag">
          <button
            onClick={() => chamberLightMutation.mutate(!status?.chamber_light)}
            disabled={!status?.connected || chamberLightMutation.isPending || !hasControlPermission}
            className={`p-1 rounded disabled:opacity-50 ${status?.chamber_light ? 'bg-yellow-500/20 hover:bg-yellow-500/30' : 'hover:bg-bambu-dark-tertiary'}`}
            title={!hasControlPermission ? t('printers.permission.noControl') : t('camera.chamberLight')}
          >
            <ChamberLight on={status?.chamber_light ?? false} className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setShowSkipObjectsModal(true)}
            disabled={!isPrintingWithObjects || !hasControlPermission}
            className={`p-1 rounded disabled:opacity-50 ${isPrintingWithObjects && hasControlPermission ? 'hover:bg-bambu-dark-tertiary' : ''}`}
            title={
              !hasControlPermission
                ? t('printers.permission.noControl')
                : !isPrintingWithObjects
                  ? t('printers.skipObjects.onlyWhilePrinting')
                  : t('printers.skipObjects.tooltip')
            }
          >
            <SkipObjectsIcon className="w-3.5 h-3.5 text-bambu-gray" />
          </button>
          <button
            onClick={refresh}
            disabled={streamLoading || reconnect.isReconnecting}
            className="p-1 hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
            title={t('camera.refreshStream')}
          >
            <RefreshCw className={`w-3.5 h-3.5 text-bambu-gray ${streamLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={toggleFullscreen}
            className="p-1 hover:bg-bambu-dark-tertiary rounded"
            title={isFullscreen ? t('camera.exitFullscreen') : t('camera.fullscreen')}
          >
            {isFullscreen ? (
              <Minimize className="w-3.5 h-3.5 text-bambu-gray" />
            ) : (
              <Fullscreen className="w-3.5 h-3.5 text-bambu-gray" />
            )}
          </button>
          <button
            onClick={() => setIsMinimized(!isMinimized)}
            className="p-1 hover:bg-bambu-dark-tertiary rounded"
            title={isMinimized ? t('camera.expand') : t('camera.minimize')}
          >
            {isMinimized ? (
              <Maximize2 className="w-3.5 h-3.5 text-bambu-gray" />
            ) : (
              <Minimize2 className="w-3.5 h-3.5 text-bambu-gray" />
            )}
          </button>
          <button
            onClick={onClose}
            className="p-1 hover:bg-red-500/20 rounded"
            title={t('common.close')}
          >
            <X className="w-3.5 h-3.5 text-bambu-gray hover:text-red-400" />
          </button>
        </div>
      </div>

      {/* Video area */}
      {!isMinimized && (
        <div
          className="relative w-full bg-black flex items-center justify-center overflow-hidden h-[calc(100%-40px)]"
          onWheel={handleWheel}
          onMouseMove={handleImageMouseMove}
          onMouseUp={handleImageMouseUp}
          onMouseLeave={handleImageMouseUp}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          style={{ touchAction: 'none' }}
        >
          {streamLoading && !reconnect.isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <RefreshCw className="w-6 h-6 text-bambu-gray animate-spin" />
            </div>
          )}
          {reconnect.isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
              <div className="text-center p-2">
                <WifiOff className="w-6 h-6 text-orange-400 mx-auto mb-2" />
                <p className="text-xs text-bambu-gray">
                  {t('camera.reconnectingIn', { countdown: reconnect.reconnectCountdown })}
                </p>
              </div>
            </div>
          )}
          {streamError && !reconnect.isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black z-10">
              <div className="text-center p-2">
                <AlertTriangle className="w-6 h-6 text-orange-400 mx-auto mb-2" />
                <p className="text-xs text-bambu-gray mb-2">{t('printers.cameraGrid.cameraUnavailable')}</p>
                <button
                  onClick={refresh}
                  className="px-2 py-1 text-xs bg-bambu-green text-white rounded hover:bg-bambu-green/80"
                >
                  {t('camera.retry')}
                </button>
              </div>
            </div>
          )}
          <canvas
            ref={canvasRef}
            className="max-w-full max-h-full object-contain select-none"
            style={transformStyle}
            onMouseDown={handleCanvasMouseDown}
          />

          {/* Zoom controls */}
          <div className="absolute bottom-2 left-2 flex items-center gap-1 bg-black/60 rounded px-1.5 py-1 no-drag">
            <button
              onClick={handleZoomOut}
              disabled={zoomLevel <= 1}
              className="p-1 hover:bg-white/10 rounded disabled:opacity-30"
              title={t('camera.zoomOut')}
            >
              <ZoomOut className="w-3.5 h-3.5 text-white" />
            </button>
            <button
              onClick={resetZoom}
              className="px-1.5 py-0.5 text-xs text-white hover:bg-white/10 rounded min-w-[32px]"
              title={t('camera.resetZoom')}
            >
              {Math.round(zoomLevel * 100)}%
            </button>
            <button
              onClick={handleZoomIn}
              disabled={zoomLevel >= 4}
              className="p-1 hover:bg-white/10 rounded disabled:opacity-30"
              title={t('camera.zoomIn')}
            >
              <ZoomIn className="w-3.5 h-3.5 text-white" />
            </button>
          </div>

          {/* Resize handle - hide in fullscreen */}
          {!isFullscreen && (
            <div
              className="absolute bottom-0 right-0 w-6 h-6 cursor-se-resize no-drag hover:bg-white/10 rounded-tl transition-colors"
              onMouseDown={handleResizeMouseDown}
              title={t('camera.resize')}
            >
              <svg
                className="w-6 h-6 text-bambu-gray/70 hover:text-bambu-gray"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path d="M22 22H20V20H22V22ZM22 18H20V16H22V18ZM18 22H16V20H18V22ZM22 14H20V12H22V14ZM18 18H16V16H18V18ZM14 22H12V20H14V22ZM22 10H20V8H22V10ZM18 14H16V12H18V14ZM14 18H12V16H14V18ZM10 22H8V20H10V22Z" />
              </svg>
            </div>
          )}
        </div>
      )}
      {/* Skip Objects Modal */}
      <SkipObjectsModal
        printerId={printerId}
        isOpen={showSkipObjectsModal}
        onClose={() => setShowSkipObjectsModal(false)}
      />
    </div>
  );
}
