/**
 * Tests for the spool grouping logic used in InventoryPage.
 *
 * The grouping is a pure client-side computation:
 * - Spools with identical material+subtype+brand+color_name+rgba+label_weight are grouped
 * - Only unused (weight_used === 0) and unassigned spools are eligible for grouping
 * - Used or assigned spools always appear individually
 * - Groups with only 1 member remain as singles
 */

import { describe, it, expect } from 'vitest';
import type { InventorySpool, SpoolAssignment } from '../../api/client';

// Replicate the grouping key function from InventoryPage (not exported)
function spoolGroupKey(s: InventorySpool): string {
  return `${s.material}|${s.subtype || ''}|${s.brand || ''}|${s.color_name || ''}|${s.rgba || ''}|${s.label_weight}`;
}

type DisplayItem =
  | { type: 'single'; spool: InventorySpool }
  | { type: 'group'; key: string; spools: InventorySpool[]; representative: InventorySpool };

// Replicate the grouping logic from InventoryPage
function computeDisplayItems(
  sortedSpools: InventorySpool[],
  assignmentMap: Record<number, SpoolAssignment>,
): DisplayItem[] {
  const groups = new Map<string, InventorySpool[]>();

  for (const spool of sortedSpools) {
    if (spool.weight_used > 0 || assignmentMap[spool.id]) {
      // Will be added as singles in the walk below
    } else {
      const key = spoolGroupKey(spool);
      const arr = groups.get(key);
      if (arr) arr.push(spool);
      else groups.set(key, [spool]);
    }
  }

  const items: DisplayItem[] = [];
  const processedKeys = new Set<string>();

  for (const spool of sortedSpools) {
    if (spool.weight_used > 0 || assignmentMap[spool.id]) {
      items.push({ type: 'single', spool });
      continue;
    }
    const key = spoolGroupKey(spool);
    if (processedKeys.has(key)) continue;
    processedKeys.add(key);
    const members = groups.get(key)!;
    if (members.length === 1) {
      items.push({ type: 'single', spool: members[0] });
    } else {
      items.push({ type: 'group', key, spools: members, representative: members[0] });
    }
  }
  return items;
}

function makeSpool(overrides: Partial<InventorySpool> & { id: number }): InventorySpool {
  return {
    material: 'PLA',
    subtype: 'Basic',
    brand: 'Polymaker',
    color_name: 'Red',
    rgba: 'FF0000FF',
    label_weight: 1000,
    core_weight: 250,
    core_weight_catalog_id: null,
    weight_used: 0,
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
    ...overrides,
  };
}

describe('spoolGroupKey', () => {
  it('generates same key for identical spools', () => {
    const a = makeSpool({ id: 1 });
    const b = makeSpool({ id: 2 });
    expect(spoolGroupKey(a)).toBe(spoolGroupKey(b));
  });

  it('generates different key when material differs', () => {
    const a = makeSpool({ id: 1, material: 'PLA' });
    const b = makeSpool({ id: 2, material: 'PETG' });
    expect(spoolGroupKey(a)).not.toBe(spoolGroupKey(b));
  });

  it('generates different key when subtype differs', () => {
    const a = makeSpool({ id: 1, subtype: 'Basic' });
    const b = makeSpool({ id: 2, subtype: 'Matte' });
    expect(spoolGroupKey(a)).not.toBe(spoolGroupKey(b));
  });

  it('generates different key when brand differs', () => {
    const a = makeSpool({ id: 1, brand: 'Polymaker' });
    const b = makeSpool({ id: 2, brand: 'Bambu Lab' });
    expect(spoolGroupKey(a)).not.toBe(spoolGroupKey(b));
  });

  it('generates different key when color_name differs', () => {
    const a = makeSpool({ id: 1, color_name: 'Red' });
    const b = makeSpool({ id: 2, color_name: 'Blue' });
    expect(spoolGroupKey(a)).not.toBe(spoolGroupKey(b));
  });

  it('generates different key when label_weight differs', () => {
    const a = makeSpool({ id: 1, label_weight: 1000 });
    const b = makeSpool({ id: 2, label_weight: 500 });
    expect(spoolGroupKey(a)).not.toBe(spoolGroupKey(b));
  });

  it('treats null and empty string subtype the same', () => {
    const a = makeSpool({ id: 1, subtype: null as unknown as string });
    const b = makeSpool({ id: 2, subtype: '' });
    expect(spoolGroupKey(a)).toBe(spoolGroupKey(b));
  });
});

describe('computeDisplayItems', () => {
  it('groups identical unused unassigned spools', () => {
    const spools = [
      makeSpool({ id: 1 }),
      makeSpool({ id: 2 }),
      makeSpool({ id: 3 }),
    ];
    const items = computeDisplayItems(spools, {});
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe('group');
    if (items[0].type === 'group') {
      expect(items[0].spools).toHaveLength(3);
      expect(items[0].representative.id).toBe(1);
    }
  });

  it('does not group spools with different properties', () => {
    const spools = [
      makeSpool({ id: 1, material: 'PLA' }),
      makeSpool({ id: 2, material: 'PETG' }),
      makeSpool({ id: 3, material: 'ABS' }),
    ];
    const items = computeDisplayItems(spools, {});
    expect(items).toHaveLength(3);
    expect(items.every((i) => i.type === 'single')).toBe(true);
  });

  it('excludes used spools from groups', () => {
    const spools = [
      makeSpool({ id: 1, weight_used: 0 }),
      makeSpool({ id: 2, weight_used: 100 }), // used
      makeSpool({ id: 3, weight_used: 0 }),
    ];
    const items = computeDisplayItems(spools, {});
    // 1 group (id:1, id:3) + 1 single (id:2)
    expect(items).toHaveLength(2);
    const group = items.find((i) => i.type === 'group');
    const single = items.find((i) => i.type === 'single');
    expect(group).toBeDefined();
    expect(single).toBeDefined();
    if (group?.type === 'group') {
      expect(group.spools).toHaveLength(2);
      expect(group.spools.map((s) => s.id).sort()).toEqual([1, 3]);
    }
    if (single?.type === 'single') {
      expect(single.spool.id).toBe(2);
    }
  });

  it('excludes assigned spools from groups', () => {
    const spools = [
      makeSpool({ id: 1 }),
      makeSpool({ id: 2 }), // assigned
      makeSpool({ id: 3 }),
    ];
    const assignmentMap: Record<number, SpoolAssignment> = {
      2: {
        spool_id: 2,
        printer_id: 1,
        printer_name: 'P1S',
        ams_id: 0,
        tray_id: 0,
        configured: true,
        fingerprint_color: null,
        fingerprint_type: null,
      },
    };
    const items = computeDisplayItems(spools, assignmentMap);
    // 1 group (id:1, id:3) + 1 single (id:2)
    expect(items).toHaveLength(2);
    const group = items.find((i) => i.type === 'group');
    expect(group?.type).toBe('group');
    if (group?.type === 'group') {
      expect(group.spools.map((s) => s.id).sort()).toEqual([1, 3]);
    }
  });

  it('does not group a single spool', () => {
    const spools = [makeSpool({ id: 1 })];
    const items = computeDisplayItems(spools, {});
    expect(items).toHaveLength(1);
    expect(items[0].type).toBe('single');
  });

  it('preserves order — group appears at first member position', () => {
    const spools = [
      makeSpool({ id: 1, material: 'PETG' }), // unique
      makeSpool({ id: 2, material: 'PLA' }),   // group member
      makeSpool({ id: 3, material: 'PLA' }),   // group member
      makeSpool({ id: 4, material: 'ABS' }),   // unique
    ];
    const items = computeDisplayItems(spools, {});
    expect(items).toHaveLength(3);
    expect(items[0].type).toBe('single'); // PETG
    expect(items[1].type).toBe('group');  // PLA group at position of id:2
    expect(items[2].type).toBe('single'); // ABS
    if (items[1].type === 'group') {
      expect(items[1].spools.map((s) => s.id)).toEqual([2, 3]);
    }
  });

  it('handles mix of groupable and non-groupable spools', () => {
    const spools = [
      makeSpool({ id: 1, material: 'PLA' }),                    // groupable
      makeSpool({ id: 2, material: 'PLA', weight_used: 50 }),   // used → single
      makeSpool({ id: 3, material: 'PLA' }),                    // groupable
      makeSpool({ id: 4, material: 'PETG' }),                   // different → single
    ];
    const items = computeDisplayItems(spools, {});
    // PLA group (id:1,3) + PLA used single (id:2) + PETG single (id:4)
    expect(items).toHaveLength(3);
  });

  it('returns all singles when no spools can be grouped', () => {
    const spools = [
      makeSpool({ id: 1, material: 'PLA', weight_used: 100 }),
      makeSpool({ id: 2, material: 'PETG', weight_used: 200 }),
    ];
    const items = computeDisplayItems(spools, {});
    expect(items).toHaveLength(2);
    expect(items.every((i) => i.type === 'single')).toBe(true);
  });

  it('returns empty array for empty input', () => {
    const items = computeDisplayItems([], {});
    expect(items).toHaveLength(0);
  });
});
