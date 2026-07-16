import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, getAuthToken, setAuthToken } from '../api/client';
import type { LoginResponse, Permission, TokenPersistence, UserResponse } from '../api/client';

interface AuthContextType {
  user: UserResponse | null;
  authEnabled: boolean;
  requiresSetup: boolean;
  loading: boolean;
  isAdmin: boolean;
  /** Login with username/password. Returns LoginResponse (may include requires_2fa). */
  login: (username: string, password: string, persistence?: TokenPersistence) => Promise<LoginResponse>;
  /** Finalise login after 2FA or OIDC — store token and set user directly. */
  loginWithToken: (token: string, user: UserResponse, persistence?: TokenPersistence) => void;
  logout: () => void;
  refreshUser: () => Promise<void>;
  refreshAuth: () => Promise<void>;
  hasPermission: (permission: Permission) => boolean;
  hasAnyPermission: (...permissions: Permission[]) => boolean;
  hasAllPermissions: (...permissions: Permission[]) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserResponse | null>(null);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [requiresSetup, setRequiresSetup] = useState(false);
  const [loading, setLoading] = useState(true);
  const hasRedirectedRef = useRef(false);
  const mountedRef = useRef(true);

  const checkAuthStatus = async () => {
    try {
      // Bootstrap: if URL has ?token= param, store it session-only first and
      // strip it from the URL. Allows SpoolBuddy kiosk to pass an API key via
      // URL on first load. Persistence to localStorage is deferred until the
      // token has been verified by the server (L-4: prevents session fixation
      // where an attacker-crafted URL immediately persists a forged/stolen token).
      const urlParams = new URLSearchParams(window.location.search);
      const urlToken = urlParams.get('token');
      if (urlToken) {
        setAuthToken(urlToken, 'session'); // session-only until server confirms it's valid
        urlParams.delete('token');
        const cleanSearch = urlParams.toString();
        const cleanUrl = window.location.pathname
          + (cleanSearch ? `?${cleanSearch}` : '')
          + window.location.hash;
        window.history.replaceState({}, '', cleanUrl);
      }

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
            // Persist kiosk token only after the server confirms it is valid.
            if (urlToken && token === urlToken) {
              setAuthToken(urlToken, 'persistent');
            }
          } else {
            // No user: either a definitive 401 (token already cleared by
            // request()) or transient failures exhausted their retries. In the
            // transient case we deliberately keep the token so a reload retries
            // rather than forcing a re-login.
            if (definitiveAuthFailure) {
              setAuthToken(null);
            }
            setUser(null);
          }
        } else {
          setUser(null);
        }
      } else {
        // Auth not enabled, allow access
        setUser(null);
      }
    } catch {
      if (!mountedRef.current) return;
      setAuthEnabled(false);
      setUser(null);
    } finally {
      if (mountedRef.current) {
        setLoading(false);
      }
    }
  };

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

    return () => {
      mountedRef.current = false;
      window.removeEventListener('auth:expired', handleAuthExpired);
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
  };

  const logout = () => {
    setAuthToken(null);
    setUser(null);
    api.logout().catch(() => {
      // Ignore logout errors
    });
    window.location.href = '/login';
  };

  const refreshUser = async () => {
    if (authEnabled && getAuthToken()) {
      try {
        const currentUser = await api.getCurrentUser();
        if (mountedRef.current) {
          setUser(currentUser);
        }
      } catch {
        setAuthToken(null);
        if (mountedRef.current) {
          setUser(null);
        }
      }
    }
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

  const hasAllPermissions = useCallback((...permissions: Permission[]): boolean => {
    if (!authEnabled) return true;
    if (isAdmin) return true;
    return permissions.every(p => permissionSet.has(p));
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
        requiresSetup,
        loading,
        isAdmin,
        login,
        loginWithToken,
        logout,
        refreshUser,
        refreshAuth,
        hasPermission,
        hasAnyPermission,
        hasAllPermissions,
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
