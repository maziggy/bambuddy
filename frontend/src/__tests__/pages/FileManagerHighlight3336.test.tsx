/**
 * /files?highlight=<id> (#3336): the queue card's "View in File Manager" link.
 * The link carries only the file id; the page looks up the file's folder,
 * opens it and marks the file. It used to point at /library, which is no route.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { server } from '../mocks/server';

function libraryFile(id: number, filename: string, folderId: number | null) {
  return {
    id,
    filename,
    file_type: '3mf',
    file_path: `/library/${filename}`,
    file_size: 4096,
    folder_id: folderId,
    thumbnail_path: null,
    print_name: null,
    print_time_seconds: null,
    print_count: 0,
    duplicate_count: 0,
    created_at: '2026-01-01T00:00:00Z',
  };
}

const rootFile = libraryFile(1, 'at-the-root.3mf', null);
const target = libraryFile(5, 'the-queued-one.3mf', 3);
const sibling = libraryFile(6, 'its-neighbour.3mf', 3);

let listedFolders: (string | null)[] = [];
// Folder 3's listing without the file, as cached before it was moved there.
let folder3BeforeTheMove = false;

/** Follows a link inside the running app, as the queue page's does. */
function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>go</button>;
}

function setViewMode(mode: 'grid' | 'list' | 'columns') {
  (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
    key === 'library-view-mode' ? mode : null,
  );
}

function card(name: string): HTMLElement {
  return screen.getByText(name).closest('[data-file-id]') as HTMLElement;
}

describe('FileManagerPage ?highlight=', () => {
  beforeEach(() => {
    listedFolders = [];
    folder3BeforeTheMove = false;
    setViewMode('grid');
    server.use(
      http.get('/api/v1/library/folders', () =>
        HttpResponse.json([
          { id: 3, name: 'Brackets', parent_id: null, project_id: null, is_external: false, file_count: 2, children: [] },
          { id: 4, name: 'Elsewhere', parent_id: null, project_id: null, is_external: false, file_count: 0, children: [] },
        ]),
      ),
      http.get('/api/v1/library/files', async ({ request }) => {
        const folderId = new URL(request.url).searchParams.get('folder_id');
        listedFolders.push(folderId);
        if (folderId === '4') return HttpResponse.json([]);
        if (folderId === '3' && folder3BeforeTheMove) return HttpResponse.json([sibling]);
        // Slower than the page's own timers, so nothing can be decided on a list
        // that is still on its way.
        if (folderId === '3') {
          await new Promise((resolve) => setTimeout(resolve, 300));
          return HttpResponse.json([target, sibling]);
        }
        return HttpResponse.json([rootFile]);
      }),
      http.get('/api/v1/library/files/5', () => HttpResponse.json(target)),
      // Readable on its own but not in its folder's listing.
      http.get('/api/v1/library/files/7', () => HttpResponse.json(libraryFile(7, 'unlisted.3mf', 3))),
      http.get('/api/v1/library/files/404', () => HttpResponse.json({ detail: 'File not found' }, { status: 404 })),
      http.get('/api/v1/library/stats', () =>
        HttpResponse.json({
          total_files: 3,
          total_folders: 1,
          total_size_bytes: 1024,
          disk_free_bytes: 1024 * 1024,
          disk_total_bytes: 2048 * 1024,
        }),
      ),
      http.get('/api/v1/settings/', () => HttpResponse.json({ check_updates: false })),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/', () => HttpResponse.json([])),
    );
  });

  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  it("opens the file's folder and marks the file", async () => {
    window.history.pushState({}, '', '/files?highlight=5');
    render(<FileManagerPage />);

    await screen.findByText('the-queued-one.3mf');
    expect(listedFolders).toContain('3');
    await waitFor(() => expect(card('the-queued-one.3mf').style.outline).toContain('solid'));
    expect(card('its-neighbour.3mf').style.outline).toBe('');
  });

  it('marks the row in the list view', async () => {
    setViewMode('list');
    window.history.pushState({}, '', '/files?highlight=5');
    render(<FileManagerPage />);

    await screen.findByText('the-queued-one.3mf');
    await waitFor(() => expect(card('the-queued-one.3mf').style.outline).toContain('solid'));
  });

  it('focuses the file in the columns view', async () => {
    setViewMode('columns');
    window.history.pushState({}, '', '/files?highlight=5');
    render(<FileManagerPage />);

    await waitFor(() => expect(screen.getAllByText('the-queued-one.3mf').length).toBeGreaterThan(0));
    await waitFor(() => {
      const row = screen.getAllByText('the-queued-one.3mf')[0].closest('[data-file-id]') as HTMLElement;
      expect(row.style.outline).toContain('solid');
      expect(row.className).toContain('ring-1');
    });
  });

  it('warns when the file is gone or not readable', async () => {
    window.history.pushState({}, '', '/files?highlight=404');
    render(<FileManagerPage />);

    expect(await screen.findByText(/File not found - it may have been deleted/)).toBeInTheDocument();
    expect(await screen.findByText('at-the-root.3mf')).toBeInTheDocument();
  });

  it("warns when the file is not in its folder's listing", async () => {
    window.history.pushState({}, '', '/files?highlight=7');
    render(<FileManagerPage />);

    expect(await screen.findByText(/File not found - it may have been deleted/)).toBeInTheDocument();
    expect(listedFolders).toContain('3');
  });

  it('looks for the file in a fresh listing, not one cached before it was moved in', async () => {
    folder3BeforeTheMove = true;
    window.history.pushState({}, '', '/files?folder=3');
    render(<><FileManagerPage /><GoTo to="/files?highlight=5" /></>);
    await screen.findByText('its-neighbour.3mf');

    folder3BeforeTheMove = false;
    fireEvent.click(screen.getByText('go'));

    await waitFor(() => expect(card('the-queued-one.3mf').style.outline).toContain('solid'));
    expect(screen.queryByText(/File not found - it may have been deleted/)).not.toBeInTheDocument();
  });

  it('does not pull someone back who opened another folder while the file was looked up', async () => {
    let answer: () => void = () => {};
    server.use(
      http.get('/api/v1/library/files/5', async () => {
        await new Promise<void>((resolve) => { answer = resolve; });
        return HttpResponse.json(target);
      }),
    );
    window.history.pushState({}, '', '/files?highlight=5');
    render(<FileManagerPage />);
    await screen.findByText('at-the-root.3mf');

    fireEvent.click(screen.getAllByText('Elsewhere')[0]);
    await waitFor(() => expect(listedFolders).toContain('4'));
    answer();

    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(listedFolders).not.toContain('3');
    expect(screen.queryByText('the-queued-one.3mf')).not.toBeInTheDocument();
  });

  it('marks nothing without the parameter', async () => {
    window.history.pushState({}, '', '/files');
    render(<FileManagerPage />);

    await screen.findByText('at-the-root.3mf');
    expect(card('at-the-root.3mf').style.outline).toBe('');
    expect(listedFolders).not.toContain('3');
  });
});
