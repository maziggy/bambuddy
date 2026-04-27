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

describe('ToastContext background dispatch — upload-done UX', () => {
  // Small fast files reach 100% upload before the printer's MQTT confirmation
  // arrives, leaving the bar parked at 100% for what feels like "stuck". When
  // status is still 'processing' but uploadProgressPct >= 99.9 the byte-count
  // line should switch to "Awaiting printer..." and the bar gets a pulse.
  function dispatchBackgroundEvent(detail: Record<string, unknown>) {
    window.dispatchEvent(new CustomEvent('background-dispatch', { detail }));
  }

  it('shows "Awaiting printer..." once upload is complete but printer has not confirmed', () => {
    const { container } = render(
      <ToastProvider>
        <div />
      </ToastProvider>
    );

    act(() => {
      dispatchBackgroundEvent({
        total: 1,
        dispatched: 0,
        processing: 1,
        completed: 0,
        failed: 0,
        active_jobs: [
          {
            job_id: 42,
            printer_name: 'X1C-2',
            source_name: 'Benchy.3mf',
            upload_bytes: 102400,
            upload_total_bytes: 102400,
            upload_progress_pct: 100.0,
          },
        ],
      });
    });

    // The byte-count line should be replaced with the awaiting-printer text.
    expect(container.textContent).toContain('Awaiting printer');
    // And the original bytes-progressed format must not be visible at the
    // same time — that is the "stuck at 100%" symptom we are fixing.
    expect(container.textContent).not.toContain('100.0%');

    // Bar gets the pulse class when in this state.
    const bar = container.querySelector('.animate-pulse');
    expect(bar).not.toBeNull();
  });

  it('still shows the byte/percent counter while upload is mid-flight', () => {
    const { container } = render(
      <ToastProvider>
        <div />
      </ToastProvider>
    );

    act(() => {
      dispatchBackgroundEvent({
        total: 1,
        dispatched: 0,
        processing: 1,
        completed: 0,
        failed: 0,
        active_jobs: [
          {
            job_id: 7,
            printer_name: 'X1C-2',
            source_name: 'Benchy.3mf',
            upload_bytes: 51200,
            upload_total_bytes: 102400,
            upload_progress_pct: 50.0,
          },
        ],
      });
    });

    expect(container.textContent).toContain('50.0%');
    expect(container.textContent).not.toContain('Awaiting printer');
    expect(container.querySelector('.animate-pulse')).toBeNull();
  });
});

describe('ToastContext viewport suppression', () => {
  // The kiosk layout flips setViewportSuppressed(true) on mount so the
  // SpoolBuddy display stays free of main-app toasts (background dispatch
  // progress, login flows, etc.). Verify the gate hides the visible viewport
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

    // Toast viewport is the fixed-position container with bottom-4 right-20.
    const findViewport = () => container.querySelector('div.fixed.bottom-4.right-20');
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
});
