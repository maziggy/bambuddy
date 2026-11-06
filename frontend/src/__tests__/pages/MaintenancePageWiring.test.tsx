/**
 * MaintenancePage wiring: which item / printer / type each control sends,
 * the confirm on deleting maintenance types, and the permission gates.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { MaintenancePage } from '../../pages/MaintenancePage';
import { setAuthToken } from '../../api/client';

type Sent = { method: string; path: string; search: string; body: unknown };

function item(id: number, printerId: number, typeId: number, name: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    printer_id: printerId,
    printer_name: printerId === 1 ? 'Alpha Printer' : 'Beta Printer',
    printer_model: 'X1C',
    maintenance_type_id: typeId,
    maintenance_type_name: name,
    maintenance_type_icon: null,
    maintenance_type_wiki_url: 'https://wiki.example/doc',
    enabled: true,
    interval_hours: 100,
    interval_type: 'hours',
    current_hours: 50,
    hours_since_maintenance: 50,
    hours_until_due: 50,
    days_since_maintenance: null,
    days_until_due: null,
    is_due: false,
    is_warning: false,
    last_performed_at: null,
    ...overrides,
  };
}

const OVERVIEW = [
  {
    printer_id: 1,
    printer_name: 'Alpha Printer',
    printer_model: 'X1C',
    total_print_hours: 10.4,
    due_count: 0,
    warning_count: 0,
    maintenance_items: [item(11, 1, 1, 'Nozzle Task')],
  },
  {
    printer_id: 2,
    printer_name: 'Beta Printer',
    printer_model: 'X1C',
    total_print_hours: 250.2,
    due_count: 0,
    warning_count: 0,
    maintenance_items: [item(21, 2, 1, 'Nozzle Task')],
  },
];

const TYPES = [
  { id: 1, name: 'Clean Nozzle', description: null, default_interval_hours: 100, interval_type: 'hours', icon: null, wiki_url: null, is_system: true, created_at: '2026-01-01T00:00:00Z' },
  { id: 7, name: 'Custom Belt Check', description: null, default_interval_hours: 300, interval_type: 'hours', icon: null, wiki_url: null, is_system: false, created_at: '2026-01-01T00:00:00Z' },
];

let sent: Sent[];

async function record(request: Request) {
  const url = new URL(request.url);
  let body: unknown = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  sent.push({ method: request.method, path: url.pathname, search: url.search, body });
}

const writes = () => sent.filter((s) => s.method !== 'GET');

beforeEach(() => {
  sent = [];
  server.use(
    http.get('*/api/v1/maintenance/overview', () => HttpResponse.json(OVERVIEW)),
    http.get('*/api/v1/maintenance/types', () => HttpResponse.json(TYPES)),
    http.post('*/api/v1/maintenance/items/:id/perform', async ({ request }) => {
      await record(request);
      return HttpResponse.json(item(21, 2, 1, 'Clean Nozzle'));
    }),
    http.patch('*/api/v1/maintenance/items/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json(item(21, 2, 1, 'Clean Nozzle'));
    }),
    http.patch('*/api/v1/maintenance/printers/:id/hours', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ printer_id: 2, total_hours: 123, archive_hours: 0, offset_hours: 123 });
    }),
    http.delete('*/api/v1/maintenance/types/:id', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ status: 'deleted' });
    }),
    http.post('*/api/v1/maintenance/types/restore-defaults', async ({ request }) => {
      await record(request);
      return HttpResponse.json({ restored: 1 });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
  vi.restoreAllMocks();
});

/** The printer card whose heading is `name`. */
async function printerCard(name: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name });
  let el: HTMLElement | null = heading;
  while (el && !within(el).queryByText('Total Print Time')) el = el.parentElement;
  // `el` is the card header; its parent is the card, which also holds the expanded items.
  return el!.parentElement!;
}

async function openSettings(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'Settings' }));
}

function typeTile(name: string): HTMLElement {
  return screen.getByText(name).closest('.rounded-xl') as HTMLElement;
}

describe('MaintenancePage wiring: status tab', () => {
  it('Reset on a task sends performMaintenance for that printer\'s item only', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await user.click(await within(beta).findByRole('button', { name: 'Reset' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/maintenance/items/21/perform' });
    expect(await screen.findByText('Maintenance marked as complete')).toBeInTheDocument();
  });

  it('the item toggle PATCHes { enabled } for that item', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const alpha = await printerCard('Alpha Printer');
    await user.click(within(alpha).getByRole('button', { name: /expand/i }));
    await user.click(await within(alpha).findByRole('switch'));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({ method: 'PATCH', path: '/api/v1/maintenance/items/11', search: '', body: { enabled: false } });
  });

  it('editing print hours sends setPrinterHours for that printer', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByText('Total Print Time'));
    const input = within(beta).getByRole('spinbutton');
    expect(input).toHaveValue(250);
    await user.clear(input);
    await user.type(input, '123');
    await user.click(within(beta).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: 'PATCH',
      path: '/api/v1/maintenance/printers/2/hours',
      search: '?total_hours=123',
    });
  });

  it('cancelling the hours edit sends nothing', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByText('Total Print Time'));
    await user.type(within(beta).getByRole('spinbutton'), '9');
    await user.click(within(beta).getByRole('button', { name: 'Cancel' }));

    expect(within(beta).queryByRole('spinbutton')).not.toBeInTheDocument();
    expect(writes()).toEqual([]);
  });
});

describe('MaintenancePage wiring: settings tab', () => {
  it('deleting a default type asks first; confirming sends DELETE for that type', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    await openSettings(user);

    const tile = await waitFor(() => typeTile('Clean Nozzle'));
    await user.click(within(tile).getByRole('button'));
    expect(await screen.findByText('Delete default maintenance task?')).toBeInTheDocument();
    expect(writes()).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/maintenance/types/1' });
  });

  it('cancelling the default-type delete sends nothing', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    await openSettings(user);

    const tile = await waitFor(() => typeTile('Clean Nozzle'));
    await user.click(within(tile).getByRole('button'));
    await screen.findByText('Delete default maintenance task?');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Delete default maintenance task?')).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('a custom type is deleted only after the browser confirm says yes', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const user = userEvent.setup();
    render(<MaintenancePage />);
    await openSettings(user);

    const tile = await waitFor(() => typeTile('Custom Belt Check'));
    const trash = within(tile).getAllByRole('button').at(-1)!;
    await user.click(trash);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(writes()).toEqual([]);

    confirmSpy.mockReturnValue(true);
    await user.click(trash);
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/maintenance/types/7' });
  });

  it('Restore Default Tasks sends the restore request', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    await openSettings(user);

    await user.click(await screen.findByRole('button', { name: 'Restore Default Tasks' }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/maintenance/types/restore-defaults' });
  });
});

describe('MaintenancePage wiring: permissions', () => {
  function signInWith(permissions: string[]) {
    setAuthToken('test-token-1', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 9,
          username: 'viewer',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions,
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
    );
  }

  it('a read-only user gets disabled controls and nothing is sent', async () => {
    signInWith(['maintenance:read']);
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await waitFor(() =>
      expect(within(beta).getByTitle('You do not have permission to edit print hours')).toBeInTheDocument(),
    );
    await user.click(within(beta).getByText('Total Print Time'));
    expect(within(beta).queryByRole('spinbutton')).not.toBeInTheDocument();

    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    expect(await within(beta).findByRole('button', { name: 'Reset' })).toBeDisabled();
    expect(within(beta).getByRole('switch')).toBeDisabled();

    await openSettings(user);
    expect(await screen.findByRole('button', { name: 'Restore Default Tasks' })).toBeDisabled();
    const tile = typeTile('Clean Nozzle');
    expect(within(tile).getByRole('button')).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it('maintenance:update enables Reset but not the type delete', async () => {
    signInWith(['maintenance:read', 'maintenance:update']);
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await waitFor(() => expect(within(beta).getByRole('button', { name: 'Reset' })).not.toBeDisabled());

    await openSettings(user);
    const tile = await waitFor(() => typeTile('Clean Nozzle'));
    expect(within(tile).getByRole('button')).toBeDisabled();
  });
});
