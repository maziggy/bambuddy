/**
 * A printer's own "Require plate-clear confirmation" (#2860): the Edit Printer
 * dialog saves on, off or null (follow the setting), and the printer card shows
 * the plate-clear action by the printer's value before the app setting.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const basePrinter = {
  id: 1,
  name: 'A1 mini',
  ip_address: '192.168.1.100',
  serial_number: '0300AA000000001',
  access_code: '12345678',
  model: 'A1 Mini',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  location: null,
  auto_archive: true,
  is_active: true,
  wear_cost_per_hour: null,
  dispatch_priority: null,
  require_plate_clear: null as boolean | null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const finishedAwaiting = {
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

function mockPrinter(printer: typeof basePrinter, appSetting: boolean) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printer])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(finishedAwaiting)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/settings/', () => HttpResponse.json({ currency: 'USD', require_plate_clear: appSetting })),
    http.get('/api/v1/settings/ui-preferences', () => HttpResponse.json({ require_plate_clear: appSetting })),
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
  await waitFor(() => expect(screen.getByText('A1 mini')).toBeInTheDocument());
  const menuBtn = [...document.querySelectorAll('button')].find((b) =>
    b.querySelector('.lucide-ellipsis-vertical'),
  )!;
  await userEvent.click(menuBtn);
  await userEvent.click(await screen.findByRole('button', { name: /^edit$/i }));
  await screen.findByText('Edit Printer');
}

const field = () => screen.getByLabelText('Require plate-clear confirmation');
const save = () => userEvent.click(screen.getByRole('button', { name: /save changes/i }));

describe('EditPrinterModal plate-clear confirmation', () => {
  beforeEach(() => {
    saved = null;
  });

  it('follows the setting by default and names its value', async () => {
    mockPrinter(basePrinter, true);
    await openEditModal();

    expect(field()).toHaveValue('');
    expect(screen.getByRole('option', { name: 'Use the setting (On)' })).toBeInTheDocument();
    await save();

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.require_plate_clear).toBeNull();
  });

  it('saves off for a printer that clears its own plate', async () => {
    mockPrinter(basePrinter, true);
    await openEditModal();

    await userEvent.selectOptions(field(), 'off');
    await save();

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.require_plate_clear).toBe(false);
  });

  it('saves on with the setting off', async () => {
    mockPrinter(basePrinter, false);
    await openEditModal();

    await userEvent.selectOptions(field(), 'on');
    await save();

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.require_plate_clear).toBe(true);
  });

  it('shows a saved override and can return to the setting', async () => {
    mockPrinter({ ...basePrinter, require_plate_clear: false }, true);
    await openEditModal();

    expect(field()).toHaveValue('off');
    await userEvent.selectOptions(field(), '');
    await save();

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.require_plate_clear).toBeNull();
  });
});

describe('printer card plate-clear action', () => {
  it('is hidden for a printer turned off, with the setting on', async () => {
    // Two printers, both finished and awaiting: only the one following the
    // setting offers the action, so once it shows the other has had its turn.
    mockPrinter({ ...basePrinter, require_plate_clear: false }, true);
    const p1s = { ...basePrinter, id: 2, name: 'P1S', serial_number: '01P00A000000002', require_plate_clear: null };
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([{ ...basePrinter, require_plate_clear: false }, p1s])),
    );
    render(<PrintersPage />);

    await screen.findByRole('button', { name: 'Mark plate as cleared' });
    expect(screen.getAllByRole('button', { name: 'Mark plate as cleared' })).toHaveLength(1);
  });

  it('is shown for a printer turned on, with the setting off', async () => {
    mockPrinter({ ...basePrinter, require_plate_clear: true }, false);
    render(<PrintersPage />);

    expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
  });

  it('uses the printer value in the grouped view too', async () => {
    // localStorage is a bare mock in the test setup.
    const getItem = vi.mocked(localStorage.getItem);
    getItem.mockImplementation((key: string) => (key === 'printerSortBy' ? 'location' : null));
    try {
      mockPrinter({ ...basePrinter, require_plate_clear: true }, false);
      render(<PrintersPage />);

      expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
      // The grouped view names the group, which the flat view does not.
      expect(screen.getAllByText(/Ungrouped/i).length).toBeGreaterThan(0);
    } finally {
      getItem.mockReset();
    }
  });

  it('follows the setting with no override', async () => {
    mockPrinter(basePrinter, true);
    render(<PrintersPage />);

    expect(await screen.findByRole('button', { name: 'Mark plate as cleared' })).toBeInTheDocument();
  });
});
