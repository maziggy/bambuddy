/**
 * Per-printer override of "Require plate-clear confirmation" (#2860).
 *
 * The Edit Printer dialog picks inherit / on / off, and the printer card
 * follows the printer's own value before the global setting.
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
  plate_clear_override: 'inherit' as 'inherit' | 'on' | 'off',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const finishedStatus = {
  connected: true,
  state: 'FINISH',
  progress: 100,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
  awaiting_plate_clear: true,
};

let saved: Record<string, unknown> | null = null;
let statusServed = 0;

function mockPrinter(printer: typeof basePrinter, requirePlateClear = false) {
  const settings = { currency: 'USD', require_plate_clear: requirePlateClear };
  server.use(
    http.get('/api/v1/settings/', () => HttpResponse.json(settings)),
    http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json(settings)),
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => {
      statusServed += 1;
      return HttpResponse.json(finishedStatus);
    }),
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

beforeEach(() => {
  saved = null;
  statusServed = 0;
});

describe('EditPrinterModal plate-clear override', () => {

  it('starts on the global setting and saves a choice', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    const select = screen.getByLabelText('Plate-clear confirmation');
    expect(select).toHaveValue('inherit');
    await userEvent.selectOptions(select, 'off');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.plate_clear_override).toBe('off');
  });

  it('shows the printer’s current choice', async () => {
    mockPrinter({ ...basePrinter, plate_clear_override: 'on' });
    await openEditModal();

    expect(screen.getByLabelText('Plate-clear confirmation')).toHaveValue('on');
  });
});

describe('printer card plate-clear override', () => {
  it('hides the gate on a printer set to never require it, with the setting on', async () => {
    mockPrinter({ ...basePrinter, plate_clear_override: 'off' }, true);
    render(<PrintersPage />);
    await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
    // Let the finished status arrive, or the absence below proves nothing.
    await waitFor(() => expect(statusServed).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 100));

    expect(screen.queryByRole('button', { name: 'Mark plate as cleared' })).not.toBeInTheDocument();
  });

  it('shows the gate on a printer set to always require it, with the setting off', async () => {
    mockPrinter({ ...basePrinter, plate_clear_override: 'on' }, false);
    render(<PrintersPage />);

    expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
  });

  it('follows the setting without an override', async () => {
    mockPrinter(basePrinter, true);
    render(<PrintersPage />);

    expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
  });
});
