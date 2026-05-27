import { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../contexts/ToastContext';
import { type BeforeInstallPromptEvent, getPendingPrompt, clearPendingPrompt } from '../pwa';

/**
 * Sidebar-footer button that installs Bambuddy as a PWA.
 *
 * On desktop (>= 1144 px) the button is always visible in the sidebar, so
 * pwa.ts suppresses Chrome's mini-infobar and this button is the sole install
 * trigger.
 *
 * On mobile (< 1144 px) pwa.ts does NOT call e.preventDefault(), so Chrome's
 * native mini-infobar fires automatically.  The button remains available inside
 * the hamburger drawer as a secondary path.
 *
 * The event is captured in pwa.ts (imported before React mounts) to avoid
 * losing it during the auth-loading phase when this component hasn't mounted yet.
 *
 * Renders nothing when there is no pending prompt (already installed,
 * unsupported browser, or iOS Safari which has no programmatic install API).
 */
export function InstallAppButton() {
  const { t } = useTranslation();
  const { showToast } = useToast();

  // Lazy initialiser: pick up any prompt that fired before this component mounted.
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(
    () => getPendingPrompt(),
  );

  useEffect(() => {
    // Listen for future events (Chrome re-fires beforeinstallprompt after the
    // PWA is uninstalled; pwa.ts recaptures and relays it as a custom event).
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

  if (!promptEvent) {
    return null;
  }

  const handleInstall = async () => {
    await promptEvent.prompt();
    const { outcome } = await promptEvent.userChoice;
    // A captured prompt can only be used once; clear it either way.
    setPromptEvent(null);
    clearPendingPrompt();
    if (outcome === 'accepted') {
      showToast(t('nav.installAppSuccess'), 'success');
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
