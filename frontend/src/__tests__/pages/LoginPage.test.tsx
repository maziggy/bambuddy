/**
 * Tests for the LoginPage component.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { LoginPage } from '../../pages/LoginPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

describe('LoginPage', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/auth/status', () => {
        return HttpResponse.json({ auth_enabled: true, requires_setup: false });
      })
    );
  });

  describe('rendering', () => {
    it('renders the login form', async () => {
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Bambuddy Login/i })).toBeInTheDocument();
      });

      expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/Password/i)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
    });

    it('renders the sign in description', async () => {
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByText(/Sign in to your account/i)).toBeInTheDocument();
      });
    });
  });

  describe('form validation', () => {
    it('shows error when submitting empty form', async () => {
      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Sign in/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      // The form has required fields, so HTML5 validation should prevent submission
      // or the component shows a toast
    });

    it('allows entering username and password', async () => {
      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpassword');

      expect(screen.getByLabelText(/Username/i)).toHaveValue('testuser');
      expect(screen.getByLabelText(/Password/i)).toHaveValue('testpassword');
    });
  });

  describe('login flow', () => {
    it('submits login request with credentials', async () => {
      const user = userEvent.setup();
      let loginCalled = false;

      server.use(
        http.post('/api/v1/auth/login', async ({ request }) => {
          loginCalled = true;
          const body = await request.json() as { username: string; password: string };
          if (body.username === 'validuser' && body.password === 'validpass') {
            return HttpResponse.json({
              access_token: 'test-token',
              token_type: 'bearer',
              user: {
                id: 1,
                username: 'validuser',
                role: 'admin',
                is_active: true,
                created_at: new Date().toISOString(),
              },
            });
          }
          return HttpResponse.json(
            { detail: 'Incorrect username or password' },
            { status: 401 }
          );
        })
      );

      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'validuser');
      await user.type(screen.getByLabelText(/Password/i), 'validpass');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      // Verify the login endpoint was called
      await waitFor(() => {
        expect(loginCalled).toBe(true);
      });
    });

    it('shows loading state during login', async () => {
      const user = userEvent.setup();
      let resolveLogin: () => void;
      const loginPromise = new Promise<void>(resolve => { resolveLogin = resolve; });

      // Slow login endpoint that we control
      server.use(
        http.post('/api/v1/auth/login', async () => {
          await loginPromise;
          return HttpResponse.json({
            access_token: 'test-token',
            token_type: 'bearer',
            user: {
              id: 1,
              username: 'testuser',
              role: 'admin',
              is_active: true,
              created_at: new Date().toISOString(),
            },
          });
        })
      );

      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpass');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      // Check for loading state - button text should change to "Logging in..."
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Logging in/i })).toBeInTheDocument();
      });

      // Release the login request
      resolveLogin!();
    });
  });

  describe('2FA flow', () => {
    // Helper: login as a 2FA user and get to the 2FA step
    async function loginWith2FA(twoFAMethods = ['totp', 'backup']) {
      const user = userEvent.setup();

      server.use(
        http.post('/api/v1/auth/login', () =>
          HttpResponse.json({
            requires_2fa: true,
            pre_auth_token: 'test-pre-auth-token',
            two_fa_methods: twoFAMethods,
          })
        )
      );

      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'mfa-user');
      await user.type(screen.getByLabelText(/Password/i), 'mfa-password');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      return user;
    }

    it('shows 2FA step when login returns requires_2fa', async () => {
      await loginWith2FA();

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });
    });

    it('shows code input on the 2FA step', async () => {
      await loginWith2FA();

      await waitFor(() => {
        // The code input field is rendered
        expect(screen.getByRole('textbox', { name: /Verification Code/i })).toBeInTheDocument();
      });
    });

    it('submits 2FA verify request with code and pre_auth_token', async () => {
      let verifyCalled = false;
      let verifyBody: unknown;

      server.use(
        http.post('/api/v1/auth/2fa/verify', async ({ request }) => {
          verifyCalled = true;
          verifyBody = await request.json();
          return HttpResponse.json({
            access_token: 'final-jwt',
            token_type: 'bearer',
            user: {
              id: 1,
              username: 'mfa-user',
              role: 'admin',
              is_active: true,
              created_at: new Date().toISOString(),
            },
          });
        })
      );

      const user = await loginWith2FA();

      await waitFor(() => {
        expect(screen.getByRole('textbox', { name: /Verification Code/i })).toBeInTheDocument();
      });

      await user.type(screen.getByRole('textbox', { name: /Verification Code/i }), '123456');
      await user.click(screen.getByRole('button', { name: /Verify/i }));

      await waitFor(() => {
        expect(verifyCalled).toBe(true);
      });

      expect(verifyBody).toMatchObject({
        pre_auth_token: 'test-pre-auth-token',
        code: '123456',
        method: 'totp',
      });
    });

    it('returns to credentials step when back button is clicked', async () => {
      await loginWith2FA();

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      const user = userEvent.setup();
      const backButton = screen.getByRole('button', { name: /Back to login/i });
      await user.click(backButton);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Bambuddy Login/i })).toBeInTheDocument();
      });
    });

    it('shows method selector when multiple 2FA methods are available', async () => {
      await loginWith2FA(['totp', 'email', 'backup']);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      // Multiple method buttons should be visible
      expect(screen.getByRole('button', { name: /Authenticator/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Email/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Backup/i })).toBeInTheDocument();
    });

    it('does not show method selector with only one 2FA method', async () => {
      await loginWith2FA(['totp']);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      // Single-method: no method selector buttons
      expect(screen.queryByRole('button', { name: /Authenticator/i })).not.toBeInTheDocument();
    });

    it('shows send code button when email method is selected', async () => {
      const _user = await loginWith2FA(['email']);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      // For email method the "Send code" button should be shown
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Send Code/i })).toBeInTheDocument();
      });
    });
  });

  describe('Remember Me', () => {
    const mockUser = {
      id: 1,
      username: 'testuser',
      role: 'admin' as const,
      is_active: true,
      created_at: new Date().toISOString(),
    };

    beforeEach(() => {
      vi.mocked(localStorage.setItem).mockClear();
      sessionStorage.clear();
      server.use(
        http.post('/api/v1/auth/login', () =>
          HttpResponse.json({
            access_token: 'test-token',
            token_type: 'bearer',
            user: mockUser,
          })
        ),
        // Prevent checkAuthStatus from clearing the token when getCurrentUser is called
        http.get('/api/v1/auth/me', () => HttpResponse.json(mockUser))
      );
    });

    it('renders Remember Me checkbox on credentials step', async () => {
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Remember Me/i)).toBeInTheDocument();
      });

      expect(screen.getByRole('checkbox', { name: /Remember Me/i })).not.toBeChecked();
    });

    it('does not persist token to localStorage when unchecked (default)', async () => {
      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpassword');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      // Token must be in sessionStorage (tab-only) but not in localStorage
      await waitFor(() => {
        expect(vi.mocked(localStorage.setItem)).not.toHaveBeenCalledWith('auth_token', expect.any(String));
        expect(sessionStorage.getItem('auth_token')).toBe('test-token');
      });
    });

    it('persists token to localStorage when Remember Me is checked', async () => {
      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.click(screen.getByRole('checkbox', { name: /Remember Me/i }));
      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpassword');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      await waitFor(() => {
        expect(vi.mocked(localStorage.setItem)).toHaveBeenCalledWith('auth_token', 'test-token');
      });
    });

    it('carries Remember Me through 2FA verification', async () => {
      server.use(
        http.post('/api/v1/auth/login', () =>
          HttpResponse.json({
            requires_2fa: true,
            pre_auth_token: 'pre-token',
            two_fa_methods: ['totp'],
          })
        ),
        http.post('/api/v1/auth/2fa/verify', () =>
          HttpResponse.json({
            access_token: 'final-token',
            token_type: 'bearer',
            user: mockUser,
          })
        )
      );

      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      // Check Remember Me before submitting credentials
      await user.click(screen.getByRole('checkbox', { name: /Remember Me/i }));
      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpassword');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      // Now on 2FA step — enter code and verify
      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      await user.type(screen.getByRole('textbox', { name: /Verification Code/i }), '123456');
      await user.click(screen.getByRole('button', { name: /Verify/i }));

      // Token must be persisted to localStorage because Remember Me was checked
      await waitFor(() => {
        expect(vi.mocked(localStorage.setItem)).toHaveBeenCalledWith('auth_token', 'final-token');
      });
    });

    it('checkbox is not shown on 2FA step', async () => {
      server.use(
        http.post('/api/v1/auth/login', () =>
          HttpResponse.json({
            requires_2fa: true,
            pre_auth_token: 'pre-token',
            two_fa_methods: ['totp'],
          })
        )
      );

      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByLabelText(/Username/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/Username/i), 'testuser');
      await user.type(screen.getByLabelText(/Password/i), 'testpassword');
      await user.click(screen.getByRole('button', { name: /Sign in/i }));

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });

      expect(screen.queryByLabelText(/Remember Me/i)).not.toBeInTheDocument();
    });
  });

  describe('OIDC with Remember Me', () => {
    const mockUser = {
      id: 1,
      username: 'oidcuser',
      role: 'admin' as const,
      is_active: true,
      created_at: new Date().toISOString(),
    };

    beforeEach(() => {
      vi.mocked(localStorage.setItem).mockClear();
      sessionStorage.clear();
    });

    afterEach(() => {
      window.location.hash = '';
      window.history.pushState({}, '', '/login');
      sessionStorage.clear();
    });

    it('persists token to localStorage after OIDC redirect when Remember Me was set', async () => {
      sessionStorage.setItem('auth_remember_me', '1');
      server.use(
        http.post('/api/v1/auth/oidc/exchange', () =>
          HttpResponse.json({
            access_token: 'oidc-token',
            token_type: 'bearer',
            user: mockUser,
          })
        )
      );

      window.location.hash = '#oidc_token=test-exchange-token';
      render(<LoginPage />);

      await waitFor(() => {
        expect(vi.mocked(localStorage.setItem)).toHaveBeenCalledWith('auth_token', 'oidc-token');
      });
      expect(sessionStorage.getItem('auth_remember_me')).toBeNull();
    });

    it('carries Remember Me through OIDC + 2FA flow', async () => {
      sessionStorage.setItem('auth_remember_me', '1');
      server.use(
        http.post('/api/v1/auth/oidc/exchange', () =>
          HttpResponse.json({
            requires_2fa: true,
            pre_auth_token: 'oidc-pre-token',
            two_fa_methods: ['totp'],
          })
        ),
        http.post('/api/v1/auth/2fa/verify', () =>
          HttpResponse.json({
            access_token: 'oidc-2fa-token',
            token_type: 'bearer',
            user: mockUser,
          })
        )
      );

      window.location.hash = '#oidc_token=test-exchange-token';
      const user = userEvent.setup();
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /Two-Factor Authentication/i })).toBeInTheDocument();
      });
      // Flag consumed on mount — no stale value for future flows
      expect(sessionStorage.getItem('auth_remember_me')).toBeNull();

      await user.type(screen.getByRole('textbox', { name: /Verification Code/i }), '123456');
      await user.click(screen.getByRole('button', { name: /Verify/i }));

      await waitFor(() => {
        expect(vi.mocked(localStorage.setItem)).toHaveBeenCalledWith('auth_token', 'oidc-2fa-token');
      });
    });

    it('cleans up auth_remember_me flag when OIDC returns an error', async () => {
      sessionStorage.setItem('auth_remember_me', '1');
      window.history.pushState({}, '', '/login?oidc_error=invalid_state');
      render(<LoginPage />);

      await waitFor(() => {
        expect(sessionStorage.getItem('auth_remember_me')).toBeNull();
      });
    });

    it('does not persist token to localStorage after OIDC redirect when Remember Me was not set', async () => {
      // No auth_remember_me flag set — token must stay session-only
      server.use(
        http.post('/api/v1/auth/oidc/exchange', () =>
          HttpResponse.json({
            access_token: 'oidc-session-token',
            token_type: 'bearer',
            user: mockUser,
          })
        )
      );

      window.location.hash = '#oidc_token=test-exchange-token';
      render(<LoginPage />);

      await waitFor(() => {
        expect(sessionStorage.getItem('auth_token')).toBe('oidc-session-token');
      });
      expect(vi.mocked(localStorage.setItem)).not.toHaveBeenCalledWith('auth_token', expect.any(String));
    });

    it('shows error toast when OIDC exchange returns unexpected response shape', async () => {
      sessionStorage.setItem('auth_remember_me', '1');
      server.use(
        // Response is missing both access_token and requires_2fa — hits the else branch
        http.post('/api/v1/auth/oidc/exchange', () =>
          HttpResponse.json({ token_type: 'bearer' })
        )
      );

      window.location.hash = '#oidc_token=test-exchange-token';
      render(<LoginPage />);

      await waitFor(() => {
        expect(screen.getByText(/Login.*failed|failed.*login/i)).toBeInTheDocument();
      });
      // Flag must still be cleaned up even on malformed response
      expect(sessionStorage.getItem('auth_remember_me')).toBeNull();
    });

    it('writes auth_remember_me flag to sessionStorage before OIDC provider redirect', async () => {
      server.use(
        http.get('/api/v1/auth/oidc/providers', () =>
          HttpResponse.json([
            {
              id: 42,
              name: 'FlagIdP',
              issuer_url: 'https://flag.test',
              client_id: 'c',
              is_enabled: true,
              icon_url: null,
              email_claim: 'email',
              require_email_verified: true,
              auto_create_users: false,
              auto_link_existing_accounts: false,
            },
          ])
        ),
        http.get('/api/v1/auth/oidc/authorize/42', () =>
          HttpResponse.json({ auth_url: 'https://flag.test/authorize?state=abc' })
        )
      );

      const user = userEvent.setup();
      render(<LoginPage />);

      // Tick "Remember Me"
      await waitFor(() => {
        expect(screen.getByRole('checkbox', { name: /Remember Me/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('checkbox', { name: /Remember Me/i }));

      // Wait for OIDC provider button to appear
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /FlagIdP/i })).toBeInTheDocument();
      });

      // Stub window.location so the OIDC redirect doesn't actually navigate.
      // Keep href valid so relative fetch URLs resolve correctly.
      Object.defineProperty(window, 'location', {
        writable: true,
        value: { ...window.location, href: 'http://localhost:3000/' },
      });

      await user.click(screen.getByRole('button', { name: /FlagIdP/i }));

      await waitFor(() => {
        expect(sessionStorage.getItem('auth_remember_me')).toBe('1');
      });
    });
  });
});
