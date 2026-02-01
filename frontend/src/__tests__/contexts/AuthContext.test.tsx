/**
 * Tests for the AuthContext permission helpers.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import type { Permission } from '../../api/client';

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
          <ThemeProvider>
            <ToastProvider>
              <AuthProvider>{children}</AuthProvider>
            </ToastProvider>
          </ThemeProvider>
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
});
