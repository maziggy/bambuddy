/**
 * Sorting the Inventory's Color column (#2729, reporter @macwhiz).
 *
 * The column existed and was visible by default, but was absent from the
 * page's sort-extractor map, so clicking its header did nothing at all. These
 * tests drive the real header rather than the sort key directly — the unit
 * tests in utils/colors.test.ts cover the ordering itself — so a regression
 * that drops the extractor again fails here even though the key still works.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import { render } from '../utils';
import InventoryPageRouter from '../../pages/InventoryPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const baseSpool = {
  subtype: null,
  brand: 'eSun',
  extra_colors: null,
  effect_type: null,
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  slicer_filament: null,
  slicer_filament_name: null,
  nozzle_temp_min: null,
  nozzle_temp_max: null,
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
  k_profiles: [] as never[],
  cost_per_kg: null,
  last_scale_weight: null,
  last_weighed_at: null,
  storage_location: null,
  category: null,
  low_stock_threshold_pct: null,
  spoolman_id: null,
  spoolman_filament_id: null,
  material: 'PLA',
  weight_used: 0,
};

// Deliberately seeded in an order no single field would produce, and with the
// two near-neutrals that a straight hue sort scatters into the blues and reds.
//
// The colour name rides on ``brand`` as well: the Color column is a swatch with
// no text and the Color Name column is hidden by default, so Brand is what
// makes the rendered order legible in an assertion.
const spool = (id: number, name: string, rgba: string | null) => ({
  ...baseSpool,
  id,
  color_name: name,
  brand: name,
  rgba,
});

const SPOOLS = [
  spool(1, 'Titan Gray', '5F6367FF'),
  spool(2, 'Sky Blue', '56B7E6FF'),
  spool(3, 'Black', '000000FF'),
  spool(4, 'Red', 'FF0000FF'),
  spool(5, 'Peanut Brown', '875718FF'),
  spool(6, 'No Colour', null),
];

const MOCK_SETTINGS = {
  currency: 'USD',
  language: 'en',
  date_format: 'system',
  time_format: 'system',
  low_stock_threshold: 20.0,
  spoolman_enabled: false,
  spoolman_url: '',
};

function setupHandlers() {
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(MOCK_SETTINGS)),
    http.get('/api/v1/settings/spoolman', () =>
      HttpResponse.json({ spoolman_enabled: 'false', spoolman_url: '' }),
    ),
    http.get('/api/v1/inventory/spools', () => HttpResponse.json(SPOOLS)),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/color-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/colors', () => HttpResponse.json([])),
    http.get('/api/v1/inventory/spool-catalog', () => HttpResponse.json([])),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
  );
}

/** Table body rows, header excluded. */
const dataRows = () => screen.getAllByRole('row').slice(1);

/** Position of the row whose text contains ``name``, or -1. */
const rowIndexOf = (name: string) =>
  dataRows().findIndex((row) => (row.textContent ?? '').includes(name));

describe('InventoryPage — sorting by colour', () => {
  beforeEach(() => {
    setupHandlers();
    // The page persists sort state per browser; start every test unsorted.
    vi.mocked(localStorage.getItem).mockReturnValue(null);
  });

  it('sorts the Color column into rainbow order with neutrals last', async () => {
    render(<InventoryPageRouter />);
    await waitFor(() => expect(dataRows().length).toBe(SPOOLS.length));

    // Make the Color Name column visible so the order is readable, then sort.
    fireEvent.click(screen.getByRole('columnheader', { name: /^color$/i }));

    await waitFor(() => {
      // Red -> Cyan -> Brown -> the neutrals -> the spool with no colour.
      const positions = ['Red', 'Sky Blue', 'Peanut Brown', 'Titan Gray', 'Black', 'No Colour'].map(
        rowIndexOf,
      );
      expect(positions.every((p) => p >= 0)).toBe(true);
      expect(positions).toEqual([...positions].sort((a, b) => a - b));
    });
  });

  it('reverses on a second click and clears on a third', async () => {
    render(<InventoryPageRouter />);
    await waitFor(() => expect(dataRows().length).toBe(SPOOLS.length));

    const header = screen.getByRole('columnheader', { name: /^color$/i });
    fireEvent.click(header);
    await waitFor(() => expect(rowIndexOf('Red')).toBeLessThan(rowIndexOf('Black')));

    fireEvent.click(header);
    await waitFor(() => expect(rowIndexOf('Black')).toBeLessThan(rowIndexOf('Red')));

    // Third click clears the sort, restoring the order the API returned.
    fireEvent.click(header);
    await waitFor(() => expect(rowIndexOf('Titan Gray')).toBe(0));
  });

  it('persists the colour sort across a remount', async () => {
    const { unmount } = render(<InventoryPageRouter />);
    await waitFor(() => expect(dataRows().length).toBe(SPOOLS.length));

    fireEvent.click(screen.getByRole('columnheader', { name: /^color$/i }));
    await waitFor(() =>
      expect(vi.mocked(localStorage.setItem).mock.calls.some(
        ([key, value]) => key === 'bambuddy-inventory-sort' && String(value).includes('rgba'),
      )).toBe(true),
    );

    unmount();
    vi.mocked(localStorage.getItem).mockImplementation((key) =>
      key === 'bambuddy-inventory-sort' ? '{"column":"rgba","direction":"asc"}' : null,
    );

    render(<InventoryPageRouter />);
    await waitFor(() => {
      expect(rowIndexOf('Red')).toBeLessThan(rowIndexOf('Black'));
    });
  });
});
