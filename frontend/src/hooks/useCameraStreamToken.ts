import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, setStreamToken, withStreamToken } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

/**
 * Fetches and caches a stream token for <img>/<video> src URLs.
 * Stores the token globally via setStreamToken() so URL generators
 * in client.ts can use withStreamToken() automatically.
 *
 * Mount this hook once near the app root (e.g., in App.tsx or a layout component).
 * Components that need token-protected URLs can import withStreamToken directly.
 */
export function useStreamTokenSync() {
  const { authEnabled } = useAuth();

  const { data } = useQuery({
    queryKey: ['camera-stream-token'],
    queryFn: () => api.getCameraStreamToken(),
    enabled: authEnabled,
    staleTime: 50 * 60 * 1000, // refresh at 50 min (tokens expire at 60)
    refetchInterval: 50 * 60 * 1000,
  });

  useEffect(() => {
    setStreamToken(data?.token ?? null);
    return () => setStreamToken(null);
  }, [data?.token]);
}

/**
 * Hook for components that need to wrap URLs with the stream token.
 * Returns a withToken function that appends ?token=xxx when auth is enabled.
 */
export function useCameraStreamToken() {
  return { withToken: withStreamToken };
}
