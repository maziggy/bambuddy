import { useState, useEffect, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, RefreshCw, AlertTriangle, Maximize2, Minimize2, GripVertical, WifiOff, ZoomIn, ZoomOut, Fullscreen, Minimize } from 'lucide-react';
import { api, getAuthToken } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { useAuth } from '../contexts/AuthContext';
import { ChamberLight } from './icons/ChamberLight';
import { SkipObjectsModal, SkipObjectsIcon } from './SkipObjectsModal';
import { useMjpegStream } from '../hooks/useMjpegStream';
import { useStreamReconnect } from '../hooks/useStreamReconnect';

interface EmbeddedCameraViewerProps {
  printerId: number;
  printerName: string;
  viewerIndex?: number;  // Used to offset multiple viewers
  onClose: () => void;
}

const STORAGE_KEY_PREFIX = 'embeddedCameraState_';

interface CameraState {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** @internal */
export const getDefaultState = (): CameraState => ({
  x: window.innerWidth - 420,
  y: 20,
  width: 400,
  height: 300,
});

export function EmbeddedCameraViewer({ printerId, printerName, viewerIndex = 0, onClose }: EmbeddedCameraViewerProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();

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
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [isMinimized, setIsMinimized] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [lastTouchDistance, setLastTouchDistance] = useState<number | null>(null);
  const [lastTouchCenter, setLastTouchCenter] = useState<{ x: number; y: number } | null>(null);

  const [streamError, setStreamError] = useState(false);
  const [showSkipObjectsModal, setShowSkipObjectsModal] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mjpegRestartRef = useRef<() => void>(() => {});

  // Fetch printer info
  const { data: printer } = useQuery({
    queryKey: ['printer', printerId],
    queryFn: () => api.getPrinter(printerId),
    enabled: printerId > 0,
  });

  // Fetch printer status for light toggle and skip objects
  const { data: status } = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 30000,
    enabled: printerId > 0,
  });

  // Chamber light mutation with optimistic update
  const chamberLightMutation = useMutation({
    mutationFn: (on: boolean) => api.setChamberLight(printerId, on),
    onMutate: async (on) => {
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printerId] });
      const previousStatus = queryClient.getQueryData(['printerStatus', printerId]);
      queryClient.setQueryData(['printerStatus', printerId], (old: typeof status) => ({
        ...old,
        chamber_light: on,
      }));
      return { previousStatus };
    },
    onSuccess: (_, on) => {
      showToast(t(on ? 'printers.chamberLightOn' : 'printers.chamberLightOff'));
    },
    onError: (error: Error, _, context) => {
      if (context?.previousStatus) {
        queryClient.setQueryData(['printerStatus', printerId], context.previousStatus);
      }
      showToast(error.message || t('printers.toast.failedToControlChamberLight'), 'error');
    },
  });

  const isPrintingWithObjects = (status?.state === 'RUNNING' || status?.state === 'PAUSE') && (status?.printable_objects_count ?? 0) >= 2;

  // Reconnect logic
  const checkStalled = useCallback(async () => {
    const s = await api.getCameraStatus(printerId);
    return s.stalled || (!s.active && !streamError);
  }, [printerId, streamError]);

  const reconnect = useStreamReconnect({
    onReconnect: () => mjpegRestartRef.current(),
    onGiveUp: () => setStreamError(true),
    stallPaused: isMinimized,
    checkStalled,
  });

  // MJPEG stream via fetch+canvas
  const streamUrl = `/printers/${printerId}/camera/stream?fps=15&quality=5&scale=1.0`;
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
  const stopSentRef = useRef(false);
  useEffect(() => {
    stopSentRef.current = false;
    const stopUrl = `/api/v1/printers/${printerId}/camera/stop`;

    const sendStopOnce = () => {
      if (printerId > 0 && !stopSentRef.current) {
        stopSentRef.current = true;
        const headers: Record<string, string> = {};
        const token = getAuthToken();
        if (token) headers['Authorization'] = `Bearer ${token}`;
        fetch(stopUrl, { method: 'POST', keepalive: true, headers }).catch(() => {});
      }
    };

    return () => {
      sendStopOnce();
    };
  }, [printerId]);

  // Fullscreen change listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      const nowFullscreen = !!document.fullscreenElement;
      setIsFullscreen(nowFullscreen);
      if (!nowFullscreen) {
        setZoomLevel(1);
        setPanOffset({ x: 0, y: 0 });
      }
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  const toggleFullscreen = () => {
    if (!containerRef.current) return;
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      containerRef.current.requestFullscreen();
    }
  };

  const handleZoomIn = () => {
    setZoomLevel(prev => Math.min(prev + 0.5, 4));
  };

  const handleZoomOut = () => {
    setZoomLevel(prev => {
      const newZoom = Math.max(prev - 0.5, 1);
      if (newZoom === 1) setPanOffset({ x: 0, y: 0 });
      return newZoom;
    });
  };

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    if (e.deltaY < 0) {
      handleZoomIn();
    } else {
      handleZoomOut();
    }
  };

  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    if (zoomLevel > 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
    }
  };

  const getMaxPan = useCallback(() => {
    if (!containerRef.current) {
      return { x: 200, y: 150 };
    }
    const container = containerRef.current.getBoundingClientRect();
    const maxX = (container.width * (zoomLevel - 1)) / 2;
    const maxY = (container.height * (zoomLevel - 1)) / 2;
    return { x: Math.max(50, maxX), y: Math.max(50, maxY) };
  }, [zoomLevel]);

  const handleImageMouseMove = (e: React.MouseEvent) => {
    if (isPanning && zoomLevel > 1) {
      const newX = e.clientX - panStart.x;
      const newY = e.clientY - panStart.y;
      const maxPan = getMaxPan();
      setPanOffset({
        x: Math.max(-maxPan.x, Math.min(maxPan.x, newX)),
        y: Math.max(-maxPan.y, Math.min(maxPan.y, newY)),
      });
    }
  };

  const handleImageMouseUp = () => {
    setIsPanning(false);
  };

  // Touch event handlers for mobile
  const getTouchDistance = (touches: React.TouchList) => {
    if (touches.length < 2) return 0;
    const dx = touches[0].clientX - touches[1].clientX;
    const dy = touches[0].clientY - touches[1].clientY;
    return Math.sqrt(dx * dx + dy * dy);
  };

  const getTouchCenter = (touches: React.TouchList) => {
    if (touches.length < 2) {
      return { x: touches[0].clientX, y: touches[0].clientY };
    }
    return {
      x: (touches[0].clientX + touches[1].clientX) / 2,
      y: (touches[0].clientY + touches[1].clientY) / 2,
    };
  };

  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      e.preventDefault();
      setLastTouchDistance(getTouchDistance(e.touches));
      setLastTouchCenter(getTouchCenter(e.touches));
    } else if (e.touches.length === 1 && zoomLevel > 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({
        x: e.touches[0].clientX - panOffset.x,
        y: e.touches[0].clientY - panOffset.y,
      });
    }
  };

  const handleTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2 && lastTouchDistance !== null) {
      e.preventDefault();
      const newDistance = getTouchDistance(e.touches);
      const scale = newDistance / lastTouchDistance;

      setZoomLevel(prev => {
        const newZoom = Math.max(1, Math.min(4, prev * scale));
        if (newZoom === 1) {
          setPanOffset({ x: 0, y: 0 });
        }
        return newZoom;
      });

      setLastTouchDistance(newDistance);

      const newCenter = getTouchCenter(e.touches);
      if (lastTouchCenter) {
        const maxPan = getMaxPan();
        setPanOffset(prev => ({
          x: Math.max(-maxPan.x, Math.min(maxPan.x, prev.x + (newCenter.x - lastTouchCenter.x))),
          y: Math.max(-maxPan.y, Math.min(maxPan.y, prev.y + (newCenter.y - lastTouchCenter.y))),
        }));
      }
      setLastTouchCenter(newCenter);
    } else if (e.touches.length === 1 && isPanning && zoomLevel > 1) {
      e.preventDefault();
      const newX = e.touches[0].clientX - panStart.x;
      const newY = e.touches[0].clientY - panStart.y;
      const maxPan = getMaxPan();
      setPanOffset({
        x: Math.max(-maxPan.x, Math.min(maxPan.x, newX)),
        y: Math.max(-maxPan.y, Math.min(maxPan.y, newY)),
      });
    }
  };

  const handleTouchEnd = (e: React.TouchEvent) => {
    if (e.touches.length < 2) {
      setLastTouchDistance(null);
      setLastTouchCenter(null);
    }
    if (e.touches.length === 0) {
      setIsPanning(false);
    }
  };

  const resetZoom = () => {
    setZoomLevel(1);
    setPanOffset({ x: 0, y: 0 });
  };

  const refresh = () => {
    setStreamError(false);
    reconnect.reset();

    const stopHeaders: Record<string, string> = {};
    const stopToken = getAuthToken();
    if (stopToken) stopHeaders['Authorization'] = `Bearer ${stopToken}`;
    fetch(`/api/v1/printers/${printerId}/camera/stop`, { method: 'POST', headers: stopHeaders }).catch(() => {});

    setTimeout(() => mjpeg.restart(), 100);
  };

  // Drag handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.no-drag')) return;
    setIsDragging(true);
    setDragOffset({
      x: e.clientX - state.x,
      y: e.clientY - state.y,
    });
  };

  // Resize handlers
  const handleResizeMouseDown = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsResizing(true);
  };

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (isDragging) {
        setState((prev) => ({
          ...prev,
          x: Math.max(0, Math.min(e.clientX - dragOffset.x, window.innerWidth - prev.width)),
          y: Math.max(0, Math.min(e.clientY - dragOffset.y, window.innerHeight - prev.height)),
        }));
      } else if (isResizing && containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        setState((prev) => ({
          ...prev,
          width: Math.max(200, Math.min(e.clientX - rect.left, window.innerWidth - prev.x - 10)),
          height: Math.max(150, Math.min(e.clientY - rect.top, window.innerHeight - prev.y - 10)),
        }));
      }
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
      };
    }
  }, [isDragging, isResizing, dragOffset]);

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
            disabled={!status?.connected || chamberLightMutation.isPending || !hasPermission('printers:control')}
            className={`p-1 rounded disabled:opacity-50 ${status?.chamber_light ? 'bg-yellow-500/20 hover:bg-yellow-500/30' : 'hover:bg-bambu-dark-tertiary'}`}
            title={!hasPermission('printers:control') ? t('printers.permission.noControl') : t('camera.chamberLight')}
          >
            <ChamberLight on={status?.chamber_light ?? false} className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setShowSkipObjectsModal(true)}
            disabled={!isPrintingWithObjects || !hasPermission('printers:control')}
            className={`p-1 rounded disabled:opacity-50 ${isPrintingWithObjects && hasPermission('printers:control') ? 'hover:bg-bambu-dark-tertiary' : ''}`}
            title={
              !hasPermission('printers:control')
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
            style={{
              transform: `scale(${zoomLevel}) translate(${panOffset.x / zoomLevel}px, ${panOffset.y / zoomLevel}px)`,
              cursor: zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'default',
            }}
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
