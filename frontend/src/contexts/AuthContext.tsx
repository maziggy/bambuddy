import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { api, getAuthToken, setAuthToken } from '../api/client';
import type { Permission, UserResponse } from '../api/client';

interface AuthContextType {
  user: UserResponse | null;
  authEnabled: boolean;
  requiresSetup: boolean;
  loading: boolean;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  refreshUser: () => Promise<void>;
  refreshAuth: () => Promise<void>;
  hasPermission: (permission: Permission) => boolean;
  hasAnyPermission: (...permissions: Permission[]) => boolean;
  hasAllPermissions: (...permissions: Permission[]) => boolean;
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
      const status = await api.getAuthStatus();
      if (!mountedRef.current) return;
      setAuthEnabled(status.auth_enabled);
      setRequiresSetup(status.requires_setup);

      if (status.auth_enabled) {
        const token = getAuthToken();
        if (token) {
          try {
            const currentUser = await api.getCurrentUser();
            if (!mountedRef.current) return;
            setUser(currentUser);
          } catch {
            // Token invalid, clear it
            setAuthToken(null);
            if (!mountedRef.current) return;
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
    return () => {
      mountedRef.current = false;
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

  const login = async (username: string, password: string) => {
    const response = await api.login({ username, password });
    setAuthToken(response.access_token);
    setUser(response.user);
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

  return (
    <AuthContext.Provider
      value={{
        user,
        authEnabled,
        requiresSetup,
        loading,
        isAdmin,
        login,
        logout,
        refreshUser,
        refreshAuth,
        hasPermission,
        hasAnyPermission,
        hasAllPermissions,
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
