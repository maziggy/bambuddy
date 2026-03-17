/**
 * Tests for InlineMappingEditor auto-match and nozzle filtering logic.
 *
 * Regression test for #624: multi-printer filament mapping showed filaments
 * from both nozzles on dual-nozzle printers (H2D). The single-printer path
 * (FilamentMapping.tsx) was fixed in commit 29e9593 but the multi-printer
 * path (InlineMappingEditor in PrinterSelector.tsx) was missed.
 */

import { describe, it, expect } from 'vitest';
import {
  autoMatchFilament,
  canonicalFilamentType,
  filamentTypesCompatible,
  filterFilamentsByNozzle,
} from '../../utils/amsHelpers';
import type { LoadedFilament, FilamentRequirement } from '../../hooks/useFilamentMapping';

// -- helpers -----------------------------------------------------------------

function makeFilament(overrides: Partial<LoadedFilament> & { globalTrayId: number }): LoadedFilament {
  return {
    type: 'PLA',
    color: '#FFFFFF',
    colorName: 'White',
    amsId: 0,
    trayId: 0,
    label: 'AMS1-T1',
    trayInfoIdx: '',
    extruderId: undefined,
    ...overrides,
  };
}

function makeReq(overrides: Partial<FilamentRequirement> = {}): FilamentRequirement {
  return {
    slot_id: 1,
    type: 'PLA',
    color: '#FFFFFF',
    used_grams: 10,
    ...overrides,
  };
}

// Dual-nozzle H2D-like setup:
// Left nozzle (extruderId=1): AMS0 with PLA Black (tray 0) and PETG White (tray 1)
// Right nozzle (extruderId=0): AMS1 with PLA White (tray 4) and PLA Red (tray 5)
const H2D_FILAMENTS: LoadedFilament[] = [
  makeFilament({ globalTrayId: 0, type: 'PLA', color: '#000000', colorName: 'Black', amsId: 0, trayId: 0, label: 'AMS1-T1', extruderId: 1 }),
  makeFilament({ globalTrayId: 1, type: 'PETG', color: '#FFFFFF', colorName: 'White', amsId: 0, trayId: 1, label: 'AMS1-T2', extruderId: 1 }),
  makeFilament({ globalTrayId: 4, type: 'PLA', color: '#FFFFFF', colorName: 'White', amsId: 1, trayId: 0, label: 'AMS2-T1', extruderId: 0 }),
  makeFilament({ globalTrayId: 5, type: 'PLA', color: '#FF0000', colorName: 'Red', amsId: 1, trayId: 1, label: 'AMS2-T2', extruderId: 0 }),
];

// -- canonicalFilamentType / filamentTypesCompatible -------------------------

describe('canonicalFilamentType', () => {
  it('maps PA-CF variants to the same canonical type', () => {
    const canonical = canonicalFilamentType('PA-CF');
    expect(canonicalFilamentType('PA12-CF')).toBe(canonical);
    expect(canonicalFilamentType('PAHT-CF')).toBe(canonical);
  });

  it('is case-insensitive', () => {
    expect(canonicalFilamentType('pa-cf')).toBe(canonicalFilamentType('PA-CF'));
    expect(canonicalFilamentType('Pa12-Cf')).toBe(canonicalFilamentType('PA12-CF'));
  });

  it('returns the type unchanged for non-equivalent types', () => {
    expect(canonicalFilamentType('PLA')).toBe('PLA');
    expect(canonicalFilamentType('PETG')).toBe('PETG');
    expect(canonicalFilamentType('ABS')).toBe('ABS');
  });

  it('returns empty string for undefined/empty input', () => {
    expect(canonicalFilamentType(undefined)).toBe('');
    expect(canonicalFilamentType('')).toBe('');
  });
});

describe('filamentTypesCompatible', () => {
  it('treats PA-CF and PA12-CF as compatible', () => {
    expect(filamentTypesCompatible('PA-CF', 'PA12-CF')).toBe(true);
  });

  it('treats PA-CF and PAHT-CF as compatible', () => {
    expect(filamentTypesCompatible('PA-CF', 'PAHT-CF')).toBe(true);
  });

  it('treats PLA and PETG as incompatible', () => {
    expect(filamentTypesCompatible('PLA', 'PETG')).toBe(false);
  });

  it('treats same types as compatible', () => {
    expect(filamentTypesCompatible('PLA', 'PLA')).toBe(true);
  });
});

// -- filterFilamentsByNozzle -------------------------------------------------

describe('filterFilamentsByNozzle', () => {
  it('returns all filaments when nozzle_id is null', () => {
    const result = filterFilamentsByNozzle(H2D_FILAMENTS, null);
    expect(result).toHaveLength(4);
  });

  it('returns all filaments when nozzle_id is undefined', () => {
    const result = filterFilamentsByNozzle(H2D_FILAMENTS, undefined);
    expect(result).toHaveLength(4);
  });

  it('filters to left nozzle (extruderId=1)', () => {
    const result = filterFilamentsByNozzle(H2D_FILAMENTS, 1);
    expect(result).toHaveLength(2);
    expect(result.every((f) => f.extruderId === 1)).toBe(true);
  });

  it('filters to right nozzle (extruderId=0)', () => {
    const result = filterFilamentsByNozzle(H2D_FILAMENTS, 0);
    expect(result).toHaveLength(2);
    expect(result.every((f) => f.extruderId === 0)).toBe(true);
  });
});

// -- autoMatchFilament -------------------------------------------------------

describe('autoMatchFilament', () => {
  it('matches exact type+color on correct nozzle', () => {
    const req = makeReq({ type: 'PLA', color: '#FFFFFF', nozzle_id: 0 });
    const result = autoMatchFilament(req, H2D_FILAMENTS, new Set());
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(4); // AMS2-T1 on right nozzle
  });

  it('does NOT match filament on wrong nozzle — regression #624', () => {
    // Require PLA Black on right nozzle (extruderId=0).
    // PLA Black exists only on left nozzle (tray 0, extruderId=1).
    const req = makeReq({ type: 'PLA', color: '#000000', nozzle_id: 0 });
    const result = autoMatchFilament(req, H2D_FILAMENTS, new Set());
    // Should NOT match tray 0 (wrong nozzle). May match tray 4 or 5 as type-only.
    if (result) {
      expect(result.extruderId).toBe(0);
      expect(result.globalTrayId).not.toBe(0);
    }
  });

  it('matches without nozzle constraint for single-nozzle printers', () => {
    const req = makeReq({ type: 'PLA', color: '#000000' }); // no nozzle_id
    const result = autoMatchFilament(req, H2D_FILAMENTS, new Set());
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(0); // Exact match: PLA Black
  });

  it('falls back to type-only match on correct nozzle', () => {
    // Require PETG Green on left nozzle — no exact color match, but PETG White exists
    const req = makeReq({ type: 'PETG', color: '#00FF00', nozzle_id: 1 });
    const result = autoMatchFilament(req, H2D_FILAMENTS, new Set());
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(1); // PETG White on left nozzle
    expect(result!.extruderId).toBe(1);
  });

  it('returns undefined when no filament matches on required nozzle', () => {
    // Require PETG on right nozzle — PETG only exists on left nozzle
    const req = makeReq({ type: 'PETG', color: '#FFFFFF', nozzle_id: 0 });
    const result = autoMatchFilament(req, H2D_FILAMENTS, new Set());
    expect(result).toBeUndefined();
  });

  it('skips already-used tray IDs', () => {
    const req = makeReq({ type: 'PLA', color: '#FFFFFF', nozzle_id: 0 });
    const used = new Set([4]); // AMS2-T1 already used
    const result = autoMatchFilament(req, H2D_FILAMENTS, used);
    // Should fall back to PLA Red (tray 5) as type-only match
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(5);
  });

  it('matches PA-CF requirement to PA12-CF filament — #688', () => {
    const filaments: LoadedFilament[] = [
      makeFilament({ globalTrayId: 0, type: 'PA12-CF', color: '#000000', colorName: 'Black' }),
    ];
    const req = makeReq({ type: 'PA-CF', color: '#000000' });
    const result = autoMatchFilament(req, filaments, new Set());
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(0);
  });

  it('matches PAHT-CF requirement to PA-CF filament — #688', () => {
    const filaments: LoadedFilament[] = [
      makeFilament({ globalTrayId: 0, type: 'PA-CF', color: '#333333', colorName: 'Dark Gray' }),
    ];
    const req = makeReq({ type: 'PAHT-CF', color: '#333333' });
    const result = autoMatchFilament(req, filaments, new Set());
    expect(result).toBeDefined();
    expect(result!.globalTrayId).toBe(0);
  });
});
