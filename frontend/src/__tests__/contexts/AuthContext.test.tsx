/**
 * Tests for the AuthContext permission helpers.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { setAuthToken, type Permission } from '../../api/client';

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          {/* ThemeProvider sits inside AuthProvider (matches App.tsx
              after the GHSA-r2qv login-page-quiet fix) so its
              useAuth() call resolves. */}
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>{children}</ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    );
  };
}

describe('AuthContext', () => {
  describe('when auth is disabled', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/auth/status', () => {
          return HttpResponse.json({
            auth_enabled: false,
            requires_setup: false,
          });
        })
      );
    });

    it('authEnabled is false', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(false);
      });
    });

    it('hasPermission returns true for any permission when auth disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(false);
      });

      // When auth is disabled, all permissions should be granted
      expect(result.current.hasPermission('printers:read' as Permission)).toBe(true);
      expect(result.current.hasPermission('settings:update' as Permission)).toBe(true);
      expect(result.current.hasPermission('users:delete' as Permission)).toBe(true);
    });

    it('hasAnyPermission returns true for any permissions when auth disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(false);
      });

      expect(
        result.current.hasAnyPermission('printers:read' as Permission, 'settings:update' as Permission)
      ).toBe(true);
    });

    it('hasAllPermissions returns true for any permissions when auth disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(false);
      });

      expect(
        result.current.hasAllPermissions('printers:read' as Permission, 'settings:update' as Permission)
      ).toBe(true);
    });
  });

  describe('when auth requires setup', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/auth/status', () => {
          return HttpResponse.json({
            auth_enabled: false,
            requires_setup: true,
          });
        })
      );
    });

    it('requiresSetup is true', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.requiresSetup).toBe(true);
      });
    });
  });

  describe('when auth is enabled but not logged in', () => {
    beforeEach(() => {
      // Clear any stored token
      localStorage.removeItem('auth_token');

      server.use(
        http.get('/api/v1/auth/status', () => {
          return HttpResponse.json({
            auth_enabled: true,
            requires_setup: false,
          });
        })
      );
    });

    it('user is null when not logged in', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(true);
      });

      // User should be null when not logged in
      expect(result.current.user).toBeNull();
    });

    it('hasPermission returns false when not logged in', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.authEnabled).toBe(true);
      });

      // Without a user, permissions should be denied
      expect(result.current.hasPermission('printers:read' as Permission)).toBe(false);
    });
  });

  describe('CVE-2026-25505 fix: auth disabled grants all access', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/auth/status', () => {
          return HttpResponse.json({
            auth_enabled: false,
            requires_setup: false,
          });
        })
      );
    });

    it('isAdmin is true when auth is disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // When auth disabled, user is treated as admin
      expect(result.current.isAdmin).toBe(true);
    });

    it('canModify allows all modifications when auth disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // All canModify checks should pass when auth is disabled
      expect(result.current.canModify('queue', 'update', 1)).toBe(true);
      expect(result.current.canModify('queue', 'update', 999)).toBe(true);
      expect(result.current.canModify('queue', 'update', null)).toBe(true);
      expect(result.current.canModify('archives', 'delete', 1)).toBe(true);
      expect(result.current.canModify('library', 'update', null)).toBe(true);
    });

    it('all permissions are granted when auth is disabled', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      // All permission checks should pass
      expect(result.current.hasPermission('archives:read' as Permission)).toBe(true);
      expect(result.current.hasPermission('archives:delete_all' as Permission)).toBe(true);
      expect(result.current.hasPermission('settings:update' as Permission)).toBe(true);
      expect(result.current.hasPermission('api_keys:create' as Permission)).toBe(true);
      expect(result.current.hasPermission('groups:delete' as Permission)).toBe(true);
    });

    it('hasAnyPermission returns true for protected permissions', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(
        result.current.hasAnyPermission(
          'api_keys:create' as Permission,
          'groups:delete' as Permission
        )
      ).toBe(true);
    });

    it('hasAllPermissions returns true for any combination', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.loading).toBe(false);
      });

      expect(
        result.current.hasAllPermissions(
          'settings:update' as Permission,
          'api_keys:create' as Permission,
          'groups:delete' as Permission
        )
      ).toBe(true);
    });
  });

  describe('auth:expired event (#1698)', () => {
    beforeEach(() => {
      // authToken is a module-level variable initialised once at import time;
      // writing to sessionStorage after import doesn't propagate. Use the
      // canonical setter so checkAuthStatus() finds the token and loads /me.
      setAuthToken('valid-token');
      server.use(
        http.get('/api/v1/auth/status', () => {
          return HttpResponse.json({
            auth_enabled: true,
            requires_setup: false,
          });
        })
      );
    });

    afterEach(() => {
      setAuthToken(null);
    });

    it('clears user when an auth:expired event is dispatched', async () => {
      const { result } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      // Wait for the initial user load from /auth/me.
      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });

      // Simulate client.ts dispatching the event after a 401 + token clear.
      act(() => {
        window.dispatchEvent(new CustomEvent('auth:expired'));
      });

      // User is cleared synchronously — ProtectedRoute (App.tsx:101) sees
      // user === null and redirects to /login on the next render.
      await waitFor(() => {
        expect(result.current.user).toBeNull();
      });
    });

    it('does not crash when the event fires after unmount', async () => {
      const { result, unmount } = renderHook(() => useAuth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });

      unmount();

      // mountedRef guards setUser; dispatching after unmount must be a no-op,
      // not a React state-update-after-unmount warning.
      expect(() => {
        window.dispatchEvent(new CustomEvent('auth:expired'));
      }).not.toThrow();
    });
  });
});
