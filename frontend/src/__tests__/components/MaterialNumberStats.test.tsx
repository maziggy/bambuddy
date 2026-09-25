/**
 * Tests for the MaterialNumberStats widget (#2870).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MaterialNumberStats } from '../../components/MaterialNumberStats';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: {
    getMaterialNumberStats: vi.fn(),
  },
}));

function renderWidget(props: { dateFrom?: string; dateTo?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MaterialNumberStats currency="EUR" {...props} />
    </QueryClientProvider>,
  );
}

describe('MaterialNumberStats', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders one row per material number with weights and cost', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockResolvedValue([
      { material_number: '16', spool_count: 1, remaining_g: 500, consumed_g: 1500, cost: 45 },
      { material_number: '15', spool_count: 12, remaining_g: 9500, consumed_g: 250, cost: 5 },
    ]);
    renderWidget();

    expect(await screen.findByText('16')).toBeInTheDocument();
    expect(screen.getByText('15')).toBeInTheDocument();
    expect(screen.getByText('12')).toBeInTheDocument();
    // >= 1 kg renders as kilograms, below stays in grams.
    expect(screen.getByText('9.50 kg')).toBeInTheDocument();
    expect(screen.getByText('500 g')).toBeInTheDocument();
    expect(screen.getByText('EUR 45.00')).toBeInTheDocument();
  });

  it('shows the empty hint when no numbers are assigned', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderWidget();

    expect(await screen.findByText(/No material numbers assigned yet/)).toBeInTheDocument();
  });

  // A 403 from a missing INVENTORY_READ, a 500 or a dropped connection are
  // not "you have not numbered your spools" — the two states must read
  // differently or the user goes looking for a problem that isn't there.
  it('reports an API failure as a failure, not as an empty inventory', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('403'));
    renderWidget();

    expect(await screen.findByText(/Could not load the material number statistics/)).toBeInTheDocument();
    expect(screen.queryByText(/No material numbers assigned yet/)).not.toBeInTheDocument();
  });

  it('passes the dashboard timeframe to the endpoint', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    renderWidget({ dateFrom: '2026-08-01', dateTo: '2026-08-31' });

    await screen.findByText(/No material numbers assigned yet/);
    expect(api.getMaterialNumberStats).toHaveBeenCalledWith({
      dateFrom: '2026-08-01',
      dateTo: '2026-08-31',
    });
  });

  // The range is part of the query key, so the cache cannot serve a
  // 30-day answer when the dashboard has moved to 90.
  it('refetches on the same client when the timeframe changes', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const { rerender } = render(
      <QueryClientProvider client={client}>
        <MaterialNumberStats currency="EUR" dateFrom="2026-08-01" />
      </QueryClientProvider>,
    );
    await screen.findByText(/No material numbers assigned yet/);
    expect(api.getMaterialNumberStats).toHaveBeenCalledTimes(1);

    rerender(
      <QueryClientProvider client={client}>
        <MaterialNumberStats currency="EUR" dateFrom="2026-09-01" />
      </QueryClientProvider>,
    );

    await vi.waitFor(() => expect(api.getMaterialNumberStats).toHaveBeenCalledTimes(2));
    expect(api.getMaterialNumberStats).toHaveBeenLastCalledWith({
      dateFrom: '2026-09-01',
      dateTo: undefined,
    });
  });
});
