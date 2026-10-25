/**
 * Tests for the AuthContext permission helpers.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { AuthProvider, useAuth } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { getAuthToken, setAuthToken, type Permission } from '../../api/client';

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
  describe('streaming URL tokens', () => {
    beforeEach(() => {
      setAuthToken(null);
      server.use(http.get('/api/v1/auth/status', () =>
        HttpResponse.json({ auth_enabled: true, requires_setup: false })
      ));
    });

    afterEach(() => {
      window.history.replaceState({}, '', '/');
      setAuthToken(null);
    });

    it.each(['/overlay/2', '/overlay/2/', '/camwall', '/camwall/'])(
      'preserves the scoped token across remounts on %s', async (path) => {
        const url = `${path}?artwork=2&token=streaming-token#preview`;
        window.history.replaceState({}, '', url);
        const authMe = vi.fn(() => HttpResponse.json({}, { status: 401 }));
        server.use(http.get('/api/v1/auth/me', authMe));
        for (let load = 0; load < 2; load++) {
          const { result, unmount } = renderHook(() => useAuth(), { wrapper: createWrapper() });
          await waitFor(() => expect(result.current.loading).toBe(false));
          expect(window.location.pathname + window.location.search + window.location.hash).toBe(url);
          expect(getAuthToken()).toBeNull();
          expect(sessionStorage.getItem('auth_token')).toBeNull();
          unmount();
        }
        expect(authMe).not.toHaveBeenCalled();
      }
    );

    it.each(['/overlay/2', '/camwall'])('does not replace an existing login on %s', async (path) => {
      setAuthToken('valid-token', 'persistent');
      window.history.replaceState({}, '', `${path}?token=streaming-token`);
      server.use(http.get('/api/v1/auth/me', () => HttpResponse.json({
        id: 1, username: 'alice', is_active: true, permissions: [], groups: [],
      })));
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(getAuthToken()).toBe('valid-token');
      expect(sessionStorage.getItem('auth_token')).toBe('valid-token');
      expect(result.current.user?.username).toBe('alice');
    });

    it('still bootstraps and removes a SpoolBuddy login token', async () => {
      window.history.replaceState({}, '', '/spoolbuddy?token=kiosk-api-key&view=ams#top');
      server.use(http.get('/api/v1/auth/me', () => HttpResponse.json({
        id: 1, username: 'kiosk', is_active: true, permissions: [], groups: [],
      })));
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(getAuthToken()).toBe('kiosk-api-key');
      expect(localStorage.setItem).toHaveBeenCalledWith('auth_token', 'kiosk-api-key');
      expect(window.location.search).toBe('?view=ams');
      expect(window.location.hash).toBe('#top');
    });
  });

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
  });

  describe('first-run setup', () => {
    const realLocation = window.location;

    function stubLocation(pathname: string) {
      const { origin, protocol, host, hostname, port } = realLocation;
      const location = { origin, protocol, host, hostname, port, pathname, href: origin + pathname, search: '', hash: '' };
      Object.defineProperty(window, 'location', { configurable: true, value: location });
      return location;
    }

    beforeEach(() => {
      server.use(
        http.get('/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: false, requires_setup: true }))
      );
    });

    afterEach(() => {
      Object.defineProperty(window, 'location', { configurable: true, value: realLocation });
    });

    it('sends a new install to the setup page', async () => {
      const location = stubLocation('/');

      renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(location.href).toBe('/setup'));
    });

    it('leaves a camera page where it is', async () => {
      const location = stubLocation('/camera/1');
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(location.href).toBe(`${realLocation.origin}/camera/1`);
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

    it('re-reads the auth status on an auth:recheck event', async () => {
      // A WebSocket closed 4401 asks for it: auth may have been turned on, or
      // the session ended, while this tab still holds the old state.
      const status = vi.fn(() => HttpResponse.json({ auth_enabled: true, requires_setup: false }));
      server.use(http.get('/api/v1/auth/status', status));
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });
      const before = status.mock.calls.length;

      act(() => {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
      });

      await waitFor(() => {
        expect(status.mock.calls.length).toBe(before + 1);
      });
    });

    it.each([
      ['the auth status', '/api/v1/auth/status'],
      ['the signed-in user', '/api/v1/auth/me'],
    ])('keeps the session when a re-check cannot read %s', async (_what, path) => {
      // A failed scope refresh (a database error) closes every socket with
      // 4401; the re-check that follows may hit the same failure. Only a
      // definite answer may sign the tab out or switch it to "auth off".
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });
      const before = result.current.user;
      server.use(http.get(path, () => HttpResponse.json({ detail: 'database is locked' }, { status: 500 })));

      act(() => {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
      });
      await new Promise((resolve) => setTimeout(resolve, 2500));

      expect(result.current.authEnabled).toBe(true);
      expect(result.current.user).toEqual(before);
    });

    it('signs a key whose owner was deactivated out on a re-check, keeping the key', async () => {
      // /auth/me answers 403 for an API key (a SpoolBuddy kiosk) whose owner is
      // deactivated or gone: a definite answer, unlike a database error. The
      // key is kept: reactivating the owner makes it work again, and the kiosk
      // would otherwise need setting up again.
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({ detail: 'API key owner is deactivated or no longer exists' }, { status: 403 })
        )
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
      });

      await waitFor(() => {
        expect(result.current.user).toBeNull();
      });
      expect(getAuthToken()).toBe('valid-token');
    });

    it('repeats a re-check that got no answer until it gets one', async () => {
      // Nothing else would ask again: after a refused token mint there is no
      // socket, so no further close can trigger it, and the tab froze.
      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => {
        expect(result.current.user).not.toBeNull();
      });
      let failing = true;
      server.use(
        http.get('/api/v1/auth/me', () =>
          failing
            ? HttpResponse.json({ detail: 'database is locked' }, { status: 500 })
            : HttpResponse.json({ detail: 'Token no longer valid' }, { status: 401 })
        )
      );

      act(() => {
        window.dispatchEvent(new CustomEvent('auth:recheck'));
      });
      await new Promise((resolve) => setTimeout(resolve, 1500));
      expect(result.current.user).not.toBeNull(); // kept: no answer yet
      failing = false;

      await waitFor(() => expect(result.current.user).toBeNull(), { timeout: 5000 });
      // A definite 401 clears the token too, as the first load does.
      expect(getAuthToken()).toBeNull();
    }, 10000);

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

  describe('token validation on mount (#1889)', () => {
    beforeEach(() => {
      // Persisted "Remember Me" token — lives in localStorage.
      setAuthToken('valid-token', 'persistent');
      server.use(
        http.get('/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false })
        )
      );
    });

    afterEach(() => {
      setAuthToken(null);
      localStorage.removeItem('auth_token');
    });

    it('keeps the persisted token when /auth/me fails transiently (does not force re-login)', async () => {
      // Backend not ready yet / brief blip → 500 on every attempt.
      server.use(http.get('/api/v1/auth/me', () => new HttpResponse(null, { status: 500 })));
      vi.mocked(window.localStorage.removeItem).mockClear();

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 4000 });

      // No user this load, but the token MUST survive so a reload can recover —
      // the pre-#1889 blanket catch deleted it, making the session unrecoverable.
      expect(result.current.user).toBeNull();
      expect(getAuthToken()).toBe('valid-token');
      // Persisted copy must not be wiped (setAuthToken(null) removes it).
      expect(window.localStorage.removeItem).not.toHaveBeenCalledWith('auth_token');
    });

    it('clears the token on a definitive 401 invalid-token response', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({ detail: 'Could not validate credentials' }, { status: 401 })
        )
      );
      vi.mocked(window.localStorage.removeItem).mockClear();

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 4000 });

      expect(result.current.user).toBeNull();
      expect(getAuthToken()).toBeNull();
      // Definitive invalid-token → persisted copy is removed.
      expect(window.localStorage.removeItem).toHaveBeenCalledWith('auth_token');
    });

    it('keeps asking after the first load gets no answer, and signs in when it does (#3360)', async () => {
      // The reporter's kiosk: the database was out of connections while it
      // loaded. It used to stop after three tries and show the login page.
      let down = true;
      server.use(
        http.get('/api/v1/auth/me', () =>
          down
            ? HttpResponse.json({ detail: 'Authentication service temporarily unavailable' }, { status: 503 })
            : HttpResponse.json({ id: 1, username: 'kiosk', is_active: true, permissions: [], groups: [] })
        )
      );

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.loading).toBe(false), { timeout: 4000 });
      expect(result.current.authUnconfirmed).toBe(true);
      expect(result.current.user).toBeNull();
      expect(getAuthToken()).toBe('valid-token');

      down = false;
      await waitFor(() => expect(result.current.user?.username).toBe('kiosk'), { timeout: 6000 });
      expect(result.current.authUnconfirmed).toBe(false);
    }, 12000);

    it('ends the wait on a definite 401', async () => {
      let down = true;
      server.use(
        http.get('/api/v1/auth/me', () =>
          down
            ? new HttpResponse(null, { status: 503 })
            : HttpResponse.json({ detail: 'Invalid API key' }, { status: 401 })
        )
      );

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.authUnconfirmed).toBe(true), { timeout: 4000 });

      down = false;
      await waitFor(() => expect(result.current.authUnconfirmed).toBe(false), { timeout: 6000 });
      expect(result.current.user).toBeNull();
      expect(getAuthToken()).toBeNull();
    }, 12000);

    it('waits instead of showing the open UI when even the auth status gets no answer', async () => {
      let down = true;
      server.use(
        http.get('/api/v1/auth/status', () =>
          down
            ? new HttpResponse(null, { status: 503 })
            : HttpResponse.json({ auth_enabled: true, requires_setup: false })
        ),
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({ id: 1, username: 'kiosk', is_active: true, permissions: [], groups: [] })
        )
      );

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
      await waitFor(() => expect(result.current.loading).toBe(false));
      expect(result.current.authUnconfirmed).toBe(true);

      down = false;
      await waitFor(() => expect(result.current.user?.username).toBe('kiosk'), { timeout: 6000 });
      expect(result.current.authEnabled).toBe(true);
      expect(result.current.authUnconfirmed).toBe(false);
    }, 12000);

    it("persists a kiosk's ?token= key once a later try confirms it", async () => {
      setAuthToken(null);
      localStorage.removeItem('auth_token');
      window.history.replaceState({}, '', '/spoolbuddy?token=bb_kiosk');
      let down = true;
      server.use(
        http.get('/api/v1/auth/me', () =>
          down
            ? new HttpResponse(null, { status: 503 })
            : HttpResponse.json({ id: 1, username: 'kiosk', is_active: true, permissions: [], groups: [] })
        )
      );
      vi.mocked(window.localStorage.setItem).mockClear();
      try {
        const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });
        await waitFor(() => expect(result.current.authUnconfirmed).toBe(true), { timeout: 4000 });
        expect(window.localStorage.setItem).not.toHaveBeenCalledWith('auth_token', 'bb_kiosk');

        down = false;
        await waitFor(() => expect(result.current.user).not.toBeNull(), { timeout: 6000 });
        expect(window.localStorage.setItem).toHaveBeenCalledWith('auth_token', 'bb_kiosk');
      } finally {
        window.history.replaceState({}, '', '/');
      }
    }, 12000);

    it('loads the user when the persisted token is valid', async () => {
      server.use(
        http.get('/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 1,
            username: 'alice',
            is_active: true,
            permissions: [],
            groups: [],
          })
        )
      );

      const { result } = renderHook(() => useAuth(), { wrapper: createWrapper() });

      await waitFor(() => expect(result.current.user).not.toBeNull());
      expect(result.current.user?.username).toBe('alice');
      expect(getAuthToken()).toBe('valid-token');
    });
  });
});
