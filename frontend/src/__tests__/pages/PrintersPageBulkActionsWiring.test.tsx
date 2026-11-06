/**
 * PrintersPage bulk actions: the requests go to exactly the selected printers.
 *
 * Three printers, all of which could take a Stop. Selecting two of them (by
 * clicking cards, by state, or by location) and confirming must stop those two
 * and never the third; cancelling the confirm must stop nothing. Bulk selection
 * needs printers:control, Clear Bed needs printers:clear_plate.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const permissions = { granted: [] as string[] };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => permissions.granted.includes(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const basePrinter = {
  ip_address: '192.0.2.100',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const printers = [
  { ...basePrinter, id: 11, name: 'Alpha', serial_number: '01P00A000000011', location: 'Lab' },
  { ...basePrinter, id: 12, name: 'Bravo', serial_number: '01P00A000000012', location: 'Lab' },
  { ...basePrinter, id: 13, name: 'Charlie', serial_number: '01P00A000000013', location: 'Shed' },
];

// 11 printing, 12 paused, 13 printing: every one of them is stoppable.
const defaultStates: Record<string, string> = { '11': 'RUNNING', '12': 'PAUSE', '13': 'RUNNING' };
let states: Record<string, string> = { ...defaultStates };

let calls: { action: string; id: string }[] = [];

function renderPage() {
  calls = [];
  const record = (action: string) => ({ params }: { params: Record<string, string | readonly string[] | undefined> }) => {
    calls.push({ action, id: params.id as string });
    return HttpResponse.json({ success: true, message: 'ok' });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/printers/:id/status', ({ params }) =>
      HttpResponse.json({
        connected: true,
        state: states[params.id as string],
        progress: 40,
        layer_num: 10,
        total_layers: 100,
        temperatures: { nozzle: 220, bed: 60, chamber: 30 },
        remaining_time: 600,
        filename: 'part.gcode',
        wifi_signal: -40,
        speed_level: 2,
        vt_tray: [],
        ams: [],
        awaiting_plate_clear: params.id === '12',
      }),
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.post('/api/v1/printers/:id/print/stop', record('stop')),
    http.post('/api/v1/printers/:id/print/pause', record('pause')),
    http.post('/api/v1/printers/:id/print/resume', record('resume')),
    http.post('/api/v1/printers/:id/clear-plate', record('clearPlate')),
    http.post('/api/v1/printers/:id/hms/clear', record('clearHMS')),
  );
  render(<PrintersPage />);
}

const card = (id: number) => document.getElementById(`printer-card-${id}`) as HTMLElement;

async function waitForCards() {
  await waitFor(() => {
    for (const p of printers) expect(card(p.id)).not.toBeNull();
  });
  // Every card's status must be in the cache before bulk enablement is read.
  await waitFor(() => {
    for (const p of printers) expect(card(p.id).textContent).toContain('Connected');
  });
}

async function enterSelectionMode(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getAllByTitle('Select')[0]);
  await waitFor(() => expect(card(11).querySelector('div.absolute.inset-0.z-20')).not.toBeNull());
}

async function clickCard(user: ReturnType<typeof userEvent.setup>, id: number) {
  await user.click(card(id).querySelector('div.absolute.inset-0.z-20') as HTMLElement);
}

const toolbar = () => screen.getByText(/^\d+ selected$/).closest('div.fixed') as HTMLElement;

async function stopAndConfirm(user: ReturnType<typeof userEvent.setup>, expectedCount: number) {
  const stop = within(toolbar()).getByRole('button', { name: 'Stop' });
  await waitFor(() => expect(stop).toBeEnabled());
  await user.click(stop);
  expect(await screen.findByText(`Stop ${expectedCount} Prints`)).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Stop All' }));
}

const stoppedIds = () => calls.filter((c) => c.action === 'stop').map((c) => c.id).sort();

describe('PrintersPage bulk actions wiring', () => {
  beforeEach(() => {
    permissions.granted = ['printers:control', 'printers:clear_plate'];
    states = { ...defaultStates };
  });

  it('stops exactly the two selected printers, not the third', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 11);
    await clickCard(user, 12);
    expect(within(toolbar()).getByText('2 selected')).toBeInTheDocument();

    await stopAndConfirm(user, 2);

    await waitFor(() => expect(stoppedIds()).toEqual(['11', '12']));
    // Settle, then make sure the unselected printer was never touched.
    await screen.findByText('Stop completed on 2 printer(s)');
    expect(calls.every((c) => c.id !== '13')).toBe(true);
    expect(calls.every((c) => c.action === 'stop')).toBe(true);
  });

  it('skips a selected printer that has nothing to stop', async () => {
    states = { ...defaultStates, '12': 'IDLE' };
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 11);
    await clickCard(user, 12);

    await stopAndConfirm(user, 2);
    await waitFor(() => expect(stoppedIds()).toEqual(['11']));
    await screen.findByText('Stop completed on 1 printer(s)');
    expect(calls).toHaveLength(1);
  });

  it('cancelling the bulk stop confirm sends nothing', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 11);
    await clickCard(user, 13);

    await user.click(within(toolbar()).getByRole('button', { name: 'Stop' }));
    expect(await screen.findByText('Stop 2 Prints')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Stop 2 Prints')).toBeNull());
    await new Promise((r) => setTimeout(r, 50));
    expect(calls).toEqual([]);
  });

  it('pause goes only to the selected printers that are printing', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 11);
    await clickCard(user, 12); // paused: not applicable for pause

    await user.click(within(toolbar()).getByRole('button', { name: 'Pause' }));
    await user.click(await screen.findByRole('button', { name: 'Pause All' }));

    await waitFor(() => expect(calls).toEqual([{ action: 'pause', id: '11' }]));
  });

  it('select by state picks only printers in that state', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);

    await user.click(within(toolbar()).getByRole('button', { name: /Select by State/ }));
    await user.click(within(toolbar()).getByRole('button', { name: /^Printing/ }));
    expect(within(toolbar()).getByText('2 selected')).toBeInTheDocument();

    await stopAndConfirm(user, 2);
    await waitFor(() => expect(stoppedIds()).toEqual(['11', '13']));
    await screen.findByText('Stop completed on 2 printer(s)');
    expect(calls.some((c) => c.id === '12')).toBe(false);
  });

  it('select by location picks only printers at that location', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);

    await user.click(within(toolbar()).getByRole('button', { name: /Select by Location/ }));
    await user.click(within(toolbar()).getByRole('button', { name: 'Shed' }));
    expect(within(toolbar()).getByText('1 selected')).toBeInTheDocument();

    await stopAndConfirm(user, 1);
    await waitFor(() => expect(stoppedIds()).toEqual(['13']));
    await screen.findByText('Stop completed on 1 printer(s)');
    expect(calls).toHaveLength(1);
  });

  it('clear bed goes only to selected printers awaiting a plate clear', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 11);
    await clickCard(user, 12);

    await user.click(within(toolbar()).getByRole('button', { name: 'Clear Bed' }));
    await user.click(await screen.findByRole('button', { name: 'Clear All' }));

    await waitFor(() => expect(calls).toEqual([{ action: 'clearPlate', id: '12' }]));
  });

  it('bulk selection is unavailable without printers:control', async () => {
    permissions.granted = ['printers:clear_plate'];
    renderPage();
    await waitForCards();
    for (const b of screen.getAllByTitle('Select')) expect(b).toBeDisabled();
    expect(screen.queryByText(/^\d+ selected$/)).toBeNull();
  });

  it('Clear Bed is disabled without printers:clear_plate', async () => {
    permissions.granted = ['printers:control'];
    const user = userEvent.setup();
    renderPage();
    await waitForCards();
    await enterSelectionMode(user);
    await clickCard(user, 12);

    const clearBed = within(toolbar()).getByRole('button', { name: 'Clear Bed' });
    expect(clearBed).toBeDisabled();
    await user.click(clearBed);
    expect(screen.queryByText(/Clear \d+ Print Beds/)).toBeNull();
    expect(calls).toEqual([]);
  });
});
