/**
 * /archives?highlight=<id>, the queue card's archive link (#3336). The page
 * used to ignore the parameter and just open the list.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useNavigate } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { ArchivesPage } from '../../pages/ArchivesPage';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';

function archive(id: number, printName: string) {
  return {
    id,
    filename: `${printName}.gcode.3mf`,
    print_name: printName,
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
  };
}

// The list without archive 3, as cached before its queued job started.
let beforeTheJobStarted = false;

/** Follows a link inside the running app, as the queue page's does. */
function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>go</button>;
}

function setViewMode(mode: string) {
  (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
    key === 'archiveViewMode' ? mode : null,
  );
}

function card(printName: string): HTMLElement {
  return screen.getByText(printName).closest('[data-archive-id]') as HTMLElement;
}

describe('ArchivesPage ?highlight=', () => {
  beforeEach(() => {
    setAuthToken(null);
    setViewMode('grid');
    beforeTheJobStarted = false;
    server.use(
      http.get('/api/v1/archives/', async () => {
        if (beforeTheJobStarted) return HttpResponse.json([archive(1, 'Benchy'), archive(2, 'Bracket')]);
        // Slower than the page's 100 ms scroll check, so nothing can be decided
        // on a list that is still on its way.
        await new Promise((resolve) => setTimeout(resolve, 300));
        return HttpResponse.json([archive(1, 'Benchy'), archive(2, 'Bracket'), archive(3, 'Just started')]);
      }),
      http.get('/api/v1/archives/stats', () => HttpResponse.json({ total_archives: 2 })),
      http.get('/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'X1 Carbon' }])),
      http.get('/api/v1/projects/', () => HttpResponse.json([])),
      http.get('/api/v1/archives/tags', () => HttpResponse.json([])),
    );
  });

  afterEach(() => {
    window.history.pushState({}, '', '/');
  });

  it('marks the linked archive', async () => {
    window.history.pushState({}, '', '/archives?highlight=2');
    render(<ArchivesPage />);

    await screen.findByText('Bracket');
    await waitFor(() => expect(card('Bracket').style.outline).toContain('solid'));
    expect(card('Benchy').style.outline).toBe('');
  });

  it('leaves the log view for the grid, where the archive can be shown', async () => {
    setViewMode('log');
    window.history.pushState({}, '', '/archives?highlight=2');
    render(<ArchivesPage />);

    await waitFor(() => expect(card('Bracket').style.outline).toContain('solid'));
  });

  it('warns when the linked archive is not in the list', async () => {
    window.history.pushState({}, '', '/archives?highlight=99');
    render(<ArchivesPage />);

    expect(await screen.findByText('Linked archive not visible - try clearing filters')).toBeInTheDocument();
  });

  it('looks for the archive in a fresh list, not one cached before it existed', async () => {
    beforeTheJobStarted = true;
    window.history.pushState({}, '', '/archives');
    render(<><ArchivesPage /><GoTo to="/archives?highlight=3" /></>);
    await screen.findByText('Bracket');

    beforeTheJobStarted = false;
    fireEvent.click(screen.getByText('go'));

    await waitFor(() => expect(card('Just started').style.outline).toContain('solid'));
    expect(screen.queryByText(/not visible - try clearing filters/)).not.toBeInTheDocument();
  });

  it('marks nothing without the parameter', async () => {
    window.history.pushState({}, '', '/archives');
    render(<ArchivesPage />);

    await screen.findByText('Bracket');
    expect(card('Bracket').style.outline).toBe('');
    expect(screen.queryByText(/not visible - try clearing filters/)).not.toBeInTheDocument();
  });
});
