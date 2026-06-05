import { describe, it, expect } from 'vitest';
import { computePopoverPosition } from '../../utils/popoverPosition';

/**
 * Tests for #1447: the AMS drying popover was rendering off the bottom of
 * the viewport with the Start button unreachable. The new helper must:
 * - keep the popover below when below fits
 * - flip above when below would overflow AND above fits
 * - stay below (degraded) when neither side fits
 * - clamp the horizontal position so a trigger near the viewport's right
 *   edge doesn't push the popover off-screen.
 */
describe('computePopoverPosition (#1447)', () => {
  // Trigger positioned in the middle of a 1024x768 viewport.
  const middleTrigger = { top: 300, bottom: 320, left: 400, right: 440 };
  const viewport = { viewportWidth: 1024, viewportHeight: 768 };

  it('places the popover below the trigger when below has room', () => {
    const pos = computePopoverPosition({
      triggerRect: middleTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      ...viewport,
    });
    expect(pos.top).toBe(middleTrigger.bottom + 4); // 324
  });

  it('right-aligns the popover to the trigger by default', () => {
    const pos = computePopoverPosition({
      triggerRect: middleTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      ...viewport,
    });
    expect(pos.left).toBe(middleTrigger.right - 240); // 200
  });

  it('can center-align the popover to the trigger', () => {
    const pos = computePopoverPosition({
      triggerRect: middleTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      horizontalAlign: 'center',
      ...viewport,
    });
    expect(pos.left).toBe(300);
  });

  it('flips above when the popover would overflow the bottom of the viewport', () => {
    // Trigger near the bottom — bottom=700 + gap 4 + height 320 = 1024 > 768.
    const bottomTrigger = { top: 680, bottom: 700, left: 400, right: 440 };
    const pos = computePopoverPosition({
      triggerRect: bottomTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      ...viewport,
    });
    // Above placement: trigger.top - gap - height = 680 - 4 - 320 = 356.
    expect(pos.top).toBe(356);
  });

  it('stays below when neither below nor above can fully fit (degraded)', () => {
    // A popover taller than the viewport itself can never fit anywhere. Stay
    // below so the user at least sees the top of the popover and can scroll
    // through it — flipping to a top-clipped position would lose visibility
    // of the action buttons at the bottom of the popover too.
    const tallPopover = { estimatedHeight: 900 };
    const trigger = { top: 380, bottom: 400, left: 400, right: 440 };
    const pos = computePopoverPosition({
      triggerRect: trigger,
      popoverWidth: 240,
      ...tallPopover,
      ...viewport,
    });
    expect(pos.top).toBe(trigger.bottom + 4);
  });

  it('clamps horizontally when trigger sits near the right viewport edge', () => {
    // Trigger.right=1020, popoverWidth=240. Default left would be 780; the
    // popover would extend to 1020 which is within viewport=1024 minus the
    // 8px margin -> 1016, so it overflows by 4px. Clamp pushes it left.
    const rightEdgeTrigger = { top: 100, bottom: 120, left: 980, right: 1020 };
    const pos = computePopoverPosition({
      triggerRect: rightEdgeTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      ...viewport,
    });
    expect(pos.left).toBeLessThanOrEqual(1024 - 240 - 8); // 776
    expect(pos.left).toBeGreaterThanOrEqual(8);
  });

  it('clamps horizontally when trigger sits near the left viewport edge', () => {
    // Trigger.right=120, popoverWidth=240. Default left would be -120 (off
    // viewport). Clamp to the margin.
    const leftEdgeTrigger = { top: 100, bottom: 120, left: 80, right: 120 };
    const pos = computePopoverPosition({
      triggerRect: leftEdgeTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      ...viewport,
    });
    expect(pos.left).toBe(8); // default margin
  });

  it('respects a custom margin', () => {
    const pos = computePopoverPosition({
      triggerRect: { top: 100, bottom: 120, left: 80, right: 120 },
      popoverWidth: 240,
      estimatedHeight: 320,
      margin: 16,
      ...viewport,
    });
    expect(pos.left).toBe(16);
  });

  it('respects a custom gap between trigger and popover', () => {
    const pos = computePopoverPosition({
      triggerRect: middleTrigger,
      popoverWidth: 240,
      estimatedHeight: 320,
      gap: 12,
      ...viewport,
    });
    expect(pos.top).toBe(middleTrigger.bottom + 12); // 332
  });
});
