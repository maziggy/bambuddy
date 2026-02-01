import { useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Layers, Clock, Timer, Printer } from 'lucide-react';
import { api } from '../api/client';
import type { PrinterStatus } from '../api/client';

type OverlaySize = 'small' | 'medium' | 'large';

interface OverlayConfig {
  size: OverlaySize;
  showProgress: boolean;
  showLayers: boolean;
  showEta: boolean;
  showFilename: boolean;
  showStatus: boolean;
  showPrinter: boolean;
}

function parseConfig(params: URLSearchParams): OverlayConfig {
  const show = params.get('show')?.split(',') || ['progress', 'layers', 'eta', 'filename', 'status'];

  return {
    size: (params.get('size') as OverlaySize) || 'medium',
    showProgress: show.includes('progress'),
    showLayers: show.includes('layers'),
    showEta: show.includes('eta'),
    showFilename: show.includes('filename'),
    showStatus: show.includes('status'),
    showPrinter: show.includes('printer'),
  };
}

function formatTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function formatETA(remainingMinutes: number): string {
  const now = new Date();
  const eta = new Date(now.getTime() + remainingMinutes * 60 * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const etaDay = new Date(eta);
  etaDay.setHours(0, 0, 0, 0);

  const timeStr = eta.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  if (etaDay.getTime() === today.getTime()) {
    return timeStr;
  } else if (etaDay.getTime() === today.getTime() + 86400000) {
    return `Tomorrow ${timeStr}`;
  } else {
    return eta.toLocaleDateString([], { weekday: 'short' }) + ' ' + timeStr;
  }
}

function getStatusText(status: PrinterStatus): string {
  if (status.stg_cur_name) return status.stg_cur_name;

  switch (status.state) {
    case 'RUNNING': return 'Printing';
    case 'PAUSE': return 'Paused';
    case 'FINISH': return 'Finished';
    case 'FAILED': return 'Failed';
    case 'IDLE': return 'Idle';
    default: return status.state || 'Unknown';
  }
}

function getSizeClasses(size: OverlaySize) {
  switch (size) {
    case 'small':
      return {
        container: 'p-3',
        text: 'text-sm',
        textLarge: 'text-lg',
        progressHeight: 'h-2',
        icon: 'w-3 h-3',
        gap: 'gap-2',
        logoHeight: 'h-12',
      };
    case 'large':
      return {
        container: 'p-6',
        text: 'text-xl',
        textLarge: 'text-3xl',
        progressHeight: 'h-4',
        icon: 'w-6 h-6',
        gap: 'gap-4',
        logoHeight: 'h-24',
      };
    case 'medium':
    default:
      return {
        container: 'p-4',
        text: 'text-base',
        textLarge: 'text-xl',
        progressHeight: 'h-3',
        icon: 'w-4 h-4',
        gap: 'gap-3',
        logoHeight: 'h-16',
      };
  }
}

export function StreamOverlayPage() {
  const { printerId } = useParams<{ printerId: string }>();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const id = parseInt(printerId || '0', 10);
  const [imageKey, setImageKey] = useState(Date.now());

  const config = useMemo(() => parseConfig(searchParams), [searchParams]);
  const sizes = getSizeClasses(config.size);

  // Fetch printer info
  const { data: printer } = useQuery({
    queryKey: ['printer', id],
    queryFn: () => api.getPrinter(id),
    enabled: id > 0,
  });

  // Fetch printer status with polling
  const { data: status } = useQuery({
    queryKey: ['printerStatus', id],
    queryFn: () => api.getPrinterStatus(id),
    enabled: id > 0,
    refetchInterval: 2000,
  });

  // WebSocket for real-time updates
  useEffect(() => {
    if (!id) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;
    const ws = new WebSocket(wsUrl);

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'printer_status' && data.printer_id === id) {
          queryClient.setQueryData(['printerStatus', id], data.status);
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onerror = () => {
      // WebSocket error - polling will continue as fallback
    };

    return () => {
      ws.close();
    };
  }, [id, queryClient]);

  // Update document title
  useEffect(() => {
    document.title = printer ? `${printer.name} - Stream Overlay` : 'Stream Overlay';
    return () => {
      document.title = 'Bambuddy';
    };
  }, [printer]);

  // Refresh stream on error
  const handleStreamError = () => {
    setTimeout(() => {
      setImageKey(Date.now());
    }, 3000);
  };

  if (!id) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <p className="text-white">Invalid printer ID</p>
      </div>
    );
  }

  if (!status) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <p className="text-gray-400">Loading...</p>
      </div>
    );
  }

  const isPrinting = status.state === 'RUNNING' || status.state === 'PAUSE';
  const progress = status.progress || 0;
  const streamUrl = `/api/v1/printers/${id}/camera/stream?fps=10&t=${imageKey}`;

  return (
    <div className="min-h-screen bg-black relative overflow-hidden">
      {/* Camera feed - fullscreen background */}
      <img
        key={imageKey}
        src={streamUrl}
        alt="Camera stream"
        className="absolute inset-0 w-full h-full object-contain"
        onError={handleStreamError}
      />

      {/* Bambuddy logo - top right */}
      <a
        href="https://github.com/maziggy/bambuddy"
        target="_blank"
        rel="noopener noreferrer"
        className="absolute top-4 right-4 z-10"
      >
        <img
          src="/img/bambuddy_logo_dark_transparent.png"
          alt="Bambuddy"
          className={`${sizes.logoHeight} object-contain drop-shadow-lg hover:scale-105 transition-transform`}
        />
      </a>

      {/* Status overlay - bottom */}
      <div className="absolute bottom-0 left-0 right-0 z-10 bg-gradient-to-t from-black/80 via-black/60 to-transparent">
        <div className={`${sizes.container}`}>
          {/* Printer name */}
          {config.showPrinter && printer && (
            <div className={`flex items-center ${sizes.gap} mb-2`}>
              <Printer className={`${sizes.icon} text-white/70`} />
              <span className={`${sizes.text} text-white font-medium`}>{printer.name}</span>
            </div>
          )}

          {/* Filename */}
          {config.showFilename && status.current_print && (
            <div className={`${sizes.textLarge} text-white font-semibold mb-2 truncate drop-shadow-md`}>
              {status.current_print.replace(/\.gcode\.3mf$|\.3mf$|\.gcode$/i, '')}
            </div>
          )}

          {/* Status text */}
          {config.showStatus && (
            <div className={`${sizes.text} text-white/70 mb-2`}>
              {getStatusText(status)}
            </div>
          )}

          {/* Progress bar */}
          {config.showProgress && isPrinting && (
            <div className="mb-3">
              <div className={`flex items-center justify-between mb-1 ${sizes.text}`}>
                <span className="text-white/70">Progress</span>
                <span className="text-white font-bold">{Math.round(progress)}%</span>
              </div>
              <div className={`w-full bg-white/20 rounded-full ${sizes.progressHeight}`}>
                <div
                  className={`bg-bambu-green ${sizes.progressHeight} rounded-full transition-all duration-500`}
                  style={{ width: `${progress}%` }}
                />
              </div>
            </div>
          )}

          {/* Stats row */}
          {isPrinting && (config.showLayers || config.showEta) && (
            <div className={`flex items-center ${sizes.gap} flex-wrap`}>
              {/* Layers */}
              {config.showLayers && status.layer_num != null && status.total_layers != null && status.total_layers > 0 && (
                <div className={`flex items-center ${sizes.gap} text-white/70`}>
                  <Layers className={sizes.icon} />
                  <span className={sizes.text}>
                    <span className="text-white">{status.layer_num}</span>
                    <span className="mx-1">/</span>
                    <span>{status.total_layers}</span>
                  </span>
                </div>
              )}

              {/* Remaining time */}
              {config.showEta && status.remaining_time != null && status.remaining_time > 0 && (
                <>
                  <div className={`flex items-center ${sizes.gap} text-white/70`}>
                    <Timer className={sizes.icon} />
                    <span className={`${sizes.text} text-white`}>
                      {formatTime(status.remaining_time * 60)}
                    </span>
                  </div>

                  <div className={`flex items-center ${sizes.gap} text-white/70`}>
                    <Clock className={sizes.icon} />
                    <span className={`${sizes.text} text-white`}>
                      ETA {formatETA(status.remaining_time)}
                    </span>
                  </div>
                </>
              )}
            </div>
          )}

          {/* Idle state */}
          {!isPrinting && (
            <div className={`${sizes.text} text-white/70 py-2`}>
              {status.connected ? 'Printer is idle' : 'Printer offline'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
