import { describe, it, expect, vi } from 'vitest';
import {
  buildFilamentPresetOptions,
  genericFilamentIdForMaterial,
  presetDisplayName,
  resolveFilamentId,
} from '../../utils/filamentPresets';
import type { BuiltinFilament, LocalPreset, OrcaProfileMeta, SlicerSetting } from '../../api/client';

const localPreset = (over: Partial<LocalPreset> = {}): LocalPreset => ({
  id: 1,
  name: 'Elegoo PLA+ @BBL X1C 0.4 nozzle',
  preset_type: 'filament',
  source: 'orca',
  filament_type: 'PLA',
  filament_vendor: 'Elegoo',
  nozzle_temp_min: 190,
  nozzle_temp_max: 230,
  pressure_advance: null,
  default_filament_colour: null,
  filament_cost: null,
  filament_density: null,
  compatible_printers: null,
  inherits: null,
  version: null,
  created_at: '',
  updated_at: '',
  ...over,
});

const orcaProfile = (over: Partial<OrcaProfileMeta> = {}): OrcaProfileMeta => ({
  setting_id: 'a1b2c3',
  name: 'Sunlu PETG',
  type: 'filament',
  version: null,
  user_id: null,
  updated_time: null,
  is_custom: true,
  ...over,
});

const cloudSetting = (over: Partial<SlicerSetting> = {}): SlicerSetting => ({
  setting_id: 'GFSA00',
  name: 'Bambu PLA Basic @BBL X1C',
  type: 'filament',
  version: null,
  user_id: null,
  updated_time: null,
  is_custom: false,
  ...over,
});

const builtin = (filament_id: string, name: string): BuiltinFilament => ({ filament_id, name });

describe('genericFilamentIdForMaterial', () => {
  it('maps an exact material', () => {
    expect(genericFilamentIdForMaterial('PETG')).toBe('GFG99');
  });

  it('is case and whitespace tolerant', () => {
    expect(genericFilamentIdForMaterial('  pla  ')).toBe('GFL99');
  });

  it('falls back to the base material when a suffix is unknown', () => {
    // "PLA-GF" has no generic of its own; the PLA generic is the honest answer.
    expect(genericFilamentIdForMaterial('PLA-GF')).toBe('GFL99');
  });

  it('returns empty rather than guessing for an unknown material', () => {
    expect(genericFilamentIdForMaterial('UNOBTANIUM')).toBe('');
    expect(genericFilamentIdForMaterial('')).toBe('');
    expect(genericFilamentIdForMaterial(null)).toBe('');
  });
});

describe('presetDisplayName', () => {
  it('strips the printer/nozzle suffix', () => {
    expect(presetDisplayName('Bambu PLA Basic @BBL X1C 0.4 nozzle')).toBe('Bambu PLA Basic');
  });

  it('strips the custom-preset marker', () => {
    expect(presetDisplayName('# My PLA @BBL P1S')).toBe('My PLA');
  });
});

describe('buildFilamentPresetOptions', () => {
  it('is empty when every source is', () => {
    expect(buildFilamentPresetOptions({})).toEqual([]);
  });

  it('ranks the tiers local > orca > cloud > builtin', () => {
    const options = buildFilamentPresetOptions({
      builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic')],
      cloudSettings: [cloudSetting({ setting_id: 'GFSB99', name: 'Generic ABS' })],
      orcaProfiles: [orcaProfile()],
      localPresets: [localPreset()],
    });
    expect(options.map(o => o.source)).toEqual(['local', 'orca_cloud', 'cloud', 'builtin']);
  });

  it('sorts by name inside a tier', () => {
    const options = buildFilamentPresetOptions({
      builtinFilaments: [builtin('GFA01', 'Bambu PLA Matte'), builtin('GFA00', 'Bambu PLA Basic')],
    });
    expect(options.map(o => o.name)).toEqual(['Bambu PLA Basic', 'Bambu PLA Matte']);
  });

  it('takes a builtin filament id straight from the table', () => {
    const [option] = buildFilamentPresetOptions({ builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic')] });
    expect(option).toMatchObject({ id: 'builtin_GFA00', filamentId: 'GFA00' });
  });

  it('derives a Bambu official cloud preset id from its setting_id', () => {
    const [option] = buildFilamentPresetOptions({ cloudSettings: [cloudSetting({ setting_id: 'GFSG98_09' })] });
    expect(option.filamentId).toBe('GFG98');
  });

  it('leaves a cloud user preset unresolved for the detail lookup', () => {
    // PFUS ids are setting ids, not filament ids — the printer rejects them,
    // so guessing one here would file the calibration under nothing.
    const [option] = buildFilamentPresetOptions({
      cloudSettings: [cloudSetting({ setting_id: 'PFUS9ac902733670a9', name: 'My PETG', is_custom: true })],
    });
    expect(option.filamentId).toBe('');
  });

  it('gives local and orca presets the generic id for their material', () => {
    const options = buildFilamentPresetOptions({
      localPresets: [localPreset({ filament_type: 'PETG' })],
      orcaProfiles: [orcaProfile({ name: 'Sunlu ABS @BBL X1C' })],
    });
    expect(options.find(o => o.source === 'local')?.filamentId).toBe('GFG99');
    expect(options.find(o => o.source === 'orca_cloud')?.filamentId).toBe('GFB99');
  });

  it('parses the material from the name when a local preset declares none', () => {
    const [option] = buildFilamentPresetOptions({
      localPresets: [localPreset({ filament_type: null, name: 'Overture TPU @BBL X1C' })],
    });
    expect(option.filamentId).toBe('GFU99');
  });

  it('collapses a cloud filament duplicated once per printer model', () => {
    // Every Bambu Cloud account carries one copy per model. They share a
    // filament id and, with the "@…" suffix stripped, one visible name.
    const options = buildFilamentPresetOptions({
      cloudSettings: [
        cloudSetting({ setting_id: 'GFSA00_00', name: 'Bambu PLA Basic @BBL X1C' }),
        cloudSetting({ setting_id: 'GFSA00_01', name: 'Bambu PLA Basic @BBL P1S' }),
        cloudSetting({ setting_id: 'GFSA00_02', name: 'Bambu PLA Basic @BBL A1' }),
      ],
    });
    expect(options).toHaveLength(1);
    expect(options[0]).toMatchObject({ name: 'Bambu PLA Basic', filamentId: 'GFA00' });
  });

  it('collapses a cloud user preset duplicated per model, which has no filament id to key on', () => {
    const options = buildFilamentPresetOptions({
      cloudSettings: [
        cloudSetting({ setting_id: 'PFUSaaa', name: 'My PETG @BBL X1C', is_custom: true }),
        cloudSetting({ setting_id: 'PFUSbbb', name: 'My PETG @BBL P1S', is_custom: true }),
      ],
    });
    expect(options).toHaveLength(1);
  });

  it('keeps distinct cloud filaments apart', () => {
    const options = buildFilamentPresetOptions({
      cloudSettings: [
        cloudSetting({ setting_id: 'GFSA00_00', name: 'Bambu PLA Basic @BBL X1C' }),
        cloudSetting({ setting_id: 'GFSA01_00', name: 'Bambu PLA Matte @BBL X1C' }),
      ],
    });
    expect(options.map(o => o.name)).toEqual(['Bambu PLA Basic', 'Bambu PLA Matte']);
  });

  it('collapses one imported filament re-imported for several printers', () => {
    const options = buildFilamentPresetOptions({
      localPresets: [
        localPreset({ id: 1, name: 'Elegoo PLA+ @BBL X1C 0.4 nozzle' }),
        localPreset({ id: 2, name: 'Elegoo PLA+ @BBL P1S 0.4 nozzle' }),
      ],
    });
    expect(options).toHaveLength(1);
  });

  it('keeps imported presets of different materials that share a generic id path', () => {
    // Keyed by name, not by generic id — otherwise two distinct PLA imports
    // would collapse into one because both map to GFL99.
    const options = buildFilamentPresetOptions({
      localPresets: [
        localPreset({ id: 1, name: 'Elegoo PLA+' }),
        localPreset({ id: 2, name: 'Polymaker PolyLite PLA' }),
      ],
    });
    expect(options).toHaveLength(2);
  });

  it('collapses Orca Cloud copies of one filament', () => {
    const options = buildFilamentPresetOptions({
      orcaProfiles: [
        orcaProfile({ setting_id: 'u1', name: 'Sunlu PETG @BBL X1C' }),
        orcaProfile({ setting_id: 'u2', name: 'Sunlu PETG @BBL A1' }),
      ],
    });
    expect(options).toHaveLength(1);
  });

  it('drops a builtin the cloud tier covers under a variant setting_id', () => {
    // Cloud ids carry a "_NN" variant suffix; without normalising it the
    // builtin tier lists the same filament a second time.
    const options = buildFilamentPresetOptions({
      cloudSettings: [cloudSetting({ setting_id: 'GFSA00_01', name: 'Bambu PLA Basic @BBL X1C' })],
      builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic'), builtin('GFA01', 'Bambu PLA Matte')],
    });
    expect(options.filter(o => o.source === 'builtin').map(o => o.filamentId)).toEqual(['GFA01']);
  });

  it('drops a builtin already offered by a cloud tier, matching the S-infix spelling', () => {
    const options = buildFilamentPresetOptions({
      cloudSettings: [cloudSetting({ setting_id: 'GFSA00', name: 'Bambu PLA Basic' })],
      builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic'), builtin('GFA01', 'Bambu PLA Matte')],
    });
    expect(options.filter(o => o.source === 'builtin').map(o => o.filamentId)).toEqual(['GFA01']);
  });

  it('drops a bambu cloud preset Orca Cloud already covers', () => {
    const options = buildFilamentPresetOptions({
      orcaProfiles: [orcaProfile({ setting_id: 'shared-id' })],
      cloudSettings: [cloudSetting({ setting_id: 'shared-id' })],
    });
    expect(options.map(o => o.source)).toEqual(['orca_cloud']);
  });

  it('keeps an Orca Cloud library that overlaps an imported bundle by name', () => {
    // These are usually the same profiles reached two ways. Letting the
    // imported tier claim the name emptied the Orca Cloud group down to
    // whatever happened not to be imported too.
    const options = buildFilamentPresetOptions({
      localPresets: [
        localPreset({ id: 1, name: 'Elegoo PLA+ @BBL X1C' }),
        localPreset({ id: 2, name: 'Sunlu PETG @BBL X1C' }),
      ],
      orcaProfiles: [
        orcaProfile({ setting_id: 'u1', name: 'Elegoo PLA+ @BBL X1C' }),
        orcaProfile({ setting_id: 'u2', name: 'Sunlu PETG @BBL X1C' }),
      ],
    });
    expect(options.filter(o => o.source === 'local')).toHaveLength(2);
    expect(options.filter(o => o.source === 'orca_cloud')).toHaveLength(2);
  });

  it('still drops a cross-tier row that carries an id a higher tier claimed', () => {
    // A shared id is true identity, unlike a shared name.
    const options = buildFilamentPresetOptions({
      orcaProfiles: [orcaProfile({ setting_id: 'shared-id', name: 'Sunlu PETG' })],
      cloudSettings: [cloudSetting({ setting_id: 'shared-id', name: 'Something Else' })],
    });
    expect(options.map(o => o.source)).toEqual(['orca_cloud']);
  });

  it('never echoes a filament the tiers above already offered back from the builtin table', () => {
    // The builtin tier is a static copy of the same Bambu catalogue, so
    // without a name check it re-listed everything under a fourth heading.
    const options = buildFilamentPresetOptions({
      localPresets: [localPreset({ name: 'Bambu PLA Basic @BBL X1C 0.4 nozzle' })],
      builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic'), builtin('GFA01', 'Bambu PLA Matte')],
    });
    expect(options.map(o => [o.source, o.name])).toEqual([
      ['local', 'Bambu PLA Basic'],
      ['builtin', 'Bambu PLA Matte'],
    ]);
  });

  it('suppresses a builtin a cloud tier already named, even with no id overlap', () => {
    const options = buildFilamentPresetOptions({
      cloudSettings: [cloudSetting({ setting_id: 'PFUSaaa', name: 'Bambu PLA Basic @BBL X1C', is_custom: true })],
      builtinFilaments: [builtin('GFA00', 'Bambu PLA Basic')],
    });
    expect(options.map(o => o.source)).toEqual(['cloud']);
  });

  it('does not let one import swallow every filament of the same material', () => {
    // Imports resolve to a shared generic id (all PLA → GFL99); claiming that
    // id would hide every other PLA behind the first one imported.
    const options = buildFilamentPresetOptions({
      localPresets: [
        localPreset({ id: 1, name: 'Elegoo PLA+', filament_type: 'PLA' }),
        localPreset({ id: 2, name: 'Polymaker PolyLite PLA', filament_type: 'PLA' }),
      ],
      builtinFilaments: [builtin('GFL99', 'Generic PLA')],
    });
    expect(options.map(o => o.name)).toEqual([
      'Elegoo PLA+',
      'Polymaker PolyLite PLA',
      'Generic PLA',
    ]);
  });

  it('strips printer suffixes from displayed names', () => {
    const [option] = buildFilamentPresetOptions({ localPresets: [localPreset()] });
    expect(option.name).toBe('Elegoo PLA+');
  });
});

describe('resolveFilamentId', () => {
  const option = (over = {}) => ({
    id: 'PFUS9ac902733670a9',
    name: 'My PETG',
    source: 'cloud' as const,
    filamentId: '',
    filamentType: 'PETG',
    ...over,
  });

  it('returns an already-known id without fetching', async () => {
    const fetchDetail = vi.fn();
    await expect(resolveFilamentId(option({ filamentId: 'GFA00' }), fetchDetail)).resolves.toBe('GFA00');
    expect(fetchDetail).not.toHaveBeenCalled();
  });

  it('fetches the cloud detail for a user preset', async () => {
    const fetchDetail = vi.fn().mockResolvedValue({ filament_id: 'P285e239' });
    await expect(resolveFilamentId(option(), fetchDetail)).resolves.toBe('P285e239');
    expect(fetchDetail).toHaveBeenCalledWith('PFUS9ac902733670a9');
  });

  it('returns empty when the detail carries no filament_id', async () => {
    // Never fall back to base_id: that collapses a custom preset onto the
    // generic it inherits from (#1053).
    const fetchDetail = vi.fn().mockResolvedValue({ base_id: 'GFSG98_09' });
    await expect(resolveFilamentId(option(), fetchDetail)).resolves.toBe('');
  });

  it('returns empty when the detail lookup fails', async () => {
    const fetchDetail = vi.fn().mockRejectedValue(new Error('offline'));
    await expect(resolveFilamentId(option(), fetchDetail)).resolves.toBe('');
  });

  it('does not fetch for a non-cloud tier that resolved to nothing', async () => {
    const fetchDetail = vi.fn();
    const unknown = option({ source: 'local' as const, filamentType: 'UNOBTANIUM' });
    await expect(resolveFilamentId(unknown, fetchDetail)).resolves.toBe('');
    expect(fetchDetail).not.toHaveBeenCalled();
  });
});
