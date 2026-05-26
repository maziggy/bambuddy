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

// Matches the compact-sidebar breakpoint in useIsSidebarCompact.ts
const SIDEBAR_COMPACT_BREAKPOINT = 1144;

/**
 * Sidebar-footer button that installs Bambuddy as a PWA.
 *
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
  const [promptEvent, setPromptEvent] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    const onBeforeInstallPrompt = (e: Event) => {
      // On desktop the sidebar button is always visible, so suppress the
      // mini-infobar and use it as the sole install trigger.
      // On mobile the button is hidden inside the drawer, so let Chrome's
      // native install UI (mini-infobar / rich dialog) appear automatically.
      if (window.innerWidth >= SIDEBAR_COMPACT_BREAKPOINT) {
        e.preventDefault();
      }
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
