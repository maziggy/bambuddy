/**
 * "To do" list of what the queue waits on (#3615): list rendering per kind,
 * merged entries, empty state, nav badge, actions and their permissions.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, render as rtlRender, renderHook } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ToastProvider } from '../../contexts/ToastContext';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import type { QueueTodo } from '../../api/client';

const perms = { granted: new Set<string>() };

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({
      user: { id: 1, username: 'op', permissions: [] },
      authEnabled: true,
      loading: false,
      isAdmin: false,
      hasPermission: (p: string) => perms.granted.has(p),
      hasAnyPermission: (...ps: string[]) => ps.some((p) => perms.granted.has(p)),
      canModify: () => true,
    }),
  };
});

import { QueueTodoList } from '../../components/QueueTodoList';
import { QueueTodoLauncher } from '../../components/QueueTodoLauncher';
import { useQueueTodoAutoRefresh } from '../../hooks/useQueueTodo';

const todo: QueueTodo = {
  count: 6,
  groups: [
    {
      printer_id: 1,
      printer_name: 'Alpha',
      target_model: null,
      entries: [
        { kind: 'plate_clear', jobs: [{ id: 10, name: 'Benchy', can_start: false }], filaments: [], printers: [], detail: null },
        { kind: 'sensor_interlock', jobs: [{ id: 10, name: 'Benchy', can_start: true }], detail: 'Enclosure Door', filaments: [], printers: [] },
        { kind: 'power_on_failed', jobs: [{ id: 10, name: 'Benchy', can_start: false }], filaments: [], printers: [], detail: null },
        {
          kind: 'load_filament',
          jobs: [
            { id: 11, name: 'A', can_start: true },
            { id: 12, name: 'B', can_start: true },
            { id: 13, name: 'C', can_start: true },
          ],
          filaments: [{ type: 'PLA', color: '#FF0000', color_name: 'Red', no_stock: true }],
          printers: [],
          detail: null,
        },
        { kind: 'start_staged', jobs: [{ id: 14, name: 'Staged job', can_start: true }], filaments: [], printers: [], detail: null },
      ],
    },
    {
      printer_id: null,
      printer_name: 'X1C',
      target_model: 'X1C',
      entries: [
        { kind: 'load_filament', jobs: [{ id: 20, name: 'Model job', can_start: true }], filaments: [{ type: 'PETG', color: '#00FF00', color_name: '', no_stock: false }], printers: [{ id: 5, name: 'A1-01' }, { id: 6, name: 'A1-02' }], detail: null },
      ],
    },
  ],
};

beforeEach(() => {
  perms.granted = new Set(['printers:clear_plate', 'queue:update_all', 'queue:read_all']);
});

describe('QueueTodoList', () => {
  it('renders every kind with details, merged job count and model group', () => {
    render(<QueueTodoList todo={todo} />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('Any X1C')).toBeInTheDocument();
    expect(screen.getByTestId('todo-entry-plate_clear')).toBeInTheDocument();
    expect(screen.getByTestId('todo-entry-sensor_interlock')).toHaveTextContent('Enclosure Door');
    expect(screen.getByTestId('todo-entry-power_on_failed')).toBeInTheDocument();
    expect(screen.getByTestId('todo-entry-start_staged')).toHaveTextContent('Staged job');
    // Merged entry: 3 jobs -> a count, one swatch, red "no stock" hint.
    const load = screen.getAllByTestId('todo-entry-load_filament')[0];
    expect(load).toHaveTextContent('3 jobs');
    expect(load).toHaveTextContent('PLA Red');
    expect(load).toHaveTextContent('none left in inventory');
    expect(screen.getAllByTestId('todo-swatch')).toHaveLength(2);
  });

  it('names the nozzle of a dual-nozzle filament, and none otherwise', () => {
    const black = { type: 'PETG', color: '#000000', color_name: 'Black', no_stock: false };
    const dual: QueueTodo = {
      count: 1,
      groups: [{
        printer_id: 1,
        printer_name: 'H2D',
        target_model: null,
        entries: [{
          kind: 'load_filament',
          jobs: [{ id: 30, name: 'Dual', can_start: false }],
          filaments: [{ ...black, nozzle: 0 }, { ...black, nozzle: 1 }, { ...black, nozzle: null }, black],
          printers: [],
          detail: null,
        }],
      }],
    };
    render(<QueueTodoList todo={dual} />);
    expect(screen.getAllByTestId('todo-nozzle').map((n) => n.textContent)).toEqual(['(Right nozzle)', '(Left nozzle)']);
  });

  it('renders nothing when empty', () => {
    const { container } = render(<QueueTodoList todo={{ count: 0, groups: [] }} />);
    expect(container.querySelector('[data-testid="queue-todo-list"]')).toBeNull();
  });

  it('leaves out a printer group without entries', () => {
    const mixed: QueueTodo = {
      count: 1,
      groups: [
        { printer_id: 1, printer_name: 'Empty printer', target_model: null, entries: [] },
        {
          printer_id: 2,
          printer_name: 'Busy printer',
          target_model: null,
          entries: [{ kind: 'plate_clear', jobs: [{ id: 10, name: 'Benchy', can_start: false }], filaments: [], printers: [], detail: null }],
        },
      ],
    };
    const { container } = render(<QueueTodoList todo={mixed} />);
    expect(container.querySelectorAll('section')).toHaveLength(1);
    expect(screen.queryByText('Empty printer')).toBeNull();
    expect(screen.getByText('Busy printer')).toBeTruthy();
  });

  it('links filament/interlock entries to the printer or the queue', () => {
    render(<QueueTodoList todo={todo} />);
    const links = screen.getAllByRole('link');
    expect(links.some((l) => l.getAttribute('href') === '/printers#printer-card-1')).toBe(true);
    expect(links.some((l) => l.getAttribute('href') === '/queue')).toBe(true);
  });

  it('plate is clear calls the clear-plate API', async () => {
    const called = vi.fn();
    server.use(
      http.post('/api/v1/printers/1/clear-plate', () => {
        called();
        return HttpResponse.json({ success: true, message: 'ok' });
      }),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: 'Plate is clear' }));
    await waitFor(() => expect(called).toHaveBeenCalled());
  });

  it('start calls the queue start API', async () => {
    const called = vi.fn();
    server.use(
      http.post('/api/v1/queue/14/start', () => {
        called();
        return HttpResponse.json({ id: 14 });
      }),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: /Start Print/ }));
    await waitFor(() => expect(called).toHaveBeenCalled());
  });

  it('disables plate-clear without permission', () => {
    perms.granted = new Set();
    render(<QueueTodoList todo={todo} />);
    expect(screen.getByRole('button', { name: 'Plate is clear' })).toBeDisabled();
  });

  it('start follows the per-job can_start with the permission reason as tooltip', () => {
    const blocked: QueueTodo = {
      count: 1,
      groups: [{
        printer_id: 1, printer_name: 'Alpha', target_model: null,
        entries: [{ kind: 'start_staged', jobs: [{ id: 14, name: 'Staged job', can_start: false }], filaments: [], printers: [], detail: null }],
      }],
    };
    perms.granted = new Set(['queue:update_own']);
    const { unmount } = render(<QueueTodoList todo={blocked} />);
    const btn = screen.getByRole('button', { name: /Start Print/ });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('title', expect.stringMatching(/Waiting for review/));
    unmount();
    perms.granted = new Set(['queue:update_all']);
    render(<QueueTodoList todo={blocked} />);
    expect(screen.getByRole('button', { name: /Start Print/ })).toHaveAttribute('title', expect.stringMatching(/permission/i));
  });

  it('keeps the translated start failure and adds a 4xx string detail; none for a 5xx', async () => {
    server.use(
      http.post('/api/v1/queue/14/start', () => HttpResponse.json({ detail: 'Printer is busy' }, { status: 500 })),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: /Start Print/ }));
    expect(await screen.findByText('Failed to start print')).toBeInTheDocument();
    expect(screen.queryByText(/Printer is busy/)).toBeNull();
  });

  it('shows the backend error message when starting fails', async () => {
    server.use(
      http.post('/api/v1/queue/14/start', () => HttpResponse.json({ detail: 'Printer is busy' }, { status: 400 })),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: /Start Print/ }));
    expect(await screen.findByText('Failed to start print (Printer is busy)')).toBeInTheDocument();
  });

  it('asks for confirmation on a filament shortage and resends with skip_filament_check', async () => {
    const calls: string[] = [];
    server.use(
      http.post('/api/v1/queue/14/start', ({ request }) => {
        const url = new URL(request.url);
        calls.push(url.search);
        if (url.searchParams.get('skip_filament_check')) return HttpResponse.json({ id: 14 });
        return HttpResponse.json(
          { detail: { code: 'insufficient_filament', message: 'short', deficit: [{ slot_id: 1, required_grams: 50, remaining_grams: 10 }] } },
          { status: 409 },
        );
      }),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: /Start Print/ }));
    fireEvent.click(await screen.findByRole('button', { name: /Print Anyway/i }));
    await waitFor(() => expect(calls).toEqual(['', '?skip_filament_check=true']));
  });

  it('asks for confirmation on an unmatched filament too', async () => {
    server.use(
      http.post('/api/v1/queue/14/start', ({ request }) =>
        new URL(request.url).searchParams.get('skip_filament_check')
          ? HttpResponse.json({ id: 14 })
          : HttpResponse.json({ detail: { code: 'unmatched_filament', message: 'x', missing: ['PLA Red'] } }, { status: 409 }),
      ),
    );
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(screen.getByRole('button', { name: /Start Print/ }));
    expect(await screen.findByText(/PLA Red/)).toBeInTheDocument();
  });

  it('renders a model-group load entry with links to the printers', () => {
    render(<QueueTodoList todo={todo} />);
    const entry = screen.getAllByTestId('todo-entry-load_filament')[1];
    expect(entry).toHaveTextContent('Load filament on A1-01 or A1-02');
    expect(screen.getByRole('link', { name: 'A1-01' })).toHaveAttribute('href', '/printers#printer-card-5');
    expect(screen.getByRole('link', { name: 'A1-02' })).toHaveAttribute('href', '/printers#printer-card-6');
  });

  it('labels a swatch with its colour when the name is empty', () => {
    render(<QueueTodoList todo={todo} />);
    expect(screen.getByLabelText('#00FF00')).toHaveAttribute('title', '#00FF00');
  });

  it('truncates long job names and keeps the full name reachable', () => {
    const long = 'L'.repeat(200);
    const t2: QueueTodo = {
      count: 1,
      groups: [{
        printer_id: 1, printer_name: 'Alpha', target_model: null,
        entries: [{ kind: 'start_staged', jobs: [{ id: 14, name: long, can_start: true }], filaments: [], printers: [], detail: null }],
      }],
    };
    render(<QueueTodoList todo={t2} />);
    const btn = screen.getByRole('button', { name: /Start Print/ });
    expect(btn.className).toMatch(/max-w-full/);
    expect(btn.className).toMatch(/min-w-0/);
    expect(btn.querySelector('span.truncate')).not.toBeNull();
    expect(screen.getByTitle(long)).toBeInTheDocument();
  });

  it('shows the heading when asked and no queue link on /queue', () => {
    rtlRender(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={['/queue']}>
          <ToastProvider><QueueTodoList todo={todo} heading /></ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByRole('heading', { name: 'To do', level: 2 })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Open queue' })).toBeNull();
  });
});

describe('QueueTodoLauncher', () => {
  it('shows the entry count and opens the panel', async () => {
    server.use(http.get('/api/v1/queue/todo', () => HttpResponse.json(todo)));
    render(<QueueTodoLauncher />);
    const badge = await screen.findByTestId('queue-todo-badge');
    expect(badge).toHaveTextContent('6');
    fireEvent.click(badge);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
  });

  it('anchors beside the rail or the open sidebar on a wide screen', async () => {
    server.use(http.get('/api/v1/queue/todo', () => HttpResponse.json(todo)));
    const prev = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: 1600 });
    try {
      for (const [expanded, cls] of [[false, 'md:left-[5rem]'], [true, 'md:left-[17rem]']] as const) {
        const { unmount } = render(<QueueTodoLauncher sidebarExpanded={expanded} />);
        fireEvent.click(await screen.findByTestId('queue-todo-badge'));
        expect(await screen.findByRole('dialog')).toHaveClass(cls);
        unmount();
      }
    } finally {
      Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: prev });
    }
  });

  it('renders no badge when the list is empty', async () => {
    const hit = vi.fn();
    server.use(
      http.get('/api/v1/queue/todo', () => {
        hit();
        return HttpResponse.json({ count: 0, groups: [] });
      }),
    );
    render(<QueueTodoLauncher />);
    await waitFor(() => expect(hit).toHaveBeenCalled());
    expect(screen.queryByTestId('queue-todo-badge')).toBeNull();
  });
});

function withClient(ui: React.ReactElement, client: QueryClient) {
  return rtlRender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ToastProvider>{ui}</ToastProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('QueueTodoLauncher panel behaviour', () => {
  it('is an accessible modal dialog in document.body with focus handling', async () => {
    server.use(http.get('/api/v1/queue/todo', () => HttpResponse.json(todo)));
    const { container } = render(<QueueTodoLauncher />);
    const trigger = (await screen.findByTestId('queue-todo-badge')).closest('button') as HTMLButtonElement;
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('button', { name: 'Close' })).toHaveFocus();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(trigger).toHaveFocus();
  });

  it('does not reopen by itself after the list emptied and filled again', async () => {
    let body: QueueTodo = todo;
    server.use(http.get('/api/v1/queue/todo', () => HttpResponse.json(body)));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    withClient(<QueueTodoLauncher />, client);
    fireEvent.click(await screen.findByTestId('queue-todo-badge'));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    body = { count: 0, groups: [] };
    await client.invalidateQueries({ queryKey: ['queue-todo'] });
    await waitFor(() => expect(screen.queryByTestId('queue-todo-badge')).toBeNull());
    body = todo;
    await client.invalidateQueries({ queryKey: ['queue-todo'] });
    await screen.findByTestId('queue-todo-badge');
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});

describe('useQueueTodoAutoRefresh debounce', () => {
  it('coalesces a burst into one refetch and ignores per-printer card queries', async () => {
    vi.useFakeTimers();
    try {
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const spy = vi.spyOn(client, 'invalidateQueries');
      renderHook(() => useQueueTodoAutoRefresh(), {
        wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
      });
      await client.fetchQuery({ queryKey: ['queue', 3, 'pending'], queryFn: async () => [] });
      await client.fetchQuery({ queryKey: ['queue', 3, 'pending', 'X1C'], queryFn: async () => [] });
      await vi.advanceTimersByTimeAsync(3000);
      expect(spy).not.toHaveBeenCalled();
      await client.fetchQuery({ queryKey: ['queue', 'pending'], queryFn: async () => [] });
      await client.fetchQuery({ queryKey: ['queue', null, 'pending,printing,skipped'], queryFn: async () => [] });
      await client.fetchQuery({ queryKey: ['queue', 'history', {}, 'x', false, 10], queryFn: async () => [] });
      expect(spy).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1600);
      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith({ queryKey: ['queue-todo'] });
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('useQueueTodoAutoRefresh', () => {
  it('invalidates the to-do list when a queue query refetches, not for others', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useQueueTodoAutoRefresh(), {
      wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
    });
    await client.fetchQuery({ queryKey: ['printers'], queryFn: async () => [] });
    expect(spy).not.toHaveBeenCalled();
    await client.fetchQuery({ queryKey: ['queue', 'pending'], queryFn: async () => [] });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ['queue-todo'] }), { timeout: 3000 });
  });
});
