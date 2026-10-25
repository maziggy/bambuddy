/**
 * Settings > Users: user and group management and "disable authentication"
 * send the request for the row/choice that was clicked, cancelling a
 * destructive dialog sends nothing, and buttons follow the permissions.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { setAuthToken } from '../../api/client';
import { SettingsPage } from '../../pages/SettingsPage';

type Captured = { method: string; path: string; search: string; body: unknown };
let captured: Captured[];
let mePermissions: string[];
let meIsAdmin: boolean;
let itemCounts: { archives: number; queue_items: number; library_files: number };

const USERS = [
  { id: 1, username: 'admin', email: null, role: 'admin', is_admin: true, is_active: true, auth_source: 'local', groups: [{ id: 1, name: 'Administrators' }], created_at: '2026-01-01T00:00:00Z' },
  { id: 2, username: 'alice', email: 'alice@example.com', role: 'user', is_admin: false, is_active: true, auth_source: 'local', groups: [{ id: 4, name: 'Farm crew' }], created_at: '2026-01-01T00:00:00Z' },
  { id: 3, username: 'bob', email: null, role: 'user', is_admin: false, is_active: true, auth_source: 'local', groups: [], created_at: '2026-01-01T00:00:00Z' },
];

const GROUPS = [
  { id: 1, name: 'Administrators', description: null, is_system: true, permissions: [], user_count: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { id: 4, name: 'Farm crew', description: null, is_system: false, permissions: [], user_count: 1, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
  { id: 6, name: 'Night shift', description: null, is_system: false, permissions: [], user_count: 0, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z' },
];

async function capture(request: Request) {
  const text = await request.text();
  const url = new URL(request.url);
  captured.push({ method: request.method, path: url.pathname, search: url.search, body: text ? JSON.parse(text) : null });
}

const writes = () => captured.filter((c) => c.method !== 'GET');

function userRow(username: string): HTMLElement {
  const name = screen
    .getAllByText(username)
    .find((el) => el.tagName === 'P' && el.className.includes('truncate') && el.closest('div.py-3'));
  if (!name) throw new Error(`user row ${username} not found`);
  return name.closest('div.py-3') as HTMLElement;
}

function groupRow(name: string): HTMLElement {
  const label = screen
    .getAllByText(name)
    .find((el) => el.tagName === 'SPAN' && el.className === 'text-white font-medium' && el.closest('div.py-3'));
  if (!label) throw new Error(`group row ${name} not found`);
  return label.closest('div.py-3') as HTMLElement;
}

async function openUsersTab() {
  window.history.replaceState({}, '', '/settings?tab=users');
  render(<SettingsPage />);
  await screen.findByText('alice');
}

beforeEach(() => {
  captured = [];
  meIsAdmin = true;
  mePermissions = ['settings:read', 'users:read', 'users:create', 'users:update', 'users:delete', 'groups:read', 'groups:delete'];
  itemCounts = { archives: 2, queue_items: 0, library_files: 1 };
  setAuthToken('test-token', 'session');
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
    http.get('*/api/v1/auth/me', () =>
      HttpResponse.json({ ...USERS[0], is_admin: meIsAdmin, permissions: mePermissions }),
    ),
    http.get('*/api/v1/users/', () => HttpResponse.json(USERS)),
    http.get('*/api/v1/groups/', () => HttpResponse.json(GROUPS)),
    http.get('*/api/v1/users/:id/items-count', () => HttpResponse.json(itemCounts)),
    http.post('*/api/v1/users/', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(USERS[2]);
    }),
    http.patch('*/api/v1/users/:id', async ({ request }) => {
      await capture(request);
      return HttpResponse.json(USERS[1]);
    }),
    http.delete('*/api/v1/users/:id', async ({ request }) => {
      await capture(request);
      return new HttpResponse(null, { status: 204 });
    }),
    http.delete('*/api/v1/groups/:id', async ({ request }) => {
      await capture(request);
      return new HttpResponse(null, { status: 204 });
    }),
    http.post('*/api/v1/auth/disable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok', auth_enabled: false });
    }),
  );
});

afterEach(() => {
  setAuthToken(null);
  window.history.replaceState({}, '', '/');
});

describe('SettingsPage users & groups wiring', () => {
  it('create user sends username, password and the ticked group ids', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    await user.click(screen.getByRole('button', { name: /add user/i }));
    await user.type(screen.getByPlaceholderText('Enter username'), 'carol');
    await user.type(screen.getByPlaceholderText('Enter password'), 'Test-password-1');
    await user.type(screen.getByPlaceholderText('Confirm password'), 'Test-password-1');
    const modal = screen.getByPlaceholderText('Enter username').closest('div.space-y-3') as HTMLElement;
    await user.click(within(modal).getByRole('checkbox', { name: /night shift/i }));
    await user.click(screen.getByRole('button', { name: /create user/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/users/' });
    expect(writes()[0].body).toEqual({
      username: 'carol',
      password: 'Test-password-1',
      role: 'user',
      group_ids: [6],
    });
  });

  it('create user is blocked while the passwords differ', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    await user.click(screen.getByRole('button', { name: /add user/i }));
    await user.type(screen.getByPlaceholderText('Enter username'), 'carol');
    await user.type(screen.getByPlaceholderText('Enter password'), 'Test-password-1');
    await user.type(screen.getByPlaceholderText('Confirm password'), 'Test-password-2');
    const create = screen.getByRole('button', { name: /create user/i });
    expect(create).toBeDisabled();
    await user.click(create);
    expect(writes()).toHaveLength(0);
  });

  it('edit user PATCHes that user without a password when it is left blank', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    const [editBtn] = within(userRow('alice')).getAllByRole('button');
    await user.click(editBtn);
    const name = await screen.findByDisplayValue('alice');
    await user.clear(name);
    await user.type(name, 'alice2');
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'PATCH', path: '/api/v1/users/2' });
    expect(writes()[0].body).toEqual({
      username: 'alice2',
      email: 'alice@example.com',
      role: 'user',
      group_ids: [4],
    });
    expect(writes()[0].body).not.toHaveProperty('password');
  });

  it('edit user with the email field emptied sends an empty email, which removes it', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    const [editBtn] = within(userRow('alice')).getAllByRole('button');
    await user.click(editBtn);
    await user.clear(await screen.findByDisplayValue('alice@example.com'));
    await user.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'PATCH', path: '/api/v1/users/2' });
    expect(writes()[0].body).toMatchObject({ email: '' });
  });

  it('delete user with items: "delete items" choice sends delete_items=true for that user', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    const buttons = within(userRow('bob')).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]);
    await user.click(await screen.findByRole('button', { name: /delete user and their items/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/users/3', search: '?delete_items=true' });
  });

  it('delete user with items: "keep items" choice sends delete_items=false', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    const buttons = within(userRow('alice')).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]);
    await user.click(await screen.findByRole('button', { name: /keep items/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/users/2', search: '?delete_items=false' });
  });

  it('cancelling the delete-user dialog sends nothing', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    const buttons = within(userRow('bob')).getAllByRole('button');
    await user.click(buttons[buttons.length - 1]);
    await screen.findByRole('button', { name: /delete user and their items/i });
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));

    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /delete user and their items/i })).not.toBeInTheDocument(),
    );
    expect(writes()).toHaveLength(0);
  });

  it('the signed-in user has no delete button on their own row', async () => {
    await openUsersTab();
    // Only the edit button
    expect(within(userRow('admin')).getAllByRole('button')).toHaveLength(1);
  });

  it('delete group asks first, then DELETEs that group; cancel sends nothing', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    // System groups: edit only, no delete button
    expect(within(groupRow('Administrators')).getAllByRole('button')).toHaveLength(1);
    const deleteNightShift = () => {
      const buttons = within(groupRow('Night shift')).getAllByRole('button');
      expect(buttons).toHaveLength(2);
      return buttons[1];
    };

    await user.click(deleteNightShift());
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(writes()).toHaveLength(0);

    await user.click(deleteNightShift());
    await user.click(screen.getByRole('button', { name: /^delete group$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'DELETE', path: '/api/v1/groups/6' });
  });

  it('disable authentication asks first; cancel sends nothing, confirm POSTs /auth/disable', async () => {
    const user = userEvent.setup();
    await openUsersTab();

    await user.click(await screen.findByRole('button', { name: /^disable$/i }));
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(writes()).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /^disable$/i }));
    await user.click(screen.getByRole('button', { name: /^disable authentication$/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/auth/disable' });
  });

  it('reset password (advanced auth) POSTs {user_id} of the user being edited', async () => {
    server.use(
      http.get('*/api/v1/auth/advanced-auth/status', () =>
        HttpResponse.json({ advanced_auth_enabled: true, smtp_configured: true, local_login_enabled: true, autologin_provider_id: null }),
      ),
      http.post('*/api/v1/auth/reset-password', async ({ request }) => {
        await capture(request);
        return HttpResponse.json({ message: 'sent' });
      }),
    );
    const user = userEvent.setup();
    await openUsersTab();

    const [editBtn] = within(userRow('alice')).getAllByRole('button');
    await user.click(editBtn);
    await user.click(await screen.findByRole('button', { name: /reset password/i }));

    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ method: 'POST', path: '/api/v1/auth/reset-password', body: { user_id: 2 } });
  });

  it('without create/delete permissions there is no add-user, delete-user, delete-group or disable-auth button', async () => {
    meIsAdmin = false;
    mePermissions = ['settings:read', 'users:read', 'users:update', 'groups:read'];
    await openUsersTab();

    expect(screen.queryByRole('button', { name: /add user/i })).not.toBeInTheDocument();
    // Edit only
    expect(within(userRow('bob')).getAllByRole('button')).toHaveLength(1);
    expect(within(groupRow('Night shift')).queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryByRole('button', { name: /^disable$/i })).not.toBeInTheDocument();
  });
});
