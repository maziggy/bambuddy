/**
 * Regression tests for buildFilamentOptions (#1248).
 *
 * The original bug was precedence-based merging: cloud presets, when present,
 * fully replaced the local-presets branch and silently hid Local Profiles.
 *
 * The follow-up clarification: the spool form is printer-agnostic, so it must
 * show every per-printer / per-nozzle variant of a preset as its own entry —
 * unlike the AMS Slot modal which is per-printer and filters down to the
 * active printer model. Both surfaces should render the same set of presets
 * if you summed the AMS Slot's per-printer-filtered output across all
 * printers; the spool form just shows the union directly.
 */

import { describe, it, expect } from 'vitest';
import { buildFilamentOptions } from '../../../components/spool-form/utils';
import type { SlicerSetting, LocalPreset, BuiltinFilament } from '../../../api/client';

const cloudPreset = (overrides: Partial<SlicerSetting> = {}): SlicerSetting => ({
  setting_id: 'GFSL00@P1S',
  name: 'Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle',
  type: 'filament',
  version: null,
  user_id: null,
  updated_time: null,
  is_custom: false,
  ...overrides,
});

const localPreset = (overrides: Partial<LocalPreset> = {}): LocalPreset => ({
  id: 1,
  name: 'My Custom PETG @Bambu Lab P2S 0.4 nozzle',
  preset_type: 'filament',
  source: 'local',
  filament_type: 'GFG00',
  filament_vendor: 'Acme',
  nozzle_temp_min: 230,
  nozzle_temp_max: 260,
  pressure_advance: null,
  default_filament_colour: null,
  filament_cost: null,
  filament_density: null,
  compatible_printers: null,
  inherits: null,
  version: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const builtin = (overrides: Partial<BuiltinFilament> = {}): BuiltinFilament => ({
  filament_id: 'GFA00',
  name: 'Bambu ASA Basic',
  ...overrides,
});

describe('buildFilamentOptions', () => {
  it('returns one entry per cloud setting_id (no @printer collapse)', () => {
    const options = buildFilamentOptions(
      [
        cloudPreset({ setting_id: 'GFSL00@P1S', name: 'Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle' }),
        cloudPreset({ setting_id: 'GFSL00@X1C', name: 'Bambu PLA Basic @Bambu Lab X1C 0.4 nozzle' }),
        cloudPreset({ setting_id: 'GFSL00@A1', name: 'Bambu PLA Basic @Bambu Lab A1 0.4 nozzle' }),
      ],
      new Set(),
    );
    expect(options).toHaveLength(3);
    expect(options.map(o => o.code)).toEqual([
      'GFSL00@A1',
      'GFSL00@P1S',
      'GFSL00@X1C',
    ]);
  });

  it('keeps the @printer suffix in displayName so users can tell variants apart', () => {
    const options = buildFilamentOptions(
      [cloudPreset({ name: 'Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle' })],
      new Set(),
    );
    expect(options[0].displayName).toBe('Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle');
  });

  it('merges local profiles even when cloud has presets (#1248 regression)', () => {
    const options = buildFilamentOptions(
      [cloudPreset()],
      new Set(),
      [localPreset()],
    );
    const names = options.map(o => o.name);
    expect(names).toContain('Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle');
    expect(names).toContain('My Custom PETG @Bambu Lab P2S 0.4 nozzle');
  });

  it('merges built-in filaments alongside cloud and local sources', () => {
    const options = buildFilamentOptions(
      [cloudPreset()],
      new Set(),
      [localPreset()],
      [builtin()],
    );
    const names = options.map(o => o.name);
    expect(names).toContain('Bambu PLA Basic @Bambu Lab P1S 0.4 nozzle');
    expect(names).toContain('My Custom PETG @Bambu Lab P2S 0.4 nozzle');
    expect(names).toContain('Bambu ASA Basic');
  });

  it('lists each local preset individually (no @printer collapse)', () => {
    const options = buildFilamentOptions(
      [],
      new Set(),
      [
        localPreset({ id: 1, name: 'My PETG @Bambu Lab P2S 0.4 nozzle' }),
        localPreset({ id: 2, name: 'My PETG @Bambu Lab X1C 0.4 nozzle' }),
        localPreset({ id: 3, name: 'My PETG @Bambu Lab P2S 0.6 nozzle' }),
      ],
    );
    expect(options).toHaveLength(3);
    expect(options.map(o => o.name).sort()).toEqual([
      'My PETG @Bambu Lab P2S 0.4 nozzle',
      'My PETG @Bambu Lab P2S 0.6 nozzle',
      'My PETG @Bambu Lab X1C 0.4 nozzle',
    ]);
  });

  it('local-preset allCodes carries both filament_type and the row id for findPresetOption', () => {
    const options = buildFilamentOptions(
      [],
      new Set(),
      [localPreset({ id: 42, filament_type: 'GFL00' })],
    );
    expect(options[0].allCodes).toEqual(expect.arrayContaining(['GFL00', '42']));
  });

  it('falls back to hardcoded list only when every source is empty', () => {
    const options = buildFilamentOptions([], new Set(), [], []);
    expect(options.length).toBeGreaterThan(0);
    expect(options.map(o => o.name)).toContain('Generic PLA');
  });

  it('skips a built-in whose setting_id is already covered by cloud', () => {
    const options = buildFilamentOptions(
      [cloudPreset({ setting_id: 'GFSA00', name: 'Bambu ASA Basic' })],
      new Set(),
      undefined,
      [builtin()],
    );
    const asaCount = options.map(o => o.name).filter(n => n === 'Bambu ASA Basic').length;
    expect(asaCount).toBe(1);
  });

  it('result is sorted alphabetically by displayName', () => {
    const options = buildFilamentOptions(
      [],
      new Set(),
      [
        localPreset({ id: 1, name: 'Zebra PLA' }),
        localPreset({ id: 2, name: 'Alpha PLA' }),
      ],
    );
    expect(options.map(o => o.name)).toEqual(['Alpha PLA', 'Zebra PLA']);
  });
});
