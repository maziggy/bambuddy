/**
 * The print dialog prices each slot at the rate its print is charged.
 *
 * The estimate and the per-slot "g left" came from the built-in assignment
 * table in both inventory modes. In Spoolman mode that table holds nothing, or
 * rows from before the switch, so a Spoolman print was estimated at the
 * default rate or at a spool the printer is not drawing on. Both now come from
 * the mode-aware slot payload (`/printers/{id}/inventory-remain`).
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, waitFor, cleanup, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { FilamentMapping } from '../../components/PrintModal/FilamentMapping';

const status = {
  id: 1,
  name: 'X1C',
  connected: true,
  state: 'IDLE',
  ams: [{ id: 0, tray: [{ id: 0, tray_type: 'PLA', tray_color: 'FF0000', tray_info_idx: 'GFA00' }] }],
  vt_tray: [],
  ams_extruder_map: {},
};

const slot = (cost: number | null) => ({
  ams_id: 0,
  tray_id: 0,
  global_tray_id: 0,
  material_key: 'k',
  remaining_g: 312.4,
  extruder: 0,
  spool: null,
  cost_per_kg: cost,
});

// A row left in the built-in table from before the switch to Spoolman.
const staleBuiltIn = [
  { id: 1, spool_id: 9, printer_id: 1, ams_id: 0, tray_id: 0, spool: { cost_per_kg: 99, label_weight: 1000, weight_used: 0 } },
];

function renderWith(cost: number | null) {
  const onCost = vi.fn();
  server.use(
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/inventory/assignments', () => HttpResponse.json(staleBuiltIn)),
    http.get('/api/v1/printers/:id/inventory-remain', () =>
      HttpResponse.json({ inventory_remain_g: { '0': 312.4 }, slot_materials: [slot(cost)] }),
    ),
  );
  render(
    <FilamentMapping
      printerId={1}
      filamentReqs={{ filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 100, used_meters: 30 }] }}
      manualMappings={{}}
      onManualMappingChange={() => {}}
      currencySymbol="$"
      defaultCostPerKg={25}
      onEstimatedCostChange={onCost}
      defaultExpanded
    />,
  );
  return onCost;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('FilamentMapping slot price', () => {
  it("estimates at the bound spool's rate, not a stale built-in row's", async () => {
    const onCost = renderWith(40);
    await waitFor(() => expect(onCost).toHaveBeenLastCalledWith(4));
    expect(screen.getByText('$4.00')).toBeInTheDocument();
  });

  it('estimates an unpriced spool at the default rate', async () => {
    const onCost = renderWith(null);
    await waitFor(() => expect(onCost).toHaveBeenLastCalledWith(2.5));
  });

  it("offers the slot with the bound spool's grams left", async () => {
    renderWith(40);
    const trigger = await waitFor(() => screen.getAllByRole('combobox')[0]);
    fireEvent.click(trigger);
    const listbox = await waitFor(() => screen.getByRole('listbox'));
    expect(listbox.textContent).toContain('312g left');
    expect(listbox.textContent).not.toContain('1000g left');
  });
});
