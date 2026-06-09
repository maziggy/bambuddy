import { useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { useOnboarding } from '../../contexts/OnboardingContext';
import { WelcomeModal } from './WelcomeModal';
import { AboutModal } from './AboutModal';
import { TourEngine } from './TourEngine';

// Routes where the onboarding overlay must never render. Setup is the
// fresh-install auth bootstrap and must be free of overlays; login is
// pre-auth; the SpoolBuddy kiosk + standalone camera/overlay pages each have
// their own layout and would be visually broken by a modal slapped over them.
const SUPPRESS_PREFIXES = ['/spoolbuddy', '/camera/', '/overlay/'];
const SUPPRESS_EXACT = new Set(['/setup', '/login']);

type Phase = 'welcome' | 'about' | 'done';

/**
 * Top-level driver for the onboarding surface. Three things can render
 * depending on backend state + local phase:
 *
 *   1. The step-by-step tour overlay (when status starts with
 *      `tour_in_progress:`)
 *   2. The Phase 0 welcome modal (when status is null OR a snooze has
 *      elapsed)
 *   3. The Phase 0 about modal (after the user clicks "Start tour" in the
 *      welcome modal — tracked via local phase state)
 *
 * Hidden in every other backend state (dismissed / completed_tour /
 * dismissed_at_migration).
 */
export function OnboardingFlow() {
  const { status, snoozedUntil, isLoaded, loadFailed } = useOnboarding();
  const { requiresSetup } = useAuth();
  const location = useLocation();
  const [phase, setPhase] = useState<Phase>('welcome');

  const onSuppressedRoute = useMemo(() => {
    if (SUPPRESS_EXACT.has(location.pathname)) return true;
    return SUPPRESS_PREFIXES.some((p) => location.pathname.startsWith(p));
  }, [location.pathname]);

  const shouldShowPhase0 = useMemo(() => {
    if (!isLoaded || loadFailed) return false;
    if (status === null) return true;
    if (status === 'snoozed' && snoozedUntil) {
      const snoozeMs = new Date(snoozedUntil).getTime();
      if (!Number.isNaN(snoozeMs) && snoozeMs <= Date.now()) return true;
    }
    return false;
  }, [status, snoozedUntil, isLoaded, loadFailed]);

  // Fresh install is still on the /setup flow — don't pop anything yet.
  // Standalone routes (login / kiosk / camera / overlay) also stay clear.
  if (requiresSetup || onSuppressedRoute) return null;

  // Tour engine takes priority — once `tour_in_progress:<step>` is the live
  // state, the engine owns the screen until the user finishes or skips.
  if (status?.startsWith('tour_in_progress:')) {
    return <TourEngine />;
  }

  if (!shouldShowPhase0 || phase === 'done') return null;

  if (phase === 'welcome') {
    return (
      <WelcomeModal
        onStartTour={() => setPhase('about')}
        onClose={() => setPhase('done')}
      />
    );
  }

  return <AboutModal onClose={() => setPhase('done')} />;
}
