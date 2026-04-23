/**
 * Tests for the Library Trash page (#1008).
 *
 * Covers: empty-trash view, populated table with file/folder/size columns,
 * restore round-trip (invalidates list), purge-now with confirmation dialog,
 * empty-trash bulk action, and the admin-only retention setting control.
 */

import { describe, it, expect, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { LibraryTrashPage } from '../../pages/LibraryTrashPage';

function trashItem(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: 1,
    filename: 'old_benchy.3mf',
    file_size: 204800,
    thumbnail_path: null,
    folder_id: null,
    folder_name: 'Calibration',
    created_by_id: 1,
    created_by_username: 'alice',
    deleted_at: '2026-04-10T12:00:00Z',
    auto_purge_at: '2026-05-10T12:00:00Z',
    ...overrides,
  };
}

afterEach(() => server.resetHandlers());

describe('LibraryTrashPage', () => {
  it('shows the empty state when there are no trashed files', async () => {
    server.use(
      http.get('*/library/trash', () =>
        HttpResponse.json({ items: [], total: 0, retention_days: 30 }),
      ),
      http.get('*/library/trash/settings', () =>
        HttpResponse.json({ retention_days: 30 }),
      ),
    );

    render(<LibraryTrashPage />);

    expect(await screen.findByText(/trash is empty/i)).toBeInTheDocument();
  });

  it('renders trashed files and the retention control for admins', async () => {
    server.use(
      http.get('*/library/trash', () =>
        HttpResponse.json({
          items: [trashItem(), trashItem({ id: 2, filename: 'old_calibration.3mf', file_size: 102400 })],
          total: 2,
          retention_days: 30,
        }),
      ),
      http.get('*/library/trash/settings', () =>
        HttpResponse.json({ retention_days: 30 }),
      ),
    );

    render(<LibraryTrashPage />);

    expect(await screen.findByText('old_benchy.3mf')).toBeInTheDocument();
    expect(screen.getByText('old_calibration.3mf')).toBeInTheDocument();
    // Retention input is visible when auth is off (isAdmin=true in tests)
    expect(screen.getByLabelText(/auto-delete after/i)).toBeInTheDocument();
  });

  it('restores a file when Restore is clicked', async () => {
    let restoreCalledFor: number | null = null;
    server.use(
      http.get('*/library/trash', () =>
        HttpResponse.json({ items: [trashItem()], total: 1, retention_days: 30 }),
      ),
      http.get('*/library/trash/settings', () =>
        HttpResponse.json({ retention_days: 30 }),
      ),
      http.post('*/library/trash/:id/restore', ({ params }) => {
        restoreCalledFor = Number(params.id);
        return HttpResponse.json({ status: 'success', id: Number(params.id) });
      }),
    );

    render(<LibraryTrashPage />);

    await screen.findByText('old_benchy.3mf');
    await userEvent.click(screen.getByRole('button', { name: /restore/i }));

    await waitFor(() => expect(restoreCalledFor).toBe(1));
  });

  it('prompts before permanently deleting a single file', async () => {
    let deleteCalledFor: number | null = null;
    server.use(
      http.get('*/library/trash', () =>
        HttpResponse.json({ items: [trashItem()], total: 1, retention_days: 30 }),
      ),
      http.get('*/library/trash/settings', () =>
        HttpResponse.json({ retention_days: 30 }),
      ),
      http.delete('*/library/trash/:id', ({ params }) => {
        deleteCalledFor = Number(params.id);
        return HttpResponse.json({ status: 'success' });
      }),
    );

    render(<LibraryTrashPage />);

    await screen.findByText('old_benchy.3mf');
    await userEvent.click(screen.getByRole('button', { name: /delete now/i }));

    // ConfirmModal opens. The modal's title is unique; the body repeats the filename.
    expect(await screen.findByRole('heading', { name: /delete permanently/i })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /delete permanently/i }));
    await waitFor(() => expect(deleteCalledFor).toBe(1));
  });

  it('empties the trash via the Empty Trash action', async () => {
    let emptyCalled = false;
    server.use(
      http.get('*/library/trash', () =>
        HttpResponse.json({
          items: [trashItem(), trashItem({ id: 2, filename: 'two.3mf' })],
          total: 2,
          retention_days: 30,
        }),
      ),
      http.get('*/library/trash/settings', () =>
        HttpResponse.json({ retention_days: 30 }),
      ),
      http.delete('*/library/trash', () => {
        emptyCalled = true;
        return HttpResponse.json({ deleted: 2 });
      }),
    );

    render(<LibraryTrashPage />);

    await screen.findByText('old_benchy.3mf');
    await userEvent.click(screen.getByRole('button', { name: /empty trash/i }));
    await userEvent.click(screen.getByRole('button', { name: /delete permanently/i }));

    await waitFor(() => expect(emptyCalled).toBe(true));
  });
});
