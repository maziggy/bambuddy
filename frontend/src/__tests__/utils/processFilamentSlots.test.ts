import { describe, expect, it } from 'vitest';
import { effectiveProcessValues, processFilamentSlots } from '../../utils/processFilamentSlots';

// #3736: the slots a slice's process settings print with beyond the geometry.
describe('processFilamentSlots', () => {
  it('counts the support slots only while supports are on', () => {
    expect(processFilamentSlots({ enable_support: '1', support_filament: '2', support_interface_filament: '3' })).toEqual(
      new Set([2, 3]),
    );
    expect(processFilamentSlots({ enable_support: '0', support_filament: '2' })).toEqual(new Set());
    expect(processFilamentSlots({ support_filament: '2' })).toEqual(new Set());
    expect(processFilamentSlots({ enable_support: true, support_filament: 2 })).toEqual(new Set([2]));
  });

  it('counts a region picker whatever the supports', () => {
    expect(processFilamentSlots({ enable_support: '0', sparse_infill_filament_id: '4', outer_wall_filament_id: 2 })).toEqual(
      new Set([2, 4]),
    );
  });

  it('reads one-element lists, and 0 or junk is no slot', () => {
    expect(processFilamentSlots({ enable_support: ['1'], support_interface_filament: ['3'] })).toEqual(new Set([3]));
    expect(processFilamentSlots({ enable_support: '1', support_filament: '0', support_interface_filament: 'x' })).toEqual(
      new Set(),
    );
  });
});

describe('effectiveProcessValues', () => {
  it('layers the preset, the ticked design keys and the dialog', () => {
    const merged = effectiveProcessValues(
      { enable_support: '0', support_filament: '0' },
      [
        { key: 'enable_support', value: '1', printer_coupled: false, preset_defining: false },
        { key: 'wall_loops', value: '5', printer_coupled: false, preset_defining: false },
      ],
      new Set(['enable_support']),
      { support_filament: '2', wall_loops: '' },
    );

    expect(merged).toEqual({ enable_support: '1', support_filament: '2' });
  });
});
