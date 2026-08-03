import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useCancellableTimeout } from '../../hooks/useCancellableTimeout';

describe('useCancellableTimeout', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('runs the callback after the delay', () => {
    const fn = vi.fn();
    const { result } = renderHook(() => useCancellableTimeout());
    act(() => result.current.schedule(fn, 1500));
    expect(fn).not.toHaveBeenCalled();
    act(() => void vi.advanceTimersByTime(1500));
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('does not run the callback after unmount', () => {
    // The bug this exists for: a modal that defers its own close by 1.5s fired
    // setState and onClose after the component was gone — which throws outright
    // once the DOM around it has been torn down.
    const fn = vi.fn();
    const { result, unmount } = renderHook(() => useCancellableTimeout());
    act(() => result.current.schedule(fn, 1500));
    unmount();
    act(() => void vi.advanceTimersByTime(5000));
    expect(fn).not.toHaveBeenCalled();
  });

  it('cancel() stops a pending callback', () => {
    const fn = vi.fn();
    const { result } = renderHook(() => useCancellableTimeout());
    act(() => result.current.schedule(fn, 1000));
    act(() => result.current.cancel());
    act(() => void vi.advanceTimersByTime(2000));
    expect(fn).not.toHaveBeenCalled();
  });

  it('scheduling again replaces the pending callback', () => {
    const first = vi.fn();
    const second = vi.fn();
    const { result } = renderHook(() => useCancellableTimeout());
    act(() => result.current.schedule(first, 1000));
    act(() => result.current.schedule(second, 1000));
    act(() => void vi.advanceTimersByTime(1000));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });

  it('is safe to cancel when nothing is pending', () => {
    const { result } = renderHook(() => useCancellableTimeout());
    expect(() => act(() => result.current.cancel())).not.toThrow();
  });
});
