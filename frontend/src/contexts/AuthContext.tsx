import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, getAuthToken, setAuthToken } from '../api/client';
import type { LoginResponse, Permission, TokenPersistence, UserResponse } from '../api/client';

interface AuthContextType {
  user: UserResponse | null;
  authEnabled: boolean;
  loading: boolean;
  /** A token is stored but the server could not say whether it is valid
   *  (backend or database down). Retried in the background; the route guards
   *  wait instead of sending a kiosk to /login, where it can't sign in (#3360). */
  authUnconfirmed: boolean;
  isAdmin: boolean;
  /** Login with username/password. Returns LoginResponse (may include requires_2fa). */
  login: (username: string, password: string, persistence?: TokenPersistence) => Promise<LoginResponse>;
  /** Finalise login after 2FA or OIDC — store token and set user directly. */
  loginWithToken: (token: string, user: UserResponse, persistence?: TokenPersistence) => void;
  logout: () => void;
  refreshAuth: () => Promise<void>;
  hasPermission: (permission: Permission) => boolean;
  hasAnyPermission: (...permissions: Permission[]) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [requiresSetup, setRequiresSetup] = useState(false);
  const [loading, setLoading] = useState(true);
  const [authUnconfirmed, setAuthUnconfirmed] = useState(false);
  // A ?token= kiosk key not yet confirmed by the server, kept for a re-check
  // that confirms it after a failed first load, which persists it then.
  const pendingUrlTokenRef = useRef<string | null>(null);
  const hasRedirectedRef = useRef(false);
  const mountedRef = useRef(true);

  const checkAuthStatus = async () => {
    let hasStoredToken = false;
    try {
      // Bootstrap: if URL has ?token= param, store it session-only first and
      // strip it from the URL. Allows SpoolBuddy kiosk to pass an API key via
      // URL on first load. Persistence to localStorage is deferred until the
      // token has been verified by the server (L-4: prevents session fixation
      // where an attacker-crafted URL immediately persists a forged/stolen token).
      const urlParams = new URLSearchParams(window.location.search);
      // Streaming pages own their scoped URL tokens. Consuming one as a login
      // token removes authentication on reload and can overwrite a user session.
      const isStreamingPage = /^\/(?:overlay\/[^/]+|camwall)\/?$/i.test(window.location.pathname);
      const urlToken = isStreamingPage ? null : urlParams.get('token');
      if (urlToken) {
        setAuthToken(urlToken, 'session'); // session-only until server confirms it's valid
        pendingUrlTokenRef.current = urlToken;
        urlParams.delete('token');
        const cleanSearch = urlParams.toString();
        const cleanUrl = window.location.pathname
          + (cleanSearch ? `?${cleanSearch}` : '')
          + window.location.hash;
        window.history.replaceState({}, '', cleanUrl);
      }

      hasStoredToken = Boolean(getAuthToken());
      const status = await api.getAuthStatus();
      if (!mountedRef.current) return;
      setAuthEnabled(status.auth_enabled);
      setRequiresSetup(status.requires_setup);

      if (status.auth_enabled) {
        const token = getAuthToken();
        if (token) {
          // Validate the stored token. A transient failure here (backend not
          // yet ready after a container/proxy restart, a brief network blip)
          // must NOT discard a valid persisted token — doing so logs the user
          // out and, because the token is deleted, a reload can't recover it
          // (#1889). Only a definitive 401 invalid-token response clears the
          // token, and `request()` already does that clearing + dispatches
          // `auth:expired`; here we just retry the transient cases and keep the
          // token so the session survives a slow load.
          let currentUser: UserResponse | null = null;
          let definitiveAuthFailure = false;
          const maxAttempts = 3;
          for (let attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
              currentUser = await api.getCurrentUser();
              break;
            } catch (err) {
              if (!mountedRef.current) return;
              // 401 invalid-token → genuinely logged out. `request()` has
              // already cleared the token; stop retrying.
              if (err instanceof ApiError && err.status === 401) {
                definitiveAuthFailure = true;
                break;
              }
              // Transient (network / 5xx / other) → back off and retry. Leave
              // the token in place so a subsequent load can recover.
              if (attempt < maxAttempts) {
                await new Promise((r) => setTimeout(r, 400 * attempt));
              }
            }
          }
          if (!mountedRef.current) return;
          if (currentUser) {
            setUser(currentUser);
            setAuthUnconfirmed(false);
            // Persist kiosk token only after the server confirms it is valid.
            if (urlToken && token === urlToken) {
              setAuthToken(urlToken, 'persistent');
              pendingUrlTokenRef.current = null;
            }
          } else {
            // No user: either a definitive 401 (token already cleared by
            // request()) or transient failures exhausted their retries. In the
            // transient case we deliberately keep the token and keep asking
            // in the background, so the page comes back by itself (#3360).
            setUser(null);
            if (definitiveAuthFailure) {
              setAuthToken(null);
              setAuthUnconfirmed(false);
            } else {
              setAuthUnconfirmed(true);
              runRecheckRef.current();
            }
          }
        } else {
          setUser(null);
          setAuthUnconfirmed(false);
        }
      } else {
        // Auth not enabled, allow access
        setUser(null);
        setAuthUnconfirmed(false);
      }
    } catch {
      if (!mountedRef.current) return;
      setUser(null);
      if (hasStoredToken) {
        // No answer at all, but a token is stored: wait for one rather than
        // showing the open UI or the login page (#3360).
        setAuthUnconfirmed(true);
        runRecheckRef.current();
      } else {
        setAuthEnabled(false);
      }
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  };

  // Like checkAuthStatus, mid-session: only a definite answer changes the
  // state. The 4401 that asks for it can come from a database failure, which
  // the re-check may meet too; at first load an error shows the login or the
  // open UI, but here it would sign a valid tab out or switch it to "auth
  // off" (every admin control shown) until a reload.
  // Returns whether it got a definite answer.
  const recheckAuthStatus = async (): Promise<boolean> => {
    let status;
    try {
      status = await api.getAuthStatus();
    } catch {
      return false;
    }
    if (!mountedRef.current) return true;
    setAuthEnabled(status.auth_enabled);
    setRequiresSetup(status.requires_setup);
    if (!status.auth_enabled || !getAuthToken()) {
      setUser(null);
      return true;
    }
    try {
      const currentUser = await api.getCurrentUser();
      if (mountedRef.current) {
        setUser(currentUser);
        const pending = pendingUrlTokenRef.current;
        if (pending && getAuthToken() === pending) {
          setAuthToken(pending, 'persistent');
          pendingUrlTokenRef.current = null;
        }
      }
      return true;
    } catch (err) {
      // 401: signed out. request() clears the token only for some messages,
      // not "Token no longer valid" (a password change or reset) or "Token has
      // been revoked", so it is cleared here, as the first load does. 403: an
      // API key (a SpoolBuddy kiosk) whose owner is deactivated or gone; a
      // user's JWT gets a 401 there. Signed out too, but the key is kept, as
      // at first load: reactivating the owner makes it work again, and the
      // kiosk would otherwise need setting up again. Anything else: no
      // answer, keep.
      if (!(err instanceof ApiError) || (err.status !== 401 && err.status !== 403)) return false;
      if (mountedRef.current) {
        if (err.status === 401) setAuthToken(null);
        setUser(null);
      }
      return true;
    }
  };

  // A re-check without an answer is repeated (2 s, doubling to 60 s) until it
  // gets one: after a refused token mint there is no socket left to ask again.
  const recheckTimerRef = useRef<number | null>(null);
  const recheckDelayRef = useRef(0);
  const runRecheck = async () => {
    if (recheckTimerRef.current !== null) {
      clearTimeout(recheckTimerRef.current);
      recheckTimerRef.current = null;
    }
    const answered = await recheckAuthStatus();
    if (!mountedRef.current) return;
    if (answered) {
      recheckDelayRef.current = 0;
      setAuthUnconfirmed(false);
      return;
    }
    const delay = recheckDelayRef.current ? Math.min(recheckDelayRef.current * 2, 60000) : 2000;
    recheckDelayRef.current = delay;
    recheckTimerRef.current = window.setTimeout(() => {
      recheckTimerRef.current = null;
      runRecheck();
    }, delay);
  };
  // The listener below is added once; it calls the current function.
  const runRecheckRef = useRef(runRecheck);
  runRecheckRef.current = runRecheck;

  useEffect(() => {
    mountedRef.current = true;
    // Check auth status on mount
    checkAuthStatus();

    // Listen for token-expiry events from the API client. setAuthToken(null)
    // in client.ts only clears storage; without this listener, `user` stays
    // populated and ProtectedRoute keeps rendering the protected tree until a
    // manual refresh — every request silently fails in the meantime (#1698).
    const handleAuthExpired = () => {
      if (!mountedRef.current) return;
      setUser(null);
    };
    window.addEventListener('auth:expired', handleAuthExpired);
    // A WebSocket closed with 4401: authentication may have been turned on, or
    // this session ended, while the tab still holds the old state.
    const handleAuthRecheck = () => {
      if (!mountedRef.current) return;
      runRecheckRef.current();
    };
    window.addEventListener('auth:recheck', handleAuthRecheck);

    return () => {
      mountedRef.current = false;
      window.removeEventListener('auth:expired', handleAuthExpired);
      window.removeEventListener('auth:recheck', handleAuthRecheck);
      if (recheckTimerRef.current !== null) clearTimeout(recheckTimerRef.current);
    };
  }, []);

  // Separate effect to handle redirect only when setup is required
  useEffect(() => {
    // Only redirect if setup is truly required (first time setup)
    // Don't redirect if user manually navigated to /setup or is on camera page
    if (!loading && requiresSetup && !authEnabled) {
      const currentPath = window.location.pathname;
      // Only redirect if not already on setup page or camera page, and haven't redirected yet
      if (currentPath !== '/setup' && !currentPath.startsWith('/camera/') && !hasRedirectedRef.current) {
        hasRedirectedRef.current = true;
        window.location.href = '/setup';
      }
    } else if (!requiresSetup) {
      // Reset redirect flag when setup is no longer required
      hasRedirectedRef.current = false;
    }
  }, [loading, requiresSetup, authEnabled]);

  const login = async (username: string, password: string, persistence: TokenPersistence = 'session'): Promise<LoginResponse> => {
    const response = await api.login({ username, password });
    if (!response.requires_2fa && response.access_token) {
      setAuthToken(response.access_token, persistence);
      await checkAuthStatus();
    }
    return response;
  };

  const loginWithToken = (token: string, userObj: UserResponse, persistence: TokenPersistence = 'session') => {
    setAuthToken(token, persistence);
    setUser(userObj);
    setAuthEnabled(true);
    setAuthUnconfirmed(false);
  };

  const logout = () => {
    setAuthToken(null);
    setUser(null);
    api.logout().catch(() => {
      // Ignore logout errors
    });
    window.location.href = '/login';
  };

  const refreshAuth = async () => {
    await checkAuthStatus();
  };

  // Memoize permission set for efficient lookups
  const permissionSet = useMemo(() => {
    return new Set(user?.permissions ?? []);
  }, [user?.permissions]);

  // Computed admin status
  const isAdmin = useMemo(() => {
    if (!authEnabled) return true; // Auth disabled = admin access
    return user?.is_admin ?? false;
  }, [authEnabled, user?.is_admin]);

  // Permission check functions
  const hasPermission = useCallback((permission: Permission): boolean => {
    if (!authEnabled) return true; // Auth disabled = allow all
    if (isAdmin) return true; // Admins have all permissions
    return permissionSet.has(permission);
  }, [authEnabled, isAdmin, permissionSet]);

  const hasAnyPermission = useCallback((...permissions: Permission[]): boolean => {
    if (!authEnabled) return true;
    if (isAdmin) return true;
    return permissions.some(p => permissionSet.has(p));
  }, [authEnabled, isAdmin, permissionSet]);

  // Ownership-based permission check
  const canModify = useCallback((
    resource: 'queue' | 'archives' | 'library',
    action: 'update' | 'delete' | 'reprint',
    createdById: number | null | undefined,
  ): boolean => {
    if (!authEnabled) return true;  // Auth disabled, allow all
    if (isAdmin) return true;  // Admins can modify anything

    const allPerm = `${resource}:${action}_all` as Permission;
    const ownPerm = `${resource}:${action}_own` as Permission;

    // User has *_all permission - can modify any item
    if (permissionSet.has(allPerm)) return true;

    // User has *_own permission - can only modify their own items
    if (permissionSet.has(ownPerm)) {
      // Ownerless items (null created_by_id) require *_all permission
      if (createdById == null) return false;
      return createdById === user?.id;
    }

    return false;
  }, [authEnabled, isAdmin, permissionSet, user?.id]);

  return (
    <AuthContext.Provider
      value={{
        user,
        authEnabled,
        loading,
        authUnconfirmed,
        isAdmin,
        login,
        loginWithToken,
        logout,
        refreshAuth,
        hasPermission,
        hasAnyPermission,
        canModify,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
