import { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../contexts/ToastContext';
import { type BeforeInstallPromptEvent, getPendingPrompt, clearPendingPrompt } from '../pwa';

/**
 * Returns true when running inside a browser (not a PWA standalone window).
 * Used to hide the button once the user has installed and reopened as a PWA.
 */
function isInStandaloneMode(): boolean {
  return (
    window.matchMedia('(display-mode: standalone)').matches ||
    // Safari/iOS sets this property instead
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

/**
 * Returns true on stock Android Chrome (not Edge, Opera, Samsung Browser).
 * These browsers all fire beforeinstallprompt and support programmatic install.
 */
function isAndroidChrome(): boolean {
  const ua = navigator.userAgent;
  return (
    /Android/.test(ua) &&
    /Chrome\//.test(ua) &&
    !/EdgA|OPR\/|SamsungBrowser/.test(ua)
  );
}

/**
 * Sidebar-footer button that installs Bambuddy as a PWA.
 *
 * Two modes:
 *
 * 1. **Native prompt** — when Chrome has fired `beforeinstallprompt` (captured
 *    early in pwa.ts before React mounts), clicking triggers the browser's
 *    native install dialog directly.
 *
 * 2. **Manual fallback** — when Chrome hasn't fired the event (e.g. a per-site
 *    cooldown from previous e.preventDefault() calls), the button is still shown
 *    on Android Chrome so the user gets an actionable hint.  Clicking shows a
 *    toast: "Tap ⋮ → Add to Home Screen".
 *
 * On desktop (>= 1144 px) pwa.ts calls e.preventDefault() so only the sidebar
 * button triggers install (never the mini-infobar).
 *
 * Returns null when already running as installed PWA, or on browsers / platforms
 * that have no install path (iOS Safari, Firefox, desktop without a prompt).
 * On desktop (>= 1144px) this button is always visible in the sidebar, so we
 * suppress Chrome's mini-infobar and use this as the single install entry point.
 *
 * On mobile (< 1144px) the button is buried inside the hamburger drawer, so we
 * do NOT suppress the mini-infobar — Chrome's native install UI fires automatically
 * and the drawer button remains available as a secondary path.
 *
 * Renders nothing when there is no pending prompt (already installed, unsupported
 * browser, or iOS Safari which has no programmatic install API).
 */
export function InstallAppButton() {
  const { t } = useTranslation();
  const { showToast } = useToast();

  // Lazy initialiser: pick up any prompt that fired before this component mounted.
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(
    () => getPendingPrompt(),
  );

  useEffect(() => {
    // Listen for future events (Chrome re-fires after uninstall; pwa.ts relays it).
    const onPrompt = (e: Event) => {
      setPromptEvent((e as CustomEvent<BeforeInstallPromptEvent>).detail);
    };
    const onInstalled = () => {
      setPromptEvent(null);
      clearPendingPrompt();
    };
    window.addEventListener('bambuddy:installprompt', onPrompt);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('bambuddy:installprompt', onPrompt);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  // Never show the button once the app is running as an installed PWA.
  if (isInStandaloneMode()) return null;

  // If we have the native prompt, we can show the button on any platform.
  // If not, only show the manual-fallback button on Android Chrome — it's the
  // only mobile browser where we know "Add to Home Screen" is in the ⋮ menu.
  if (!promptEvent && !isAndroidChrome()) return null;

  const handleInstall = async () => {
    if (promptEvent) {
      // Native flow: Chrome shows its own install dialog.
      await promptEvent.prompt();
      const { outcome } = await promptEvent.userChoice;
      setPromptEvent(null);
      clearPendingPrompt();
      if (outcome === 'accepted') {
        showToast(t('nav.installAppSuccess'), 'success');
      }
    } else {
      // Manual fallback: Chrome is in cooldown or hasn't offered the prompt yet.
      // Guide the user to the browser's own install path.
      showToast(
        t('nav.installAppManual', {
          defaultValue: 'Tap the ⋮ menu → "Install app" or "Add to Home Screen" to install',
        }),
        'info',
      );
    }
  };

  return (
    <button
      onClick={handleInstall}
      className="p-2 rounded-lg hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray-light hover:text-white"
      title={t('nav.installApp')}
      aria-label={t('nav.installApp')}
    >
      <Download className="w-5 h-5" />
    </button>
  );
}
