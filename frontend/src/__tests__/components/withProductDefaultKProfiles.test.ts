/**
 * withProductDefaultKProfiles (#1326): a page that saves a new spool's K
 * profiles without showing the product's default keeps the default for every
 * hotend nothing was picked for.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { api } from '../../api/client';
import { withProductDefaultKProfiles } from '../../components/spool-form/utils';

vi.mock('../../api/client', () => ({ api: { resolveProfileDefault: vi.fn() } }));

const mApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const product = { brand: 'Bambu Lab ', material: 'PETG', subtype: 'HF', color_name: '' };
const k = (printer_id: number, cali_idx: number, extruder = 0) => ({
  printer_id, extruder, nozzle_diameter: '0.4', k_value: 0.02, name: null, cali_idx, setting_id: null,
});

describe('withProductDefaultKProfiles', () => {
  beforeEach(() => vi.clearAllMocks());

  it('adds the default for the hotends nothing was picked for', async () => {
    mApi.resolveProfileDefault.mockResolvedValue({ k_profiles: [k(1, 5), k(1, 6, 1), k(3, 7)] });
    const merged = await withProductDefaultKProfiles([k(3, 9)], product);
    expect(merged.map((p) => [p.printer_id, p.extruder, p.cali_idx])).toEqual([
      [3, 0, 9],
      [1, 0, 5],
      [1, 1, 6],
    ]);
    expect(mApi.resolveProfileDefault).toHaveBeenCalledWith({ brand: 'Bambu Lab', material: 'PETG', subtype: 'HF', color_name: '' });
  });

  it('without a default or a lookup, the picks alone', async () => {
    mApi.resolveProfileDefault.mockResolvedValue(null);
    expect(await withProductDefaultKProfiles([k(3, 9)], product)).toEqual([k(3, 9)]);
    mApi.resolveProfileDefault.mockRejectedValue(new Error('offline'));
    expect(await withProductDefaultKProfiles([k(3, 9)], product)).toEqual([k(3, 9)]);
  });
});
