/**
 * Printer access page (#1727): groups reach picked printers plus whole
 * locations, edited by group or by printer and saved together.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PrinterAccessSettings } from '../../components/PrinterAccessSettings';

const printer = (id: number, name: string, location: string | null, model = 'X1C') => ({
  id,
  name,
  location,
  model,
  serial_number: `SN${id}`,
  ip_address: `10.0.0.${id}`,
  is_active: true,
});

const printers = [
  printer(1, 'Alpha', 'Lab A'),
  printer(2, 'Bravo', 'Lab A', 'P1S'),
  printer(3, 'Charlie', 'Lab B'),
  printer(4, 'Loose', null),
];

const group = (id: number, name: string, extra: Record<string, unknown> = {}) => ({
  id,
  name,
  description: null,
  permissions: [],
  is_system: false,
  restrict_printers: false,
  printer_ids: [],
  locations: [],
  user_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...extra,
});

const groups = [
  group(1, 'Administrators', { is_system: true }),
  group(3, 'Viewers', { is_system: true }),
  group(5, 'Team A', { restrict_printers: true, printer_ids: [3], locations: ['Lab A'] }),
  group(6, 'Team Old', { restrict_printers: true, locations: ['Gone'] }),
];

const users = [
  { id: 1, username: 'boss', is_active: true, is_admin: true, groups: [{ id: 1, name: 'Administrators' }] },
  { id: 2, username: 'alice', is_active: true, is_admin: false, groups: [{ id: 3, name: 'Viewers' }, { id: 5, name: 'Team A' }] },
  { id: 3, username: 'bob', is_active: true, is_admin: false, groups: [{ id: 3, name: 'Viewers' }] },
];

let patches: Record<number, Record<string, unknown>>;

beforeEach(() => {
  patches = {};
  server.use(
    http.get('/api/v1/groups/', () => HttpResponse.json(groups)),
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/users/', () => HttpResponse.json(users)),
    http.patch('/api/v1/groups/:id', async ({ request, params }) => {
      const body = (await request.json()) as Record<string, unknown>;
      patches[Number(params.id)] = body;
      return HttpResponse.json({ ...groups.find((g) => g.id === Number(params.id)), ...body });
    })
  );
});

const open = (query: string) => {
  window.history.pushState({}, '', `/settings?tab=users&sub=printer-access${query}`);
  return render(<PrinterAccessSettings />);
};

describe('PrinterAccessSettings by group', () => {
  it('shows what a group reaches through its locations and its picks', async () => {
    open('&group=5');

    expect(await screen.findByText('Members can use 3 of 4 printers.')).toBeInTheDocument();
    // Through Lab A: ticked and fixed
    expect(screen.getByLabelText('Alpha')).toBeChecked();
    expect(screen.getByLabelText('Alpha')).toBeDisabled();
    // Picked on its own
    expect(screen.getByLabelText('Charlie')).toBeChecked();
    expect(screen.getByLabelText('Charlie')).toBeEnabled();
    expect(screen.getByLabelText('Loose')).not.toBeChecked();
  });

  it('gives a whole location and saves it', async () => {
    const user = userEvent.setup();
    open('&group=5');
    await screen.findByText('Members can use 3 of 4 printers.');

    await user.click(screen.getByLabelText('Every printer in Lab B, including printers added there later'));
    await user.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() => expect(patches[5]).toBeDefined());
    expect(patches[5]).toEqual({ restrict_printers: true, printer_ids: [3], locations: ['Lab A', 'Lab B'] });
    expect(patches[6]).toBeUndefined();
  });

  it('ticks only the printers the filters leave', async () => {
    const user = userEvent.setup();
    open('&group=5');
    await screen.findByText('Members can use 3 of 4 printers.');

    await user.type(screen.getByLabelText('Search name, model, serial or location'), 'loose');
    await waitFor(() => expect(screen.queryByLabelText('Charlie')).not.toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Tick all shown' }));
    await user.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() => expect(patches[5]).toBeDefined());
    expect(patches[5].printer_ids).toEqual([3, 4]);
    expect(patches[5].locations).toEqual(['Lab A']);
  });

  it('lists a given location no printer has any more, so it can be removed', async () => {
    const user = userEvent.setup();
    open('&group=6');

    expect(await screen.findByText('Gone')).toBeInTheDocument();
    expect(screen.getByText(/won't see any printer/)).toBeInTheDocument();
    await user.click(screen.getByLabelText('Every printer in Gone, including printers added there later'));
    await user.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() => expect(patches[6]).toBeDefined());
    expect(patches[6].locations).toEqual([]);
  });

  it('filters the group list', async () => {
    const user = userEvent.setup();
    open('');
    await screen.findByText('Team A');

    await user.click(screen.getByRole('button', { name: 'Not limited' }));
    expect(screen.queryByText('Team A')).not.toBeInTheDocument();
    expect(screen.getByText('Viewers')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'All' }));
    await user.type(screen.getByLabelText('Search groups'), 'old');
    expect(screen.getByText('Team Old')).toBeInTheDocument();
    expect(screen.queryByText('Viewers')).not.toBeInTheDocument();
  });

  it('cannot limit Administrators', async () => {
    open('&group=1');
    expect(await screen.findByText('Administrators always see every printer.')).toBeInTheDocument();
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
  });

  it('discards drafts', async () => {
    const user = userEvent.setup();
    open('&group=5');
    await screen.findByText('Members can use 3 of 4 printers.');

    await user.click(screen.getByLabelText('Charlie'));
    expect(screen.getByText('Unsaved changes: Team A')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByText(/Unsaved changes:/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Charlie')).toBeChecked();
  });
});

describe('PrinterAccessSettings by printer', () => {
  it('shows one printer from the card menu, with who reaches it and who sees everything', async () => {
    open('&view=printers&printer=3');

    expect(await screen.findByText('Charlie')).toBeInTheDocument();
    expect(screen.queryByText('Alpha')).not.toBeInTheDocument();
    expect(screen.getByText('Team A')).toBeInTheDocument();
    // bob is in no limited group, so he sees every printer
    expect(await screen.findByText('Users in no limited group: bob')).toBeInTheDocument();
  });

  it('removes a picked printer from a group', async () => {
    const user = userEvent.setup();
    open('&view=printers&printer=3');
    await screen.findByText('Charlie');

    await user.click(screen.getByRole('button', { name: 'Remove access for Team A' }));
    await user.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() => expect(patches[5]).toBeDefined());
    expect(patches[5].printer_ids).toEqual([]);
    expect(patches[5].locations).toEqual(['Lab A']);
  });

  it('gives a group access to a printer', async () => {
    const user = userEvent.setup();
    open('&view=printers&printer=4');
    await screen.findByText('Loose');

    await user.selectOptions(screen.getByLabelText('Give access to…'), 'Team Old');
    await user.click(screen.getByRole('button', { name: /Save/ }));

    await waitFor(() => expect(patches[6]).toBeDefined());
    expect(patches[6].printer_ids).toEqual([4]);
  });

  it('marks access through a location, which is changed per group', async () => {
    open('&view=printers&printer=1');
    await screen.findByText('Alpha');

    expect(screen.getByTitle('Through location Lab A. Change it under By group.')).toHaveTextContent('Team A');
    expect(screen.queryByRole('button', { name: 'Remove access for Team A' })).not.toBeInTheDocument();
  });
});
