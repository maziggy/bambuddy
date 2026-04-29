import { describe, it, expect } from 'vitest';
import { spoolMatchesQuery, filterSpoolsByQuery } from '../../utils/inventorySearch';
import type { InventorySpool } from '../../api/client';

function makeSpool(overrides: Partial<InventorySpool> = {}): InventorySpool {
  return {
    id: 1,
    material: 'PLA',
    subtype: 'Basic',
    color_name: 'Red',
    rgba: 'FF0000FF',
    brand: 'Bambu Lab',
    label_weight: 1000,
    core_weight: 250,
    core_weight_catalog_id: null,
    weight_used: 0,
    slicer_filament: null,
    slicer_filament_name: null,
    nozzle_temp_min: 220,
    nozzle_temp_max: 240,
    note: null,
    added_full: null,
    last_used: null,
    encode_time: null,
    tag_uid: null,
    tray_uuid: null,
    data_origin: 'local',
    tag_type: null,
    archived_at: null,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
    cost_per_kg: null,
    last_scale_weight: null,
    last_weighed_at: null,
    category: null,
    low_stock_threshold_pct: null,
    ...overrides,
  };
}

describe('spoolMatchesQuery', () => {
  it('returns true for empty query', () => {
    expect(spoolMatchesQuery(makeSpool(), '')).toBe(true);
  });

  it('matches on material (case-insensitive)', () => {
    const spool = makeSpool({ material: 'PETG' });
    expect(spoolMatchesQuery(spool, 'petg')).toBe(true);
    expect(spoolMatchesQuery(spool, 'PET')).toBe(true);
    expect(spoolMatchesQuery(spool, 'pla')).toBe(false);
  });

  it('matches on brand (case-insensitive)', () => {
    const spool = makeSpool({ brand: 'Prusament' });
    expect(spoolMatchesQuery(spool, 'prusa')).toBe(true);
    expect(spoolMatchesQuery(spool, 'PRUSA')).toBe(true);
  });

  it('matches on color_name (case-insensitive)', () => {
    const spool = makeSpool({ color_name: 'Galaxy Black' });
    expect(spoolMatchesQuery(spool, 'galaxy')).toBe(true);
    expect(spoolMatchesQuery(spool, 'GALAXY')).toBe(true);
    expect(spoolMatchesQuery(spool, 'blue')).toBe(false);
  });

  it('matches on subtype (case-insensitive)', () => {
    const spool = makeSpool({ subtype: 'Matte' });
    expect(spoolMatchesQuery(spool, 'matt')).toBe(true);
    expect(spoolMatchesQuery(spool, 'MATTE')).toBe(true);
  });

  it('returns false when null optional fields do not match', () => {
    const spool = makeSpool({ brand: null, color_name: null, subtype: null });
    expect(spoolMatchesQuery(spool, 'bambu')).toBe(false);
  });
});

describe('filterSpoolsByQuery', () => {
  const spools = [
    makeSpool({ id: 1, material: 'PLA', brand: 'Bambu Lab', color_name: 'Red' }),
    makeSpool({ id: 2, material: 'PETG', brand: 'Prusament', color_name: 'Blue' }),
    makeSpool({ id: 3, material: 'ABS', brand: null, color_name: 'Black', subtype: 'Matte' }),
  ];

  it('returns all spools for empty query', () => {
    expect(filterSpoolsByQuery(spools, '')).toHaveLength(3);
  });

  it('filters by material', () => {
    const result = filterSpoolsByQuery(spools, 'pla');
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1);
  });

  it('filters by brand', () => {
    const result = filterSpoolsByQuery(spools, 'prusa');
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(2);
  });

  it('filters by subtype', () => {
    const result = filterSpoolsByQuery(spools, 'matte');
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(3);
  });

  it('returns empty array when no match', () => {
    expect(filterSpoolsByQuery(spools, 'nylon')).toHaveLength(0);
  });
});
