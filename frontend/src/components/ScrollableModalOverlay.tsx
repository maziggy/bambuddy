import { useLayoutEffect, useRef, type HTMLAttributes } from 'react';
import './ScrollableModalOverlay.css';

interface ScrollableModalOverlayProps extends HTMLAttributes<HTMLDivElement> {
  zIndexClassName?: string;
}

/** Fixed backdrop for a single panel that may exceed the visible viewport. */
export function ScrollableModalOverlay({ children, className = '', zIndexClassName = 'z-50', ...props }: ScrollableModalOverlayProps) {
  const overlay = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const element = overlay.current;
    const viewport = window.visualViewport;
    if (!element || !viewport) return;

    // Mobile browsers can shrink or pan the visible area for the keyboard
    // without changing the layout viewport used by fixed elements.
    const updateViewport = () => {
      element.style.setProperty('--modal-top', `${viewport.offsetTop}px`);
      element.style.setProperty('--modal-height', `${viewport.height}px`);
    };

    updateViewport();
    viewport.addEventListener('resize', updateViewport);
    viewport.addEventListener('scroll', updateViewport);
    return () => {
      viewport.removeEventListener('resize', updateViewport);
      viewport.removeEventListener('scroll', updateViewport);
    };
  }, []);

  return (
    <div ref={overlay} className={`fixed scrollable-modal-overlay ${zIndexClassName} ${className}`} {...props}>
      {children}
    </div>
  );
}
