/**
 * Wear cost per printing hour in the Edit Printer dialog (#694).
 *
 * The rate is optional: a number turns it on for the printer, and an empty
 * field sends null so the printer stops adding wear to its prints.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const basePrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: null,
  auto_archive: true,
  is_active: true,
  wear_cost_per_hour: null as number | null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const mockStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
};

let saved: Record<string, unknown> | null = null;

function mockPrinter(printer: typeof basePrinter) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(mockStatus)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/printers/diagnostic', () =>
      HttpResponse.json({ printer_id: null, ip_address: printer.ip_address, overall: 'ok', checks: [] }),
    ),
    http.patch('/api/v1/printers/:id', async ({ request }) => {
      saved = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...printer, ...saved });
    }),
  );
}

async function openEditModal() {
  render(<PrintersPage />);
  await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
  const menuBtn = [...document.querySelectorAll('button')].find((b) =>
    b.querySelector('.lucide-ellipsis-vertical'),
  )!;
  await userEvent.click(menuBtn);
  await userEvent.click(await screen.findByRole('button', { name: /^edit$/i }));
  await screen.findByText('Edit Printer');
}

describe('EditPrinterModal wear cost', () => {
  beforeEach(() => {
    saved = null;
  });

  it('saves a rate the user enters', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    await userEvent.type(screen.getByLabelText(/Wear cost per printing hour/i), '0.35');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.wear_cost_per_hour).toBe(0.35);
  });

  it('turns wear cost off when the field is cleared', async () => {
    mockPrinter({ ...basePrinter, wear_cost_per_hour: 0.5 });
    await openEditModal();

    const field = screen.getByLabelText(/Wear cost per printing hour/i);
    expect(field).toHaveValue(0.5);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.wear_cost_per_hour).toBeNull();
  });
});
