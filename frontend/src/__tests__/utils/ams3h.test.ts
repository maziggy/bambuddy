/**
 * The H3C's AMS 3H (unit 32, AMS type 6, global trays 28-36) and its
 * per-hotend external inputs 247-255 ("Ext-n") in the amsHelpers labels and
 * card layout.
 */
import { describe, expect, it } from 'vitest';
import {
  ams3hDryerGridColumns,
  ams3hSlotGridPlacement,
  amsHasLoadedTray,
  amsUnitSlotCount,
  formatAssignmentSlotLabel,
  formatSlotLabel,
  getAmsLabel,
  getGlobalTrayId,
  isAms3hUnit,
  preferLowestSortKey,
  rackExternalLabel,
  spoolBuddyAmsName,
} from '../../utils/amsHelpers';

const ams3h = { id: 32, ams_type: 6 };

describe('the AMS 3H unit', () => {
  it('is told by its AMS type, or by its unit id 32 when the type is not at hand', () => {
    expect(isAms3hUnit(ams3h)).toBe(true);
    expect(isAms3hUnit({ id: 32 })).toBe(true);
    expect(isAms3hUnit({ id: 0, ams_type: 1 })).toBe(false);
    expect(isAms3hUnit({ id: 128 })).toBe(false);
    expect(isAms3hUnit(undefined)).toBe(false);
  });

  it('draws nine slots, a regular AMS four and an AMS-HT one', () => {
    expect(amsUnitSlotCount(ams3h)).toBe(9);
    expect(amsUnitSlotCount({ id: 1 })).toBe(4);
    expect(amsUnitSlotCount({ id: 129 })).toBe(1);
  });

  it('numbers its slots 28-36, clear of the AMS-HT range', () => {
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8].map((slot) => getGlobalTrayId(32, slot, false, 6))).toEqual([28, 29, 30, 31, 32, 33, 34, 35, 36]);
    expect(getGlobalTrayId(32, 6, false)).toBe(34);
    // Every other kind keeps its numbers.
    expect(getGlobalTrayId(1, 2, false)).toBe(6);
    expect(getGlobalTrayId(129, 0, false)).toBe(129);
    expect(getGlobalTrayId(255, 0, true)).toBe(254);
    expect(getGlobalTrayId(255, 1, true)).toBe(255);
  });

  it('sees its slot on the toolhead', () => {
    const unit = { ...ams3h, tray: [{ id: 6 }, { id: 7 }] };
    expect(amsHasLoadedTray(unit, 34, undefined)).toBe(true);
    expect(amsHasLoadedTray(unit, 6, undefined)).toBe(false);
  });

  it('is labelled AMS 3H and its slots 3H-1..3H-9', () => {
    expect(getAmsLabel(32)).toBe('AMS 3H');
    expect(formatSlotLabel(32, 6, false, false)).toBe('3H-7');
    expect(formatSlotLabel(1, 2, false, false)).toBe('B3');
    expect(spoolBuddyAmsName(32)).toBe('AMS 3H');
  });

  it('follows the reported AMS type over the unit id', () => {
    expect(getAmsLabel(32, 7)).not.toBe('AMS 3H');
    expect(formatSlotLabel(32, 6, false, false, 7)).not.toBe('3H-7');
    expect(getAmsLabel(40, 6)).toBe('AMS 3H');
    expect(formatSlotLabel(40, 0, false, false, 6)).toBe('3H-1');
    expect(spoolBuddyAmsName(40, 6)).toBe('AMS 3H');
  });

  it('leaves the SpoolBuddy names of every other unit as they were', () => {
    expect(spoolBuddyAmsName(0)).toBe('AMS A');
    expect(spoolBuddyAmsName(3)).toBe('AMS D');
    expect(spoolBuddyAmsName(129)).toBe('AMS HT B');
    expect(spoolBuddyAmsName(6)).toBe('AMS 6');
  });

  it('sorts its slots by position for the prefer-lowest tie-break', () => {
    const key = (trayId: number) => preferLowestSortKey({ globalTrayId: 28 + trayId, amsId: 32, trayId }, undefined)[2];
    expect(key(0)).toBeLessThan(key(8));
    expect(key(8)).toBeLessThan(preferLowestSortKey({ globalTrayId: 128, amsId: 128, trayId: 0 }, undefined)[2]);
  });
});

describe('slot labels outside the printer card', () => {
  it('labels inventory assignments on an H3C by slot and by hotend input', () => {
    expect(formatAssignmentSlotLabel({ ams_id: 32, tray_id: 6, printer_model: 'H3C' })).toBe('3H-7');
    expect(formatAssignmentSlotLabel({ ams_id: 32, tray_id: 8 })).toBe('3H-9');
    expect(formatAssignmentSlotLabel({ ams_id: 1, tray_id: 2, printer_model: 'X1C' })).toBe('B3');
    // External inputs: (255, 1) = 255 feeds hotend 0 -> Ext-1, (255, 5) = 250 -> Ext-6.
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 1, printer_model: 'H3C' })).toBe('Ext-1');
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 5, printer_model: 'H3C' })).toBe('Ext-6');
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 0, printer_model: 'X1C' })).toBe('Ext');
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 1 })).toBe('Ext');
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 8, printer_model: 'N10' })).toBe('Ext-3');
    expect(formatAssignmentSlotLabel({ ams_id: 129, tray_id: 0, printer_model: 'H3C' })).toBe('HT-B');
  });

  it('numbers a per-hotend input like Bambu Studio: Ext-n feeds PrintTool n', () => {
    expect(rackExternalLabel(255)).toBe('Ext-1');
    expect(rackExternalLabel(247)).toBe('Ext-9');
  });
});

describe('AMS 3H card layout', () => {
  it('puts 2, 4, 6, 8 in the back row and 1-9 odd in front, staggered, as on the unit', () => {
    const rows = [1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => [n, ams3hSlotGridPlacement(n)]);
    expect(rows.filter(([, p]) => (p as { gridRow: number }).gridRow === 1).map(([n]) => n)).toEqual([2, 4, 6, 8]);
    expect(rows.filter(([, p]) => (p as { gridRow: number }).gridRow === 2).map(([n]) => n)).toEqual([1, 3, 5, 7, 9]);
    // Each back slot starts half a slot after the front slot before it.
    expect(ams3hSlotGridPlacement(1).gridColumn).toBe('1 / span 2');
    expect(ams3hSlotGridPlacement(2).gridColumn).toBe('2 / span 2');
    expect(ams3hSlotGridPlacement(9).gridColumn).toBe('9 / span 2');
  });

  it('boxes the dryer around slots 6 and 8 with its readings in the free column after slot 8', () => {
    // As captured: zone_slots [5, 7] (tray ids 0-8) = slots 6 and 8.
    expect(ams3hDryerGridColumns([5, 7])).toEqual({ start: 6, end: 11, info: 10 });
  });

  it('draws no readings column when the dryer does not end at slot 8', () => {
    expect(ams3hDryerGridColumns([1, 3])).toEqual({ start: 2, end: 6, info: null });
  });

  it('draws no dryer for no slots or front-row slots', () => {
    expect(ams3hDryerGridColumns(undefined)).toBeNull();
    expect(ams3hDryerGridColumns([])).toBeNull();
    expect(ams3hDryerGridColumns([4, 6])).toBeNull(); // slots 5 and 7 are in front
  });
});
