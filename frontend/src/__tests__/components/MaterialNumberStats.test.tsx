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

function renderWidget() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MaterialNumberStats currency="EUR" />
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

  it('fails quiet on API errors (e.g. missing permission)', async () => {
    (api.getMaterialNumberStats as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('403'));
    renderWidget();

    expect(await screen.findByText(/No material numbers assigned yet/)).toBeInTheDocument();
  });
});
