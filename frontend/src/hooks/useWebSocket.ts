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

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws`;

    const ws = new WebSocket(wsUrl);

    let pingInterval: number | null = null;

    ws.onopen = () => {
      console.log('[WebSocket] Connected');
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
        handleMessage(message);
      } catch {
        // Ignore parse errors
      }
    };

    ws.onclose = (event) => {
      console.log('[WebSocket] Closed', event.code, event.reason);
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
      console.error('[WebSocket] Error', error);
      ws.close();
    };

    wsRef.current = ws;
  }, []);

  const handleMessage = useCallback((message: WebSocketMessage) => {
    switch (message.type) {
      case 'printer_status':
        // Update the printer status in the query cache
        if (message.printer_id !== undefined) {
          queryClient.setQueryData(
            ['printerStatus', message.printer_id],
            (old: Record<string, unknown> | undefined) => ({
              ...old,
              ...message.data,
            })
          );
        }
        break;

      case 'print_complete':
        // Invalidate archives to refresh the list
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        queryClient.invalidateQueries({ queryKey: ['archiveStats'] });
        break;

      case 'archive_created':
        // Invalidate archives to show new archive
        queryClient.invalidateQueries({ queryKey: ['archives'] });
        queryClient.invalidateQueries({ queryKey: ['archiveStats'] });
        break;

      case 'pong':
        // Keepalive response, ignore
        break;
    }
  }, [queryClient]);

  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
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
