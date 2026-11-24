import { useEffect, useRef } from 'react';

/** Keep keyboard navigation in the RFID chooser, including when opened over the spool editor. */
export function useRfidDialog(open: boolean, pending: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  const busy = useRef(pending);
  busy.current = pending;
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const trigger = document.activeElement;
    dialog.focus();
    const onKey = (event: KeyboardEvent) => {
      if (!dialog.contains(document.activeElement)) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!busy.current) close.current();
      } else if (event.key === 'Tab') {
        const controls = dialog.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled)');
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!first || (event.shiftKey && [dialog, first].includes(document.activeElement as HTMLElement)) ||
            (!event.shiftKey && document.activeElement === last)) {
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        }
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus();
    };
  }, [open]);
  return ref;
}
