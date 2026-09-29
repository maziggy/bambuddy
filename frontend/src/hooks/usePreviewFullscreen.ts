import { useRef, type RefObject } from 'react';
import { useElementFullscreen } from './useElementFullscreen';

export interface PreviewFullscreen {
  panelRef: RefObject<HTMLDivElement | null>;
  isFullscreen: boolean;
  toggleFullscreen: () => void;
}

/**
 * Fullscreen state for a file preview panel (#2976).
 *
 * Held by the modal rather than by `PreviewModalShell`, which renders the
 * toggle: the 3D viewer drives layout effects off `isFullscreen` — in
 * fullscreen the plate list splits off the canvas — so the flag has to be
 * readable outside the shell's own subtree.
 */
export function usePreviewFullscreen(): PreviewFullscreen {
  const panelRef = useRef<HTMLDivElement>(null);
  const { isFullscreen, toggleFullscreen } = useElementFullscreen(panelRef);
  return { panelRef, isFullscreen, toggleFullscreen };
}
