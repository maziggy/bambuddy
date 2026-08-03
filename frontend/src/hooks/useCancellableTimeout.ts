import { useCallback, useEffect, useRef } from 'react';

/**
 * setTimeout that cannot outlive the component that scheduled it.
 *
 * Modals here defer their own close by a second or more so the printer has
 * time to process the command that was just sent. A plain setTimeout for that
 * keeps a reference to setState and to the parent's onClose, and fires whether
 * or not the modal is still mounted — closing an already-dismissed dialog, or
 * throwing outright once the surrounding environment is gone ("window is not
 * defined" when a test's DOM is torn down before the timer fires).
 *
 * Returns a schedule function. Scheduling again replaces any pending timer, and
 * unmounting cancels it.
 */
export function useCancellableTimeout() {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancel = useCallback(() => {
    if (timer.current !== null) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const schedule = useCallback((fn: () => void, ms: number) => {
    cancel();
    timer.current = setTimeout(() => {
      timer.current = null;
      fn();
    }, ms);
  }, [cancel]);

  useEffect(() => cancel, [cancel]);

  return { schedule, cancel };
}
