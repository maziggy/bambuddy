/**
 * The material-number filter chip (#2870).
 *
 * The chip needs one slot for "no number assigned". The other chips spell
 * that '__none__', but a material number is free text, so '__none__' can be
 * a real value — and then picking it filtered for the spools that have no
 * number at all. The sentinel is now longer than the column's 64-character
 * cap, so no spool can collide with it.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
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

// One spool whose material number is literally the old sentinel, one with no
// number at all. The two must never be confused for each other.
const SPOOLS = [
  { ...BASE, id: 1, brand: 'AlphaBrand', material_number: '__none__' },
  { ...BASE, id: 2, brand: 'BetaBrand', material_number: null },
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

async function materialNumberSelect(): Promise<HTMLSelectElement> {
  return waitFor(() => {
    const found = screen
      .getAllByRole('combobox')
      .find((el) => el.querySelector('option[value=""]')?.textContent === 'Material No.');
    if (!found) throw new Error('material number chip not rendered');
    return found as HTMLSelectElement;
  });
}

/** Brand names shown in the table body — the chips list brands too. */
function rowBrands(): string[] {
  return Array.from(document.querySelectorAll('tbody tr'))
    .map((row) => row.textContent ?? '')
    .flatMap((text) => ['AlphaBrand', 'BetaBrand'].filter((b) => text.includes(b)));
}

describe('InventoryPage material-number filter', () => {
  beforeEach(() => {
    setupHandlers();
  });

  it('filters for the literal value "__none__" rather than for unnumbered spools', async () => {
    render(<InventoryPageRouter />);
    const select = await materialNumberSelect();
    await waitFor(() => expect(rowBrands()).toEqual(['AlphaBrand', 'BetaBrand']));

    fireEvent.change(select, { target: { value: '__none__' } });

    // The spool whose number IS '__none__', not the one without a number.
    await waitFor(() => expect(rowBrands()).toEqual(['AlphaBrand']));
  });

  it('still offers a slot that finds the spools with no number', async () => {
    render(<InventoryPageRouter />);
    const select = await materialNumberSelect();
    await waitFor(() => expect(rowBrands()).toEqual(['AlphaBrand', 'BetaBrand']));

    const noneOption = Array.from(select.options).find((o) => o.textContent === 'No material number');
    expect(noneOption).toBeTruthy();
    // The sentinel outruns SpoolBase's 64-character cap, so it is a value no
    // spool can carry.
    expect(noneOption!.value.length).toBeGreaterThan(64);

    fireEvent.change(select, { target: { value: noneOption!.value } });

    await waitFor(() => expect(rowBrands()).toEqual(['BetaBrand']));
  });
});
