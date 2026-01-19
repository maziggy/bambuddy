/**
 * Tests for the useLongPress hook.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useLongPress } from '../../hooks/useLongPress';

describe('useLongPress', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('calls onLongPress after delay', () => {
    const onLongPress = vi.fn();
    const onClick = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onClick, delay: 500 })
    );

    // Simulate mouse down
    act(() => {
      result.current.onMouseDown({} as React.MouseEvent);
    });

    // Fast forward past the delay
    act(() => {
      vi.advanceTimersByTime(600);
    });

    // Should trigger long press
    expect(onLongPress).toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('calls onClick for short press', () => {
    const onLongPress = vi.fn();
    const onClick = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onClick, delay: 500 })
    );

    // Simulate mouse down
    act(() => {
      result.current.onMouseDown({} as React.MouseEvent);
    });

    // Release before delay
    act(() => {
      vi.advanceTimersByTime(200);
      result.current.onMouseUp({} as React.MouseEvent);
    });

    // Should trigger click, not long press
    expect(onClick).toHaveBeenCalled();
    expect(onLongPress).not.toHaveBeenCalled();
  });

  it('cancels on mouse leave', () => {
    const onLongPress = vi.fn();
    const onClick = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onClick, delay: 500 })
    );

    // Simulate mouse down
    act(() => {
      result.current.onMouseDown({} as React.MouseEvent);
    });

    // Mouse leaves before delay
    act(() => {
      vi.advanceTimersByTime(200);
      result.current.onMouseLeave({} as React.MouseEvent);
    });

    // Continue past delay
    act(() => {
      vi.advanceTimersByTime(400);
    });

    // Neither should be called
    expect(onLongPress).not.toHaveBeenCalled();
    expect(onClick).not.toHaveBeenCalled();
  });

  it('uses default delay of 500ms', () => {
    const onLongPress = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress })
    );

    // Simulate mouse down
    act(() => {
      result.current.onMouseDown({} as React.MouseEvent);
    });

    // Just before default delay
    act(() => {
      vi.advanceTimersByTime(450);
    });
    expect(onLongPress).not.toHaveBeenCalled();

    // After default delay
    act(() => {
      vi.advanceTimersByTime(100);
    });
    expect(onLongPress).toHaveBeenCalled();
  });

  it('handles touch events', () => {
    const onLongPress = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress, delay: 500 })
    );

    // Simulate touch start
    act(() => {
      result.current.onTouchStart({} as React.TouchEvent);
    });

    // Fast forward past the delay
    act(() => {
      vi.advanceTimersByTime(600);
    });

    expect(onLongPress).toHaveBeenCalled();
  });

  it('cancels on touch end', () => {
    const onLongPress = vi.fn();
    const onClick = vi.fn();

    const { result } = renderHook(() =>
      useLongPress({ onLongPress, onClick, delay: 500 })
    );

    // Simulate touch start
    act(() => {
      result.current.onTouchStart({} as React.TouchEvent);
    });

    // End touch before delay
    act(() => {
      vi.advanceTimersByTime(200);
      result.current.onTouchEnd({} as React.TouchEvent);
    });

    expect(onClick).toHaveBeenCalled();
    expect(onLongPress).not.toHaveBeenCalled();
  });
});
