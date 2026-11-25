/**
 * Printer card wiring for the destructive / out-of-service actions:
 * delete printer (with or without its archives), smart-plug power off and
 * auto-off, maintenance mode and the plate-detection toggle.
 *
 * Every test asserts the request that actually leaves the browser, and the
 * destructive ones also pin that cancelling sends nothing.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient } from '@tanstack/react-query';
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

const PRINTER_ID = 4;
const PLUG_ID = 42;

const basePrinter = {
  id: PRINTER_ID,
  name: 'Garage P1S',
  ip_address: '192.0.2.104',
  serial_number: '01P00A000000004',
  access_code: '12345678',
  model: 'P1S',
  enabled: true,
  is_active: true,
  plate_detection_enabled: false,
  nozzle_diameter: 0.4,
  nozzle_type: 'stainless_steel',
  auto_archive: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

const statusFor = (state: string) => ({
  connected: true,
  state,
  progress: state === 'RUNNING' ? 40 : 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: { nozzle: 25, bed: 25, chamber: 25 },
  remaining_time: 0,
  filename: null,
  wifi_signal: -40,
  speed_level: 2,
  vt_tray: [],
  ams: [],
});

const smartPlug = {
  id: PLUG_ID,
  name: 'Printer Plug',
  plug_type: 'tasmota',
  ip_address: '192.0.2.50',
  ha_entity_id: null,
  enabled: true,
  printer_id: PRINTER_ID,
  auto_on: false,
  auto_off: false,
  auto_off_executed: false,
};

type Sent = { method: string; path: string; search: string; body: unknown };

interface SetupOptions {
  state?: string;
  printer?: Partial<typeof basePrinter>;
  withPlug?: boolean;
}

function setup({ state = 'IDLE', printer = {}, withPlug = false }: SetupOptions = {}) {
  const sent: Sent[] = [];
  let assignmentFetches = 0;
  const record = async ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    const text = await request.text();
    sent.push({
      method: request.method,
      path: url.pathname,
      search: url.search,
      body: text ? JSON.parse(text) : null,
    });
    return HttpResponse.json({ success: true, status: 'deleted', archives_deleted: true, action: 'off' });
  };
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json([{ ...basePrinter, ...printer }])),
    http.get('/api/v1/queue/', () => HttpResponse.json([])),
    http.get('/api/v1/printers/:id/status', () => HttpResponse.json(statusFor(state))),
    http.get('/api/v1/smart-plugs/by-printer/:id', () => HttpResponse.json(withPlug ? smartPlug : null)),
    http.get('/api/v1/smart-plugs/:id/status', () =>
      HttpResponse.json({ state: 'ON', reachable: true, device_name: 'Plug', energy: { power: 120 } })
    ),
    http.get('/api/v1/inventory/assignments', () => {
      assignmentFetches += 1;
      return HttpResponse.json([]);
    }),
    http.delete('/api/v1/printers/:id', record),
    http.patch('/api/v1/printers/:id', record),
    http.post('/api/v1/smart-plugs/:id/control', record),
    http.patch('/api/v1/smart-plugs/:id', record),
  );
  render(<PrintersPage />);
  return { sent, assignmentFetches: () => assignmentFetches };
}

/** Opens the card's 3-dot overflow menu. */
async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  // The trigger's title is t('common.more', 'More'); match loosely so the
  // label wording does not matter, but it must be the card's More button.
  await screen.findByText('Garage P1S');
  const trigger = (await screen.findAllByTitle(/more/i)).find((el) => el.tagName === 'BUTTON');
  expect(trigger).toBeDefined();
  await user.click(trigger!);
}

const settle = () => new Promise((r) => setTimeout(r, 50));

describe('PrintersPage delete printer wiring', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('confirm with "Delete print archives" checked sends DELETE ?delete_archives=true', async () => {
    const user = userEvent.setup();
    const { sent } = setup();

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const heading = await screen.findByText('Delete Printer');
    const dialog = heading.closest('div.fixed') as HTMLElement;
    expect(within(dialog).getByRole('checkbox')).toBeChecked();
    expect(sent).toEqual([]);

    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'DELETE',
      path: `/api/v1/printers/${PRINTER_ID}`,
      search: '?delete_archives=true',
    });
  });

  it('confirm with the archives box unchecked sends delete_archives=false', async () => {
    const user = userEvent.setup();
    const { sent } = setup();

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = (await screen.findByText('Delete Printer')).closest('div.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('checkbox'));
    expect(within(dialog).getByRole('checkbox')).not.toBeChecked();
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'DELETE',
      path: `/api/v1/printers/${PRINTER_ID}`,
      search: '?delete_archives=false',
    });
  });

  it('cancel sends nothing and resets the archives box for next time', async () => {
    const user = userEvent.setup();
    const { sent } = setup();

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    let dialog = (await screen.findByText('Delete Printer')).closest('div.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Delete Printer')).toBeNull());
    await settle();
    expect(sent).toEqual([]);

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    dialog = (await screen.findByText('Delete Printer')).closest('div.fixed') as HTMLElement;
    expect(within(dialog).getByRole('checkbox')).toBeChecked();
  });

  it('without printers:delete the menu entry opens nothing and sends nothing', async () => {
    permissions.denied = new Set(['printers:delete']);
    const user = userEvent.setup();
    const { sent } = setup();

    await openMenu(user);
    const entry = await screen.findByRole('button', { name: 'Delete' });
    expect(entry).toHaveAttribute('title', 'You do not have permission to delete printers');
    await user.click(entry);
    await settle();
    expect(screen.queryByText('Delete Printer')).toBeNull();
    expect(sent).toEqual([]);
  });

  // The backend drops the deleted printer's slot assignments; a cached list
  // would keep showing spools as assigned to a printer that no longer exists.
  it('a successful delete refetches the spool assignments', async () => {
    const user = userEvent.setup();
    const { sent, assignmentFetches } = setup();

    await waitFor(() => expect(assignmentFetches()).toBeGreaterThan(0));
    await settle();
    const before = assignmentFetches();

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    const dialog = (await screen.findByText('Delete Printer')).closest('div.fixed') as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(sent).toHaveLength(1));

    await waitFor(() => expect(assignmentFetches()).toBeGreaterThan(before));
  });

  it('a successful delete also refreshes the Spoolman slot assignments', async () => {
    const invalidate = vi.spyOn(QueryClient.prototype, 'invalidateQueries');
    try {
      const user = userEvent.setup();
      const { sent } = setup();

      await openMenu(user);
      await user.click(await screen.findByRole('button', { name: 'Delete' }));
      const dialog = (await screen.findByText('Delete Printer')).closest('div.fixed') as HTMLElement;
      await user.click(within(dialog).getByRole('button', { name: 'Delete' }));
      await waitFor(() => expect(sent).toHaveLength(1));

      await waitFor(() =>
        expect(invalidate).toHaveBeenCalledWith({ queryKey: ['spoolman-slot-assignments'] })
      );
    } finally {
      invalidate.mockRestore();
    }
  });
});

describe('PrintersPage smart plug power wiring', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('power off while RUNNING shows the printing warning; confirm POSTs action=off to the plug', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ state: 'RUNNING', withPlug: true });

    await user.click(await screen.findByTitle('Turn off'));
    expect(await screen.findByText(/"Garage P1S" is currently printing!/)).toBeInTheDocument();
    expect(sent).toEqual([]);

    await user.click(screen.getByRole('button', { name: 'Power Off' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'POST',
      path: `/api/v1/smart-plugs/${PLUG_ID}/control`,
      body: { action: 'off' },
    });
  });

  it('power off -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ state: 'RUNNING', withPlug: true });

    await user.click(await screen.findByTitle('Turn off'));
    await screen.findByText('Power Off Printer');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Power Off Printer')).toBeNull());
    await settle();
    expect(sent).toEqual([]);
  });

  it('auto-off toggle PATCHes the plug with the flipped auto_off', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ withPlug: true });

    await user.click(await screen.findByTitle('Auto power-off after print'));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'PATCH',
      path: `/api/v1/smart-plugs/${PLUG_ID}`,
      body: { auto_off: true },
    });
  });

  it('without smart_plugs:control the power and auto-off buttons are disabled', async () => {
    permissions.denied = new Set(['smart_plugs:control']);
    const user = userEvent.setup();
    const { sent } = setup({ state: 'RUNNING', withPlug: true });

    await screen.findByText('Printer Plug');
    const buttons = screen.getAllByTitle('You do not have permission to control smart plugs');
    expect(buttons).toHaveLength(2);
    for (const b of buttons) {
      expect(b).toBeDisabled();
      await user.click(b);
    }
    await settle();
    expect(screen.queryByText('Power Off Printer')).toBeNull();
    expect(sent).toEqual([]);
  });
});

describe('PrintersPage maintenance mode and plate detection wiring', () => {
  beforeEach(() => {
    permissions.denied = new Set();
  });

  it('entering maintenance on an idle printer PATCHes {is_active:false}', async () => {
    const user = userEvent.setup();
    const { sent } = setup();

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Enter maintenance mode' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'PATCH',
      path: `/api/v1/printers/${PRINTER_ID}`,
      body: { is_active: false },
    });
  });

  it('entering maintenance mid-print asks first; cancel sends nothing, confirm sends is_active:false', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ state: 'RUNNING' });

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Enter maintenance mode' }));
    await screen.findByText('Enter maintenance mode mid-print?');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText('Enter maintenance mode mid-print?')).toBeNull());
    await settle();
    expect(sent).toEqual([]);

    await openMenu(user);
    await user.click(await screen.findByRole('button', { name: 'Enter maintenance mode' }));
    const title = await screen.findByText('Enter maintenance mode mid-print?');
    const modal = title.closest('[role="dialog"]') ?? title.parentElement!.parentElement!.parentElement!;
    const confirm = within(modal as HTMLElement)
      .getAllByRole('button', { name: 'Enter maintenance mode' })
      .at(-1)!;
    await user.click(confirm);
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'PATCH',
      path: `/api/v1/printers/${PRINTER_ID}`,
      body: { is_active: false },
    });
  });

  it('exiting maintenance PATCHes {is_active:true}', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ printer: { is_active: false } });

    await user.click(await screen.findByRole('button', { name: 'Exit maintenance' }));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'PATCH',
      path: `/api/v1/printers/${PRINTER_ID}`,
      body: { is_active: true },
    });
  });

  it('plate-detection toggle PATCHes the inverse of the current setting', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ printer: { plate_detection_enabled: false } });

    await user.click(await screen.findByTitle('Plate check disabled - Click to enable'));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({
      method: 'PATCH',
      path: `/api/v1/printers/${PRINTER_ID}`,
      body: { plate_detection_enabled: true },
    });
  });

  it('plate-detection toggle on an enabled printer sends plate_detection_enabled:false', async () => {
    const user = userEvent.setup();
    const { sent } = setup({ printer: { plate_detection_enabled: true } });

    await user.click(await screen.findByTitle('Plate check enabled - Click to disable'));
    await waitFor(() => expect(sent).toHaveLength(1));
    expect(sent[0]).toMatchObject({ body: { plate_detection_enabled: false } });
  });
});
