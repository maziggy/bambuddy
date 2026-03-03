/**
 * Tests for SpoolBuddy AMS page logic:
 * - External slot active state (tray_now=255 bug fix)
 * - Fill level override fallback chain (inventory → AMS remain)
 *
 * These mirror inline logic from SpoolBuddyAmsPage.tsx, extracted for testability.
 */
import { describe, it, expect } from 'vitest';

/**
 * Mirrors the ext slot isExtActive calculation from SpoolBuddyAmsPage.tsx.
 * tray_now=255 means "no tray loaded" (idle) — should never mark any slot active.
 */
function computeExtActive(
  trayNow: number,
  isDualNozzle: boolean,
  extTrayId: number,
  activeExtruder: number | undefined,
): boolean {
  return trayNow === 255 ? false
    : isDualNozzle && trayNow === 254
      ? (extTrayId === 254 && activeExtruder === 1) ||
        (extTrayId === 255 && activeExtruder === 0)
      : trayNow === extTrayId;
}

/**
 * Mirrors the effective fill fallback from SpoolBuddyAmsPage.tsx and AmsUnitCard.tsx.
 * Priority: inventory fill override → AMS remain (if >= 0)
 */
function computeEffectiveFill(
  fillOverride: number | null,
  amsRemain: number | null | undefined,
): number | null {
  const amsFill = amsRemain != null && amsRemain >= 0 ? amsRemain : null;
  return fillOverride ?? amsFill;
}

describe('ext slot active state', () => {
  describe('tray_now=255 (idle) — no slot should be active', () => {
    it('single-nozzle: ext (id=254) not active when tray_now=255', () => {
      expect(computeExtActive(255, false, 254, undefined)).toBe(false);
    });

    it('dual-nozzle: ext-L (id=254) not active when tray_now=255', () => {
      expect(computeExtActive(255, true, 254, 1)).toBe(false);
    });

    it('dual-nozzle: ext-R (id=255) not active when tray_now=255', () => {
      // This was the bug: trayNow(255) === extTrayId(255) without the guard
      expect(computeExtActive(255, true, 255, 0)).toBe(false);
    });
  });

  describe('tray_now=254 on dual-nozzle — uses active_extruder', () => {
    it('ext-L active when active_extruder=1 (left)', () => {
      expect(computeExtActive(254, true, 254, 1)).toBe(true);
    });

    it('ext-R active when active_extruder=0 (right)', () => {
      expect(computeExtActive(254, true, 255, 0)).toBe(true);
    });

    it('ext-L not active when active_extruder=0 (right)', () => {
      expect(computeExtActive(254, true, 254, 0)).toBe(false);
    });

    it('ext-R not active when active_extruder=1 (left)', () => {
      expect(computeExtActive(254, true, 255, 1)).toBe(false);
    });
  });

  describe('tray_now=254 on single-nozzle — direct ID match', () => {
    it('ext (id=254) active when tray_now=254', () => {
      expect(computeExtActive(254, false, 254, undefined)).toBe(true);
    });
  });

  describe('AMS tray active — ext slots not active', () => {
    it('ext not active when AMS slot is active (tray_now=5)', () => {
      expect(computeExtActive(5, false, 254, undefined)).toBe(false);
    });
  });
});

describe('fill level override fallback', () => {
  it('uses inventory fill when available, ignoring AMS remain', () => {
    expect(computeEffectiveFill(75, 50)).toBe(75);
  });

  it('falls back to AMS remain when no inventory fill', () => {
    expect(computeEffectiveFill(null, 50)).toBe(50);
  });

  it('returns null when neither source available', () => {
    expect(computeEffectiveFill(null, null)).toBeNull();
  });

  it('returns null when AMS remain is -1 (unknown) and no inventory fill', () => {
    expect(computeEffectiveFill(null, -1)).toBeNull();
  });

  it('uses inventory fill even when AMS remain is -1', () => {
    expect(computeEffectiveFill(80, -1)).toBe(80);
  });

  it('uses AMS remain of 0 (empty) as valid fill', () => {
    expect(computeEffectiveFill(null, 0)).toBe(0);
  });

  it('uses inventory fill of 0 over AMS remain', () => {
    expect(computeEffectiveFill(0, 50)).toBe(0);
  });
});
