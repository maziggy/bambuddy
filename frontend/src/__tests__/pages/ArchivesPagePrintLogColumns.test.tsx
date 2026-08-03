/**
 * Print Log column configuration (#2636, reporter @ajbastien).
 *
 * The log view hardcoded seven columns, so four populated fields of
 * `print_log_entries` — filament used, cost, energy, energy cost — were
 * unreachable in the UI even though the API had always returned them. The
 * "Filament" column showed only type and colour, which is what made the
 * amount look missing: the per-archive Print Log modal has shown grams since
 * it was written, so the two surfaces disagreed.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { ArchivesPage } from '../../pages/ArchivesPage';

/** Columns visible out of the box — the seven that shipped before, plus the
 *  filament amount #2636 asked for. */
const DEFAULT_VISIBLE_COUNT = 8;

const LOG_ENTRIES = [
  {
    id: 1,
    archive_id: 10,
    print_name: 'Califlower Calibration',
    printer_name: '3DP-00M-191',
    printer_id: 1,
    status: 'completed',
    started_at: '2026-07-24T18:35:00Z',
    completed_at: '2026-07-24T19:24:00Z',
    duration_seconds: 2940,
    filament_type: 'PLA',
    filament_color: '#000000',
    filament_used_grams: 15.5,
    cost: 0.42,
    energy_kwh: 0.31,
    energy_cost: 0.09,
    failure_reason: null,
    thumbnail_path: null,
    created_by_id: null,
    created_by_username: null,
    created_at: '2026-07-24T18:35:00Z',
  },
];

/** One archive so the page isn't in its empty state; the log view doesn't
 *  read it, but the rest of the page does. */
const ONE_ARCHIVE = [
  {
    id: 10,
    filename: 'cali.gcode.3mf',
    print_name: 'Califlower',
    printer_id: 1,
    printer_name: '3DP-00M-191',
    print_time_seconds: 2940,
    filament_used_grams: 15.5,
    status: 'completed',
    started_at: '2026-07-24T18:35:00Z',
    completed_at: '2026-07-24T19:24:00Z',
    thumbnail_path: null,
    notes: null,
    rating: null,
    project_id: null,
    project_name: null,
    project_color: null,
    print_count: 1,
    tags: '',
    created_at: '2026-07-24T18:00:00Z',
    updated_at: '2026-07-24T19:24:00Z',
    has_f3d: false,
  },
];

/** Query strings the page asked the log endpoint for, newest last. */
const logRequests: URLSearchParams[] = [];

function mockLog(entries = LOG_ENTRIES, archives: unknown[] = ONE_ARCHIVE) {
  server.use(
    http.get('/api/v1/archives/', () => HttpResponse.json(archives)),
    http.get('/api/v1/archives/stats', () =>
      HttpResponse.json({
        total_archives: 0,
        total_print_time_seconds: 0,
        total_filament_grams: 0,
        prints_this_week: 0,
        prints_this_month: 0,
      }),
    ),
    http.get('/api/v1/archives/tags', () => HttpResponse.json([])),
    http.get('/api/v1/print-log/', ({ request }) => {
      logRequests.push(new URL(request.url).searchParams);
      return HttpResponse.json({ items: entries, total: entries.length });
    }),
  );
}

/** `setup.ts` replaces localStorage with a no-op vi.fn() stub, so a stored
 *  config has to be handed to the component through the mock. Keyed on the
 *  column key alone — the page reads several other keys (view mode, page
 *  size) and answering those with column JSON would derail the whole page. */
function stubStoredColumns(value: string | null) {
  vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
    (key === 'bambuddy-printlog-columns' ? value : null),
  );
}

/** Switch to the log view — the clipboard icon beside grid / list / calendar. */
async function openLogView() {
  render(<ArchivesPage />);
  await waitFor(() => expect(screen.getByTitle('Print Log')).toBeInTheDocument());
  fireEvent.click(screen.getByTitle('Print Log'));
  await waitFor(() => expect(screen.getByText('All Statuses')).toBeInTheDocument());
  await waitFor(() => expect(screen.queryByText('No print log entries found')).toBeNull());
}

describe('Print Log columns', () => {
  beforeEach(() => {
    logRequests.length = 0;
    stubStoredColumns(null);
    vi.mocked(localStorage.setItem).mockClear();
    mockLog();
  });

  it('shows how much filament the run used, not just which filament', async () => {
    await openLogView();

    const table = screen.getByRole('table');
    // The pre-existing "Filament" column: colour swatch + type.
    expect(within(table).getByText('PLA')).toBeInTheDocument();
    // The amount, which is what the issue asked for.
    expect(within(table).getByText('Filament Used')).toBeInTheDocument();
    expect(within(table).getByText('15.5 g')).toBeInTheDocument();
  });

  it('keeps the optional columns hidden until asked for', async () => {
    await openLogView();

    // Cost / energy exist on every row but would widen the table for everyone,
    // so they ship off. Scoped to the table: "Cost" also appears elsewhere.
    const table = screen.getByRole('table');
    expect(within(table).queryByText('Cost')).not.toBeInTheDocument();
    expect(within(table).queryByText('Energy')).not.toBeInTheDocument();
    expect(within(table).queryByText('$0.42')).not.toBeInTheDocument();
  });

  it('renders a column the user switches on, and remembers it', async () => {
    await openLogView();

    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: /Columns/ }));
    expect(await screen.findByText('Configure Columns')).toBeInTheDocument();

    // Turn on the first hidden column, then apply.
    await user.click(screen.getAllByTitle('Show column')[0]);
    await user.click(screen.getByRole('button', { name: 'Apply Changes' }));

    await waitFor(() => {
      expect(localStorage.setItem).toHaveBeenCalledWith(
        'bambuddy-printlog-columns',
        expect.any(String),
      );
    });
    const [, written] = vi.mocked(localStorage.setItem).mock.calls.at(-1)!;
    const stored = JSON.parse(written as string) as Array<Record<string, unknown>>;
    // Labels are deliberately NOT persisted — they are re-derived from t() on
    // every render, so a config stored before a language switch can't pin the
    // picker to the old language.
    expect(Object.keys(stored[0]).sort()).toEqual(['id', 'visible']);
    // Whatever was off is now on: the picker's first hidden entry.
    expect(stored.filter((c) => c.visible).length).toBeGreaterThan(
      DEFAULT_VISIBLE_COUNT,
    );
  });

  it('falls back to defaults when the stored config is unusable', async () => {
    // A hand-edited or truncated value must not take the page down with it.
    stubStoredColumns('{not json');
    await openLogView();

    const table = screen.getByRole('table');
    expect(within(table).getByText('Filament Used')).toBeInTheDocument();
    expect(within(table).getByText('15.5 g')).toBeInTheDocument();
  });

  it('drops columns that no longer exist and adopts ones added since', async () => {
    // An upgrade must neither crash on a removed id nor silently hide a new
    // column: the stored order wins, unknown ids go, new defaults append.
    stubStoredColumns(
      JSON.stringify([
        { id: 'date', visible: true },
        { id: 'gone_in_a_later_version', visible: true },
      ]),
    );
    await openLogView();

    const table = screen.getByRole('table');
    expect(within(table).getByText('Date')).toBeInTheDocument();
    // Appended from the defaults, with its default visibility.
    expect(within(table).getByText('Filament Used')).toBeInTheDocument();
  });

  it('shows a dash for energy that the background task has not written yet', async () => {
    // energy_kwh / energy_cost land via a background task after the row is
    // created, so a just-finished print genuinely has none.
    stubStoredColumns(
      JSON.stringify([
        { id: 'print_name', visible: true },
        { id: 'energy', visible: true },
      ]),
    );
    // Every other visible cell has a value, so a lone dash can only be energy.
    mockLog([{ ...LOG_ENTRIES[0], energy_kwh: null, created_by_username: 'martin' }]);
    await openLogView();

    const table = screen.getByRole('table');
    expect(within(table).getByText('Energy')).toBeInTheDocument();
    expect(within(table).getByText('—')).toBeInTheDocument();
    expect(within(table).queryByText('0.31 kWh')).not.toBeInTheDocument();
  });

  it('reaches the log even when there are no archives left', async () => {
    // `print_log_entries` outlives the archives it refers to — deleting an
    // archive only NULLs the FK, and clearing the log is a separate action.
    // The page's "no archives yet" branch used to short-circuit before the
    // log branch, so purging archives made the whole Print Log unreachable
    // while its rows were still in the database.
    mockLog(LOG_ENTRIES, []);
    await openLogView();

    const table = screen.getByRole('table');
    expect(within(table).getByText('Califlower Calibration')).toBeInTheDocument();
    expect(within(table).getByText('15.5 g')).toBeInTheDocument();
  });

  it('sorts on the server, not just the page the browser is holding', async () => {
    // The table is paginated server-side, so ordering the 25 rows in hand
    // would answer "the priciest print on this page". The click has to reach
    // the query.
    await openLogView();
    logRequests.length = 0;

    await userEvent.setup().click(screen.getByRole('button', { name: /Filament Used/ }));

    await waitFor(() => expect(logRequests.length).toBeGreaterThan(0));
    const last = logRequests[logRequests.length - 1];
    expect(last.get('sort_by')).toBe('filament_used');
    // Amounts open biggest-first; that is what someone clicking them wants.
    expect(last.get('sort_dir')).toBe('desc');
    // Back to page 1 — re-sorting from page 3 would otherwise drop the user
    // into the middle of a freshly ordered list with no explanation.
    expect(last.get('offset')).toBe('0');
  });

  it('flips direction on a second click of the same column', async () => {
    await openLogView();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: /Filament Used/ }));
    await waitFor(() =>
      expect(logRequests[logRequests.length - 1].get('sort_dir')).toBe('desc'),
    );

    await user.click(screen.getByRole('button', { name: /Filament Used/ }));
    await waitFor(() =>
      expect(logRequests[logRequests.length - 1].get('sort_dir')).toBe('asc'),
    );
    expect(logRequests[logRequests.length - 1].get('sort_by')).toBe('filament_used');
  });

  it('opens text columns A-Z and marks the active header for screen readers', async () => {
    await openLogView();

    await userEvent.setup().click(screen.getByRole('button', { name: /Printer/ }));

    await waitFor(() =>
      expect(logRequests[logRequests.length - 1].get('sort_by')).toBe('printer'),
    );
    expect(logRequests[logRequests.length - 1].get('sort_dir')).toBe('asc');

    const header = screen.getByRole('columnheader', { name: /Printer/ });
    expect(header).toHaveAttribute('aria-sort', 'ascending');
    // Only the active column claims a sort.
    expect(screen.getByRole('columnheader', { name: /Status/ })).toHaveAttribute('aria-sort', 'none');
  });

  it('starts from the stored sort and ignores one naming a dropped column', async () => {
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => {
      if (key === 'bambuddy-printlog-sort') return JSON.stringify({ column: 'not_a_column', direction: 'asc' });
      return null;
    });
    await openLogView();

    // Falls back to the default rather than sending a key the API rejects.
    expect(logRequests[0].get('sort_by')).toBe('date');
    expect(logRequests[0].get('sort_dir')).toBe('desc');
  });
});
