/**
 * A running print's cost is an estimate until completion re-prices it from
 * the spools that actually fed it (#3261). The card says so; a finished
 * print's cost is shown as final. A run that failed or was cancelled leaves
 * one full print's grams and cost on the archive, and the card says that
 * the run's own figures are in the Print Log (#3318).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

const baseArchive = {
  printer_id: 1,
  printer_name: 'H2S',
  print_time_seconds: 3600,
  filament_used_grams: 4.17,
  thumbnail_path: null,
  notes: null,
  rating: null,
  project_id: null,
  project_name: null,
  project_color: null,
  print_count: 1,
  tags: '',
  has_f3d: false,
  started_at: '2026-10-07T10:00:00Z',
  created_at: '2026-10-07T10:00:00Z',
  updated_at: '2026-10-07T10:00:00Z',
};

const archives = [
  {
    ...baseArchive,
    id: 1,
    filename: 'running.gcode.3mf',
    print_name: 'Running',
    status: 'printing',
    completed_at: null,
    cost: 7.8,
  },
  {
    ...baseArchive,
    id: 2,
    filename: 'finished.gcode.3mf',
    print_name: 'Finished',
    status: 'completed',
    completed_at: '2026-10-07T11:00:00Z',
    cost: 10.94,
  },
  {
    ...baseArchive,
    id: 3,
    filename: 'failed.gcode.3mf',
    print_name: 'Failed',
    status: 'failed',
    completed_at: '2026-10-07T10:02:00Z',
    cost: 20.22,
  },
  {
    ...baseArchive,
    id: 4,
    filename: 'cancelled.gcode.3mf',
    print_name: 'Cancelled',
    status: 'cancelled',
    completed_at: '2026-10-07T10:05:00Z',
    cost: 5.5,
  },
];

const FULL_PRINT = 'One full print of this file. What this run used is in the Print Log';

describe('ArchivesPage cost estimate (#3261)', () => {
  beforeEach(() => {
    setAuthToken(null);
    server.use(
      http.get('/api/v1/archives/', () => HttpResponse.json(archives)),
      http.get('/api/v1/archives/stats', () =>
        HttpResponse.json({
          total_archives: 2,
          total_print_time_seconds: 7200,
          total_filament_grams: 8.34,
          prints_this_week: 2,
          prints_this_month: 2,
        })
      ),
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'H2S' }])),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/tags', () => HttpResponse.json([]))
    );
  });

  it('marks a running print cost as an estimate and a finished one as final', async () => {
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('archive-cost')).toHaveLength(4);
    });
    const costs = screen.getAllByTestId('archive-cost');
    const running = costs.find((el) => el.textContent?.endsWith('7.80'))!;
    const finished = costs.find((el) => el.textContent?.endsWith('10.94'))!;

    expect(running.textContent).toMatch(/^~.*7\.80$/);
    expect(running).toHaveAttribute('title', 'Estimate — the final cost is set when the print finishes');

    expect(finished.textContent).not.toContain('~');
    expect(finished.textContent).toMatch(/10\.94$/);
    expect(finished).not.toHaveAttribute('title');
  });

  it('says a failed or cancelled run shows one full print, not what the run used (#3318)', async () => {
    render(<ArchivesPage />);

    await waitFor(() => {
      expect(screen.getAllByTestId('archive-cost')).toHaveLength(4);
    });
    const costs = screen.getAllByTestId('archive-cost');
    const grams = screen.getAllByTestId('archive-grams');
    const byCost = (value: string) => costs.findIndex((el) => el.textContent?.endsWith(value));

    for (const value of ['20.22', '5.50']) {
      const i = byCost(value);
      expect(costs[i].textContent).not.toContain('~');
      expect(costs[i]).toHaveAttribute('title', FULL_PRINT);
      expect(grams[i]).toHaveAttribute('title', FULL_PRINT);
    }
    for (const value of ['7.80', '10.94']) {
      expect(costs[byCost(value)]).not.toHaveAttribute('title', FULL_PRINT);
      expect(grams[byCost(value)]).not.toHaveAttribute('title');
    }
  });
});
