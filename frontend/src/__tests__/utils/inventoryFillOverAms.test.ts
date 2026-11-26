import { describe, expect, it } from 'vitest';

import { inventoryFillOverAms } from '../../utils/amsHelpers';

const UUID = 'A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4';
const NO_UUID = '00000000000000000000000000000000';
const NO_TAG = '0000000000000000';

// #676 / #3738: when an inventory spool at 0% gives way to the AMS reading.
describe('inventoryFillOverAms', () => {
  it('keeps 0% on a spool without RFID, whatever the AMS reports', () => {
    // The reporter's A1: no tag, remain 100 as a placeholder.
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: 100, tray_uuid: NO_UUID, tag_uid: NO_TAG })).toBe(0);
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: 100, tray_uuid: null, tag_uid: null })).toBe(0);
  });

  it('gives way to an RFID spool the AMS still reads filament on', () => {
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: 40, tray_uuid: UUID, tag_uid: NO_TAG })).toBeNull();
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: 40, tray_uuid: NO_UUID, tag_uid: '1A2B3C4D' })).toBeNull();
  });

  it('keeps 0% when the RFID spool reads empty or unknown too', () => {
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: 0, tray_uuid: UUID })).toBe(0);
    expect(inventoryFillOverAms(0, { tray_type: 'PLA', remain: -1, tray_uuid: UUID })).toBe(0);
    expect(inventoryFillOverAms(0, { tray_type: '', remain: 40, tray_uuid: UUID })).toBe(0);
  });

  it('keeps any other inventory fill, and none stays none', () => {
    expect(inventoryFillOverAms(35, { tray_type: 'PLA', remain: 90, tray_uuid: UUID })).toBe(35);
    expect(inventoryFillOverAms(null, { tray_type: 'PLA', remain: 90, tray_uuid: UUID })).toBeNull();
    expect(inventoryFillOverAms(undefined, null)).toBeNull();
  });
});
