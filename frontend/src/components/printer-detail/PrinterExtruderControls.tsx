import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { api, type PrinterStatus } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import { ExtrudeStepSelector, type ExtrudeStep } from './ExtrudeStepSelector';

interface PrinterExtruderControlsProps {
  printerId: number;
  status: PrinterStatus;
  motionDisabled: boolean;
  canControl: boolean;
}

export function PrinterExtruderControls({
  printerId,
  status,
  motionDisabled,
  canControl,
}: PrinterExtruderControlsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [step, setStep] = useState<ExtrudeStep>(50);
  const disabled = motionDisabled || !canControl || !status.connected;
  const nozzleTemp = status.temperatures?.nozzle ?? 0;
  const coldWarning = nozzleTemp < 170;

  const onError = (error: Error) =>
    showToast(error.message || t('printers.toast.failedToSendCommand'), 'error');

  const loadMutation = useMutation({
    mutationFn: () => api.loadAmsTray(printerId, 254),
    onSuccess: () => showToast(t('printerDetail.loadStarted')),
    onError,
  });
  const unloadMutation = useMutation({
    mutationFn: () => api.unloadAms(printerId),
    onSuccess: () => showToast(t('printerDetail.unloadStarted')),
    onError,
  });
  const extrudeMutation = useMutation({
    mutationFn: (distance: number) => api.extrudeFilament(printerId, distance),
    onError,
  });

  const btnClass =
    'flex items-center justify-center gap-1 px-3 py-2 rounded-lg text-xs font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed';

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
        {t('printerDetail.extruder')}
      </h3>

      <div className="flex gap-2">
        <button
          type="button"
          disabled={disabled || unloadMutation.isPending}
          className={`${btnClass} flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary text-white`}
          onClick={() => unloadMutation.mutate()}
        >
          {t('printers.ams.unload')}
        </button>
        <button
          type="button"
          disabled={disabled || loadMutation.isPending}
          className={`${btnClass} flex-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary text-white`}
          onClick={() => loadMutation.mutate()}
        >
          {t('printers.ams.load')}
        </button>
      </div>

      <div className="flex flex-col items-center gap-1 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary">
        <button
          type="button"
          disabled={disabled || extrudeMutation.isPending || coldWarning}
          className={btnClass}
          title={coldWarning ? t('printerDetail.extrudeColdWarning') : undefined}
          onClick={() => extrudeMutation.mutate(step)}
        >
          <ArrowUp className="w-4 h-4" />
        </button>
        <div className="w-8 h-16 rounded bg-bambu-dark-secondary border border-bambu-green/30" />
        <button
          type="button"
          disabled={disabled || extrudeMutation.isPending || coldWarning}
          className={btnClass}
          title={coldWarning ? t('printerDetail.extrudeColdWarning') : undefined}
          onClick={() => extrudeMutation.mutate(-step)}
        >
          <ArrowDown className="w-4 h-4" />
        </button>
      </div>

      <ExtrudeStepSelector
        value={step}
        onChange={setStep}
        label={t('printerDetail.extrudeStep')}
        disabled={disabled}
      />
      {coldWarning && !motionDisabled && (
        <p className="text-[10px] text-amber-400">{t('printerDetail.extrudeColdWarning')}</p>
      )}
    </div>
  );
}
