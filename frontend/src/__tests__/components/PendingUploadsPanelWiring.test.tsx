/**
 * Wiring tests for PendingUploadsPanel: pin the requests sent for approving
 * an upload into the File Manager (with tags/notes/project), discard,
 * approve-all and discard-all, and that
 * cancelling a destructive confirm sends nothing.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { PendingUploadsPanel } from '../../components/PendingUploadsPanel';
import { setAuthToken } from '../../api/client';

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
      return HttpResponse.json({ stored: 2, failed: 0 });
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
      return HttpResponse.json({ id: 100, filename: 'first.gcode.3mf', folder_id: 3 });
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
  it('Add to File Manager with tags, notes and a project posts them for that upload id only', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('Second Upload');

    // Expand toggle is the first (icon-only) button in the row.
    await user.click(within(row).getAllByRole('button')[0]);
    const card = row.parentElement as HTMLElement;
    await user.type(within(card).getByPlaceholderText('e.g. functional, prototype, gift'), 'gift');
    await user.type(within(card).getByPlaceholderText('Add notes about this print...'), 'for bob');
    const select = within(card).getByRole('combobox');
    await waitFor(() => expect(within(select).getByRole('option', { name: 'Gadgets' })).toBeInTheDocument());
    // Archived projects are not offered as a target.
    expect(within(select).queryByRole('option', { name: 'Archived Proj' })).toBeNull();
    await user.selectOptions(select, '5');

    await user.click(within(row).getByRole('button', { name: /Add to File Manager/ }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({
      method: 'POST',
      path: '/api/v1/pending-uploads/22/archive',
      body: { tags: 'gift', notes: 'for bob', project_id: 5 },
    });
  });

  it('Add to File Manager without a project omits project_id', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('First Upload');

    await user.click(within(row).getByRole('button', { name: /Add to File Manager/ }));

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
    const discardTitle = await screen.findByText('Discard upload');
    const discardDialog = discardTitle.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(discardDialog).getByRole('button', { name: 'Discard' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'DELETE', path: '/api/v1/pending-uploads/22' });
  });

  it('Discard -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    const row = await rowFor('Second Upload');

    const buttons = within(row).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]);
    await screen.findByText('Discard upload');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Discard upload')).toBeNull());
    expect(calls).toHaveLength(0);
  });

  it('Add all -> confirm posts archive-all', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Add all' }));
    const title = await screen.findByText('Add all uploads');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Add all' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'POST', path: '/api/v1/pending-uploads/archive-all' });
  });

  it('Discard all -> confirm sends DELETE discard-all', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Discard all' }));
    const title = await screen.findByText('Discard all uploads');
    const dialog = title.closest('div.flex-1')?.parentElement?.parentElement as HTMLElement;
    await user.click(within(dialog).getByRole('button', { name: 'Discard all' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ method: 'DELETE', path: '/api/v1/pending-uploads/discard-all' });
  });

  it('Discard all -> cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<PendingUploadsPanel />);
    await screen.findByText('First Upload');

    await user.click(screen.getByRole('button', { name: 'Discard all' }));
    await screen.findByText('Discard all uploads');
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    await waitFor(() => expect(screen.queryByText('Discard all uploads')).toBeNull());
    expect(calls).toHaveLength(0);
  });
});


// Approving adds a File Manager file, so it needs library:upload as well as
// queue:create (the backend checks both); without them the buttons are off.
describe('PendingUploadsPanel permissions', () => {
  function signInWith(permissions: string[]) {
    setAuthToken('test-token-1', 'session');
    server.use(
      http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
      http.get('*/api/v1/auth/me', () =>
        HttpResponse.json({
          id: 9,
          username: 'teacher',
          is_active: true,
          is_admin: false,
          groups: [],
          permissions,
          created_at: '2026-01-01T00:00:00Z',
        }),
      ),
    );
  }

  afterEach(() => {
    setAuthToken(null);
  });

  it.each([
    [['queue:create'], true],
    [['queue:create', 'library:upload'], false],
  ])('with %j the approve buttons are disabled: %s', async (permissions, disabled) => {
    signInWith(permissions);
    render(<PendingUploadsPanel />);
    const row = await rowFor('First Upload');

    await waitFor(() => {
      const button = within(row).getByRole('button', { name: /Add to File Manager/ }) as HTMLButtonElement;
      expect(button.disabled).toBe(disabled);
    });
    expect((screen.getByRole('button', { name: 'Add all' }) as HTMLButtonElement).disabled).toBe(disabled);
  });
});
