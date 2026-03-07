/**
 * Tests for the useStreamReconnect hook.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useStreamReconnect } from '../../hooks/useStreamReconnect';

describe('useStreamReconnect', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function setup(overrides: Partial<Parameters<typeof useStreamReconnect>[0]> = {}) {
    const onReconnect = vi.fn();
    const onGiveUp = vi.fn();
    return renderHook(() =>
      useStreamReconnect({
        maxAttempts: 3,
        initialDelay: 1000,
        maxDelay: 8000,
        onReconnect,
        onGiveUp,
        ...overrides,
      })
    );
  }

  describe('initial state', () => {
    it('starts with zero reconnect attempts', () => {
      const { result } = setup();
      expect(result.current.reconnectAttempts).toBe(0);
    });

    it('starts not reconnecting', () => {
      const { result } = setup();
      expect(result.current.isReconnecting).toBe(false);
    });

    it('starts with zero countdown', () => {
      const { result } = setup();
      expect(result.current.reconnectCountdown).toBe(0);
    });
  });

  describe('handleStreamSuccess', () => {
    it('resets reconnect state', () => {
      const { result } = setup();
      act(() => result.current.handleStreamSuccess());
      expect(result.current.reconnectAttempts).toBe(0);
      expect(result.current.isReconnecting).toBe(false);
    });
  });

  describe('handleStreamError', () => {
    it('triggers initial fast retry before first success', () => {
      const onReconnect = vi.fn();
      const { result } = setup({ onReconnect, initialRetryDelay: 500, initialRetryMax: 3 });

      act(() => result.current.handleStreamError());

      // Should schedule a fast retry, not visible reconnecting
      expect(result.current.isReconnecting).toBe(false);

      // Fast forward past initialRetryDelay
      act(() => { vi.advanceTimersByTime(600); });
      expect(onReconnect).toHaveBeenCalledTimes(1);
    });

    it('calls onGiveUp after exhausting initial retries without success', () => {
      const onGiveUp = vi.fn();
      const onReconnect = vi.fn();
      const { result } = setup({ onReconnect, onGiveUp, initialRetryDelay: 100, initialRetryMax: 2 });

      // Exhaust initial retries
      for (let i = 0; i < 2; i++) {
        act(() => result.current.handleStreamError());
        act(() => { vi.advanceTimersByTime(200); });
      }

      // Third error should give up
      act(() => result.current.handleStreamError());
      expect(onGiveUp).toHaveBeenCalled();
    });

    it('uses exponential backoff after first connection', () => {
      const onReconnect = vi.fn();
      const { result } = setup({ onReconnect, initialDelay: 1000, maxDelay: 8000 });

      // Simulate first successful connection
      act(() => result.current.handleStreamSuccess());

      // Now trigger error — should enter reconnecting
      act(() => result.current.handleStreamError());
      expect(result.current.isReconnecting).toBe(true);
      expect(result.current.reconnectCountdown).toBe(1); // ceil(1000/1000)

      // Advance past first delay
      act(() => { vi.advanceTimersByTime(1100); });
      expect(onReconnect).toHaveBeenCalledTimes(1);
      expect(result.current.reconnectAttempts).toBe(1);
    });
  });

  describe('reset', () => {
    it('clears all reconnect state', () => {
      const onReconnect = vi.fn();
      const { result } = setup({ onReconnect });

      // Get into reconnecting state
      act(() => result.current.handleStreamSuccess());
      act(() => result.current.handleStreamError());
      expect(result.current.isReconnecting).toBe(true);

      act(() => result.current.reset());
      expect(result.current.reconnectAttempts).toBe(0);
      expect(result.current.isReconnecting).toBe(false);
      expect(result.current.reconnectCountdown).toBe(0);
    });
  });

  describe('returned API', () => {
    it('returns all expected functions', () => {
      const { result } = setup();
      expect(typeof result.current.handleStreamError).toBe('function');
      expect(typeof result.current.handleStreamSuccess).toBe('function');
      expect(typeof result.current.reset).toBe('function');
      expect(typeof result.current.cleanup).toBe('function');
    });
  });
});
