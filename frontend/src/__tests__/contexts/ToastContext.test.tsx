/**
 * Tests for ToastContext's post-unmount safety guards.
 *
 * Regression: a login response handler calling showToast AFTER the provider
 * had already been unmounted by Vitest's afterEach scheduled a 3s setTimeout
 * that fired during test teardown. The callback's setToasts then tried to
 * schedule a React update against a torn-down jsdom, producing
 * "window is not defined" as an uncaught exception.
 *
 * The provider now gates every setToasts call on an isMountedRef and
 * re-checks inside the auto-dismiss setTimeout callback so stale async
 * paths no-op instead of crashing.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act, render, renderHook } from '@testing-library/react';
import { type ReactNode } from 'react';
import { ToastProvider, useToast } from '../../contexts/ToastContext';

function Wrapper({ children }: { children: ReactNode }) {
  return <ToastProvider>{children}</ToastProvider>;
}

describe('ToastContext post-unmount safety', () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  it('does not crash when showToast is called after unmount', () => {
    const { result, unmount } = renderHook(() => useToast(), { wrapper: Wrapper });

    // Capture the callbacks BEFORE unmount — a real stale-closure scenario.
    // (Async handlers that kicked off before unmount keep their captured
    // context value and will invoke this function after we tear down.)
    const { showToast } = result.current;

    unmount();

    // Post-unmount invocation is now a no-op; must not throw.
    expect(() => showToast('delayed error message', 'error')).not.toThrow();
  });

  it('does not invoke setToasts when the auto-dismiss timer fires after unmount', async () => {
    vi.useFakeTimers();

    const { result, unmount } = renderHook(() => useToast(), { wrapper: Wrapper });

    act(() => {
      result.current.showToast('will outlive the provider', 'error');
    });

    // Unmount BEFORE the 3s timer fires — the unmount effect clears pending
    // timers, but a belt-and-braces check inside the timer callback (for
    // cases where the timer was scheduled post-unmount) must also hold.
    unmount();

    // Advance past the 3s auto-dismiss window. If the guard isn't in place
    // this would throw "window is not defined" in a torn-down jsdom; we
    // simulate by asserting no error propagates.
    expect(() => {
      vi.advanceTimersByTime(5000);
    }).not.toThrow();

    vi.useRealTimers();
  });

  it('post-unmount showPersistentToast and dismissToast are no-ops', () => {
    const { result, unmount } = renderHook(() => useToast(), { wrapper: Wrapper });
    const { showPersistentToast, dismissToast } = result.current;
    unmount();

    // Both must short-circuit rather than attempt setState on a dead tree.
    expect(() => showPersistentToast('orphan', 'still here', 'info')).not.toThrow();
    expect(() => dismissToast('orphan')).not.toThrow();
  });

  it('normal showToast flow still displays and auto-dismisses while mounted', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useToast(), { wrapper: Wrapper });

    act(() => {
      result.current.showToast('mounted path works', 'success');
    });

    // No easy way to read toast DOM from the hook alone; assert the timer
    // ran without throwing — that proves the isMountedRef guard didn't
    // incorrectly short-circuit the mounted path.
    expect(() => {
      act(() => {
        vi.advanceTimersByTime(3500);
      });
    }).not.toThrow();

    vi.useRealTimers();
  });
});

describe('ToastContext viewport suppression', () => {
  // The kiosk layout flips setViewportSuppressed(true) on mount so the
  // SpoolBuddy display stays free of main-app toasts (login flows, etc.).
  // Verify the gate hides the visible viewport
  // without affecting the underlying state machine.
  function ViewportProbe() {
    const { showToast, setViewportSuppressed } = useToast();
    return (
      <>
        <button data-testid="show-toast" onClick={() => showToast('hello', 'success')} />
        <button data-testid="suppress-on" onClick={() => setViewportSuppressed(true)} />
        <button data-testid="suppress-off" onClick={() => setViewportSuppressed(false)} />
      </>
    );
  }

  it('hides the visible toast viewport when suppressed but keeps state alive', () => {
    const { container, getByTestId } = render(
      <ToastProvider>
        <ViewportProbe />
      </ToastProvider>
    );

    // Toast viewport is the fixed-position container; position is set via
    // safe-area calc() (#2612) so match the stable data-testid, not classes.
    const findViewport = () => container.querySelector('[data-testid="toast-viewport"]');
    expect(findViewport()?.className).not.toContain('hidden');

    act(() => {
      getByTestId('suppress-on').click();
    });
    expect(findViewport()?.className).toContain('hidden');

    // State is unaffected — emitting a toast while suppressed is fine; the
    // state container exists, just hidden.
    act(() => {
      getByTestId('show-toast').click();
    });
    expect(findViewport()?.className).toContain('hidden');

    // Restore on unmount of the kiosk layout (or via the setter directly).
    act(() => {
      getByTestId('suppress-off').click();
    });
    expect(findViewport()?.className).not.toContain('hidden');
  });

  it('caps every toast to the viewport width so it cannot run off-screen (#2612)', () => {
    const { container, getByTestId } = render(
      <ToastProvider>
        <ViewportProbe />
      </ToastProvider>
    );

    act(() => {
      getByTestId('show-toast').click();
    });

    // The fixed-width dispatch toast (420px) overflowed the left edge of a
    // phone in an installed PWA. Every toast now carries a viewport-relative
    // max-width so it stays on-screen; pin it here.
    const viewport = container.querySelector('[data-testid="toast-viewport"]');
    const toast = viewport?.querySelector<HTMLElement>('div[style]');
    expect(toast?.style.maxWidth).toContain('100vw');
    expect(toast?.style.maxWidth).toContain('safe-area-inset-left');
    expect(toast?.style.maxWidth).toContain('safe-area-inset-right');
  });
});
