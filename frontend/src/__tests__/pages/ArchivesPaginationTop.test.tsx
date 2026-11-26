/**
 * The Archives grid and list views get a pagination bar above the results as
 * well as below them (#3079), so a page can be changed without scrolling all
 * the way down first, which is painful on a phone. The top copy only shows
 * when there is another page to go to.
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { server } from '../mocks/server';

/** Fields the grid card and list row read; only id, name and created_at vary. */
function archive(id: number) {
  return {
    id,
    filename: `part-${id}.gcode.3mf`,
    print_name: `Part ${id}`,
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
    // A minute apart, so the newest-first sort has a fixed order.
    created_at: `2026-09-01T09:${String(id).padStart(2, '0')}:00Z`,
    updated_at: '2026-09-01T11:00:00Z',
    has_f3d: false,
    confirm_requested: false,
    user_verdict: null,
  };
}

/** View mode and page size restore themselves from (mocked) localStorage. */
function stored(viewMode: string, pageSize: string) {
  vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
    key === 'archiveViewMode' ? viewMode : key === 'archivePageSize' ? pageSize : null,
  );
}

function useArchives(count: number) {
  const archives = Array.from({ length: count }, (_, i) => archive(i + 1));
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
}

describe('ArchivesPage pagination bar above the results (#3079)', () => {
  afterEach(() => {
    vi.mocked(localStorage.getItem).mockReset();
  });

  for (const view of ['grid', 'list'] as const) {
    it(`shows a working top bar in the ${view} view when there is a second page`, async () => {
      stored(view, '25');
      useArchives(30);
      render(<ArchivesPage />);

      const top = await screen.findByTestId('archive-pagination-top');
      expect(screen.getByTestId('archive-pagination-bottom')).toBeInTheDocument();
      expect(within(top).getByText(/Page\s+1\s+of\s+2/)).toBeInTheDocument();

      // The newest-first sort puts Part 30 on page 1 and Part 1 on page 2.
      expect(screen.queryByText('Part 1')).not.toBeInTheDocument();
      // Next page is the third of the four arrow buttons.
      fireEvent.click(within(top).getAllByRole('button')[2]);

      expect(await screen.findByText('Part 1')).toBeInTheDocument();
      // Both bars follow the same page state.
      expect(within(screen.getByTestId('archive-pagination-top')).getByText(/Page\s+2\s+of\s+2/)).toBeInTheDocument();
      expect(within(screen.getByTestId('archive-pagination-bottom')).getByText(/Page\s+2\s+of\s+2/)).toBeInTheDocument();
    });
  }

  it('leaves the top bar out when everything fits on one page', async () => {
    stored('grid', '25');
    useArchives(3);
    render(<ArchivesPage />);

    expect(await screen.findByText('Part 1')).toBeInTheDocument();
    expect(screen.queryByTestId('archive-pagination-top')).not.toBeInTheDocument();
  });

  it('leaves the top bar out in "All" mode, where the bottom bar keeps the size picker', async () => {
    stored('grid', '-1');
    useArchives(30);
    render(<ArchivesPage />);

    expect(await screen.findByText('Part 1')).toBeInTheDocument();
    expect(screen.getByTestId('archive-pagination-bottom')).toBeInTheDocument();
    expect(screen.queryByTestId('archive-pagination-top')).not.toBeInTheDocument();
  });
});
