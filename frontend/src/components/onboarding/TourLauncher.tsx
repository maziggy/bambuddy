import { useTranslation } from 'react-i18next';
import { useOnboarding } from '../../contexts/OnboardingContext';
import { TOUR_STEPS, statusForStep } from './tourSteps';
import { MascotIcon } from './MascotIcon';

/**
 * BB icon rendered in the sidebar footer that relaunches the tour from step
 * 0. Consumes the `[data-tour="help-icon"]` selector reserved in the anchor
 * PR. Acts as the rehome target for `onboarding.outro.rehome` — the user
 * can always find BB here even after dismissing the tour.
 */
export function TourLauncher() {
  const { t } = useTranslation();
  const { setStatus } = useOnboarding();

  return (
    <button
      data-tour="help-icon"
      onClick={() => setStatus(statusForStep(0))}
      className="p-1 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
      title={t('onboarding.outro.rehome')}
      aria-label={t('onboarding.outro.rehome')}
      disabled={TOUR_STEPS.length === 0}
    >
      <MascotIcon pose="started" className="w-7 h-7" />
    </button>
  );
}
