import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef } from 'react';
import { useToast } from '../contexts/ToastContext';
import { useTranslation } from 'react-i18next';
import { api, ApiError } from '../api/client';
import { inventoryLocationsQueryKey, inventorySuppliersQueryKey } from '../utils/inventoryQueries';

// The auth-failure close code of /api/v1/ws (websocket.py
// _WS_CLOSE_UNAUTHORIZED). It reaches the browser only for a socket the server
// had accepted: one closed when its session ended (password change or reset,
// 2FA removal, deactivation, rename), when authentication was turned on, when a
// scope refresh failed, or by the server's check right after accepting it. The
// hook asks AuthContext to re-check (a tab whose session ended goes to the
// login page, which unmounts this provider) and reconnects with a fresh token.
// Each 4401 before a socket's first message doubles the wait, up to five
// minutes: no /auth/ws-token loop, and a failure that clears still recovers.
//
// A socket refused before it was accepted (no or invalid token, user or key
// gone, a failed lookup) gets an HTTP 403 on the handshake, which the browser
// reports as 1006 with no open, as for a server that is down. Those can't be
// told apart, so they get no re-check, and a backoff capped at 30 s, so tabs
// still come back quickly after a restart.
const WS_CLOSE_UNAUTHORIZED = 4401;
const UNAUTHORIZED_RETRY_FIRST_MS = 3000;
const UNAUTHORIZED_RETRY_MAX_MS = 300000;
const HANDSHAKE_RETRY_MAX_MS = 30000;

interface WebSocketMessage {
  type: string;
  printer_id?: number;
  data?: Record<string, unknown>;
  printer_name?: string;
  filename?: string;
  missing_slots?: Array<{ slot?: string }>;
  // Spool-assignment read-back verification (#2582).
  slot?: string;
  verified?: boolean;
  kprofile_applied?: boolean;
  // Slicer Pipeline run events (#1425 PR C). ``run`` carries the full
  // PipelineRunResponse payload — typed loosely here so the WebSocket hook
  // doesn't pull the full client.ts types in.
  run?: { pipeline_id?: number | null };
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  // Set true by the effect cleanup so a close event fired *during* unmount
  // can't schedule a reconnect after the provider is gone (the old code cleared
  // reconnectTimeoutRef, then .close() ran ws.onclose which set a *fresh*
  // timeout — a leaked reconnect that kept minting ws-tokens post-logout).
  const disposedRef = useRef(false);
  // The wait before the next reconnect after a 4401; 0 once a socket is
  // admitted (its first message).
  const unauthorizedDelayRef = useRef(0);
  // The wait before the next reconnect after a handshake that failed (the
  // socket never opened); 0 once one opens.
  const handshakeDelayRef = useRef(0);
  const queryClient = useQueryClient();
  const lastMissingSpoolWarningRef = useRef<Map<number, string>>(new Map());
  const { showToast } = useToast();
  const { t } = useTranslation();

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
        handleMessageRef.current(message);
        // Small delay between messages to prevent overwhelming the browser.
        // This setTimeout is the yield; a requestAnimationFrame around the
        // handler used to sit here too, which stalled the whole queue in a
        // hidden tab (see the note on the rAF removal below).
        if (messageQueueRef.current.length > 0) {
          setTimeout(processNext, 16); // ~60fps
        } else {
          processingRef.current = false;
        }
      } else {
        processingRef.current = false;
      }
    };

    processNext();
  }, []);

  const connect = useCallback(async () => {
    if (disposedRef.current || wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    // GHSA-r2qv follow-up: when auth is enabled, /ws now requires a token
    // minted by POST /api/v1/auth/ws-token. We use the shared ``api.request``
    // helper (via ``api.getWebSocketToken``) so the JWT Authorization header
    // is attached — a raw ``fetch()`` with ``credentials: 'include'`` would
    // miss it (Bambuddy uses Bearer tokens, not cookies, for JWT auth).
    // Auth-disabled deployments accept connections without a token.
    let token: string | undefined;
    try {
      const resp = await api.getWebSocketToken();
      token = resp.token;
    } catch (err) {
      // A 401/403 from the token mint is an AUTH decision, not a transient
      // blip, so retrying is pointless and hammers /auth/ws-token every 3s:
      //   401 — not signed in. An expired JWT: ``request()`` already cleared
      //         it and dispatched ``auth:expired``, so the route guard is
      //         redirecting to /login. "Authentication required" (no token,
      //         e.g. a tab opened while auth was off, which was turned on while
      //         its socket was down) is not treated as a sign-out there, so
      //         ask AuthContext to re-check, which then shows the login.
      //   403 — the user is validly logged in but their group lacks
      //         WEBSOCKET_CONNECT (they stay logged in; live updates simply
      //         degrade to the REST polling the query cache already does), or
      //         an API key (a kiosk) whose owner was deactivated while its
      //         socket was down. Re-checked as well: the first gets their user
      //         back and nothing changes, the second goes to the login.
      // Either way: do NOT open a tokenless socket (the server just closes it
      // 4401) and do NOT reconnect. The old catch-all fell through to a
      // tokenless socket whose 4401 close rescheduled connect() forever. A
      // network/5xx error is not auth — fall through and let the socket + its
      // reconnect loop handle it (auth-disabled deployments also land here with
      // no token and connect fine).
      const status = err instanceof ApiError ? err.status : 0;
      if (status === 401 || status === 403) {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
        return;
      }
    }

    if (disposedRef.current) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : '';
    const wsUrl = `${protocol}//${window.location.host}/api/v1/ws${tokenParam}`;

    const ws = new WebSocket(wsUrl);

    let pingInterval: number | null = null;
    let opened = false;

    ws.onopen = () => {
      opened = true;
      handshakeDelayRef.current = 0;
      if (import.meta.env.MODE !== 'test') console.log('[WebSocket] Connected');
      // Start ping interval
      pingInterval = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'ping' }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      unauthorizedDelayRef.current = 0;
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
      wsRef.current = null;

      // Don't reconnect once the provider has unmounted. After an auth
      // rejection (4401): re-check auth, and reconnect with a growing wait (see
      // WS_CLOSE_UNAUTHORIZED). A socket that never opened (a refused handshake
      // or a server that is down) also waits longer each time, up to 30 s. One
      // that opened and then dropped is a network drop: the 3s reconnect.
      if (disposedRef.current) {
        return;
      }
      if (event.code === WS_CLOSE_UNAUTHORIZED) {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
        const delay = unauthorizedDelayRef.current
          ? Math.min(unauthorizedDelayRef.current * 2, UNAUTHORIZED_RETRY_MAX_MS)
          : UNAUTHORIZED_RETRY_FIRST_MS;
        unauthorizedDelayRef.current = delay;
        reconnectTimeoutRef.current = window.setTimeout(() => {
          connect();
        }, delay);
        return;
      }
      if (!opened) {
        const delay = handshakeDelayRef.current
          ? Math.min(handshakeDelayRef.current * 2, HANDSHAKE_RETRY_MAX_MS)
          : UNAUTHORIZED_RETRY_FIRST_MS;
        handshakeDelayRef.current = delay;
        reconnectTimeoutRef.current = window.setTimeout(() => {
          connect();
        }, delay);
        return;
      }

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

  // Write every pending printer status into the query cache.
  //
  // Extracted so the hidden-tab path below can run it inline: both paths share
  // this one body, so the merge semantics cannot drift apart. Cancels any
  // scheduled coalescing timer, since everything it was going to write has
  // just been written and re-running it would re-apply stale data over newer.
  const flushPrinterStatus = useCallback(() => {
    if (printerStatusTimeoutRef.current) {
      clearTimeout(printerStatusTimeoutRef.current);
      printerStatusTimeoutRef.current = null;
    }

    const updates = new Map(pendingPrinterStatus.current);
    pendingPrinterStatus.current.clear();

    updates.forEach((statusData, id) => {
      queryClient.setQueryData(['printerStatus', id], (old: Record<string, unknown> | undefined) => {
        const merged = { ...old, ...statusData };
        if (merged.wifi_signal == null && old?.wifi_signal != null) {
          merged.wifi_signal = old.wifi_signal;
        }
        return merged;
      });
    });
  }, [queryClient]);

  // Printer status update — coalesced while the tab is visible, written
  // straight through while it is not.
  //
  // #2754 (reporter @mic4rd), in two stages. First, these writes ran inside a
  // requestAnimationFrame: a hidden tab gets no rendering opportunities, so
  // the browser *holds* queued frame callbacks rather than throttling them,
  // and nothing reached the cache until the tab was shown again. Removing the
  // frame callback fixed that total stall but not the report, because a second
  // timer-shaped dependency was left behind — this 100ms coalescing window.
  //
  // Browsers clamp timers in a hidden page to at best once a second, and drop
  // pages hidden for more than five minutes to roughly one wake-up a minute.
  // The reporter saw a tab title stuck at 2% beside a page at 40%.
  //
  // The coalescing exists to stop rapid messages triggering a render cascade.
  // A hidden tab is not painting, so there is no cascade to prevent there —
  // the timer is pure cost, and it is exactly the thing being throttled. So
  // when hidden, skip it and write immediately.
  //
  // Note "hidden", not "unfocused": on Windows a fully-occluded window reports
  // visibilityState 'hidden' too, which is why the reporter saw this from
  // merely clicking away rather than only from switching tabs.
  const throttledPrinterStatusUpdate = useCallback((printerId: number, data: Record<string, unknown>) => {
    // Merge with any pending data for this printer
    const existing = pendingPrinterStatus.current.get(printerId) || {};
    pendingPrinterStatus.current.set(printerId, { ...existing, ...data });

    if (document.hidden) {
      flushPrinterStatus();
      return;
    }

    // Schedule update if not already scheduled
    if (!printerStatusTimeoutRef.current) {
      printerStatusTimeoutRef.current = window.setTimeout(flushPrinterStatus, 100);
    }
  }, [flushPrinterStatus]);

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

      // Invalidate queries one at a time with delays to prevent freeze.
      // The 500ms stagger is the anti-cascade measure; a frame callback around
      // each invalidation used to sit inside it and stalled these refreshes in
      // a hidden tab for the same reason as the status writes above (#2754).
      let delay = 0;
      keys.forEach((key) => {
        setTimeout(() => {
          queryClient.invalidateQueries({ queryKey: [key] });
        }, delay);
        delay += 500; // 500ms between each invalidation
      });
    }, 3000);
  }, [queryClient]);

  // Slot changes skip the cascade debounce above. That debounce exists for
  // print completion, where one event fans out across half the app; a spool
  // swap touches one slot and the user is standing at the printer looking at
  // the card. Waiting 3s of quiet and then staggering the keys 500ms apart put
  // several seconds of visibly wrong data on screen for no benefit, and the
  // timer restarts on every further event, so a busy moment could defer it
  // indefinitely. React Query coalesces repeat invalidations of one key, so a
  // Spoolman sync reporting a dozen slots at once still costs three refetches.
  const invalidateSlotQueries = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
    queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
    queryClient.invalidateQueries({ queryKey: ['slotPresets'] });
    // Both modes' bindings, which the print dialog prices and weighs slots from.
    queryClient.invalidateQueries({ queryKey: ['printer-inventory-remain'] });
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

      case 'missing_spool_assignment': {
        if (message.printer_id === undefined || !Array.isArray(message.missing_slots)) {
          break;
        }

        const missingSlotLabels = message.missing_slots
          .map((slot) => (slot && typeof slot.slot === 'string' ? slot.slot : 'Unknown'))
          .filter((slot) => slot.length > 0);

        if (missingSlotLabels.length === 0) {
          lastMissingSpoolWarningRef.current.delete(message.printer_id);
          break;
        }

        const signature = missingSlotLabels.join('|');
        if (lastMissingSpoolWarningRef.current.get(message.printer_id) === signature) {
          break;
        }
        lastMissingSpoolWarningRef.current.set(message.printer_id, signature);

        const printerName = message.printer_name || `Printer ${message.printer_id}`;
        const toastMsg = t('printers.toast.missingSpoolAssignment', {
          printer: printerName,
          slots: missingSlotLabels.join(', '),
        });
        showToast(toastMsg, 'warning');
        break;
      }

      case 'print_complete':
        // Don't invalidate printerStatus here - it causes re-render cascade and browser freeze
        // The printer_status websocket messages will naturally update the status
        debouncedInvalidate('archives');
        debouncedInvalidate('archiveStats');
        break;

      case 'print_confirm_request': {
        // #1898: a completed print asked for its outcome verdict. Handed to
        // the globally mounted ConfirmOutcomeDialog in Layout via the same
        // CustomEvent relay plate_not_empty uses.
        const detail = (message as unknown as { data?: { archive_id?: number; print_name?: string } }).data;
        if (detail?.archive_id != null) {
          window.dispatchEvent(new CustomEvent('print-confirm-request', { detail }));
        }
        break;
      }

      case 'kill_switch_triggered': {
        const printer = message.printer_name || `Printer ${message.printer_id ?? '?'}`;
        const filename = message.filename || t('common.unknown');
        showToast(t('printers.toast.killSwitchTriggered', { printer, filename }), 'error');
        break;
      }

      case 'billing_charge_failed': {
        const printer = message.printer_name || `Printer ${message.printer_id ?? '?'}`;
        const filename = message.filename || t('common.unknown');
        showToast(t('printers.toast.billingChargeFailed', { printer, filename }), 'error');
        break;
      }

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

      case 'plate_check_failed':
        // The plate check before a print could not run; the print goes on.
        window.dispatchEvent(new CustomEvent('plate-check-failed', {
          detail: {
            printer_id: message.printer_id,
            printer_name: (message as unknown as { printer_name?: string }).printer_name,
            reason: (message as unknown as { reason?: string }).reason,
          }
        }));
        break;

      case 'announcements_changed':
        // The backend fetched a newer feed: re-read the list so the sidebar dot
        // and the banner appear without a reload. The event says nothing itself.
        debouncedInvalidate('announcements');
        break;

      case 'printer_locations_changed':
        // A location was created, renamed, restyled or deleted, or printers moved
        // between them (#2962). Every page that shows a printer's location reads
        // it from the printer list.
        debouncedInvalidate('printer-locations');
        debouncedInvalidate('printers');
        break;

      case 'inventory_changed':
        // Spool created/updated/deleted/archived/restored - refresh inventory across all tabs
        debouncedInvalidate('inventory-spools');
        debouncedInvalidate('spoolman-inventory-spools');
        debouncedInvalidate(inventoryLocationsQueryKey[0]);
        debouncedInvalidate(inventorySuppliersQueryKey[0]);
        // The per-material-number aggregate is derived from the same rows (#2870).
        debouncedInvalidate('material-number-stats');
        // Printer cards and slot views read a spool's weight from the slot rows.
        debouncedInvalidate('spool-assignments');
        break;

      case 'spool_assignment_changed':
        // Spool assigned/unassigned - refresh assignment data across all tabs.
        // Both inventory modes: the Spoolman AMS sync raises this event too, and
        // its slot rows live under their own query keys.
        invalidateSlotQueries();
        // A Spoolman move clears the spool's fallback tag for the slot it left
        // (#1574). The slot cards fall back to the tag -> spool map once the
        // slot row is gone, so a cached map kept the spool on its old slot in
        // every tab but the one that moved it. Fetched in Spoolman mode only.
        queryClient.invalidateQueries({ queryKey: ['linked-spools'] });
        break;

      case 'spool_assignment_verified': {
        // #2582: the backend read the AMS telemetry back after an assignment
        // and either confirmed the tray accepted it or timed out. Toast the
        // outcome so the AMS→Studio hand-off is no longer silent.
        // Backend always supplies printer_name (falls back to "Printer <id>"),
        // so the '||' here only guards a malformed payload.
        const printer = message.printer_name || 'Printer';
        const slot = message.slot || '?';
        if (message.verified) {
          if (message.kprofile_applied === false) {
            // Filament id landed but the K-profile (cali_idx) did not — the
            // exact "loaded but no flow profile" case the reporter chased.
            showToast(
              t('printers.toast.assignmentVerifiedNoKprofile', { slot, printer }),
              'warning'
            );
          } else {
            showToast(t('printers.toast.assignmentVerified', { slot, printer }), 'success');
          }
        } else {
          showToast(t('printers.toast.assignmentNotConfirmed', { slot, printer }), 'warning');
        }
        break;
      }

      case 'spool_auto_assigned':
        // RFID tag matched - refresh inventory and assignment data.
        // slotPresets is not optional here: auto-assigning rewrites the slot's
        // slot_preset_mappings row, and the AMS slot card reads that row ahead
        // of the live tray_info_idx. Leave it cached and swapping a spool keeps
        // the *previous* spool's preset name on the card -- the rest of the
        // card updates off the status push, so it reads as one wrong line
        // rather than a stale card. Only the manual assign path invalidated it.
        debouncedInvalidate('inventory-spools');
        invalidateSlotQueries();
        break;

      case 'spool_usage_logged':
        // Filament consumption recorded - refresh spool data, and the slot rows
        // printer cards read a spool's remaining weight from.
        debouncedInvalidate('inventory-spools');
        debouncedInvalidate('spool-assignments');
        break;

      case 'unknown_tag': {
        // Unknown RFID tag detected — dispatch event for UI. The backend
        // ships the slot's current tray data alongside the event so
        // consumers don't have to look it up from the (frequently stale)
        // cached printerStatus query.
        const m = message as unknown as {
          printer_id?: number;
          ams_id?: number;
          tray_id?: number;
          tag_uid?: string;
          tray_uuid?: string;
          tray_type?: string | null;
          tray_color?: string | null;
          tray_sub_brands?: string | null;
          tray_count?: number | null;
        };
        window.dispatchEvent(new CustomEvent('unknown-tag', {
          detail: {
            printer_id: m.printer_id,
            ams_id: m.ams_id,
            tray_id: m.tray_id,
            tag_uid: m.tag_uid,
            tray_uuid: m.tray_uuid,
            tray_type: m.tray_type,
            tray_color: m.tray_color,
            tray_sub_brands: m.tray_sub_brands,
            tray_count: m.tray_count,
          }
        }));
        break;
      }

      case 'spoolbuddy_weight':
        window.dispatchEvent(new CustomEvent('spoolbuddy-weight', { detail: message }));
        break;

      case 'spoolbuddy_tag_matched':
        window.dispatchEvent(new CustomEvent('spoolbuddy-tag-matched', { detail: message }));
        debouncedInvalidate('inventory-spools');
        break;

      case 'spoolbuddy_unknown_tag':
        window.dispatchEvent(new CustomEvent('spoolbuddy-unknown-tag', { detail: message }));
        break;

      case 'spoolbuddy_tag_removed':
        window.dispatchEvent(new CustomEvent('spoolbuddy-tag-removed', { detail: message }));
        break;

      case 'spoolbuddy_tag_written':
        window.dispatchEvent(new CustomEvent('spoolbuddy-tag-written', { detail: message }));
        debouncedInvalidate('inventory-spools');
        break;

      case 'spoolbuddy_tag_write_failed':
        window.dispatchEvent(new CustomEvent('spoolbuddy-tag-write-failed', { detail: message }));
        break;

      // Written to the chip, but not linked to the spool.
      case 'spoolbuddy_tag_link_failed':
        window.dispatchEvent(new CustomEvent('spoolbuddy-tag-link-failed', { detail: message }));
        break;

      // A scanned tag could not be looked up at all, as opposed to an unknown tag.
      case 'spoolman_unavailable':
        window.dispatchEvent(new CustomEvent('spoolman-unavailable', { detail: message }));
        break;

      case 'spoolbuddy_lookup_error':
        window.dispatchEvent(new CustomEvent('spoolbuddy-lookup-error', { detail: message }));
        break;

      case 'spoolbuddy_unregistered':
        debouncedInvalidate('spoolbuddy-devices');
        break;

      case 'spoolbuddy_online':
        window.dispatchEvent(new CustomEvent('spoolbuddy-online', { detail: message }));
        debouncedInvalidate('spoolbuddy-devices');
        debouncedInvalidate('spoolbuddy-update-check');
        break;

      case 'spoolbuddy_offline':
        window.dispatchEvent(new CustomEvent('spoolbuddy-offline', { detail: message }));
        debouncedInvalidate('spoolbuddy-devices');
        break;

      case 'spoolbuddy_update':
        debouncedInvalidate('spoolbuddy-devices');
        debouncedInvalidate('spoolbuddy-update-check');
        break;

      // Dispatch toast lifecycle (#1625 follow-up — restored the upload
      // progress UI that the scheduler unification removed). Four backend
      // event types collapse to one frontend channel. No
      // `queue_item_queued` (the toast must wait for the upload to
      // actually start) and no `queue_item_dispatched` (the legacy
      // background-dispatch flow kept status='processing' from upload
      // start until printer ack — the "Awaiting printer…" subtitle is
      // derived from upload_progress_pct >= 99.9, not from a separate
      // event).
      case 'queue_item_uploading':
      case 'queue_item_upload_progress':
      case 'queue_item_acked':
      case 'queue_item_failed':
        window.dispatchEvent(new CustomEvent('bambuddy:dispatch-toast', { detail: message }));
        break;
      // Slicer Pipeline runs (#1425 PR C). State transitions on the run
      // refresh both the dashboard list AND the per-pipeline "Last run"
      // chip in Settings → Pipelines.
      case 'pipeline_run_updated':
        queryClient.invalidateQueries({ queryKey: ['pipeline-runs-all'] });
        if (message.run?.pipeline_id) {
          queryClient.invalidateQueries({ queryKey: ['pipeline-runs', message.run.pipeline_id] });
        }
        break;
    }
  }, [queryClient, debouncedInvalidate, invalidateSlotQueries, throttledPrinterStatusUpdate, showToast, t]);

  // Keep the ref updated with latest handleMessage
  useEffect(() => {
    handleMessageRef.current = handleMessage;
  }, [handleMessage]);

  useEffect(() => {
    // connect() is async after the GHSA-r2qv fix (mints a ws-token first).
    // Fire-and-forget at mount; the inner reconnect loop also calls
    // connect() in the ws.onclose handler.
    disposedRef.current = false;
    void connect();

    return () => {
      // Mark disposed BEFORE closing so the ws.onclose triggered by close()
      // sees it and won't schedule a post-unmount reconnect.
      disposedRef.current = true;
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
}
