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
  onToast,
}: UseAmsOperationsProps): UseAmsOperationsReturn {
  const [state, setState] = useState<OperationState>('IDLE');
  const [context, setContext] = useState<OperationContext | null>(null);

  // Track previous values for transition detection
  const prevAmsStatusMainRef = useRef(amsStatusMain);
  const prevTrayDataRef = useRef<string>('');

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
    prevTrayDataRef.current = '';
  }, [clearOperationTimeout]);

  // Set up timeout for current operation
  const startOperationTimeout = useCallback((timeoutMs: number) => {
    clearOperationTimeout();
    const startTime = context?.startTime ?? Date.now();
    timeoutRef.current = setTimeout(() => {
      console.log(`[useAmsOperations] Operation timed out after ${timeoutMs}ms`);
      reset();
    }, timeoutMs);
  }, [clearOperationTimeout, reset, context?.startTime]);

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

    console.log(`[useAmsOperations] Starting refresh: AMS ${amsId}, Tray ${trayId}`);

    // Capture current tray data signature for change detection
    const unit = amsUnits.find(u => u.id === amsId);
    const tray = unit?.tray?.find(t => t.id === trayId);
    if (tray) {
      prevTrayDataRef.current = JSON.stringify({
        tag_uid: tray.tag_uid,
        tray_uuid: tray.tray_uuid,
        tray_id_name: tray.tray_id_name,
        tray_type: tray.tray_type,
        tray_color: tray.tray_color,
      });
    }

    const startTime = Date.now();
    setState('REFRESHING');
    setContext({ refreshTarget: { amsId, trayId }, startTime });

    // Set timeout
    timeoutRef.current = setTimeout(() => {
      console.log(`[useAmsOperations] Refresh timeout for AMS ${amsId} tray ${trayId}`);
      reset();
    }, REFRESH_TIMEOUT_MS);

    refreshMutation.mutate({ amsId, trayId });
  }, [state, amsUnits, reset, refreshMutation]);

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

  // Detect REFRESH completion via tray data change
  useEffect(() => {
    if (state !== 'REFRESHING' || !context?.refreshTarget) return;

    const { amsId, trayId } = context.refreshTarget;
    const unit = amsUnits.find(u => u.id === amsId);
    const tray = unit?.tray?.find(t => t.id === trayId);

    if (!tray) return;

    const currentSignature = JSON.stringify({
      tag_uid: tray.tag_uid,
      tray_uuid: tray.tray_uuid,
      tray_id_name: tray.tray_id_name,
      tray_type: tray.tray_type,
      tray_color: tray.tray_color,
    });

    // Require minimum 500ms to avoid false positives from initial render
    const elapsed = Date.now() - context.startTime;
    if (prevTrayDataRef.current && prevTrayDataRef.current !== currentSignature && elapsed > 500) {
      console.log(`[useAmsOperations] Refresh complete: data changed for AMS ${amsId} tray ${trayId} (took ${elapsed}ms)`);
      reset();
    }
  }, [state, context, amsUnits, reset]);

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
