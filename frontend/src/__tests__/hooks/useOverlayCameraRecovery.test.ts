import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useOverlayCameraRecovery } from '../../hooks/useOverlayCameraRecovery';

describe('useOverlayCameraRecovery', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('renews the connection periodically without an error event', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    act(() => vi.advanceTimersByTime(59_999));
    expect(result.current.imageKey).toBe(initial);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.imageKey).not.toBe(initial);
    act(() => vi.advanceTimersByTime(60_000));
    expect(result.current.imageKey).toBe(initial + 2);
  });

  it('deduplicates image errors and retries after three seconds', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    act(() => { result.current.handleStreamError(); result.current.handleStreamError(); });
    act(() => vi.advanceTimersByTime(2_999));
    expect(result.current.imageKey).toBe(initial);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.imageKey).toBe(initial + 1);
  });

  it('gives a connection opened by an error retry a full renewal period', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    act(() => vi.advanceTimersByTime(56_000));
    act(() => result.current.handleStreamError());
    act(() => vi.advanceTimersByTime(3_000));
    expect(result.current.imageKey).toBe(initial + 1);
    act(() => vi.advanceTimersByTime(59_999));
    expect(result.current.imageKey).toBe(initial + 1);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.imageKey).toBe(initial + 2);
  });

  it('cancels a pending error retry when periodic renewal happens first', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    act(() => vi.advanceTimersByTime(59_000));
    act(() => result.current.handleStreamError());
    act(() => vi.advanceTimersByTime(1_000));
    expect(result.current.imageKey).toBe(initial + 1);
    act(() => vi.advanceTimersByTime(3_000));
    expect(result.current.imageKey).toBe(initial + 1);
  });

  it('does not schedule recovery while the camera is disabled', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(false));
    const initial = result.current.imageKey;
    act(() => result.current.handleStreamError());
    expect(vi.getTimerCount()).toBe(0);
    act(() => vi.advanceTimersByTime(120_000));
    expect(result.current.imageKey).toBe(initial);
  });

  it('cancels all recovery timers when hidden or unmounted', () => {
    const { result, rerender, unmount } = renderHook(
      ({ enabled }) => useOverlayCameraRecovery(enabled), { initialProps: { enabled: true } },
    );
    act(() => result.current.handleStreamError());
    rerender({ enabled: false });
    expect(vi.getTimerCount()).toBe(0);
    act(() => result.current.handleStreamError());
    expect(vi.getTimerCount()).toBe(0);
    rerender({ enabled: true });
    act(() => result.current.handleStreamError());
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
  // A detached img keeps its MJPEG connection open (measured in Chromium): the
  // old stream is closed before the key changes, and a moment before the new
  // one opens (a USB camera serves one reader).
  function withImg(result: { current: ReturnType<typeof useOverlayCameraRecovery> }) {
    const img = document.createElement('img');
    img.setAttribute('src', '/api/v1/printers/1/camera/stream?t=1');
    result.current.imgRef.current = img;
    return img;
  }

  it('closes the old stream shortly before opening the renewed one', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    const img = withImg(result);

    act(() => vi.advanceTimersByTime(59_800));
    expect(img.getAttribute('src')).toBeNull();
    expect(result.current.closing).toBe(true);
    expect(result.current.imageKey).toBe(initial);

    act(() => vi.advanceTimersByTime(200));
    expect(result.current.closing).toBe(false);
    expect(result.current.imageKey).toBe(initial + 1);
  });

  it('closes a failed stream until its retry', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const img = withImg(result);

    act(() => result.current.handleStreamError({ currentTarget: img } as never));

    expect(img.getAttribute('src')).toBeNull();
    expect(result.current.closing).toBe(true);
  });

  it('takes the error of a closed img for no failure', () => {
    const { result } = renderHook(() => useOverlayCameraRecovery(true));
    const initial = result.current.imageKey;
    const closed = document.createElement('img');

    act(() => result.current.handleStreamError({ currentTarget: closed } as never));
    act(() => vi.advanceTimersByTime(3_000));

    expect(result.current.imageKey).toBe(initial);
  });
});
