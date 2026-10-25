/**
 * Print control wiring on the printer card: Stop / Pause / Resume.
 *
 * Each button opens a confirm dialog; only confirming may reach the printer,
 * and the request must name the card's own printer id. Without
 * `printers:control` the buttons are disabled and nothing can be sent.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const permissions = { denied: new Set<string>() };

const mockUseAuth = {
  user: { id: 1, username: 'operator', permissions: [] as string[] },
  authEnabled: true,
  loading: false,
  isAdmin: false,
  login: vi.fn(),
  loginWithToken: vi.fn(),
  logout: vi.fn(),
  refreshAuth: vi.fn(),
  hasPermission: vi.fn((permission: string) => !permissions.denied.has(permission)),
  hasAnyPermission: vi.fn(() => true),
  canModify: vi.fn(() => true),
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';

const PRINTER_ID = 7;

const mockPrinter = {
  id: PRINTER_ID,
  name: 'Workshop X1C',
  ip_address: '192.0.2.100',
  serial_number: '00M09A350100007',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const statusFor = (state: string) => ({
  connected: true,
  state,
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
});

type Sent = { method: string; path: string };

function setup(state: string): Sent[] {
  const sent: Sent[] = [];
  const record = ({ request }: { request: Request }) => {
    sent.push({ method: request.method, path: new URL(request.url).pathname });
    return HttpResponse.json({ success: true, message: 'ok' });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([mockPrinter])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusFor(state))),
    http.post('/api/v1/printers/:id/print/stop', record),
    http.post('/api/v1/printers/:id/print/pause', record),
    http.post('/api/v1/printers/:id/print/resume', record),
  );
  render(<PrintersPage />);
  return sent;
}

async function controlButton(label: 'Stop' | 'Pause' | 'Resume') {
  return (await screen.findByRole('button', { name: label })) as HTMLButtonElement;
}

describe('PrintersPage print control wiring', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('Stop -> confirm sends POST /printers/{id}/print/stop for this printer only', async () => {
    const user = userEvent.setup();
    const sent = setup('RUNNING');

    await user.click(await controlButton('Stop'));
    // Nothing goes out before the operator confirms.
    expect(sent).toEqual([]);
    await user.click(await screen.findByRole('button', { name: 'Stop Print' }));

    await waitFor(() =>
      expect(sent).toEqual([{ method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/print/stop` }])
    );
  });

  it('Stop -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    const sent = setup('RUNNING');

    await user.click(await controlButton('Stop'));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop Print' })).toBeNull());
    // Give any stray mutation a tick to fire.
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([]);
  });

  it('Pause on a RUNNING printer -> confirm sends /print/pause', async () => {
    const user = userEvent.setup();
    const sent = setup('RUNNING');

    expect(screen.queryByRole('button', { name: 'Resume' })).toBeNull();
    await user.click(await controlButton('Pause'));
    await user.click(await screen.findByRole('button', { name: 'Pause Print' }));

    await waitFor(() =>
      expect(sent).toEqual([{ method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/print/pause` }])
    );
  });

  it('Pause -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    const sent = setup('RUNNING');

    await user.click(await controlButton('Pause'));
    await user.click(await screen.findByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Pause Print' })).toBeNull());
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([]);
  });

  it('Resume on a PAUSE printer -> confirm sends /print/resume', async () => {
    const user = userEvent.setup();
    const sent = setup('PAUSE');

    await user.click(await controlButton('Resume'));
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    await user.click(await screen.findByRole('button', { name: 'Resume Print' }));

    await waitFor(() =>
      expect(sent).toEqual([{ method: 'POST', path: `/api/v1/printers/${PRINTER_ID}/print/resume` }])
    );
  });

  it('Stop and Pause are disabled on an IDLE printer', async () => {
    setup('IDLE');
    expect(await controlButton('Stop')).toBeDisabled();
    expect(await controlButton('Pause')).toBeDisabled();
  });

  it('without printers:control the buttons are disabled and clicking sends nothing', async () => {
    permissions.denied = new Set(['printers:control']);
    const user = userEvent.setup();
    const sent = setup('RUNNING');

    // Disabled buttons carry the permission hint as title, which becomes the
    // accessible description, not the name.
    const stop = await controlButton('Stop');
    const pause = await controlButton('Pause');
    expect(stop).toBeDisabled();
    expect(pause).toBeDisabled();
    expect(stop).toHaveAttribute('title', 'You do not have permission to control printers');

    await user.click(stop);
    await user.click(pause);
    expect(screen.queryByRole('button', { name: 'Stop Print' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Pause Print' })).toBeNull();
    expect(sent).toEqual([]);
  });
});
