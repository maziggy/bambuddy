import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, CardContent } from '../Card';
import { Button } from '../Button';
import { useOnboarding } from '../../contexts/OnboardingContext';
import { MascotIcon } from './MascotIcon';

const SNOOZE_DAYS = 7;

interface WelcomeModalProps {
  onStartTour: () => void;
  onClose: () => void;
}

/**
 * Phase 0.1 welcome modal. Pops once for new users; the three buttons map to
 * the three branches in docs/onboarding-tour-plan.md:
 *   - Start tour    → advance to AboutModal (status update happens at the end)
 *   - I'm experienced → persist `dismissed`
 *   - Remind me later → persist `snoozed` + a 7-day timestamp
 *
 */
export function WelcomeModal({ onStartTour, onClose }: WelcomeModalProps) {
  const { t } = useTranslation();
  const { setStatus } = useOnboarding();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleExperienced = async () => {
    await setStatus('dismissed');
    onClose();
  };

  const handleSnooze = async () => {
    const snoozeUntil = new Date(Date.now() + SNOOZE_DAYS * 24 * 60 * 60 * 1000).toISOString();
    await setStatus('snoozed', snoozeUntil);
    onClose();
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-[110]"
      role="dialog"
      aria-modal="true"
      aria-labelledby="onboarding-welcome-title"
    >
      <Card className="w-full max-w-md">
        <CardContent className="p-6 text-center">
          <MascotIcon pose="started" className="w-24 h-24 mx-auto mb-3" />
          <h2 id="onboarding-welcome-title" className="text-xl font-semibold text-white">
            {t('onboarding.welcome.title')}
          </h2>
          <p className="text-sm text-bambu-gray mt-2 mb-6">{t('onboarding.welcome.body')}</p>
          <div className="flex flex-col gap-2">
            <Button onClick={onStartTour}>{t('onboarding.welcome.startTour')}</Button>
            <Button variant="secondary" onClick={handleExperienced}>
              {t('onboarding.welcome.experienced')}
            </Button>
            <Button variant="ghost" onClick={handleSnooze}>
              {t('onboarding.button.remindLater')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
