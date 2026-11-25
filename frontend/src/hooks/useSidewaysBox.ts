import { useLayoutEffect, useState, type RefObject } from 'react';

/**
 * The size in px of the box a camera image is shown in, measured only while
 * the camera is turned sideways (90 or 270 degrees). CSS rotate() does not
 * change layout, so a quarter-turned image must be laid out with the box's
 * height as its width and vice versa (#3362).
 *
 * Measured rather than written as 100cqh/100cqw because container units need
 * Chromium 105+, which older OBS browser sources and kiosk browsers don't
 * have, and viewport units are wrong for anything smaller than the window.
 */
export function useSidewaysBox(ref: RefObject<HTMLElement | null>, active: boolean) {
  const [box, setBox] = useState<{ width: number; height: number } | null>(null);
  // Layout effect: measured before paint, so a sideways camera never shows a
  // frame at the unswapped size.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!active || !el) return;
    const measure = () => {
      // Layout dimensions, so a transform scale on an ancestor is left out.
      const width = el.clientWidth;
      const height = el.clientHeight;
      if (width > 0 && height > 0) {
        setBox((prev) => (prev && prev.width === width && prev.height === height ? prev : { width, height }));
      }
    };
    measure();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, active]);
  return active ? box : null;
}

/** Whether a camera rotation turns the image on its side. */
export function isSideways(rotation: number | null | undefined): boolean {
  return Math.abs((rotation ?? 0) % 180) === 90;
}
