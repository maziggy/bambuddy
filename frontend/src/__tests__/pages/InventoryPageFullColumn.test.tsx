/**
 * The Inventory table has no "Full" column.
 *
 * It showed `added_full`, which nothing has ever written, so every row read
 * "-". A column layout saved while it was switched on must not bring it back.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import InventoryPageRouter from '../../pages/InventoryPage';

const SPOOL = {
  id: 5, material: 'PETG', subtype: null, brand: 'eSun', color_name: 'Blue', rgba: '0000FFFF',
  extra_colors: null, effect_type: null, label_weight: 1000, core_weight: 250, core_weight_catalog_id: null,
  weight_used: 400, slicer_filament: null, slicer_filament_name: null, nozzle_temp_min: null,
  nozzle_temp_max: null, note: null, added_full: null, last_used: null, encode_time: null, tag_uid: null,
  tray_uuid: null, data_origin: null, tag_type: null, archived_at: null, created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z', k_profiles: [], cost_per_kg: null, last_scale_weight: null,
  last_weighed_at: null, storage_location: null, category: null, low_stock_threshold_pct: null,
  spoolman_id: null, spoolman_filament_id: null,
};

afterEach(() => {
  vi.mocked(localStorage.getItem).mockReset();
});

describe('InventoryPage — Full column', () => {
  it('is not shown, even from a saved layout that had it on', async () => {
    const saved = JSON.stringify([
      { id: 'material', label: 'Material', visible: true },
      { id: 'added_full', label: 'Full', visible: true },
    ]);
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === 'bambuddy-inventory-columns' ? saved : null,
    );
    server.use(http.get('/api/v1/inventory/spools', () => HttpResponse.json([SPOOL])));

    render(<InventoryPageRouter />);

    await waitFor(() => expect(screen.getAllByText('PETG').length).toBeGreaterThan(0));
    const headers = screen.getAllByRole('columnheader').map((h) => h.textContent?.trim());
    expect(headers).toContain('Material');
    expect(headers).not.toContain('Full');
  });
});
