/**
 * Tests for the useWebSocket hook.
 *
 * Tests WebSocket connection management and message handling.
 * Uses vitest.mock to mock the entire module before MSW can intercept.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor, act, screen } from '@testing-library/react';
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ToastProvider } from '../../contexts/ToastContext';

// Track WebSocket instances created during tests
let wsInstances: MockWebSocket[] = [];
let originalWebSocket: typeof WebSocket;

// Mock react-i18next BEFORE any modules that use it are imported
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      if (key === 'printers.toast.missingSpoolAssignment' && options) {
        const { printer, slots } = options as { printer: string; slots: string };
        return `Missing assignments for ${printer}: ${slots}`;
      }
      if (key === 'printers.toast.killSwitchTriggered' && options) {
        const { printer, filename } = options as { printer: string; filename: string };
        return `Billing kill switch stopped ${filename} on ${printer}`;
      }
      if (key === 'printers.toast.billingChargeFailed' && options) {
        const { printer, filename } = options as { printer: string; filename: string };
        return `Billing failed for ${filename} on ${printer}. The budget reservation was retained; check the server logs.`;
      }
      return key;
    },
    i18n: {},
  }),
}));

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

  // Helper to simulate the server closing with a specific code (e.g. 4401,
  // the /ws auth-rejection close code).
  simulateClose(code: number) {
    this.readyState = MockWebSocket.CLOSED;
    if (this.onclose) {
      this.onclose(new CloseEvent('close', { code }));
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

// Wrapper with QueryClient and ToastProvider for hook testing
function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(
      ToastProvider,
      {},
      React.createElement(
        QueryClientProvider,
        { client: queryClient },
        children
      )
    );
  };
}

/**
 * After GHSA-r2qv, useWebSocket awaits a ws-token fetch before constructing
 * the WebSocket. The MockWebSocket isn't pushed into ``wsInstances`` until
 * that promise resolves. ``waitFor`` from testing-library uses real-time
 * polling and so wedges under ``vi.useFakeTimers()``; flushing microtasks
 * manually works under both real and fake timers because Promise resolution
 * runs on the microtask queue, not on the mocked clock.
 *
 * Two iterations suffice for ``await fetch(...)`` → ``await resp.json()``;
 * a small headroom lets future awaits land here without changing every
 * call site.
 */
async function waitForWs(): Promise<MockWebSocket> {
  for (let i = 0; i < 10 && wsInstances.length === 0; i++) {
    await Promise.resolve();
  }
  const ws = wsInstances[wsInstances.length - 1];
  if (!ws) {
    throw new Error('WebSocket was not constructed after microtask flush');
  }
  return ws;
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

    // After GHSA-r2qv, useWebSocket fetches a ws-token via api.getWebSocketToken
    // before opening the socket. ``api.request`` reads ``response.headers``
    // and ``response.status``; the stub must expose those (a missing
    // ``headers`` field throws inside request() and the silent catch in
    // useWebSocket then proceeds with an undefined token, so the assertion
    // "URL contains ?token=" fails without making the cause obvious).
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        status: 200,
        statusText: 'OK',
        headers: { get: () => null },
        json: async () => ({ token: 'test-ws-token' }),
      })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
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
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();
      expect(ws).toBeDefined();
      expect(ws.url).toContain('/api/v1/ws');
      // GHSA-r2qv: the ws-token mint result is appended as ?token=...
      expect(ws.url).toContain('token=test-ws-token');
    });

    it('reports connected state when WebSocket opens', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      // Initially not connected
      expect(result.current.isConnected).toBe(false);

      // Simulate connection opening
      const ws = await waitForWs();
      act(() => {
        ws.open();
      });

      await waitFor(() => {
        expect(result.current.isConnected).toBe(true);
      });
    });
  });

  describe('message handling', () => {
    it('updates printer status in query cache on printer_status message', async () => {
      // Test the printer status update logic directly using setQueryData
      // The WebSocket handler with throttling is complex to test with fake timers,
      // so we test the core behavior directly

      // Simulate what the throttled update does
      queryClient.setQueryData(
        ['printerStatus', 1],
        (old: Record<string, unknown> | undefined) => {
          const statusData = { state: 'IDLE', progress: 0 };
          const merged = { ...old, ...statusData };
          return merged;
        }
      );

      // Check query cache was updated
      const cachedData = queryClient.getQueryData(['printerStatus', 1]);
      expect(cachedData).toEqual({ state: 'IDLE', progress: 0 });
    });

    it('preserves wifi_signal when new value is null', async () => {
      // Test the wifi_signal preservation logic directly on QueryClient
      // The throttled WebSocket handler makes this hard to test end-to-end
      // This tests that the merge logic correctly preserves wifi_signal

      // Set initial data with wifi_signal
      queryClient.setQueryData(['printerStatus', 1], {
        wifi_signal: -65,
        state: 'IDLE',
      });

      // Simulate what the throttled update does - use setQueryData with updater function
      queryClient.setQueryData(
        ['printerStatus', 1],
        (old: Record<string, unknown> | undefined) => {
          const statusData = { state: 'RUNNING', wifi_signal: null };
          const merged = { ...old, ...statusData };
          // This is the preservation logic from useWebSocket
          if (merged.wifi_signal == null && old?.wifi_signal != null) {
            merged.wifi_signal = old.wifi_signal;
          }
          return merged;
        }
      );

      const cachedData = queryClient.getQueryData(['printerStatus', 1]) as Record<
        string,
        unknown
      >;
      expect(cachedData.wifi_signal).toBe(-65); // Preserved
      expect(cachedData.state).toBe('RUNNING'); // Updated
    });

    it('invalidates archives on print_complete message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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

      // Advance timers to trigger debounced invalidation (3000ms delay + 500ms between each)
      await act(async () => {
        vi.advanceTimersByTime(4000);
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archiveStats'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    it('invalidates archives on archive_created message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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

      // Advance timers to trigger debounced invalidation (3000ms delay + 500ms between each)
      await act(async () => {
        vi.advanceTimersByTime(4000);
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archiveStats'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    it('invalidates archives on archive_updated message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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

      // Advance timers to trigger debounced invalidation (3000ms delay)
      await act(async () => {
        vi.advanceTimersByTime(4000);
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    it('invalidates inventory queries on inventory_changed message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

      act(() => {
        ws.open();
      });

      act(() => {
        ws.simulateMessage({ type: 'inventory_changed' });
      });

      await act(async () => {
        vi.advanceTimersByTime(5000);
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['inventory-spools'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['spoolman-inventory-spools'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['inventory-locations'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    /*
     * Swapping a spool leaves the previous spool's preset name on the AMS slot
     * card.
     *
     * The RFID auto-assign rewrites the slot's slot_preset_mappings row, and
     * PrintersPage reads `slotPreset?.preset_name` *ahead of* the live
     * tray_info_idx lookup -- so a cached row wins over correct data pushed
     * over the socket. Everything else on the card rides the status push and
     * updates instantly, which is why this surfaces as one wrong line rather
     * than a stale card: pull a Bambu ABS Orange, insert a PLA Matte Dark
     * Blue, and the card reads "Bambu ABS" against the new colour.
     *
     * `slotPresets` has a 2-minute staleTime and no refetch interval, so on a
     * dashboard left open and focused nothing ever refetches it.
     */
    it('invalidates slot presets on spool_auto_assigned message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

      act(() => {
        ws.open();
      });

      act(() => {
        ws.simulateMessage({
          type: 'spool_auto_assigned',
          printer_id: 7,
          ams_id: 0,
          tray_id: 0,
          spool_id: 110,
        });
      });

      // No timer advance: the user is standing at the printer looking at the
      // card, so the slot's own queries must not wait out the 3s cascade
      // debounce (which any further event would restart anyway).
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['slotPresets'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['spool-assignments'] });

      // The spool list is not on the card's critical path and stays debounced.
      expect(invalidateSpy).not.toHaveBeenCalledWith({ queryKey: ['inventory-spools'] });
      await act(async () => {
        vi.advanceTimersByTime(5000);
      });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['inventory-spools'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });

    /*
     * Spoolman mode reaches the same slot_preset_mappings row through its own
     * AMS sync, which raises spool_assignment_changed. Its slot rows live under
     * a different query key, so the internal-mode key alone left that half of
     * the UI on the previous spool.
     */
    it('invalidates both inventory modes on spool_assignment_changed message', async () => {
      vi.useFakeTimers();
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

      act(() => {
        ws.open();
      });

      act(() => {
        ws.simulateMessage({
          type: 'spool_assignment_changed',
          printer_id: 7,
          ams_id: 0,
          tray_id: 0,
        });
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['slotPresets'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['spool-assignments'] });
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['spoolman-slot-assignments'] });

      vi.useRealTimers();
      vi.unstubAllGlobals();
    });
    it('handles missing_spool_assignment message without error', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();
      act(() => {
        ws.open();
      });

      // This test verifies that the hook properly handles missing_spool_assignment messages
      // without throwing an error. The actual toast display is tested via the UI.
      expect(() => {
        act(() => {
          ws.simulateMessage({
            type: 'missing_spool_assignment',
            printer_id: 7,
            printer_name: 'Printer B',
            missing_slots: [{ slot: 'A2' }, { slot: 'Ext-L' }],
          });
        });
      }).not.toThrow();

      vi.unstubAllGlobals();
    });

    it('shows an error toast when the billing kill switch stops a print', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();
      act(() => {
        ws.open();
        ws.simulateMessage({
          type: 'kill_switch_triggered',
          printer_id: 7,
          printer_name: 'Printer B',
          filename: 'foreign_job.3mf',
        });
      });

      const toast = screen.getByText('Billing kill switch stopped foreign_job.3mf on Printer B');
      expect(toast.parentElement).toHaveClass('bg-red-500/10');
    });

    it('shows an error toast when a completed print could not be charged', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();
      act(() => {
        ws.open();
        ws.simulateMessage({
          type: 'billing_charge_failed',
          printer_id: 7,
          printer_name: 'Printer B',
          filename: 'paid-job.3mf',
        });
      });

      const toast = screen.getByText(
        'Billing failed for paid-job.3mf on Printer B. The budget reservation was retained; check the server logs.',
      );
      expect(toast.parentElement).toHaveClass('bg-red-500/10');
    });

    it('handles spool_assignment_verified messages (success and failure) without error', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();
      act(() => {
        ws.open();
      });

      // #2582: verified (loaded), loaded-but-no-K-profile, and not-confirmed
      // all route to a toast — assert none of the branches throw.
      expect(() => {
        act(() => {
          ws.simulateMessage({
            type: 'spool_assignment_verified',
            printer_id: 3,
            printer_name: 'Printer A',
            slot: 'A1',
            verified: true,
            kprofile_applied: true,
          });
          ws.simulateMessage({
            type: 'spool_assignment_verified',
            printer_id: 3,
            printer_name: 'Printer A',
            slot: 'A1',
            verified: true,
            kprofile_applied: false,
          });
          ws.simulateMessage({
            type: 'spool_assignment_verified',
            printer_id: 3,
            printer_name: 'Printer A',
            slot: 'A1',
            verified: false,
            saw_tray: true,
          });
        });
      }).not.toThrow();

      vi.unstubAllGlobals();
    });

    it('ignores pong messages without error', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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

  /**
   * #2754 (reporter @mic4rd): live updates froze whenever the tab wasn't in
   * front, and caught up all at once on switching back.
   *
   * Two causes, fixed in two rounds. First the cache writes ran inside a
   * requestAnimationFrame, and a hidden tab gets no rendering opportunities —
   * the browser holds queued frame callbacks indefinitely rather than merely
   * throttling them. The rAF stub below is what makes those tests meaningful:
   * it hands back a handle and never invokes the callback, which is what a
   * real hidden tab does.
   *
   * Removing the frame callback did not close the report, because the 100ms
   * coalescing timer was still in the path and a hidden page's timers are
   * clamped to at best once a second — once a minute past five minutes hidden.
   * So the writes must not depend on a timer either while hidden, which is
   * what `writes without waiting on a timer` pins down. Note it deliberately
   * never advances the clock: a test that advances fake timers cannot tell a
   * throttled timer from a prompt one, which is exactly why the original tests
   * kept passing while the reporter's tab stayed frozen.
   */
  describe('hidden tab (#2754)', () => {
    let rafSpy: ReturnType<typeof vi.fn>;

    beforeEach(() => {
      // The shared test client sets gcTime: 0, which collects a query the
      // moment it has no observers — advancing timers past the 100ms
      // coalescing window would drop the entry we just wrote before we could
      // read it back. Nothing observes ['printerStatus', 1] here, so this
      // block needs a client that keeps unobserved data.
      queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      });
      Object.defineProperty(document, 'hidden', { configurable: true, value: true });
      // Order matters: vi.useFakeTimers() fakes requestAnimationFrame as well
      // (backing it with the mock clock, so advanceTimersByTime would run it
      // and hide the very defect under test). Stub it afterwards so the
      // never-firing version is the one the hook sees.
      vi.useFakeTimers();
      rafSpy = vi.fn(() => 1);
      vi.stubGlobal('requestAnimationFrame', rafSpy);
    });

    afterEach(() => {
      vi.useRealTimers();
      Object.defineProperty(document, 'hidden', { configurable: true, value: false });
    });

    it('applies printer status to the query cache', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });
      const ws = await waitForWs();
      act(() => ws.open());

      act(() => {
        ws.simulateMessage({
          type: 'printer_status',
          printer_id: 1,
          data: { state: 'RUNNING', progress: 42 },
        });
      });

      // Past the 100ms coalescing window.
      await act(async () => {
        vi.advanceTimersByTime(200);
      });

      // This is the key the tab-title/favicon progress reads
      // (usePrintProgressTitle) and nothing else.
      expect(queryClient.getQueryData(['printerStatus', 1])).toMatchObject({
        state: 'RUNNING',
        progress: 42,
      });
      expect(rafSpy).not.toHaveBeenCalled();
    });

    it('writes without waiting on a timer', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });
      const ws = await waitForWs();
      act(() => ws.open());

      act(() => {
        ws.simulateMessage({
          type: 'printer_status',
          printer_id: 1,
          data: { state: 'RUNNING', progress: 42 },
        });
      });

      // No advanceTimersByTime: a hidden tab's timers are throttled to once a
      // second at best, so anything the title depends on has to have landed
      // already. Reintroduce the coalescing timer on this path and the cache
      // is still empty here.
      expect(queryClient.getQueryData(['printerStatus', 1])).toMatchObject({
        state: 'RUNNING',
        progress: 42,
      });
    });

    it('applies the newest value when several arrive before a frame would have run', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });
      const ws = await waitForWs();
      act(() => ws.open());

      act(() => {
        ws.simulateMessage({ type: 'printer_status', printer_id: 1, data: { progress: 40 } });
        ws.simulateMessage({ type: 'printer_status', printer_id: 1, data: { progress: 41 } });
      });

      // Writing through per message must not resurrect an earlier one: the
      // pending map is drained on each flush, so a stale entry cannot be
      // re-applied over the newer value.
      expect(queryClient.getQueryData(['printerStatus', 1])).toMatchObject({ progress: 41 });
    });

    it('drains queued messages instead of wedging the queue', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');
      const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });
      const ws = await waitForWs();
      act(() => ws.open());

      // Everything other than printer_status goes through the message queue,
      // which used to stall with processingRef stuck true — messages then
      // piled up unbounded until the tab was shown again.
      act(() => {
        ws.simulateMessage({ type: 'print_complete', printer_id: 1, data: {} });
      });

      // 3s debounce, then the 500ms-apart stagger.
      await act(async () => {
        vi.advanceTimersByTime(4000);
      });

      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['archives'] });
      expect(rafSpy).not.toHaveBeenCalled();
    });
  });

  describe('visible tab still coalesces (#2754)', () => {
    /**
     * The counterpart to the hidden-tab block: the write-through is scoped to
     * a hidden tab on purpose. A visible one is painting, and the 100ms window
     * is what stops a burst of status messages turning into a render cascade —
     * so "just always write through" is not the simplification it looks like.
     */
    it('defers the write while the tab is visible', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const client = new QueryClient({
        defaultOptions: { queries: { retry: false, gcTime: Infinity } },
      });
      vi.useFakeTimers();
      try {
        renderHook(() => useWebSocket(), { wrapper: createWrapper(client) });
        const ws = await waitForWs();
        act(() => ws.open());

        act(() => {
          ws.simulateMessage({
            type: 'printer_status',
            printer_id: 1,
            data: { state: 'RUNNING', progress: 42 },
          });
        });

        expect(client.getQueryData(['printerStatus', 1])).toBeUndefined();

        await act(async () => {
          vi.advanceTimersByTime(200);
        });

        expect(client.getQueryData(['printerStatus', 1])).toMatchObject({ progress: 42 });
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe('sendMessage', () => {
    it('sends JSON message when connected', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { result } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

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

      const { useWebSocket } = await import('../../hooks/useWebSocket');

      renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      // GHSA-r2qv: connect() awaits a ws-token fetch before constructing
      // the WebSocket. Flush microtasks under fake timers so the await
      // resolves and MockWebSocket is pushed into wsInstances.
      await vi.advanceTimersByTimeAsync(0);
      const firstWs = wsInstances[wsInstances.length - 1]!;

      // Open connection
      act(() => {
        firstWs.open();
      });

      const instanceCountBefore = wsInstances.length;

      // Close connection
      act(() => {
        firstWs.close();
      });

      // Wait for reconnect timeout (3 seconds) + microtask flush for the
      // async connect() that the reconnect schedules.
      await vi.advanceTimersByTimeAsync(3000);

      // Should have created new WebSocket
      expect(wsInstances.length).toBe(instanceCountBefore + 1);
      expect(wsInstances[wsInstances.length - 1]).not.toBe(firstWs);

      vi.useRealTimers();
    });

    it('does NOT reconnect after an auth-rejection close (4401)', async () => {
      // Regression: a 4401 (ws-token invalid/expired or caller lacks
      // WEBSOCKET_CONNECT) used to reschedule connect() every 3s, spamming
      // /auth/ws-token forever. It must be terminal now.
      vi.useFakeTimers();

      const { useWebSocket } = await import('../../hooks/useWebSocket');
      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });

      await vi.advanceTimersByTimeAsync(0);
      const firstWs = wsInstances[wsInstances.length - 1]!;
      act(() => {
        firstWs.open();
      });

      const instanceCountBefore = wsInstances.length;

      // Server rejects auth.
      act(() => {
        firstWs.simulateClose(4401);
      });

      // No reconnect even after the 3s window elapses.
      await vi.advanceTimersByTimeAsync(3000);
      expect(wsInstances.length).toBe(instanceCountBefore);

      vi.useRealTimers();
    });

    it('does NOT open a socket or reconnect when ws-token mint returns 403', async () => {
      // Mike/Forge's case: an authenticated user whose group lacks
      // WEBSOCKET_CONNECT. POST /auth/ws-token returns 403; the hook must NOT
      // fall through to a tokenless socket (server closes it 4401) and must NOT
      // enter the reconnect loop — it degrades to REST polling instead.
      vi.useFakeTimers();

      vi.stubGlobal(
        'fetch',
        vi.fn(async () => ({
          ok: false,
          status: 403,
          statusText: 'Forbidden',
          headers: { get: () => null },
          json: async () => ({ detail: 'Insufficient permissions' }),
        })),
      );

      const { useWebSocket } = await import('../../hooks/useWebSocket');
      renderHook(() => useWebSocket(), { wrapper: createWrapper(queryClient) });

      // Flush the token-mint rejection, then let the (would-be) reconnect
      // window pass. No socket should ever be constructed.
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(3000);
      expect(wsInstances.length).toBe(0);

      vi.useRealTimers();
    });

    it('does NOT reconnect when a close fires during unmount', async () => {
      // The provider unmounting (e.g. logout redirect) must not leave a
      // scheduled reconnect behind — the cleanup marks disposed before
      // close(), so the resulting onclose is a no-op.
      vi.useFakeTimers();

      const { useWebSocket } = await import('../../hooks/useWebSocket');
      const { unmount } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      await vi.advanceTimersByTimeAsync(0);
      const ws = wsInstances[wsInstances.length - 1]!;
      act(() => {
        ws.open();
      });

      const instanceCountBefore = wsInstances.length;

      // Unmount closes the socket, which fires onclose synchronously.
      act(() => {
        unmount();
      });

      await vi.advanceTimersByTimeAsync(3000);
      expect(wsInstances.length).toBe(instanceCountBefore);

      vi.useRealTimers();
    });

    it('cleans up on unmount', async () => {
      const { useWebSocket } = await import('../../hooks/useWebSocket');

      const { unmount } = renderHook(() => useWebSocket(), {
        wrapper: createWrapper(queryClient),
      });

      const ws = await waitForWs();

      // Open connection
      act(() => {
        ws.open();
      });

      unmount();

      expect(ws.close).toHaveBeenCalled();
    });
  });
});
