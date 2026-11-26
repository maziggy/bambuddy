/**
 * The P3C's AMS 3P (#3737): unit 33, AMS type 7 (Bambu Studio's ZMULTI_B),
 * seven slots with global trays 37-43, and seven per-hotend external inputs
 * 249-255. The twin of backend/tests/unit/test_p3c_ams3p.py.
 */
import { describe, expect, it } from 'vitest';
import {
  amsUnitSlotCount,
  formatAssignmentSlotLabel,
  formatSlotLabel,
  getAmsLabel,
  isAms3hUnit,
  isAms3pUnit,
  rackExternalLabel,
  spoolBuddyAmsName,
} from '../../utils/amsHelpers';
import { getMaintenanceWikiUrl } from '../../utils/maintenanceWikiUrls';
import { getPrinterImage } from '../../utils/printer';
import { mapModelCode } from '../../utils/printerModel';
import { AmsType, globalTrayId, isExternalTray, locateTray, rackSlotCount, trayLayoutOf, unitAmsType } from '../../utils/trayIds';

const ams3p = { id: 33, ams_type: AmsType.ZMULTI_B };

describe('the AMS 3P unit', () => {
  it('is told by its AMS type, or by its unit id 33 when the type is not at hand', () => {
    expect(isAms3pUnit(ams3p)).toBe(true);
    expect(isAms3pUnit({ id: 33 })).toBe(true);
    expect(unitAmsType(33)).toBe(AmsType.ZMULTI_B);
    expect(isAms3hUnit(ams3p)).toBe(false);
    expect(isAms3pUnit({ id: 32 })).toBe(false);
  });

  it('draws seven slots', () => {
    expect(rackSlotCount(AmsType.ZMULTI_B)).toBe(7);
    expect(rackSlotCount(AmsType.ZMULTI_A)).toBe(9);
    expect(amsUnitSlotCount(ams3p)).toBe(7);
  });

  it('numbers its slots 37-43', () => {
    const layout = trayLayoutOf([ams3p]);
    expect(Array.from({ length: 7 }, (_, k) => globalTrayId(33, k, AmsType.ZMULTI_B))).toEqual([37, 38, 39, 40, 41, 42, 43]);
    expect(locateTray(layout, 43)).toEqual([33, 6]);
    // 44 is past its last slot.
    expect(locateTray(layout, 44)).toEqual([11, 0]);
  });

  it('keeps a stored slot without live data', () => {
    expect(locateTray(null, 37)).toEqual([33, 0]);
    expect(locateTray(null, 43)).toEqual([33, 6]);
  });

  it('is labelled AMS 3P and its slots 3P-1..3P-7', () => {
    expect(getAmsLabel(33, AmsType.ZMULTI_B)).toBe('AMS 3P');
    expect(spoolBuddyAmsName(33, AmsType.ZMULTI_B)).toBe('AMS 3P');
    expect(formatSlotLabel(33, 6, false, false, AmsType.ZMULTI_B)).toBe('3P-7');
    expect(formatAssignmentSlotLabel({ ams_id: 33, tray_id: 0, printer_model: 'P3C' })).toBe('3P-1');
  });
});

describe('the P3C', () => {
  it('is a rack printer from its model alone, with the AMS 3P', () => {
    expect(trayLayoutOf(null, null, 'P3C')).toEqual({ units: [{ id: 33, type: AmsType.ZMULTI_B }], rack: true });
    expect(trayLayoutOf(null, null, 'N11').rack).toBe(true);
  });

  it('names its seven external inputs by the PrintTool they feed', () => {
    const layout = trayLayoutOf([ams3p]);
    expect(isExternalTray(249, layout.rack)).toBe(true);
    expect(rackExternalLabel(249)).toBe('Ext-7');
    expect(rackExternalLabel(255)).toBe('Ext-1');
    expect(formatAssignmentSlotLabel({ ams_id: 255, tray_id: 4, printer_model: 'P3C' })).toBe('Ext-7');
  });

  it('maps its model code, picture and maintenance pages', () => {
    expect(mapModelCode('N11')).toBe('P3C');
    expect(getPrinterImage('P3C')).toBe('/img/printers/p3c.png');
    expect(getPrinterImage('N11')).toBe('/img/printers/p3c.png');
    expect(getMaintenanceWikiUrl('Lubricate Steel Rods', 'P3C')).toBe('https://wiki.bambulab.com/en/p3c/maintenance/lubricate-x-y-z-axis');
    expect(getMaintenanceWikiUrl('Check Belt Tension', 'P3C')).toBe('https://wiki.bambulab.com/en/p3c/maintenance/belt_tension');
    expect(getMaintenanceWikiUrl('Lubricate Linear Rails', 'P3C')).toBeNull();
  });
});
