/**
 * The batch ID, owner and purchase date columns (#2828, #1301, #3408): off by
 * default, shown when switched on, sortable, and found by the search box.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
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
  batch_id: null,
  owner: null,
  purchase_date: null,
};

const SPOOLS = [
  { ...BASE, id: 1, brand: 'AlphaBrand', batch_id: 'LOT-2411-07', owner: 'Alice', purchase_date: '2026-03-14' },
  { ...BASE, id: 2, brand: 'BetaBrand', batch_id: 'LOT-1000-01', owner: 'Bob', purchase_date: '2026-01-02' },
  { ...BASE, id: 3, brand: 'GammaBrand' },
];

const SHOWN = JSON.stringify([
  { id: 'brand', visible: true },
  { id: 'batch_id', visible: true },
  { id: 'owner', visible: true },
  { id: 'purchase_date', visible: true },
]);

function setupHandlers() {
  server.use(
    http.get('/api/v1/settings/', () =>
      HttpResponse.json({ currency: 'USD', low_stock_threshold: 20.0, language: 'en', date_format: 'iso' }),
    ),
    http.get('/api/v1/settings/spoolman', () => HttpResponse.json({ spoolman_enabled: 'false', spoolman_url: '' })),
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

function showColumns() {
  vi.mocked(localStorage.getItem).mockImplementation((key) => (key === 'bambuddy-inventory-columns' ? SHOWN : null));
}

describe('InventoryPage batch ID, owner and purchase date columns', () => {
  beforeEach(() => {
    setupHandlers();
    vi.mocked(localStorage.getItem).mockReturnValue(null);
  });

  it('are hidden until switched on', async () => {
    render(<InventoryPageRouter />);

    await waitFor(() => expect(row('AlphaBrand')).toBeInTheDocument());
    expect(screen.queryByRole('columnheader', { name: /Batch ID/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /Owner/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: /Purchase date/ })).not.toBeInTheDocument();
  });

  it('show each spool’s values, and a dash where there is none', async () => {
    showColumns();
    render(<InventoryPageRouter />);

    await waitFor(() => expect(row('AlphaBrand')).toHaveTextContent('LOT-2411-07'));
    expect(row('AlphaBrand')).toHaveTextContent('Alice');
    expect(row('AlphaBrand')).toHaveTextContent('2026-03-14');
    expect(row('BetaBrand')).toHaveTextContent('Bob');
    expect(row('GammaBrand')).not.toHaveTextContent('LOT');
  });

  it('sort by purchase date', async () => {
    showColumns();
    vi.mocked(localStorage.getItem).mockImplementation((key) => {
      if (key === 'bambuddy-inventory-columns') return SHOWN;
      return key === 'bambuddy-inventory-sort' ? '{"column":"purchase_date","direction":"asc"}' : null;
    });
    render(<InventoryPageRouter />);

    await waitFor(() => {
      const texts = Array.from(document.querySelectorAll('tbody tr')).map((r) => r.textContent ?? '');
      const at = (brand: string) => texts.findIndex((t) => t.includes(brand));
      // None first, then the earlier day, then the later one.
      expect(at('GammaBrand')).toBeLessThan(at('BetaBrand'));
      expect(at('BetaBrand')).toBeLessThan(at('AlphaBrand'));
    });
  });

  it('are searched: one owner’s spools, one batch’s spools', async () => {
    const user = userEvent.setup();
    render(<InventoryPageRouter />);
    const box = await screen.findByPlaceholderText(/search/i);

    const rows = () => Array.from(document.querySelectorAll('tbody tr')).map((r) => r.textContent ?? '');

    await user.type(box, 'alice');
    await waitFor(() => expect(rows().some((t) => t.includes('BetaBrand'))).toBe(false));
    expect(rows().some((t) => t.includes('AlphaBrand'))).toBe(true);

    await user.clear(box);
    await user.type(box, 'LOT-1000');
    await waitFor(() => expect(rows().some((t) => t.includes('AlphaBrand'))).toBe(false));
    expect(rows().some((t) => t.includes('BetaBrand'))).toBe(true);
  });
});
