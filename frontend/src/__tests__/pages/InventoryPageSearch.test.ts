/**
 * Unit tests for the global search filter used in InventoryPage.
 *
 * The filter is a pure client-side computation — replicated inline here
 * following the same pattern as InventoryPageGrouping.test.ts so no DOM
 * render or API mock is needed.
 *
 * Fields covered: brand, material, color_name, subtype, note,
 * slicer_filament_name, storage_location.
 */

import { describe, it, expect } from 'vitest';
import type { InventorySpool } from '../../api/client';
import { filterSpoolsByQuery } from '../../utils/inventorySearch';

function applySearch(spools: InventorySpool[], search: string): InventorySpool[] {
  return filterSpoolsByQuery(spools, search);
}

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

describe('InventoryPage search filter', () => {
  describe('storage_location', () => {
    it('returns spools whose storage_location matches the query', () => {
      const spools = [
        makeSpool({ id: 1, storage_location: 'IKEA Regal' }),
        makeSpool({ id: 2, storage_location: 'Kiste - PLA' }),
        makeSpool({ id: 3, storage_location: 'Lagerregal' }),
      ];
      expect(applySearch(spools, 'IKEA').map((s) => s.id)).toEqual([1]);
      expect(applySearch(spools, 'Kiste').map((s) => s.id)).toEqual([2]);
      expect(applySearch(spools, 'regal').map((s) => s.id)).toEqual([1, 3]);
    });

    it('is case-insensitive for storage_location', () => {
      const spools = [makeSpool({ id: 1, storage_location: 'IKEA Regal' })];
      expect(applySearch(spools, 'ikea regal')).toHaveLength(1);
      expect(applySearch(spools, 'IKEA REGAL')).toHaveLength(1);
      expect(applySearch(spools, 'ikEa')).toHaveLength(1);
    });

    it('matches partial storage_location strings', () => {
      const spools = [makeSpool({ id: 1, storage_location: 'Kiste - PLA' })];
      expect(applySearch(spools, 'Kis')).toHaveLength(1);
      expect(applySearch(spools, 'PLA')).toHaveLength(1);
      expect(applySearch(spools, '- PLA')).toHaveLength(1);
    });

    it('does not crash when storage_location is null', () => {
      const spools = [makeSpool({ id: 1, storage_location: null })];
      expect(() => applySearch(spools, 'regal')).not.toThrow();
      expect(applySearch(spools, 'regal')).toHaveLength(0);
    });

    it('excludes spools whose storage_location does not match', () => {
      const spools = [
        makeSpool({ id: 1, storage_location: 'IKEA Regal' }),
        makeSpool({ id: 2, storage_location: 'Kiste - PLA' }),
      ];
      expect(applySearch(spools, 'IKEA').map((s) => s.id)).toEqual([1]);
    });
  });

  describe('existing fields (regression)', () => {
    it('still finds by brand', () => {
      const spools = [
        makeSpool({ id: 1, brand: 'Bambu Lab' }),
        makeSpool({ id: 2, brand: 'Polymaker' }),
      ];
      expect(applySearch(spools, 'polymaker').map((s) => s.id)).toEqual([2]);
    });

    it('still finds by material', () => {
      const spools = [
        makeSpool({ id: 1, material: 'PLA' }),
        makeSpool({ id: 2, material: 'PETG' }),
      ];
      expect(applySearch(spools, 'petg').map((s) => s.id)).toEqual([2]);
    });

    it('still finds by color_name', () => {
      const spools = [
        makeSpool({ id: 1, color_name: 'Jade White' }),
        makeSpool({ id: 2, color_name: 'Black' }),
      ];
      expect(applySearch(spools, 'jade').map((s) => s.id)).toEqual([1]);
    });

    it('still finds by note', () => {
      const spools = [
        makeSpool({ id: 1, note: 'fast print only' }),
        makeSpool({ id: 2, note: null }),
      ];
      expect(applySearch(spools, 'fast').map((s) => s.id)).toEqual([1]);
    });

    it('returns all spools when search is empty', () => {
      const spools = [makeSpool({ id: 1 }), makeSpool({ id: 2 })];
      expect(applySearch(spools, '')).toHaveLength(2);
    });

    it('returns empty array when nothing matches', () => {
      const spools = [makeSpool({ id: 1, brand: 'Bambu Lab', material: 'PLA' })];
      expect(applySearch(spools, 'xxxxxxxx')).toHaveLength(0);
    });
  });

  describe('cross-field matching', () => {
    it('matches a spool if any field contains the query', () => {
      const spool = makeSpool({
        id: 1,
        brand: 'Bambu Lab',
        material: 'PLA',
        storage_location: 'IKEA Regal',
      });
      // Each individual field matches
      expect(applySearch([spool], 'Bambu')).toHaveLength(1);
      expect(applySearch([spool], 'PLA')).toHaveLength(1);
      expect(applySearch([spool], 'IKEA')).toHaveLength(1);
    });

    it('a query matching only storage_location is found even when other fields do not match', () => {
      const spools = [
        makeSpool({ id: 1, brand: 'Polymaker', material: 'PETG', storage_location: 'IKEA Regal' }),
        makeSpool({ id: 2, brand: 'Polymaker', material: 'PETG', storage_location: 'Kiste' }),
      ];
      const result = applySearch(spools, 'IKEA');
      expect(result.map((s) => s.id)).toEqual([1]);
    });
  });
});
