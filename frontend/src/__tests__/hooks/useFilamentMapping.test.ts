/**
 * Tests for the useFilamentMapping hook and helper functions.
 *
 * Tests the tray_info_idx matching logic that ensures the exact spool
 * selected during slicing is used when multiple trays have identical filament.
 */

import { describe, it, expect } from 'vitest';
import {
  buildLoadedFilaments,
  computeAmsMapping,
} from '../../hooks/useFilamentMapping';
import type { PrinterStatus } from '../../api/client';

// Helper to create a minimal printer status with AMS data
function createPrinterStatus(ams: PrinterStatus['ams'], vt_tray: PrinterStatus['vt_tray'] = []): PrinterStatus {
  return {
    ams,
    vt_tray,
  } as PrinterStatus;
}

describe('buildLoadedFilaments', () => {
  it('returns empty array for undefined status', () => {
    const result = buildLoadedFilaments(undefined);
    expect(result).toEqual([]);
  });

  it('extracts filaments from AMS units', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00' },
          { id: 1, tray_type: 'PETG', tray_color: '00FF00', tray_info_idx: 'GFA01' },
        ],
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({
      type: 'PLA',
      color: '#FF0000',
      amsId: 0,
      trayId: 0,
      globalTrayId: 0,
      trayInfoIdx: 'GFA00',
    });
    expect(result[1]).toMatchObject({
      type: 'PETG',
      color: '#00FF00',
      globalTrayId: 1,
      trayInfoIdx: 'GFA01',
    });
  });

  it('includes tray_info_idx from AMS trays', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'P4d64437' },
        ],
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result[0].trayInfoIdx).toBe('P4d64437');
  });

  it('handles missing tray_info_idx', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000' },  // No tray_info_idx
        ],
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result[0].trayInfoIdx).toBe('');
  });

  it('extracts external spool with tray_info_idx', () => {
    const status = createPrinterStatus(
      [],
      [{ tray_type: 'TPU', tray_color: '0000FF', tray_info_idx: 'EXT001' }]
    );

    const result = buildLoadedFilaments(status);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      type: 'TPU',
      isExternal: true,
      globalTrayId: 254,
      trayInfoIdx: 'EXT001',
    });
  });

  it('skips empty trays', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00' },
          { id: 1, tray_type: '', tray_color: '' },  // Empty tray
          { id: 2 },  // No tray_type
        ],
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result).toHaveLength(1);
    expect(result[0].type).toBe('PLA');
  });

  it('marks AMS-HT units correctly', () => {
    const status = createPrinterStatus([
      {
        id: 128,  // AMS-HT typically has high ID
        tray: [
          { id: 0, tray_type: 'PLA-CF', tray_color: '000000', tray_info_idx: 'HT001' },
        ],  // Single tray = AMS-HT
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result[0].isHt).toBe(true);
    expect(result[0].globalTrayId).toBe(128);  // AMS-HT uses ams_id directly
  });
});

describe('computeAmsMapping', () => {
  it('returns undefined for empty filament requirements', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);

    expect(computeAmsMapping(undefined, status)).toBeUndefined();
    expect(computeAmsMapping({ filaments: [] }, status)).toBeUndefined();
  });

  it('returns undefined when no filaments loaded', () => {
    const reqs = {
      filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10 }],
    };

    expect(computeAmsMapping(reqs, undefined)).toBeUndefined();
    expect(computeAmsMapping(reqs, createPrinterStatus([]))).toBeUndefined();
  });

  it('matches by tray_info_idx with highest priority', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, tray_info_idx: 'GFA01' },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA00' },  // Same color, wrong idx
          { id: 1, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA01' },  // Exact idx match
          { id: 2, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA02' },  // Same color, wrong idx
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([1]);  // Should pick tray 1, not tray 0
  });

  it('matches multiple identical filaments by tray_info_idx (H2D Pro scenario)', () => {
    // This is the exact scenario from issue #245 - multiple black PLA spools
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 50, tray_info_idx: 'GFA03' },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA00' },
          { id: 1, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA01' },
          { id: 2, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA02' },
          { id: 3, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA03' },  // This one
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([3]);  // Should pick tray 3, not tray 0
  });

  it('falls back to color match when tray_info_idx is empty', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, tray_info_idx: '' },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '00FF00', tray_info_idx: 'GFA00' },  // Wrong color
          { id: 1, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA01' },  // Color match
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([1]);
  });

  it('falls back to color match when tray_info_idx does not match', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, tray_info_idx: 'OLD_SPOOL' },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'NEW_SPOOL' },  // Different idx, same color
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0]);  // Falls back to color match
  });

  it('matches by type only when color differs', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10 },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '0000FF' },  // Same type, different color
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0]);  // Type-only match
  });

  it('returns -1 for unmatched slots', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'TPU', color: '#FF0000', used_grams: 10 },  // No TPU loaded
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000' },
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([-1]);
  });

  it('avoids duplicate tray assignment', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10 },
        { slot_id: 2, type: 'PLA', color: '#FF0000', used_grams: 10 },  // Same requirements
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000' },  // Only one PLA
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0, -1]);  // First slot gets the match, second is unmatched
  });

  it('handles multi-slot mapping with tray_info_idx', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, tray_info_idx: 'GFA00' },
        { slot_id: 2, type: 'PLA', color: '#000000', used_grams: 10, tray_info_idx: 'GFA02' },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA00' },
          { id: 1, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA01' },
          { id: 2, tray_type: 'PLA', tray_color: '000000', tray_info_idx: 'GFA02' },
        ],
      },
    ]);

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0, 2]);  // Each slot gets its specific tray
  });

  it('handles external spool matching', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'TPU', color: '#0000FF', used_grams: 10, tray_info_idx: 'EXT001' },
      ],
    };
    const status = createPrinterStatus(
      [],
      [{ tray_type: 'TPU', tray_color: '0000FF', tray_info_idx: 'EXT001' }]
    );

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([254]);  // External spool global ID
  });
});

describe('buildLoadedFilaments - nozzle awareness', () => {
  it('sets extruderId from ams_extruder_map', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
      {
        id: 1,
        tray: [{ id: 0, tray_type: 'PETG', tray_color: '00FF00' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = buildLoadedFilaments(status);

    expect(result[0].extruderId).toBe(1);  // AMS 0 → left nozzle
    expect(result[1].extruderId).toBe(0);  // AMS 1 → right nozzle
  });

  it('leaves extruderId undefined when no ams_extruder_map', () => {
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);

    const result = buildLoadedFilaments(status);

    expect(result[0].extruderId).toBeUndefined();
  });
});

describe('computeAmsMapping - nozzle filtering', () => {
  it('filters candidates by nozzle_id when set', () => {
    // Filament requires left nozzle (extruder 1), only AMS 0 is on left
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, nozzle_id: 1 },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,  // Left nozzle
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
      {
        id: 1,  // Right nozzle
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0]);  // AMS 0, tray 0 (on left nozzle)
  });

  it('filters to right nozzle when nozzle_id=0', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, nozzle_id: 0 },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,  // Left nozzle
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
      {
        id: 1,  // Right nozzle
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([4]);  // AMS 1, tray 0 (global ID = 1*4+0 = 4, on right nozzle)
  });

  it('returns -1 when target nozzle has no trays (hard filter)', () => {
    // Requires nozzle_id=1 (left), but no AMS units are on left nozzle
    // Hard filter: cross-nozzle assignment causes "position of left hotend is abnormal"
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, nozzle_id: 1 },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,  // Right nozzle only
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 0 };  // AMS 0 → right nozzle, none on left

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([-1]);  // Hard filter: no fallback to wrong nozzle
  });

  it('stays restricted when target nozzle has trays but wrong type', () => {
    // Left nozzle has PETG, right has PLA — but requires PLA on left
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, nozzle_id: 1 },
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,  // Left nozzle - only PETG
        tray: [{ id: 0, tray_type: 'PETG', tray_color: '00FF00' }],
      },
      {
        id: 1,  // Right nozzle - has PLA
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([-1]);  // No PLA on left nozzle, stays restricted
  });

  it('skips nozzle filtering when nozzle_id is undefined', () => {
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10 },  // No nozzle_id
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,
        tray: [{ id: 0, tray_type: 'PETG', tray_color: '00FF00' }],
      },
      {
        id: 1,
        tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000' }],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([4]);  // Picks best match regardless of nozzle
  });

  it('handles dual-nozzle multi-slot mapping', () => {
    // Two filaments: one for left, one for right
    const reqs = {
      filaments: [
        { slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 10, nozzle_id: 1 },  // Left
        { slot_id: 2, type: 'PETG', color: '#00FF00', used_grams: 10, nozzle_id: 0 }, // Right
      ],
    };
    const status = createPrinterStatus([
      {
        id: 0,  // Left nozzle
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF0000' },
        ],
      },
      {
        id: 1,  // Right nozzle
        tray: [
          { id: 0, tray_type: 'PETG', tray_color: '00FF00' },
        ],
      },
    ]);
    (status as any).ams_extruder_map = { '0': 1, '1': 0 };

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([0, 4]);  // Left gets AMS0-T0, Right gets AMS1-T0
  });
});

// ============================================================================
// MODEL-SPECIFIC TESTS: Real data from actual printers
// ============================================================================

/**
 * H2D real data fixture (from live API response 2026-02-18).
 *
 * Configuration:
 *   LEFT nozzle (extruder 1): AMS 0 (4-slot), AMS 2 (4-slot)
 *   RIGHT nozzle (extruder 0): AMS 1 (4-slot), AMS-HT 128 (1-slot, empty)
 *   External: 254 (Ext-L, LEFT nozzle), 255 (Ext-R, RIGHT nozzle)
 *
 * ams_extruder_map: {"0": 1, "1": 0, "2": 1, "128": 0}
 */
function createH2DStatus(): PrinterStatus {
  const status = createPrinterStatus(
    [
      {
        id: 0, // LEFT nozzle (extruder 1)
        humidity: 24,
        temp: 21.4,
        tray: [
          { id: 0, tray_type: 'PETG', tray_color: 'FFFFFFFF', tray_info_idx: 'GFG02', tray_sub_brands: 'PETG HF' },
          { id: 1, tray_type: 'PLA', tray_color: 'C8C8C8FF', tray_info_idx: 'GFA06', tray_sub_brands: 'PLA Silk+' },
          { id: 2, tray_type: 'PETG', tray_color: '875718FF', tray_info_idx: 'GFG02', tray_sub_brands: 'PETG HF' },
          { id: 3, tray_type: 'PLA', tray_color: '000000FF', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic' },
        ],
      },
      {
        id: 1, // RIGHT nozzle (extruder 0)
        humidity: 25,
        temp: 21.7,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FFFFFFFF', tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic' },
          { id: 1, tray_type: 'PETG', tray_color: '000000FF', tray_info_idx: 'GFG02', tray_sub_brands: 'PETG HF' },
          { id: 2, tray_type: 'PLA', tray_color: '5F6367FF', tray_info_idx: 'GFA06', tray_sub_brands: 'PLA Silk+' },
          { id: 3, tray_type: 'PLA', tray_color: 'B39B84FF', tray_info_idx: 'GFA02', tray_sub_brands: 'PLA Metal' },
        ],
      },
      {
        id: 128, // AMS-HT, RIGHT nozzle (extruder 0) — empty
        humidity: 48,
        temp: 21.4,
        tray: [
          { id: 0 }, // empty tray
        ],
      },
      {
        id: 2, // LEFT nozzle (extruder 1)
        humidity: 18,
        temp: 24.0,
        tray: [
          { id: 0, tray_type: 'PLA-S', tray_color: 'FFFFFFFF', tray_info_idx: 'P8aa1726' },
          { id: 1, tray_type: 'PLA', tray_color: '56B7E6FF', tray_info_idx: 'PFUS9924' },
          { id: 2, tray_type: 'PETG', tray_color: '6EE53CFF', tray_info_idx: 'GFG02', tray_sub_brands: 'PETG HF' },
          { id: 3, tray_type: 'PLA', tray_color: 'FF0000FF', tray_info_idx: 'PFUS9ac9' },
        ],
      },
    ],
    [
      { id: 254, tray_type: 'PLA', tray_color: '000000FF', tray_info_idx: 'P4d64437' }, // Ext-L (loaded)
      { id: 255, tray_type: '', tray_color: '00000000' }, // Ext-R (empty)
    ]
  );
  (status as any).ams_extruder_map = { '0': 1, '1': 0, '2': 1, '128': 0 };
  return status;
}

/**
 * X1C real data fixture (from live API response 2026-02-18).
 *
 * Configuration:
 *   Single nozzle (extruder 0): AMS 0 (4-slot), AMS 1 (4-slot)
 *   External: 254 (single)
 *
 * ams_extruder_map: {"0": 0, "1": 0}  ← NOT empty, all on extruder 0
 */
function createX1CStatus(): PrinterStatus {
  const status = createPrinterStatus(
    [
      {
        id: 0,
        humidity: 23,
        temp: 26.1,
        tray: [
          { id: 0 }, // empty (has tray_color but no tray_type)
          { id: 1 }, // empty
          { id: 2 }, // empty (has tray_color FFFFFFFF but no tray_type)
          { id: 3 }, // empty
        ],
      },
      {
        id: 1,
        humidity: 20,
        temp: 25.9,
        tray: [
          { id: 0 }, // empty
          { id: 1, tray_type: 'PLA', tray_color: 'EBCFA6FF', tray_info_idx: 'PFUS22b2' },
          { id: 2, tray_type: 'PLA', tray_color: 'FCECD6FF', tray_info_idx: 'P4d64437' },
          { id: 3, tray_type: 'PLA', tray_color: '0066FFFF', tray_info_idx: 'P4d64437' },
        ],
      },
    ],
    [
      { id: 254, tray_type: '', tray_color: '00000000' }, // empty
    ]
  );
  (status as any).ams_extruder_map = { '0': 0, '1': 0 };
  return status;
}

describe('H2D model tests (dual nozzle, real data)', () => {
  describe('buildLoadedFilaments', () => {
    it('assigns correct extruderId to all AMS units', () => {
      const result = buildLoadedFilaments(createH2DStatus());

      // AMS 0 trays → extruder 1 (LEFT)
      const ams0 = result.filter((f) => f.amsId === 0);
      expect(ams0).toHaveLength(4);
      ams0.forEach((f) => expect(f.extruderId).toBe(1));

      // AMS 1 trays → extruder 0 (RIGHT)
      const ams1 = result.filter((f) => f.amsId === 1);
      expect(ams1).toHaveLength(4);
      ams1.forEach((f) => expect(f.extruderId).toBe(0));

      // AMS 2 trays → extruder 1 (LEFT)
      const ams2 = result.filter((f) => f.amsId === 2);
      expect(ams2).toHaveLength(4);
      ams2.forEach((f) => expect(f.extruderId).toBe(1));
    });

    it('computes correct globalTrayId for all AMS types', () => {
      const result = buildLoadedFilaments(createH2DStatus());

      // Regular AMS: amsId * 4 + trayId
      expect(result.find((f) => f.amsId === 0 && f.trayId === 0)?.globalTrayId).toBe(0);
      expect(result.find((f) => f.amsId === 0 && f.trayId === 3)?.globalTrayId).toBe(3);
      expect(result.find((f) => f.amsId === 1 && f.trayId === 0)?.globalTrayId).toBe(4);
      expect(result.find((f) => f.amsId === 1 && f.trayId === 3)?.globalTrayId).toBe(7);
      expect(result.find((f) => f.amsId === 2 && f.trayId === 0)?.globalTrayId).toBe(8);
      expect(result.find((f) => f.amsId === 2 && f.trayId === 3)?.globalTrayId).toBe(11);
    });

    it('skips empty AMS-HT tray (no tray_type)', () => {
      const result = buildLoadedFilaments(createH2DStatus());
      // AMS-HT 128 is empty in real data — should be skipped
      const ht = result.filter((f) => f.amsId === 128);
      expect(ht).toHaveLength(0);
    });

    it('includes loaded external spool with correct extruder', () => {
      const result = buildLoadedFilaments(createH2DStatus());
      const ext = result.filter((f) => f.isExternal);
      // Only Ext-L (254) has filament, Ext-R (255) is empty
      expect(ext).toHaveLength(1);
      expect(ext[0].globalTrayId).toBe(254);
      expect(ext[0].type).toBe('PLA');
      // Ext-L (254) should be LEFT nozzle (extruder 1)
      expect(ext[0].extruderId).toBe(1);
    });

    it('returns 13 loaded filaments total (12 AMS + 1 external)', () => {
      const result = buildLoadedFilaments(createH2DStatus());
      // AMS 0: 4, AMS 1: 4, AMS-HT 128: 0 (empty), AMS 2: 4, External: 1
      expect(result).toHaveLength(13);
    });
  });

  describe('computeAmsMapping', () => {
    it('matches left-nozzle filament to left-nozzle AMS only', () => {
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 10, nozzle_id: 1 },
        ],
      };
      const result = computeAmsMapping(reqs, createH2DStatus());
      // Black PLA on LEFT: AMS 0 T4 (globalTrayId 3) is PLA Basic black on left
      expect(result).toEqual([3]);
    });

    it('matches right-nozzle filament to right-nozzle AMS only', () => {
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FFFFFF', used_grams: 10, nozzle_id: 0 },
        ],
      };
      const result = computeAmsMapping(reqs, createH2DStatus());
      // White PLA on RIGHT: AMS 1 T1 (globalTrayId 4) is PLA Basic white on right
      expect(result).toEqual([4]);
    });

    it('rejects cross-nozzle assignment (right requires type only on left)', () => {
      const reqs = {
        filaments: [
          // PLA-S only exists on AMS 2 T1 (left nozzle), but requires right nozzle
          { slot_id: 1, type: 'PLA-S', color: '#FFFFFF', used_grams: 10, nozzle_id: 0, tray_info_idx: 'P8aa1726' },
        ],
      };
      const result = computeAmsMapping(reqs, createH2DStatus());
      expect(result).toEqual([-1]); // No fallback to wrong nozzle
    });

    it('maps dual-nozzle multi-filament print correctly', () => {
      const reqs = {
        filaments: [
          // Slot 1: PETG white on LEFT → AMS 0 T1 (globalTrayId 0)
          { slot_id: 1, type: 'PETG', color: '#FFFFFF', used_grams: 30, nozzle_id: 1, tray_info_idx: 'GFG02' },
          // Slot 2: PLA white on RIGHT → AMS 1 T1 (globalTrayId 4)
          { slot_id: 2, type: 'PLA', color: '#FFFFFF', used_grams: 20, nozzle_id: 0, tray_info_idx: 'GFA00' },
        ],
      };
      const result = computeAmsMapping(reqs, createH2DStatus());
      expect(result).toEqual([0, 4]);
    });

    it('matches external spool on correct nozzle', () => {
      const reqs = {
        filaments: [
          // Ext-L has black PLA loaded, on LEFT nozzle (extruder 1)
          { slot_id: 1, type: 'PLA', color: '#000000', used_grams: 5, nozzle_id: 1, tray_info_idx: 'P4d64437' },
        ],
      };
      const result = computeAmsMapping(reqs, createH2DStatus());
      expect(result).toEqual([254]); // External spool on left nozzle
    });
  });
});

describe('X1C model tests (single nozzle, real data)', () => {
  describe('buildLoadedFilaments', () => {
    it('assigns all filaments to extruder 0', () => {
      const result = buildLoadedFilaments(createX1CStatus());
      result.forEach((f) => expect(f.extruderId).toBe(0));
    });

    it('computes correct globalTrayId for regular AMS', () => {
      const result = buildLoadedFilaments(createX1CStatus());
      // AMS 1 T2 (tray id 1) → globalTrayId 5
      expect(result.find((f) => f.amsId === 1 && f.trayId === 1)?.globalTrayId).toBe(5);
      // AMS 1 T3 (tray id 2) → globalTrayId 6
      expect(result.find((f) => f.amsId === 1 && f.trayId === 2)?.globalTrayId).toBe(6);
      // AMS 1 T4 (tray id 3) → globalTrayId 7
      expect(result.find((f) => f.amsId === 1 && f.trayId === 3)?.globalTrayId).toBe(7);
    });

    it('returns only loaded trays (3 from AMS 1)', () => {
      const result = buildLoadedFilaments(createX1CStatus());
      // AMS 0: all 4 slots empty, AMS 1: slots 1-3 loaded, External: empty
      expect(result).toHaveLength(3);
    });
  });

  describe('computeAmsMapping', () => {
    it('matches single-nozzle file without nozzle filtering', () => {
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#0066FF', used_grams: 15 },
        ],
      };
      const result = computeAmsMapping(reqs, createX1CStatus());
      // Blue PLA → AMS 1 T4 (globalTrayId 7, color 0066FF)
      expect(result).toEqual([7]);
    });

    it('matches by tray_info_idx across AMS units', () => {
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#EBCFA6', used_grams: 10, tray_info_idx: 'PFUS22b2' },
        ],
      };
      const result = computeAmsMapping(reqs, createX1CStatus());
      // PFUS22b2 uniquely in AMS 1 T2 (globalTrayId 5)
      expect(result).toEqual([5]);
    });

    it('handles non-unique tray_info_idx with color matching', () => {
      // P4d64437 appears in both AMS 1 T3 and T4
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#FCECD6', used_grams: 10, tray_info_idx: 'P4d64437' },
        ],
      };
      const result = computeAmsMapping(reqs, createX1CStatus());
      // Should pick AMS 1 T3 (globalTrayId 6, color FCECD6) over T4 (0066FF)
      expect(result).toEqual([6]);
    });

    it('does not cross-nozzle filter for single-nozzle printer', () => {
      // Even if ams_extruder_map exists, single-nozzle 3MF has no nozzle_id
      const reqs = {
        filaments: [
          { slot_id: 1, type: 'PLA', color: '#EBCFA6', used_grams: 10 },
          { slot_id: 2, type: 'PLA', color: '#0066FF', used_grams: 10 },
        ],
      };
      const result = computeAmsMapping(reqs, createX1CStatus());
      // Both should match freely across all AMS units
      expect(result).toEqual([5, 7]);
    });
  });
});
