/**
 * The "To do" tab of the Queue page (#3615): its place and count in the tab
 * strip, the list inside the tab instead of above the queue, the empty text,
 * the ?tab=todo link, and the navigation button next to it.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { QueuePage } from '../../pages/QueuePage';
import { QueueTodoLauncher } from '../../components/QueueTodoLauncher';
import type { QueueTodo } from '../../api/client';

const pendingJob = {
  id: 11,
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
  printer_name: 'Alpha',
  print_time_seconds: 3600,
  batch_id: null,
  batch_name: null,
  created_by_id: null,
};

const todo: QueueTodo = {
  count: 3,
  groups: [
    {
      printer_id: 1,
      printer_name: 'Alpha',
      target_model: null,
      entries: [
        { kind: 'plate_clear', jobs: [{ id: 11, name: 'Pending Job', can_start: false }], filaments: [], printers: [], detail: null },
        { kind: 'power_on_failed', jobs: [{ id: 11, name: 'Pending Job', can_start: false }], filaments: [], printers: [], detail: null },
      ],
    },
    {
      printer_id: null,
      printer_name: 'X1C',
      target_model: 'X1C',
      entries: [
        { kind: 'sensor_interlock', jobs: [{ id: 20, name: 'Model job', can_start: true }], filaments: [], printers: [], detail: 'Enclosure Door' },
      ],
    },
  ],
};

const tabStrip = () => screen.getByRole('button', { name: /^Batches/ }).parentElement as HTMLElement;
const tabNames = () => within(tabStrip()).getAllByRole('button').map((b) => b.textContent);

beforeEach(() => {
  vi.mocked(localStorage.getItem).mockImplementation(() => null);
  server.use(
    http.get('*/api/v1/queue/', () => HttpResponse.json([pendingJob])),
    http.get('*/api/v1/queue/history', () => HttpResponse.json({ items: [], total: 0, locations: [] })),
    http.get('*/api/v1/queue/todo', () => HttpResponse.json(todo)),
    http.get('*/api/v1/printers/', () => HttpResponse.json([{ id: 1, name: 'Alpha', model: 'X1C', enabled: true }])),
  );
});

afterEach(() => {
  window.history.replaceState({}, '', '/');
});

describe('QueuePage To do tab', () => {
  it('sits between Queue and Batches and shows the number of open entries', async () => {
    render(<QueuePage />);
    await waitFor(() => expect(tabNames().slice(0, 3)).toEqual(['Queue1', 'To do3', 'Batches']));
  });

  it('shows no count while nothing is to do', async () => {
    server.use(http.get('*/api/v1/queue/todo', () => HttpResponse.json({ count: 0, groups: [] })));
    render(<QueuePage />);
    await waitFor(() => expect(tabNames().slice(0, 3)).toEqual(['Queue1', 'To do', 'Batches']));
  });

  it('opens on the Queue tab without the list, and lists the entries in the To do tab', async () => {
    render(<QueuePage />);
    await screen.findByRole('button', { name: 'To do 3' });
    await screen.findByRole('button', { name: 'Queue 1' });
    expect(screen.queryByTestId('queue-todo-list')).toBeNull();
    // The queue's filters are there on the Queue tab.
    expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: 'To do 3' }));
    const list = await screen.findByTestId('queue-todo-list');
    expect(within(list).getByText('Alpha')).toBeInTheDocument();
    expect(within(list).getByText('Any X1C')).toBeInTheDocument();
    expect(within(list).getByTestId('todo-entry-plate_clear')).toBeInTheDocument();
    // The tab shows the list alone, without the queue's filters.
    expect(screen.queryByRole('combobox')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Queue 1' }));
    await waitFor(() => expect(screen.queryByTestId('queue-todo-list')).toBeNull());
  });

  it('"Open queue" on an entry without a printer shows the Queue tab', async () => {
    render(<QueuePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'To do 3' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Open queue' }));
    await waitFor(() => expect(screen.queryByTestId('queue-todo-list')).toBeNull());
    expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0);
  });

  it('says so when nothing is to do', async () => {
    server.use(http.get('*/api/v1/queue/todo', () => HttpResponse.json({ count: 0, groups: [] })));
    render(<QueuePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'To do' }));
    expect(await screen.findByTestId('queue-todo-empty')).toHaveTextContent('Nothing to do');
    expect(screen.queryByTestId('queue-todo-list')).toBeNull();
  });

  it('opens from /queue?tab=todo', async () => {
    window.history.replaceState({}, '', '/queue?tab=todo');
    render(<QueuePage />);
    expect(await screen.findByTestId('queue-todo-list')).toBeInTheDocument();
    await screen.findByRole('button', { name: 'Queue 1' });
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('does not come back as the remembered tab', async () => {
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => (key === 'queue.activeTab' ? 'todo' : null));
    render(<QueuePage />);
    await screen.findByRole('button', { name: 'To do 3' });
    expect(screen.queryByTestId('queue-todo-list')).toBeNull();
    expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0);
  });

  it('keeps the navigation button and its panel, with the same count as the tab', async () => {
    render(
      <>
        <QueueTodoLauncher />
        <QueuePage />
      </>,
    );
    const badge = await screen.findByTestId('queue-todo-badge');
    expect(badge).toHaveTextContent('3');
    await screen.findByRole('button', { name: 'To do 3' });
    expect(screen.queryByTestId('queue-todo-list')).toBeNull();

    fireEvent.click(badge);
    const dialog = await screen.findByRole('dialog', { name: 'To do' });
    expect(within(dialog).getByTestId('queue-todo-list')).toBeInTheDocument();
    expect(within(dialog).getByText('Alpha')).toBeInTheDocument();
    // The panel is not the page: no button that switches the tab.
    expect(within(dialog).queryByRole('button', { name: 'Open queue' })).toBeNull();
  });
});
