/**
 * Machine-control wiring on the printer card: bed / chamber temperature,
 * fan speeds, airduct mode, chamber light, XY / extruder jog, homing and the
 * H2D left/right extruder selection.
 *
 * Bed jog, nozzle temperature and print speed are covered elsewhere; these
 * tests pin what reaches the wire for the rest.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
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

const printerFor = (model: string, extra: Record<string, unknown> = {}) => ({
  id: PRINTER_ID,
  name: `Test ${model}`,
  ip_address: '192.0.2.103',
  serial_number: '00M09A350100003',
  access_code: '12345678',
  model,
  enabled: true,
  nozzle_diameter: 0.4,
  nozzle_type: 'hardened_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  ...extra,
});

const idleStatus = (extra: Record<string, unknown> = {}) => ({
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  speed_level: 2,
  chamber_light: false,
  cooling_fan_speed: 0,
  big_fan1_speed: 0,
  big_fan2_speed: 0,
  vt_tray: [],
  ams: [],
  ...extra,
});

type Sent = { path: string; params: Record<string, string> };

function setup(model: string, statusExtra: Record<string, unknown> = {}, printerExtra: Record<string, unknown> = {}) {
  const sent: Sent[] = [];
  const record = ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    sent.push({
      path: url.pathname.replace(`/api/v1/printers/${PRINTER_ID}/`, ''),
      params: Object.fromEntries(url.searchParams.entries()),
    });
    return HttpResponse.json({ success: true, message: 'ok' });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([printerFor(model, printerExtra)])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(idleStatus(statusExtra))),
    http.post('/api/v1/printers/:id/temperature/bed', record),
    http.post('/api/v1/printers/:id/temperature/chamber', record),
    http.post('/api/v1/printers/:id/temperature/nozzle', record),
    http.post('/api/v1/printers/:id/fan-speed', record),
    http.post('/api/v1/printers/:id/airduct-mode', record),
    http.post('/api/v1/printers/:id/chamber-light', record),
    http.post('/api/v1/printers/:id/xy-jog', record),
    http.post('/api/v1/printers/:id/extruder-jog', record),
    http.post('/api/v1/printers/:id/home-axes', record),
    http.post('/api/v1/printers/:id/bed-jog', record),
    http.post('/api/v1/printers/:id/select-extruder', record),
  );
  render(<PrintersPage />);
  return sent;
}

/** Popover container from its title text (title div's parent). */
async function popover(title: string) {
  return (await screen.findByText(title)).parentElement as HTMLElement;
}

async function submitCustom(user: ReturnType<typeof userEvent.setup>, title: string, value: string) {
  const pop = await popover(title);
  await user.type(within(pop).getByPlaceholderText('Custom'), value);
  await user.click(within(pop).getByRole('button', { name: 'Set' }));
}

describe('PrintersPage machine controls: temperatures', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('bed popover: custom 60 -> POST temperature/bed?target=60 for this printer', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click((await screen.findByText('Bed')).parentElement!);
    await submitCustom(user, 'Set Bed Temperature', '60');

    await waitFor(() => expect(sent).toEqual([{ path: 'temperature/bed', params: { target: '60' } }]));
  });

  it('chamber popover on a printer with a chamber heater -> POST temperature/chamber', async () => {
    const user = userEvent.setup();
    const sent = setup('H2D', { supports_chamber_heater: true });

    await user.click((await screen.findByText('Chamber')).parentElement!);
    await submitCustom(user, 'Set Chamber Temperature', '45');

    await waitFor(() => expect(sent).toEqual([{ path: 'temperature/chamber', params: { target: '45' } }]));
  });

  it('chamber reading on a sensor-only printer opens no temperature control', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C', { supports_chamber_heater: false });

    await user.click((await screen.findByText('Chamber')).parentElement!);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('Set Chamber Temperature')).toBeNull();
    expect(sent).toEqual([]);
  });

  it('without printers:control the bed card opens no popover', async () => {
    permissions.denied = new Set(['printers:control']);
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click((await screen.findByText('Bed')).parentElement!);
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByText('Set Bed Temperature')).toBeNull();
    expect(sent).toEqual([]);
  });
});

describe('PrintersPage machine controls: fans', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it.each([
    ['Part Cooling Fan', 'part', '70'],
    ['Auxiliary Fan', 'aux', '30'],
    ['Chamber Fan', 'chamber', '55'],
  ])('%s -> POST fan-speed?fan=%s', async (label, fan, speed) => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click(await screen.findByTitle(label));
    await submitCustom(user, `Set ${label} Speed`, speed);

    await waitFor(() => expect(sent).toEqual([{ path: 'fan-speed', params: { fan, speed } }]));
  });
});

describe('PrintersPage machine controls: light and airduct', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('chamber light toggle sends on=true when the light is off', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C', { chamber_light: false });

    await user.click(await screen.findByTitle('Turn on chamber light'));
    await waitFor(() => expect(sent).toEqual([{ path: 'chamber-light', params: { on: 'true' } }]));
  });

  it('chamber light toggle sends on=false when the light is on', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C', { chamber_light: true });

    await user.click(await screen.findByTitle('Turn off chamber light'));
    await waitFor(() => expect(sent).toEqual([{ path: 'chamber-light', params: { on: 'false' } }]));
  });

  it('chamber light is disabled without printers:control and sends nothing', async () => {
    permissions.denied = new Set(['printers:control']);
    const user = userEvent.setup();
    const sent = setup('X1C', { chamber_light: false });

    await screen.findByText('Test X1C');
    await waitFor(() => expect(screen.getByText('Bed')).toBeInTheDocument());
    expect(screen.queryByTitle('Turn on chamber light')).toBeNull();
    const gated = screen
      .getAllByTitle('You do not have permission to control printers')
      .filter((el) => el.tagName === 'BUTTON');
    expect(gated.length).toBeGreaterThan(0);
    for (const b of gated) {
      expect(b).toBeDisabled();
      await user.click(b);
    }
    await new Promise((r) => setTimeout(r, 50));
    expect(sent).toEqual([]);
  });

  it.each([
    ['Heating', 'heating', 0],
    ['Cooling', 'cooling', 1],
  ])('airduct menu %s -> POST airduct-mode?mode=%s', async (label, mode, current) => {
    const user = userEvent.setup();
    const sent = setup('H2D', { airduct_mode: current });

    await user.click(await screen.findByTitle(/^Airduct Mode: /));
    await user.click(await screen.findByRole('button', { name: label }));
    await waitFor(() => expect(sent).toEqual([{ path: 'airduct-mode', params: { mode } }]));
  });

  it('an H2D Pro, stored as "H2D Pro", has the airduct menu too (#3335)', async () => {
    const user = userEvent.setup();
    const sent = setup('H2D Pro', { airduct_mode: 0 });

    await user.click(await screen.findByTitle(/^Airduct Mode: /));
    await user.click(await screen.findByRole('button', { name: 'Heating' }));
    await waitFor(() => expect(sent).toEqual([{ path: 'airduct-mode', params: { mode: 'heating' } }]));
  });

  it('an X1E has no airduct menu', async () => {
    setup('X1E', { airduct_mode: 0 });

    await screen.findByText('Test X1E');
    await waitFor(() => expect(screen.getByText('Bed')).toBeInTheDocument());
    expect(screen.queryByTitle(/^Airduct Mode: /)).toBeNull();
  });
});

describe('PrintersPage machine controls: movement', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it.each([
    ['Move Y forward', { x: '0', y: '10' }],
    ['Move Y back', { x: '0', y: '-10' }],
    ['Move X left', { x: '-10', y: '0' }],
    ['Move X right', { x: '10', y: '0' }],
  ])('%s -> POST xy-jog with the signed default 10 mm step', async (label, params) => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click(await screen.findByTitle('Jog Controls'));
    await user.click(await screen.findByLabelText(label));
    await waitFor(() => expect(sent).toEqual([{ path: 'xy-jog', params }]));
  });

  it('XY jog uses the selected step size', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click(await screen.findByTitle('Jog Controls'));
    await user.click(await screen.findByRole('button', { name: '50' }));
    await user.click(screen.getByLabelText('Move X left'));
    await waitFor(() => expect(sent).toEqual([{ path: 'xy-jog', params: { x: '-50', y: '0' } }]));
  });

  it.each([
    ['Retract filament', '-10'],
    ['Extrude filament', '10'],
  ])('%s -> POST extruder-jog?distance=%s', async (label, distance) => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click(await screen.findByTitle('Jog Controls'));
    await user.click(await screen.findByLabelText(label));
    await waitFor(() => expect(sent).toEqual([{ path: 'extruder-jog', params: { distance } }]));
  });

  it('Auto Home -> POST home-axes?axes=all', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C');

    await user.click(await screen.findByTitle('Jog Controls'));
    await user.click(await screen.findByLabelText('Auto Home'));
    await waitFor(() => expect(sent).toEqual([{ path: 'home-axes', params: { axes: 'all' } }]));
  });

  it('movement controls are not reachable while printing', async () => {
    const user = userEvent.setup();
    const sent = setup('X1C', { state: 'RUNNING' });
    const trigger = await screen.findByTitle('Disabled while printing');
    expect(trigger).toBeDisabled();
    await user.click(trigger);
    expect(screen.queryByLabelText('Move X left')).toBeNull();
    expect(sent).toEqual([]);
  });
});

describe('PrintersPage machine controls: H2D extruder selection', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  const dualStatus = (active: number) => ({
    temperatures: { nozzle: 25, nozzle_2: 25, bed: 25, chamber: 25 },
    active_extruder: active,
  });

  it.each([
    ['Left', '1', 0],
    ['Right', '0', 1],
  ])('selecting %s -> POST select-extruder?extruder=%s', async (label, extruder, active) => {
    const user = userEvent.setup();
    const sent = setup('H2D', dualStatus(active), { nozzle_count: 2 });

    const card = await screen.findByTitle(/^Active: /);
    fireEvent.click(card);
    const pop = await popover('Set Nozzle Selection');
    await user.click(within(pop).getByRole('button', { name: label }));

    await waitFor(() => expect(sent).toEqual([{ path: 'select-extruder', params: { extruder } }]));
  });
});
