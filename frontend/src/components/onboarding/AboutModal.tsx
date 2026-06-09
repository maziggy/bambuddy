import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { useOnboarding } from '../../contexts/OnboardingContext';
import { statusForStep } from './tourSteps';
import { MascotIcon } from './MascotIcon';

interface AboutModalProps {
  onClose: () => void;
}

/**
 * Phase 0.2 "What Bambuddy is and isn't" modal. Shown after the user clicks
 * "Start tour" in the welcome modal. The Continue button launches the
 * step-by-step tour engine by setting status to `tour_in_progress:<first>`;
 * Skip persists `dismissed` so the welcome flow never re-shows.
 */
export function AboutModal({ onClose }: AboutModalProps) {
  const { t } = useTranslation();
  const { setStatus } = useOnboarding();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleDone = async () => {
    // Launch the step-by-step engine — the engine takes over rendering once
    // OnboardingFlow sees the tour_in_progress status.
    await setStatus(statusForStep(0));
    onClose();
  };

  const handleSkip = async () => {
    await setStatus('dismissed');
    onClose();
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-[110]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="onboarding-about-title"
    >
      <Card className="w-full max-w-lg">
        <CardContent className="p-6">
          <div className="text-center mb-4">
            <MascotIcon pose="walk" className="w-20 h-20 mx-auto mb-3" />
            <h2 id="onboarding-about-title" className="text-xl font-semibold text-white">
              {t('onboarding.about.title')}
            </h2>
          </div>

          <div className="space-y-4 text-sm">
            <div>
              <h3 className="font-semibold text-bambu-green mb-1">{t('onboarding.about.doesTitle')}</h3>
              <p className="text-bambu-gray">{t('onboarding.about.doesBody')}</p>
            </div>
            <div>
              <h3 className="font-semibold text-bambu-gray-light mb-1">{t('onboarding.about.isntTitle')}</h3>
              <p className="text-bambu-gray">{t('onboarding.about.isntBody')}</p>
            </div>
            <p className="text-xs text-bambu-gray italic">{t('onboarding.about.privacy')}</p>
          </div>

          <div className="flex gap-2 mt-6">
            <Button variant="secondary" onClick={handleSkip} className="flex-1">
              {t('onboarding.button.skipTour')}
            </Button>
            <Button onClick={handleDone} className="flex-1">
              {t('onboarding.button.done')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
