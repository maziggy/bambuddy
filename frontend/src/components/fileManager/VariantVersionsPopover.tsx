import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';

const PHONE_BREAKPOINT = 640;
const MIN_WIDTH = 340;
const MAX_WIDTH = 420;
const MARGIN = 8;
const GAP = 4;
// The click that opens the popover (and the focus it moves) can scroll the
// page; that scroll is not the user leaving.
const SCROLL_GRACE_MS = 150;

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useIsPhone(): boolean {
  const [phone, setPhone] = useState(() => window.innerWidth < PHONE_BREAKPOINT);
  useEffect(() => {
    const onResize = () => setPhone(window.innerWidth < PHONE_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return phone;
}

interface Placement {
  left: number;
  top: number;
  width: number;
  maxHeight: number;
}

// The versions of a group (#3628), next to the card instead of inside it, so
// opening them never moves the other cards. From 640px wide: a popover under
// the card (above it when there is no room), as wide as the card within
// 340-420px. Narrower: a bottom sheet with a backdrop.
//
// Rendered in a portal so no scrolling ancestor clips it. `onClose` is told
// whether focus belongs back on the toggle (Escape, Tab out, the close
// button) or went where the user clicked (outside click, scroll).
export function VariantVersionsPopover({
  id,
  name,
  anchorRef,
  toggleRef,
  onClose,
  layoutKey,
  children,
}: {
  id: string;
  name: string;
  anchorRef: React.RefObject<HTMLElement | null>;
  toggleRef: React.RefObject<HTMLElement | null>;
  onClose: (restoreFocus: boolean) => void;
  /** Changes when the content's height does, to place the popover again. */
  layoutKey: number;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const phone = useIsPhone();
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState<Placement | null>(null);
  const [resizeTick, setResizeTick] = useState(0);
  const phoneRef = useRef(phone);
  phoneRef.current = phone;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const close = useCallback((restoreFocus: boolean) => closeRef.current(restoreFocus), []);

  useEffect(() => {
    const onResize = () => setResizeTick((n) => n + 1);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useLayoutEffect(() => {
    const panel = panelRef.current;
    const anchor = anchorRef.current;
    if (phone || !panel || !anchor) {
      setPlacement(null);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    const width = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, rect.width));
    const left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - width - MARGIN));
    const cap = window.innerHeight * 0.6;
    // The versions' natural height (plus the border), whatever the last
    // placement capped the panel to.
    const height = Math.min((bodyRef.current?.scrollHeight ?? panel.scrollHeight) + 2, cap);
    const below = window.innerHeight - rect.bottom - GAP - MARGIN;
    const above = rect.top - GAP - MARGIN;
    let top: number;
    let maxHeight = cap;
    if (height <= below) {
      top = rect.bottom + GAP;
    } else if (height <= above) {
      top = rect.top - GAP - height;
    } else if (below >= above) {
      top = rect.bottom + GAP;
      maxHeight = Math.max(below, 120);
    } else {
      maxHeight = Math.max(above, 120);
      top = rect.top - GAP - Math.min(height, maxHeight);
    }
    setPlacement({ left, top, width, maxHeight });
  }, [phone, anchorRef, layoutKey, resizeTick]);

  // Focus goes into the popover when it opens.
  useEffect(() => {
    const first = panelRef.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus({ preventScroll: true });
  }, []);

  // A sheet covers the page, so the page does not scroll behind it.
  useEffect(() => {
    if (!phone) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  }, [phone]);

  useEffect(() => {
    const openedAt = Date.now();
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || toggleRef.current?.contains(target)) return;
      close(false);
    };
    const onScroll = (event: Event) => {
      // The sheet's backdrop covers the page: nothing to leave by scrolling.
      if (phoneRef.current) return;
      if (Date.now() - openedAt < SCROLL_GRACE_MS) return;
      // Scrolling the list of versions is not leaving it.
      if (rootRef.current?.contains(event.target as Node)) return;
      close(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        // A version's menu is open: Escape closes that menu (it listens
        // itself), the popover stays. The menu is the only fixed element in it.
        if (panelRef.current?.querySelector('.fixed')) return;
        close(true);
        return;
      }
      if (event.key !== 'Tab') return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const active = document.activeElement;
      if (!active || !panel.contains(active)) {
        close(false);
        return;
      }
      const leaving = event.shiftKey ? active === focusable[0] : active === focusable[focusable.length - 1];
      if (leaving) {
        // Tab out of the popover: back to the toggle, where it sits in the
        // reading order.
        event.preventDefault();
        close(true);
      }
    };
    const onDragStart = () => close(false);
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    document.addEventListener('scroll', onScroll, true);
    document.addEventListener('dragstart', onDragStart, true);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('scroll', onScroll, true);
      document.removeEventListener('dragstart', onDragStart, true);
    };
  }, [close, toggleRef]);

  // The popover is a portal child of the card, so its clicks would bubble to
  // the card (select, open) through the React tree.
  const stop = {
    onClick: (e: React.MouseEvent) => e.stopPropagation(),
    onDoubleClick: (e: React.MouseEvent) => e.stopPropagation(),
    onContextMenu: (e: React.MouseEvent) => e.stopPropagation(),
  };

  return createPortal(
    <div ref={rootRef} {...stop}>
      {phone && (
        <div
          data-testid="variant-versions-backdrop"
          className="fixed inset-0 z-[999] bg-black/60"
          onClick={() => close(false)}
        />
      )}
      <div
        ref={panelRef}
        id={id}
        role="dialog"
        aria-label={t('fileManager.variants.versionsOf', { name })}
        data-testid={phone ? 'variant-versions-sheet' : 'variant-versions-popover'}
        style={
          phone
            ? undefined
            : {
                position: 'fixed',
                left: placement?.left ?? 0,
                top: placement?.top ?? 0,
                width: placement?.width ?? MIN_WIDTH,
                maxHeight: placement?.maxHeight,
                visibility: placement ? 'visible' : 'hidden',
              }
        }
        className={
          phone
            ? 'fixed inset-x-0 bottom-0 z-[1000] max-h-[70vh] flex flex-col overflow-hidden rounded-t-2xl border border-b-0 border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-2xl'
            : 'z-[1000] flex flex-col overflow-hidden rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-xl'
        }
      >
        {phone && (
          <div className="flex items-center gap-2 px-4 py-3 border-b border-bambu-dark-tertiary">
            <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-white" title={name}>
              {t('fileManager.variants.versionsOf', { name })}
            </h3>
            <button
              type="button"
              aria-label={t('common.close')}
              onClick={() => close(true)}
              className="p-1.5 rounded flex-shrink-0 text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}
        <div ref={bodyRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
