/**
 * BIQU Panda Breath on the printer card (#1466): the tile shows the heater
 * bound to the printer, and its popover sends one change per click to that
 * printer's panda-breath route.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
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

const PRINTER_ID = 3;

const panda = (extra: Record<string, unknown> = {}) => ({
  device_id: 'AABBCC',
  online: true,
  chamber_temp: 41.4,
  target_temp: 50,
  work_on: true,
  mode: 'auto mode',
  filament_drying_mode: 'pla',
  drying_running: false,
  drying_remaining_min: 0,
  ...extra,
});

function setup(pandaBreath: Record<string, unknown> | null, connected = true) {
  const sent: unknown[] = [];
  server.use(
    http.get('/api/v1/printers/', () =>
      HttpResponse.json([
        {
          id: PRINTER_ID,
          name: 'Test X1C',
          ip_address: '192.0.2.103',
          serial_number: '00M09A350100003',
          access_code: '12345678',
          model: 'X1C',
          enabled: true,
          nozzle_diameter: 0.4,
          nozzle_type: 'hardened_steel',
          auto_archive: true,
          created_at: '2024-01-01T00:00:00Z',
          updated_at: '2024-01-01T00:00:00Z',
        },
      ])
    ),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({
        connected,
        state: connected ? 'IDLE' : null,
        progress: 0,
        temperatures: { nozzle: 25, bed: 25, chamber: 25 },
        vt_tray: [],
        ams: [],
        panda_breath: pandaBreath,
      })
    ),
    http.post('/api/v1/printers/:id/panda-breath', async ({ request, params }) => {
      sent.push({ printer: Number(params.id), body: await request.json() });
      return HttpResponse.json({ success: true });
    })
  );
  render(<PrintersPage />);
  return sent;
}

async function openPopover(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId('panda-breath-tile'));
  return (await screen.findAllByText('Panda Breath')).map((el) => el.parentElement as HTMLElement).at(-1)!;
}

describe('PrintersPage: Panda Breath tile', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('shows the chamber reading and target of the bound heater', async () => {
    setup(panda());
    const tile = await screen.findByTestId('panda-breath-tile');
    expect(tile).toHaveTextContent('41°C / 50°');
  });

  it('no tile without a bound heater', async () => {
    setup(null);
    await screen.findByText('Test X1C');
    expect(screen.queryByTestId('panda-breath-tile')).not.toBeInTheDocument();
  });

  it('an offline heater says so and opens no controls', async () => {
    const user = userEvent.setup();
    setup(panda({ online: false }));
    const tile = await screen.findByTestId('panda-breath-tile');
    expect(tile).toHaveTextContent('Offline');
    await user.click(tile);
    expect(screen.queryByText('Target temperature (0-60 °C):')).not.toBeInTheDocument();
  });

  it('turning off sends work_on=false for this printer', async () => {
    const user = userEvent.setup();
    const sent = setup(panda());
    const pop = await openPopover(user);
    await user.click(within(pop).getByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(sent).toEqual([{ printer: PRINTER_ID, body: { work_on: false } }]));
  });

  it('a mode button sends that mode', async () => {
    const user = userEvent.setup();
    const sent = setup(panda());
    const pop = await openPopover(user);
    expect(within(pop).getByRole('button', { name: 'Auto' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(pop).getByRole('button', { name: 'Drying' }));
    await waitFor(() => expect(sent).toEqual([{ printer: PRINTER_ID, body: { mode: 'filament drying' } }]));
  });

  it('a custom target sends target_temp', async () => {
    const user = userEvent.setup();
    const sent = setup(panda());
    const pop = await openPopover(user);
    await user.type(within(pop).getByPlaceholderText('Custom'), '55');
    await user.click(within(pop).getByRole('button', { name: 'Set' }));
    await waitFor(() => expect(sent).toEqual([{ printer: PRINTER_ID, body: { target_temp: 55 } }]));
  });

  it('without the control right the tile opens nothing', async () => {
    const user = userEvent.setup();
    permissions.denied = new Set(['printers:control']);
    setup(panda());
    await user.click(await screen.findByTestId('panda-breath-tile'));
    expect(screen.queryByText('Target temperature (0-60 °C):')).not.toBeInTheDocument();
  });

  it('stays on the card of a printer that is off, where the heater keeps running', async () => {
    const user = userEvent.setup();
    const sent = setup(panda({ mode: 'filament drying' }), false);
    const pop = await openPopover(user);
    await user.click(within(pop).getByRole('button', { name: 'Turn off' }));
    await waitFor(() => expect(sent).toEqual([{ printer: PRINTER_ID, body: { work_on: false } }]));
  });

  it('on the card of a printer that is off, too, only the control right opens it', async () => {
    const user = userEvent.setup();
    permissions.denied = new Set(['printers:control']);
    setup(panda(), false);
    await user.click(await screen.findByTestId('panda-breath-tile'));
    expect(screen.queryByText('Target temperature (0-60 °C):')).not.toBeInTheDocument();
  });
});
