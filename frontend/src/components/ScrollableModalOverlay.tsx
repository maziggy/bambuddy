import { useLayoutEffect, useState, type HTMLAttributes } from 'react';

interface ScrollableModalOverlayProps extends HTMLAttributes<HTMLDivElement> {
  /** Stacking class; defaults to z-50 like the overlays it replaces. */
  zIndexClassName?: string;
}

type VisibleArea = { top: number; height: number };

function readVisibleArea(): VisibleArea | null {
  const viewport = window.visualViewport;
  return viewport ? { top: viewport.offsetTop, height: viewport.height } : null;
}

/**
 * Backdrop for one dialog panel that may be taller than the screen (#3664).
 *
 * A short panel stays centred; a tall one scrolls inside the backdrop from
 * its top to its actions, and the swipe stops at the backdrop instead of
 * scrolling the page behind it. On a phone the on-screen keyboard shrinks
 * only the visible area, not the one fixed elements are laid out in, so the
 * backdrop follows `visualViewport` to keep the actions above the keyboard.
 */
export function ScrollableModalOverlay({
  children,
  className = '',
  zIndexClassName = 'z-50',
  style,
  ...props
}: ScrollableModalOverlayProps) {
  const [area, setArea] = useState<VisibleArea | null>(readVisibleArea);

  useLayoutEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const update = () => setArea(readVisibleArea());
    update();
    viewport.addEventListener('resize', update);
    viewport.addEventListener('scroll', update);
    return () => {
      viewport.removeEventListener('resize', update);
      viewport.removeEventListener('scroll', update);
    };
  }, []);

  return (
    <div
      {...props}
      className={`fixed inset-0 flex flex-col items-center overflow-y-auto overscroll-contain p-4 pb-[max(1rem,env(safe-area-inset-bottom))] [&>*]:my-auto [&>*]:shrink-0 ${zIndexClassName} ${className}`}
      style={area ? { ...style, top: area.top, bottom: 'auto', height: area.height } : style}
    >
      {children}
    </div>
  );
}
