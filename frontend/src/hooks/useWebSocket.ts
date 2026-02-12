import { useEffect, useRef, useCallback, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';

interface WebSocketMessage {
  type: string;
  printer_id?: number;
  data?: Record<string, unknown>;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const queryClient = useQueryClient();
  const [isConnected, setIsConnected] = useState(false);

  // Debounce invalidations to prevent rapid re-render cascades
  const pendingInvalidations = useRef<Set<string>>(new Set());
  const invalidationTimeoutRef = useRef<number | null>(null);

  // Throttle printer status updates to prevent freeze during rapid messages
  const pendingPrinterStatus = useRef<Map<number, Record<string, unknown>>>(new Map());
  const printerStatusTimeoutRef = useRef<number | null>(null);

  // Throttle message processing to prevent browser freeze
  const messageQueueRef = useRef<WebSocketMessage[]>([]);
  const processingRef = useRef(false);

  // Use ref for handleMessage to avoid stale closure in connect
  const handleMessageRef = useRef<(message: WebSocketMessage) => void>(() => {});

  // Process message queue with throttling to prevent UI freeze
  const processMessageQueue = useCallback(() => {
    if (processingRef.current || messageQueueRef.current.length === 0) {
      return;
    }

    processingRef.current = true;

    const processNext = () => {
      const message = messageQueueRef.current.shift();
      if (message) {
        // Use requestAnimationFrame to yield to the browser
        requestAnimationFrame(() => {
          handleMessageRef.current(message);
          // Small delay between messages to prevent overwhelming the browser
          if (messageQueueRef.current.length > 0) {
            setTimeout(processNext, 16); // ~60fps
          } else {
            processingRef.current = false;
          }
        });
      } else {
        processingRef.current = false;
      }
    };

    processNext();
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;

    const ws = new WebSocket(wsUrl);

    let pingInterval: number | null = null;

    ws.onopen = () => {
      if (import.meta.env.MODE !== 'test') console.log('[WebSocket] Connected');
      setIsConnected(true);
      // Start ping interval
      pingInterval = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const message: WebSocketMessage = JSON.parse(event.data);
        // Handle printer_status directly (already throttled) to avoid queue delays
        // This prevents the "timelapse" effect where status updates are applied slowly
        if (message.type === 'printer_status' && message.printer_id !== undefined && message.data) {
          handleMessageRef.current(message);
        } else {
          // Queue other messages for throttled processing
          messageQueueRef.current.push(message);
          processMessageQueue();
        }
      } catch {
        // Ignore parse errors
      }
    };

    ws.onclose = (event) => {
      if (import.meta.env.MODE !== 'test') console.log('[WebSocket] Closed', event.code, event.reason);
      if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
      }
      setIsConnected(false);
      wsRef.current = null;

      // Reconnect after 3 seconds
      reconnectTimeoutRef.current = window.setTimeout(() => {
        connect();
      }, 3000);
    };

    ws.onerror = (error) => {
      if (import.meta.env.MODE !== 'test') console.error('[WebSocket] Error', error);
      ws.close();
    };

    wsRef.current = ws;
  }, [processMessageQueue]);

  // Throttled printer status update - coalesces rapid updates per printer
  const throttledPrinterStatusUpdate = useCallback((printerId: number, data: Record<string, unknown>) => {
    // Merge with any pending data for this printer
    const existing = pendingPrinterStatus.current.get(printerId) || {};
    pendingPrinterStatus.current.set(printerId, { ...existing, ...data });

    // Schedule update if not already scheduled
    if (!printerStatusTimeoutRef.current) {
      printerStatusTimeoutRef.current = window.setTimeout(() => {
        const updates = new Map(pendingPrinterStatus.current);
        pendingPrinterStatus.current.clear();
        printerStatusTimeoutRef.current = null;

        // Apply all pending updates
        requestAnimationFrame(() => {
          updates.forEach((statusData, id) => {
            queryClient.setQueryData(
              ['printerStatus', id],
              (old: Record<string, unknown> | undefined) => {
                const merged = { ...old, ...statusData };
                if (merged.wifi_signal == null && old?.wifi_signal != null) {
                  merged.wifi_signal = old.wifi_signal;
                }
                return merged;
              }
            );
          });
        });
      }, 100); // Update at most every 100ms
    }
  }, [queryClient]);

  // Debounced invalidation helper - coalesces multiple rapid invalidations
  const debouncedInvalidate = useCallback((queryKey: string) => {
    pendingInvalidations.current.add(queryKey);

    // Clear existing timeout
    if (invalidationTimeoutRef.current) {
      clearTimeout(invalidationTimeoutRef.current);
    }

    // Schedule invalidation after a delay (3s to prevent browser freeze on print completion)
    invalidationTimeoutRef.current = window.setTimeout(() => {
      const keys = Array.from(pendingInvalidations.current);
      pendingInvalidations.current.clear();
      invalidationTimeoutRef.current = null;

      // Invalidate queries one at a time with delays to prevent freeze
      let delay = 0;
      keys.forEach((key) => {
        setTimeout(() => {
          requestAnimationFrame(() => {
            queryClient.invalidateQueries({ queryKey: [key] });
          });
        }, delay);
        delay += 500; // 500ms between each invalidation
      });
    }, 3000);
  }, [queryClient]);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'printer_status':
        if (message.printer_id !== undefined && message.data) {
          throttledPrinterStatusUpdate(message.printer_id, message.data);
        }
        break;

      case 'print_start':
        // Refetch printer status immediately when print starts to get printable_objects_count
        if (message.printer_id !== undefined) {
          queryClient.invalidateQueries({ queryKey: ['printerStatus', message.printer_id] });
        }
        break;

      case 'print_complete':
        // Don't invalidate printerStatus here - it causes re-render cascade and browser freeze
        // The printer_status websocket messages will naturally update the status
        debouncedInvalidate('archives');
        debouncedInvalidate('archiveStats');
        break;

      case 'archive_created':
        debouncedInvalidate('archives');
        debouncedInvalidate('archiveStats');
        break;

      case 'archive_updated':
        debouncedInvalidate('archives');
        break;

      case 'pong':
        // Keepalive response, ignore
        break;

      case 'plate_not_empty':
        // Plate detection found objects - print was paused
        // Dispatch event for toast notification
        window.dispatchEvent(new CustomEvent('plate-not-empty', {
          detail: {
            printer_id: message.printer_id,
            printer_name: (message as unknown as { printer_name?: string }).printer_name,
            message: (message as unknown as { message?: string }).message,
          }
        }));
        break;

      case 'spool_auto_assigned':
        // RFID tag matched - refresh inventory data
        debouncedInvalidate('inventory-spools');
        debouncedInvalidate('inventory-assignments');
        break;

      case 'spool_usage_logged':
        // Filament consumption recorded - refresh spool data
        debouncedInvalidate('inventory-spools');
        break;

      case 'unknown_tag':
        // Unknown RFID tag detected - dispatch event for UI
        window.dispatchEvent(new CustomEvent('unknown-tag', {
          detail: {
            printer_id: (message as unknown as { printer_id?: number }).printer_id,
            ams_id: (message as unknown as { ams_id?: number }).ams_id,
            tray_id: (message as unknown as { tray_id?: number }).tray_id,
            tag_uid: (message as unknown as { tag_uid?: string }).tag_uid,
            tray_uuid: (message as unknown as { tray_uuid?: string }).tray_uuid,
          }
        }));
        break;
    }
  }, [queryClient, debouncedInvalidate, throttledPrinterStatusUpdate]);

  // Keep the ref updated with latest handleMessage
  useEffect(() => {
    handleMessageRef.current = handleMessage;
  }, [handleMessage]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (invalidationTimeoutRef.current) {
        clearTimeout(invalidationTimeoutRef.current);
      }
      if (printerStatusTimeoutRef.current) {
        clearTimeout(printerStatusTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    }
  }, []);

  return { isConnected, sendMessage };
}
