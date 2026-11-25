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

/** The reset dialog, opened by a task's Reset button. */
async function resetDialog(task: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: `Reset "${task}"?` });
  return heading.closest('div.fixed') as HTMLElement;
}

describe('MaintenancePage wiring: status tab', () => {
  it('Reset asks first, then sends performMaintenance with the comment for that printer\'s item only', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await user.click(await within(beta).findByRole('button', { name: 'Reset' }));

    const dialog = await resetDialog('Nozzle Task');
    expect(within(dialog).getByText(/on Beta Printer/)).toBeInTheDocument();
    expect(writes()).toEqual([]);
    await user.type(within(dialog).getByLabelText('Comment (optional)'), '  Nozzle swapped to 0.6  ');
    await user.click(within(dialog).getByRole('button', { name: 'Reset' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({
      method: 'POST',
      path: '/api/v1/maintenance/items/21/perform',
      body: { notes: 'Nozzle swapped to 0.6' },
    });
    expect(await screen.findByText('Maintenance marked as complete')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Reset "Nozzle Task"?' })).not.toBeInTheDocument());
  });

  it('a reset without a comment sends no notes', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const alpha = await printerCard('Alpha Printer');
    await user.click(within(alpha).getByRole('button', { name: /expand/i }));
    await user.click(await within(alpha).findByRole('button', { name: 'Reset' }));
    await user.click(within(await resetDialog('Nozzle Task')).getByRole('button', { name: 'Reset' }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toEqual({ method: 'POST', path: '/api/v1/maintenance/items/11/perform', search: '', body: {} });
  });

  it('Escape keeps the reset dialog open while a comment is typed, and closes it when empty', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await user.click(await within(beta).findByRole('button', { name: 'Reset' }));
    const dialog = await resetDialog('Nozzle Task');
    const comment = within(dialog).getByLabelText('Comment (optional)');
    await user.type(comment, 'Half-written note');
    await user.keyboard('{Escape}');

    expect(screen.getByRole('heading', { name: 'Reset "Nozzle Task"?' })).toBeInTheDocument();
    expect(comment).toHaveValue('Half-written note');

    await user.clear(comment);
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Reset "Nozzle Task"?' })).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('a click beside the reset dialog keeps it open while a comment is typed, and closes it when empty', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await user.click(await within(beta).findByRole('button', { name: 'Reset' }));
    const dialog = await resetDialog('Nozzle Task');
    const comment = within(dialog).getByLabelText('Comment (optional)');
    const backdrop = screen.getByRole('heading', { name: 'Reset "Nozzle Task"?' }).closest('.fixed') as HTMLElement;
    await user.type(comment, 'Half-written note');
    await user.click(backdrop);

    expect(screen.getByRole('heading', { name: 'Reset "Nozzle Task"?' })).toBeInTheDocument();
    expect(comment).toHaveValue('Half-written note');

    await user.clear(comment);
    await user.click(backdrop);
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Reset "Nozzle Task"?' })).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
  });

  it('cancelling the reset dialog sends nothing', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: /expand/i }));
    await user.click(await within(beta).findByRole('button', { name: 'Reset' }));
    await user.click(within(await resetDialog('Nozzle Task')).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Reset "Nozzle Task"?' })).not.toBeInTheDocument());
    expect(writes()).toEqual([]);
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

describe('MaintenancePage wiring: maintenance log', () => {
  const LOG_ENTRY = {
    id: 5,
    printer_id: 2,
    printer_name: 'Beta Printer',
    maintenance_type_id: 1,
    maintenance_type_name: 'Clean Nozzle',
    hours_at_maintenance: 248.04,
    notes: 'Swapped to hardened 0.4',
    performed_by_id: 9,
    performed_by_username: 'mechanic',
    performed_at: '2026-11-01T10:30:00',
  };

  function stubDownloads() {
    const createObjectURL = vi.fn(() => 'blob:maintenance-log');
    // A subclass, not a copy: requests still build URLs with `new URL()`.
    class DownloadURL extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = vi.fn();
    }
    vi.stubGlobal('URL', DownloadURL);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    return createObjectURL;
  }

  beforeEach(() => {
    server.use(
      http.get('*/api/v1/maintenance/printers/:id/log', async ({ request, params }) => {
        await record(request);
        const items = params.id === '2' ? [LOG_ENTRY] : [];
        return HttpResponse.json({ items, total: items.length });
      }),
      http.get('*/api/v1/maintenance/printers/:id/log/export', async ({ request }) => {
        await record(request);
        return new HttpResponse('csv', {
          headers: { 'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="maintenance_log_Beta.csv"' },
        });
      }),
      http.get('*/api/v1/maintenance/log/export', async ({ request }) => {
        await record(request);
        return new HttpResponse('csv', {
          headers: { 'Content-Type': 'text/csv', 'Content-Disposition': 'attachment; filename="maintenance_log_all.csv"' },
        });
      }),
      http.delete('*/api/v1/maintenance/log/:entryId', async ({ request, params }) => {
        await record(request);
        return HttpResponse.json({ status: 'deleted', id: Number(params.entryId) });
      }),
      http.delete('*/api/v1/maintenance/log', async ({ request }) => {
        await record(request);
        return HttpResponse.json({ deleted: 4 });
      }),
      http.delete('*/api/v1/maintenance/printers/:id/log', async ({ request }) => {
        await record(request);
        return HttpResponse.json({ deleted: 1 });
      }),
    );
  });

  async function openBetaLog(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
    await user.click(within(await printerCard('Beta Printer')).getByRole('button', { name: 'Log' }));
    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Beta Printer' });
    await within(dialog).findByText('Clean Nozzle');
    return dialog;
  }

  /** A confirmation on top of the page, found by its heading. */
  async function confirmation(title: string): Promise<HTMLElement> {
    return (await screen.findByRole('heading', { name: title })).closest('div.fixed') as HTMLElement;
  }

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('a printer\'s Log lists its resets with task, user, hours and comment', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    const beta = await printerCard('Beta Printer');
    await user.click(within(beta).getByRole('button', { name: 'Log' }));

    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Beta Printer' });
    expect(await within(dialog).findByText('Clean Nozzle')).toBeInTheDocument();
    expect(within(dialog).getByText('by mechanic')).toBeInTheDocument();
    expect(within(dialog).getByText('at 248.0 h')).toBeInTheDocument();
    expect(within(dialog).getByText('Swapped to hardened 0.4')).toBeInTheDocument();
    expect(within(dialog).getByText('Showing 1 of 1')).toBeInTheDocument();
    expect(sent.some((s) => s.path === '/api/v1/maintenance/printers/2/log')).toBe(true);
  });

  it('a printer without resets shows the empty log and cannot export', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(within(await printerCard('Alpha Printer')).getByRole('button', { name: 'Log' }));

    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Alpha Printer' });
    expect(await within(dialog).findByText('No maintenance has been logged for this printer yet.')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Export CSV' })).toBeDisabled();
  });

  it('Load more fetches the next page by offset, each row once', async () => {
    const all = Array.from({ length: 75 }, (_, n) => ({ ...LOG_ENTRY, id: 1000 - n, notes: `reset ${n}` }));
    server.use(
      http.get('*/api/v1/maintenance/printers/:id/log', async ({ request }) => {
        await record(request);
        const url = new URL(request.url);
        const limit = Number(url.searchParams.get('limit'));
        const offset = Number(url.searchParams.get('offset'));
        return HttpResponse.json({ items: all.slice(offset, offset + limit), total: all.length });
      }),
    );
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(within(await printerCard('Beta Printer')).getByRole('button', { name: 'Log' }));
    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Beta Printer' });
    expect(await within(dialog).findByText('Showing 50 of 75')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Load more' }));

    expect(await within(dialog).findByText('Showing 75 of 75')).toBeInTheDocument();
    expect(within(dialog).getByText('reset 74')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Load more' })).not.toBeInTheDocument();
    const logReads = sent.filter((s) => s.path === '/api/v1/maintenance/printers/2/log').map((s) => s.search);
    expect(logReads).toEqual(['?limit=50&offset=0', '?limit=50&offset=50']);
  });

  it('a log that fails to load says so, offers a retry and still allows the export', async () => {
    server.use(
      http.get('*/api/v1/maintenance/printers/:id/log', () =>
        HttpResponse.json({ detail: 'Database is locked' }, { status: 500 }),
      ),
    );
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(within(await printerCard('Beta Printer')).getByRole('button', { name: 'Log' }));
    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Beta Printer' });

    expect(
      await within(dialog).findByText('Could not load the maintenance log: Database is locked', {}, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText('No maintenance has been logged for this printer yet.')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Export CSV' })).not.toBeDisabled();
  });

  it('Export CSV downloads that printer\'s log', async () => {
    const createObjectURL = stubDownloads();
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(within(await printerCard('Beta Printer')).getByRole('button', { name: 'Log' }));
    const dialog = await screen.findByRole('dialog', { name: 'Maintenance log: Beta Printer' });
    await within(dialog).findByText('Clean Nozzle');
    await user.click(within(dialog).getByRole('button', { name: 'Export CSV' }));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(sent.some((s) => s.path === '/api/v1/maintenance/printers/2/log/export')).toBe(true);
  });

  it('Export log in the header downloads the whole fleet\'s log', async () => {
    const createObjectURL = stubDownloads();
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(await screen.findByRole('button', { name: 'Export log' }));

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(sent.some((s) => s.path === '/api/v1/maintenance/log/export')).toBe(true);
  });

  it('deleting an entry asks first, then deletes that entry and reloads the log', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    const dialog = await openBetaLog(user);

    await user.click(within(dialog).getByRole('button', { name: 'Delete entry' }));
    const confirm = await confirmation('Delete this log entry?');
    expect(within(confirm).getByText(/"Clean Nozzle" from .* is permanently removed/)).toBeInTheDocument();
    expect(writes()).toEqual([]);
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(writes()).toEqual([{ method: 'DELETE', path: '/api/v1/maintenance/log/5', search: '', body: null }]),
    );
    expect(await screen.findByText('Log entry deleted')).toBeInTheDocument();
    await waitFor(() =>
      expect(sent.filter((s) => s.method === 'GET' && s.path === '/api/v1/maintenance/printers/2/log')).toHaveLength(2),
    );
    expect(screen.queryByRole('heading', { name: 'Delete this log entry?' })).not.toBeInTheDocument();
  });

  it('Escape cancels the entry delete and leaves the log open', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    const dialog = await openBetaLog(user);

    await user.click(within(dialog).getByRole('button', { name: 'Delete entry' }));
    await confirmation('Delete this log entry?');
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete this log entry?' })).not.toBeInTheDocument());
    expect(screen.getByRole('dialog', { name: 'Maintenance log: Beta Printer' })).toBeInTheDocument();
    expect(writes()).toEqual([]);
  });

  it('Clean up in a printer\'s log deletes its entries older than the chosen days', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    const dialog = await openBetaLog(user);

    await user.click(within(dialog).getByRole('button', { name: 'Clean up' }));
    const confirm = await confirmation('Delete old log entries?');
    expect(within(confirm).getByText(/maintenance log of Beta Printer/)).toBeInTheDocument();
    const days = within(confirm).getByLabelText('Delete entries older than (days)');
    expect(days).toHaveValue(365);
    await user.clear(days);
    await user.type(days, '90');
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(writes()).toEqual([
        { method: 'DELETE', path: '/api/v1/maintenance/printers/2/log', search: '?older_than_days=90', body: null },
      ]),
    );
    expect(await screen.findByText('Log entries deleted: 1')).toBeInTheDocument();
  });

  it('the cleanup cannot be confirmed without an age of at least one day', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);
    const dialog = await openBetaLog(user);

    await user.click(within(dialog).getByRole('button', { name: 'Clean up' }));
    const confirm = await confirmation('Delete old log entries?');
    const days = within(confirm).getByLabelText('Delete entries older than (days)');
    const deleteButton = within(confirm).getByRole('button', { name: 'Delete' });

    await user.clear(days);
    expect(deleteButton).toBeDisabled();
    await user.type(days, '0');
    expect(deleteButton).toBeDisabled();
    await user.clear(days);
    await user.type(days, '1.5');
    expect(deleteButton).toBeDisabled();
    expect(writes()).toEqual([]);
  });

  it('Clean up log in the header cleans up the whole fleet', async () => {
    const user = userEvent.setup();
    render(<MaintenancePage />);

    await user.click(await screen.findByRole('button', { name: 'Clean up log' }));
    const confirm = await confirmation('Delete old log entries?');
    expect(within(confirm).getByText(/for all printers you can see/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole('button', { name: 'Delete' }));

    await waitFor(() =>
      expect(writes()).toEqual([
        { method: 'DELETE', path: '/api/v1/maintenance/log', search: '?older_than_days=365', body: null },
      ]),
    );
    expect(await screen.findByText('Log entries deleted: 4')).toBeInTheDocument();
  });

  it('without maintenance:delete there is no cleanup and no entry delete', async () => {
    signInWith(['maintenance:read']);
    const user = userEvent.setup();
    render(<MaintenancePage />);

    expect(await screen.findByRole('button', { name: 'Export log' })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Clean up log' })).not.toBeInTheDocument());
    const dialog = await openBetaLog(user);
    expect(within(dialog).queryByRole('button', { name: 'Clean up' })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: 'Delete entry' })).not.toBeInTheDocument();
  });
});

describe('MaintenancePage wiring: permissions', () => {
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
