/**
 * The "Last dried" column (#2863): shown by default, with the temperature and
 * hours of the run when they are known.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const BASE = {
  material: 'PLA',
  subtype: 'Basic',
  color_name: 'Red',
  rgba: 'FF0000FF',
  label_weight: 1000,
  core_weight: 250,
  weight_used: 100,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: 220,
  nozzle_temp_max: 240,
  note: null,
  added_full: null,
  last_used: null,
  encode_time: null,
  tag_uid: null,
  tray_uuid: null,
  data_origin: null,
  tag_type: null,
  archived_at: null,
  created_at: '2025-01-01T00:00:00Z',
  updated_at: '2025-01-01T00:00:00Z',
  k_profiles: [],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  storage_location: null,
  category: null,
  low_stock_threshold_pct: null,
  weight_locked: false,
};

const SPOOLS = [
  { ...BASE, id: 1, brand: 'AlphaBrand', last_dried_at: '2026-09-01T12:00:00', last_dried_temp: 55, last_dried_hours: 7.5 },
  { ...BASE, id: 2, brand: 'BetaBrand', last_dried_at: '2026-09-02T12:00:00', last_dried_temp: null, last_dried_hours: null },
  { ...BASE, id: 3, brand: 'GammaBrand', last_dried_at: null },
];

function setupHandlers() {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json({ currency: 'USD', low_stock_threshold: 20.0, language: 'en' })),
    http.get('/api/v1/settings/spoolman', () =>
      HttpResponse.json({ spoolman_enabled: 'false', spoolman_url: '' })
    ),
    http.get('/api/v1/inventory/spools', () => HttpResponse.json(SPOOLS)),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/color-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/colors', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/spool-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/locations', () => HttpResponse.json([])),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
  );
}

function row(brand: string): HTMLElement {
  const found = Array.from(document.querySelectorAll('tbody tr')).find((r) => r.textContent?.includes(brand));
  if (!found) throw new Error(`no row for ${brand}`);
  return found as HTMLElement;
}

describe('InventoryPage last dried column', () => {
  beforeEach(() => {
    setupHandlers();
    vi.mocked(localStorage.getItem).mockReturnValue(null);
  });

  it('is visible by default', async () => {
    render(<InventoryPageRouter />);
    await waitFor(() => expect(screen.getByRole('columnheader', { name: /Last dried/ })).toBeInTheDocument());
  });

  it('shows the temperature and hours of the run when known', async () => {
    render(<InventoryPageRouter />);
    await waitFor(() => expect(row('AlphaBrand')).toHaveTextContent('55°C · 7.5h'));
    expect(row('BetaBrand')).not.toHaveTextContent('°C');
  });

  it('sorts by the date', async () => {
    vi.mocked(localStorage.getItem).mockImplementation((key) =>
      key === 'bambuddy-inventory-sort' ? '{"column":"last_dried","direction":"desc"}' : null,
    );
    render(<InventoryPageRouter />);

    await waitFor(() => {
      const brands = Array.from(document.querySelectorAll('tbody tr')).map((r) => r.textContent ?? '');
      expect(brands.findIndex((t) => t.includes('BetaBrand'))).toBeLessThan(
        brands.findIndex((t) => t.includes('AlphaBrand')),
      );
    });
  });
});
