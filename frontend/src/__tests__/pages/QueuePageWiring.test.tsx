/**
 * Wiring tests for QueuePage: pin the exact requests behind the row and
 * bulk actions (stop / cancel / remove / start / clear history / group /
 * ungroup / resume after failure), that cancelling a destructive confirm
 * sends nothing, and the permission gates in front of them.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { QueuePage } from '../../pages/QueuePage';
import { setAuthToken } from '../../api/client';

const baseItem = {
  id: 1,
  printer_id: 1,
  archive_id: 1,
  position: 1,
  status: 'pending',
  scheduled_time: null,
  require_previous_success: false,
  auto_off_after: false,
  manual_start: false,
  ams_mapping: null,
  plate_id: null,
  bed_levelling: 'on',
  flow_cali: 'off',
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
  use_ams: true,
  started_at: null,
  completed_at: null,
  error_message: null,
  created_at: '2024-01-01T00:00:00Z',
  archive_name: 'Pending Job',
  archive_thumbnail: null,
  printer_name: 'Test Printer',
  print_time_seconds: 3600,
  batch_id: null as number | null,
  batch_name: null as string | null,
  created_by_id: null as number | null,
};

const pendingA = { ...baseItem, id: 11, position: 1, archive_name: 'Pending A' };
const pendingB = { ...baseItem, id: 12, position: 2, archive_name: 'Pending B' };
const pendingC = { ...baseItem, id: 13, position: 3, archive_name: 'Pending C' };
const printing = {
  ...baseItem,
  id: 21,
  position: 0,
  status: 'printing',
  started_at: '2024-01-01T10:00:00Z',
  archive_name: 'Running Job',
};
const completedA = {
  ...baseItem,
  id: 31,
  status: 'completed',
  completed_at: '2024-01-01T09:00:00Z',
  archive_name: 'Done A',
};
const completedB = { ...completedA, id: 32, archive_name: 'Done B' };

const printers = [
  {
    id: 1,
    name: 'Test Printer',
    ip_address: '192.0.2.100',
    serial_number: 'TESTSERIAL0001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    created_at: '2024-01-01T00:00:00Z',
  },
];

interface Captured {
  method: string;
  path: string;
  search: string;
  body?: unknown;
}

let calls: Captured[];

const capture = async (request: Request) => {
  const url = new URL(request.url);
  let body: unknown;
  if (request.method !== 'GET' && request.method !== 'DELETE') {
    const text = await request.text();
    body = text ? JSON.parse(text) : undefined;
  }
  calls.push({ method: request.method, path: url.pathname, search: url.search, body });
};

const mutationCalls = () => calls.filter((c) => c.method !== 'GET');

// As the server splits them: skipped jobs are in both, the queue keeps them
// for the "blocked by a previous failure" banner.
const QUEUE_STATUSES = ['pending', 'printing', 'skipped'];
const HISTORY_STATUSES = ['completed', 'failed', 'skipped', 'cancelled'];

/** The queue as the page loads it: active jobs from /queue/, finished ones as one history page. */
function useQueue(items: { status: string }[]) {
  const history = items.filter((item) => HISTORY_STATUSES.includes(item.status));
  server.use(
    http.get('*/api/v1/queue/', () => HttpResponse.json(items.filter((item) => QUEUE_STATUSES.includes(item.status)))),
    http.get('*/api/v1/queue/history', () => HttpResponse.json({ items: history, total: history.length, locations: [] })),
  );
}

beforeEach(() => {
  calls = [];
  vi.mocked(localStorage.getItem).mockImplementation((key: string) => {
    if (key === 'queue.historyCollapsed') return 'false';
    if (key === 'queue.viewMode') return 'list';
    return null;
  });
  useQueue([pendingA, printing, completedA]);
  server.use(
    http.get('*/api/v1/printers/', () => HttpResponse.json(printers)),
    http.post('*/api/v1/queue/history/clear', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ cleared: 2, kept: 0 });
    }),
    http.delete('*/api/v1/queue/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok', deleted: true });
    }),
    http.post('*/api/v1/queue/batches/:id/ungroup', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ ungrouped_count: 2, message: 'ok' });
    }),
    http.post('*/api/v1/queue/batches', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ id: 77, name: 'x', status: 'pending' });
    }),
    http.post('*/api/v1/queue/printer/:pid/resume', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ acknowledged: 1, restored: 2 });
    }),
    http.post('*/api/v1/queue/:id/cancel', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.post('*/api/v1/queue/:id/stop', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.post('*/api/v1/queue/:id/start', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ ...pendingA, manual_start: false });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
});

/** Selection checkbox of the pending row showing ``name``. */
function selectBoxFor(name: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(name);
  while (el) {
    const boxes = el.querySelectorAll<HTMLElement>('button.w-6.h-6.rounded.border');
    if (boxes.length === 1) return boxes[0];
    if (boxes.length > 1) break;
    el = el.parentElement;
  }
  throw new Error(`no selection box for ${name}`);
}

/** The dialog card wrapping a ConfirmModal title. */
function dialogOf(title: HTMLElement): HTMLElement {
  return title.closest('div.fixed') as HTMLElement;
}

describe('QueuePage wiring: row actions', () => {
  it('Stop on a printing item -> confirm -> POST /queue/{id}/stop for that item', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Running Job');

    await user.click(screen.getByTitle('Stop Print'));
    const dialog = dialogOf(await screen.findByText(/stop the current print "Running Job"/));
    await user.click(within(dialog).getByRole('button', { name: 'Stop Print' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'POST', path: '/api/v1/queue/21/stop' });
  });

  it('Stop -> cancel the confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Running Job');

    await user.click(screen.getByTitle('Stop Print'));
    const dialog = dialogOf(await screen.findByText(/stop the current print "Running Job"/));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText(/stop the current print/)).toBeNull());
    expect(mutationCalls()).toHaveLength(0);
  });

  it('Cancel on a pending item -> confirm -> POST /queue/{id}/cancel', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending A');

    await user.click(screen.getByTitle('Cancel'));
    const dialog = dialogOf(await screen.findByText('Are you sure you want to cancel "Pending A"?'));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel Print' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'POST', path: '/api/v1/queue/11/cancel' });
  });

  it('Cancel on a pending item -> dismiss the confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending A');

    await user.click(screen.getByTitle('Cancel'));
    const dialog = dialogOf(await screen.findByText('Are you sure you want to cancel "Pending A"?'));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText(/want to cancel "Pending A"/)).toBeNull());
    expect(mutationCalls()).toHaveLength(0);
  });

  it('Remove on a history item -> confirm -> DELETE /queue/{id}', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /^History/ }));
    await screen.findByText('Done A');

    await user.click(screen.getByTitle('Remove'));
    const dialog = dialogOf(await screen.findByText(/remove "Done A" from the queue history/));
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/queue/31' });
  });

  it('Remove -> cancel the confirm sends nothing', async () => {
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /^History/ }));
    await screen.findByText('Done A');

    await user.click(screen.getByTitle('Remove'));
    const dialog = dialogOf(await screen.findByText(/remove "Done A" from the queue history/));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText(/from the queue history/)).toBeNull());
    expect(mutationCalls()).toHaveLength(0);
  });

  it('Clear History -> confirm sends one clear for the history, nothing else', async () => {
    useQueue([pendingA, completedA, completedB]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /^History/ }));
    await screen.findByText('Done B');

    await user.click(screen.getByRole('button', { name: /clear history/i }));
    const dialog = dialogOf(await screen.findByText(/remove all 2 item\(s\) from the history/));
    await user.click(within(dialog).getByRole('button', { name: 'Clear History' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'POST', path: '/api/v1/queue/history/clear' });
  });

  it('Start Print on a staged item -> POST /queue/{id}/start without skipping the filament check', async () => {
    useQueue([{ ...pendingA, manual_start: true }]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending A');

    await user.click(screen.getByTitle('Start Print'));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({
      method: 'POST',
      path: '/api/v1/queue/11/start',
      search: '',
    });
  });
});

describe('QueuePage wiring: batches', () => {
  it('Group as batch sends createBatch with the typed name and exactly the selected ids', async () => {
    useQueue([pendingA, pendingB, pendingC]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending C');

    await user.click(selectBoxFor('Pending A'));
    await user.click(selectBoxFor('Pending C'));
    await user.click(await screen.findByTitle('Group as batch…'));

    const input = await screen.findByPlaceholderText(/./, { selector: 'input[maxlength="120"]' });
    await user.clear(input);
    await user.type(input, 'Weekend run');
    await user.click(screen.getByRole('button', { name: 'Create batch' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    const call = mutationCalls()[0];
    expect(call).toMatchObject({ method: 'POST', path: '/api/v1/queue/batches' });
    expect(call.body).toEqual({ name: 'Weekend run', item_ids: [11, 13] });
  });

  it('Group as batch is not offered for a single selected item', async () => {
    useQueue([pendingA, pendingB]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending B');

    await user.click(selectBoxFor('Pending A'));
    await screen.findByText('1 selected');
    expect(screen.queryByTitle('Group as batch…')).toBeNull();
  });

  it('Ungroup -> confirm -> POST /queue/batches/{id}/ungroup', async () => {
    useQueue([
      { ...pendingA, batch_id: 55, batch_name: 'Grouped' },
      { ...pendingB, batch_id: 55, batch_name: 'Grouped' },
    ]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Grouped');

    await user.click(screen.getByTitle('Ungroup'));
    const dialog = dialogOf(await screen.findByText('Ungroup batch?'));
    await user.click(within(dialog).getByRole('button', { name: 'Ungroup' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'POST', path: '/api/v1/queue/batches/55/ungroup' });
  });

  it('Ungroup -> cancel sends nothing', async () => {
    useQueue([
      { ...pendingA, batch_id: 55, batch_name: 'Grouped' },
      { ...pendingB, batch_id: 55, batch_name: 'Grouped' },
    ]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Grouped');

    await user.click(screen.getByTitle('Ungroup'));
    const dialog = dialogOf(await screen.findByText('Ungroup batch?'));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Ungroup batch?')).toBeNull());
    expect(mutationCalls()).toHaveLength(0);
  });
});

describe('QueuePage wiring: resume after failure', () => {
  const skipped = (id: number, printerId: number, printerName: string) => ({
    ...baseItem,
    id,
    printer_id: printerId,
    printer_name: printerName,
    status: 'skipped',
    error_message: 'Previous print failed or was aborted',
    archive_name: `Skipped ${id}`,
  });

  it('Resume after failure -> confirm -> POST /queue/printer/{printerId}/resume for that printer', async () => {
    useQueue([pendingA, skipped(41, 1, 'Alpha'), skipped(42, 7, 'Bravo'), skipped(43, 7, 'Bravo')]);
    const user = userEvent.setup();
    render(<QueuePage />);

    const banner = (await screen.findByText(/Bravo is blocked by a previous-print failure/)).closest(
      'div.rounded-lg',
    ) as HTMLElement;
    await user.click(within(banner).getByRole('button', { name: /Resume after failure/ }));
    const dialog = dialogOf(await screen.findByText('Resume queue after failure?'));
    await user.click(within(dialog).getByRole('button', { name: 'Resume after failure' }));

    await waitFor(() => expect(mutationCalls()).toHaveLength(1));
    expect(mutationCalls()[0]).toMatchObject({ method: 'POST', path: '/api/v1/queue/printer/7/resume' });
  });

  it('Resume after failure -> cancel sends nothing', async () => {
    useQueue([pendingA, skipped(42, 7, 'Bravo')]);
    const user = userEvent.setup();
    render(<QueuePage />);

    await user.click(await screen.findByRole('button', { name: /Resume after failure/ }));
    const dialog = dialogOf(await screen.findByText('Resume queue after failure?'));
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Resume queue after failure?')).toBeNull());
    expect(mutationCalls()).toHaveLength(0);
  });
});

describe('QueuePage wiring: permission gates', () => {
  const signInWith = (permissions: string[], userId = 7) => {
    setAuthToken('test-token', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: true, requires_setup: false }),
      ),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({ id: userId, username: 'member', is_admin: false, permissions }),
      ),
    );
  };

  it('Clear History is disabled without queue:delete_all and sends nothing when clicked', async () => {
    signInWith(['queue:read', 'queue:delete_own']);
    useQueue([completedA]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /^History/ }));
    await screen.findByText('Done A');

    const clear = screen.getByRole('button', { name: /clear history/i });
    expect(clear).toBeDisabled();
    await user.click(clear);
    expect(screen.queryByText(/from the history\?/)).toBeNull();
    expect(mutationCalls()).toHaveLength(0);
  });

  it('Clear History is enabled with queue:delete_all', async () => {
    signInWith(['queue:read', 'queue:delete_all']);
    useQueue([completedA]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await user.click(await screen.findByRole('button', { name: /^History/ }));
    await screen.findByText('Done A');

    await waitFor(() => expect(screen.getByRole('button', { name: /clear history/i })).toBeEnabled());
  });

  it("Stop is disabled on someone else's print without queue:update_all", async () => {
    signInWith(['queue:read', 'queue:update_own'], 7);
    useQueue([{ ...printing, created_by_id: 99 }]);
    render(<QueuePage />);
    await screen.findByText('Running Job');

    const stop = await screen.findByTitle('You do not have permission to stop prints');
    expect(stop).toBeDisabled();
  });

  it('Stop stays enabled on the user\'s own print with queue:update_own', async () => {
    signInWith(['queue:read', 'queue:update_own'], 7);
    useQueue([{ ...printing, created_by_id: 7 }]);
    render(<QueuePage />);
    await screen.findByText('Running Job');

    await waitFor(() => expect(screen.getByTitle('Stop Print')).toBeEnabled());
  });

  it('Reorder arrows are hidden without queue:reorder and shown with it', async () => {
    signInWith(['queue:read']);
    useQueue([pendingA, pendingB]);
    const { unmount } = render(<QueuePage />);
    await screen.findByText('Pending B');
    expect(screen.queryByTitle('Move Down')).toBeNull();
    unmount();

    signInWith(['queue:read', 'queue:reorder']);
    render(<QueuePage />);
    await screen.findByText('Pending B');
    await waitFor(() => expect(screen.getAllByTitle('Move Down')).toHaveLength(2));
  });

  it('Resume-after-failure banner is hidden without queue:update_all', async () => {
    signInWith(['queue:read', 'queue:update_own']);
    useQueue([
      pendingA,
      { ...baseItem, id: 42, status: 'skipped', error_message: 'Previous print failed or was aborted' },
    ]);
    render(<QueuePage />);
    await screen.findByText('Pending A');
    expect(screen.queryByRole('button', { name: /Resume after failure/ })).toBeNull();
  });

  it('Bulk edit disables auto power off without printers:control', async () => {
    signInWith(['queue:read', 'queue:update_all']);
    useQueue([pendingA]);
    const user = userEvent.setup();
    render(<QueuePage />);
    await screen.findByText('Pending A');

    await user.click(screen.getByText('Select All'));
    await user.click(await screen.findByTitle('Edit Selected'));
    const row = (await screen.findByText('Auto power off after print')).closest('div') as HTMLElement;
    for (const btn of within(row).getAllByRole('button')) {
      expect(btn).toBeDisabled();
    }
  });
});
