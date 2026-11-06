import { lazy, type ComponentType } from 'react';

/**
 * Pages load as separate files when first opened (#3175).
 *
 * An update replaces those files, and the build deletes the old ones. A tab
 * opened before the update still knows only the old names, so the next page
 * it opens fails to load. Reloading picks up the new index.html and with it
 * the new names. The time of that reload is kept in sessionStorage so a file
 * that is missing for some other reason shows the error screen instead of
 * reloading forever.
 *
 * A file also fails to load while the server is down, for example while it
 * restarts for that update. Reloading then would leave the browser on its own
 * "can't connect" page, which a SpoolBuddy kiosk cannot get off by itself, so
 * the reload waits until the server answers again.
 */

const RELOAD_AT_KEY = 'bambuddy_chunk_reload_at';
// A reload more recent than this was ours and did not help.
const RELOAD_WINDOW_MS = 10_000;
const SERVER_POLL_MS = 3_000;

let reloading = false;

/** Reload the page for a file that failed to load, at most once per window.
 *  Returns whether a reload is under way. */
export function reloadForMissingChunk(): boolean {
  if (reloading) return true;
  try {
    const last = Number(window.sessionStorage.getItem(RELOAD_AT_KEY));
    if (last && Date.now() - last < RELOAD_WINDOW_MS) return false;
    window.sessionStorage.setItem(RELOAD_AT_KEY, String(Date.now()));
  } catch {
    // No sessionStorage, no way to stop a loop: show the error instead.
    return false;
  }
  reloading = true;
  window.location.reload();
  return true;
}

/** Whether Bambuddy answers. A 5xx is a proxy saying it does not (502-504,
 *  Cloudflare's 52x). A redirect, such as an SSO proxy sending the browser to
 *  its login, is an answer: a reload then goes where it should. */
async function serverAnswers(): Promise<boolean> {
  try {
    const response = await fetch('/health', { cache: 'no-store', redirect: 'manual' });
    return response.status < 500;
  } catch {
    return false;
  }
}

/** After a file failed to load: wait until the server answers, then reload
 *  once. Resolves false when a reload would not help. */
export async function recoverFromMissingChunk(): Promise<boolean> {
  while (!(await serverAnswers())) {
    await new Promise((resolve) => setTimeout(resolve, SERVER_POLL_MS));
  }
  return reloadForMissingChunk();
}

/** A page component that loads on first render.
 *  `name` picks the export, since most pages are named exports. */
export function lazyPage<M extends Record<string, unknown>, K extends keyof M>(
  load: () => Promise<M>,
  name: K,
) {
  return lazy(async () => {
    try {
      const module = await load();
      return { default: module[name] as ComponentType };
    } catch (error) {
      // The loading spinner stays up while the server is away and the page
      // reloads; only a reload that did not help reaches the error screen.
      if (await recoverFromMissingChunk()) return new Promise<never>(() => {});
      throw error;
    }
  });
}
