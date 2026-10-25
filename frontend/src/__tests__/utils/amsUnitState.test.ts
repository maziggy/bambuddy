/**
 * AMS-HT is told apart by its unit id (128-135), not by reporting one tray;
 * and an AMS whose filament is threaded into the feed tube cannot rotate its
 * spools for drying. The tray state code for "loaded" (11) differs between
 * firmware versions, so the slot the printer reports as loaded counts too.
 */

import { describe, it, expect } from 'vitest';
import { amsHasLoadedTray, isAmsHtUnit, trayForSlot } from '../../utils/amsHelpers';

const tray = (id: number, state?: number) => ({ id, state });

describe('isAmsHtUnit', () => {
  it('is the unit id range', () => {
    expect(isAmsHtUnit(128)).toBe(true);
    expect(isAmsHtUnit(135)).toBe(true);
    expect(isAmsHtUnit(0)).toBe(false);
    expect(isAmsHtUnit(136)).toBe(false);
  });
});

describe('amsHasLoadedTray', () => {
  it('sees the loaded state code', () => {
    expect(amsHasLoadedTray({ id: 0, tray: [tray(0, 10), tray(1, 11)] }, 255, {})).toBe(true);
  });

  it('sees the slot the printer reports as loaded, whatever its state code', () => {
    expect(amsHasLoadedTray({ id: 1, tray: [tray(0, 10), tray(1, 10)] }, 5, {})).toBe(true);
    expect(amsHasLoadedTray({ id: 1, tray: [tray(0, 10), tray(1, 10)] }, 1, {})).toBe(false);
  });

  it('sees an AMS-HT that is loaded', () => {
    expect(amsHasLoadedTray({ id: 128, tray: [tray(0, 10)] }, 128, {})).toBe(true);
  });

  it('sees a hotend fed from it on a two-nozzle printer', () => {
    // tray_now names only the active nozzle's slot; the other hotend's comes
    // from extruder_slots.
    const slots = { '1': { ams_id: 2, slot_id: 3, has_filament: true } };
    expect(amsHasLoadedTray({ id: 2, tray: [tray(3, 10)] }, 255, slots)).toBe(true);
    expect(amsHasLoadedTray({ id: 2, tray: [tray(3, 10)] }, 255, { '1': { ...slots['1'], has_filament: false } })).toBe(false);
  });

  it('is false for an idle AMS', () => {
    expect(amsHasLoadedTray({ id: 0, tray: [tray(0, 10), tray(1, 9)] }, 255, {})).toBe(false);
    expect(amsHasLoadedTray(undefined, 0, {})).toBe(false);
  });
});

describe('trayForSlot', () => {
  it('finds a slot by its tray id when the AMS reports only some trays', () => {
    const trays = [{ id: 2, tray_type: 'PETG' }];
    expect(trayForSlot(trays, 2)?.tray_type).toBe('PETG');
    expect(trayForSlot(trays, 0)).toBeUndefined();
  });

  it('falls back to the position when the ids do not tell the trays apart', () => {
    const trays = [{ id: 0, tray_type: 'PLA' }, { id: 0, tray_type: 'PETG' }];
    expect(trayForSlot(trays, 1)?.tray_type).toBe('PETG');
  });
});
