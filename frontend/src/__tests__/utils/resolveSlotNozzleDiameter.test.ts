/**
 * Tests for resolveSlotNozzleDiameter helper (#1899).
 *
 * The AMS Slot config picker must filter filament presets by the nozzle that
 * actually feeds a given AMS, not the hardcoded 0.4mm default. This resolver
 * reads the installed nozzle diameter from the printer status, honouring the
 * per-AMS extruder binding on dual-nozzle printers. It returns undefined when
 * the hardware hasn't been reported, so the caller keeps its own default.
 */

import { describe, it, expect } from 'vitest';

import { resolveSlotNozzleDiameter } from '../../utils/amsHelpers';

describe('resolveSlotNozzleDiameter', () => {
  it('returns undefined when status is null or undefined', () => {
    expect(resolveSlotNozzleDiameter(null, 0)).toBeUndefined();
    expect(resolveSlotNozzleDiameter(undefined, 0)).toBeUndefined();
  });

  it('returns undefined when no nozzles are reported', () => {
    expect(resolveSlotNozzleDiameter({ nozzles: [] }, 0)).toBeUndefined();
    expect(resolveSlotNozzleDiameter({}, 0)).toBeUndefined();
  });

  it('returns undefined when the reported nozzle diameter is an empty default', () => {
    expect(resolveSlotNozzleDiameter({ nozzles: [{ nozzle_diameter: '' }] }, 0)).toBeUndefined();
  });

  it('returns the single-nozzle diameter regardless of amsId (no extruder map)', () => {
    const status = { nozzles: [{ nozzle_diameter: '0.6' }] };
    expect(resolveSlotNozzleDiameter(status, 0)).toBe('0.6');
    expect(resolveSlotNozzleDiameter(status, 3)).toBe('0.6');
  });

  it('resolves the per-AMS nozzle on a dual-nozzle printer via ams_extruder_map', () => {
    // AMS 0 → left nozzle (0.4), AMS 1 → right nozzle (0.6)
    const status = {
      nozzles: [{ nozzle_diameter: '0.4' }, { nozzle_diameter: '0.6' }],
      ams_extruder_map: { '0': 0, '1': 1 },
    };
    expect(resolveSlotNozzleDiameter(status, 0)).toBe('0.4');
    expect(resolveSlotNozzleDiameter(status, 1)).toBe('0.6');
  });

  it('falls back to the primary nozzle when the AMS is not in the extruder map', () => {
    const status = {
      nozzles: [{ nozzle_diameter: '0.4' }, { nozzle_diameter: '0.6' }],
      ams_extruder_map: { '0': 0 },
    };
    // AMS 5 has no mapping → index 0 (primary)
    expect(resolveSlotNozzleDiameter(status, 5)).toBe('0.4');
  });

  it('falls back to the primary nozzle when the mapped nozzle has no diameter', () => {
    // Dual-nozzle stub where the second entry is still an empty default.
    const status = {
      nozzles: [{ nozzle_diameter: '0.4' }, { nozzle_diameter: '' }],
      ams_extruder_map: { '1': 1 },
    };
    expect(resolveSlotNozzleDiameter(status, 1)).toBe('0.4');
  });
});
