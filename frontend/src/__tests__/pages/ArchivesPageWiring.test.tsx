/**
 * Wiring tests for ArchivesPage: pin the exact mutation requests behind
 * archive delete (card, list row, bulk), file removals (timelapse, source
 * 3MF, F3D), project assignment, bulk favourite and the print-log
 * edit / delete / clear actions -- and that cancelling each destructive
 * confirm sends nothing.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { setAuthToken } from '../../api/client';

const archive = (over: Record<string, unknown>) => ({
  id: 1,
  filename: 'a.gcode.3mf',
  print_name: 'Archive',
  printer_id: 1,
  printer_name: 'X1 Carbon',
  print_time_seconds: 3600,
  filament_used_grams: 10,
  status: 'completed',
  started_at: '2024-01-01T10:00:00Z',
  completed_at: '2024-01-01T11:00:00Z',
  thumbnail_path: null,
  notes: null,
  rating: null,
  project_id: null,
  project_name: null,
  project_color: null,
  print_count: 1,
  tags: '',
  created_at: '2024-01-01T09:00:00Z',
  updated_at: '2024-01-01T11:00:00Z',
  has_f3d: false,
  is_favorite: false,
  ...over,
});

const ARCHIVES = [
  archive({ id: 1, print_name: 'Benchy', created_at: '2024-01-03T09:00:00Z' }),
  archive({
    id: 2,
    print_name: 'Bracket',
    created_at: '2024-01-02T09:00:00Z',
    timelapse_path: 'timelapse/2.mp4',
    source_3mf_path: 'source/2.3mf',
    f3d_path: 'f3d/2.f3d',
  }),
  archive({ id: 3, print_name: 'Clip', created_at: '2024-01-01T09:00:00Z' }),
];

const LOG_ENTRIES = [
  {
    id: 501,
    archive_id: 1,
    print_name: 'Logged Print',
    printer_name: 'X1 Carbon',
    printer_id: 1,
    status: 'completed',
    started_at: '2024-01-01T10:00:00Z',
    completed_at: '2024-01-01T11:00:00Z',
    duration_seconds: 3600,
    filament_type: 'PLA',
    filament_color: '#000000',
    filament_used_grams: 10,
    cost: 0.4,
    energy_kwh: null,
    energy_cost: null,
    failure_reason: null,
    thumbnail_path: null,
    created_by_id: null,
    created_by_username: null,
    created_at: '2024-01-01T10:00:00Z',
  },
];

interface Captured {
  method: string;
  path: string;
  search: string;
  body?: unknown;
}

let calls: Captured[];
let viewMode: string | null;

async function capture(request: Request) {
  const url = new URL(request.url);
  let body: unknown;
  if (request.method === 'PATCH' || request.method === 'POST' || request.method === 'PUT') {
    const text = await request.text();
    body = text ? JSON.parse(text) : undefined;
  }
  calls.push({ method: request.method, path: url.pathname, search: url.search, body });
}

const mutations = () => calls.filter((c) => c.method !== 'GET');

beforeEach(() => {
  calls = [];
  viewMode = null;
  setAuthToken(null);
  vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
    key === 'archiveViewMode' ? viewMode : null,
  );
  server.use(
    http.get('*/api/v1/archives/', () => HttpResponse.json(ARCHIVES)),
    http.get('*/api/v1/archives/stats', () =>
      HttpResponse.json({
        total_archives: 3,
        total_print_time_seconds: 0,
        total_filament_grams: 0,
        prints_this_week: 0,
        prints_this_month: 0,
      }),
    ),
    http.get('*/api/v1/archives/tags', () => HttpResponse.json([])),
    http.get('*/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X1 Carbon' }])),
    http.get('*/api/v1/projects/', () =>
      HttpResponse.json([
        { id: 4, name: 'Gizmos', color: '#ff0000', status: 'active' },
        { id: 8, name: 'Shelved', color: '#00ff00', status: 'archived' },
      ]),
    ),
    http.get('*/api/v1/archives/:id/delete-impact', () =>
      HttpResponse.json({ related_queue_items: 0, currently_printing: 0 }),
    ),
    http.get('*/api/v1/print-log/', () =>
      HttpResponse.json({ items: LOG_ENTRIES, total: LOG_ENTRIES.length }),
    ),
    http.delete('*/api/v1/archives/:id', async ({ request }) => {
      await capture(request);
      return new HttpResponse(null, { status: 204 });
    }),
    http.delete('*/api/v1/archives/:id/timelapse', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ status: 'ok' });
    }),
    http.delete('*/api/v1/archives/:id/source', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ status: 'ok' });
    }),
    http.delete('*/api/v1/archives/:id/f3d', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ status: 'ok' });
    }),
    http.patch('*/api/v1/archives/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(ARCHIVES[0]);
    }),
    http.post('*/api/v1/archives/:id/favorite', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(ARCHIVES[0]);
    }),
    http.delete('*/api/v1/print-log/', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ deleted: 1 });
    }),
    http.delete('*/api/v1/print-log/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ status: 'ok', id: 501 });
    }),
    http.patch('*/api/v1/print-log/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(LOG_ENTRIES[0]);
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
});

async function cardFor(id: number): Promise<HTMLElement> {
  await screen.findByText('Benchy');
  const el = document.querySelector<HTMLElement>(`[data-archive-id="${id}"]`);
  expect(el).not.toBeNull();
  return el!;
}

/** The fixed overlay of the ConfirmModal whose title is ``title``. */
async function confirmDialog(title: string): Promise<HTMLElement> {
  const heading = await screen.findByRole('heading', { name: title });
  return heading.closest('div.fixed') as HTMLElement;
}

async function openMenu(id: number) {
  fireEvent.contextMenu(await cardFor(id));
}

describe('ArchivesPage wiring: single archive delete', () => {
  it('card Delete -> confirm sends DELETE /archives/{id} for that archive only', async () => {
    const user = userEvent.setup();
    render(<ArchivesPage />);
    const card = await cardFor(2);

    await user.click(within(card).getByTitle('Delete'));
    const dialog = await confirmDialog('Delete Archive');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/archives/2', search: '' });
  });

  it('card Delete with "also remove from Quick Stats" sends purge_stats=true', async () => {
    const user = userEvent.setup();
    render(<ArchivesPage />);
    const card = await cardFor(3);

    await user.click(within(card).getByTitle('Delete'));
    const dialog = await confirmDialog('Delete Archive');
    await user.click(within(dialog).getByRole('checkbox'));
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({
      method: 'DELETE',
      path: '/api/v1/archives/3',
      search: '?purge_stats=true',
    });
  });

  it('card Delete -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<ArchivesPage />);
    const card = await cardFor(2);

    await user.click(within(card).getByTitle('Delete'));
    const dialog = await confirmDialog('Delete Archive');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete Archive' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });

  it('list row Delete -> confirm sends DELETE for that row', async () => {
    viewMode = 'list';
    const user = userEvent.setup();
    render(<ArchivesPage />);
    const row = await cardFor(3);

    await user.click(within(row).getByTitle('Delete'));
    const dialog = await confirmDialog('Delete Archive');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/archives/3' });
  });

  it('list row Delete -> cancel sends nothing', async () => {
    viewMode = 'list';
    const user = userEvent.setup();
    render(<ArchivesPage />);
    const row = await cardFor(3);

    await user.click(within(row).getByTitle('Delete'));
    const dialog = await confirmDialog('Delete Archive');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete Archive' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });
});

describe('ArchivesPage wiring: file removals from the context menu', () => {
  const cases: Array<[string, string, string]> = [
    ['Remove Timelapse', 'Remove Timelapse', '/api/v1/archives/2/timelapse'],
    ['Remove Source 3MF', 'Remove Source 3MF', '/api/v1/archives/2/source'],
    ['Remove F3D', 'Remove F3D', '/api/v1/archives/2/f3d'],
  ];

  for (const [menuLabel, dialogTitle, path] of cases) {
    it(`${menuLabel} -> confirm sends DELETE ${path}`, async () => {
      const user = userEvent.setup();
      render(<ArchivesPage />);
      await openMenu(2);

      await user.click(await screen.findByRole('button', { name: menuLabel }));
      const dialog = await confirmDialog(dialogTitle);
      await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

      await waitFor(() => expect(mutations()).toHaveLength(1));
      expect(mutations()[0]).toMatchObject({ method: 'DELETE', path });
    });

    it(`${menuLabel} -> cancel sends nothing`, async () => {
      const user = userEvent.setup();
      render(<ArchivesPage />);
      await openMenu(2);

      await user.click(await screen.findByRole('button', { name: menuLabel }));
      const dialog = await confirmDialog(dialogTitle);
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      await waitFor(() => expect(screen.queryByRole('heading', { name: dialogTitle })).toBeNull());
      expect(mutations()).toHaveLength(0);
    });
  }

  it('list row: Remove Timelapse -> confirm sends DELETE for that row', async () => {
    viewMode = 'list';
    const user = userEvent.setup();
    render(<ArchivesPage />);
    await openMenu(2);

    await user.click(await screen.findByRole('button', { name: 'Remove Timelapse' }));
    const dialog = await confirmDialog('Remove Timelapse');
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/archives/2/timelapse' });
  });
});

describe('ArchivesPage wiring: project and favourite', () => {
  it('Add to Project -> project sends PATCH /archives/{id} with project_id', async () => {
    const user = userEvent.setup();
    render(<ArchivesPage />);
    await openMenu(3);

    // Click (not hover) toggles the submenu open.
    fireEvent.click(await screen.findByRole('button', { name: 'Add to Project' }));
    const gizmos = await screen.findByRole('button', { name: 'Gizmos' });
    // Archived projects are not offered.
    expect(screen.queryByRole('button', { name: 'Shelved' })).toBeNull();
    await user.click(gizmos);

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'PATCH',
      path: '/api/v1/archives/3',
      search: '',
      body: { project_id: 4 },
    });
  });

  it('Remove from Project sends PATCH with project_id null', async () => {
    server.use(
      http.get('*/api/v1/archives/', () =>
        HttpResponse.json([
          ARCHIVES[0],
          archive({ id: 9, print_name: 'Filed', project_id: 4, project_name: 'Gizmos' }),
        ]),
      ),
    );
    const user = userEvent.setup();
    render(<ArchivesPage />);
    await openMenu(9);

    fireEvent.click(await screen.findByRole('button', { name: 'Add to Project' }));
    await user.click(await screen.findByRole('button', { name: 'Remove from Project' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'PATCH', path: '/api/v1/archives/9', body: { project_id: null } });
  });

  it('context menu Add to Favorites posts /archives/{id}/favorite', async () => {
    const user = userEvent.setup();
    render(<ArchivesPage />);
    await openMenu(1);

    await user.click(await screen.findByRole('button', { name: 'Add to Favorites' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'POST', path: '/api/v1/archives/1/favorite' });
  });
});

describe('ArchivesPage wiring: bulk selection', () => {
  /** Select archives 1 and 3 via the context menu + selection-mode clicks. */
  async function selectOneAndThree(user: ReturnType<typeof userEvent.setup>) {
    render(<ArchivesPage />);
    await openMenu(1);
    const menu = (await screen.findByRole('button', { name: 'Add to Project' })).closest(
      'div.fixed',
    ) as HTMLElement;
    await user.click(within(menu).getByRole('button', { name: 'Select' }));
    await screen.findByText('1 selected');
    fireEvent.click(await cardFor(3));
    return (await screen.findByText('2 selected')).closest('div.fixed') as HTMLElement;
  }

  it('bulk Delete -> confirm sends DELETE for exactly the selected ids', async () => {
    const user = userEvent.setup();
    const toolbar = await selectOneAndThree(user);

    await user.click(within(toolbar).getByRole('button', { name: 'Delete' }));
    const dialog = await confirmDialog('Delete Archives');
    await user.click(within(dialog).getByRole('button', { name: 'Delete 2' }));

    await waitFor(() => expect(mutations()).toHaveLength(2));
    expect(mutations().map((c) => `${c.method} ${c.path}`).sort()).toEqual([
      'DELETE /api/v1/archives/1',
      'DELETE /api/v1/archives/3',
    ]);
  });

  it('bulk Delete -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    const toolbar = await selectOneAndThree(user);

    await user.click(within(toolbar).getByRole('button', { name: 'Delete' }));
    const dialog = await confirmDialog('Delete Archives');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete Archives' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });

  it('bulk Favorite toggles each selected id once', async () => {
    const user = userEvent.setup();
    const toolbar = await selectOneAndThree(user);

    await user.click(within(toolbar).getByRole('button', { name: 'Favorite' }));

    await waitFor(() => expect(mutations()).toHaveLength(2));
    expect(mutations().map((c) => `${c.method} ${c.path}`).sort()).toEqual([
      'POST /api/v1/archives/1/favorite',
      'POST /api/v1/archives/3/favorite',
    ]);
  });
});

describe('ArchivesPage wiring: print log', () => {
  async function openLog() {
    render(<ArchivesPage />);
    fireEvent.click(await screen.findByTitle('Print Log'));
    await screen.findByText('Logged Print');
  }

  it('Clear Log -> confirm sends DELETE /print-log/', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByRole('button', { name: 'Clear Log' }));
    const dialog = await confirmDialog('Clear Print Log');
    await user.click(within(dialog).getByRole('button', { name: 'Clear All' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/print-log/' });
  });

  it('Clear Log -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByRole('button', { name: 'Clear Log' }));
    const dialog = await confirmDialog('Clear Print Log');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Clear Print Log' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });

  it('row delete -> confirm sends DELETE /print-log/{id}', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByTitle('Delete print log entry'));
    const dialog = await confirmDialog('Delete print log entry');
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/print-log/501' });
  });

  it('row delete -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByTitle('Delete print log entry'));
    const dialog = await confirmDialog('Delete print log entry');
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Delete print log entry' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });

  it('row edit sends PATCH /print-log/{id} with only the changed fields', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByTitle('Edit print log entry'));
    const heading = await screen.findByRole('heading', { name: 'Edit print log entry' });
    const modal = heading.parentElement as HTMLElement;
    const [statusSelect] = within(modal).getAllByRole('combobox');
    await user.selectOptions(statusSelect, 'failed');
    await user.click(within(modal).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mutations()).toHaveLength(1));
    expect(mutations()[0]).toEqual({
      method: 'PATCH',
      path: '/api/v1/print-log/501',
      search: '',
      body: { status: 'failed' },
    });
  });

  it('row edit with nothing changed sends nothing', async () => {
    const user = userEvent.setup();
    await openLog();

    await user.click(screen.getByTitle('Edit print log entry'));
    const heading = await screen.findByRole('heading', { name: 'Edit print log entry' });
    await user.click(within(heading.parentElement as HTMLElement).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Edit print log entry' })).toBeNull());
    expect(mutations()).toHaveLength(0);
  });
});

describe('ArchivesPage wiring: reprint opens on the archive plate (#3395)', () => {
  const plate = (index: number) => ({
    index, name: '', has_thumbnail: false, thumbnail_url: null, objects: [`Part ${index}`],
    filaments: [], print_time_seconds: 600, filament_used_grams: 5,
  });

  beforeEach(() => {
    server.use(
      http.get('*/api/v1/archives/', () =>
        HttpResponse.json(ARCHIVES.map((a) => ({ ...a, file_path: `archive/${a.id}.gcode.3mf`, plate_id: a.id === 2 ? 3 : null }))),
      ),
      http.get('*/api/v1/archives/:id/plates', () =>
        HttpResponse.json({ is_multi_plate: true, plates: [plate(1), plate(2), plate(3)] }),
      ),
      http.get('*/api/v1/archives/:id/filament-requirements', () => HttpResponse.json({ filaments: [] })),
    );
  });

  it.each([
    ['card', null, (el: HTMLElement) => within(el).getByRole('button', { name: /Print/ })],
    ['list row', 'list', (el: HTMLElement) => within(el).getByTitle('Print')],
  ])('%s Print preselects the plate the archive printed', async (_where, mode, button) => {
    viewMode = mode;
    const user = userEvent.setup();
    render(<ArchivesPage />);

    await user.click(button(await cardFor(2)));

    await waitFor(() => {
      expect(screen.getByTestId('selected-plates-summary')).toHaveTextContent(/^Plate 3$/);
    });
  });
});
