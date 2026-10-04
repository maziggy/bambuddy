import { describe, expect, it } from 'vitest';

import type { UnifiedPreset, UnifiedPresetsBySlot, UnifiedPresetsResponse } from '../../api/client';
import { pickFilamentForSlot } from '../../utils/slicePresetPicker';
import { buildCompatibilityIndex } from '../../utils/slicerPrinterMatch';

// #3234 made the sidecar list every filament the slicer ships and resolve the
// material of all of them. Before, a few presets with no known material kept
// the no-such-material fallback on the right printer by accident; with those
// gone, the fallback has to look at the printer itself.

const index = buildCompatibilityIndex({ 'Bambu Lab X1 Carbon': 'X1C', 'Bambu Lab A1': 'A1' });
const X1C = 'Bambu Lab X1 Carbon 0.4 nozzle';
const A1 = 'Bambu Lab A1 0.4 nozzle';

function empty(): UnifiedPresetsBySlot {
  return { printer: [], process: [], filament: [] };
}

function tiers(perTier: Partial<Record<'local' | 'orca_cloud' | 'cloud' | 'standard', Partial<UnifiedPreset>[]>>) {
  const build = (source: 'local' | 'orca_cloud' | 'cloud' | 'standard') => ({
    ...empty(),
    filament: (perTier[source] ?? []).map((e) => ({ id: e.name as string, source, ...e })) as UnifiedPreset[],
  });
  return {
    local: build('local'),
    orca_cloud: build('orca_cloud'),
    cloud: build('cloud'),
    standard: build('standard'),
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
  } as UnifiedPresetsResponse;
}

const pick = (by: UnifiedPresetsResponse, type: string, printer = X1C, color = '') =>
  pickFilamentForSlot(by, { type, color }, printer, index)?.id;

describe('pickFilamentForSlot — no preset of the plate material (#3234)', () => {
  it('falls back to a preset the selected printer can take', () => {
    // Alphabetically first and scored the same, but for another printer: the
    // slicer would refuse the slice outright.
    const by = tiers({
      standard: [
        { name: 'Bambu ABS @BBL A1', filament_type: 'ABS', compatible_printers: [A1] },
        { name: 'Bambu PLA Basic @BBL X1C', filament_type: 'PLA', compatible_printers: [X1C] },
      ],
    });
    expect(pick(by, 'XYZ')).toBe('Bambu PLA Basic @BBL X1C');
  });

  it('still returns something when only other printers have presets', () => {
    const by = tiers({
      standard: [{ name: 'Bambu ABS @BBL A1', filament_type: 'ABS', compatible_printers: [A1] }],
    });
    expect(pick(by, 'XYZ')).toBe('Bambu ABS @BBL A1');
  });
});
