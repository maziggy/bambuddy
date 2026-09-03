/**
 * A sliced 3MF named as a project must still offer Print (#2993).
 *
 * Download an archive that carries the green GCODE badge, re-import it, and the
 * file came back without a Print button. Nothing was lost from the file -- the
 * download serves the stored bytes verbatim -- but the library decided what it
 * was from the filename, and a per-plate export or a cloud-dispatched print
 * arrives as `Foo.3mf` with its G-code intact.
 *
 * The backend has always been willing to print these (`library.py` takes both
 * `3mf` and `gcode.3mf` on the G-code path), so this was the UI refusing to
 * offer something that would have worked.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { render } from '../utils';
import { FileManagerPage } from '../../pages/FileManagerPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const base = {
  file_path: '/library/x',
  file_size: 1048576,
  folder_id: null,
  thumbnail_path: null,
  print_name: null,
  print_time_seconds: null,
  print_count: 0,
  duplicate_count: 0,
  created_at: '2024-01-01T00:00:00Z',
};

/** All three named `.3mf`, so only file_type can tell them apart. */
const files = [
  // The reported file: sliced, named as a project.
  { ...base, id: 1, filename: 'Labyrinth - Plate 3.3mf', file_type: 'gcode.3mf' },
  // A genuine project. Must NOT gain a Print button.
  { ...base, id: 2, filename: 'Labyrinth source.3mf', file_type: '3mf' },
];

function serve(rows: unknown[]) {
  server.use(
    http.get('/api/v1/library/folders', () => HttpResponse.json([])),
    http.get('/api/v1/library/files', () => HttpResponse.json(rows)),
    http.get('/api/v1/library/stats', () =>
      HttpResponse.json({
        total_files: rows.length,
        total_folders: 0,
        total_size_bytes: 1,
        disk_free_bytes: 1,
        disk_total_bytes: 2,
      }),
    ),
  );
}

/** The row's own action strip, so one file's buttons can't answer for another. */
async function rowFor(filename: string): Promise<HTMLElement> {
  const label = await screen.findByText(filename);
  const row = label.closest('div[class*="grid-cols-"]');
  if (!row) throw new Error(`no list row around ${filename}`);
  return row as HTMLElement;
}

describe('FileManagerPage — a sliced 3MF that is not named like one (#2993)', () => {
  beforeEach(() => {
    // localStorage is a module-global vi.fn mock (see __tests__/setup.ts), so
    // the view mode has to be programmed rather than written. List view is the
    // one that puts the row actions on screen without opening a kebab menu.
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
      key === 'library-view-mode' ? 'list' : null,
    );
    serve(files);
  });

  afterEach(() => {
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockReset();
  });

  it('offers Print for the file whose type says it is sliced', async () => {
    render(<FileManagerPage />);

    await waitFor(() => expect(screen.getByText('Labyrinth - Plate 3.3mf')).toBeInTheDocument());
    const row = await rowFor('Labyrinth - Plate 3.3mf');

    await waitFor(() => {
      expect(within(row).getByTitle('Print')).toBeInTheDocument();
    });
  });

  it('does not also offer to slice it, which would re-slice its own G-code', async () => {
    // The Slice gate refused a `.gcode.3mf` *name*, so without this the file
    // would gain a Print button and a Slice button at the same time.
    render(<FileManagerPage />);

    await waitFor(() => expect(screen.getByText('Labyrinth - Plate 3.3mf')).toBeInTheDocument());
    const row = await rowFor('Labyrinth - Plate 3.3mf');

    expect(within(row).queryByTitle('Slice')).not.toBeInTheDocument();
  });

  it('still offers Slice on the genuine project', async () => {
    render(<FileManagerPage />);

    await waitFor(() => expect(screen.getByText('Labyrinth source.3mf')).toBeInTheDocument());
    const row = await rowFor('Labyrinth source.3mf');

    expect(within(row).getByTitle('Slice')).toBeInTheDocument();
  });

  it('does not offer Print for a genuine project of the same shape', async () => {
    render(<FileManagerPage />);

    await waitFor(() => expect(screen.getByText('Labyrinth source.3mf')).toBeInTheDocument());
    const row = await rowFor('Labyrinth source.3mf');

    expect(within(row).queryByTitle('Print')).not.toBeInTheDocument();
  });
});
