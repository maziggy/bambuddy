import { useEffect, useState } from 'react';

interface TourSpotlightProps {
  /** Anchor element to highlight. Null fades the spotlight off — the dimmed
   *  backdrop is preserved for the no-anchor outro step. */
  anchor: Element | null;
}

const SPOTLIGHT_PADDING = 8;

/**
 * Dimmed-page backdrop with a transparent cutout around the anchor element.
 *
 * Uses `box-shadow: 0 0 0 9999px` to paint the dark area around a transparent
 * div — far cheaper than an SVG mask and animates smoothly. `pointer-events:
 * none` so clicks pass through to the underlying page (we do not gate the
 * user; the spotlight is a visual cue, not a wall).
 *
 * Recomputes the anchor rect on `resize` and `scroll` so the cutout follows
 * the element through layout changes.
 */
export function TourSpotlight({ anchor }: TourSpotlightProps) {
  const [rect, setRect] = useState<DOMRect | null>(null);

  useEffect(() => {
    if (!anchor) {
      setRect(null);
      return;
    }

    const update = () => setRect(anchor.getBoundingClientRect());
    update();

    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchor]);

  if (!rect) {
    return (
      <div
        className="fixed inset-0 bg-black/60 z-[100] pointer-events-none"
        aria-hidden="true"
      />
    );
  }

  return (
    <div
      data-testid="tour-spotlight"
      aria-hidden="true"
      className="fixed rounded-lg z-[100] pointer-events-none transition-all duration-200"
      style={{
        top: rect.top - SPOTLIGHT_PADDING,
        left: rect.left - SPOTLIGHT_PADDING,
        width: rect.width + SPOTLIGHT_PADDING * 2,
        height: rect.height + SPOTLIGHT_PADDING * 2,
        boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.6)',
      }}
    />
  );
}
