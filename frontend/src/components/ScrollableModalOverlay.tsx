import { useLayoutEffect, useRef, type ReactNode, type MouseEventHandler } from 'react';
import './ScrollableModalOverlay.css';

interface ScrollableModalOverlayProps {
  children: ReactNode;
  className: string;
  onClick?: MouseEventHandler<HTMLDivElement>;
}

/** Fixed backdrop for a single panel that may exceed the visible viewport. */
export function ScrollableModalOverlay({ children, className, onClick }: ScrollableModalOverlayProps) {
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
    <div ref={overlay} className={`scrollable-modal-overlay ${className}`} onClick={onClick}>
      {children}
    </div>
  );
}
