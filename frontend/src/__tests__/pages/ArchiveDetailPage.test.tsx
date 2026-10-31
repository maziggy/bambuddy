/**
 * The archive page (archive redesign P4): the file in the header, every print
 * of it below with its own outcome, figures, note and media.
 */

/// <reference types="@testing-library/jest-dom" />

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ArchiveDetailPage } from '../../pages/ArchiveDetailPage';

const navigate = vi.fn();
let routeId = '5';
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: routeId }),
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

const runs = [
  run({ id: 12, plate_id: 2, printer_name: 'B', status: 'failed', failure_reason: 'spaghetti', started_at: '2026-10-20T09:00:00Z', timelapse_path: 'archives/p/b.mp4', photos: ['finish_b.jpg'] }),
  run({ id: 11, plate_id: 1, user_verdict: 'good', notes: 'brim lifted', timelapse_path: 'archives/p/a.mp4', photos: ['finish_a.jpg'] }),
];

describe('ArchiveDetailPage', () => {
  let patched: { id: string; body: unknown } | null;

  beforeEach(() => {
    routeId = '5';
    navigate.mockReset();
    patched = null;
    server.use(
      http.get('/api/v1/archives/:id', ({ params }) =>
        Number(params.id) === 5 || Number(params.id) === 4 ? HttpResponse.json(archive) : new HttpResponse(null, { status: 404 })
      ),
      http.get('/api/v1/archives/5/runs', () => HttpResponse.json({ items: runs, total: runs.length })),
      http.patch('/api/v1/print-log/:id', async ({ params, request }) => {
        patched = { id: String(params.id), body: await request.json() };
        return HttpResponse.json(run({ id: Number(params.id), notes: 'warped' }));
      }),
    );
  });

  it('shows the file and every print of it, newest first', async () => {
    render(<ArchiveDetailPage />);

    expect(await screen.findByRole('heading', { name: 'Planter' })).toBeInTheDocument();
    expect(screen.getByText('Two plates')).toBeInTheDocument();
    const items = await screen.findAllByTestId(/archive-run-/);
    expect(items.map((i) => i.dataset.testid)).toEqual(['archive-run-12', 'archive-run-11']);
    expect(within(items[0]).getByText('B')).toBeInTheDocument();
    expect(within(items[1]).getByText('brim lifted')).toBeInTheDocument();
    expect(within(items[1]).getByText('good')).toBeInTheDocument();
  });

  it("plays each print's own video", async () => {
    render(<ArchiveDetailPage />);
    const first = await screen.findByTestId('archive-run-11');

    fireEvent.click(within(first).getByRole('button', { name: /Timelapse video/ }));

    const video = await waitFor(() => {
      const el = document.querySelector('video');
      expect(el).not.toBeNull();
      return el!;
    });
    expect(video.getAttribute('src')).toContain('/archives/5/runs/11/timelapse');
  });

  it("leaves the file's video to the prints that hold it", async () => {
    render(<ArchiveDetailPage />);
    await screen.findByTestId('archive-run-12');

    // The archive's video is print 12's: no header button for it.
    const header = screen.getByRole('heading', { name: 'Planter' }).closest('div.flex-1')!;
    expect(within(header as HTMLElement).queryByRole('button', { name: /Timelapse video/ })).toBeNull();
  });

  it('saves a note on one print', async () => {
    render(<ArchiveDetailPage />);
    const latest = await screen.findByTestId('archive-run-12');

    fireEvent.click(within(latest).getByRole('button', { name: /Add note/ }));
    fireEvent.change(within(latest).getByRole('textbox'), { target: { value: 'warped' } });
    fireEvent.click(within(latest).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(patched).toEqual({ id: '12', body: { notes: 'warped' } }));
  });

  it('opens the archive a merged one went into', async () => {
    routeId = '4';
    window.history.pushState({}, '', '/archives/4?run=11');
    render(<ArchiveDetailPage />);

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/archives/5?run=11', { replace: true }));
    window.history.pushState({}, '', '/');
  });

  it('marks the print a link points at', async () => {
    window.history.pushState({}, '', '/archives/5?run=11');
    render(<ArchiveDetailPage />);

    const item = await screen.findByTestId('archive-run-11');
    expect(item.firstElementChild?.className).toContain('ring-2');
    expect(screen.getByTestId('archive-run-12').firstElementChild?.className ?? '').not.toContain('ring-2');
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
    render(<ArchiveDetailPage />);

    fireEvent.click(await screen.findByRole('button', { name: 'Try again' }));

    expect(await screen.findByRole('heading', { name: 'Planter' })).toBeInTheDocument();
  });

  it('names the one print of an archive printed before prints were logged', async () => {
    server.use(
      http.get('/api/v1/archives/:id', () => HttpResponse.json({ ...archive, status: 'completed', run_count: 0 })),
      http.get('/api/v1/archives/5/runs', () => HttpResponse.json({ items: [], total: 0 })),
    );
    render(<ArchiveDetailPage />);

    expect(await screen.findByText('Printed before Bambuddy logged each print: Completed.')).toBeInTheDocument();
  });

  it('says so for an archive it may not see', async () => {
    routeId = '9';
    render(<ArchiveDetailPage />);

    expect(await screen.findByText('This archive does not exist or you cannot see it.')).toBeInTheDocument();
  });
});
