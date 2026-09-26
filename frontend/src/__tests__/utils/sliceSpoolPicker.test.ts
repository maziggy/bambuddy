import { describe, expect, it } from 'vitest';
import type { UnifiedPresetsResponse } from '../../api/client';
import { buildCompatibilityIndex } from '../../utils/slicerPrinterMatch';
import {
  brandFromProductName,
  buildSpoolPicker,
  spoolSwatchColor,
  type SpoolPickerInput,
} from '../../utils/sliceSpoolPicker';

const COMPAT = buildCompatibilityIndex({
  'Bambu Lab H2D': 'H2D',
  'Bambu Lab H2C': 'H2C',
});

function filamentCatalog(
  filaments: UnifiedPresetsResponse['standard']['filament'],
): UnifiedPresetsResponse {
  const empty = { printer: [], process: [], filament: [] };
  return {
    local: empty,
    orca_cloud: empty,
    cloud: empty,
    standard: { printer: [], process: [], filament: filaments },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
  };
}

function input(overrides: Partial<SpoolPickerInput> = {}): SpoolPickerInput {
  return {
    printers: [{ id: 1, name: 'H2D' }],
    statuses: [{
      id: 1,
      connected: true,
      ams: [{
        id: 0,
        tray: [
          { id: 0, tray_type: 'PLA', tray_color: 'FF8800FF', tray_info_idx: 'SUN20010' },
          { id: 1, tray_type: '' },
        ],
      }],
      vt_tray: [{ id: 254, tray_type: '' }],
    }],
    slotPresets: {
      1: {
        0: { presetId: 'SUN20010', presetName: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2C 0.4 nozzle' },
      },
    },
    brands: { 1: { 0: { brand: 'Sunlu' } } },
    presets: filamentCatalog([
      { id: 'sun-h2c', name: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2C 0.4 nozzle', source: 'standard', filament_type: 'PLA' },
      { id: 'sun-h2d', name: 'SUNLU PLA MATTE GEN2 @BBL H2D', source: 'standard', filament_type: 'PLA' },
      { id: 'basic', name: 'Bambu PLA Basic @BBL H2D', source: 'standard', filament_type: 'PLA' },
    ]),
    printerName: 'Bambu Lab H2D 0.4 nozzle',
    compatIndex: COMPAT,
    externalTitle: 'External spool',
    ...overrides,
  };
}

describe('slice spool picker', () => {
  it('reads a brand off the product name ahead of the material type', () => {
    expect(brandFromProductName('SUNLU PLA MATTE GEN2 @Bambu Lab H2D 0.4 nozzle', 'PLA')).toBe('SUNLU');
    expect(brandFromProductName('Bambu Lab PETG HF @BBL H2D', 'PETG')).toBe('Bambu Lab');
    expect(brandFromProductName('Generic PLA', 'PLA')).toBe('Generic');
  });

  it('keeps a reported spool colour and drops an empty reading', () => {
    expect(spoolSwatchColor('FF8800FF')).toBe('#FF8800');
    expect(spoolSwatchColor('00000000')).toBeNull();
    expect(spoolSwatchColor('')).toBeNull();
  });

  it('pads a connected AMS to four slots and picks the profile for the selected printer', () => {
    const [printer] = buildSpoolPicker(input());
    expect(printer.name).toBe('H2D');
    expect(printer.units).toHaveLength(1);
    expect(printer.units[0].title).toBe('AMS-A');
    expect(printer.units[0].columns).toBe(4);
    const [loaded, empty] = printer.units[0].slots;
    expect(loaded.empty).toBe(false);
    expect(loaded.material).toBe('PLA');
    expect(loaded.profileName).toBe('SUNLU PLA MATTE GEN2');
    expect(loaded.brand).toBe('Sunlu');
    expect(loaded.color).toBe('#FF8800');
    expect(loaded.preset).toEqual({ source: 'standard', id: 'sun-h2d' });
    expect(empty.empty).toBe(true);
    expect(printer.units[0].slots[2].empty).toBe(true);
    expect(printer.units[0].slots[3].empty).toBe(true);
  });

  it('leaves a slot unselectable when no copy matches the selected printer', () => {
    const [printer] = buildSpoolPicker(input({
      presets: filamentCatalog([
        { id: 'sun-h2c', name: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2C 0.4 nozzle', source: 'standard', filament_type: 'PLA' },
      ]),
    }));
    expect(printer.units[0].slots[0].preset).toBeNull();
    expect(printer.units[0].slots[0].material).toBe('PLA');
  });

  it('shows a loaded external spool and skips an empty holder and offline printers', () => {
    const groups = buildSpoolPicker(input({
      printers: [
        { id: 1, name: 'H2D' },
        { id: 2, name: 'Offline' },
      ],
      statuses: [
        {
          id: 1,
          connected: true,
          ams: [],
          vt_tray: [
            { id: 254, tray_type: '' },
            { id: 255, tray_type: 'PETG', tray_color: '#112233' },
          ],
        },
        {
          id: 2,
          connected: false,
          ams: [{ id: 0, tray: [{ tray_type: 'ABS' }] }],
        },
      ],
      slotPresets: {
        1: {
          1021: { presetId: 'PETG1', presetName: 'OVERTURE PETG @BBL H2D' },
        },
      },
      brands: { 1: { 255: { brand: 'Overture' } } },
      presets: filamentCatalog([
        { id: 'petg', name: 'OVERTURE PETG @BBL H2D', source: 'standard', filament_type: 'PETG' },
      ]),
    }));
    expect(groups).toHaveLength(1);
    expect(groups[0].units).toHaveLength(1);
    expect(groups[0].units[0].title).toBe('External spool');
    expect(groups[0].units[0].slots).toHaveLength(1);
    expect(groups[0].units[0].slots[0].profileName).toBe('OVERTURE PETG');
    expect(groups[0].units[0].slots[0].brand).toBe('Overture');
    expect(groups[0].units[0].slots[0].material).toBe('PETG');
    expect(groups[0].units[0].slots[0].preset).toEqual({ source: 'standard', id: 'petg' });
  });

  it('keeps only the selected printer model when that filter is set', () => {
    const connected = (id: number): SpoolPickerInput['statuses'][number] => ({
      id,
      connected: true,
      ams: [{ id: 0, tray: [{ tray_type: 'PLA' }] }],
    });
    const groups = buildSpoolPicker(input({
      printers: [
        { id: 1, name: 'Workshop H2D', model: 'H2D' },
        { id: 2, name: 'Workshop H2C', model: 'H2C' },
      ],
      statuses: [connected(1), connected(2)],
      slotPresets: {},
      brands: {},
      restrictToModel: 'H2D',
    }));
    expect(groups.map((group) => group.name)).toEqual(['Workshop H2D']);
  });

  it('uses one cell for an AMS-HT', () => {
    const [printer] = buildSpoolPicker(input({
      statuses: [{
        id: 1,
        connected: true,
        ams: [{ id: 128, is_ams_ht: true, tray: [{ tray_type: 'ASA', tray_color: 'AABBCCFF' }] }],
      }],
      slotPresets: {},
      brands: {},
    }));
    expect(printer.units[0].title).toBe('HT-A');
    expect(printer.units[0].columns).toBe(4);
    expect(printer.units[0].slots).toHaveLength(1);
    expect(printer.units[0].slots[0].preset).toBeNull();
    expect(printer.units[0].slots[0].material).toBe('ASA');
  });

  it('packs AMS-HT units into rows of four beside a regular AMS', () => {
    const [printer] = buildSpoolPicker(input({
      statuses: [{
        id: 1,
        connected: true,
        ams: [
          { id: 0, tray: [{ tray_type: 'PLA' }] },
          { id: 128, is_ams_ht: true, tray: [{ tray_type: 'ASA' }] },
          { id: 1, tray: [{ tray_type: 'PLA' }] },
          { id: 129, is_ams_ht: true, tray: [{ tray_type: 'PETG' }] },
          { id: 130, is_ams_ht: true, tray: [{ tray_type: 'ABS' }] },
          { id: 131, is_ams_ht: true, tray: [{ tray_type: 'TPU' }] },
          { id: 132, is_ams_ht: true, tray: [{ tray_type: 'PLA' }] },
        ],
      }],
      slotPresets: {},
      brands: {},
    }));
    expect(printer.units.map((unit) => unit.title)).toEqual(['AMS-A', 'AMS-B', 'AMS-HT']);
    expect(printer.units[2].columns).toBe(4);
    expect(printer.units[2].slots.map((slot) => slot.label)).toEqual([
      'HT-A', 'HT-B', 'HT-C', 'HT-D', 'HT-E',
    ]);
  });
});
