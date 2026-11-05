import { describe, expect, it } from 'vitest';
import { cloudPresetFilamentId, cloudPresetFilamentIds, filamentIdToSettingId, settingIdToFilamentId } from '../../utils/filamentIds';
import { toFilamentId } from '../../components/spool-form/utils';
import { slotPresetDescribesTray } from '../../utils/amsHelpers';

// #3387: the "drop the S after GF" rule is wrong for SUNLU and for a few
// P1/X1 presets that carry another filament's id. Mirrors
// backend/tests/unit/test_filament_ids_3387.py.
describe('settingIdToFilamentId', () => {
  it.each([
    ['GFSNLS03_04', 'GFSNL03'], // SUNLU PLA+ @BBL A1
    ['GFSA00_04', 'GFA00'], // Bambu PLA Basic @BBL A1, the plain rule
    ['GFSA05_10', 'GFA01'], // Bambu PLA Matte @BBL P1P
    ['GFSA05_02', 'GFA05'], // Bambu PLA Silk @BBL X1
    ['GFSA05', 'GFA05'], // the bare id Bambuddy builds from GFA05 keeps the rule
    ['GFSR99_10', 'GFU99'], // Generic TPU @BBL P1P
    ['GFSNL03', 'GFSNL03'], // already SUNLU's filament_id
    ['GFS99', 'GFS99'], // a support filament_id
    ['PFUScd84f663d2c2ef', 'PFUScd84f663d2c2ef'],
  ])('%s -> %s', (id, expected) => {
    expect(settingIdToFilamentId(id)).toBe(expected);
  });
});

describe('filamentIdToSettingId', () => {
  it.each([
    ['GFSNL03', 'GFSNLS03'],
    ['GFA00', 'GFSA00'],
    ['GFS99', 'GFSS99'],
    ['GFSL05', 'GFSL05'],
    ['P8a85d5a', 'P8a85d5a'],
  ])('%s -> %s', (id, expected) => {
    expect(filamentIdToSettingId(id)).toBe(expected);
  });
});

describe('toFilamentId', () => {
  it('matches a SUNLU spool to its K-profile', () => {
    expect(toFilamentId('GFSNLS03_04')).toBe(toFilamentId('GFSNL03'));
  });

  it('leaves a support filament_id whole', () => {
    expect(toFilamentId('GFS99')).toBe('GFS99');
  });
});

describe('slotPresetDescribesTray', () => {
  it('knows the slot still holds a preset with an unusual id', () => {
    expect(slotPresetDescribesTray('GFSNLS03_04', 'GFSNL03')).toBe(true);
    expect(slotPresetDescribesTray('GFSA05_10', 'GFA01')).toBe(true);
    expect(slotPresetDescribesTray('GFSA05_10', 'GFA05')).toBe(false);
  });
});

describe('cloudPresetFilamentId', () => {
  const silkOrMatte = { setting_id: 'GFSA05', filament_id: 'GFA01' };

  it('takes the list\'s id for a suffixed preset', () => {
    expect(cloudPresetFilamentId({ setting_id: 'GFSX01_02', filament_id: 'GFX07' })).toBe('GFX07');
  });

  it('reads an ambiguous bare id by the rule, unless it was picked from the list', () => {
    expect(cloudPresetFilamentId(silkOrMatte)).toBe('GFA05');
    expect(cloudPresetFilamentId(silkOrMatte, { pickedFromList: true })).toBe('GFA01');
    expect(cloudPresetFilamentId(silkOrMatte, { slotTrayInfoIdx: 'GFA01' })).toBe('GFA01');
    expect(cloudPresetFilamentId(silkOrMatte, { slotTrayInfoIdx: 'GFL99' })).toBe('GFA05');
  });

  it('matches a slot holding either filament', () => {
    expect(cloudPresetFilamentIds(silkOrMatte)).toEqual(['GFA01', 'GFA05']);
    expect(cloudPresetFilamentIds({ setting_id: 'PFUSab', filament_id: 'Pab12345' })).toEqual(['Pab12345']);
  });
});
