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
  /** Printer ID for stall detection API calls */
  printerId?: number;
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

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
    if (countdownIntervalRef.current) { clearInterval(countdownIntervalRef.current); countdownIntervalRef.current = null; }
    if (stallCheckIntervalRef.current) { clearInterval(stallCheckIntervalRef.current); stallCheckIntervalRef.current = null; }
    if (initialRetryTimerRef.current) { clearTimeout(initialRetryTimerRef.current); initialRetryTimerRef.current = null; }
  }, []);

  // Cleanup on unmount
  useEffect(() => clearTimers, [clearTimers]);

  const attemptReconnect = useCallback(() => {
    if (reconnectAttempts >= maxAttempts) {
      setIsReconnecting(false);
      onGiveUp?.();
      return;
    }

    const delay = Math.min(initialDelay * Math.pow(2, reconnectAttempts), maxDelay);
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
      setReconnectAttempts(prev => prev + 1);
      setIsReconnecting(false);
      onReconnect();
    }, delay);
  }, [reconnectAttempts, maxAttempts, initialDelay, maxDelay, onReconnect, onGiveUp]);

  // Stall detection
  useEffect(() => {
    if (!checkStalled || stallPaused || isReconnecting) {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
      return;
    }

    stallCheckIntervalRef.current = setInterval(async () => {
      try {
        const stalled = await checkStalled();
        if (stalled) {
          if (stallCheckIntervalRef.current) {
            clearInterval(stallCheckIntervalRef.current);
            stallCheckIntervalRef.current = null;
          }
          attemptReconnect();
        }
      } catch {
        // Ignore errors
      }
    }, 5000);

    return () => {
      if (stallCheckIntervalRef.current) {
        clearInterval(stallCheckIntervalRef.current);
        stallCheckIntervalRef.current = null;
      }
    };
  }, [checkStalled, stallPaused, isReconnecting, attemptReconnect]);

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

    if (reconnectAttempts < maxAttempts) {
      attemptReconnect();
    } else {
      onGiveUp?.();
    }
  }, [reconnectAttempts, maxAttempts, initialRetryMax, initialRetryDelay, onReconnect, onGiveUp, attemptReconnect]);

  const handleStreamSuccess = useCallback(() => {
    hasConnectedRef.current = true;
    initialRetryCountRef.current = 0;
    if (initialRetryTimerRef.current) { clearTimeout(initialRetryTimerRef.current); initialRetryTimerRef.current = null; }
    setReconnectAttempts(0);
    setIsReconnecting(false);
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }
    if (countdownIntervalRef.current) { clearInterval(countdownIntervalRef.current); countdownIntervalRef.current = null; }
  }, []);

  const reset = useCallback(() => {
    hasConnectedRef.current = false;
    initialRetryCountRef.current = 0;
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
