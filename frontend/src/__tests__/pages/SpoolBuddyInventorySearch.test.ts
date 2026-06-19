/**
 * Regression test for #1738 — SpoolBuddy's inventory search must match by
 * numeric spool ID, just like Bambuddy's main InventoryPage. Both pages now
 * share `filterSpoolsByQuery` so behaviour stays in lockstep; this test fails
 * loudly if SpoolBuddyInventoryPage ever re-inlines its filter and drops
 * fields.
 */

import { describe, it, expect } from 'vitest';
import type { InventorySpool } from '../../api/client';
import { filterSpoolsByQuery } from '../../utils/inventorySearch';

function makeSpool(overrides: Partial<InventorySpool> & { id: number }): InventorySpool {
  return {
    material: 'PLA',
    subtype: 'Basic',
    brand: 'Bambu Lab',
    color_name: 'White',
    rgba: 'FFFFFFFF',
    label_weight: 1000,
    core_weight: 250,
    core_weight_catalog_id: null,
    weight_used: 0,
    weight_locked: false,
    slicer_filament: null,
    slicer_filament_name: null,
    nozzle_temp_min: null,
    nozzle_temp_max: null,
    note: null,
    added_full: null,
    last_used: null,
    encode_time: null,
    tag_uid: null,
    tray_uuid: null,
    data_origin: null,
    tag_type: null,
    archived_at: null,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    k_profiles: [],
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
    storage_location: null,
    ...overrides,
  };
}

describe('SpoolBuddyInventoryPage search filter (#1738)', () => {
  it('matches an exact spool ID', () => {
    const spools = [
      makeSpool({ id: 1336 }),
      makeSpool({ id: 1337 }),
      makeSpool({ id: 42 }),
    ];
    const result = filterSpoolsByQuery(spools, '1336');
    expect(result.map((s) => s.id)).toEqual([1336]);
  });

  it('matches a partial spool ID', () => {
    const spools = [
      makeSpool({ id: 100 }),
      makeSpool({ id: 200 }),
      makeSpool({ id: 1001 }),
    ];
    const result = filterSpoolsByQuery(spools, '00');
    expect(result.map((s) => s.id).sort((a, b) => a - b)).toEqual([100, 200, 1001]);
  });

  it('still matches by the existing fields SpoolBuddy supported pre-fix', () => {
    const spools = [
      makeSpool({ id: 1, material: 'PLA', brand: 'Bambu Lab', color_name: 'Red' }),
      makeSpool({ id: 2, material: 'PETG', brand: 'Polymaker', color_name: 'Blue' }),
    ];
    expect(filterSpoolsByQuery(spools, 'polymaker').map((s) => s.id)).toEqual([2]);
    expect(filterSpoolsByQuery(spools, 'PLA').map((s) => s.id)).toEqual([1]);
    expect(filterSpoolsByQuery(spools, 'red').map((s) => s.id)).toEqual([1]);
  });

  it('also matches by storage_location and slicer_filament_name (parity gain)', () => {
    const spools = [
      makeSpool({ id: 1, storage_location: 'IKEA Regal' }),
      makeSpool({ id: 2, slicer_filament_name: 'Generic PLA Matte' }),
    ];
    expect(filterSpoolsByQuery(spools, 'IKEA').map((s) => s.id)).toEqual([1]);
    expect(filterSpoolsByQuery(spools, 'Matte').map((s) => s.id)).toEqual([2]);
  });
});
