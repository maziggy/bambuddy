export interface PopoverPosition {
  top: number;
  left: number;
  /** Which side of the trigger the popover landed on. */
  placement: 'below' | 'above';
  /**
   * Viewport y of the popover edge facing the trigger: the top edge for
   * 'below' (equals `top`), the bottom edge for 'above'. Anchoring an
   * 'above' popover by this edge (CSS `bottom`) lets late-appearing content
   * grow it upward, keeping it glued to the trigger.
   */
  anchorY: number;
  /**
   * X-offset within the popover for an anchor arrow pointing at the
   * trigger's center, clamped inside the popover's rounded corners.
   */
  arrowLeft: number;
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
  // iOS Safari's bottom URL/toolbar overlay is excluded from window.innerHeight
  // but included in the layout viewport, so a popover anchored against
  // innerHeight gets its footer clipped behind the toolbar (#1669, iPhone 17
  // Safari). visualViewport reflects the actually-visible area when the
  // toolbar is up; fall back to innerHeight where it isn't available.
  const visualHeight =
    typeof window !== 'undefined' && window.visualViewport
      ? window.visualViewport.height
      : typeof window !== 'undefined'
        ? window.innerHeight
        : 0;
  const {
    triggerRect,
    popoverWidth,
    estimatedHeight,
    viewportHeight = visualHeight,
    viewportWidth = window.innerWidth,
    margin = 8,
    gap = 4,
    horizontalAlign = 'right',
  } = opts;

  // Vertical: prefer below; flip above when below can't fit the full height
  // and above either fits it or offers more room. Top clamps to the margin
  // so the popover never runs off the top; callers cap the height and
  // scroll internally.
  let top = triggerRect.bottom + gap;
  let placement: PopoverPosition['placement'] = 'below';
  const belowSpace = viewportHeight - margin - top;
  if (belowSpace < estimatedHeight) {
    const aboveSpace = triggerRect.top - gap - margin;
    if (aboveSpace >= estimatedHeight || aboveSpace > belowSpace) {
      top = Math.max(margin, triggerRect.top - gap - estimatedHeight);
      placement = 'above';
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

  const anchorY = placement === 'above' ? triggerRect.top - gap : top;
  // Keep the arrow clear of the popover's rounded corners.
  const arrowLeft = Math.max(14, Math.min(popoverWidth - 14, triggerCenter - left));

  return { top, left, placement, anchorY, arrowLeft };
}
