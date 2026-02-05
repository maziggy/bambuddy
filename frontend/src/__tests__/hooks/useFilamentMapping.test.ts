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
function createPrinterStatus(ams: PrinterStatus['ams'], vt_tray?: PrinterStatus['vt_tray']): PrinterStatus {
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
      { tray_type: 'TPU', tray_color: '0000FF', tray_info_idx: 'EXT001' }
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
    expect(result[0].globalTrayId).toBe(512);  // 128 * 4 + 0
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
      { tray_type: 'TPU', tray_color: '0000FF', tray_info_idx: 'EXT001' }
    );

    const result = computeAmsMapping(reqs, status);

    expect(result).toEqual([254]);  // External spool global ID
  });
});
