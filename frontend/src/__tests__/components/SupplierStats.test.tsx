/**
 * Tests for the "By Supplier" dashboard widget (#2988).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SupplierStats } from '../../components/SupplierStats';
import { api } from '../../api/client';

vi.mock('../../api/client', () => ({
  api: { getSupplierStats: vi.fn() },
}));

const rows = [
  { supplier_id: 1, supplier_name: 'Filament24', spool_count: 2, remaining_g: 1400, consumed_g: 600, cost: 12.5 },
];

function renderWidget(props: { dateFrom?: string; dateTo?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SupplierStats currency="€" {...props} />
    </QueryClientProvider>,
  );
}

describe('SupplierStats', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.getSupplierStats).mockResolvedValue(rows);
  });

  it('passes the dashboard timeframe to the endpoint', async () => {
    renderWidget({ dateFrom: '2026-08-01', dateTo: '2026-08-31' });
    expect(await screen.findByText('Filament24')).toBeInTheDocument();
    expect(api.getSupplierStats).toHaveBeenCalledWith('2026-08-01', '2026-08-31');
  });

  it('refetches when the timeframe changes', async () => {
    const { rerender } = renderWidget({ dateFrom: '2026-08-01' });
    await screen.findByText('Filament24');

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <SupplierStats currency="€" dateFrom="2026-01-01" />
      </QueryClientProvider>,
    );
    await screen.findByText('Filament24');

    // The range is part of the query key, so a new window is a new read.
    expect(vi.mocked(api.getSupplierStats).mock.calls).toContainEqual(['2026-01-01', undefined]);
  });

  it('reports a failed request as an error, not as "no suppliers yet"', async () => {
    // A 403 from a missing inventory:read or a 500 used to render the empty
    // state, telling the user they had not assigned suppliers they had.
    vi.mocked(api.getSupplierStats).mockRejectedValue(new Error('boom'));
    renderWidget();
    expect(await screen.findByText(/Could not load supplier statistics/i)).toBeInTheDocument();
    expect(screen.queryByText(/No purchases recorded yet/i)).not.toBeInTheDocument();
  });

  it('still shows the empty state when the inventory really is empty', async () => {
    vi.mocked(api.getSupplierStats).mockResolvedValue([]);
    renderWidget();
    expect(await screen.findByText(/No purchases recorded yet/i)).toBeInTheDocument();
  });
});
