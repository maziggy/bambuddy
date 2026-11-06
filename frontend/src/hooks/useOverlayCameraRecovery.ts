import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from 'react';

// MJPEG <img> connections can stop delivering frames without firing onError.
// Renew only the camera connection, keeping the overlay and its credentials.
// This also renews healthy connections; it does not measure frame freshness.
const STREAM_RENEWAL_MS = 60_000;
const ERROR_RETRY_MS = 3_000;
// The old stream is closed this long before the new one opens: a detached img
// keeps its connection open (measured in Chromium, which OBS browser sources
// run on), and a USB camera serves one reader.
const RENEWAL_GAP_MS = 200;

export function useOverlayCameraRecovery(enabled: boolean) {
  const [imageKey, setImageKey] = useState(Date.now);
  // While true the img renders with no src, so no re-render reopens it early.
  const [closing, setClosing] = useState(false);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const close = useCallback(() => {
    // Closed now, not at the next render, which may already be the reopen.
    imgRef.current?.removeAttribute('src');
    setClosing(true);
  }, []);

  const renew = useCallback(() => {
    if (retryTimer.current !== null) {
      clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
    setClosing(false);
    setImageKey((key) => key + 1);
  }, []);

  useEffect(() => {
    if (!enabled) return;
    // Start a fresh deadline after each retry so a newly opened connection
    // isn't immediately replaced by the previous connection's renewal timer.
    const closeTimer = setTimeout(close, STREAM_RENEWAL_MS - RENEWAL_GAP_MS);
    const renewalTimer = setTimeout(renew, STREAM_RENEWAL_MS);
    return () => {
      clearTimeout(closeTimer);
      clearTimeout(renewalTimer);
      if (retryTimer.current !== null) {
        clearTimeout(retryTimer.current);
        retryTimer.current = null;
      }
      setClosing(false);
    };
  }, [enabled, imageKey, renew, close]);

  const handleStreamError = useCallback(
    (event?: SyntheticEvent<HTMLImageElement>) => {
      if (!enabled || retryTimer.current !== null) return;
      // Closing the stream (src removed) fires error too.
      if (event && !event.currentTarget.getAttribute('src')) return;
      close();
      retryTimer.current = setTimeout(renew, ERROR_RETRY_MS);
    },
    [enabled, renew, close],
  );

  return { imageKey, closing, imgRef, handleStreamError };
}
