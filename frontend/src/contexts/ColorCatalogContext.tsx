import { useEffect, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';
import { setColorCatalog } from '../utils/colors';
import { useAuth } from './AuthContext';

/**
 * Loads the backend color catalog once per session and pushes it into
 * utils/colors.ts so getColorName/resolveSpoolColorName can do synchronous
 * lookups from render paths (JSX `title={...}`, table cells, etc.) without
 * threading a hook through every call site.
 *
 * Gated on authentication state because the backend endpoint requires a valid
 * session when auth is enabled — firing before login would just 401 and retry.
 */
export function ColorCatalogProvider({ children }: { children: ReactNode }) {
  const { authEnabled, user, loading: authLoading } = useAuth();

  // Fire when auth state is resolved AND we're actually allowed to hit the API.
  // When auth is disabled, `user` is null but the endpoint accepts anyone —
  // only gate on `!authLoading`. When auth is enabled, we need a logged-in user
  // or the request 401s and gets retried in a loop.
  const enabled = !authLoading && (!authEnabled || user !== null);

  const { data } = useQuery({
    queryKey: ['color-catalog-map'],
    queryFn: async () => {
      const response = await api.getColorNameMap();
      return response.colors;
    },
    // Catalog rarely changes during a session; no background refetch needed.
    staleTime: Infinity,
    gcTime: Infinity,
    retry: 2,
    enabled,
  });

  useEffect(() => {
    if (data) {
      setColorCatalog(data);
    }
  }, [data]);

  return <>{children}</>;
}
