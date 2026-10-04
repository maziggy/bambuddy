/**
 * Matching loaded spools against the Slice dialog's profiles (#3172).
 */

import { describe, it, expect } from 'vitest';
import type { LoadedSpoolPrinter, LoadedSpoolTray, UnifiedPresetsResponse } from '../../api/client';
import {
  buildFilamentNameIndex,
  isConnectedModelPreset,
  matchSlotPreset,
  matchedFilamentRefs,
  presetBaseName,
  printerPresetModel,
  printersOfModel,
  trayColourHex,
} from '../../utils/sliceLoadedSpools';
import { buildCompatibilityIndex } from '../../utils/slicerPrinterMatch';

const MODELS = {
  'Bambu Lab X1 Carbon': 'X1C',
  'Bambu Lab H2D': 'H2D',
  'Bambu Lab A1 Mini': 'A1 Mini',
  'Bambu Lab A1 mini': 'A1 Mini',
};
const compat = buildCompatibilityIndex(MODELS);
const H2D = 'Bambu Lab H2D 0.4 nozzle';

function presets(overrides: Partial<UnifiedPresetsResponse> = {}): UnifiedPresetsResponse {
  return {
    orca_cloud: { printer: [], process: [], filament: [] },
    cloud: { printer: [], process: [], filament: [] },
    local: { printer: [], process: [], filament: [] },
    standard: { printer: [], process: [], filament: [] },
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
    ...overrides,
  };
}

const standardFilaments = [
  { id: 'Bambu PLA Basic @BBL X1C', name: 'Bambu PLA Basic @BBL X1C', source: 'standard' as const, filament_type: 'PLA' },
  { id: 'Bambu PLA Basic @BBL H2D', name: 'Bambu PLA Basic @BBL H2D', source: 'standard' as const, filament_type: 'PLA' },
  { id: 'Generic PETG @BBL H2D', name: 'Generic PETG @BBL H2D', source: 'standard' as const, filament_type: 'PETG' },
  { id: 'Generic PLA @BBL H2D', name: 'Generic PLA @BBL H2D', source: 'standard' as const, filament_type: 'PLA' },
];

function tray(overrides: Partial<LoadedSpoolTray> = {}): LoadedSpoolTray {
  return {
    ams_id: 0,
    tray_id: 0,
    tray_type: 'PLA',
    tray_sub_brands: null,
    tray_color: 'FF0000FF',
    tray_info_idx: null,
    exists: true,
    state: null,
    saved_preset: null,
    ...overrides,
  };
}

function match(t: LoadedSpoolTray, data: UnifiedPresetsResponse, printerName: string | null = H2D) {
  return matchSlotPreset(t, data, buildFilamentNameIndex(data), printerName, compat)?.ref ?? null;
}

describe('printerPresetModel', () => {
  it('reads the model from a Bambu printer profile', () => {
    expect(printerPresetModel('Bambu Lab X1 Carbon 0.4 nozzle', MODELS)).toBe('X1C');
    expect(printerPresetModel('# Bambu Lab H2D 0.6 nozzle', MODELS)).toBe('H2D');
    expect(printerPresetModel('Bambu Lab A1 mini 0.2 nozzle', MODELS)).toBe('A1 Mini');
  });

  it('keeps an unknown Bambu model as written and gives up on other names', () => {
    expect(printerPresetModel('Bambu Lab Q9 0.4 nozzle', MODELS)).toBe('Q9');
    expect(printerPresetModel('My farm printer', MODELS)).toBeNull();
    expect(printerPresetModel(null, MODELS)).toBeNull();
  });
});

describe('isConnectedModelPreset', () => {
  it('keeps profiles of online models at every nozzle size and drops the rest', () => {
    const online = ['H2D'];
    expect(isConnectedModelPreset({ name: 'Bambu Lab H2D 0.4 nozzle' }, online, MODELS)).toBe(true);
    expect(isConnectedModelPreset({ name: 'Bambu Lab H2D 0.8 nozzle' }, online, MODELS)).toBe(true);
    expect(isConnectedModelPreset({ name: 'Bambu Lab X1 Carbon 0.4 nozzle' }, online, MODELS)).toBe(false);
  });

  it('never hides a profile whose model it cannot read', () => {
    expect(isConnectedModelPreset({ name: 'My farm printer' }, ['H2D'], MODELS)).toBe(true);
  });

  it('understands the short A1 Mini code', () => {
    expect(isConnectedModelPreset({ name: 'Bambu Lab A1 mini 0.4 nozzle' }, ['A1M'], MODELS)).toBe(true);
  });
});

describe('presetBaseName', () => {
  it('drops the clone prefix and the printer suffix', () => {
    expect(presetBaseName('# Bambu PLA Basic @BBL X1C')).toBe('bambu pla basic');
    expect(presetBaseName('SUNLU TPU @Bambu Lab H2D 0.4 nozzle')).toBe('sunlu tpu');
    expect(presetBaseName('Overture  PLA')).toBe('overture pla');
  });
});

describe('matchSlotPreset', () => {
  it('takes the saved profile by id when it fits the printer', () => {
    const data = presets({
      local: { printer: [], process: [], filament: [{ id: '7', name: 'Overture PLA Matte', source: 'local' }] },
    });
    const t = tray({
      saved_preset: { preset_id: 'local_7', preset_name: 'Overture PLA Matte', preset_source: 'local' },
    });
    expect(match(t, data)).toEqual({ source: 'local', id: '7' });
  });

  it("finds the selected printer's copy of a profile saved for another model", () => {
    const data = presets({
      cloud: {
        printer: [],
        process: [],
        filament: [{ id: 'GFSA00', name: 'Bambu PLA Basic @BBL X1C', source: 'cloud', filament_type: 'PLA' }],
      },
      standard: { printer: [], process: [], filament: standardFilaments },
    });
    const t = tray({
      tray_info_idx: 'GFA00',
      saved_preset: { preset_id: 'GFSA00', preset_name: 'Bambu PLA Basic', preset_source: 'cloud' },
    });
    expect(match(t, data)).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL H2D' });
    // On an X1C the saved profile itself fits.
    expect(match(t, data, 'Bambu Lab X1 Carbon 0.4 nozzle')).toEqual({ source: 'cloud', id: 'GFSA00' });
  });

  it("names a Bambu spool nobody configured in Bambuddy from its brand text", () => {
    const data = presets({ standard: { printer: [], process: [], filament: standardFilaments } });
    const t = tray({ tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic' });
    expect(match(t, data)).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL H2D' });
  });

  it('does not put "Bambu" in front of a third-party spool', () => {
    const data = presets({ standard: { printer: [], process: [], filament: standardFilaments } });
    const t = tray({ tray_info_idx: 'P4d64437', tray_sub_brands: 'PLA Basic' });
    expect(match(t, data)).toBeNull();
  });

  it('uses the generic profile for a spool with no brand text', () => {
    const data = presets({ standard: { printer: [], process: [], filament: standardFilaments } });
    expect(match(tray({ tray_type: 'PETG', tray_info_idx: 'GFG99' }), data)).toEqual({
      source: 'standard',
      id: 'Generic PETG @BBL H2D',
    });
  });

  it('ignores a saved profile the slot has been reconfigured away from', () => {
    const data = presets({
      local: { printer: [], process: [], filament: [{ id: '7', name: 'Overture PLA Matte', source: 'local' }] },
      standard: { printer: [], process: [], filament: standardFilaments },
    });
    const t = tray({
      tray_info_idx: 'GFL99',
      saved_preset: {
        preset_id: 'local_7',
        preset_name: 'Overture PLA Matte',
        preset_source: 'local',
        tray_info_idx: 'GFL05',
      },
    });
    expect(match(t, data)).toEqual({ source: 'standard', id: 'Generic PLA @BBL H2D' });
  });

  it('skips a same-named profile that states another material', () => {
    const data = presets({
      standard: {
        printer: [],
        process: [],
        filament: [{ id: 'Generic PLA @BBL H2D', name: 'Generic PLA @BBL H2D', source: 'standard', filament_type: 'PETG' }],
      },
    });
    expect(match(tray({ tray_type: 'PLA' }), data)).toBeNull();
  });

  it('matches nothing for an empty or unidentified slot', () => {
    const data = presets({ standard: { printer: [], process: [], filament: standardFilaments } });
    expect(match(tray({ tray_type: null }), data)).toBeNull();
  });
});

describe('printersOfModel and matchedFilamentRefs', () => {
  const printers: LoadedSpoolPrinter[] = [
    {
      id: 1,
      name: 'H2D one',
      model: 'H2D',
      ams: [{ id: 0, is_ams_ht: false, trays: [tray({ tray_info_idx: 'GFA00', tray_sub_brands: 'PLA Basic' }), tray({ tray_id: 1, tray_type: null })] }],
      external: [tray({ ams_id: 255, tray_type: 'PETG', tray_info_idx: 'GFG99' })],
      external_holders: 2,
    },
    { id: 2, name: 'X1C', model: 'X1C', ams: [], external: [], external_holders: 1 },
  ];

  it('narrows to the selected model, and keeps all when it is unknown', () => {
    expect(printersOfModel(printers, 'H2D').map((p) => p.id)).toEqual([1]);
    expect(printersOfModel(printers, null).map((p) => p.id)).toEqual([1, 2]);
  });

  it('collects the profiles of every loaded spool', () => {
    const data = presets({ standard: { printer: [], process: [], filament: standardFilaments } });
    const refs = matchedFilamentRefs(printers, data, buildFilamentNameIndex(data), H2D, compat);
    expect([...refs].sort()).toEqual(['standard:Bambu PLA Basic @BBL H2D', 'standard:Generic PETG @BBL H2D']);
  });
});

describe('trayColourHex', () => {
  it('drops the alpha byte', () => {
    expect(trayColourHex({ tray_color: 'ff8800FF' })).toBe('#FF8800');
    expect(trayColourHex({ tray_color: null })).toBeNull();
    expect(trayColourHex({ tray_color: 'zz' })).toBeNull();
  });
});
