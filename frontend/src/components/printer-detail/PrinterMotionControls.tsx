import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, Home } from 'lucide-react';
import { api } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { StepSelector, type JogStep } from './StepSelector';

interface PrinterMotionControlsProps {
  printerId: number;
  motionDisabled: boolean;
  canControl: boolean;
}

export function PrinterMotionControls({ printerId, motionDisabled, canControl }: PrinterMotionControlsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [step, setStep] = useState<JogStep>(10);
  const [showNotHomed, setShowNotHomed] = useState<null | { axis: 'X' | 'Y' | 'Z'; distance: number }>(null);

  const disabled = motionDisabled || !canControl;

  const onError = (error: Error) =>
    showToast(error.message || t('printers.toast.failedToSendCommand'), 'error');

  const jogMutation = useMutation({
    mutationFn: ({ axis, distance, force }: { axis: 'X' | 'Y' | 'Z'; distance: number; force?: boolean }) =>
      api.jogAxis(printerId, axis, distance, force ?? false),
    onError,
  });

  const homeMutation = useMutation({
    mutationFn: () => api.homeAxes(printerId, 'all'),
    onSuccess: () => {
      try {
        sessionStorage.setItem(`bambuddy.bedJog.warned.${printerId}`, '1');
      } catch {
        /* ignore */
      }
      showToast(t('printers.bedJog.homingStarted'));
    },
    onError,
  });

  const requestJog = (axis: 'X' | 'Y' | 'Z', direction: 1 | -1) => {
    const distance = direction * step;
    const warnedKey = `bambuddy.bedJog.warned.${printerId}`;
    let warned = false;
    try {
      warned = sessionStorage.getItem(warnedKey) === '1';
    } catch {
      /* ignore */
    }
    if (warned) {
      jogMutation.mutate({ axis, distance, force: true });
    } else {
      setShowNotHomed({ axis, distance });
    }
  };

  const btnClass =
    'flex items-center justify-center p-2 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed';

  return (
    <div className="space-y-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
        {t('printerDetail.motion')}
      </h3>
      {motionDisabled && (
        <p className="text-xs text-amber-400/90">{t('printerDetail.motionDisabledWhilePrinting')}</p>
      )}

      <div className="grid grid-cols-3 gap-1 max-w-[180px] mx-auto">
        <div />
        <button type="button" disabled={disabled} className={btnClass} onClick={() => requestJog('Y', 1)} aria-label="+Y">
          <ArrowUp className="w-5 h-5" />
        </button>
        <div />
        <button type="button" disabled={disabled} className={btnClass} onClick={() => requestJog('X', -1)} aria-label="-X">
          <ArrowLeft className="w-5 h-5" />
        </button>
        <button
          type="button"
          disabled={disabled}
          className={`${btnClass} !text-bambu-green hover:!text-bambu-green`}
          onClick={() => homeMutation.mutate()}
          aria-label={t('printers.bedJog.homeZ')}
        >
          <Home className="w-5 h-5" />
        </button>
        <button type="button" disabled={disabled} className={btnClass} onClick={() => requestJog('X', 1)} aria-label="+X">
          <ArrowRight className="w-5 h-5" />
        </button>
        <div />
        <button type="button" disabled={disabled} className={btnClass} onClick={() => requestJog('Y', -1)} aria-label="-Y">
          <ArrowDown className="w-5 h-5" />
        </button>
        <div />
      </div>

      <div className="flex justify-center gap-2">
        <button
          type="button"
          disabled={disabled}
          className={`${btnClass} min-w-[72px] text-xs`}
          onClick={() => requestJog('Z', -step)}
        >
          ↑ {step}
        </button>
        <button
          type="button"
          disabled={disabled}
          className={`${btnClass} min-w-[72px] text-xs`}
          onClick={() => requestJog('Z', step)}
        >
          ↓ {step}
        </button>
      </div>

      <StepSelector
        value={step}
        onChange={setStep}
        label={t('printers.bedJog.step')}
        disabled={disabled}
      />

      {showNotHomed && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl p-4 max-w-sm w-full shadow-xl">
            <h4 className="font-semibold text-white mb-2">{t('printers.bedJog.notHomedTitle')}</h4>
            <p className="text-sm text-bambu-gray mb-4">{t('printers.bedJog.notHomedMessage')}</p>
            <div className="flex flex-col gap-2">
              <button
                type="button"
                className="w-full py-2 rounded-lg bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30"
                onClick={() => {
                  homeMutation.mutate();
                  setShowNotHomed(null);
                }}
              >
                {t('printers.bedJog.homeZ')}
              </button>
              <button
                type="button"
                className="w-full py-2 rounded-lg bg-bambu-dark-tertiary text-white hover:bg-bambu-gray/30"
                onClick={() => {
                  const { axis, distance } = showNotHomed;
                  jogMutation.mutate({ axis, distance, force: true });
                  try {
                    sessionStorage.setItem(`bambuddy.bedJog.warned.${printerId}`, '1');
                  } catch {
                    /* ignore */
                  }
                  setShowNotHomed(null);
                }}
              >
                {t('printers.bedJog.moveAnyway')}
              </button>
              <button
                type="button"
                className="w-full py-2 text-sm text-bambu-gray"
                onClick={() => setShowNotHomed(null)}
              >
                {t('common.cancel')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
