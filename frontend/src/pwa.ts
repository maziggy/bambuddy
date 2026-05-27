/**
 * PWA install-prompt capture — must be imported before React mounts.
 *
 * `beforeinstallprompt` fires once, shortly after the page `load` event.
 * `InstallAppButton` is buried inside `ProtectedRoute → Layout`, which is only
 * rendered after the auth API call resolves.  If the event fires during that
 * loading window the component-level `useEffect` listener doesn't exist yet
 * and the event is silently dropped.
 *
 * Importing this module in main.tsx (before `createRoot`) installs the global
 * listener synchronously so no event can ever be missed.  The captured prompt
 * is retrieved via `getPendingPrompt()` and re-broadcast via a custom event
 * for any component that mounts later.
 */

// The BeforeInstallPromptEvent is not in the standard TS DOM lib.
export interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  prompt(): Promise<void>;
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

// Must match the breakpoint in InstallAppButton / useIsSidebarCompact.
const SIDEBAR_COMPACT_BREAKPOINT = 1144;

let _pendingPrompt: BeforeInstallPromptEvent | null = null;

/** Returns the most recently captured install prompt, or null. */
export function getPendingPrompt(): BeforeInstallPromptEvent | null {
  return _pendingPrompt;
}

/** Called by InstallAppButton after the prompt is consumed or dismissed. */
export function clearPendingPrompt(): void {
  _pendingPrompt = null;
}

window.addEventListener('beforeinstallprompt', (e) => {
  // On desktop the sidebar install button is always visible, so suppress
  // Chrome's mini-infobar and use the button as the sole install trigger.
  // On mobile the button is buried inside the hamburger drawer, so let the
  // mini-infobar appear naturally as the primary prompt.
  if (window.innerWidth >= SIDEBAR_COMPACT_BREAKPOINT) {
    e.preventDefault();
  }
  _pendingPrompt = e as BeforeInstallPromptEvent;
  // Notify any already-mounted components (e.g. after an in-app uninstall
  // Chrome re-fires the event; we relay it via a custom event).
  window.dispatchEvent(
    new CustomEvent<BeforeInstallPromptEvent>('bambuddy:installprompt', { detail: _pendingPrompt }),
  );
});
