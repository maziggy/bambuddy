import { describe, expect, it } from 'vitest';

import type { UnifiedPreset, UnifiedPresetsBySlot, UnifiedPresetsResponse } from '../../api/client';
import {
  pickFilamentForSlot,
  pickProcessDefault,
  statesDifferentMaterial,
} from '../../utils/slicePresetPicker';
import { buildCompatibilityIndex } from '../../utils/slicerPrinterMatch';

// The production registry, trimmed to the models these cases name.
const index = buildCompatibilityIndex({
  'Bambu Lab X1 Carbon': 'X1C',
  'Bambu Lab P1S': 'P1S',
  'Bambu Lab A1': 'A1',
  'Bambu Lab A1 mini': 'A1 Mini',
});

const P1S = 'Bambu Lab P1S 0.4 nozzle';
const X1C = 'Bambu Lab X1 Carbon 0.4 nozzle';
const A1 = 'Bambu Lab A1 0.4 nozzle';

function empty(): UnifiedPresetsBySlot {
  return { printer: [], process: [], filament: [] };
}

function unified(overrides: Partial<UnifiedPresetsResponse> = {}): UnifiedPresetsResponse {
  return {
    orca_cloud: empty(),
    cloud: empty(),
    local: empty(),
    standard: empty(),
    cloud_status: 'ok',
    orca_cloud_status: 'ok',
    ...overrides,
  };
}

function standard(slot: 'process' | 'filament', entries: Partial<UnifiedPreset>[]): UnifiedPresetsResponse {
  const list = entries.map((e) => ({
    id: e.name as string,
    source: 'standard' as const,
    ...e,
  })) as UnifiedPreset[];
  return unified({ standard: { ...empty(), [slot]: list } });
}

describe('statesDifferentMaterial', () => {
  it('is true only when both sides state a material and they differ', () => {
    expect(statesDifferentMaterial({ filament_type: 'PETG' }, 'PLA')).toBe(true);
  });

  it('is false for the same material, whatever the casing or padding', () => {
    expect(statesDifferentMaterial({ filament_type: ' pla ' }, 'PLA')).toBe(false);
  });

  it('is false when the preset states no material', () => {
    // 32 of the shipped BBL filament profiles inherit from a parent the bundle
    // does not contain, so their material genuinely cannot be resolved.
    // "Unknown" has to stay eligible or those slots get nothing.
    expect(statesDifferentMaterial({ filament_type: null }, 'PLA')).toBe(false);
    expect(statesDifferentMaterial({ filament_type: '' }, 'PLA')).toBe(false);
  });

  it('is false when the plate asks for no particular material', () => {
    expect(statesDifferentMaterial({ filament_type: 'PETG' }, '')).toBe(false);
  });
});

describe('pickFilamentForSlot — material is a hard partition (#2982)', () => {
  it('never picks a stated PETG for a PLA plate, however well the colour fits', () => {
    // The reported symptom, in the state the fixed sidecar produces: with a
    // material on both presets the +10 type bonus decides it. What made the
    // PETG win was the standard tier reporting a null material for all 1156
    // presets it listed, which left colour and tier the only signals in play.
    const presets = standard('filament', [
      { name: 'eSUN PETG Basic @BBL A1', filament_type: 'PETG', filament_colour: '#FF0000' },
      { name: 'Bambu PLA Basic @BBL A1', filament_type: 'PLA', filament_colour: '#FFFFFF' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FF0000' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL A1' });
  });

  it('prefers the right material over a higher tier offering the wrong one', () => {
    const presets = unified({
      local: {
        ...empty(),
        filament: [
          { id: 'my-petg', name: 'My PETG @BBL A1', source: 'local', filament_type: 'PETG', filament_colour: '#FF0000' },
        ],
      },
      standard: {
        ...empty(),
        filament: [
          { id: 'Bambu PLA Basic @BBL A1', name: 'Bambu PLA Basic @BBL A1', source: 'standard', filament_type: 'PLA', filament_colour: '#FF0000' },
        ],
      },
    });
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FF0000' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL A1' });
  });

  it('still picks a preset that states no material at all', () => {
    // Ordering that only the hard partition produces: on score alone the PETG
    // wins here, because an unknown material earns no type bonus either.
    const presets = standard('filament', [
      { name: 'Mystery @BBL A1', filament_type: null },
      { name: 'eSUN PETG Basic @BBL A1', filament_type: 'PETG', filament_colour: '#FFFFFF' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FFFFFF' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Mystery @BBL A1' });
  });

  it('prefers the right material on the right printer over the right material on the wrong one', () => {
    const presets = standard('filament', [
      { name: 'Bambu PLA Basic @BBL X1C', filament_type: 'PLA', filament_colour: '#FF0000' },
      { name: 'Bambu PLA Basic @BBL A1', filament_type: 'PLA', filament_colour: '#FFFFFF' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FF0000' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL A1' });
  });

  it('falls back to a wrong-material preset rather than leaving the slot empty', () => {
    // A registry with nothing of the asked-for material still has to fill the
    // slot: a visible wrong preset can be changed in the dropdown, a null
    // renders as an empty slot with nothing to act on.
    const presets = standard('filament', [
      { name: 'eSUN PETG Basic @BBL A1', filament_type: 'PETG', filament_colour: '#FFFFFF' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FFFFFF' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'eSUN PETG Basic @BBL A1' });
  });

  it('prefers a right-material preset for the wrong printer over a wrong-material one for the right printer', () => {
    const presets = standard('filament', [
      { name: 'Bambu PLA Basic @BBL X1C', filament_type: 'PLA', filament_colour: '#FFFFFF' },
      { name: 'eSUN PETG Basic @BBL A1', filament_type: 'PETG', filament_colour: '#FFFFFF' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FFFFFF' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL X1C' });
  });

  it('keeps colour as the tie-breaker within one material', () => {
    const presets = standard('filament', [
      { name: 'Bambu PLA Matte @BBL A1', filament_type: 'PLA', filament_colour: '#FFFFFF' },
      { name: 'Bambu PLA Basic @BBL A1', filament_type: 'PLA', filament_colour: '#FF0000' },
    ]);
    const pick = pickFilamentForSlot(presets, { type: 'PLA', color: '#FF0000' }, A1, index);
    expect(pick).toEqual({ source: 'standard', id: 'Bambu PLA Basic @BBL A1' });
  });
});

describe('pickProcessDefault — compatible_printers reaches the standard tier (#2982)', () => {
  // Verbatim from the shipped bundle: every P1S process is named for an X1C
  // and names the P1S only here.
  const x1cStandard = {
    name: '0.20mm Standard @BBL X1C',
    compatible_printers: [X1C, 'Bambu Lab X1 0.4 nozzle', P1S],
  };

  it('picks an X1C-named process for a P1S when it declares the P1S', () => {
    const presets = standard('process', [
      { name: '0.06mm Fine @BBL A1 0.2 nozzle', compatible_printers: ['Bambu Lab A1 0.2 nozzle'] },
      x1cStandard,
    ]);
    const pick = pickProcessDefault(presets, P1S, index, null);
    expect(pick).toEqual({ source: 'standard', id: '0.20mm Standard @BBL X1C' });
  });

  it('does not offer an A1 process to a P1S', () => {
    // What shipped: with no declared list the name matcher read all 198 as
    // mismatches, and the fall-through returned the alphabetically first.
    const presets = standard('process', [
      { name: '0.06mm Fine @BBL A1 0.2 nozzle', compatible_printers: ['Bambu Lab A1 0.2 nozzle'] },
      x1cStandard,
    ]);
    const pick = pickProcessDefault(presets, P1S, index, null);
    expect(pick?.id).not.toBe('0.06mm Fine @BBL A1 0.2 nozzle');
  });

  it('lets a declared list overrule the printer the name implies', () => {
    const presets = standard('process', [x1cStandard]);
    expect(pickProcessDefault(presets, P1S, index, null)).toEqual({
      source: 'standard',
      id: '0.20mm Standard @BBL X1C',
    });
    expect(pickProcessDefault(presets, A1, index, null)).toEqual({
      source: 'standard',
      id: '0.20mm Standard @BBL X1C',
    });
  });
});

describe('pickProcessDefault — layer height within equally-valid candidates (#2982)', () => {
  const heights = (names: string[]) =>
    standard('process', names.map((name) => ({ name, compatible_printers: [X1C] })));

  it('prefers 0.20mm Standard over the alphabetically first 0.08mm', () => {
    // Tier order says which source to prefer; within a tier the list is
    // alphabetical, and Bambu's naming puts the finest — slowest — height
    // first. Every X1C slice that did not name its own process silently got
    // 0.08mm Extra Fine.
    const presets = heights([
      '0.08mm Extra Fine @BBL X1C',
      '0.12mm Fine @BBL X1C',
      '0.20mm Standard @BBL X1C',
      '0.28mm Extra Draft @BBL X1C',
    ]);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('0.20mm Standard @BBL X1C');
  });

  it('takes the nearest height when nothing sits exactly at 0.2mm', () => {
    const presets = heights(['0.08mm Extra Fine @BBL X1C', '0.16mm Optimal @BBL X1C']);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('0.16mm Optimal @BBL X1C');
  });

  it('breaks an equal distance toward the coarser height', () => {
    // 0.16 and 0.24 are both 0.04 away. The coarser one prints faster, which
    // is the friendlier default to be wrong in.
    const presets = heights(['0.16mm Optimal @BBL X1C', '0.24mm Draft @BBL X1C']);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('0.24mm Draft @BBL X1C');
  });

  it('still picks a preset whose name carries no height', () => {
    const presets = heights(['My Favourite Process']);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('My Favourite Process');
  });

  it('prefers a readable height over a name with none', () => {
    const presets = heights(['A Nameless Process', '0.20mm Standard @BBL X1C']);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('0.20mm Standard @BBL X1C');
  });

  it('does not let layer height override tier order', () => {
    const presets = unified({
      local: {
        ...empty(),
        process: [
          { id: 'mine', name: '0.08mm Mine @BBL X1C', source: 'local', compatible_printers: [X1C] },
        ],
      },
      standard: {
        ...empty(),
        process: [
          { id: 'std', name: '0.20mm Standard @BBL X1C', source: 'standard', compatible_printers: [X1C] },
        ],
      },
    });
    expect(pickProcessDefault(presets, X1C, index, null)).toEqual({ source: 'local', id: 'mine' });
  });

  it('does not override a process the 3MF named', () => {
    const presets = heights(['0.08mm Extra Fine @BBL X1C', '0.20mm Standard @BBL X1C']);
    const pick = pickProcessDefault(presets, X1C, index, '0.08mm Extra Fine @BBL X1C');
    expect(pick?.id).toBe('0.08mm Extra Fine @BBL X1C');
  });

  it('does not let a coarse mismatch beat a compatible fine one', () => {
    // Compatibility is decided before height is ever consulted.
    const presets = standard('process', [
      { name: '0.08mm Extra Fine @BBL X1C', compatible_printers: [X1C] },
      { name: '0.28mm Extra Draft @BBL A1', compatible_printers: [A1] },
    ]);
    expect(pickProcessDefault(presets, X1C, index, null)?.id).toBe('0.08mm Extra Fine @BBL X1C');
  });
});
