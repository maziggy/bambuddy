export interface PopoverPosition {
  top: number;
  left: number;
}

interface RectLike {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface ComputePopoverPositionOpts {
  /** Trigger element's bounding rect (viewport coordinates). */
  triggerRect: RectLike;
  /** Popover width in CSS pixels. */
  popoverWidth: number;
  /**
   * Estimated popover height in CSS pixels. Used to detect bottom-edge
   * overflow so we can flip above the trigger. A conservative over-estimate
   * is preferable to an under-estimate — over-estimating just flips slightly
   * sooner, under-estimating leaves the popover clipped off the viewport.
   */
  estimatedHeight: number;
  /** Viewport height. Defaults to window.innerHeight. Injectable for tests. */
  viewportHeight?: number;
  /** Viewport width. Defaults to window.innerWidth. Injectable for tests. */
  viewportWidth?: number;
  /** Margin to keep between the popover and the viewport edges. */
  margin?: number;
  /** Gap between the trigger and the popover. */
  gap?: number;
  /** Horizontal alignment relative to the trigger. Defaults to right-aligned. */
  horizontalAlign?: 'right' | 'center';
}

/**
 * Compute fixed-positioning coordinates for a popover anchored to a trigger.
 *
 * Default placement is BELOW the trigger, right-aligned to the trigger. Flips
 * to ABOVE the trigger when below would overflow the viewport (#1447 — the
 * AMS drying popover on the printer card sits at the bottom of the AMS row
 * and was rendering off the bottom of the viewport with the Start button
 * unreachable on smaller screens).
 *
 * Horizontal axis right-aligns to triggerRect.right and clamps to the
 * viewport with the configured margin so a trigger near the right edge
 * doesn't push the popover off-screen.
 */
export function computePopoverPosition(opts: ComputePopoverPositionOpts): PopoverPosition {
  const {
    triggerRect,
    popoverWidth,
    estimatedHeight,
    viewportHeight = window.innerHeight,
    viewportWidth = window.innerWidth,
    margin = 8,
    gap = 4,
    horizontalAlign = 'right',
  } = opts;

  // Vertical: prefer below, flip to above only when below overflows AND
  // above would actually fit. If neither fits (a popover taller than the
  // viewport), stay below — at least the top of the popover is visible
  // and the user can scroll inside it, which is better than flipping to a
  // top-clipped position where the action buttons might also be unreachable.
  let top = triggerRect.bottom + gap;
  const wouldOverflowBottom = top + estimatedHeight > viewportHeight - margin;
  if (wouldOverflowBottom) {
    const aboveTop = triggerRect.top - gap - estimatedHeight;
    if (aboveTop >= margin) {
      top = aboveTop;
    }
  }

  // Horizontal: align to trigger; clamp to viewport bounds.
  const triggerCenter = triggerRect.left + ((triggerRect.right - triggerRect.left) / 2);
  let left = horizontalAlign === 'center'
    ? triggerCenter - (popoverWidth / 2)
    : triggerRect.right - popoverWidth;
  if (left < margin) {
    left = margin;
  } else if (left + popoverWidth > viewportWidth - margin) {
    left = Math.max(margin, viewportWidth - popoverWidth - margin);
  }

  return { top, left };
}
