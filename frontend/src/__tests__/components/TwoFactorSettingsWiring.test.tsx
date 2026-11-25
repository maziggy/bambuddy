/**
 * Account 2FA settings: each action sends the code/password the user typed
 * to the right endpoint, a rejected code does not advance the wizard, new
 * backup codes are shown once, and unlinking SSO targets the clicked
 * provider.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';

const mockUseAuth = {
  authEnabled: true,
  loading: false,
  user: { id: 3, username: 'tester', email: 'tester@example.com', is_admin: false, permissions: [] },
  hasPermission: () => true,
};
vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return { ...actual, useAuth: () => mockUseAuth };
});

import { TwoFactorSettings } from '../../components/TwoFactorSettings';

type Captured = { method: string; path: string; body: Record<string, unknown> | null };
let captured: Captured[];
let twoFaStatus: { totp_enabled: boolean; email_otp_enabled: boolean; backup_codes_remaining: number };
let totpEnableOk: boolean;

async function capture(request: Request) {
  const text = await request.text();
  captured.push({
    method: request.method,
    path: new URL(request.url).pathname,
    body: text ? JSON.parse(text) : null,
  });
}

const sent = (suffix: string) => captured.filter((c) => c.path.endsWith(suffix));

beforeEach(() => {
  captured = [];
  totpEnableOk = true;
  twoFaStatus = { totp_enabled: false, email_otp_enabled: false, backup_codes_remaining: 0 };
  server.use(
    http.get('*/api/v1/auth/2fa/status', () => HttpResponse.json(twoFaStatus)),
    http.get('*/api/v1/auth/oidc/links', () =>
      HttpResponse.json([
        { id: 100, provider_id: 5, provider_name: 'Alpha IdP', provider_email: 'a@idp.example', created_at: '2026-01-01T00:00:00Z' },
        { id: 101, provider_id: 8, provider_name: 'Beta IdP', provider_email: 'b@idp.example', created_at: '2026-01-01T00:00:00Z' },
      ]),
    ),
    http.post('*/api/v1/auth/2fa/totp/setup', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ secret: 'TESTSECRETBASE32', qr_code_b64: 'AAAA', provisioning_uri: 'otpauth://totp/x' });
    }),
    http.post('*/api/v1/auth/2fa/totp/enable', async ({ request }) => {
      await capture(request);
      return totpEnableOk
        ? HttpResponse.json({ message: 'ok', backup_codes: ['AAAA1111', 'BBBB2222'] })
        : HttpResponse.json({ detail: 'Invalid code' }, { status: 400 });
    }),
    http.post('*/api/v1/auth/2fa/totp/disable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.post('*/api/v1/auth/2fa/totp/regenerate-backup-codes', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ backup_codes: ['NEWC0DE1', 'NEWC0DE2'] });
    }),
    http.post('*/api/v1/auth/2fa/email/enable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'code sent', setup_token: 'setup-token-1' });
    }),
    http.post('*/api/v1/auth/2fa/email/enable/confirm', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.post('*/api/v1/auth/2fa/email/disable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.delete('*/api/v1/auth/oidc/links/:providerId', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
  );
});

describe('TwoFactorSettings wiring', () => {
  it('TOTP setup: setup -> secret/QR -> code -> enableTOTP(code) -> backup codes', async () => {
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /set up authenticator app/i }));
    await waitFor(() => expect(sent('/totp/setup')).toHaveLength(1));
    expect(await screen.findByAltText('TOTP QR Code')).toHaveAttribute('src', 'data:image/png;base64,AAAA');

    await user.click(screen.getByRole('button', { name: /scanned the code/i }));
    const activate = screen.getByRole('button', { name: /^activate$/i });
    await user.type(screen.getByPlaceholderText('000000'), '123456');
    await user.click(activate);

    await waitFor(() => expect(sent('/totp/enable')).toHaveLength(1));
    expect(sent('/totp/enable')[0].body).toEqual({ code: '123456' });
    expect(await screen.findByText('AAAA1111')).toBeInTheDocument();
  });

  it('a wrong TOTP code shows an error and stays on the confirm step', async () => {
    totpEnableOk = false;
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /set up authenticator app/i }));
    await user.click(await screen.findByRole('button', { name: /scanned the code/i }));
    await user.type(screen.getByPlaceholderText('000000'), '000000');
    await user.click(screen.getByRole('button', { name: /^activate$/i }));

    expect(await screen.findByText(/invalid code/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^activate$/i })).toBeInTheDocument();
    expect(screen.queryByText(/save your backup codes/i)).not.toBeInTheDocument();
  });

  it('activate stays disabled until 6 digits are entered', async () => {
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /set up authenticator app/i }));
    await user.click(await screen.findByRole('button', { name: /scanned the code/i }));
    await user.type(screen.getByPlaceholderText('000000'), '12345');
    expect(screen.getByRole('button', { name: /^activate$/i })).toBeDisabled();
    expect(sent('/totp/enable')).toHaveLength(0);
  });

  it('disable TOTP sends the typed code to /totp/disable; cancel sends nothing', async () => {
    twoFaStatus = { totp_enabled: true, email_otp_enabled: false, backup_codes_remaining: 5 };
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    // Cancel first
    await user.click(await screen.findByRole('button', { name: /disable authenticator/i }));
    await user.type(screen.getByPlaceholderText('000000 or XXXXXXXX'), '654321');
    await user.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(sent('/totp/disable')).toHaveLength(0);

    await user.click(screen.getByRole('button', { name: /disable authenticator/i }));
    const input = screen.getByPlaceholderText('000000 or XXXXXXXX');
    expect(input).toHaveValue('');
    await user.type(input, 'abcd2345');
    await user.click(screen.getByRole('button', { name: /disable authenticator/i }));

    await waitFor(() => expect(sent('/totp/disable')).toHaveLength(1));
    // CodeInput upper-cases backup codes
    expect(sent('/totp/disable')[0].body).toEqual({ code: 'ABCD2345' });
  });

  it('regenerate backup codes sends the code and shows the new codes once', async () => {
    twoFaStatus = { totp_enabled: true, email_otp_enabled: false, backup_codes_remaining: 2 };
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /regenerate backup codes/i }));
    await user.type(screen.getByPlaceholderText('000000 or XXXXXXXX'), '112233');
    await user.click(screen.getByRole('button', { name: /regenerate backup codes/i }));

    await waitFor(() => expect(sent('/regenerate-backup-codes')).toHaveLength(1));
    expect(sent('/regenerate-backup-codes')[0].body).toEqual({ code: '112233' });
    expect(await screen.findByText('NEWC0DE1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /saved my codes/i }));
    await waitFor(() => expect(screen.queryByText('NEWC0DE1')).not.toBeInTheDocument());
  });

  it('email OTP enable: request -> confirm sends {setup_token, code}', async () => {
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /enable email otp/i }));
    await waitFor(() => expect(sent('/email/enable')).toHaveLength(1));

    await user.type(await screen.findByPlaceholderText('000000'), '778899');
    await user.click(screen.getByRole('button', { name: /verify & enable/i }));

    await waitFor(() => expect(sent('/email/enable/confirm')).toHaveLength(1));
    expect(sent('/email/enable/confirm')[0].body).toEqual({ setup_token: 'setup-token-1', code: '778899' });
  });

  it('email OTP disable requires the password and sends it to /email/disable', async () => {
    twoFaStatus = { totp_enabled: false, email_otp_enabled: true, backup_codes_remaining: 0 };
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    await user.click(await screen.findByRole('button', { name: /disable email otp/i }));
    const confirm = screen.getByRole('button', { name: /disable email otp/i });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByPlaceholderText(/enter your password/i), 'test-password-1');
    await user.click(confirm);

    await waitFor(() => expect(sent('/email/disable')).toHaveLength(1));
    expect(sent('/email/disable')[0].body).toEqual({ password: 'test-password-1' });
  });

  it('unlinking an SSO account asks first, then deletes the clicked provider link; cancel sends nothing', async () => {
    const user = userEvent.setup();
    render(<TwoFactorSettings />);

    const row = (await screen.findByText('Beta IdP')).closest('div.flex.items-center.justify-between') as HTMLElement;
    await user.click(within(row).getByRole('button'));
    const dialog = () => screen.getByText('Unlink account?').closest('div.fixed') as HTMLElement;
    expect(within(dialog()).getByText(/sign in with Beta IdP/)).toBeInTheDocument();
    await user.click(within(dialog()).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByText('Unlink account?')).toBeNull();
    expect(captured.filter((c) => c.method === 'DELETE')).toEqual([]);

    await user.click(within(row).getByRole('button'));
    await user.click(within(dialog()).getByRole('button', { name: 'Unlink' }));

    await waitFor(() => expect(sent('/oidc/links/8')).toHaveLength(1));
    expect(captured.filter((c) => c.method === 'DELETE')).toEqual([
      { method: 'DELETE', path: '/api/v1/auth/oidc/links/8', body: null },
    ]);
  });
});
