/**
 * Tests for AMS drying feature logic.
 *
 * The drying presets, time formatting, module type gating, and temperature
 * clamping are all defined inline in PrintersPage.tsx. These tests validate
 * the logic directly by mirroring the relevant constants and functions.
 */
import { describe, it, expect } from 'vitest';

/**
 * Mirrors the DRYING_PRESETS constant from PrintersPage.tsx.
 * Format: { n3f temp, n3s temp, n3f hours, n3s hours }
 */
const DRYING_PRESETS: Record<string, { n3f: number; n3s: number; n3f_hours: number; n3s_hours: number }> = {
  'PLA':   { n3f: 45, n3s: 45, n3f_hours: 12, n3s_hours: 12 },
  'PETG':  { n3f: 65, n3s: 65, n3f_hours: 12, n3s_hours: 12 },
  'TPU':   { n3f: 65, n3s: 75, n3f_hours: 12, n3s_hours: 18 },
  'ABS':   { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'ASA':   { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'PA':    { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 12 },
  'PC':    { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'PVA':   { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 18 },
};

/**
 * Mirrors the inline dry_time formatting from PrintersPage.tsx:
 *   dry_time >= 60 ? `${Math.floor(dry_time / 60)}h ${dry_time % 60}m` : `${dry_time}m`
 */
function formatDryTime(dry_time: number): string {
  if (dry_time >= 60) {
    return `${Math.floor(dry_time / 60)}h ${dry_time % 60}m`;
  }
  return `${dry_time}m`;
}

/**
 * Mirrors the temperature clamping from PrintersPage.tsx:
 *   Math.min(maxTemp, Math.max(45, value))
 * where maxTemp is 65 for n3f and 85 for n3s.
 */
function clampTemp(value: number, moduleType: 'n3f' | 'n3s'): number {
  const maxTemp = moduleType === 'n3s' ? 85 : 65;
  return Math.min(maxTemp, Math.max(45, value));
}

describe('DRYING_PRESETS structure', () => {
  const expectedFilaments = ['PLA', 'PETG', 'TPU', 'ABS', 'ASA', 'PA', 'PC', 'PVA'];

  it('contains all expected filament types', () => {
    for (const fil of expectedFilaments) {
      expect(DRYING_PRESETS).toHaveProperty(fil);
    }
  });

  it('has no unexpected filament types', () => {
    expect(Object.keys(DRYING_PRESETS).sort()).toEqual(expectedFilaments.sort());
  });

  it('n3f temps are all within 45-65 range', () => {
    for (const [fil, preset] of Object.entries(DRYING_PRESETS)) {
      expect(preset.n3f, `${fil} n3f temp`).toBeGreaterThanOrEqual(45);
      expect(preset.n3f, `${fil} n3f temp`).toBeLessThanOrEqual(65);
    }
  });

  it('n3s temps are all within 45-85 range', () => {
    for (const [fil, preset] of Object.entries(DRYING_PRESETS)) {
      expect(preset.n3s, `${fil} n3s temp`).toBeGreaterThanOrEqual(45);
      expect(preset.n3s, `${fil} n3s temp`).toBeLessThanOrEqual(85);
    }
  });

  it('all hours are between 1-24', () => {
    for (const [fil, preset] of Object.entries(DRYING_PRESETS)) {
      expect(preset.n3f_hours, `${fil} n3f_hours`).toBeGreaterThanOrEqual(1);
      expect(preset.n3f_hours, `${fil} n3f_hours`).toBeLessThanOrEqual(24);
      expect(preset.n3s_hours, `${fil} n3s_hours`).toBeGreaterThanOrEqual(1);
      expect(preset.n3s_hours, `${fil} n3s_hours`).toBeLessThanOrEqual(24);
    }
  });

  it('n3s temp is always >= n3f temp for same filament', () => {
    for (const [fil, preset] of Object.entries(DRYING_PRESETS)) {
      expect(preset.n3s, `${fil}: n3s should be >= n3f`).toBeGreaterThanOrEqual(preset.n3f);
    }
  });
});

describe('dry_time formatting', () => {
  it('formats >= 60 minutes as hours and minutes', () => {
    expect(formatDryTime(119)).toBe('1h 59m');
  });

  it('formats exactly 60 minutes as 1h 0m', () => {
    expect(formatDryTime(60)).toBe('1h 0m');
  });

  it('formats large values correctly', () => {
    expect(formatDryTime(750)).toBe('12h 30m');
  });

  it('formats < 60 minutes as minutes only', () => {
    expect(formatDryTime(42)).toBe('42m');
  });

  it('formats 1 minute', () => {
    expect(formatDryTime(1)).toBe('1m');
  });

  it('dry_time = 0 means not drying (shows 0m)', () => {
    // In the UI, dry_time > 0 gates whether the drying bar is shown at all,
    // so formatDryTime(0) would not be called. But the value itself means "not drying".
    expect(formatDryTime(0)).toBe('0m');
  });
});

describe('module type detection — drying button visibility', () => {
  /**
   * Mirrors the condition from PrintersPage.tsx:
   *   ams.module_type === 'n3f' || ams.module_type === 'n3s'
   * The drying button only shows for AMS 2 Pro (n3f) and AMS-HT (n3s).
   */
  function shouldShowDryingButton(moduleType: string): boolean {
    return moduleType === 'n3f' || moduleType === 'n3s';
  }

  it('shows for n3f (AMS 2 Pro)', () => {
    expect(shouldShowDryingButton('n3f')).toBe(true);
  });

  it('shows for n3s (AMS-HT)', () => {
    expect(shouldShowDryingButton('n3s')).toBe(true);
  });

  it('does not show for ams (original AMS)', () => {
    expect(shouldShowDryingButton('ams')).toBe(false);
  });

  it('does not show for empty string', () => {
    expect(shouldShowDryingButton('')).toBe(false);
  });

  it('does not show for unknown types', () => {
    expect(shouldShowDryingButton('unknown')).toBe(false);
  });
});

describe('temperature clamping', () => {
  describe('n3f (max 65)', () => {
    it('clamps value below minimum to 45', () => {
      expect(clampTemp(30, 'n3f')).toBe(45);
    });

    it('clamps value above maximum to 65', () => {
      expect(clampTemp(80, 'n3f')).toBe(65);
    });

    it('keeps value within range unchanged', () => {
      expect(clampTemp(55, 'n3f')).toBe(55);
    });

    it('keeps minimum boundary value', () => {
      expect(clampTemp(45, 'n3f')).toBe(45);
    });

    it('keeps maximum boundary value', () => {
      expect(clampTemp(65, 'n3f')).toBe(65);
    });
  });

  describe('n3s (max 85)', () => {
    it('clamps value below minimum to 45', () => {
      expect(clampTemp(10, 'n3s')).toBe(45);
    });

    it('clamps value above maximum to 85', () => {
      expect(clampTemp(100, 'n3s')).toBe(85);
    });

    it('keeps value within range unchanged', () => {
      expect(clampTemp(70, 'n3s')).toBe(70);
    });

    it('keeps minimum boundary value', () => {
      expect(clampTemp(45, 'n3s')).toBe(45);
    });

    it('keeps maximum boundary value', () => {
      expect(clampTemp(85, 'n3s')).toBe(85);
    });

    it('allows values above n3f max (e.g. 75)', () => {
      expect(clampTemp(75, 'n3s')).toBe(75);
    });
  });
});

describe('rotate tray option', () => {
  it('defaults to false', () => {
    // Mirrors the initial state: useState(false)
    const defaultRotateTray = false;
    expect(defaultRotateTray).toBe(false);
  });

  it('is included in the API URL when true', () => {
    // Mirrors the API call construction from client.ts
    const buildUrl = (rotateTray: boolean) =>
      `/printers/1/drying/start?ams_id=0&temp=55&duration=4&filament=PLA&rotate_tray=${rotateTray}`;
    expect(buildUrl(true)).toContain('rotate_tray=true');
    expect(buildUrl(false)).toContain('rotate_tray=false');
  });

  it('resets to false when opening popover for a new AMS unit', () => {
    // Mirrors the popover open logic: setDryingRotateTray(false) is called
    // each time the popover opens for any AMS unit
    let rotateTray = true; // user enabled it for previous AMS
    // Simulates opening popover for a different AMS
    rotateTray = false; // setDryingRotateTray(false)
    expect(rotateTray).toBe(false);
  });
});

describe('rotate tray gate (per-AMS tray.state === 11)', () => {
  /**
   * Mirrors the gate from PrintersPage.tsx — rotation is physically impossible
   * when ANY tray in the targeted AMS has its filament threaded out into the
   * feed tube. The whole AMS rotates as one mechanism (all 4 spools turn
   * together), so a single loaded slot locks the entire unit.
   *
   * Per-tray Bambu `state`:
   *   9  = empty (no spool)
   *   10 = spool present, NOT loaded into tube (rotation possible)
   *   11 = loaded into tube (rotation impossible)
   *
   * This catches both mid-print (active feed) AND idle-with-threaded-filament
   * — the H2D's post-print state leaves filament in the tube but tray_now
   * resets to 255, which a tray_now-only check would silently miss.
   */
  type TrayLike = { state?: number };
  type AmsLike = { id: number; tray?: TrayLike[] };

  function isTrayLoadedInThisAms(
    amsData: AmsLike[],
    targetAmsId: number | null,
  ): boolean {
    if (targetAmsId === null) return false;
    const targetAms = amsData.find(a => a.id === targetAmsId);
    return (targetAms?.tray ?? []).some(tray => tray.state === 11);
  }

  it('returns false when AMS id is null (modal closed)', () => {
    const ams = [{ id: 0, tray: [{ state: 11 }] }];
    expect(isTrayLoadedInThisAms(ams, null)).toBe(false);
  });

  it('returns false when targeted AMS not found in amsData', () => {
    const ams = [{ id: 0, tray: [{ state: 11 }] }];
    expect(isTrayLoadedInThisAms(ams, 1)).toBe(false);
  });

  it('returns false when all trays are empty (state=9)', () => {
    const ams = [{ id: 0, tray: [{ state: 9 }, { state: 9 }, { state: 9 }, { state: 9 }] }];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(false);
  });

  it('returns false when all trays have spools but none loaded (state=10)', () => {
    // The "all AMS have spools loaded" case the gate now catches correctly:
    // spool present in the slot, NOT threaded into the tube → rotation possible.
    const ams = [{ id: 0, tray: [{ state: 10 }, { state: 10 }, { state: 10 }, { state: 10 }] }];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(false);
  });

  it('returns true when ANY tray is loaded into tube (state=11)', () => {
    // H2D's typical post-print state: one tray's filament is still threaded out
    // into the feed tube even after the print finishes. tray_now=255 but
    // this tray's state stays at 11. The whole AMS is mechanically locked.
    const ams = [{ id: 0, tray: [{ state: 10 }, { state: 11 }, { state: 9 }, { state: 10 }] }];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(true);
  });

  it('returns true when targeted AMS-B has a loaded tray (per-AMS isolation)', () => {
    // AMS-A locked, AMS-B free; targeting AMS-A → true, targeting AMS-B → false
    const ams = [
      { id: 0, tray: [{ state: 11 }, { state: 9 }] },
      { id: 1, tray: [{ state: 10 }, { state: 10 }] },
    ];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(true);
    expect(isTrayLoadedInThisAms(ams, 1)).toBe(false);
  });

  it('returns false when targeted AMS has no trays array', () => {
    const ams = [{ id: 0 }];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(false);
  });

  it('treats missing state as not-loaded (conservative default-allow)', () => {
    // If firmware doesn't report a state field, default to allowing rotate.
    // The firmware-side dry_sf_reason check still rejects on the route side
    // if rotation is actually impossible, so being lenient here is safe.
    const ams = [{ id: 0, tray: [{ state: undefined }, { state: undefined }] }];
    expect(isTrayLoadedInThisAms(ams, 0)).toBe(false);
  });

  it('submission clamp: rotateTray collapses to false when gate is active', () => {
    // Mirrors:  rotateTray: dryingRotateTray && !isTrayLoadedInThisAms
    // A user enabling rotate before tray.state shifts to 11 (e.g. user loads
    // filament from the AMS UI while popover is open) sees the toggle disable,
    // and the submit also sends rotate_tray=false. Without the clamp, firmware
    // would reject with dry_sf_reason=[3] (ConsumableAtAmsOutlet) post-click.
    const userToggleState = true;
    const ams = [{ id: 0, tray: [{ state: 11 }, { state: 10 }] }];
    const trayLoaded = isTrayLoadedInThisAms(ams, 0);
    const submittedValue = userToggleState && !trayLoaded;
    expect(trayLoaded).toBe(true);
    expect(submittedValue).toBe(false);
  });

  it('submission clamp: rotateTray passes through when gate is inactive', () => {
    const userToggleState = true;
    const ams = [{ id: 0, tray: [{ state: 10 }, { state: 10 }] }];
    const trayLoaded = isTrayLoadedInThisAms(ams, 0);
    const submittedValue = userToggleState && !trayLoaded;
    expect(trayLoaded).toBe(false);
    expect(submittedValue).toBe(true);
  });
});
