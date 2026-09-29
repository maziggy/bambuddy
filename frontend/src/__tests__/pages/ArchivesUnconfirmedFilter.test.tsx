/**
 * The "Unconfirmed" archive filter (#1898) must agree with the rest of the
 * feature about what "still waiting for a verdict" means.
 *
 * confirm_requested is copied onto the archive at dispatch, not at completion,
 * and nothing ever clears it again. So a print that was dispatched with the
 * outcome prompt enabled and then failed, was cancelled, or is still running
 * keeps confirm_requested = true with user_verdict = null for good.
 *
 * Everything else in the feature gates on status === 'completed': the pending /
 * rejected / good badges, the "Confirm Outcome" context-menu entry, and the
 * backend's resolve_pending_confirmation_as_good. If the filter alone does not,
 * those rows show up in the "Unconfirmed" view carrying no badge and no menu
 * entry to answer them with, and they never leave it.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { server } from '../mocks/server';

/** Fields the grid card reads; only status/confirm_requested/user_verdict vary. */
function archive(overrides: Record<string, unknown>) {
  return {
    id: 0,
    filename: 'part.gcode.3mf',
    print_name: 'Part',
    printer_id: 1,
    printer_name: 'X1 Carbon',
    print_time_seconds: 3600,
    filament_used_grams: 15,
    status: 'completed',
    started_at: '2026-09-01T10:00:00Z',
    completed_at: '2026-09-01T11:00:00Z',
    thumbnail_path: null,
    notes: null,
    rating: null,
    project_id: null,
    project_name: null,
    project_color: null,
    print_count: 1,
    duplicate_count: 0,
    duplicate_sequence: 0,
    tags: '',
    created_at: '2026-09-01T09:00:00Z',
    updated_at: '2026-09-01T11:00:00Z',
    has_f3d: false,
    confirm_requested: false,
    user_verdict: null,
    ...overrides,
  };
}

const archives = [
  // The one and only row the filter is for.
  archive({ id: 1, print_name: 'Waiting Benchy', status: 'completed', confirm_requested: true }),
  // Asked for a verdict at dispatch, then failed / was cancelled / never finished.
  archive({ id: 2, print_name: 'Failed Bracket', status: 'failed', confirm_requested: true }),
  archive({ id: 3, print_name: 'Cancelled Gear', status: 'aborted', confirm_requested: true }),
  archive({ id: 4, print_name: 'Running Cube', status: 'printing', confirm_requested: true }),
  // Completed and already answered.
  archive({ id: 5, print_name: 'Answered Vase', status: 'completed', confirm_requested: true, user_verdict: 'good' }),
  // Completed, never asked.
  archive({ id: 6, print_name: 'Quiet Clip', status: 'completed' }),
];

const FILTER_TITLE = 'Show only prints still waiting for their outcome verdict';

describe('ArchivesPage unconfirmed-outcome filter', () => {
  beforeEach(() => {
    // Every filter on this page restores itself from localStorage; a leftover
    // flag would decide the assertions below instead of the click.
    localStorage.clear();
    server.use(
      http.get('/api/v1/archives/', () => HttpResponse.json(archives)),
      http.get('/api/v1/archives/stats', () => HttpResponse.json({})),
      http.get('/api/v1/archives/tags', () => HttpResponse.json([])),
      http.get('/api/v1/archives/no-3mf-warning', () =>
        HttpResponse.json({ has_fallback: false, reason: null }),
      ),
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X1 Carbon' }])),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
    );
  });

  it('lists only completed prints that are still waiting for a verdict', async () => {
    render(<ArchivesPage />);

    // Unfiltered first, so a page that rendered nothing can't pass vacuously.
    expect(await screen.findByText('Waiting Benchy')).toBeInTheDocument();
    expect(screen.getByText('Failed Bracket')).toBeInTheDocument();

    fireEvent.click(screen.getByTitle(FILTER_TITLE));

    expect(await screen.findByText('Waiting Benchy')).toBeInTheDocument();
    // The regression: these carry confirm_requested = true and no verdict, but
    // no badge and no menu entry can ever answer them.
    expect(screen.queryByText('Failed Bracket')).not.toBeInTheDocument();
    expect(screen.queryByText('Cancelled Gear')).not.toBeInTheDocument();
    expect(screen.queryByText('Running Cube')).not.toBeInTheDocument();
    // And the two that were never pending in the first place.
    expect(screen.queryByText('Answered Vase')).not.toBeInTheDocument();
    expect(screen.queryByText('Quiet Clip')).not.toBeInTheDocument();
  });

  it('leaves every archive alone while the filter is off', async () => {
    render(<ArchivesPage />);

    for (const name of [
      'Waiting Benchy',
      'Failed Bracket',
      'Cancelled Gear',
      'Running Cube',
      'Answered Vase',
      'Quiet Clip',
    ]) {
      expect(await screen.findByText(name)).toBeInTheDocument();
    }
  });
});
