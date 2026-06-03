import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Minus, Plus } from 'lucide-react';
import { api } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';
import type { PrinterControlCapabilities } from '../../utils/printerCapabilities';
import type { PrinterStatus } from '../../api/client';
import { HeaterThermometer } from './HeaterThermometer';

interface PrinterTemperatureControlsProps {
  printerId: number;
  status: PrinterStatus;
  capabilities: PrinterControlCapabilities;
  canControl: boolean;
}

function TempRow({
  label,
  current,
  target,
  color,
  isHeating,
  disabled,
  presets,
  maxTemp,
  step,
  onSet,
}: {
  label: string;
  current: number;
  target?: number;
  color: string;
  isHeating: boolean;
  disabled: boolean;
  presets: number[];
  maxTemp: number;
  step: number;
  onSet: (value: number) => void;
}) {
  const { t } = useTranslation();
  const activeTarget = target && target > 0 ? Math.round(target) : 0;
  const [draft, setDraft] = useState(String(activeTarget || ''));

  useEffect(() => {
    setDraft(activeTarget > 0 ? String(activeTarget) : '');
  }, [activeTarget]);

  const parsed = parseInt(draft, 10);
  const draftValid = draft !== '' && !Number.isNaN(parsed) && parsed >= 0 && parsed <= maxTemp;
  const draftChanged = draftValid && parsed !== activeTarget;

  const apply = (value: number) => {
    const clamped = Math.max(0, Math.min(maxTemp, value));
    onSet(clamped);
    setDraft(clamped > 0 ? String(clamped) : '');
  };

  const nudge = (delta: number) => {
    const base = draftValid ? parsed : activeTarget || current;
    apply(base + delta);
  };

  return (
    <div className="p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary space-y-2.5">
      <div className="flex items-center gap-2">
        <HeaterThermometer className="w-4 h-5 flex-shrink-0" color={color} isHeating={isHeating} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-bambu-gray">{label}</p>
          <p className="text-base tabular-nums text-white leading-tight">
            {Math.round(current)}°C
            {activeTarget > 0 && (
              <span className="text-sm text-bambu-gray font-normal">
                {' '}
                → {activeTarget}°C
              </span>
            )}
          </p>
        </div>
      </div>

      <div className="flex items-stretch gap-1.5">
        <button
          type="button"
          disabled={disabled}
          onClick={() => nudge(-step)}
          className="px-2 rounded-md bg-bambu-dark-secondary border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray/40 disabled:opacity-40 transition-colors"
          aria-label={t('printerDetail.tempDecrease')}
        >
          <Minus className="w-4 h-4" />
        </button>
        <div className="flex-1 flex items-center rounded-md bg-bambu-dark-secondary border border-bambu-dark-tertiary focus-within:border-bambu-green/50 transition-colors overflow-hidden">
          <input
            type="number"
            inputMode="numeric"
            min={0}
            max={maxTemp}
            placeholder="—"
            disabled={disabled}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && draftValid) apply(parsed);
            }}
            className="flex-1 min-w-0 px-2.5 py-2 text-sm tabular-nums text-white bg-transparent border-none focus:outline-none disabled:opacity-50 placeholder:text-bambu-gray/50"
          />
          <span className="pr-2.5 text-sm text-bambu-gray select-none">°C</span>
        </div>
        <button
          type="button"
          disabled={disabled}
          onClick={() => nudge(step)}
          className="px-2 rounded-md bg-bambu-dark-secondary border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray/40 disabled:opacity-40 transition-colors"
          aria-label={t('printerDetail.tempIncrease')}
        >
          <Plus className="w-4 h-4" />
        </button>
        <button
          type="button"
          disabled={disabled || (!draftChanged && draft === '' && activeTarget === 0)}
          onClick={() => draftValid && apply(parsed)}
          className={`px-3 rounded-md text-xs font-medium transition-colors ${
            draftChanged
              ? 'bg-bambu-green text-white hover:bg-bambu-green/90'
              : 'bg-bambu-dark-secondary border border-bambu-dark-tertiary text-bambu-gray hover:text-white disabled:opacity-40'
          }`}
        >
          {t('common.apply')}
        </button>
      </div>

      <div className="flex flex-wrap gap-1">
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            disabled={disabled}
            onClick={() => apply(preset)}
            className={`min-w-[2.5rem] px-2 py-1 rounded text-xs tabular-nums transition-colors ${
              activeTarget === preset
                ? 'bg-bambu-green/20 text-bambu-green ring-1 ring-bambu-green/40'
                : 'bg-bambu-dark-secondary text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white'
            }`}
          >
            {preset === 0 ? t('common.off') : `${preset}°`}
          </button>
        ))}
      </div>
    </div>
  );
}

export function PrinterTemperatureControls({
  printerId,
  status,
  capabilities,
  canControl,
}: PrinterTemperatureControlsProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const temps = status.temperatures;
  const disabled = !canControl || !status.connected;

  const onError = (error: Error) =>
    showToast(error.message || t('printers.toast.failedToSendCommand'), 'error');

  const bedMutation = useMutation({
    mutationFn: (target: number) => api.setBedTemperature(printerId, target),
    onError,
  });
  const nozzleMutation = useMutation({
    mutationFn: ({ target, nozzle }: { target: number; nozzle: number }) =>
      api.setNozzleTemperature(printerId, target, nozzle),
    onError,
  });
  const chamberMutation = useMutation({
    mutationFn: (target: number) => api.setChamberTemperature(printerId, target),
    onError,
  });

  const nozzlePresets = [0, 170, 200, 220];
  const bedPresets = [0, 60, 80, 100];
  const chamberPresets = [0, 45, 55, 65];

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-bambu-gray">
        {t('printerDetail.temperatures')}
      </h3>
      <TempRow
        label={t('printers.temperatures.nozzle')}
        current={temps?.nozzle ?? 0}
        target={temps?.nozzle_target}
        color="text-orange-400"
        isHeating={!!temps?.nozzle_heating}
        disabled={disabled || nozzleMutation.isPending}
        presets={nozzlePresets}
        maxTemp={320}
        step={5}
        onSet={(v) => nozzleMutation.mutate({ target: v, nozzle: 0 })}
      />
      {capabilities.showDualNozzle && (
        <TempRow
          label={t('printerDetail.nozzleLeft')}
          current={temps?.nozzle_2 ?? 0}
          target={temps?.nozzle_2_target}
          color="text-orange-400"
          isHeating={!!temps?.nozzle_2_heating}
          disabled={disabled || nozzleMutation.isPending}
          presets={nozzlePresets}
          maxTemp={320}
          step={5}
          onSet={(v) => nozzleMutation.mutate({ target: v, nozzle: 1 })}
        />
      )}
      <TempRow
        label={t('printers.temperatures.bed')}
        current={temps?.bed ?? 0}
        target={temps?.bed_target}
        color="text-blue-400"
        isHeating={!!temps?.bed_heating}
        disabled={disabled || bedMutation.isPending}
        presets={bedPresets}
        maxTemp={120}
        step={5}
        onSet={(v) => bedMutation.mutate(v)}
      />
      {capabilities.showChamberTemp && (
        <TempRow
          label={t('printers.temperatures.chamber')}
          current={temps?.chamber ?? 0}
          target={temps?.chamber_target}
          color="text-green-400"
          isHeating={!!temps?.chamber_heating}
          disabled={disabled || chamberMutation.isPending}
          presets={chamberPresets}
          maxTemp={80}
          step={5}
          onSet={(v) => chamberMutation.mutate(v)}
        />
      )}
      {status.nozzles?.[0]?.nozzle_diameter && (
        <p className="text-[10px] text-bambu-gray px-1">
          {t('printerDetail.nozzleInfo', {
            diameter: status.nozzles[0].nozzle_diameter,
            type: status.nozzles[0].nozzle_type || '—',
          })}
        </p>
      )}
    </div>
  );
}
