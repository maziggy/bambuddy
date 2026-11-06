/**
 * The Manyfold tab of Model Sources (#1471): connecting an install, browsing
 * and searching its models, importing files, and slicing what was imported.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ManyfoldTab } from '../../components/ManyfoldTab';
import { setAuthToken } from '../../api/client';

const model = {
  id: 'cube01',
  name: 'Calibration Cube',
  caption: 'A 20 mm cube',
  description: 'Prints in 10 minutes',
  license: 'CC-BY-4.0',
  tags: ['test', 'cube'],
  url: 'http://manyfold.local/models/cube01',
  has_preview: true,
  files: [
    { id: 'f1', name: 'Cube', mime: 'model/stl', importable: true, library_file: null },
    { id: 'f2', name: 'Cube Plate', mime: 'model/3mf', importable: true, library_file: { id: 42, filename: 'cube plate.3mf', folder_id: 7 } },
    { id: 'f3', name: 'Notes', mime: 'application/pdf', importable: false, library_file: null },
    { id: 'f4', name: 'Cube Step', mime: 'model/step', importable: true, library_file: null },
  ],
};

function connected(opts: { models?: Array<{ id: string; name: string }>; total?: number; hasNext?: boolean; useSlicerApi?: boolean } = {}) {
  const models = opts.models ?? [
    { id: 'cube01', name: 'Calibration Cube' },
    { id: 'boat02', name: 'Benchy' },
  ];
  const listRequests: URLSearchParams[] = [];
  server.use(
    http.get('*/manyfold/status', () => HttpResponse.json({ configured: true, url: 'http://manyfold.local' })),
    http.get('*/manyfold/models', ({ request }) => {
      const params = new URL(request.url).searchParams;
      listRequests.push(params);
      const page = Number(params.get('page'));
      return HttpResponse.json({
        total: opts.total ?? models.length,
        page,
        has_next: opts.hasNext ?? false,
        has_previous: page > 1,
        models,
      });
    }),
    http.get('*/manyfold/models/:id/preview', () => new HttpResponse(null, { status: 404 })),
    http.get('*/manyfold/models/:id', ({ params }) =>
      HttpResponse.json(
        params.id === 'cube01'
          ? model
          : { ...model, id: params.id, name: models.find((m) => m.id === params.id)?.name ?? String(params.id) },
      ),
    ),
    http.get('*/library/folders', () => HttpResponse.json([])),
    http.get('*/settings/', () => HttpResponse.json({ use_slicer_api: opts.useSlicerApi ?? true, preferred_slicer: 'bambu_studio' })),
  );
  return { listRequests };
}

describe('ManyfoldTab', () => {
  beforeEach(() => {
    // jsdom has no object URLs; previews are blobs turned into one.
    URL.createObjectURL = vi.fn(() => 'blob:preview');
    URL.revokeObjectURL = vi.fn();
  });
  afterEach(() => {
    vi.restoreAllMocks();
    setAuthToken(null);
  });

  describe('connecting', () => {
    beforeEach(() => {
      server.use(
        http.get('*/manyfold/status', () => HttpResponse.json({ configured: false, url: '' })),
        http.get('*/manyfold/config', () =>
          HttpResponse.json({ url: '', client_id: '', has_client_secret: false, configured: false }),
        ),
      );
    });

    it('tests and saves the connection, then shows the models', async () => {
      const user = userEvent.setup();
      let saved: Record<string, unknown> | null = null;
      let tested: Record<string, unknown> | null = null;
      server.use(
        http.post('*/manyfold/config/test', async ({ request }) => {
          tested = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ model_count: 12 });
        }),
        http.put('*/manyfold/config', async ({ request }) => {
          saved = (await request.json()) as Record<string, unknown>;
          connected();
          return HttpResponse.json({ url: 'http://manyfold.local', client_id: 'app', has_client_secret: true, configured: true });
        }),
      );
      render(<ManyfoldTab />);

      const save = await screen.findByRole('button', { name: 'Save' });
      expect(save).toBeDisabled();
      await user.type(screen.getByLabelText('Manyfold URL'), 'http://manyfold.local');
      await user.type(screen.getByLabelText('Client ID'), 'app');
      await user.type(screen.getByLabelText('Client secret'), 's3cret');

      await user.click(screen.getByRole('button', { name: 'Test connection' }));
      expect(await screen.findByRole('status')).toHaveTextContent('Models Bambuddy can see: 12');
      expect(tested).toEqual({ url: 'http://manyfold.local', client_id: 'app', client_secret: 's3cret' });

      await user.click(save);
      await waitFor(() => expect(saved).toEqual({ url: 'http://manyfold.local', client_id: 'app', client_secret: 's3cret' }));
      expect(await screen.findByText('Settings saved')).toBeInTheDocument();
      expect(await screen.findByText('Benchy')).toBeInTheDocument();
    });

    it('shows its own text for a refused secret', async () => {
      const user = userEvent.setup();
      server.use(
        http.post('*/manyfold/config/test', () =>
          HttpResponse.json(
            { detail: { code: 'manyfold_credentials', message: 'Manyfold did not accept the client ID or secret.' } },
            { status: 502 },
          ),
        ),
      );
      render(<ManyfoldTab />);
      await user.type(await screen.findByLabelText('Manyfold URL'), 'http://manyfold.local');
      await user.type(screen.getByLabelText('Client ID'), 'app');
      await user.type(screen.getByLabelText('Client secret'), 'wrong');
      await user.click(screen.getByRole('button', { name: 'Test connection' }));
      expect(await screen.findByRole('status')).toHaveTextContent('Manyfold did not accept the client ID or secret.');
    });

    it('keeps a stored secret when the field is left empty', async () => {
      const user = userEvent.setup();
      let saved: Record<string, unknown> | null = null;
      server.use(
        http.get('*/manyfold/config', () =>
          HttpResponse.json({ url: 'http://manyfold.local', client_id: 'app', has_client_secret: true, configured: false }),
        ),
        http.put('*/manyfold/config', async ({ request }) => {
          saved = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ url: 'http://manyfold.local', client_id: 'app', has_client_secret: true, configured: true });
        }),
      );
      render(<ManyfoldTab />);
      const secret = await screen.findByPlaceholderText('Stored. Leave empty to keep it.');
      expect(secret).toHaveValue('');
      await waitFor(() => expect(screen.getByLabelText('Manyfold URL')).toHaveValue('http://manyfold.local'));
      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(saved).toEqual({ url: 'http://manyfold.local', client_id: 'app' }));
    });
  });

  describe('browsing', () => {
    it('lists models, searches, and pages', async () => {
      const user = userEvent.setup();
      const { listRequests } = connected({ total: 30, hasNext: true });
      render(<ManyfoldTab />);

      expect(await screen.findByText('Calibration Cube')).toBeInTheDocument();
      expect(screen.getByText('Models: 30')).toBeInTheDocument();

      await user.type(screen.getByRole('searchbox'), 'bench');
      await user.click(screen.getByRole('button', { name: 'Search' }));
      await waitFor(() => expect(listRequests.at(-1)?.get('q')).toBe('bench'));

      await user.click(screen.getByRole('button', { name: /Next/ }));
      await waitFor(() => expect(listRequests.at(-1)?.get('page')).toBe('2'));
      expect(listRequests.at(-1)?.get('q')).toBe('bench');
    });

    it('shows a placeholder for a model without a preview, without an <img>', async () => {
      connected();
      render(<ManyfoldTab />);
      await screen.findByText('Benchy');
      // A failing <img> on a protected URL would make the app renew its media token.
      await waitFor(() => expect(document.querySelectorAll('img')).toHaveLength(0));
    });

    it('shows a preview Manyfold has', async () => {
      connected();
      server.use(
        http.get('*/manyfold/models/:id/preview', () =>
          new HttpResponse(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), { headers: { 'Content-Type': 'image/png' } }),
        ),
      );
      render(<ManyfoldTab />);
      await waitFor(() => expect(document.querySelectorAll('img[src="blob:preview"]').length).toBeGreaterThan(0));
    });
  });

  describe('a model', () => {
    it('imports a file and offers library actions for imported ones', async () => {
      const user = userEvent.setup();
      connected();
      let imported: Record<string, unknown> | null = null;
      server.use(
        http.post('*/manyfold/import', async ({ request }) => {
          imported = (await request.json()) as Record<string, unknown>;
          return HttpResponse.json({ library_file_id: 43, filename: 'cube.stl', folder_id: 7, was_existing: false });
        }),
      );
      render(<ManyfoldTab />);
      await user.click(await screen.findByText('Calibration Cube'));

      expect(await screen.findByText('A 20 mm cube')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: /Open in Manyfold/ })).toHaveAttribute('href', model.url);

      const rows = screen.getAllByRole('listitem');
      const stl = rows.find((row) => within(row).queryByText('Cube'))!;
      const plate = rows.find((row) => within(row).queryByText('Cube Plate'))!;
      const notes = rows.find((row) => within(row).queryByText('Notes'))!;

      expect(within(plate).getByText('In library')).toBeInTheDocument();
      expect(within(plate).getByRole('button', { name: /Slice/ })).toBeInTheDocument();
      expect(within(notes).queryByRole('button')).toBeNull();
      expect(within(notes).getByText("Bambuddy can't slice or print this type")).toBeInTheDocument();

      await user.click(within(stl).getByRole('button', { name: /Import/ }));
      await waitFor(() => expect(imported).toEqual({ model_id: 'cube01', file_id: 'f1', folder_id: null }));
      expect(await screen.findByText('Imported cube.stl')).toBeInTheDocument();
    });

    it('hides importing from users without manyfold:import', async () => {
      // settings:update shows the Connection button, which proves the user's
      // permissions were loaded rather than everything being hidden.
      connected();
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
        http.get('*/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 2,
            username: 'viewer',
            role: 'user',
            is_active: true,
            is_admin: false,
            groups: [],
            permissions: ['manyfold:view', 'settings:update'],
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
      );
      setAuthToken('test-token', 'session');
      const user = userEvent.setup();
      render(<ManyfoldTab />);
      expect(await screen.findByRole('button', { name: /Connection/ })).toBeInTheDocument();
      await user.click(await screen.findByText('Calibration Cube'));
      await screen.findByText('A 20 mm cube');
      expect(screen.queryByRole('button', { name: /^Import$/ })).toBeNull();
      expect(screen.queryByText('Import to')).toBeNull();
    });
  });

  describe('importing several files', () => {
    /** Records each import, and how many ran at the same time. */
    function recordImports(fail: string[] = []) {
      const calls: Array<Record<string, unknown>> = [];
      let running = 0;
      let maxRunning = 0;
      server.use(
        http.post('*/manyfold/import', async ({ request }) => {
          const body = (await request.json()) as Record<string, unknown>;
          calls.push(body);
          running += 1;
          maxRunning = Math.max(maxRunning, running);
          await new Promise((resolve) => setTimeout(resolve, 20));
          running -= 1;
          if (fail.includes(String(body.file_id))) {
            return HttpResponse.json(
              { detail: { code: 'manyfold_unreachable', message: 'down' } },
              { status: 502 },
            );
          }
          return HttpResponse.json({ library_file_id: 50, filename: `${body.file_id}.stl`, folder_id: 7, was_existing: false });
        }),
      );
      return { calls, maxRunning: () => maxRunning };
    }

    it('imports only the ticked files', async () => {
      const user = userEvent.setup();
      connected();
      const rec = recordImports();
      render(<ManyfoldTab />);
      await user.click(await screen.findByText('Calibration Cube'));
      const importSelected = await screen.findByRole('button', { name: /Import selected \(0\)/ });
      expect(importSelected).toBeDisabled();
      // Imported and unprintable files can't be ticked.
      expect(screen.queryByRole('checkbox', { name: 'Select Cube Plate' })).toBeNull();
      expect(screen.queryByRole('checkbox', { name: 'Select Notes' })).toBeNull();

      await user.click(screen.getByRole('checkbox', { name: 'Select Cube Step' }));
      await user.click(screen.getByRole('button', { name: /Import selected \(1\)/ }));
      await waitFor(() => expect(rec.calls).toEqual([{ model_id: 'cube01', file_id: 'f4', folder_id: null }]));
      expect(await screen.findByText('Imported: 1 · already in library: 0 · failed: 0')).toBeInTheDocument();
    });

    it('Import all takes every importable file, one at a time, and carries on past a failure', async () => {
      const user = userEvent.setup();
      connected();
      const rec = recordImports(['f1']);
      render(<ManyfoldTab />);
      await user.click(await screen.findByText('Calibration Cube'));
      await user.click(await screen.findByRole('button', { name: 'Import all' }));

      await waitFor(() => expect(rec.calls.map((c) => c.file_id)).toEqual(['f1', 'f4']));
      expect(rec.maxRunning()).toBe(1);
      expect(await screen.findByText('Imported: 1 · already in library: 0 · failed: 1')).toBeInTheDocument();
      expect(
        screen.getByText("Not imported: Cube. Bambuddy can't reach Manyfold. Check the URL and that Manyfold is running."),
      ).toBeInTheDocument();
    });

    it('Select all ticks every importable file', async () => {
      const user = userEvent.setup();
      connected();
      render(<ManyfoldTab />);
      await user.click(await screen.findByText('Calibration Cube'));
      await user.click(await screen.findByRole('checkbox', { name: 'Select all' }));
      expect(screen.getByRole('button', { name: /Import selected \(2\)/ })).toBeEnabled();
      await user.click(screen.getByRole('checkbox', { name: 'Select all' }));
      expect(screen.getByRole('button', { name: /Import selected \(0\)/ })).toBeDisabled();
    });

    it('ticked models in the grid import all their files, and the ticks survive paging', async () => {
      const user = userEvent.setup();
      connected({ total: 30, hasNext: true });
      const rec = recordImports();
      render(<ManyfoldTab />);

      await user.click(await screen.findByRole('checkbox', { name: 'Select Calibration Cube' }));
      expect(screen.getByText('Models selected: 1')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: /Next/ }));
      await waitFor(() => expect(screen.getByText('Page 2')).toBeInTheDocument());
      expect(screen.getByText('Models selected: 1')).toBeInTheDocument();
      await user.click(screen.getByRole('checkbox', { name: 'Select Benchy' }));
      expect(screen.getByText('Models selected: 2')).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Import their files' }));
      await waitFor(() => expect(rec.calls).toHaveLength(4));
      expect(rec.calls.map((c) => `${c.model_id}/${c.file_id}`)).toEqual([
        'cube01/f1',
        'cube01/f4',
        'boat02/f1',
        'boat02/f4',
      ]);
      expect(rec.maxRunning()).toBe(1);
      expect(await screen.findByText('Imported: 4 · already in library: 0 · failed: 0')).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByText(/Models selected/)).toBeNull());
    });

    it('Select page ticks every model shown', async () => {
      const user = userEvent.setup();
      connected();
      render(<ManyfoldTab />);
      await user.click(await screen.findByRole('button', { name: 'Select page' }));
      expect(screen.getByText('Models selected: 2')).toBeInTheDocument();
      await user.click(screen.getByRole('button', { name: 'Clear selection' }));
      expect(screen.queryByText(/Models selected/)).toBeNull();
    });
  });
});
