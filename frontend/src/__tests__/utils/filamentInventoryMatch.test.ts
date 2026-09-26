import { describe, expect, it } from 'vitest';

import {
  buildFilamentInventoryIdentity,
  filamentPresetIsInInventory,
  normalizeFilamentPresetName,
} from '../../utils/filamentInventoryMatch';

describe('filament inventory preset matching (#3157)', () => {
  it('normalizes custom markers and printer-specific Bambu suffixes', () => {
    expect(normalizeFilamentPresetName(' #  Bambu PLA Basic @BBL A1 0.4 nozzle '))
      .toBe('bambu pla basic');
    expect(normalizeFilamentPresetName('SUNLU TPU 95A @Bambu Lab H2D 0.4 nozzle (Custom)'))
      .toBe('sunlu tpu 95a');
  });

  it('does not strip an unrelated @ from a user-authored name', () => {
    expect(normalizeFilamentPresetName('My @work PLA')).toBe('my @work pla');
  });

  it('matches the stored opaque preset id exactly', () => {
    const inventory = buildFilamentInventoryIdentity([
      { slicer_filament: 'PFUS-123', slicer_filament_name: null },
    ]);
    expect(filamentPresetIsInInventory({ id: 'PFUS-123', name: 'Renamed profile' }, inventory)).toBe(true);
    expect(filamentPresetIsInInventory({ id: 'pfus-123', name: 'Renamed profile' }, inventory)).toBe(false);
  });

  it('uses the normalized name to match another printer variant', () => {
    const inventory = buildFilamentInventoryIdentity([
      {
        slicer_filament: 'a1-copy',
        slicer_filament_name: 'Bambu PLA Basic @BBL A1',
      },
    ]);
    expect(
      filamentPresetIsInInventory(
        { id: 'x1c-copy', name: 'Bambu PLA Basic @BBL X1C 0.4 nozzle' },
        inventory,
      ),
    ).toBe(true);
  });

  it('disables filtering when active inventory has no linked slicer profile', () => {
    expect(buildFilamentInventoryIdentity([
      { slicer_filament: null, slicer_filament_name: null },
    ])).toBeNull();
  });
});
