/**
 * A printer's place in the pick order of any-model jobs (#2915), in the Edit
 * Printer dialog: a number saves, empty clears it, and 0 is a place, not "none".
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
  wear_cost_per_hour: null,
  dispatch_priority: null as number | null,
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

describe('EditPrinterModal priority for any-model jobs', () => {
  beforeEach(() => {
    saved = null;
  });

  it('saves a priority the user enters', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    await userEvent.type(screen.getByLabelText(/Priority for any-model jobs/i), '3');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.dispatch_priority).toBe(3);
  });

  it('sends null when it was never set', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.dispatch_priority).toBeNull();
  });

  it('clears it when the field is emptied', async () => {
    mockPrinter({ ...basePrinter, dispatch_priority: 2 });
    await openEditModal();

    const field = screen.getByLabelText(/Priority for any-model jobs/i);
    expect(field).toHaveValue(2);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.dispatch_priority).toBeNull();
  });

  it('keeps 0 as a place, not as none', async () => {
    mockPrinter({ ...basePrinter, dispatch_priority: 0 });
    await openEditModal();

    expect(screen.getByLabelText(/Priority for any-model jobs/i)).toHaveValue(0);
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.dispatch_priority).toBe(0);
  });
});
