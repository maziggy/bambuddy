/**
 * Tests for the useWebSocket hook.
 *
 * Tests WebSocket connection management and message handling.
 * Uses vitest.mock to mock the entire module before MSW can intercept.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// Track WebSocket instances created during tests
let wsInstances: MockWebSocket[] = [];
let originalWebSocket: typeof WebSocket;

// Enhanced MockWebSocket that tracks instances
class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  url: string;
  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
  }

  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) {
      this.onclose(new CloseEvent('close'));
    }
  });

  // Required by MSW's interceptor - these are no-ops but prevent the error
  addEventListener = vi.fn();
  removeEventListener = vi.fn();

  // Helper to simulate connection opening
  open() {
    this.readyState = MockWebSocket.OPEN;
    if (this.onopen) {
      this.onopen(new Event('open'));
    }
  }

  // Helper to simulate receiving a message
  simulateMessage(data: unknown) {
    if (this.onmessage) {
      this.onmessage(
        new MessageEvent('message', {
          data: JSON.stringify(data),
        })
      );
    }
  }
}

// Create test QueryClient
function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });
}

// Wrapper with QueryClient for hook testing
function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(
      QueryClientProvider,
      { client: queryClient },
      children
    );
  };
}

function getLatestWs(): MockWebSocket | undefined {
  return wsInstances[wsInstances.length - 1];
}

describe('useWebSocket hook', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    wsInstances = [];
    queryClient = createTestQueryClient();
    // Save original and install mock
    originalWebSocket = globalThis.WebSocket;
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    // Restore original WebSocket
    globalThis.WebSocket = originalWebSocket;
  });

  describe('WebSocket Mock', () => {
    it('creates WebSocket with correct URL', () => {
      const ws = new MockWebSocket('ws://test.local/ws');
      expect(ws.url).toBe('ws://test.local/ws');
    });

    it('starts in CONNECTING state', () => {
      const ws = new MockWebSocket('ws://test.local/ws');
      expect(ws.readyState).toBe(MockWebSocket.CONNECTING);
    });

    it('transitions to OPEN state', () => {
      const ws = new MockWebSocket('ws://test.local/ws');
      const onOpen = vi.fn();
      ws.onopen = onOpen;

      ws.open();

      expect(ws.readyState).toBe(MockWebSocket.OPEN);
      expect(onOpen).toHaveBeenCalled();
    });

    it('can receive messages', () => {
      const ws = new MockWebSocket('ws://test.local/ws');
      const onMessage = vi.fn();
      ws.onmessage = onMessage;

      ws.open();
      ws.simulateMessage({ type: 'status', data: { connected: true } });

      expect(onMessage).toHaveBeenCalled();
    });

    it('can close connection', () => {
      const ws = new MockWebSocket('ws://test.local/ws');
      const onClose = vi.fn();
      ws.onclose = onClose;

      ws.close();

      expect(ws.readyState).toBe(MockWebSocket.CLOSED);
      expect(onClose).toHaveBeenCalled();
    });

    it('tracks all instances', () => {
      wsInstances = [];
      new MockWebSocket('ws://a');
      new MockWebSocket('ws://b');
      expect(wsInstances.length).toBe(2);
    });
  });

  describe('hook connection', () => {
    it('connects to WebSocket on mount', async () => {
      // Reset module cache to get fresh import with our mock
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs();
      expect(ws).toBeDefined();
      expect(ws?.url).toContain('/api/v1/ws');
    });

    it('reports connected state when WebSocket opens', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      // Initially not connected
      expect(result.current.isConnected).toBe(false);

      // Simulate connection opening
      const ws = getLatestWs();
      act(() => {
        ws?.open();
      });

      await waitFor(() => {
        expect(result.current.isConnected).toBe(true);
      });
    });
  });

  describe('message handling', () => {
    it('updates printer status in query cache on printer_status message', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate printer status message
      act(() => {
        ws.simulateMessage({
          type: 'printer_status',
          printer_id: 1,
          data: { state: 'IDLE', progress: 0 },
        });
      });

      // Check query cache was updated
      const cachedData = queryClient.getQueryData(['printerStatus', 1]);
      expect(cachedData).toEqual({ state: 'IDLE', progress: 0 });
    });

    it('preserves wifi_signal when new value is null', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      // Pre-populate cache with wifi_signal
      queryClient.setQueryData(['printerStatus', 1], {
        wifi_signal: -65,
        state: 'IDLE',
      });

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate status update with null wifi_signal
      act(() => {
        ws.simulateMessage({
          type: 'printer_status',
          printer_id: 1,
          data: { state: 'RUNNING', wifi_signal: null },
        });
      });

      const cachedData = queryClient.getQueryData(['printerStatus', 1]) as Record<
        string,
        unknown
      >;
      expect(cachedData.wifi_signal).toBe(-65); // Preserved
      expect(cachedData.state).toBe('RUNNING'); // Updated
    });

    it('invalidates archives on print_complete message', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate print complete
      act(() => {
        ws.simulateMessage({
          type: 'print_complete',
          printer_id: 1,
          data: { status: 'completed' },
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archiveStats'] });
    });

    it('invalidates archives on archive_created message', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate archive created
      act(() => {
        ws.simulateMessage({
          type: 'archive_created',
          data: { id: 1, filename: 'test.3mf' },
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archiveStats'] });
    });

    it('invalidates archives on archive_updated message', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate archive updated (e.g., timelapse attached)
      act(() => {
        ws.simulateMessage({
          type: 'archive_updated',
          data: { id: 1, timelapse_attached: true },
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
    });

    it('ignores pong messages without error', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate pong response
      act(() => {
        ws.simulateMessage({
          type: 'pong',
        });
      });

      // Should not invalidate any queries for pong
      expect(invalidateSpy).not.toHaveBeenCalled();
    });

    it('handles malformed JSON gracefully', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate malformed message (should not throw)
      expect(() => {
        act(() => {
          if (ws.onmessage) {
            ws.onmessage(
              new MessageEvent('message', {
                data: 'not valid json{{{',
              })
            );
          }
        });
      }).not.toThrow();
    });

    it('handles unknown message types gracefully', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      // Simulate unknown message type
      expect(() => {
        act(() => {
          ws.simulateMessage({
            type: 'unknown_type',
            data: { foo: 'bar' },
          });
        });
      }).not.toThrow();

      expect(invalidateSpy).not.toHaveBeenCalled();
    });
  });

  describe('sendMessage', () => {
    it('sends JSON message when connected', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      act(() => {
        result.current.sendMessage({ type: 'test', data: 'hello' });
      });

      expect(ws.send).toHaveBeenCalledWith(
        JSON.stringify({ type: 'test', data: 'hello' })
      );
    });

    it('does not send when disconnected', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Don't open connection - still in CONNECTING state

      act(() => {
        result.current.sendMessage({ type: 'test' });
      });

      expect(ws.send).not.toHaveBeenCalled();
    });
  });

  describe('reconnection', () => {
    it('reconnects after connection closes', async () => {
      vi.useFakeTimers();
      vi.resetModules();

      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const firstWs = getLatestWs()!;

      // Open connection
      act(() => {
        firstWs.open();
      });

      const instanceCountBefore = wsInstances.length;

      // Close connection
      act(() => {
        firstWs.close();
      });

      // Wait for reconnect timeout (3 seconds)
      act(() => {
        vi.advanceTimersByTime(3000);
      });

      // Should have created new WebSocket
      expect(wsInstances.length).toBe(instanceCountBefore + 1);
      expect(getLatestWs()).not.toBe(firstWs);

      vi.useRealTimers();
    });

    it('cleans up on unmount', async () => {
      vi.resetModules();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { unmount } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = getLatestWs()!;

      // Open connection
      act(() => {
        ws.open();
      });

      unmount();

      expect(ws.close).toHaveBeenCalled();
    });
  });
});
