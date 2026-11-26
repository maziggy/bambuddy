/**
 * The "To do" list for three waits of the queue (#3615): a filament with none
 * left in the inventory (#3590), a spool that holds too little (#3137) and a
 * plate changer with no plate left (#3575).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import type { QueueTodo, QueueTodoEntry, QueueTodoFilament } from '../../api/client';

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

const job = { id: 10, name: 'Benchy', can_start: false };

function entry(partial: Partial<QueueTodoEntry> & Pick<QueueTodoEntry, 'kind'>): QueueTodoEntry {
  return { jobs: [job], filaments: [], printers: [], detail: null, ...partial };
}

function onPrinter(...entries: QueueTodoEntry[]): QueueTodo {
  return {
    count: entries.length,
    groups: [{ printer_id: 1, printer_name: 'Alpha', target_model: null, entries }],
  };
}

function filament(partial: Partial<QueueTodoFilament>): QueueTodoFilament {
  return { type: 'PLA', color: '#FF0000', color_name: 'Red', no_stock: false, ...partial };
}

beforeEach(() => {
  perms.granted = new Set(['printers:clear_plate', 'queue:update_all', 'queue:read_all']);
});

describe('a filament with none left in the inventory', () => {
  it('is marked on its filament only', () => {
    const todo = onPrinter(
      entry({
        kind: 'load_filament',
        filaments: [filament({ no_stock: true }), filament({ type: 'PETG', color_name: 'Blue', color: '#0000FF' })],
      }),
    );
    render(<QueueTodoList todo={todo} />);
    const [red, blue] = within(screen.getByTestId('todo-entry-load_filament')).getAllByRole('listitem');
    expect(red).toHaveTextContent('PLA Red');
    expect(red).toHaveTextContent('(none left in inventory)');
    expect(blue).toHaveTextContent('PETG Blue');
    expect(blue).not.toHaveTextContent('none left in inventory');
    expect(screen.getByTestId('todo-entry-load_filament')).toHaveTextContent('Load filament');
  });
});

describe('a spool that holds too little', () => {
  const short = (partial: Partial<QueueTodoFilament> = {}) =>
    filament({
      short: true,
      short_trays: [{ ams_id: 0, tray_id: 1, remaining_grams: 20.4 }],
      required_grams: 299.6,
      ...partial,
    });

  it('names the spool, what it has left and what the job needs, and is not a filament to load', () => {
    render(<QueueTodoList todo={onPrinter(entry({ kind: 'load_filament', filaments: [short()] }))} />);
    const row = screen.getByTestId('todo-entry-load_filament');
    expect(row).toHaveTextContent('Replace spool');
    expect(row).not.toHaveTextContent('Load filament');
    expect(screen.getByTestId('todo-short')).toHaveTextContent(
      'Not enough PLA Red: A2 has 20 g, the job needs 300 g',
    );
    // Still a link to the printer, and no start button: the job is not staged.
    expect(within(row).getByRole('link', { name: 'Open printer' })).toHaveAttribute('href', '/printers#printer-card-1');
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('names an AMS-HT and the external holder by their own labels', () => {
    const todo = onPrinter(
      entry({
        kind: 'load_filament',
        filaments: [
          short({ short_trays: [{ ams_id: 128, tray_id: 0, remaining_grams: 5 }] }),
          short({ type: 'PETG', short_trays: [{ ams_id: 255, tray_id: 0, remaining_grams: 7 }] }),
        ],
      }),
    );
    render(<QueueTodoList todo={todo} />);
    const [ht, ext] = screen.getAllByTestId('todo-short');
    expect(ht).toHaveTextContent('Not enough PLA Red: HT-A has 5 g, the job needs 300 g');
    expect(ext).toHaveTextContent('Not enough PETG Red: Ext has 7 g, the job needs 300 g');
  });

  it('leaves out what is not known', () => {
    const todo = onPrinter(
      entry({
        kind: 'load_filament',
        filaments: [
          short({ required_grams: null }),
          short({ type: 'PETG', short_trays: [{ ams_id: 1, tray_id: 0, remaining_grams: null }] }),
          short({ type: 'ABS', short_trays: [] }),
        ],
      }),
    );
    render(<QueueTodoList todo={todo} />);
    const [noNeed, noAmount, noTray] = screen.getAllByTestId('todo-short');
    expect(noNeed).toHaveTextContent('Not enough PLA Red: A2 has 20 g');
    expect(noNeed).not.toHaveTextContent('needs');
    expect(noAmount).toHaveTextContent('Not enough PETG Red in B1');
    expect(noTray).toHaveTextContent('Not enough ABS Red');
  });

  it('keeps "Load filament" for an entry that also misses a filament', () => {
    const todo = onPrinter(entry({ kind: 'load_filament', filaments: [short(), filament({ type: 'PETG' })] }));
    render(<QueueTodoList todo={todo} />);
    const row = screen.getByTestId('todo-entry-load_filament');
    expect(row).toHaveTextContent('Load filament');
    expect(row).not.toHaveTextContent('Replace spool');
    expect(screen.getAllByTestId('todo-short')).toHaveLength(1);
  });

  it('lists the printers of a model job without a tray', () => {
    const todo: QueueTodo = {
      count: 1,
      groups: [
        {
          printer_id: null,
          printer_name: 'X1C',
          target_model: 'X1C',
          entries: [
            entry({
              kind: 'load_filament',
              filaments: [short({ short_trays: [] })],
              printers: [{ id: 5, name: 'X1C-1' }, { id: 6, name: 'X1C-2' }],
            }),
          ],
        },
      ],
    };
    render(<QueueTodoList todo={todo} />);
    const row = screen.getByTestId('todo-entry-load_filament');
    expect(row).toHaveTextContent('Replace spool on X1C-1 or X1C-2');
    expect(screen.getByTestId('todo-short')).toHaveTextContent('Not enough PLA Red');
    expect(screen.getByRole('link', { name: 'X1C-2' })).toHaveAttribute('href', '/printers#printer-card-6');
  });

  it('leaves the start of a staged job to its own entry', () => {
    const todo = onPrinter(
      entry({ kind: 'load_filament', filaments: [short()] }),
      entry({ kind: 'start_staged', jobs: [{ ...job, can_start: true }] }),
    );
    render(<QueueTodoList todo={todo} />);
    expect(within(screen.getByTestId('todo-entry-load_filament')).queryByRole('button')).toBeNull();
    expect(
      within(screen.getByTestId('todo-entry-start_staged')).getByRole('button', { name: 'Start Print' }),
    ).toBeEnabled();
  });
});

describe('a plate changer with no plate left', () => {
  const todo = onPrinter(
    entry({
      kind: 'plate_changer_empty',
      plate_capacity: 5,
      jobs: [job, { id: 11, name: 'Bracket', can_start: false }, { id: 12, name: 'Clip', can_start: false }],
    }),
  );

  function refillRoute(called: () => void, status = 200) {
    return http.post('/api/v1/printers/1/refill-plates', () => {
      called();
      return status === 200
        ? HttpResponse.json({ id: 1, name: 'Alpha', plate_capacity: 5, plates_remaining: 5 })
        : HttpResponse.json({ detail: 'This printer has no plate changer capacity set' }, { status });
    });
  }

  const CONFIRM_TITLE = 'Reset plate changer count?';

  // Opens the confirmation and returns its confirm button (the entry's own button comes first).
  async function openConfirm() {
    fireEvent.click(screen.getByRole('button', { name: 'Refilled / Emptied' }));
    await screen.findByText(CONFIRM_TITLE);
    const buttons = screen.getAllByRole('button', { name: 'Refilled / Emptied' });
    expect(buttons).toHaveLength(2);
    return buttons[1];
  }

  it('is an entry of its own on the printer, with the jobs that wait for it', () => {
    render(<QueueTodoList todo={todo} />);
    const row = screen.getByTestId('todo-entry-plate_changer_empty');
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(row).toHaveTextContent('Plate changer empty');
    expect(row).toHaveTextContent('3 jobs');
    expect(within(row).getByRole('button', { name: 'Refilled / Emptied' })).toBeEnabled();
    // The action replaces the plain link to the printer.
    expect(within(row).queryByRole('link')).toBeNull();
  });

  it('resets the count only after the confirmation', async () => {
    const called = vi.fn();
    server.use(refillRoute(called));
    render(<QueueTodoList todo={todo} />);
    const confirm = await openConfirm();
    expect(screen.getByText(/The count goes back to 5 and the queue sends this printer jobs again/)).toBeInTheDocument();
    expect(called).not.toHaveBeenCalled();
    fireEvent.click(confirm);
    await waitFor(() => expect(called).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('Plate changer count reset to 5')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(CONFIRM_TITLE)).toBeNull());
  });

  it('does nothing when the confirmation is cancelled', async () => {
    const called = vi.fn();
    server.use(refillRoute(called));
    render(<QueueTodoList todo={todo} />);
    await openConfirm();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByText(CONFIRM_TITLE)).toBeNull());
    expect(called).not.toHaveBeenCalled();
  });

  it('shows why the reset failed', async () => {
    server.use(refillRoute(vi.fn(), 400));
    render(<QueueTodoList todo={todo} />);
    fireEvent.click(await openConfirm());
    expect(await screen.findByText('This printer has no plate changer capacity set')).toBeInTheDocument();
  });

  it('needs the permission of the printer card (printers:clear_plate)', () => {
    perms.granted = new Set(['queue:read_all']);
    render(<QueueTodoList todo={todo} />);
    const button = screen.getByRole('button', { name: 'Refilled / Emptied' });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(screen.queryByText(CONFIRM_TITLE)).toBeNull();
  });

  it('comes right after the plate to clear', () => {
    render(
      <QueueTodoList
        todo={onPrinter(
          entry({ kind: 'plate_clear' }),
          entry({ kind: 'plate_changer_empty', plate_capacity: 5 }),
          entry({ kind: 'load_filament', filaments: [filament({})] }),
        )}
      />,
    );
    const kinds = screen.getAllByTestId(/^todo-entry-/).map((el) => el.getAttribute('data-testid'));
    expect(kinds).toEqual(['todo-entry-plate_clear', 'todo-entry-plate_changer_empty', 'todo-entry-load_filament']);
  });
});
