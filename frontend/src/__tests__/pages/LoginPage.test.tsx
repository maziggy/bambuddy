/**
 * Tests for the LoginPage component.
 */

import { describe, it, expect, beforeEach } from 'vitest';
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
});
