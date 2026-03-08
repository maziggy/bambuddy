import { useState, useRef, useCallback, useEffect } from 'react';

interface UseStreamReconnectOptions {
  maxAttempts?: number;
  initialDelay?: number;
  maxDelay?: number;
  stallCheckInterval?: number;
  initialRetryDelay?: number;
  initialRetryMax?: number;
  onReconnect: () => void;
  onGiveUp?: () => void;
  /** Pause stall detection (e.g. when minimized) */
  stallPaused?: boolean;
  /** Stall check function — called periodically, should return true if stalled */
  checkStalled?: () => Promise<boolean>;
}

interface UseStreamReconnectReturn {
  reconnectAttempts: number;
  isReconnecting: boolean;
  reconnectCountdown: number;
  handleStreamError: () => void;
  handleStreamSuccess: () => void;
  reset: () => void;
  cleanup: () => void;
}

export function useStreamReconnect({
  maxAttempts = 5,
  initialDelay = 2000,
  maxDelay = 30000,
  stallCheckInterval = 30000,
  initialRetryDelay = 500,
  initialRetryMax = 10,
  onReconnect,
  onGiveUp,
  stallPaused = false,
  checkStalled,
}: UseStreamReconnectOptions): UseStreamReconnectReturn {
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [isReconnecting, setIsReconnecting] = useState(false);
  const [reconnectCountdown, setReconnectCountdown] = useState(0);

  const reconnectTimerRef = useRef<NodeJS.Timeout | null>(null);
  const countdownIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const stallCheckIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const hasConnectedRef = useRef(false);
  const initialRetryCountRef = useRef(0);
  const initialRetryTimerRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttemptsRef = useRef(0);

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
    if (countdownIntervalRef.current) { clearInterval(countdownIntervalRef.current); countdownIntervalRef.current = null; }
    if (stallCheckIntervalRef.current) { clearInterval(stallCheckIntervalRef.current); stallCheckIntervalRef.current = null; }
    if (initialRetryTimerRef.current) { clearTimeout(initialRetryTimerRef.current); initialRetryTimerRef.current = null; }
  }, []);

  // Cleanup on unmount
  useEffect(() => clearTimers, [clearTimers]);

  const attemptReconnect = useCallback(() => {
    if (reconnectAttemptsRef.current >= maxAttempts) {
      setIsReconnecting(false);
      onGiveUp?.();
      return;
    }

    const delay = Math.min(initialDelay * Math.pow(2, reconnectAttemptsRef.current), maxDelay);
    setIsReconnecting(true);
    setReconnectCountdown(Math.ceil(delay / 1000));

    if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
    countdownIntervalRef.current = setInterval(() => {
      setReconnectCountdown(prev => {
        if (prev <= 1) {
          if (countdownIntervalRef.current) clearInterval(countdownIntervalRef.current);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    reconnectTimerRef.current = setTimeout(() => {
      reconnectAttemptsRef.current += 1;
      setReconnectAttempts(reconnectAttemptsRef.current);
      setIsReconnecting(false);
      onReconnect();
    }, delay);
  }, [maxAttempts, initialDelay, maxDelay, onReconnect, onGiveUp]);

  // Store attemptReconnect in a ref so the stall-check interval doesn't restart
  // every time the reconnect counter changes
  const attemptReconnectRef = useRef(attemptReconnect);
  attemptReconnectRef.current = attemptReconnect;

  // Stall detection
  useEffect(() => {
    if (!checkStalled || stallPaused || isReconnecting) {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
      return;
    }

    let stallCheckInFlight = false;
    stallCheckIntervalRef.current = setInterval(async () => {
      if (stallCheckInFlight) return;
      stallCheckInFlight = true;
      try {
        const stalled = await checkStalled();
        if (stalled) {
          if (stallCheckIntervalRef.current) {
            clearInterval(stallCheckIntervalRef.current);
            stallCheckIntervalRef.current = null;
          }
          attemptReconnectRef.current();
        }
      } catch {
        // Ignore errors
      } finally {
        stallCheckInFlight = false;
      }
    }, stallCheckInterval);

    return () => {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
    };
  }, [checkStalled, stallPaused, isReconnecting, stallCheckInterval]);

  const handleStreamError = useCallback(() => {
    if (!hasConnectedRef.current) {
      if (initialRetryTimerRef.current) return;
      if (initialRetryCountRef.current < initialRetryMax) {
        initialRetryCountRef.current += 1;
        initialRetryTimerRef.current = setTimeout(() => {
          initialRetryTimerRef.current = null;
          onReconnect();
        }, initialRetryDelay);
        return;
      }
      onGiveUp?.();
      return;
    }

    if (reconnectAttemptsRef.current < maxAttempts) {
      attemptReconnect();
    } else {
      onGiveUp?.();
    }
  }, [maxAttempts, initialRetryMax, initialRetryDelay, onReconnect, onGiveUp, attemptReconnect]);

  const handleStreamSuccess = useCallback(() => {
    hasConnectedRef.current = true;
    initialRetryCountRef.current = 0;
    reconnectAttemptsRef.current = 0;
    if (initialRetryTimerRef.current) { clearTimeout(initialRetryTimerRef.current); initialRetryTimerRef.current = null; }
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
    if (countdownIntervalRef.current) { clearInterval(countdownIntervalRef.current); countdownIntervalRef.current = null; }
  }, []);

  const reset = useCallback(() => {
    hasConnectedRef.current = false;
    initialRetryCountRef.current = 0;
    reconnectAttemptsRef.current = 0;
    setReconnectAttempts(0);
    setIsReconnecting(false);
    setReconnectCountdown(0);
    clearTimers();
  }, [clearTimers]);

  return {
    reconnectAttempts,
    isReconnecting,
    reconnectCountdown,
    handleStreamError,
    handleStreamSuccess,
    reset,
    cleanup: clearTimers,
  };
}
