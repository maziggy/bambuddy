import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  api,
  setStreamToken,
  getStreamToken,
  setMediaToken,
  getMediaToken,
  withStreamToken,
  withMediaToken,
} from '../api/client';
import { useAuth } from '../contexts/AuthContext';

/** True for the three live-camera routes, which take the camera stream token.
 *  Everything else under /api/v1/ that a browser loads as an element src is
 *  media and takes the media token (#3025). */
export function isCameraUrl(src: string): boolean {
  return src.includes('/camera/');
}

/**
 * Walks the DOM and updates every <img>/<video> pointing at /api/v1/ so its
 * src carries the right token: the camera token for live-camera URLs, the
 * media token for everything else. Either may be null -- a user without
 * camera:view has no camera token, and their thumbnails must still be
 * rewritten. Exported for unit testing; called from useStreamTokenSync when a
 * token arrives after first render.
 */
export function rewriteMediaSrcWithToken(
  root: ParentNode,
  mediaToken: string | null,
  cameraToken: string | null
): number {
  let updated = 0;
  root
    .querySelectorAll<HTMLImageElement | HTMLVideoElement>(
      'img[src*="/api/v1/"], video[src*="/api/v1/"]'
    )
    .forEach((el) => {
      const src = el.getAttribute('src') || '';
      const token = isCameraUrl(src) ? cameraToken : mediaToken;
      if (!token) return;
      const tokenParam = `token=${encodeURIComponent(token)}`;
      if (src.includes(tokenParam)) return;
      const withoutToken = src.replace(/([?&])token=[^&]*(&|$)/, (_m, pre, post) =>
        post === '&' ? pre : pre === '?' ? '' : ''
      );
      const sep = withoutToken.includes('?') ? '&' : '?';
      el.src = `${withoutToken}${sep}${tokenParam}`;
      updated += 1;
    });
  return updated;
}

/**
 * Fetches and caches the query-param tokens <img>/<video> src URLs need, and
 * publishes them through setMediaToken() / setStreamToken() so the URL
 * generators in client.ts pick them up automatically.
 *
 * Two tokens, fetched independently (#3025):
 *
 *   media  — every signed-in user gets one. Thumbnails, plate previews,
 *            timelapses, cover images and link icons ride on it.
 *   camera — only users with camera:view, because that is what minting one
 *            costs. Asking for it unconditionally would 403 on every page
 *            load for everyone else.
 *
 * Also listens for global image/video load errors on token-protected URLs and
 * refreshes the matching token (e.g. after a backend restart drops them).
 *
 * Mount this hook once near the app root. Components that need token-protected
 * URLs can import withMediaToken / withStreamToken directly.
 */
export function useStreamTokenSync() {
  const { authEnabled, user, loading: authLoading, hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const refreshingRef = useRef(false);

  // Key the tokens by user id so a login/logout invalidates the cache
  // automatically — otherwise a failed anonymous fetch on the login page
  // would be cached and never retried after sign-in.
  //
  // Race-aware gate (same shape as ColorCatalogProvider): wait for
  // ``checkAuthStatus`` to finish before deciding whether to fetch.
  // The previous form ``authEnabled ? !!user : true`` evaluated to
  // ``true`` on first render because ``authEnabled`` defaults to false,
  // firing a 401 POST on the login page before AuthContext had a chance
  // to settle on ``authEnabled=true, user=null``.
  const signedIn = !authLoading && (!authEnabled || user !== null);

  const { data: mediaData } = useQuery({
    queryKey: ['media-token', user?.id ?? null],
    queryFn: () => api.getMediaToken(),
    enabled: signedIn,
    staleTime: 50 * 60 * 1000, // refresh at 50 min (tokens expire at 60)
    refetchInterval: 50 * 60 * 1000,
  });

  // Only ask for a camera token when the user may actually have one. When auth
  // is disabled hasPermission() is vacuously true, which is correct — the mint
  // endpoint is open then too.
  const canViewCamera = !authEnabled || hasPermission('camera:view');

  const { data: cameraData } = useQuery({
    queryKey: ['camera-stream-token', user?.id ?? null],
    queryFn: () => api.getCameraStreamToken(),
    enabled: signedIn && canViewCamera,
    staleTime: 50 * 60 * 1000,
    refetchInterval: 50 * 60 * 1000,
  });

  const mediaTokenValue = mediaData?.token ?? null;
  const cameraTokenValue = cameraData?.token ?? null;

  useEffect(() => {
    setMediaToken(mediaTokenValue);
    setStreamToken(cameraTokenValue);

    // Images/videos that rendered before a token arrived have src URLs
    // without ?token=…; update them in place so they reload with auth.
    if (mediaTokenValue || cameraTokenValue) {
      rewriteMediaSrcWithToken(document, mediaTokenValue, cameraTokenValue);
    }

    return () => {
      setMediaToken(null);
      setStreamToken(null);
    };
  }, [mediaTokenValue, cameraTokenValue]);

  // Listen for image/video load errors on token-protected URLs.
  // When the backend restarts, in-memory tokens are lost and all
  // thumbnail/stream requests return 401. This handler detects that and
  // forces a refresh of whichever token the failing URL used, so images
  // recover without a page reload.
  useEffect(() => {
    if (!authEnabled) return;

    const handleError = (event: Event) => {
      const el = event.target;
      if (!(el instanceof HTMLImageElement || el instanceof HTMLVideoElement)) return;

      const src = el.src || '';
      const camera = isCameraUrl(src);
      const token = camera ? getStreamToken() : getMediaToken();
      if (!token || !src.includes(`token=${encodeURIComponent(token)}`)) return;

      // This image/video used one of our tokens and failed — likely invalid
      if (refreshingRef.current) return;
      refreshingRef.current = true;

      queryClient.invalidateQueries({
        queryKey: camera ? ['camera-stream-token'] : ['media-token'],
      });

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
 * Hook for components that need to wrap camera URLs with the stream token.
 * Returns a withToken function that appends ?token=xxx when auth is enabled.
 */
export function useCameraStreamToken() {
  return { withToken: withStreamToken };
}

/**
 * Hook for components that need to wrap media URLs with the media token.
 */
export function useMediaToken() {
  return { withToken: withMediaToken };
}
