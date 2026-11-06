import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

import { OVERLAY_DIMENSIONS, type OverlayLayout } from '../utils/overlayLayout';

// Render at the browser-source resolution, then scale the whole composition.
// Text, camera and spacing therefore match in settings, OBS and standalone tabs.
export function OverlayFrame({ layout, preview = false, children }: {
  layout: OverlayLayout;
  preview?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0);
  const { width, height } = OVERLAY_DIMENSIONS[layout];
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setScale(Math.min(element.clientWidth / width, element.clientHeight / height));
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [width, height]);

  const canvasStyle: CSSProperties & Record<'--overlay-vmin' | '--overlay-height', string> = {
    width, height,
    '--overlay-vmin': '10.8px',
    '--overlay-height': `${height}px`,
    position: 'absolute', top: '50%', left: '50%',
    transform: `translate(-50%, -50%) scale(${scale})`,
    transformOrigin: 'center',
  };
  return (
    <div ref={ref} className={preview ? 'relative w-full overflow-hidden' : 'fixed inset-0 overflow-hidden'}
      style={preview ? { aspectRatio: `${width} / ${height}` } : undefined}>
      <div style={canvasStyle}>{children}</div>
    </div>
  );
}
