/**
 * Tests for the Batch Orders tab (#342).
 *
 * The point of this view is the gap the Queue and History tabs cannot show:
 * what an order asked for versus what has actually been produced, including
 * the runs that failed and are therefore still owed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';

import { render } from '../utils';
import { server } from '../mocks/server';
import { BatchOrdersView } from '../../components/BatchOrdersView';
import type { PrintBatch, PrintBatchPlateProgress } from '../../api/client';

const plate = (over: Partial<PrintBatchPlateProgress> = {}): PrintBatchPlateProgress => {
  const merged = {
    plate_id: 1,
    plate_name: null,
    quantity_target: 1,
    dispatched: 1,
    remaining: 0,
    pending_count: 0,
    printing_count: 0,
    completed_count: 1,
    failed_count: 0,
    cancelled_count: 0,
    skipped_count: 0,
    actual_cost: null,
    estimated_remaining_cost: null,
    filament_used_grams: null,
    print_time_seconds: 0,
    can_dispatch: false,
    ...over,
  } satisfies PrintBatchPlateProgress;
  // A plate keeps something to clone unless a test says otherwise, so by
  // default anything still owed is queueable (#2960).
  return { ...merged, can_dispatch: over.can_dispatch ?? merged.remaining > 0 };
};

const batch = (over: Partial<PrintBatch> = {}): PrintBatch => {
  const merged = {
  id: 1,
  name: 'Widget run',
  archive_id: 7,
  library_file_id: null,
  quantity: 6,
  status: 'active',
  created_at: '2026-08-01T10:00:00Z',
  completed_at: null,
  created_by_id: null,
  created_by_username: null,
  project_id: null,
  due_date: null,
  notes: null,
  pending_count: 0,
  printing_count: 0,
  completed_count: 0,
  failed_count: 0,
  cancelled_count: 0,
  skipped_count: 0,
  has_targets: true,
  target_count: 6,
  remaining_count: 6,
  actual_cost: null,
  estimated_remaining_cost: null,
  filament_used_grams: null,
  print_time_seconds: 0,
  plates: [],
  dispatchable_count: 0,
  ...over,
  } satisfies PrintBatch;
  return { ...merged, dispatchable_count: over.dispatchable_count ?? merged.remaining_count };
};

const allow = () => true;
const deny = () => false;
const passthroughT = (key: string, options?: Record<string, unknown>) => {
  void options;
  return key;
};

describe('BatchOrdersView (#342)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('/api/v1/settings/', () => HttpResponse.json({ currency: 'EUR' })),
      http.get('/api/v1/queue/batches', () => HttpResponse.json([])),
    );
  });

  it('shows the empty state when nothing matches the filter', async () => {
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);
    await waitFor(() =>
      expect(screen.getByText('queue.batchOrders.emptyTitle')).toBeInTheDocument(),
    );
  });

  it('surfaces a finished order that has no queue rows left', async () => {
    // The case the Queue and History tabs each miss: every run completed, so
    // nothing is pending, yet the order is the thing the user wants to see.
    server.use(
      http.get('/api/v1/queue/batches', ({ request }) => {
        const status = new URL(request.url).searchParams.get('status');
        if (status !== 'completed') return HttpResponse.json([]);
        return HttpResponse.json([
          batch({ status: 'completed', completed_count: 6, remaining_count: 0, completed_at: '2026-08-02T10:00:00Z' }),
        ]);
      }),
    );
    const user = userEvent.setup();
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('queue.batchOrders.emptyTitle')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'queue.batchOrders.filter.completed' }));

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    expect(screen.getByText('queue.batchOrders.status.completed')).toBeInTheDocument();
  });

  it('offers to queue what a failed run still owes', async () => {
    let dispatched: { plate_id?: number | null; only_plate?: boolean } | null = null;
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({
            completed_count: 1,
            failed_count: 1,
            target_count: 2,
            remaining_count: 1,
            plates: [plate({ quantity_target: 2, dispatched: 1, remaining: 1, completed_count: 1, failed_count: 1 })],
          }),
        ]),
      ),
      http.post('/api/v1/queue/batches/:id/dispatch', async ({ request }) => {
        dispatched = (await request.json()) as { plate_id?: number | null };
        return HttpResponse.json(batch({ remaining_count: 0, pending_count: 1 }));
      }),
    );

    const user = userEvent.setup();
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    // Reported at both levels: the order summary and the plate that burned.
    expect(screen.getAllByText('queue.batchOrders.failed')).toHaveLength(2);

    await user.click(screen.getByRole('button', { name: /queue.batchOrders.dispatchRemaining/ }));
    await waitFor(() => expect(dispatched).not.toBeNull());
    // Order-level dispatch covers every plate, so no plate filter is sent.
    expect(dispatched).toEqual({});
  });

  it('dispatches a single plate from its own row', async () => {
    let dispatched: { plate_id?: number | null; only_plate?: boolean } | null = null;
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({
            target_count: 4,
            remaining_count: 3,
            plates: [
              plate({ plate_id: 1, quantity_target: 1, remaining: 0 }),
              plate({ plate_id: 2, quantity_target: 3, dispatched: 0, completed_count: 0, remaining: 3 }),
            ],
          }),
        ]),
      ),
      http.post('/api/v1/queue/batches/:id/dispatch', async ({ request }) => {
        dispatched = (await request.json()) as { plate_id?: number | null };
        return HttpResponse.json(batch());
      }),
    );

    const user = userEvent.setup();
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    // Only the plate with work outstanding offers the action.
    const plateButtons = screen.getAllByRole('button', { name: 'queue.batchOrders.dispatchPlate' });
    expect(plateButtons).toHaveLength(1);

    await user.click(plateButtons[0]);
    await waitFor(() => expect(dispatched).not.toBeNull());
    expect(dispatched).toEqual({ plate_id: 2, only_plate: true });
  });

  it('marks a legacy batch as grouping-only and offers no dispatch', async () => {
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({ has_targets: false, target_count: 3, remaining_count: 0, pending_count: 3, plates: [] }),
        ]),
      ),
    );
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('queue.batchOrders.noTargets')).toBeInTheDocument());
    expect(
      screen.queryByRole('button', { name: /queue.batchOrders.dispatchRemaining/ }),
    ).not.toBeInTheDocument();
  });

  it('hides the dispatch and cancel actions without permission', async () => {
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({ pending_count: 2, remaining_count: 2, plates: [plate({ quantity_target: 3, remaining: 2 })] }),
        ]),
      ),
    );
    render(<BatchOrdersView hasPermission={deny} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    expect(
      screen.queryByRole('button', { name: /queue.batchOrders.dispatchRemaining/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /queue.batchOrders.dispatchPlate/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'queue.cancelBatch' })).not.toBeInTheDocument();
  });

  it('shows cost only once a run has produced one', async () => {
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({ id: 1, name: 'Priced', actual_cost: 6, estimated_remaining_cost: 3, completed_count: 2 }),
          batch({ id: 2, name: 'Unpriced', actual_cost: null, estimated_remaining_cost: null }),
        ]),
      ),
    );
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Priced')).toBeInTheDocument());
    // One cost line, belonging to the priced order — never a fabricated 0.00.
    expect(screen.getAllByText('queue.batchOrders.costSoFar')).toHaveLength(1);
  });

  it('explains a plate that cannot be queued instead of offering a button that fails', async () => {
    // #2960: the plate's last queue item was deleted, so there is nothing left
    // to clone its configuration from. The card used to offer "Queue remaining"
    // anyway and answer with an error toast.
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({
            target_count: 3,
            remaining_count: 3,
            dispatchable_count: 0,
            plates: [
              plate({ plate_id: 1, quantity_target: 3, dispatched: 0, completed_count: 0, remaining: 3, can_dispatch: false }),
            ],
          }),
        ]),
      ),
    );
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    expect(screen.getByText('queue.batchOrders.strandedNotice')).toBeInTheDocument();
    expect(screen.getByText('queue.batchOrders.strandedPlate')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /queue.batchOrders.dispatchRemaining/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'queue.batchOrders.dispatchPlate' })).not.toBeInTheDocument();
  });

  it('still offers to close an order with nothing pending to cancel', async () => {
    // #2960: cancel is the only way out of an order whose runs were all
    // deleted, and that order has no pending items by definition.
    server.use(
      http.get('/api/v1/queue/batches', () =>
        HttpResponse.json([
          batch({
            pending_count: 0,
            target_count: 3,
            remaining_count: 3,
            dispatchable_count: 0,
            plates: [plate({ quantity_target: 3, dispatched: 0, completed_count: 0, remaining: 3, can_dispatch: false })],
          }),
        ]),
      ),
    );
    render(<BatchOrdersView hasPermission={allow} t={passthroughT} />);

    await waitFor(() => expect(screen.getByText('Widget run')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'queue.cancelBatch' })).toBeInTheDocument();
  });
});
