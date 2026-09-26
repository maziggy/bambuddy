import { describe, expect, it } from 'vitest';
import type { UnifiedPresetsResponse } from '../../api/client';
import {
  connectedModelsFromFleet,
  filamentPresetMatchesLoadedSpools,
  installedNozzlesFromFleet,
  isPrinterPresetForModels,
  loadedSpoolsFromFleet,
  pickConnectedPrinterPreset,
  pickLoadedFilamentPreset,
  type FleetPrinter,
  type FleetStatus,
} from '../../utils/sliceConnectedFilters';
import { EMPTY_COMPATIBILITY_INDEX } from '../../utils/slicerPrinterMatch';

const PRINTER_MODELS: Record<string, string> = {
  'Bambu Lab X1 Carbon': 'X1C',
  'Bambu Lab H2D': 'H2D',
  'Bambu Lab H2D Pro': 'H2D Pro',
  'Bambu Lab H2C': 'H2C',
  'Bambu Lab A1 Mini': 'A1 Mini',
  'Bambu Lab A1 mini': 'A1 Mini',
  'Bambu Lab P1S': 'P1S',
};

function presets(printers: { id: string; name: string }[]): UnifiedPresetsResponse {
  const empty = { printer: [], process: [], filament: [] };
  return {
    local: empty,
    orca_cloud: empty,
    cloud: empty,
    standard: {
      printer: printers.map((p) => ({ ...p, source: 'standard' as const })),
      process: [],
      filament: [],
    },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
  };
}

describe('isPrinterPresetForModels', () => {
  const connected = ['H2D', 'H2C'];

  it('keeps every nozzle size of a connected model', () => {
    for (const name of [
      'Bambu Lab H2D 0.2 nozzle',
      'Bambu Lab H2D 0.4 nozzle',
      'Bambu Lab H2D 0.6 nozzle',
      'Bambu Lab H2D 0.8 nozzle',
      'Bambu Lab H2C 0.2 nozzle',
      'Bambu Lab H2C 0.6 nozzle',
    ]) {
      expect(isPrinterPresetForModels(name, connected, PRINTER_MODELS)).toBe(true);
    }
  });

  it('hides models that are not connected', () => {
    expect(isPrinterPresetForModels('Bambu Lab X1 Carbon 0.4 nozzle', connected, PRINTER_MODELS)).toBe(false);
    expect(isPrinterPresetForModels('Bambu Lab P1S 0.4 nozzle', connected, PRINTER_MODELS)).toBe(false);
    expect(isPrinterPresetForModels('Bambu Lab H2D Pro 0.4 nozzle', connected, PRINTER_MODELS)).toBe(false);
  });

  it('keeps a preset whose model cannot be read', () => {
    expect(isPrinterPresetForModels('Shop default', connected, PRINTER_MODELS)).toBe(true);
  });
});

describe('pickConnectedPrinterPreset', () => {
  it('prefers the nozzle that is actually installed, without dropping the others from consideration', () => {
    const by = presets([
      { id: 'x1c', name: 'Bambu Lab X1 Carbon 0.4 nozzle' },
      { id: 'h2d06', name: 'Bambu Lab H2D 0.6 nozzle' },
      { id: 'h2d04', name: 'Bambu Lab H2D 0.4 nozzle' },
      { id: 'h2c02', name: 'Bambu Lab H2C 0.2 nozzle' },
    ]);
    expect(pickConnectedPrinterPreset(
      by,
      ['H2D', 'H2C'],
      PRINTER_MODELS,
      [{ model: 'H2D', diameters: ['0.4'] }, { model: 'H2C', diameters: ['0.2'] }],
    )).toEqual({ source: 'standard', id: 'h2d04' });
  });

  it('falls back to the first connected-model profile when no nozzle size was reported', () => {
    const by = presets([
      { id: 'x1c', name: 'Bambu Lab X1 Carbon 0.4 nozzle' },
      { id: 'h2d06', name: 'Bambu Lab H2D 0.6 nozzle' },
    ]);
    expect(pickConnectedPrinterPreset(by, ['H2D'], PRINTER_MODELS, [])).toEqual({
      source: 'standard',
      id: 'h2d06',
    });
  });
});

describe('fleet snapshots', () => {
  const printers: FleetPrinter[] = [
    { id: 1, model: 'H2D' },
    { id: 2, model: 'H2C' },
    { id: 3, model: 'X1C' },
  ];
  const statuses: FleetStatus[] = [
    {
      id: 1,
      connected: true,
      nozzles: [{ nozzle_diameter: '0.4' }],
      ams: [{ id: 0, tray: [{ tray_type: 'PLA', tray_sub_brands: 'PLA' }, { tray_type: '' }] }],
    },
    {
      id: 2,
      connected: true,
      nozzles: [{ nozzle_diameter: '0.6' }],
      nozzle_rack: [{ nozzle_diameter: '0.2' }],
      ams: [{ id: 0, tray: [{ tray_type: 'PETG', tray_sub_brands: 'PETG' }] }],
    },
    {
      id: 3,
      connected: false,
      nozzles: [{ nozzle_diameter: '0.4' }],
      ams: [{ tray: [{ tray_type: 'ABS', tray_sub_brands: 'ABS Basic' }] }],
    },
  ];

  it('lists connected models only', () => {
    expect(connectedModelsFromFleet(printers, statuses)).toEqual(['H2C', 'H2D']);
  });

  it('keeps every reported nozzle, including the rack', () => {
    expect(installedNozzlesFromFleet(printers, statuses)).toEqual([
      { model: 'H2D', diameters: ['0.4'] },
      { model: 'H2C', diameters: ['0.6', '0.2'] },
    ]);
  });

  it('uses the AMS slot profile and ignores empty trays, the external spool, and offline printers', () => {
    expect(loadedSpoolsFromFleet(printers, statuses, {
      1: { 0: { presetId: 'SUN20010', presetName: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2D 0.4 nozzle' } },
      3: { 0: { presetId: 'GFSB99', presetName: 'Sunlu ABS @BBL X1C 0.4 nozzle' } },
    })).toEqual([
      {
        type: 'PLA',
        subBrand: 'PLA',
        presetId: 'SUN20010',
        presetName: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2D 0.4 nozzle',
      },
      { type: 'PETG', subBrand: 'PETG', presetId: '', presetName: '' },
    ]);
  });

  it('drops a saved Bambu profile once the slot reports a different filament', () => {
    const swapped: FleetStatus[] = [{
      id: 1,
      connected: true,
      ams: [{
        id: 0,
        tray: [{
          tray_type: 'PETG',
          tray_sub_brands: 'PETG Basic',
          tray_info_idx: 'GFB00',
        }],
      }],
    }];
    expect(loadedSpoolsFromFleet([{ id: 1, model: 'H2D' }], swapped, {
      1: { 0: { presetId: 'GFSA00', presetName: 'Bambu PLA Basic @BBL H2D' } },
    })).toEqual([
      { type: 'PETG', subBrand: 'PETG Basic', presetId: '', presetName: '' },
    ]);
  });

  it('keeps a saved Bambu profile while the slot still reports that filament', () => {
    const same: FleetStatus[] = [{
      id: 1,
      connected: true,
      ams: [{
        id: 0,
        tray: [{
          tray_type: 'PLA',
          tray_sub_brands: 'PLA Basic',
          tray_info_idx: 'GFA00',
        }],
      }],
    }];
    expect(loadedSpoolsFromFleet([{ id: 1, model: 'H2D' }], same, {
      1: { 0: { presetId: 'GFSA00', presetName: 'Bambu PLA Basic @BBL H2D' } },
    })).toEqual([
      {
        type: 'PLA',
        subBrand: 'PLA Basic',
        presetId: 'GFSA00',
        presetName: 'Bambu PLA Basic @BBL H2D',
      },
    ]);
  });
});

describe('filamentPresetMatchesLoadedSpools', () => {
  const spools = [
    {
      type: 'PLA',
      subBrand: 'PLA',
      presetId: 'SUN20010',
      presetName: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2C 0.4 nozzle',
    },
    {
      type: 'PETG',
      subBrand: 'PETG',
      presetId: 'SUN22001',
      presetName: 'SUNLU PETG BASIC GEN2 @Bambu Lab H2C 0.4 nozzle',
    },
  ];

  it('matches the AMS spool profile at any nozzle and ignores other profiles for that printer', () => {
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'sun-h2d-06', name: 'SUNLU PLA MATTE GEN2 @Bambu Lab H2D 0.6 nozzle', filament_type: 'PLA' },
      spools,
    )).toBe(true);
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'SUN20010', name: 'SUNLU PLA MATTE GEN2 @BBL H2D', filament_type: 'PLA' },
      spools,
    )).toBe(true);
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'sun-petg', name: 'SUNLU PETG BASIC GEN2 @Bambu Lab H2D 0.4 nozzle', filament_type: 'PETG' },
      spools,
    )).toBe(true);
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'bbl-pla', name: 'Bambu PLA Basic @BBL H2D', filament_type: 'PLA' },
      spools,
    )).toBe(false);
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'bbl-matte', name: 'Bambu PLA Matte @BBL H2D', filament_type: 'PLA' },
      spools,
    )).toBe(false);
    expect(filamentPresetMatchesLoadedSpools(
      { id: 'generic', name: 'Generic PLA @BBL H2D', filament_type: 'PLA' },
      spools,
    )).toBe(false);
  });

  it('does not open every profile of the material when the slot has no saved profile', () => {
    const unlabeled = [{ type: 'PLA', subBrand: '', presetId: '', presetName: '' }];
    expect(filamentPresetMatchesLoadedSpools(
      { name: 'Bambu PLA Basic @BBL H2D', filament_type: 'PLA' },
      unlabeled,
    )).toBe(false);
    expect(filamentPresetMatchesLoadedSpools(
      { name: 'Generic PLA @BBL H2D', filament_type: 'PLA' },
      unlabeled,
    )).toBe(false);
  });

  it('picks a loaded spool that fits the plate instead of another PLA', () => {
    const empty = { printer: [], process: [], filament: [] };
    const by: UnifiedPresetsResponse = {
      local: empty,
      orca_cloud: empty,
      cloud: empty,
      standard: {
        printer: [],
        process: [],
        filament: [
          { id: 'matte', name: 'Bambu PLA Matte @BBL H2D', source: 'standard', filament_type: 'PLA' },
          { id: 'basic', name: 'Bambu PLA Basic @BBL H2D', source: 'standard', filament_type: 'PLA' },
          { id: 'abs', name: 'Bambu ABS Basic @BBL H2D', source: 'standard', filament_type: 'ABS' },
        ],
      },
      cloud_status: 'ok',
      orca_cloud_status: 'ok',
    };
    expect(pickLoadedFilamentPreset(
      by,
      [{ type: 'PLA', subBrand: 'PLA Basic', presetId: '', presetName: 'Bambu PLA Basic @BBL H2D' }],
      'Bambu Lab H2D 0.4 nozzle',
      EMPTY_COMPATIBILITY_INDEX,
      'PLA',
    )).toEqual({ source: 'standard', id: 'basic' });
  });

  it('does not treat a generic profile as the Bambu profile of the same product name', () => {
    const bambuBasic = [{
      type: 'PLA',
      subBrand: 'PLA Basic',
      presetId: 'GFSA00',
      presetName: 'Bambu PLA Basic @BBL H2D',
    }];
    expect(filamentPresetMatchesLoadedSpools(
      { name: 'Generic PLA Basic @BBL H2D', filament_type: 'PLA' },
      bambuBasic,
    )).toBe(false);
    expect(filamentPresetMatchesLoadedSpools(
      { name: 'Bambu Lab PLA Basic @BBL H2D 0.6 nozzle', filament_type: 'PLA' },
      bambuBasic,
    )).toBe(true);
  });

  it('does not treat a shorter material code as a match inside another', () => {
    expect(filamentPresetMatchesLoadedSpools(
      { name: 'Bambu PLA Basic @BBL H2D', filament_type: 'PLA' },
      [{ type: 'PA', subBrand: 'PA', presetId: '', presetName: '' }],
    )).toBe(false);
  });
});
