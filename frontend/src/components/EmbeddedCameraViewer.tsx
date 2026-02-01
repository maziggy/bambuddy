import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { X, RefreshCw, AlertTriangle, Maximize2, Minimize2, GripVertical, WifiOff, ZoomIn, ZoomOut, Fullscreen, Minimize } from 'lucide-react';
import { api } from '../api/client';

interface EmbeddedCameraViewerProps {
  printerId: number;
  printerName: string;
  viewerIndex?: number;  // Used to offset multiple viewers
  onClose: () => void;
}

const STORAGE_KEY_PREFIX = 'embeddedCameraState_';
const MAX_RECONNECT_ATTEMPTS = 5;
const INITIAL_RECONNECT_DELAY = 2000;
const MAX_RECONNECT_DELAY = 30000;
const STALL_CHECK_INTERVAL = 5000;

interface CameraState {
  x: number;
  y: number;
  width: number;
  height: number;
}

const DEFAULT_STATE: CameraState = {
  x: window.innerWidth - 420,
  y: 20,
  width: 400,
  height: 300,
};

export function EmbeddedCameraViewer({ printerId, printerName, viewerIndex = 0, onClose }: EmbeddedCameraViewerProps) {
  const { t } = useTranslation();
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
    return {
      ...DEFAULT_STATE,
      x: Math.max(0, DEFAULT_STATE.x - offset),
      y: Math.max(0, DEFAULT_STATE.y + offset),
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

  // Stream state
  const [streamError, setStreamError] = useState(false);
  const [streamLoading, setStreamLoading] = useState(true);
  const [imageKey, setImageKey] = useState(Date.now());
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [reconnectCountdown, setReconnectCountdown] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);
  const countdownIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const stallCheckIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch printer info
  const { data: printer } = useQuery({
    queryKey: ['printer', printerId],
    queryFn: () => api.getPrinter(printerId),
    enabled: printerId > 0,
  });

  // Save state to localStorage (printer-specific)
  useEffect(() => {
    const saveTimeout = setTimeout(() => {
      localStorage.setItem(storageKey, JSON.stringify(state));
    }, 500);
    return () => clearTimeout(saveTimeout);
  }, [state, storageKey]);

  // Cleanup on unmount
  const stopSentRef = useRef(false);
  useEffect(() => {
    stopSentRef.current = false;
    const stopUrl = `/api/v1/printers/${printerId}/camera/stop`;

    const sendStopOnce = () => {
      if (printerId > 0 && !stopSentRef.current) {
        stopSentRef.current = true;
        navigator.sendBeacon(stopUrl);
      }
    };

    const imgElement = imgRef.current;

    return () => {
      if (imgElement) {
        imgElement.src = '';
      }
      sendStopOnce();
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
      if (stallCheckIntervalRef.current) clearInterval(stallCheckIntervalRef.current);
    };
  }, [printerId]);

  // Auto-hide loading after timeout
  useEffect(() => {
    if (streamLoading) {
      const timer = setTimeout(() => setStreamLoading(false), 3000);
      return () => clearTimeout(timer);
    }
  }, [streamLoading, imageKey]);

  // Auto-reconnect logic
  const attemptReconnect = useCallback(() => {
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setIsReconnecting(false);
      setStreamError(true);
      return;
    }

    const delay = Math.min(
      INITIAL_RECONNECT_DELAY * Math.pow(2, reconnectAttempts),
      MAX_RECONNECT_DELAY
    );

    setIsReconnecting(true);
    setReconnectCountdown(Math.ceil(delay / 1000));

    countdownIntervalRef.current = setInterval(() => {
      setReconnectCountdown((prev) => {
        if (prev <= 1) {
          if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    reconnectTimerRef.current = setTimeout(() => {
      setReconnectAttempts((prev) => prev + 1);
      setIsReconnecting(false);
      setStreamLoading(true);
      setStreamError(false);
      if (imgRef.current) imgRef.current.src = '';
      setImageKey(Date.now());
    }, delay);
  }, [reconnectAttempts]);

  // Stall detection
  useEffect(() => {
    if (streamLoading || isReconnecting || isMinimized) {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
      return;
    }

    stallCheckIntervalRef.current = setInterval(async () => {
      try {
        const response = await fetch(`/api/v1/printers/${printerId}/camera/status`);
        if (response.ok) {
          const status = await response.json();
          if (status.stalled || (!status.active && !streamError)) {
            if (stallCheckIntervalRef.current) {
              clearInterval(stallCheckIntervalRef.current);
              stallCheckIntervalRef.current = null;
            }
            setStreamLoading(false);
            attemptReconnect();
          }
        }
      } catch {
        // Ignore errors
      }
    }, STALL_CHECK_INTERVAL);

    return () => {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
    };
  }, [streamLoading, streamError, isReconnecting, isMinimized, printerId, attemptReconnect]);

  // Fullscreen change listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      const nowFullscreen = !!document.fullscreenElement;
      setIsFullscreen(nowFullscreen);
      // Reset zoom and pan when exiting fullscreen
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

  const handleImageMouseDown = (e: React.MouseEvent) => {
    if (zoomLevel > 1) {
      e.preventDefault();
      setIsPanning(true);
      setPanStart({ x: e.clientX - panOffset.x, y: e.clientY - panOffset.y });
    }
  };

  // Calculate max pan based on container size and zoom level
  const getMaxPan = useCallback(() => {
    if (!containerRef.current || !imgRef.current) {
      return { x: 200, y: 150 };
    }
    const container = containerRef.current.getBoundingClientRect();
    // Allow panning up to half the zoomed overflow in each direction
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
      // Pinch gesture start
      e.preventDefault();
      setLastTouchDistance(getTouchDistance(e.touches));
      setLastTouchCenter(getTouchCenter(e.touches));
    } else if (e.touches.length === 1 && zoomLevel > 1) {
      // Single touch pan start
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
      // Pinch gesture
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

      // Also handle pan during pinch
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
      // Single touch pan
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

  const handleStreamError = () => {
    setStreamLoading(false);
    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      attemptReconnect();
    } else {
      setStreamError(true);
    }
  };

  const handleStreamLoad = () => {
    setStreamLoading(false);
    setStreamError(false);
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
  };

  const refresh = () => {
    setStreamLoading(true);
    setStreamError(false);
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);

    fetch(`/api/v1/printers/${printerId}/camera/stop`).catch(() => {});

    if (imgRef.current) imgRef.current.src = '';
    setTimeout(() => setImageKey(Date.now()), 100);
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

  const streamUrl = `/api/v1/printers/${printerId}/camera/stream?fps=10&t=${imageKey}`;

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
            onClick={refresh}
            disabled={streamLoading || isReconnecting}
            className="p-1 hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
            title={t('camera.refreshStream')}
          >
            <RefreshCw className={`w-3.5 h-3.5 text-bambu-gray ${streamLoading ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={toggleFullscreen}
            className="p-1 hover:bg-bambu-dark-tertiary rounded"
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
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
            title={isMinimized ? 'Expand' : 'Minimize'}
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
          className={`relative w-full bg-black flex items-center justify-center overflow-hidden ${isFullscreen ? 'h-[calc(100%-40px)]' : 'h-[calc(100%-40px)]'}`}
          onWheel={handleWheel}
          onMouseMove={handleImageMouseMove}
          onMouseUp={handleImageMouseUp}
          onMouseLeave={handleImageMouseUp}
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          style={{ touchAction: 'none' }}
        >
          {streamLoading && !isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <RefreshCw className="w-6 h-6 text-bambu-gray animate-spin" />
            </div>
          )}
          {isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/80 z-10">
              <div className="text-center p-2">
                <WifiOff className="w-6 h-6 text-orange-400 mx-auto mb-2" />
                <p className="text-xs text-bambu-gray">
                  Reconnecting in {reconnectCountdown}s...
                </p>
              </div>
            </div>
          )}
          {streamError && !isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black z-10">
              <div className="text-center p-2">
                <AlertTriangle className="w-6 h-6 text-orange-400 mx-auto mb-2" />
                <p className="text-xs text-bambu-gray mb-2">Camera unavailable</p>
                <button
                  onClick={refresh}
                  className="px-2 py-1 text-xs bg-bambu-green text-white rounded hover:bg-bambu-green/80"
                >
                  Retry
                </button>
              </div>
            </div>
          )}
          <img
            ref={imgRef}
            key={imageKey}
            src={streamUrl}
            alt="Camera stream"
            className="max-w-full max-h-full object-contain select-none"
            style={{
              transform: `scale(${zoomLevel}) translate(${panOffset.x / zoomLevel}px, ${panOffset.y / zoomLevel}px)`,
              cursor: zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'default',
            }}
            onError={handleStreamError}
            onLoad={handleStreamLoad}
            onMouseDown={handleImageMouseDown}
            draggable={false}
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
              title={t('camera.dragToResize')}
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
    </div>
  );
}
