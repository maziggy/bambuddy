/**
 * The drying popover has to name a material the printer will recognise.
 *
 * It seeds two things from one lookup: the temperature it prefills, and the
 * filament name the start command carries. The dropdown rendering that name
 * falls back to its first option when the value isn't in its list -- silently
 * -- so a lookup that can return something outside the table shows one material
 * and sends another. An AMS-HT holding Support for PLA/PETG displayed PLA and
 * told the printer PLA-S (#2774). These tests pin the invariant that closes
 * that: whatever comes back is a key the table has.
 */
import { describe, it, expect } from 'vitest';

import { resolveDryingPresetKey, type DryingPreset } from '../../utils/dryingPresets';

// The shipped table, as PrintersPage.tsx defines it.
const PRESETS: Record<string, DryingPreset> = {
  'PLA':   { n3f: 45, n3s: 45, n3f_hours: 12, n3s_hours: 12 },
  'PETG':  { n3f: 65, n3s: 65, n3f_hours: 12, n3s_hours: 12 },
  'TPU':   { n3f: 65, n3s: 75, n3f_hours: 12, n3s_hours: 18 },
  'ABS':   { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'ASA':   { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'PA':    { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 12 },
  'PC':    { n3f: 65, n3s: 80, n3f_hours: 12, n3s_hours: 8 },
  'PVA':   { n3f: 65, n3s: 85, n3f_hours: 12, n3s_hours: 18 },
};

describe('resolveDryingPresetKey', () => {
  it('always answers with a key the table has', () => {
    // The invariant behind #2774. Anything outside the table reaches the
    // dropdown as a value it will not display and the printer as a material
    // the user never chose.
    const trayTypes = [
      'PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PA', 'PC', 'PVA',
      'PLA-S', 'PLA-CF', 'PETG-CF', 'PET-CF', 'ABS-GF', 'ASA-CF', 'PAHT-CF',
      'PA6-CF', 'PPS-CF', 'PPA-CF', 'HIPS', 'PP', 'PE', 'EVA', 'PHA', 'PCTG',
      'Nylon', 'TPU for AMS', '', '   ', 'wildly unknown',
    ];
    for (const trayType of trayTypes) {
      expect(PRESETS).toHaveProperty(resolveDryingPresetKey(trayType, PRESETS));
    }
  });

  it('passes a listed material straight through', () => {
    expect(resolveDryingPresetKey('PETG', PRESETS)).toBe('PETG');
    expect(resolveDryingPresetKey('PVA', PRESETS)).toBe('PVA');
  });

  it('is case-insensitive and ignores a trailing qualifier', () => {
    expect(resolveDryingPresetKey('petg', PRESETS)).toBe('PETG');
    expect(resolveDryingPresetKey('TPU for AMS', PRESETS)).toBe('TPU');
  });

  it('dries a support material as its base', () => {
    // Support for PLA/PETG reports as PLA-S -- the case from the report.
    expect(resolveDryingPresetKey('PLA-S', PRESETS)).toBe('PLA');
  });

  it('dries a composite as its base rather than defaulting to PLA', () => {
    // The reason the suffix is stripped instead of just falling back: PETG-CF
    // wants PETG's 65 degrees, and landing on PLA's 45 would quietly waste the
    // cycle.
    expect(resolveDryingPresetKey('PETG-CF', PRESETS)).toBe('PETG');
    expect(resolveDryingPresetKey('PLA-CF', PRESETS)).toBe('PLA');
    expect(resolveDryingPresetKey('ABS-GF', PRESETS)).toBe('ABS');
    expect(resolveDryingPresetKey('ASA-CF', PRESETS)).toBe('ASA');
  });

  it('recognises the polyamide family under its own spellings', () => {
    expect(resolveDryingPresetKey('PA6-CF', PRESETS)).toBe('PA');
    expect(resolveDryingPresetKey('PAHT-CF', PRESETS)).toBe('PA');
    expect(resolveDryingPresetKey('Nylon', PRESETS)).toBe('PA');
  });

  it('falls back to the coolest row for an unknown material', () => {
    // Under-drying an exotic filament wastes a cycle; PA's 85 degrees would
    // deform a PLA spool. So an unrecognised material must never inherit a
    // hotter row than PLA's.
    for (const unknown of ['PPS-CF', 'PEEK', 'wildly unknown']) {
      expect(resolveDryingPresetKey(unknown, PRESETS)).toBe('PLA');
    }
  });

  it('handles an empty tray', () => {
    // No spool loaded -- the popover still has to open on something.
    expect(resolveDryingPresetKey(undefined, PRESETS)).toBe('PLA');
    expect(resolveDryingPresetKey(null, PRESETS)).toBe('PLA');
    expect(resolveDryingPresetKey('', PRESETS)).toBe('PLA');
  });

  it('respects a custom preset table', () => {
    // Users can override the table from settings, so the lookup answers about
    // the table it was handed, not the shipped one.
    const custom = { ...PRESETS, 'PLA-S': { n3f: 55, n3s: 55, n3f_hours: 8, n3s_hours: 8 } };
    expect(resolveDryingPresetKey('PLA-S', custom)).toBe('PLA-S');
  });
});
