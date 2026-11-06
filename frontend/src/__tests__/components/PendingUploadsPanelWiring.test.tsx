/**
 * Wiring tests for PendingUploadsPanel: pin the requests sent for archive
 * (with tags/notes/project), discard, archive-all and discard-all, and that
 * cancelling a destructive confirm sends nothing.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PendingUploadsPanel } from '../../components/PendingUploadsPanel';

const uploads = [
  {
    id: 11,
    filename: 'first.gcode.3mf',
    display_name: 'First Upload',
    file_size: 1000,
    source_ip: null,
    status: 'pending',
    tags: null,
    notes: null,
    project_id: null,
    uploaded_at: '2026-05-01T10:00:00Z',
  },
  {
    id: 22,
    filename: 'second.gcode.3mf',
    display_name: 'Second Upload',
    file_size: 2000,
    source_ip: null,
    status: 'pending',
    tags: null,
    notes: null,
    project_id: null,
    uploaded_at: '2026-05-01T10:00:00Z',
  },
];

const projects = [
  { id: 5, name: 'Gadgets', status: 'active', parent_id: null },
  { id: 9, name: 'Archived Proj', status: 'archived', parent_id: null },
];

interface Captured {
  method: string;
  path: string;
  body?: unknown;
}

let calls: Captured[];

beforeEach(() => {
  calls = [];
  server.use(
    http.get('*/api/v1/pending-uploads/', () => HttpResponse.json(uploads)),
    http.get('*/api/v1/projects/', () => HttpResponse.json(projects)),
    http.post('*/api/v1/pending-uploads/archive-all', ({ request }) => {
      calls.push({ method: 'POST', path: new URL(request.url).pathname });
      return HttpResponse.json({ archived: 2, failed: 0 });
    }),
    http.delete('*/api/v1/pending-uploads/discard-all', ({ request }) => {
      calls.push({ method: 'DELETE', path: new URL(request.url).pathname });
      return HttpResponse.json({ discarded: 2 });
    }),
    http.post('*/api/v1/pending-uploads/:id/archive', async ({ request }) => {
      calls.push({
        method: 'POST',
        path: new URL(request.url).pathname,
        body: await request.json(),
      });
      return HttpResponse.json({ id: 100, print_name: 'First Upload', filename: 'first.gcode.3mf' });
    }),
    http.delete('*/api/v1/pending-uploads/:id', ({ request }) => {
      calls.push({ method: 'DELETE', path: new URL(request.url).pathname });
      return HttpResponse.json({ success: true });
    }),
  );
});

/** The row card for an upload, found via its display name. */
async function rowFor(name: string): Promise<HTMLElement> {
  const label = await screen.findByText(name);
  // name <p> -> div -> div.flex gap-3 -> div.flex justify-between (row header)
  const header = label.closest('div.justify-between') as HTMLElement;
  expect(header).not.toBeNull();
  return header;
}

describe('PendingUploadsPanel wiring', () => {
  it('Archive with tags, notes and a project posts them for that upload id only', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('Second Upload');

    // Expand toggle is the first (icon-only) button in the row.
    await user.click(within(row).getAllByRole('button')[0]);
    const card = row.parentElement as HTMLElement;
    await user.type(within(card).getByPlaceholderText('e.g., functional, prototype, gift'), 'gift');
    await user.type(within(card).getByPlaceholderText('Add notes about this print...'), 'for bob');
    const select = within(card).getByRole('combobox');
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Gadgets' })).toBeInTheDocument());
    // Archived projects are not offered as a target.
    expect(within(select).queryByRole('option', { name: 'Archived Proj' })).toBeNull();
    await user.selectOptions(select, '5');

    await user.click(within(row).getByRole('button', { name: /Archive/ }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      method: 'POST',
      path: '/api/v1/pending-uploads/22/archive',
      body: { tags: 'gift', notes: 'for bob', project_id: 5 },
    });
  });

  it('Archive without a project omits project_id', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('First Upload');

    await user.click(within(row).getByRole('button', { name: /Archive/ }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].path).toBe('/api/v1/pending-uploads/11/archive');
    expect(calls[0].body).toEqual({ tags: '', notes: '' });
  });

  it('Discard -> confirm sends DELETE for that upload id', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('Second Upload');

    const buttons = within(row).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]); // trash icon
    expect(await screen.findByText('Discard Upload')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'DELETE', path: '/api/v1/pending-uploads/22' });
  });

  it('Discard -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('Second Upload');

    const buttons = within(row).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]);
    await screen.findByText('Discard Upload');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Discard Upload')).toBeNull());
    expect(calls).toHaveLength(0);
  });

  it('Archive All -> confirm posts archive-all', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Archive All' }));
    const title = await screen.findByText('Archive All Uploads');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Archive All' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'POST', path: '/api/v1/pending-uploads/archive-all' });
  });

  it('Discard All -> confirm sends DELETE discard-all', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Discard All' }));
    const title = await screen.findByText('Discard All Uploads');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Discard All' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'DELETE', path: '/api/v1/pending-uploads/discard-all' });
  });

  it('Discard All -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Discard All' }));
    await screen.findByText('Discard All Uploads');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Discard All Uploads')).toBeNull());
    expect(calls).toHaveLength(0);
  });
});
