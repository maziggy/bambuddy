import { useState, useRef, useCallback, useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { AMSUnit } from '../../api/client';

/**
 * AMS Operation State Machine
 *
 * States:
 * - IDLE: No operation in progress, all buttons enabled
 * - REFRESHING: RFID refresh in progress for a specific slot
 * - LOADING: Filament load in progress
 * - UNLOADING: Filament unload in progress
 *
 * Completion detection:
 * - REFRESH: AMS tray data changes (tag_uid, tray_uuid, etc.) OR timeout (15s)
 * - LOAD/UNLOAD: ams_status_main transitions from 1 (filament_change) to 0 (idle) OR timeout (60s)
 *
 * Rules:
 * - Only one operation at a time
 * - All operations have timeout fallback
 * - Operation can be cancelled/reset manually
 */

export type OperationState = 'IDLE' | 'REFRESHING' | 'LOADING' | 'UNLOADING';

export interface RefreshTarget {
  amsId: number;
  trayId: number;
}

export interface OperationContext {
  // For REFRESHING: which slot is being refreshed
  refreshTarget?: RefreshTarget;
  // For LOADING: target tray ID we're loading
  loadTargetTrayId?: number;
  // Timestamp when operation started
  startTime: number;
}

interface UseAmsOperationsProps {
  printerId: number;
  amsUnits: AMSUnit[];
  amsStatusMain: number;
  trayNow: number;
  lastAmsUpdate: number;  // Backend timestamp for detecting AMS data updates
  onToast: (message: string, type: 'success' | 'error') => void;
}

interface UseAmsOperationsReturn {
  // Current state
  state: OperationState;
  context: OperationContext | null;

  // Operation triggers
  startRefresh: (amsId: number, trayId: number) => void;
  startLoad: (trayId: number, extruderId?: number) => void;
  startUnload: () => void;

  // Manual reset (e.g., for retry)
  reset: () => void;

  // Derived state helpers
  isOperationInProgress: boolean;
  isRefreshingSlot: (amsId: number, trayId: number) => boolean;

  // For FilamentChangeCard - which type of operation
  isLoadOperation: boolean;
  loadTargetTrayId: number | null;

  // Mutation error states (for UI feedback)
  loadError: Error | null;
  unloadError: Error | null;
  refreshError: Error | null;
}

// Timeouts for different operations
const REFRESH_TIMEOUT_MS = 15000; // 15 seconds for RFID refresh
const FILAMENT_CHANGE_TIMEOUT_MS = 120000; // 2 minutes for load/unload (these can take a while with heating)

export function useAmsOperations({
  printerId,
  amsUnits,
  amsStatusMain,
  trayNow,
  lastAmsUpdate,
  onToast,
}: UseAmsOperationsProps): UseAmsOperationsReturn {
  const [state, setState] = useState<OperationState>('IDLE');
  const [context, setContext] = useState<OperationContext | null>(null);

  // Track previous values for transition detection
  const prevAmsStatusMainRef = useRef(amsStatusMain);
  // Track initial tray data signature for detecting changes during refresh
  const refreshInitialDataRef = useRef<string>('');
  // Track initial lastAmsUpdate value at start of refresh (to detect when new data arrives)
  const refreshInitialAmsUpdateRef = useRef<number>(0);

  // Timeout ref for cleanup
  const timeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Clear any pending timeout
  const clearOperationTimeout = useCallback(() => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  // Reset to IDLE state
  const reset = useCallback(() => {
    clearOperationTimeout();
    setState('IDLE');
    setContext(null);
    refreshInitialDataRef.current = '';
    refreshInitialAmsUpdateRef.current = 0;
  }, [clearOperationTimeout]);

  // === Mutations ===

  const refreshMutation = useMutation({
    mutationFn: ({ amsId, trayId }: { amsId: number; trayId: number }) =>
      api.refreshAmsTray(printerId, amsId, trayId),
    onSuccess: (data) => {
      if (data.success) {
        onToast(data.message || 'RFID refresh started', 'success');
      } else {
        onToast(data.message || 'Failed to refresh tray', 'error');
        reset();
      }
    },
    onError: (error) => {
      console.error('[useAmsOperations] Refresh error:', error);
      onToast('Failed to refresh tray', 'error');
      reset();
    },
  });

  const loadMutation = useMutation({
    mutationFn: ({ trayId, extruderId }: { trayId: number; extruderId?: number }) =>
      api.amsLoadFilament(printerId, trayId, extruderId),
    onSuccess: (data) => {
      console.log('[useAmsOperations] Load request sent:', data);
      // Don't reset here - wait for ams_status_main transition
    },
    onError: (error) => {
      console.error('[useAmsOperations] Load error:', error);
      reset();
    },
  });

  const unloadMutation = useMutation({
    mutationFn: () => api.amsUnloadFilament(printerId),
    onSuccess: (data) => {
      console.log('[useAmsOperations] Unload request sent:', data);
      // Don't reset here - wait for ams_status_main transition
    },
    onError: (error) => {
      console.error('[useAmsOperations] Unload error:', error);
      reset();
    },
  });

  // === Operation Triggers ===

  const startRefresh = useCallback((amsId: number, trayId: number) => {
    if (state !== 'IDLE') {
      console.log('[useAmsOperations] Cannot start refresh - operation in progress:', state);
      return;
    }

    // Capture current tray data to detect changes
    const unit = amsUnits.find(u => u.id === amsId);
    const tray = unit?.tray?.find(t => t.id === trayId);
    if (tray) {
      refreshInitialDataRef.current = JSON.stringify({
        tag_uid: tray.tag_uid,
        tray_uuid: tray.tray_uuid,
        tray_type: tray.tray_type,
        tray_color: tray.tray_color,
      });
    }

    // Capture initial lastAmsUpdate to detect when NEW data arrives
    refreshInitialAmsUpdateRef.current = lastAmsUpdate;

    const startTime = Date.now();
    console.log(`[useAmsOperations] Starting refresh: AMS ${amsId}, Tray ${trayId}, startTime=${startTime}, initialAmsUpdate=${lastAmsUpdate}`);

    setState('REFRESHING');
    setContext({ refreshTarget: { amsId, trayId }, startTime });

    // Set timeout
    timeoutRef.current = setTimeout(() => {
      console.log(`[useAmsOperations] Refresh timeout for AMS ${amsId} tray ${trayId}`);
      reset();
    }, REFRESH_TIMEOUT_MS);

    refreshMutation.mutate({ amsId, trayId });
  }, [state, amsUnits, lastAmsUpdate, reset, refreshMutation]);

  const startLoad = useCallback((trayId: number, extruderId?: number) => {
    if (state !== 'IDLE') {
      console.log('[useAmsOperations] Cannot start load - operation in progress:', state);
      return;
    }

    console.log(`[useAmsOperations] Starting load: tray ${trayId}, extruder ${extruderId}`);

    const startTime = Date.now();
    setState('LOADING');
    setContext({ loadTargetTrayId: trayId, startTime });

    // Set timeout
    timeoutRef.current = setTimeout(() => {
      console.log(`[useAmsOperations] Load timeout for tray ${trayId}`);
      reset();
    }, FILAMENT_CHANGE_TIMEOUT_MS);

    loadMutation.mutate({ trayId, extruderId });
  }, [state, reset, loadMutation]);

  const startUnload = useCallback(() => {
    if (state !== 'IDLE') {
      console.log('[useAmsOperations] Cannot start unload - operation in progress:', state);
      return;
    }

    console.log('[useAmsOperations] Starting unload');

    const startTime = Date.now();
    setState('UNLOADING');
    setContext({ startTime });

    // Set timeout
    timeoutRef.current = setTimeout(() => {
      console.log('[useAmsOperations] Unload timeout');
      reset();
    }, FILAMENT_CHANGE_TIMEOUT_MS);

    unloadMutation.mutate();
  }, [state, reset, unloadMutation]);

  // === Completion Detection ===

  // Detect REFRESH completion by waiting for new AMS data to arrive
  // RFID read takes 5-10 seconds. We detect completion when:
  // 1. Data changed (new/different spool detected) - complete after minimum 1s
  // 2. lastAmsUpdate timestamp changed from initial AND elapsed > 5 seconds
  //    This means a new AMS data packet arrived after the RFID read should be done
  useEffect(() => {
    if (state !== 'REFRESHING' || !context?.refreshTarget) return;

    const { amsId, trayId } = context.refreshTarget;
    const elapsed = Date.now() - context.startTime;

    // Get current tray data
    const unit = amsUnits.find(u => u.id === amsId);
    const tray = unit?.tray?.find(t => t.id === trayId);
    if (!tray) return;

    const currentData = JSON.stringify({
      tag_uid: tray.tag_uid,
      tray_uuid: tray.tray_uuid,
      tray_type: tray.tray_type,
      tray_color: tray.tray_color,
    });

    // Check if data changed (new spool detected)
    const dataChanged = refreshInitialDataRef.current && currentData !== refreshInitialDataRef.current;

    // Check if lastAmsUpdate changed from when we started
    const amsUpdateChanged = lastAmsUpdate !== refreshInitialAmsUpdateRef.current;

    // Primary completion: data changed (new spool detected) - complete quickly
    if (dataChanged && elapsed > 1000) {
      console.log(`[useAmsOperations] Refresh complete: data changed for AMS ${amsId} tray ${trayId} (took ${elapsed}ms)`);
      reset();
      return;
    }

    // Secondary completion: new AMS update received after minimum wait time
    // Wait 8 seconds to ensure RFID read has time to complete before considering updates
    if (amsUpdateChanged && elapsed > 8000) {
      console.log(`[useAmsOperations] Refresh complete: new AMS update after ${elapsed}ms for AMS ${amsId} tray ${trayId}`);
      reset();
    }
  }, [state, context, amsUnits, lastAmsUpdate, reset]);

  // Detect LOAD/UNLOAD completion via ams_status_main transition 1 → 0
  useEffect(() => {
    if (state !== 'LOADING' && state !== 'UNLOADING') {
      prevAmsStatusMainRef.current = amsStatusMain;
      return;
    }

    const wasActive = prevAmsStatusMainRef.current === 1;
    const isNowIdle = amsStatusMain === 0;

    if (wasActive && isNowIdle) {
      console.log(`[useAmsOperations] ${state} complete: ams_status_main transitioned 1→0`);
      reset();
    }

    prevAmsStatusMainRef.current = amsStatusMain;
  }, [state, amsStatusMain, reset]);

  // Secondary completion detection for LOAD: tray_now matches target
  useEffect(() => {
    if (state !== 'LOADING' || !context?.loadTargetTrayId) return;

    if (trayNow === context.loadTargetTrayId) {
      console.log(`[useAmsOperations] Load complete: tray_now=${trayNow} matches target`);
      reset();
    }
  }, [state, context, trayNow, reset]);

  // Secondary completion detection for UNLOAD: tray_now becomes 255
  useEffect(() => {
    if (state !== 'UNLOADING') return;

    // Only trigger if we're past the initial phase (give it 1s to start)
    const elapsed = context?.startTime ? Date.now() - context.startTime : 0;
    if (trayNow === 255 && elapsed > 1000) {
      console.log('[useAmsOperations] Unload complete: tray_now=255');
      reset();
    }
  }, [state, context, trayNow, reset]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      clearOperationTimeout();
    };
  }, [clearOperationTimeout]);

  // === Derived State ===

  const isOperationInProgress = state !== 'IDLE';

  const isRefreshingSlot = useCallback((amsId: number, trayId: number) => {
    if (state !== 'REFRESHING' || !context?.refreshTarget) return false;
    return context.refreshTarget.amsId === amsId && context.refreshTarget.trayId === trayId;
  }, [state, context]);

  const isLoadOperation = state === 'LOADING';
  const loadTargetTrayId = context?.loadTargetTrayId ?? null;

  return {
    state,
    context,
    startRefresh,
    startLoad,
    startUnload,
    reset,
    isOperationInProgress,
    isRefreshingSlot,
    isLoadOperation,
    loadTargetTrayId,
    loadError: loadMutation.error,
    unloadError: unloadMutation.error,
    refreshError: refreshMutation.error,
  };
}
