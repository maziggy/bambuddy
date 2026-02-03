import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { RefreshCw, AlertTriangle, Camera, Maximize, Minimize, WifiOff, ZoomIn, ZoomOut } from 'lucide-react';
import { api } from '../api/client';

const MAX_RECONNECT_ATTEMPTS = 5;
const INITIAL_RECONNECT_DELAY = 2000; // 2 seconds
const MAX_RECONNECT_DELAY = 30000; // 30 seconds
const STALL_CHECK_INTERVAL = 5000; // Check every 5 seconds

export function CameraPage() {
  const { t } = useTranslation();
  const { printerId } = useParams<{ printerId: string }>();
  const id = parseInt(printerId || '0', 10);

  const [streamMode, setStreamMode] = useState<'stream' | 'snapshot'>('stream');
  const [streamError, setStreamError] = useState(false);
  const [streamLoading, setStreamLoading] = useState(true);
  const [imageKey, setImageKey] = useState(Date.now());
  const [transitioning, setTransitioning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [reconnectCountdown, setReconnectCountdown] = useState(0);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [panOffset, setPanOffset] = useState({ x: 0, y: 0 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [lastTouchDistance, setLastTouchDistance] = useState<number | null>(null);
  const [lastTouchCenter, setLastTouchCenter] = useState<{ x: number; y: number } | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);
  const countdownIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const stallCheckIntervalRef = useRef<NodeJS.Timeout | null>(null);

  // Fetch printer info for the title
  const { data: printer } = useQuery({
    queryKey: ['printer', id],
    queryFn: () => api.getPrinter(id),
    enabled: id > 0,
  });

  // Update document title
  useEffect(() => {
    if (printer) {
      document.title = `${printer.name} - Camera`;
    }
    return () => {
      document.title = 'Bambuddy';
    };
  }, [printer]);

  // Cleanup on unmount - stop the camera stream
  // Track if we've already sent the stop signal to avoid duplicate calls
  const stopSentRef = useRef(false);

  useEffect(() => {
    const stopUrl = `/api/v1/printers/${id}/camera/stop`;
    stopSentRef.current = false;

    const sendStopOnce = () => {
      if (id > 0 && !stopSentRef.current) {
        stopSentRef.current = true;
        navigator.sendBeacon(stopUrl);
      }
    };

    // Handle page unload/close with sendBeacon (more reliable than fetch on unload)
    const handleBeforeUnload = () => {
      sendStopOnce();
    };

    window.addEventListener('beforeunload', handleBeforeUnload);

    // Store ref value for cleanup - ref may change by cleanup time
    const imgElement = imgRef.current;

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);

      // Clear the image source first to stop the stream
      if (imgElement) {
        imgElement.src = '';
      }
      // Send stop signal only once
      sendStopOnce();
    };
  }, [id]);

  // Auto-hide loading after timeout
  useEffect(() => {
    if (streamLoading && !transitioning) {
      const timeout = streamMode === 'stream' ? 3000 : 20000;
      const timer = setTimeout(() => {
        setStreamLoading(false);
      }, timeout);
      return () => clearTimeout(timer);
    }
  }, [streamMode, streamLoading, imageKey, transitioning]);

  // Fullscreen change listener - refresh stream after fullscreen transition
  useEffect(() => {
    const handleFullscreenChange = () => {
      const nowFullscreen = !!document.fullscreenElement;
      setIsFullscreen(nowFullscreen);
      // Reset zoom on fullscreen transition
      setZoomLevel(1);
      setPanOffset({ x: 0, y: 0 });

      // Refresh stream after fullscreen transition to prevent stall
      if (streamMode === 'stream' && !transitioning) {
        // Clear image src first, then set new key after delay
        if (imgRef.current) {
          imgRef.current.src = '';
        }
        setTimeout(() => {
          setStreamLoading(true);
          setImageKey(Date.now());
        }, 200);
      }
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [streamMode, transitioning]);

  // Save window size and position when user resizes or moves
  // Works for both popup windows and standalone camera pages
  useEffect(() => {
    let saveTimeout: NodeJS.Timeout;
    const saveWindowState = () => {
      // Debounce to avoid saving during drag
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

  // Clean up reconnect timers on unmount
  useEffect(() => {
    return () => {
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current);
      }
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current);
      }
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
      }
    };
  }, []);

  // Auto-reconnect logic
  const attemptReconnect = useCallback(() => {
    if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      setIsReconnecting(false);
      setStreamError(true);
      return;
    }

    // Calculate delay with exponential backoff
    const delay = Math.min(
      INITIAL_RECONNECT_DELAY * Math.pow(2, reconnectAttempts),
      MAX_RECONNECT_DELAY
    );

    setIsReconnecting(true);
    setReconnectCountdown(Math.ceil(delay / 1000));

    // Countdown timer
    countdownIntervalRef.current = setInterval(() => {
      setReconnectCountdown((prev) => {
        if (prev <= 1) {
          if (countdownIntervalRef.current) {
            clearInterval(countdownIntervalRef.current);
          }
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    // Reconnect after delay
    reconnectTimerRef.current = setTimeout(() => {
      setReconnectAttempts((prev) => prev + 1);
      setIsReconnecting(false);
      setStreamLoading(true);
      setStreamError(false);
      if (imgRef.current) {
        imgRef.current.src = '';
      }
      setImageKey(Date.now());
    }, delay);
  }, [reconnectAttempts]);

  // Stall detection - periodically check if stream is still receiving frames
  useEffect(() => {
    // Only skip stall check during initial load, reconnecting, or transitioning
    // Continue checking even during streamError to detect recovery
    if (streamMode !== 'stream' || streamLoading || isReconnecting || transitioning) {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
      return;
    }

    // Start stall detection after stream has loaded
    stallCheckIntervalRef.current = setInterval(async () => {
      try {
        const status = await api.getCameraStatus(id);
        // Trigger reconnect if:
        // 1. Backend reports stall (no frames for 10+ seconds)
        // 2. OR stream is not active anymore (process died)
        if (status.stalled || (!status.active && !streamError)) {
          console.log(`Stream issue detected: stalled=${status.stalled}, active=${status.active}, reconnecting...`);
          if (stallCheckIntervalRef.current) {
            clearInterval(stallCheckIntervalRef.current);
            stallCheckIntervalRef.current = null;
          }
          setStreamLoading(false);
          attemptReconnect();
        }
      } catch {
        // Ignore fetch errors - server might be temporarily unavailable
      }
    }, STALL_CHECK_INTERVAL);

    return () => {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
    };
  }, [streamMode, streamLoading, streamError, isReconnecting, transitioning, id, attemptReconnect]);

  const handleStreamError = () => {
    setStreamLoading(false);

    // Only auto-reconnect for live stream mode
    if (streamMode === 'stream' && reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      attemptReconnect();
    } else {
      setStreamError(true);
    }
  };

  const handleStreamLoad = () => {
    setStreamLoading(false);
    setStreamError(false);
    // Reset reconnect attempts on successful connection
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current);
    }

    // Auto-resize window to fit video content (only if no saved preference)
    if (imgRef.current && !localStorage.getItem('cameraWindowState')) {
      const img = imgRef.current;
      const videoWidth = img.naturalWidth;
      const videoHeight = img.naturalHeight;

      if (videoWidth > 0 && videoHeight > 0) {
        // Add space for header bar (~45px) and some padding
        const headerHeight = 45;
        const padding = 16;

        // Calculate window size (outer size includes chrome)
        const chromeWidth = window.outerWidth - window.innerWidth;
        const chromeHeight = window.outerHeight - window.innerHeight;

        const targetWidth = videoWidth + padding + chromeWidth;
        const targetHeight = videoHeight + headerHeight + padding + chromeHeight;

        try {
          window.resizeTo(targetWidth, targetHeight);
        } catch {
          // resizeTo may not be allowed in all contexts
        }
      }
    }
  };

  const stopStream = () => {
    if (id > 0) {
      fetch(`/api/v1/printers/${id}/camera/stop`).catch(() => {});
    }
  };

  const switchToMode = (newMode: 'stream' | 'snapshot') => {
    if (streamMode === newMode || transitioning) return;
    setTransitioning(true);
    setStreamLoading(true);
    setStreamError(false);
    // Reset reconnect state on mode switch
    setReconnectAttempts(0);
    setIsReconnecting(false);
    // Reset zoom on mode switch
    setZoomLevel(1);
    setPanOffset({ x: 0, y: 0 });
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current);
    }

    if (imgRef.current) {
      imgRef.current.src = '';
    }

    // Stop any active streams when switching modes
    if (streamMode === 'stream') {
      stopStream();
    }

    setTimeout(() => {
      setStreamMode(newMode);
      setImageKey(Date.now());
      setTransitioning(false);
    }, 100);
  };

  const refresh = () => {
    if (transitioning) return;
    setTransitioning(true);
    setStreamLoading(true);
    setStreamError(false);
    // Reset reconnect state on manual refresh
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
    }
    if (countdownIntervalRef.current) {
      clearInterval(countdownIntervalRef.current);
    }

    if (imgRef.current) {
      imgRef.current.src = '';
    }

    // Stop any active streams before refresh
    if (streamMode === 'stream') {
      stopStream();
    }

    setTimeout(() => {
      setImageKey(Date.now());
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
    if (!containerRef.current) {
      return { x: 300, y: 200 };
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

  const currentUrl = transitioning
    ? ''
    : streamMode === 'stream'
      ? `/api/v1/printers/${id}/camera/stream?fps=15&t=${imageKey}`
      : `/api/v1/printers/${id}/camera/snapshot?t=${imageKey}`;

  const isDisabled = streamLoading || transitioning || isReconnecting;

  if (!id) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <p className="text-white">{t('camera.invalidPrinterId')}</p>
      </div>
    );
  }

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
        onMouseMove={handleImageMouseMove}
        onMouseUp={handleImageMouseUp}
        onMouseLeave={handleImageMouseUp}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{ touchAction: 'none' }}
      >
        <div className="relative w-full h-full flex items-center justify-center">
          {(streamLoading || transitioning) && !isReconnecting && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <div className="text-center">
                <RefreshCw className="w-8 h-8 text-bambu-gray animate-spin mx-auto mb-2" />
                <p className="text-sm text-bambu-gray">
                  {streamMode === 'stream' ? t('camera.connectingToCamera') : t('camera.capturingSnapshot')}
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
          {streamError && !isReconnecting && (
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
          <img
            ref={imgRef}
            key={imageKey}
            src={currentUrl}
            alt={t('camera.cameraStream')}
            className="max-w-full max-h-full object-contain select-none"
            style={{
              transform: `scale(${zoomLevel}) translate(${panOffset.x / zoomLevel}px, ${panOffset.y / zoomLevel}px)`,
              cursor: zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'default',
            }}
            onError={currentUrl ? handleStreamError : undefined}
            onLoad={currentUrl ? handleStreamLoad : undefined}
            onMouseDown={handleImageMouseDown}
            draggable={false}
          />

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
    </div>
  );
}
