/**
 * Email settings: the SMTP save request carries the form values (and an
 * empty password when it was not retyped, so the server keeps the stored
 * one), the test email goes out with the form's settings, and the
 * advanced-auth toggle only calls enable/disable when its preconditions hold.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { render } from '../utils';
import { server } from '../mocks/server';
import { EmailSettings } from '../../components/EmailSettings';

type Captured = { method: string; path: string; body: unknown };

let captured: Captured[];
let advancedStatus: { advanced_auth_enabled: boolean; smtp_configured: boolean };
let authEnabled: boolean;

const STORED_SMTP = {
  smtp_host: 'smtp.example',
  smtp_port: 587,
  smtp_username: 'mailer',
  smtp_password: null,
  smtp_security: 'starttls',
  smtp_auth_enabled: true,
  smtp_from_email: 'from@example.com',
  smtp_from_name: 'BamBuddy',
};

async function capture(request: Request) {
  const text = await request.text();
  captured.push({
    method: request.method,
    path: new URL(request.url).pathname,
    body: text ? JSON.parse(text) : null,
  });
}

beforeEach(() => {
  captured = [];
  advancedStatus = { advanced_auth_enabled: false, smtp_configured: true };
  authEnabled = true;
  server.use(
    http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: authEnabled, requires_setup: false })),
    http.get('*/api/v1/auth/smtp', () => HttpResponse.json(STORED_SMTP)),
    http.get('*/api/v1/auth/advanced-auth/status', () => HttpResponse.json(advancedStatus)),
    http.post('*/api/v1/auth/smtp', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'ok' });
    }),
    http.post('*/api/v1/auth/smtp/test', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ success: true, message: 'sent' });
    }),
    http.post('*/api/v1/auth/advanced-auth/enable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'enabled', advanced_auth_enabled: true });
    }),
    http.post('*/api/v1/auth/advanced-auth/disable', async ({ request }) => {
      await capture(request);
      return HttpResponse.json({ message: 'disabled', advanced_auth_enabled: false });
    }),
  );
});

describe('EmailSettings wiring', () => {
  it('save sends host/port/security/from and an empty password when it was not retyped', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByDisplayValue('smtp.example');
    const from = screen.getByDisplayValue('from@example.com');
    await user.clear(from);
    await user.type(from, 'printer@example.com');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(captured.filter((c) => c.path.endsWith('/auth/smtp'))).toHaveLength(1));
    const req = captured[0];
    expect(req.method).toBe('POST');
    expect(req.body).toEqual({
      smtp_host: 'smtp.example',
      smtp_port: 587,
      smtp_username: 'mailer',
      smtp_password: '',
      smtp_security: 'starttls',
      smtp_auth_enabled: true,
      smtp_from_email: 'printer@example.com',
      smtp_from_name: 'BamBuddy',
    });
  });

  it('changing security to SSL sends ssl with port 465, and a retyped password is sent', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByDisplayValue('smtp.example');
    await user.selectOptions(screen.getByDisplayValue('STARTTLS (Port 587)'), 'ssl');
    await user.type(document.querySelector('input[type="password"]') as HTMLInputElement, 'test-password-1');
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0].body).toMatchObject({
      smtp_security: 'ssl',
      smtp_port: 465,
      smtp_password: 'test-password-1',
    });
  });

  it('save without a host sends nothing', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    await user.clear(await screen.findByDisplayValue('smtp.example'));
    await user.click(screen.getByRole('button', { name: /save settings/i }));

    expect(await screen.findByText(/please fill in all required fields/i)).toBeInTheDocument();
    expect(captured).toHaveLength(0);
  });

  it('send test email posts the recipient and the form as it is, unsaved edits included', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByDisplayValue('smtp.example');
    const from = screen.getByDisplayValue('from@example.com');
    await user.clear(from);
    await user.type(from, 'typed@example.com');
    await user.type(screen.getByPlaceholderText('test@example.com'), 'probe@example.com');
    await user.click(screen.getByRole('button', { name: /send test email/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({
      method: 'POST',
      path: '/api/v1/auth/smtp/test',
      body: {
        test_recipient: 'probe@example.com',
        smtp: { ...STORED_SMTP, smtp_password: '', smtp_from_email: 'typed@example.com' },
      },
    });
  });

  it('enable advanced auth calls enable once SMTP is configured', async () => {
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByText(/advanced authentication is disabled/i);
    // Wait for AuthProvider to report auth enabled before clicking
    await waitFor(async () => {
      captured = [];
      await user.click(screen.getByRole('button', { name: /^enable$/i }));
      expect(captured.map((c) => c.path)).toContain('/api/v1/auth/advanced-auth/enable');
    });
    expect(captured.map((c) => c.path)).not.toContain('/api/v1/auth/advanced-auth/disable');
  });

  it('enable advanced auth sends nothing while SMTP is not configured/tested', async () => {
    advancedStatus = { advanced_auth_enabled: false, smtp_configured: false };
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByText(/advanced authentication is disabled/i);
    // Click until auth status has loaded and the SMTP precondition is the one refusing
    await waitFor(async () => {
      await user.click(screen.getByRole('button', { name: /^enable$/i }));
      expect(screen.getAllByText(/configure and test smtp settings first/i).length).toBeGreaterThan(0);
    });
    expect(captured).toHaveLength(0);
  });

  it('enable advanced auth sends nothing while app authentication is off', async () => {
    authEnabled = false;
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByText(/advanced authentication is disabled/i);
    await user.click(screen.getByRole('button', { name: /^enable$/i }));

    expect(await screen.findByText(/enable authentication first/i)).toBeInTheDocument();
    expect(captured).toHaveLength(0);
  });

  it('disable advanced auth calls disable when it is on', async () => {
    advancedStatus = { advanced_auth_enabled: true, smtp_configured: true };
    const user = userEvent.setup();
    render(<EmailSettings />);

    await screen.findByText(/advanced authentication is enabled/i);
    await waitFor(async () => {
      captured = [];
      await user.click(screen.getByRole('button', { name: /^disable$/i }));
      expect(captured.map((c) => c.path)).toContain('/api/v1/auth/advanced-auth/disable');
    });
    expect(captured.map((c) => c.path)).not.toContain('/api/v1/auth/advanced-auth/enable');
  });
});
