/**
 * The "By Supplier" widget on the stats dashboard (#2988).
 *
 * It aggregates the internal spool table's purchase-source assignments. In
 * Spoolman mode those assignments live in the Spoolman twin table instead, so
 * the card would say "no suppliers yet" next to an inventory showing supplier
 * chips. The widget is dropped in that mode, and held back until the mode is
 * known so a Spoolman install never flashes it.
 */

import { describe, it, expect, beforeEach } from 'vitest';
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

let supplierRequests: URL[] = [];

function setupHandlers(spoolmanEnabled: boolean, spoolmanGate?: Promise<void>) {
  server.use(
    http.get('/api/v1/archives/stats', () => HttpResponse.json(EMPTY_STATS)),
    http.get('/api/v1/printers/', () => HttpResponse.json([])),
    http.get('/api/v1/archives/slim', () => HttpResponse.json([])),
    http.get('/api/v1/settings/', () => HttpResponse.json({ currency: 'USD' })),
    http.get('/api/v1/settings/spoolman', async () => {
      // A gate holds the settings response back while the rest of the
      // dashboard renders, which is the ordering the widget has to survive.
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
    http.get('/api/v1/inventory/stats/suppliers', ({ request }) => {
      supplierRequests.push(new URL(request.url));
      return HttpResponse.json([]);
    })
  );
}

function gated() {
  let open = () => {};
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { gate, open: () => open() };
}

describe('StatsPage supplier widget', () => {
  beforeEach(() => {
    supplierRequests = [];
  });

  it('shows the widget in internal inventory mode', async () => {
    setupHandlers(false);
    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('By Supplier')).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(supplierRequests.length).toBeGreaterThan(0);
    });
  });

  it('drops the widget entirely in Spoolman mode', async () => {
    setupHandlers(true);
    render(<StatsPage />);

    // Wait for the dashboard to be up before asserting on an absence.
    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });

    expect(screen.queryByText('By Supplier')).toBeNull();
    expect(supplierRequests).toHaveLength(0);
  });

  it('holds the widget back until the Spoolman setting has resolved', async () => {
    const { gate, open } = gated();
    setupHandlers(true, gate);
    render(<StatsPage />);

    // The dashboard is fully up on the archive response alone.
    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });
    expect(screen.queryByText('By Supplier')).toBeNull();
    expect(supplierRequests).toHaveLength(0);

    open();
    await waitFor(() => {
      expect(screen.queryByText('By Supplier')).toBeNull();
    });
    expect(supplierRequests).toHaveLength(0);
  });

  it('shows the widget once the setting says this is not Spoolman mode', async () => {
    const { gate, open } = gated();
    setupHandlers(false, gate);
    render(<StatsPage />);

    await waitFor(() => {
      expect(screen.getByText('Filament Trends')).toBeInTheDocument();
    });
    expect(screen.queryByText('By Supplier')).toBeNull();

    open();
    await waitFor(() => {
      expect(screen.getByText('By Supplier')).toBeInTheDocument();
    });
  });
});
