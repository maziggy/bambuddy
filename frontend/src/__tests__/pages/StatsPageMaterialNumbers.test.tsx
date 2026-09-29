/**
 * The "By Material Number" widget on the stats dashboard (#2870).
 *
 * It sits in the same grid as the widgets that follow the dashboard
 * timeframe, so its usage half has to follow it too — otherwise it shows
 * lifetime totals next to cards headed "Last 30 days".
 *
 * In Spoolman mode it aggregates the internal spool table, which is empty
 * there, while the inventory list does show numbers mapped from Spoolman's
 * filament.article_number. A permanently empty card saying "no material
 * numbers assigned yet" next to an inventory full of them is worse than no
 * card, so the widget is dropped in that mode.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '../utils';
import { StatsPage } from '../../pages/StatsPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const EMPTY_STATS = {
  total_prints: 0,
  successful_prints: 0,
  failed_prints: 0,
  cancelled_prints: 0,
  total_print_time_hours: 0,
  total_filament_grams: 0,
  total_cost: 0,
  prints_by_filament_type: {},
  prints_by_printer: {},
  average_time_accuracy: 0,
  time_accuracy_by_printer: {},
  total_energy_kwh: 0,
  total_energy_cost: 0,
};

let materialNumberRequests: URL[] = [];

function setupHandlers(spoolmanEnabled: boolean, spoolmanGate?: Promise<void>) {
  server.use(
    http.get('/api/v1/archives/stats', () => HttpResponse.json(EMPTY_STATS)),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/archives/slim', () => HttpResponse.json([])),
    http.get('/api/v1/settings/', () => HttpResponse.json({ currency: 'USD' })),
    http.get('/api/v1/settings/spoolman', async () => {
      // A gate lets a test hold the settings response back while the rest of
      // the dashboard renders, which is the ordering the widget has to survive.
      if (spoolmanGate) await spoolmanGate;
      return HttpResponse.json({
        spoolman_enabled: spoolmanEnabled ? 'true' : 'false',
        spoolman_url: spoolmanEnabled ? 'http://spoolman.local' : '',
      });
    }),
    http.get('/api/v1/archives/analysis/failures', () =>
      HttpResponse.json({
        period_days: 30,
        total_prints: 0,
        failed_prints: 0,
        failure_rate: 0,
        failures_by_reason: {},
        failures_by_filament: {},
        failures_by_printer: {},
        failures_by_hour: {},
        recent_failures: [],
        trend: [],
      })
    ),
    http.get('/api/v1/inventory/stats/material-numbers', ({ request }) => {
      materialNumberRequests.push(new URL(request.url));
      return HttpResponse.json([]);
    })
  );
}

/** The suite stubs localStorage with bare mocks, so feed the timeframe in. */
function withTimeframe(preset: string) {
  (localStorage.getItem as ReturnType<typeof vi.fn>).mockImplementation((key: string) =>
    key === 'bambusy-stats-timeframe' ? JSON.stringify({ preset }) : null
  );
}

describe('StatsPage material-number widget', () => {
  beforeEach(() => {
    materialNumberRequests = [];
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockReset();
  });

  afterEach(() => {
    (localStorage.getItem as ReturnType<typeof vi.fn>).mockReset();
  });

  it('asks the endpoint for the dashboard timeframe', async () => {
    withTimeframe('last-30');
    setupHandlers(false);
    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('By Material Number')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(materialNumberRequests.length).toBeGreaterThan(0);
    });

    const today = new Date();
    const from = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - 29));
    expect(materialNumberRequests[0].searchParams.get('date_from')).toBe(from.toISOString().split('T')[0]);
    expect(materialNumberRequests[0].searchParams.get('date_to')).toBe(today.toISOString().split('T')[0]);
  });

  it('asks for lifetime totals when the timeframe is all time', async () => {
    withTimeframe('all-time');
    setupHandlers(false);
    render(<StatsPage />);

    await waitFor(() => {
      expect(materialNumberRequests.length).toBeGreaterThan(0);
    });
    expect(materialNumberRequests[0].searchParams.get('date_from')).toBeNull();
    expect(materialNumberRequests[0].searchParams.get('date_to')).toBeNull();
  });

  it('drops the widget entirely in Spoolman mode', async () => {
    setupHandlers(true);
    render(<StatsPage />);

    // Wait for the dashboard to be up before asserting on an absence.
    await waitFor(() => {
      expect(screen.getByText('Statistics')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });

    expect(screen.queryByText('By Material Number')).toBeNull();
    expect(materialNumberRequests).toHaveLength(0);
  });

  // #2870: the mode decision has to wait for the settings response. Deriving
  // it from `undefined` treats "not loaded yet" as "internal mode", so a
  // Spoolman install flashed the card and fired the aggregate request before
  // the setting arrived.
  it('holds the widget back until the Spoolman setting has resolved', async () => {
    let openGate = () => {};
    const gate = new Promise<void>((resolve) => {
      openGate = resolve;
    });
    setupHandlers(true, gate);
    render(<StatsPage />);

    // The dashboard is fully up on the archive response alone.
    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });
    expect(screen.queryByText('By Material Number')).toBeNull();
    expect(materialNumberRequests).toHaveLength(0);

    openGate();
    await waitFor(() => {
      expect(screen.queryByText('By Material Number')).toBeNull();
    });
    expect(materialNumberRequests).toHaveLength(0);
  });

  it('shows the widget once the setting says this is not Spoolman mode', async () => {
    let openGate = () => {};
    const gate = new Promise<void>((resolve) => {
      openGate = resolve;
    });
    setupHandlers(false, gate);
    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });
    expect(screen.queryByText('By Material Number')).toBeNull();

    openGate();
    await waitFor(() => {
      expect(screen.getByText('By Material Number')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(materialNumberRequests.length).toBeGreaterThan(0);
    });
  });
});
