/**
 * `fetchPrinterCalibrations` asks the printer for its K-profile table.
 *
 * Two properties, both measured on real hardware rather than reasoned about:
 *
 * 1. It asks for EVERY standard nozzle size, not only the sizes currently
 *    fitted. A K profile is stored on the printer per diameter and survives a
 *    nozzle swap, so fetching only what is screwed in right now hides a 0.6
 *    profile until a 0.6 is fitted, and stops a spool being prepared for a
 *    nozzle that is about to be changed to.
 *
 * 2. It asks for them ONE AT A TIME. H2-series firmware answers only the first
 *    one or two of a concurrent burst of `extrusion_cali_get` and silently
 *    drops the rest; each dropped request then costs a 5-second timeout before
 *    the retry. Measured on an H2C and an H2D: four parallel requests took 11s
 *    and 23s, against roughly 1s sent in series. An X1C answers all four
 *    concurrently, which is why this stayed hidden while only dual-diameter
 *    printers ever sent more than one request.
 *
 * The second is the one worth a test: it is invisible in every unit-level
 * result (the same rows come back either way) and only shows up as a stall on
 * one brand of hardware.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const getKProfiles = vi.fn();

vi.mock('../../../api/client', () => ({
  api: {
    get getKProfiles() {
      return getKProfiles;
    },
  },
}));

import { fetchPrinterCalibrations } from '../../../components/spool-form/utils';
import { STANDARD_NOZZLE_DIAMETERS } from '../../../components/spool-form/constants';

function profile(slotId: number, diameter: string) {
  return {
    slot_id: slotId,
    filament_id: 'GFL99',
    setting_id: 'GFSL99',
    name: `PLA ${diameter}`,
    k_value: '0.020',
    n_coef: '1.0',
    extruder_id: 0,
    nozzle_diameter: diameter,
  };
}

describe('fetchPrinterCalibrations', () => {
  beforeEach(() => {
    getKProfiles.mockReset();
  });

  it('asks for every standard nozzle size, not just the fitted one', async () => {
    getKProfiles.mockResolvedValue({ profiles: [] });

    await fetchPrinterCalibrations(1, { nozzles: [{ nozzle_diameter: '0.4' }] });

    const asked = getKProfiles.mock.calls.map(([, diameter]) => diameter);
    expect(asked).toEqual(expect.arrayContaining(STANDARD_NOZZLE_DIAMETERS));
  });

  it('includes an unusual fitted diameter alongside the standard sizes', async () => {
    getKProfiles.mockResolvedValue({ profiles: [] });

    await fetchPrinterCalibrations(1, { nozzles: [{ nozzle_diameter: '1.0' }] });

    const asked = getKProfiles.mock.calls.map(([, diameter]) => diameter);
    expect(asked).toContain('1.0');
    // Asked once each, no duplicate for a size that is both standard and fitted.
    expect(new Set(asked).size).toBe(asked.length);
  });

  it('never has two requests in flight at once', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    getKProfiles.mockImplementation(async () => {
      inFlight++;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise(resolve => setTimeout(resolve, 0));
      inFlight--;
      return { profiles: [] };
    });

    await fetchPrinterCalibrations(1, { nozzles: [{ nozzle_diameter: '0.4' }] });

    expect(getKProfiles.mock.calls.length).toBeGreaterThan(1);
    expect(maxInFlight).toBe(1);
  });

  it('keeps the diameters that answered when one request fails', async () => {
    getKProfiles.mockImplementation(async (_id: number, diameter: string) => {
      if (diameter === '0.6') throw new Error('printer said no');
      return { profiles: [profile(1, diameter)] };
    });

    const rows = await fetchPrinterCalibrations(1, { nozzles: [{ nozzle_diameter: '0.4' }] });

    const diameters = rows.map(r => r.nozzle_diameter);
    expect(diameters).toContain('0.4');
    expect(diameters).not.toContain('0.6');
  });

  it('flattens every size into one list of calibrations', async () => {
    getKProfiles.mockImplementation(async (_id: number, diameter: string) => ({
      profiles: diameter === '0.8' ? [] : [profile(Number(diameter.replace('.', '')), diameter)],
    }));

    const rows = await fetchPrinterCalibrations(1, { nozzles: [{ nozzle_diameter: '0.4' }] });

    expect(rows.map(r => r.nozzle_diameter).sort()).toEqual(['0.2', '0.4', '0.6']);
    expect(rows[0].k_value).toBeCloseTo(0.02);
  });
});
