/**
 * Admin "Disable 2FA" in the Edit user dialog.
 *
 * The wiki has long told admins to remove a locked-out user's 2FA from Edit
 * user, but there was no control for it, and the client helper sent no body,
 * so the admin password the route requires never reached it.
 */

import { http, HttpResponse } from 'msw';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import userEvent from '@testing-library/user-event';

import { render, screen, waitFor } from '../utils';
import { server } from '../mocks/server';

const mockShowToast = vi.fn();
const mockAuth = {
  user: { id: 1, username: 'admin', auth_source: 'local' } as { id: number; username: string; auth_source: string },
  authEnabled: true,
  isAdmin: true,
};

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({
      ...mockAuth,
      loading: false,
      hasPermission: () => true,
      hasAnyPermission: () => true,
      canModify: () => true,
      login: vi.fn(),
      loginWithToken: vi.fn(),
      logout: vi.fn(),
      refreshAuth: vi.fn(),
    }),
  };
});

vi.mock('../../contexts/ToastContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/ToastContext')>();
  return { ...actual, useToast: () => ({ showToast: mockShowToast }) };
});

import { AdminDisableTwoFactor } from '../../components/AdminDisableTwoFactor';

function captureDelete(response: object = { message: 'ok', had_2fa: true }, status = 200) {
  const calls: { id: string; body: unknown }[] = [];
  server.use(
    http.delete('/api/v1/auth/2fa/admin/:id', async ({ request, params }) => {
      calls.push({ id: String(params.id), body: await request.json() });
      return HttpResponse.json(response, { status });
    }),
  );
  return calls;
}

describe('AdminDisableTwoFactor', () => {
  beforeEach(() => {
    mockShowToast.mockReset();
    mockAuth.user = { id: 1, username: 'admin', auth_source: 'local' };
    mockAuth.isAdmin = true;
    mockAuth.authEnabled = true;
  });

  it('sends the admin password and says the 2FA was removed', async () => {
    const calls = captureDelete();
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    await userEvent.type(screen.getByLabelText('Your password'), 'admin-pass');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ id: '7', body: { admin_password: 'admin-pass' } });
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Two-factor authentication removed for bob', 'success'));
  });

  it('says so when the user had no 2FA', async () => {
    captureDelete({ message: 'ok', had_2fa: false });
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    await userEvent.type(screen.getByLabelText('Your password'), 'admin-pass');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('bob had no two-factor authentication to remove', 'info'));
  });

  it('cannot confirm without a password', async () => {
    const calls = captureDelete();
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));

    expect(screen.getByRole('button', { name: 'Confirm' })).toBeDisabled();
    expect(calls).toHaveLength(0);
  });

  it('asks no password of an admin without a local one', async () => {
    mockAuth.user = { id: 1, username: 'sso-admin', auth_source: 'oidc' };
    const calls = captureDelete();
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    expect(screen.queryByLabelText('Your password')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].body).toEqual({ admin_password: null });
  });

  it('asks for the password when an SSO admin turns out to have one', async () => {
    // An OIDC account can be given a password in Edit user; the route then asks for it.
    mockAuth.user = { id: 1, username: 'sso-admin', auth_source: 'oidc' };
    const calls: unknown[] = [];
    server.use(
      http.delete('/api/v1/auth/2fa/admin/:id', async ({ request }) => {
        const body = (await request.json()) as { admin_password: string | null };
        calls.push(body);
        if (!body.admin_password) return HttpResponse.json({ detail: 'Admin password required' }, { status: 401 });
        return HttpResponse.json({ message: 'ok', had_2fa: true });
      }),
    );
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));
    await userEvent.type(await screen.findByLabelText('Your password'), 'sso-local-pass');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(calls).toEqual([{ admin_password: null }, { admin_password: 'sso-local-pass' }]));
    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith('Two-factor authentication removed for bob', 'success'));
  });

  it('shows the server refusal', async () => {
    captureDelete({ detail: 'Admin password required' }, 401);
    render(<AdminDisableTwoFactor userId={7} username="bob" />);

    await userEvent.click(screen.getByRole('button', { name: 'Disable 2FA' }));
    await userEvent.type(screen.getByLabelText('Your password'), 'wrong');
    await userEvent.click(screen.getByRole('button', { name: 'Confirm' }));

    await waitFor(() => expect(mockShowToast).toHaveBeenCalledWith(expect.stringContaining('Admin password required'), 'error'));
  });

  it('is not offered to a non-admin', () => {
    mockAuth.isAdmin = false;
    render(<AdminDisableTwoFactor userId={7} username="bob" />);
    expect(screen.queryByRole('button', { name: 'Disable 2FA' })).not.toBeInTheDocument();
  });

  it('is not offered on your own account, which has its own 2FA settings', () => {
    render(<AdminDisableTwoFactor userId={1} username="admin" />);
    expect(screen.queryByRole('button', { name: 'Disable 2FA' })).not.toBeInTheDocument();
  });

  it('is not offered with authentication off', () => {
    mockAuth.authEnabled = false;
    render(<AdminDisableTwoFactor userId={7} username="bob" />);
    expect(screen.queryByRole('button', { name: 'Disable 2FA' })).not.toBeInTheDocument();
  });
});
