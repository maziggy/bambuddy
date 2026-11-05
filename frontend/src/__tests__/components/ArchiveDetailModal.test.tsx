/**
 * The archive modal (archive redesign P4): the file in the header and its own
 * section, its plates, and every print of it in a table whose rows open to
 * the print's own outcome, figures, note and media. It opens over the
 * archives list and closes on Escape or a click outside it.
 */

/// <reference types="@testing-library/jest-dom" />

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ArchiveDetailModal } from '../../components/ArchiveDetailModal';

const NOFILE = 'No 3MF file available — the file could not be downloaded from the printer when the print was recorded';

const navigate = vi.fn();
const onClose = vi.fn();
let routeId = '5';
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

const archive = {
  id: 5,
  filename: 'planter.gcode.3mf',
  print_name: 'Planter',
  printer_id: 2,
  status: 'failed',
  print_time_seconds: 7200,
  filament_used_grams: 80,
  total_layers: 240,
  thumbnail_path: null,
  timelapse_path: 'archives/p/b.mp4',
  photos: ['finish_a.jpg', 'finish_b.jpg'],
  notes: 'Two plates',
  tags: 'garden',
  layer_height: 0.2,
  nozzle_diameter: 0.4,
  bed_type: 'Textured PEI Plate',
  sliced_for_model: 'X1C',
  file_size: 4404019,
  project_id: null,
  project_name: null,
  file_path: 'archives/p/planter.gcode.3mf',
  plate_id: 2,
  created_by_id: null,
  run_count: 2,
  successful_run_count: 1,
  failed_run_count: 1,
  created_at: '2026-10-01T09:00:00Z',
};

const run = (overrides: Record<string, unknown>) => ({
  archive_id: 5,
  print_name: 'Planter',
  printer_name: 'A',
  printer_id: 1,
  status: 'completed',
  started_at: '2026-10-01T09:00:00Z',
  completed_at: '2026-10-01T10:00:00Z',
  duration_seconds: 3600,
  filament_type: 'PLA',
  filament_color: null,
  filament_used_grams: 40,
  cost: 1.2,
  energy_kwh: null,
  energy_cost: null,
  wear_cost: null,
  failure_reason: null,
  user_verdict: null,
  confirm_requested: null,
  notes: null,
  photos: null,
  timelapse_path: null,
  thumbnail_path: null,
  created_by_id: null,
  created_by_username: null,
  created_at: '2026-10-01T10:00:00Z',
  ...overrides,
});

const plate = (index: number, seconds: number) => ({
  index,
  // The plates endpoint names an unnamed plate after its first object.
  name: `Part ${index}`,
  objects: [`Part ${index}`],
  has_thumbnail: false,
  thumbnail_url: null,
  print_time_seconds: seconds,
  filament_used_grams: 40,
  filaments: [{ slot_id: 1, type: 'PLA', color: '#FF0000', used_grams: 40, used_meters: 13 }],
});

const openRow = (id: number) => {
  const row = screen.getByTestId(`archive-run-${id}`);
  fireEvent.click(within(row).getByRole('button', { expanded: false }));
  return row;
};

const runs = [
  run({ id: 12, plate_id: 2, printer_name: 'B', status: 'failed', failure_reason: 'spaghetti', started_at: '2026-10-20T09:00:00Z', timelapse_path: 'archives/p/b.mp4', photos: ['finish_b.jpg'] }),
  run({ id: 11, plate_id: 1, user_verdict: 'good', notes: 'brim lifted', timelapse_path: 'archives/p/a.mp4', photos: ['finish_a.jpg'], estimate_seconds: 3300, estimate_grams: 42 }),
];

describe('ArchiveDetailModal', () => {
  let patched: { id: string; body: unknown } | null;

  beforeEach(() => {
    routeId = '5';
    navigate.mockReset();
    onClose.mockReset();
    patched = null;
    server.use(
      http.get('/api/v1/archives/:id', ({ params }) =>
        Number(params.id) === 5 || Number(params.id) === 4 ? HttpResponse.json(archive) : new HttpResponse(null, { status: 404 })
      ),
      http.get('/api/v1/archives/5/runs', () => HttpResponse.json({ items: runs, total: runs.length })),
      http.get('/api/v1/archives/5/plates', () =>
        HttpResponse.json({ archive_id: 5, filename: 'planter.gcode.3mf', plates: [plate(1, 3300), plate(2, 3600)], is_multi_plate: true }),
      ),
      http.patch('/api/v1/print-log/:id', async ({ params, request }) => {
        patched = { id: String(params.id), body: await request.json() };
        return HttpResponse.json(run({ id: Number(params.id), notes: 'warped' }));
      }),
    );
  });

  it('shows the file and every print of it, newest first', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    expect(await screen.findByRole('heading', { name: 'Planter' })).toBeInTheDocument();
    expect(screen.getByText('Two plates')).toBeInTheDocument();
    const items = await screen.findAllByTestId(/archive-run-/);
    expect(items.map((i) => i.dataset.testid)).toEqual(['archive-run-12', 'archive-run-11']);
    expect(within(items[0]).getByText('B')).toBeInTheDocument();
    expect(within(items[1]).getByText('good')).toBeInTheDocument();
    // A print's note is in its row's details.
    expect(within(items[1]).queryByText('brim lifted')).toBeNull();
    openRow(11);
    expect(within(items[1]).getByText('brim lifted')).toBeInTheDocument();
  });

  it('puts the file section before the plates', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-plate-2');

    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(['File', 'Plates', 'Print history']);
    expect(screen.getByText('0.2 mm')).toBeInTheDocument();
    expect(screen.getByText('Textured PEI Plate')).toBeInTheDocument();
    expect(screen.getByText('4.2 MB')).toBeInTheDocument();
    expect(screen.getByText('planter.gcode.3mf · sliced for X1C · 0.4 mm · Textured PEI Plate')).toBeInTheDocument();
  });

  it('counts the prints of each plate and shows only theirs on request', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    const first = await screen.findByTestId('archive-plate-1');
    await screen.findByTestId('archive-run-12');

    expect(within(first).getByText('Plate 1')).toBeInTheDocument();
    expect(within(first).getByText('· Part 1')).toBeInTheDocument();
    expect(within(first).getByText('Good: 1')).toBeInTheDocument();
    expect(within(first).getByText('Failed: 0')).toBeInTheDocument();
    expect(within(first).getByText('Last print vs estimate: +9%')).toBeInTheDocument();
    const second = screen.getByTestId('archive-plate-2');
    expect(within(second).getByText('Failed: 1')).toBeInTheDocument();
    // Its only print failed: nothing finished to measure against the estimate.
    expect(within(second).queryByText(/Last print vs estimate/)).toBeNull();

    fireEvent.click(within(first).getByRole('button', { name: 'Show its prints' }));

    expect(screen.getAllByTestId(/archive-run-/).map((i) => i.dataset.testid)).toEqual(['archive-run-11']);
    expect(screen.getByRole('button', { name: 'Plate 1', pressed: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'All plates' }));
    expect(screen.getAllByTestId(/archive-run-/)).toHaveLength(2);
  });

  it("puts each print's figures next to its plate's estimate", async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    const first = await screen.findByTestId('archive-run-11');

    expect(within(first).getByText('1h 0m vs 55m (+9%)')).toBeInTheDocument();
    expect(within(first).getByText('40.0 g (est. 42.0 g)')).toBeInTheDocument();
    // A print without one shows its own figures alone.
    expect(within(screen.getByTestId('archive-run-12')).getByText('40.0 g')).toBeInTheDocument();
  });

  it('gives a time that was not measured no percentage', async () => {
    server.use(
      http.get('/api/v1/archives/5/runs', () =>
        HttpResponse.json({ items: [run({ id: 14, plate_id: 1, duration_unmeasured: true, estimate_seconds: 3300 })], total: 1 }),
      ),
    );
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    expect(within(await screen.findByTestId('archive-run-14')).getByText('~1h 0m (est. 55m)')).toBeInTheDocument();
  });

  it('offers a source file to slice, not to print', async () => {
    server.use(
      http.get('/api/v1/archives/:id', () =>
        HttpResponse.json({ ...archive, filename: 'planter.3mf', print_time_seconds: null, total_layers: null }),
      ),
    );
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-plate-1');

    expect(screen.queryByRole('button', { name: 'Print' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Print this plate/ })).toBeNull();
    expect(screen.getByRole('button', { name: /Slice/ })).toBeInTheDocument();
  });

  it('says why printing is off', async () => {
    server.use(http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, file_path: null })));
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-plate-1');

    const print = screen.getByRole('button', { name: /^Print$/ });
    expect(print).toBeDisabled();
    expect(print).toHaveAttribute('title', NOFILE);
    expect(within(screen.getByTestId('archive-plate-1')).getByRole('button', { name: /Print this plate/ })).toHaveAttribute('title', NOFILE);
    expect(screen.getByRole('button', { name: /Slice/ })).toHaveAttribute('title', NOFILE);
  });

  it('measures a stopped print by how far it got', async () => {
    server.use(
      http.get('/api/v1/archives/5/runs', () =>
        HttpResponse.json({ items: [run({ id: 13, plate_id: 2, status: 'failed', duration_seconds: 1200, estimate_seconds: 3600 })], total: 1 }),
      ),
    );
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    expect(within(await screen.findByTestId('archive-run-13')).getByText('20m of 1h 0m')).toBeInTheDocument();
  });

  it("plays each print's own video", async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-11');
    const first = openRow(11);

    fireEvent.click(within(first).getByRole('button', { name: /Timelapse video/ }));

    const video = await waitFor(() => {
      const el = document.querySelector('video');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(video.getAttribute('src')).toContain('/archives/5/runs/11/timelapse');
  });

  it("leaves the file's video to the prints that hold it", async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');

    // The archive's video is print 12's: the actions menu leaves it out.
    const before = screen.queryAllByText('Timelapse video').length;
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.getByText('Download')).toBeInTheDocument();
    expect(screen.queryAllByText('Timelapse video')).toHaveLength(before);
  });

  it("offers a video no print holds in the actions menu", async () => {
    server.use(http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, timelapse_path: 'archives/p/old.mp4' })));
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');

    const before = screen.queryAllByText('Timelapse video').length;
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    expect(screen.queryAllByText('Timelapse video')).toHaveLength(before + 1);
  });

  it('gives focus back to the actions button when its menu closes', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');

    const more = screen.getByRole('button', { name: 'More actions' });
    fireEvent.click(more);
    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByText('Download')).toBeNull());
    expect(document.activeElement).toBe(more);
  });

  it('closes on Escape and on a click outside it', async () => {
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    const dialog = await screen.findByRole('dialog', { name: 'Planter' });

    const backdrop = screen.getByTestId('archive-modal-backdrop');
    Object.defineProperty(backdrop, 'clientWidth', { value: 1000 });

    fireEvent.mouseDown(dialog, { clientX: 500 });
    expect(onClose).not.toHaveBeenCalled();
    // On its scrollbar, right of its content box: that scrolls.
    fireEvent.mouseDown(backdrop, { clientX: 1005 });
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.mouseDown(backdrop, { clientX: 20 });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('lets Escape in a note being written leave the note only', async () => {
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');
    const row = openRow(12);
    fireEvent.click(within(row).getByRole('button', { name: /Add note/ }));

    fireEvent.keyDown(within(row).getByRole('textbox'), { key: 'Escape' });

    expect(onClose).not.toHaveBeenCalled();
  });

  it('lets Escape close a menu opened from it first', async () => {
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.keyDown(document, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByText('Download')).toBeNull());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('shows a larger cover on hover', async () => {
    server.use(http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, thumbnail_path: 'archives/p/thumb.png' })));
    const matchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      ...matchMedia(query),
      matches: query.includes('hover'),
    })) as unknown as typeof window.matchMedia;
    try {
      render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
      fireEvent.mouseEnter((await screen.findByRole('img', { name: 'Planter' })).parentElement!);

      const preview = await screen.findByTestId('thumbnail-hover-preview');
      expect(preview.style.width).toBe('480px');
    } finally {
      window.matchMedia = matchMedia;
    }
  });

  it("shows each plate's photos and video", async () => {
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    const second = await screen.findByTestId('archive-plate-2');
    await screen.findByTestId('archive-run-12');

    fireEvent.click(within(second).getByRole('button', { name: /Timelapse video/ }));
    const video = await waitFor(() => {
      const el = document.querySelector('video');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(video.getAttribute('src')).toContain('/archives/5/runs/12/timelapse');
    expect(within(second).getByRole('button', { name: 'Photos (1)' })).toBeInTheDocument();
  });

  it("shows the archive's photos on a print logged before prints kept their own", async () => {
    server.use(
      http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, photos: ['old.jpg'], timelapse_path: null })),
      // Its video is its own already; its photos are still the archive's.
      http.get('/api/v1/archives/5/runs', () =>
        HttpResponse.json({ items: [run({ id: 16, plate_id: 1, timelapse_path: 'archives/p/16.mp4' })], total: 1 }),
      ),
    );
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    await screen.findByTestId('archive-run-16');
    const row = openRow(16);

    expect(within(row).getByRole('button', { name: 'Photos (1)' })).toBeInTheDocument();
    expect(within(screen.getByTestId('archive-plate-1')).getByRole('button', { name: 'Photos (1)' })).toBeInTheDocument();
  });

  it("does not share the archive's photos with a print that has its own list", async () => {
    server.use(
      http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, photos: ['old.jpg'], timelapse_path: null })),
      http.get('/api/v1/archives/5/runs', () => HttpResponse.json({ items: [run({ id: 18, plate_id: 1, photos: [] })], total: 1 })),
    );
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    await screen.findByTestId('archive-run-18');
    const row = openRow(18);

    expect(within(row).queryByRole('button', { name: /Photos/ })).toBeNull();
  });

  it('shares only the photos no print holds, and the video none holds', async () => {
    server.use(
      http.get('/api/v1/archives/:id', () =>
        HttpResponse.json({ ...archive, photos: ['finish_a.jpg', 'finish_b.jpg', 'old.jpg'], timelapse_path: 'archives/p/old.mp4' }),
      ),
      http.get('/api/v1/archives/5/runs', () =>
        HttpResponse.json({ items: [...runs, run({ id: 17, plate_id: 2, started_at: '2026-09-01T09:00:00Z' })], total: 3 }),
      ),
    );
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
    await screen.findByTestId('archive-run-17');
    const row = openRow(17);

    expect(within(row).getByRole('button', { name: 'Photos (1)' })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: /Timelapse video/ }));
    const video = await waitFor(() => {
      const el = document.querySelector('video');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(video.getAttribute('src')).toContain('/archives/5/timelapse');
  });

  it("shows a larger plate cover on hover", async () => {
    server.use(
      http.get('/api/v1/archives/5/plates', () =>
        HttpResponse.json({
          archive_id: 5,
          filename: 'planter.gcode.3mf',
          plates: [{ ...plate(1, 3300), has_thumbnail: true }, plate(2, 3600)],
          is_multi_plate: true,
        }),
      ),
    );
    const matchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      ...matchMedia(query),
      matches: query.includes('hover'),
    })) as unknown as typeof window.matchMedia;
    try {
      render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);
      const card = await screen.findByTestId('archive-plate-1');
      fireEvent.mouseEnter(within(card).getByRole('img', { name: 'Plate 1' }).parentElement!);

      expect((await screen.findByTestId('thumbnail-hover-preview')).style.width).toBe('400px');
    } finally {
      window.matchMedia = matchMedia;
    }
  });

  it("dots each print's filament colours", async () => {
    server.use(
      http.get('/api/v1/archives/5/runs', () =>
        HttpResponse.json({ items: [run({ id: 15, filament_type: 'PLA,PETG', filament_color: '#FF0000,#00FF00' })], total: 1 }),
      ),
    );
    render(<ArchiveDetailModal archiveId={5} onClose={onClose} />);

    const cell = within(await screen.findByTestId('archive-run-15')).getByTestId('run-filament');
    expect(cell).toHaveTextContent('PLA, PETG');
    expect(cell.textContent).not.toContain('#FF0000');
    expect(within(cell).getByTitle('#00FF00').style.backgroundColor).toBe('rgb(0, 255, 0)');
  });

  it('deletes from the actions menu after asking', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }));
    fireEvent.click(screen.getByText('Delete'));

    expect(await screen.findByText('Delete Archive')).toBeInTheDocument();
  });

  it('saves a note on one print', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-12');
    const latest = openRow(12);

    fireEvent.click(within(latest).getByRole('button', { name: /Add note/ }));
    fireEvent.change(within(latest).getByRole('textbox'), { target: { value: 'warped' } });
    fireEvent.click(within(latest).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(patched).toEqual({ id: '12', body: { notes: 'warped' } }));
  });

  it('opens a decided print to change its outcome', async () => {
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);
    await screen.findByTestId('archive-run-11');
    const decided = openRow(11);

    fireEvent.click(within(decided).getByRole('button', { name: /Change outcome/ }));

    const dialog = await screen.findByTestId('confirm-outcome-dialog');
    expect(within(dialog).getByRole('heading', { name: 'Change outcome' })).toBeInTheDocument();
    expect(await within(dialog).findByRole('button', { name: 'Reject' })).toBeInTheDocument();
  });

  it('opens the archive a merged one went into', async () => {
    routeId = '4';
    window.history.pushState({}, '', '/archives/4?run=11');
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/archives/5?run=11', { replace: true }));
    window.history.pushState({}, '', '/');
  });

  it('marks the print a link points at', async () => {
    window.history.pushState({}, '', '/archives/5?run=11');
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    const item = await screen.findByTestId('archive-run-11');
    expect(item.className).toContain('ring-2');
    expect(screen.getByTestId('archive-run-12').className).not.toContain('ring-2');
    // and opens its details
    expect(within(item).getByText('brim lifted')).toBeInTheDocument();
    window.history.pushState({}, '', '/');
  });

  it('offers to try again when the archive could not be loaded', async () => {
    let calls = 0;
    server.use(
      http.get('/api/v1/archives/:id', () => {
        calls += 1;
        return calls === 1 ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(archive);
      }),
    );
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));

    expect(await screen.findByRole('heading', { name: 'Planter' })).toBeInTheDocument();
  });

  it('names the one print of an archive printed before prints were logged', async () => {
    server.use(
      http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, status: 'completed', run_count: 0 })),
      http.get('/api/v1/archives/5/runs', () => HttpResponse.json({ items: [], total: 0 })),
    );
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    expect(await screen.findByText('Printed before Bambuddy logged each print: Completed.')).toBeInTheDocument();
  });

  it('says so for an archive it may not see', async () => {
    routeId = '9';
    render(<ArchiveDetailModal archiveId={Number(routeId)} onClose={onClose} />);

    expect(await screen.findByText('This archive does not exist or you cannot see it.')).toBeInTheDocument();
  });
});
