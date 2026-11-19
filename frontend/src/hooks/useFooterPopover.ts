import { useEffect, useRef, useState } from 'react';

/**
 * Open/close state of a popover anchored above a sidebar footer button and
 * rendered in a portal (the collapsed sidebar's footer scrolls, which would
 * clip it). Shared by the account menu and the language menu (#3623).
 *
 * It opens above the button, kept inside the window; it closes on an outside
 * click, and on Escape or a second click on the button, which return focus to
 * the button. When it opens, its first select takes focus.
 */
export function useFooterPopover(width: number) {
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; bottom: number } | null>(null);

  const close = (restoreFocus = false) => {
    setPosition(null);
    if (restoreFocus) buttonRef.current?.focus();
  };

  const toggle = () => {
    if (position) {
      close(true);
      return;
    }
    const rect = buttonRef.current?.getBoundingClientRect();
    if (!rect) return;
    setPosition({
      left: Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)),
      bottom: window.innerHeight - rect.top + 4,
    });
  };

  const isOpen = !!position;
  useEffect(() => {
    if (isOpen) menuRef.current?.querySelector('select')?.focus();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!menuRef.current?.contains(target) && !buttonRef.current?.contains(target)) setPosition(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setPosition(null);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [isOpen]);

  const style = position ? { position: 'fixed' as const, left: position.left, bottom: position.bottom, width } : undefined;

  return { buttonRef, menuRef, isOpen, style, toggle, close };
}
