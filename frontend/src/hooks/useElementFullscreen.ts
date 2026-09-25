import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

// True where the unprefixed Fullscreen API can be used on an arbitrary
// element. iPhone Safari only offers it for <video>, and an <iframe> without
// allowfullscreen reports fullscreenEnabled === false.
export function fullscreenApiAvailable(): boolean {
  return (
    typeof document !== 'undefined' &&
    document.fullscreenEnabled === true &&
    typeof document.documentElement.requestFullscreen === 'function'
  );
}

/**
 * Fullscreen for a preview panel (#2976).
 *
 * Uses the Fullscreen API on the element behind `ref` where the browser has
 * it, and otherwise a viewport-filling layout driven by the same
 * `isFullscreen` flag, so the toggle keeps working on iPhone Safari and in
 * embedded views that refuse the request (a webview without the fullscreen
 * permission rejects it even though `fullscreenEnabled` says yes). In API
 * mode the flag follows `fullscreenchange`: Esc is handled by the browser and
 * simply shows up as a change, and the panel leaves fullscreen on unmount so
 * closing the modal never strands the document in fullscreen.
 */
export function useElementFullscreen(ref: RefObject<HTMLElement | null>) {
  const apiAvailable = fullscreenApiAvailable();
  const [isFullscreen, setIsFullscreen] = useState(false);
  // True while the viewport-filling fallback is what the user sees.
  const fallbackActiveRef = useRef(false);

  useEffect(() => {
    if (!apiAvailable) return;
    const element = ref.current;
    const sync = () => {
      if (fallbackActiveRef.current) return;
      setIsFullscreen(document.fullscreenElement != null && document.fullscreenElement === ref.current);
    };
    document.addEventListener('fullscreenchange', sync);
    return () => {
      document.removeEventListener('fullscreenchange', sync);
      if (element && document.fullscreenElement === element) {
        Promise.resolve(document.exitFullscreen()).catch(() => {});
      }
    };
  }, [apiAvailable, ref]);

  const toggleFullscreen = useCallback(() => {
    if (fallbackActiveRef.current) {
      fallbackActiveRef.current = false;
      setIsFullscreen(false);
      return;
    }
    if (apiAvailable && document.fullscreenElement) {
      Promise.resolve(document.exitFullscreen()).catch(() => {});
      return;
    }
    const element = ref.current;
    if (!element) return;
    const enterFallback = () => {
      fallbackActiveRef.current = true;
      setIsFullscreen(true);
    };
    if (!apiAvailable) {
      enterFallback();
      return;
    }
    Promise.resolve(element.requestFullscreen()).catch(enterFallback);
  }, [apiAvailable, ref]);

  return { isFullscreen, toggleFullscreen, apiAvailable };
}
