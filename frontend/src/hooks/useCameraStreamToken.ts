import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, setStreamToken, getStreamToken, withStreamToken } from '../api/client';
import { useAuth } from '../contexts/AuthContext';

/**
 * Fetches and caches a stream token for <img>/<video> src URLs.
 * Stores the token globally via setStreamToken() so URL generators
 * in client.ts can use withStreamToken() automatically.
 *
 * Also listens for global image load errors on token-protected URLs
 * and automatically refreshes the token (e.g., after backend restart
 * invalidates in-memory tokens).
 *
 * Mount this hook once near the app root (e.g., in App.tsx or a layout component).
 * Components that need token-protected URLs can import withStreamToken directly.
 */
export function useStreamTokenSync() {
  const { authEnabled } = useAuth();
  const queryClient = useQueryClient();
  const refreshingRef = useRef(false);

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

  // Listen for image/video load errors on token-protected URLs.
  // When the backend restarts, in-memory stream tokens are lost and all
  // thumbnail/stream requests return 401. This handler detects that and
  // forces a token refresh so images recover without a page reload.
  useEffect(() => {
    if (!authEnabled) return;

    const handleError = (event: Event) => {
      const el = event.target;
      if (!(el instanceof HTMLImageElement || el instanceof HTMLVideoElement)) return;

      const src = el.src || '';
      const token = getStreamToken();
      if (!token || !src.includes(`token=${encodeURIComponent(token)}`)) return;

      // This image/video used our stream token and failed — token likely invalid
      if (refreshingRef.current) return;
      refreshingRef.current = true;

      queryClient.invalidateQueries({ queryKey: ['camera-stream-token'] });

      // Reset after a delay so future errors can trigger another refresh
      setTimeout(() => {
        refreshingRef.current = false;
      }, 5000);
    };

    // Use capture phase to catch errors before they're swallowed
    document.addEventListener('error', handleError, true);
    return () => document.removeEventListener('error', handleError, true);
  }, [authEnabled, queryClient]);
}

/**
 * Hook for components that need to wrap URLs with the stream token.
 * Returns a withToken function that appends ?token=xxx when auth is enabled.
 */
export function useCameraStreamToken() {
  return { withToken: withStreamToken };
}
