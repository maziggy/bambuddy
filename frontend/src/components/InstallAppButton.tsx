import { useState, useEffect } from 'react';
import { Download } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useToast } from '../contexts/ToastContext';

// The beforeinstallprompt event is not in the standard TS DOM lib.
interface BeforeInstallPromptEvent extends Event {
  readonly platforms: string[];
  prompt: () => Promise<void>;
  readonly userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
}

/**
 * Sidebar-footer button that installs Bambuddy as a PWA.
 *
 * Chrome for Android removed the automatic install banner in Chrome 108, so
 * without an in-app trigger the only install path on Android is a buried
 * browser-menu item (#1460). This button captures the `beforeinstallprompt`
 * event and re-fires it on click. It renders nothing when the browser has no
 * pending prompt - already installed, unsupported browser, or iOS Safari
 * (which has no programmatic install at all).
 */
export function InstallAppButton() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    const onBeforeInstallPrompt = (e: Event) => {
      // Suppress Chrome's own mini-infobar (desktop) so this button is the
      // single, predictable install entry point.
      e.preventDefault();
      setPromptEvent(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => setPromptEvent(null);
    window.addEventListener('beforeinstallprompt', onBeforeInstallPrompt);
    window.addEventListener('appinstalled', onInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', onBeforeInstallPrompt);
      window.removeEventListener('appinstalled', onInstalled);
    };
  }, []);

  if (!promptEvent) {
    return null;
  }

  const handleInstall = async () => {
    await promptEvent.prompt();
    const { outcome } = await promptEvent.userChoice;
    // A captured prompt can only be used once; drop it either way so the
    // button hides until the browser fires a fresh beforeinstallprompt.
    setPromptEvent(null);
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
