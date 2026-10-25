import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const PREVIEW_SIZE = 256;
const GAP = 8;
const EDGE_MARGIN = 8;

interface ThumbnailHoverPreviewProps {
  /** The image to enlarge; null shows nothing on hover. */
  src: string | null;
  children: ReactNode;
  className?: string;
}

/**
 * A thumbnail that shows a larger copy of itself while the pointer rests on it
 * (#3313). Desktop only: a touch screen has no hover. The preview is portalled
 * and fixed-positioned, so a parent with overflow:hidden (a batch group's card)
 * cannot clip it. It sits to the right of the thumbnail, or to the left when
 * there is no room, and is kept inside the viewport vertically.
 */
export function ThumbnailHoverPreview({ src, children, className }: ThumbnailHoverPreviewProps) {
  const anchorRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);

  const show = useCallback(() => {
    if (!src || !window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    const rightOf = rect.right + GAP;
    const left = rightOf + PREVIEW_SIZE + EDGE_MARGIN <= window.innerWidth
      ? rightOf
      : Math.max(EDGE_MARGIN, rect.left - GAP - PREVIEW_SIZE);
    const top = Math.max(EDGE_MARGIN, Math.min(rect.top, window.innerHeight - PREVIEW_SIZE - EDGE_MARGIN));
    setPosition({ left, top });
  }, [src]);

  const hide = useCallback(() => setPosition(null), []);

  // A scroll moves the thumbnail out from under the fixed preview.
  useEffect(() => {
    if (!position) return;
    window.addEventListener('scroll', hide, true);
    return () => window.removeEventListener('scroll', hide, true);
  }, [position, hide]);

  return (
    <div ref={anchorRef} className={className} onMouseEnter={show} onMouseLeave={hide}>
      {children}
      {position && src && createPortal(
        <img
          src={src}
          alt=""
          data-testid="thumbnail-hover-preview"
          style={{ left: position.left, top: position.top, width: PREVIEW_SIZE, height: PREVIEW_SIZE }}
          className="fixed z-[60] pointer-events-none object-cover rounded-lg shadow-2xl border-2 border-bambu-dark-tertiary bg-bambu-dark"
        />,
        document.body,
      )}
    </div>
  );
}
