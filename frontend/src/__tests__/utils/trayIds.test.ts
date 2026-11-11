/**
 * Parity with the backend: the cases of backend/tests/unit/test_tray_ids.py
 * against utils/trayIds (and the amsHelpers wrappers built on it), with the
 * backend's expected values. Backend-only parts (parsing `info`, raw tray_now,
 * wire encodings, loaded-tray test, tray bits) have no frontend twin.
 */
import { describe, expect, it } from 'vitest';
import {
  AmsType,
  externalHotend,
  externalSlotIndex,
  externalTrayId,
  globalTrayId,
  isExternalTray,
  isRackAms,
  layoutAmsType,
  locateTray,
  trayLayoutOf,
  unitAmsType,
  type TrayLayout,
} from '../../utils/trayIds';
import { getAmsLabel, getGlobalTrayId } from '../../utils/amsHelpers';

// The H3C as the status API reports it.
const H3C_UNITS = [{ id: 32, ams_type: AmsType.ZMULTI_A }];
const H3C_VT = Array.from({ length: 9 }, (_, k) => ({ id: 247 + k }));
const H3C = trayLayoutOf(H3C_UNITS, H3C_VT);

// The backend's TrayLayout.slot_key / tray_from_slot_key, from the frontend pieces.
function slotKey(layout: TrayLayout, trayId: number): [number, number] | null {
  if (isExternalTray(trayId, true)) return [255, externalSlotIndex(trayId)];
  return locateTray(layout, trayId);
}
function trayFromSlotKey(layout: TrayLayout, amsId: number, trayId: number): number {
  return getGlobalTrayId(amsId, trayId, amsId === 255, layoutAmsType(layout, amsId));
}

describe('AMS type', () => {
  it('is told from the id without a type', () => {
    expect(unitAmsType(0)).toBe(AmsType.AMS);
    expect(unitAmsType(3)).toBe(AmsType.AMS);
    expect(unitAmsType(128)).toBe(AmsType.N3S);
    expect(unitAmsType(135)).toBe(AmsType.N3S);
    expect(unitAmsType(32)).toBe(AmsType.ZMULTI_A);
  });

  it('takes the reported type over the id, except for an AMS-HT', () => {
    expect(unitAmsType(32, AmsType.ZMULTI_B)).toBe(AmsType.ZMULTI_B);
    expect(unitAmsType(128, AmsType.AMS)).toBe(AmsType.N3S);
    expect(unitAmsType(5, null)).toBe(AmsType.AMS);
    expect(unitAmsType(5, AmsType.EXT_SPOOL)).toBe(AmsType.AMS);
  });

  it('knows the rack types', () => {
    expect(isRackAms(AmsType.ZMULTI_A) && isRackAms(AmsType.ZMULTI_B)).toBe(true);
    for (const kind of [AmsType.AMS, AmsType.N3F, AmsType.N3S, AmsType.AMS_LITE_MIXED]) {
      expect(isRackAms(kind)).toBe(false);
    }
  });
});

describe('global tray id', () => {
  it.each([
    [0, 0, AmsType.AMS, 0],
    [1, 2, AmsType.AMS, 6],
    [3, 3, AmsType.N3F, 15],
    [6, 1, AmsType.AMS_LITE, 25], // the A2L's AMS Lite, stored as unit 6
    [16, 1, AmsType.AMS_LITE_MIXED, 25], // ...and Studio's own numbering of its unit 16
    [32, 0, AmsType.ZMULTI_A, 28],
    [32, 8, AmsType.ZMULTI_A, 36],
    [33, 0, AmsType.ZMULTI_B, 37],
    [33, 4, AmsType.ZMULTI_B, 41],
    [128, 0, AmsType.N3S, 128],
    [135, 0, AmsType.N3S, 135],
  ])('unit %i slot %i (type %i) is %i', (amsId, slot, kind, expected) => {
    expect(globalTrayId(amsId, slot, kind)).toBe(expected);
    expect(getGlobalTrayId(amsId, slot, false, kind)).toBe(expected);
  });

  it('tells a regular AMS, an AMS-HT and the AMS 3H by their ids without a type', () => {
    expect([0, 1, 2, 3].map((slot) => globalTrayId(1, slot))).toEqual([4, 5, 6, 7]);
    expect(globalTrayId(129, 0)).toBe(129);
    expect(Array.from({ length: 9 }, (_, slot) => globalTrayId(32, slot))).toEqual([28, 29, 30, 31, 32, 33, 34, 35, 36]);
  });

  it('keeps the AMS 3H out of the AMS-HT and regular ranges', () => {
    for (let slot = 0; slot < 9; slot++) {
      const tray = globalTrayId(32, slot);
      expect(tray < 128 || tray > 135).toBe(true);
      expect(tray).toBeGreaterThanOrEqual(16);
    }
  });
});

describe('layout', () => {
  it('reads the H3C as a rack printer', () => {
    expect(H3C.rack).toBe(true);
    expect(H3C.units).toEqual([{ id: 32, type: AmsType.ZMULTI_A }]);
    expect(layoutAmsType(H3C, 32)).toBe(AmsType.ZMULTI_A);
  });

  it('numbers the H3C both ways', () => {
    for (let slot = 0; slot < 9; slot++) {
      const tray = globalTrayId(32, slot, layoutAmsType(H3C, 32));
      expect(tray).toBe(28 + slot);
      expect(locateTray(H3C, tray)).toEqual([32, slot]);
      expect(slotKey(H3C, tray)).toEqual([32, slot]);
      expect(trayFromSlotKey(H3C, 32, slot)).toBe(tray);
    }
  });

  it('numbers a regular printer both ways, unchanged', () => {
    const layout = trayLayoutOf([{ id: 0, ams_type: AmsType.AMS }, { id: 1, ams_type: AmsType.AMS }, { id: 128 }]);
    expect(layout.rack).toBe(false);
    for (const amsId of [0, 1]) {
      for (let slot = 0; slot < 4; slot++) {
        const tray = globalTrayId(amsId, slot, layoutAmsType(layout, amsId));
        expect(tray).toBe(amsId * 4 + slot);
        expect(locateTray(layout, tray)).toEqual([amsId, slot]);
        expect(trayFromSlotKey(layout, amsId, slot)).toBe(tray);
        expect(slotKey(layout, tray)).toEqual([amsId, slot]);
      }
    }
    expect(globalTrayId(128, 0, layoutAmsType(layout, 128))).toBe(128);
    expect(locateTray(layout, 128)).toEqual([128, 0]);
  });

  it('agrees both ways without live data', () => {
    // No AMS data: 28-36 are the AMS 3H's (unit 32), 247-255 external inputs.
    const empty = trayLayoutOf([]);
    for (let slot = 0; slot < 9; slot++) {
      expect(trayFromSlotKey(empty, 32, slot)).toBe(28 + slot);
      expect(slotKey(empty, 28 + slot)).toEqual([32, slot]);
    }
    for (let index = 0; index < 9; index++) {
      expect(slotKey(empty, trayFromSlotKey(empty, 255, index))).toEqual([255, index]);
    }
  });

  it('keeps 28-31 for a listed regular unit 7', () => {
    const layout = trayLayoutOf([{ id: 7, ams_type: AmsType.AMS }]);
    expect(slotKey(layout, 28)).toEqual([7, 0]);
    expect(slotKey(layout, 31)).toEqual([7, 3]);
    expect(trayFromSlotKey(layout, 7, 2)).toBe(30);
  });

  it('numbers an AMS-B by its type', () => {
    const layout = trayLayoutOf([{ id: 40, ams_type: AmsType.ZMULTI_B }]);
    expect(layout.rack).toBe(true);
    expect(globalTrayId(40, 2, layoutAmsType(layout, 40))).toBe(39);
    expect(locateTray(layout, 39)).toEqual([40, 2]);
  });

  it('takes an explicit type from the API', () => {
    const layout = trayLayoutOf([{ id: 32, ams_type: 6 }]);
    expect(globalTrayId(32, 4, layoutAmsType(layout, 32))).toBe(32);
    expect(locateTray(layout, 32)).toEqual([32, 4]);
  });

  it('counts a unit listed twice once', () => {
    const layout = trayLayoutOf([{ id: 32, ams_type: 6 }, { id: 32, ams_type: 6 }]);
    expect(layout.units).toEqual([{ id: 32, type: AmsType.ZMULTI_A }]);
    expect(locateTray(layout, 32)).toEqual([32, 4]);
  });
});

describe('external inputs', () => {
  it('are 254 and 255 everywhere', () => {
    for (const tray of [254, 255]) {
      expect(isExternalTray(tray)).toBe(true);
      expect(isExternalTray(tray, true)).toBe(true);
    }
  });

  it('are 247-253 only on a rack printer', () => {
    for (let tray = 247; tray <= 253; tray++) {
      expect(isExternalTray(tray)).toBe(false);
      expect(isExternalTray(tray, true)).toBe(true);
      expect(isExternalTray(tray, H3C.rack)).toBe(true);
    }
    expect(isExternalTray(250, trayLayoutOf([{ id: 0 }], [{ id: 254 }, { id: 255 }]).rack)).toBe(false);
  });

  it('never include an AMS slot', () => {
    for (const tray of [0, 15, 28, 36, 128]) expect(isExternalTray(tray, true)).toBe(false);
  });

  it('feed hotend 255 - id', () => {
    expect([255, 254, 253, 252, 251, 250, 249, 248, 247].map(externalHotend)).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8]);
  });

  it('are stored under ams_id 255 as tray 0-8', () => {
    expect(externalSlotIndex(254)).toBe(0);
    expect(externalSlotIndex(255)).toBe(1);
    expect([247, 248, 249, 250, 251, 252, 253].map(externalSlotIndex)).toEqual([2, 3, 4, 5, 6, 7, 8]);
    for (let tray = 247; tray <= 255; tray++) {
      expect(externalTrayId(externalSlotIndex(tray))).toBe(tray);
      expect(slotKey(H3C, tray)).toEqual([255, externalSlotIndex(tray)]);
      expect(trayFromSlotKey(H3C, 255, externalSlotIndex(tray))).toBe(tray);
    }
    expect(locateTray(H3C, 250)).toBeNull();
  });

  it('make a rack printer when one of 247-253 is reported', () => {
    expect(trayLayoutOf([], H3C_VT).rack).toBe(true);
    expect(trayLayoutOf([], [{ id: 254 }, { id: 255 }]).rack).toBe(false);
  });

  it('keep their slot keys on a regular printer', () => {
    const layout = trayLayoutOf([{ id: 0 }], [{ id: 254 }]);
    expect(slotKey(layout, 254)).toEqual([255, 0]);
    expect(slotKey(layout, 255)).toEqual([255, 1]);
    // 247-253 are only ever external inputs, so they keep their stored key everywhere.
    expect(slotKey(layout, 250)).toEqual([255, 5]);
    expect(locateTray(layout, 250)).toBeNull();
  });
});

describe('unit labels', () => {
  it('match the backend', () => {
    expect(getAmsLabel(0)).toBe('AMS-A');
    expect(getAmsLabel(129)).toBe('HT-B');
    expect(getAmsLabel(32)).toBe('AMS 3H');
  });
});

describe('model fallback', () => {
  it('gives an H3C without AMS data its AMS 3H', () => {
    const ams3h = { units: [{ id: 32, type: AmsType.ZMULTI_A }], rack: true };
    expect(trayLayoutOf(null, null, 'H3C')).toEqual(ams3h);
    expect(trayLayoutOf([], [], 'N10')).toEqual(ams3h);
    expect(slotKey(trayLayoutOf(null, null, 'H3C'), 250)).toEqual([255, 5]);
    expect(isExternalTray(250, trayLayoutOf(null, null, 'H3C').rack)).toBe(true);
    expect(trayLayoutOf(null, null, 'X1C').rack).toBe(false);
    expect(trayLayoutOf(null, null).rack).toBe(false);
  });

  it('never outranks live units', () => {
    expect(trayLayoutOf([{ id: 0, ams_type: 1 }], [{ id: 254 }], 'H3C').rack).toBe(false);
  });
});
