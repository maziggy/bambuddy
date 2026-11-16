/**
 * Plate changer capacity per printer (#3575).
 *
 * The printer card shows "Plates x / y" once a capacity is set, turns into a
 * prominent "empty" state at 0 and resets the count through the refill
 * endpoint. The Edit Printer dialog sets the capacity, never the count.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { setAuthToken } from '../../api/client';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

type PlatePrinter = {
  id: number;
  name: string;
  plate_capacity: number | null;
  plates_remaining: number | null;
  [key: string]: unknown;
};

const basePrinter: PlatePrinter = {
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
  require_plate_clear: null,
  plate_capacity: null,
  plates_remaining: null,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const idleStatus = {
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
let refillCalls = 0;
let listFetches = 0;
let current: PlatePrinter = basePrinter;
let store: Record<string, string> = {};

function mockPrinter(printer: PlatePrinter, status: Record<string, unknown> = idleStatus) {
  current = printer;
  server.use(
    http.get('/api/v1/printers/', () => {
      listFetches += 1;
      return HttpResponse.json([current]);
    }),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(status)),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/printers/diagnostic', () =>
      HttpResponse.json({ printer_id: null, ip_address: printer.ip_address, overall: 'ok', checks: [] }),
    ),
    http.post('/api/v1/printers/:id/refill-plates', () => {
      refillCalls += 1;
      current = { ...current, plates_remaining: current.plate_capacity };
      return HttpResponse.json(current);
    }),
    http.patch('/api/v1/printers/:id', async ({ request }) => {
      saved = (await request.json()) as Record<string, unknown>;
      return HttpResponse.json({ ...printer, ...saved });
    }),
  );
}

function useCardSize(size: string) {
  store['printerCardSize'] = size;
}

function mockWithoutClearPlatePermission() {
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({
        id: 1,
        username: 'viewer',
        is_admin: false,
        permissions: ['printers:read'],
      }),
    ),
  );
}

async function confirmRefill() {
  // Wait for the live status first: the controls move from the offline slot into
  // the status block once it arrives, which would detach an early click target.
  await screen.findAllByText(/idle|ready/i);
  await userEvent.click(await screen.findByRole('button', { name: /^Refilled \/ Emptied$/ }));
}

async function renderCard() {
  render(<PrintersPage />);
  await waitFor(() => expect(screen.getByText('X1 Carbon')).toBeInTheDocument());
}

async function openEditModal() {
  await renderCard();
  const menuBtn = [...document.querySelectorAll('button')].find((b) =>
    b.querySelector('.lucide-ellipsis-vertical'),
  )!;
  await userEvent.click(menuBtn);
  await userEvent.click(await screen.findByRole('button', { name: /^edit$/i }));
  await screen.findByText('Edit Printer');
}

beforeEach(() => {
  saved = null;
  refillCalls = 0;
  listFetches = 0;
  store = {};
  vi.mocked(localStorage.getItem).mockImplementation((key: string) => store[key] ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
    store[key] = String(value);
  });
});

afterEach(() => {
  setAuthToken(null);
  vi.mocked(localStorage.getItem).mockReset();
  vi.mocked(localStorage.setItem).mockReset();
});

describe('printer card plate changer', () => {
  it('shows neither pill nor button without a capacity', async () => {
    mockPrinter(basePrinter);
    await renderCard();
    // Let the status arrive, or the absence below proves nothing.
    await screen.findAllByText(/idle|ready/i);

    expect(screen.queryByText(/^Plates \d+ \/ \d+$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Refilled \/ Emptied/)).not.toBeInTheDocument();
  });

  it('shows the remaining count and a small refill button with plates left', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 3 });
    await renderCard();

    await screen.findAllByText(/idle|ready/i);
    expect(await screen.findByText('Plates 3 / 5')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refilled / Emptied' })).toBeInTheDocument();
    expect(screen.queryByText(/Plate changer empty/)).not.toBeInTheDocument();
  });

  it('shows the control exactly once while printing', async () => {
    mockPrinter(
      { ...basePrinter, plate_capacity: 5, plates_remaining: 3 },
      { ...idleStatus, state: 'RUNNING', progress: 42, layer_num: 10, total_layers: 100, subtask_name: 'Benchy' },
    );
    await renderCard();

    await screen.findByText('Plates 3 / 5');
    await screen.findAllByText(/running|printing/i);
    expect(screen.getAllByText('Plates 3 / 5')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Refilled / Emptied' })).toHaveLength(1);
  });

  it('shows the control exactly once for a printer in maintenance', async () => {
    mockPrinter({ ...basePrinter, is_active: false, plate_capacity: 5, plates_remaining: 3 });
    await renderCard();

    expect(await screen.findByText('Plates 3 / 5')).toBeInTheDocument();
    expect(screen.getAllByText('Plates 3 / 5')).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: 'Refilled / Emptied' })).toHaveLength(1);
  });

  it('shows the empty state and a prominent button at 0', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 0 });
    await renderCard();

    const button = await screen.findByRole('button', { name: 'Refilled / Emptied' });
    expect(button.className).toContain('flex-1');
    // The pill says "empty"; the button never repeats it.
    expect(button).not.toHaveTextContent(/Plate changer empty/);
    expect(screen.getByText('Plate changer empty')).toBeInTheDocument();
    expect(screen.queryByText('Plates 0 / 5')).not.toBeInTheDocument();
  });

  it('reads a missing count with a capacity set as empty', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: null });
    await renderCard();

    await screen.findAllByText(/idle|ready/i);
    expect(await screen.findByText('Plate changer empty')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Refilled / Emptied' })).toBeInTheDocument();
  });

  it('asks for confirmation first: cancel sends nothing', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 0 });
    await renderCard();

    await confirmRefill();
    expect(await screen.findByText('Reset plate changer count?')).toBeInTheDocument();
    expect(refillCalls).toBe(0);

    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByText('Reset plate changer count?')).not.toBeInTheDocument());
    expect(refillCalls).toBe(0);
  });

  it('resets the count through the refill endpoint and refetches the printers', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 0 });
    await renderCard();

    const fetchesBefore = listFetches;
    await confirmRefill();
    await screen.findByText('Reset plate changer count?');
    // The modal renders after the card, so its confirm button is the last match.
    const buttons = screen.getAllByRole('button', { name: 'Refilled / Emptied' });
    expect(refillCalls).toBe(0);
    await userEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(refillCalls).toBe(1));
    await waitFor(() => expect(listFetches).toBeGreaterThan(fetchesBefore));
    expect(await screen.findByText('Plates 5 / 5')).toBeInTheDocument();
  });

  it('is still shown while the printer is disconnected', async () => {
    mockPrinter(
      { ...basePrinter, plate_capacity: 5, plates_remaining: 2 },
      { ...idleStatus, connected: false, state: 'unknown' },
    );
    await renderCard();

    expect(await screen.findByText('Plates 2 / 5')).toBeInTheDocument();
    const button = screen.getByRole('button', { name: 'Refilled / Emptied' });
    expect(button).toBeEnabled();
  });

  it('disables the button without the clear-plate permission when auth is on', async () => {
    mockWithoutClearPlatePermission();
    mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 0 });
    await renderCard();

    const button = await screen.findByRole('button', { name: 'Refilled / Emptied' });
    await waitFor(() => expect(button).toBeDisabled());
    await userEvent.click(button);
    expect(refillCalls).toBe(0);
  });

  describe('compact view', () => {
    beforeEach(() => useCardSize('1'));

    it('renders one element that is both the count and the refill button', async () => {
      mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 3 });
      await renderCard();

      const button = (await screen.findByText('3/5')).closest('button')!;
      expect(button).toBeTruthy();
      expect(button).toHaveAttribute('title', expect.stringContaining('Plates 3 / 5'));
      expect(button).toHaveAttribute('aria-label', 'Plates 3 / 5: Refilled / Emptied');
      // Bare numbers only, and no second control for the same thing.
      expect(screen.queryByText('Plates 3 / 5')).not.toBeInTheDocument();
      expect(screen.getAllByText('3/5')).toHaveLength(1);
      expect(screen.queryByText('Refilled / Emptied')).not.toBeInTheDocument();

      await userEvent.click(button);
      expect(await screen.findByText('Reset plate changer count?')).toBeInTheDocument();
      expect(refillCalls).toBe(0);
    });

    it('shows the empty text in the single element at 0', async () => {
      mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 0 });
      await renderCard();

      const button = (await screen.findByText('0/5')).closest('button')!;
      expect(button.className).toContain('text-red-700');
      expect(button).toHaveAttribute('aria-label', 'Plate changer empty: Refilled / Emptied');
      expect(screen.getAllByText('0/5')).toHaveLength(1);
    });

    it('keeps the card size when the confirm modal is cancelled or its backdrop clicked', async () => {
      mockPrinter({ ...basePrinter, plate_capacity: 5, plates_remaining: 3 });
      await renderCard();

      await userEvent.click((await screen.findByText('3/5')).closest('button')!);
      const title = await screen.findByText('Reset plate changer count?');
      // The backdrop is the outermost fixed overlay around the dialog.
      const backdrop = title.closest('.fixed') as HTMLElement;
      expect(backdrop).toBeTruthy();
      await userEvent.click(backdrop);
      await waitFor(() => expect(screen.queryByText('Reset plate changer count?')).not.toBeInTheDocument());
      expect(store['printerCardSize']).toBe('1');

      await userEvent.click((await screen.findByText('3/5')).closest('button')!);
      await screen.findByText('Reset plate changer count?');
      await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
      await waitFor(() => expect(screen.queryByText('Reset plate changer count?')).not.toBeInTheDocument());
      expect(store['printerCardSize']).toBe('1');
      expect(refillCalls).toBe(0);
    });

    it('shows nothing without a capacity', async () => {
      mockPrinter(basePrinter);
      await renderCard();
      await new Promise((resolve) => setTimeout(resolve, 100));

      expect(screen.queryByText(/Plates \d+ \/ \d+/)).not.toBeInTheDocument();
      expect(screen.queryByText('Plate changer empty')).not.toBeInTheDocument();
    });
  });
});

describe('EditPrinterModal plate changer capacity', () => {
  it('saves a capacity as a number and never sends the count', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    await userEvent.type(screen.getByLabelText('Plate changer capacity'), '5');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.plate_capacity).toBe(5);
    expect(saved).not.toHaveProperty('plates_remaining');
  });

  it('leaves plate_capacity out when it did not change', async () => {
    mockPrinter(basePrinter);
    await openEditModal();

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).not.toHaveProperty('plate_capacity');
    expect(saved).not.toHaveProperty('plates_remaining');
  });

  it('leaves plate_capacity out when a set value is unchanged', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 4, plates_remaining: 2 });
    await openEditModal();

    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).not.toHaveProperty('plate_capacity');
  });

  it('treats a negative value as off', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 4, plates_remaining: 2 });
    await openEditModal();

    const field = screen.getByLabelText('Plate changer capacity');
    await userEvent.clear(field);
    fireEvent.change(field, { target: { value: '-3' } });
    // The input's min=0 blocks a click on Save, so submit the form directly.
    fireEvent.submit(field.closest('form')!);

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.plate_capacity).toBeNull();
  });

  it('shows the current capacity and clears it when emptied', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 4, plates_remaining: 2 });
    await openEditModal();

    const field = screen.getByLabelText('Plate changer capacity');
    expect(field).toHaveValue(4);
    await userEvent.clear(field);
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.plate_capacity).toBeNull();
    expect(saved).not.toHaveProperty('plates_remaining');
  });

  it('treats 0 as off and sends null', async () => {
    mockPrinter({ ...basePrinter, plate_capacity: 4, plates_remaining: 2 });
    await openEditModal();

    const field = screen.getByLabelText('Plate changer capacity');
    await userEvent.clear(field);
    await userEvent.type(field, '0');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved!.plate_capacity).toBeNull();
  });
});
