import { useState, useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { RefreshCw, AlertTriangle, Camera, Maximize, Minimize } from 'lucide-react';
import { api } from '../api/client';

export function CameraPage() {
  const { printerId } = useParams<{ printerId: string }>();
  const id = parseInt(printerId || '0', 10);

  const [streamMode, setStreamMode] = useState<'stream' | 'snapshot'>('stream');
  const [streamError, setStreamError] = useState(false);
  const [streamLoading, setStreamLoading] = useState(true);
  const [imageKey, setImageKey] = useState(Date.now());
  const [transitioning, setTransitioning] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

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

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (imgRef.current) {
        imgRef.current.src = '';
      }
    };
  }, []);

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

  // Fullscreen change listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  const handleStreamError = () => {
    setStreamError(true);
    setStreamLoading(false);
  };

  const handleStreamLoad = () => {
    setStreamLoading(false);
    setStreamError(false);
  };

  const switchToMode = (newMode: 'stream' | 'snapshot') => {
    if (streamMode === newMode || transitioning) return;
    setTransitioning(true);
    setStreamLoading(true);
    setStreamError(false);

    if (imgRef.current) {
      imgRef.current.src = '';
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

    if (imgRef.current) {
      imgRef.current.src = '';
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

  const currentUrl = transitioning
    ? ''
    : streamMode === 'stream'
      ? `/api/v1/printers/${id}/camera/stream?fps=10&t=${imageKey}`
      : `/api/v1/printers/${id}/camera/snapshot?t=${imageKey}`;

  const isDisabled = streamLoading || transitioning;

  if (!id) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <p className="text-white">Invalid printer ID</p>
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
              Live
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
              Snapshot
            </button>
          </div>
          <button
            onClick={refresh}
            disabled={isDisabled}
            className="p-1.5 hover:bg-bambu-dark-tertiary rounded disabled:opacity-50"
            title={streamMode === 'stream' ? 'Restart stream' : 'Refresh snapshot'}
          >
            <RefreshCw className={`w-4 h-4 text-bambu-gray ${isDisabled ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={toggleFullscreen}
            className="p-1.5 hover:bg-bambu-dark-tertiary rounded"
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
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
      <div className="flex-1 flex items-center justify-center p-2">
        <div className="relative w-full h-full flex items-center justify-center">
          {(streamLoading || transitioning) && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <div className="text-center">
                <RefreshCw className="w-8 h-8 text-bambu-gray animate-spin mx-auto mb-2" />
                <p className="text-sm text-bambu-gray">
                  {streamMode === 'stream' ? 'Connecting to camera...' : 'Capturing snapshot...'}
                </p>
              </div>
            </div>
          )}
          {streamError && (
            <div className="absolute inset-0 flex items-center justify-center bg-black z-10">
              <div className="text-center p-4">
                <AlertTriangle className="w-12 h-12 text-orange-400 mx-auto mb-3" />
                <p className="text-white mb-2">Camera unavailable</p>
                <p className="text-xs text-bambu-gray mb-4 max-w-md">
                  Make sure the printer is powered on and connected.
                </p>
                <button
                  onClick={refresh}
                  className="px-4 py-2 bg-bambu-green text-white rounded hover:bg-bambu-green/80 transition-colors"
                >
                  Retry
                </button>
              </div>
            </div>
          )}
          <img
            ref={imgRef}
            key={imageKey}
            src={currentUrl}
            alt="Camera stream"
            className="max-w-full max-h-full object-contain"
            onError={currentUrl ? handleStreamError : undefined}
            onLoad={currentUrl ? handleStreamLoad : undefined}
          />
        </div>
      </div>
    </div>
  );
}
