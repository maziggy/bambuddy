import { useCallback, type MutableRefObject } from 'react';

/**
 * A ref for a camera stream <img> that closes the stream of every img the
 * component drops: unmounted, re-keyed, or hidden by a conditional.
 *
 * A detached img keeps its MJPEG connection open (measured in Chromium) until
 * the browser closes: the backend stream, the chamber light it holds and the
 * /camera/stop it blocks stay with it. Removing the src closes it, also on a
 * detached img. Done once React's commit is over and only for an img no longer
 * in the page: React runs a ref's cleanup with the img still attached, and in
 * development (StrictMode) also for an img that stays.
 *
 * Removing the src fires no event. Setting it to '' (the viewers' reconnect and
 * restart code does, on the img still shown) fires error with an empty src:
 * error handlers must ignore an img whose src is empty or missing.
 *
 * Takes the component's own ref (kept up to date with the img shown) and
 * returns the callback to pass as the img's ref.
 */
export function useStreamImgRef(imgRef: MutableRefObject<HTMLImageElement | null>) {
  return useCallback(
    (img: HTMLImageElement | null) => {
      imgRef.current = img;
      if (!img) return;
      return () => {
        if (imgRef.current === img) imgRef.current = null;
        queueMicrotask(() => {
          if (!img.isConnected) img.removeAttribute('src');
        });
      };
    },
    [imgRef],
  );
}
