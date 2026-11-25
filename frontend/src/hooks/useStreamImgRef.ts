import { useCallback, type MutableRefObject } from 'react';

/**
 * A ref for a camera stream <img> that gives it its src only once React has
 * put it on the page, and closes the stream of every img the component drops:
 * unmounted, re-keyed, or hidden by a conditional.
 *
 * Pass the stream URL here and render the img WITHOUT a src prop. React sets
 * an element's props while it renders, before anything is committed, and the
 * browser opens an img's request the moment its src is set, attached or not.
 * A render React throws away (an interrupted or restarted one) never commits,
 * so no ref sees its img and nothing can close it: one camera overlay mounting
 * during page load opened about twenty MJPEG streams that way, each held open
 * on a detached img until the tab closed (#3431). Set here, the src only ever
 * reaches an img that is on the page.
 *
 * The src is (re)applied when the img is attached and when the URL changes,
 * not on other re-renders: the viewers clear it themselves to close a stream
 * (reconnect, restart, closing), and an unrelated re-render must not reopen it.
 * ``undefined`` or '' removes it, as React does for an empty src prop.
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
 * Takes the component's own ref (kept up to date with the img shown) and the
 * stream URL, and returns the callback to pass as the img's ref.
 */
export function useStreamImgRef(imgRef: MutableRefObject<HTMLImageElement | null>, src: string | undefined) {
  return useCallback(
    (img: HTMLImageElement | null) => {
      imgRef.current = img;
      if (!img) return;
      if (!src) {
        if (img.hasAttribute('src')) img.removeAttribute('src');
      } else if (img.getAttribute('src') !== src) {
        img.src = src;
      }
      return () => {
        if (imgRef.current === img) imgRef.current = null;
        queueMicrotask(() => {
          if (!img.isConnected) img.removeAttribute('src');
        });
      };
    },
    [imgRef, src],
  );
}
