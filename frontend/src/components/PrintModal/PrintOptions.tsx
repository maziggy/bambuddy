import { useState } from 'react';
import { Settings, ChevronDown, ChevronUp, Flame } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type {
  PrintOptionsProps,
  PrintOptions as PrintOptionsType,
  PreheatOverride,
  CalibrationMode,
} from './types';
import {
  CALIBRATION_MODES,
  CALIBRATION_MODE_ACTIVE,
  CALIBRATION_MODE_INACTIVE,
} from '../../utils/calibrationMode';

type OptionConfig = {
  key: keyof PrintOptionsType;
  label: string;
  desc: string;
  dualNozzleOnly?: boolean;
  /** Tri-state (off/on/auto) rather than a plain on/off pair. */
  tristate?: boolean;
};

// On/off options render as the same button pair, minus the "auto" choice.
const BOOLEAN_MODES = ['off', 'on'] as const;

/**
 * Print options toggle panel with collapsible UI.
 * Shows bed levelling, flow/vibration calibration, layer inspection, timelapse,
 * and (for dual-nozzle printers only) nozzle offset calibration.
 */
export function PrintOptionsPanel({
  options,
  onChange,
  defaultExpanded = false,
  showDualNozzleOptions = false,
}: PrintOptionsProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

  // Labels/descriptions reuse the settings.default* namespace — identical strings,
  // already translated across all locales. Only nozzle_offset_cali is new (#1682).
  const printOptionsConfig: OptionConfig[] = [
    { key: 'bed_levelling', label: t('settings.defaultBedLevelling'), desc: t('settings.defaultBedLevellingDesc'), tristate: true },
    { key: 'flow_cali', label: t('settings.defaultFlowCali'), desc: t('settings.defaultFlowCaliDesc'), tristate: true },
    { key: 'vibration_cali', label: t('settings.defaultVibrationCali'), desc: t('settings.defaultVibrationCaliDesc') },
    { key: 'layer_inspect', label: t('settings.defaultLayerInspect'), desc: t('settings.defaultLayerInspectDesc') },
    { key: 'timelapse', label: t('settings.defaultTimelapse'), desc: t('settings.defaultTimelapseDesc') },
    { key: 'nozzle_offset_cali', label: t('settings.defaultNozzleOffsetCali'), desc: t('settings.defaultNozzleOffsetCaliDesc'), dualNozzleOnly: true, tristate: true },
  ];

  const visibleOptions = printOptionsConfig.filter(o => !o.dualNozzleOnly || showDualNozzleOptions);

  const handleToggle = (key: keyof PrintOptionsType, value: boolean) => {
    onChange({ ...options, [key]: value });
  };

  const handleCalibrationMode = (key: keyof PrintOptionsType, mode: CalibrationMode) => {
    onChange({ ...options, [key]: mode });
  };

  const handlePreheatOverride = (next: PreheatOverride) => {
    onChange({
      ...options,
      preheat_override: next,
      // Clearing override→off also clears the chamber-target override so the
      // backend doesn't carry a stale value if the user re-enables later.
      ...(next === 'off' ? { preheat_chamber_target_override: null } : {}),
    });
  };

  const handlePreheatTarget = (raw: string) => {
    if (raw === '') {
      onChange({ ...options, preheat_chamber_target_override: null });
      return;
    }
    const parsed = parseInt(raw, 10);
    if (Number.isNaN(parsed)) return;
    onChange({
      ...options,
      preheat_chamber_target_override: Math.max(0, Math.min(60, parsed)),
    });
  };

  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-2 text-sm text-bambu-gray hover:text-white transition-colors w-full"
      >
        <Settings className="w-4 h-4" />
        <span>{t('queue.bulkEdit.printOptions')}</span>
        {isExpanded ? (
          <ChevronUp className="w-4 h-4 ml-auto" />
        ) : (
          <ChevronDown className="w-4 h-4 ml-auto" />
        )}
      </button>
      {isExpanded && (
        <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
          {visibleOptions.map(({ key, label, desc, tristate }) =>
            tristate ? (
              <div key={key} className="flex items-center justify-between gap-3">
                <div>
                  <span className="text-sm text-white">{label}</span>
                  <p className="text-xs text-bambu-gray">{desc}</p>
                </div>
                <div className="flex gap-1 shrink-0">
                  {CALIBRATION_MODES.map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      onClick={() => handleCalibrationMode(key, mode)}
                      className={`px-2.5 py-1 text-xs rounded transition-colors ${
                        options[key as 'bed_levelling'] === mode
                          ? CALIBRATION_MODE_ACTIVE[mode]
                          : CALIBRATION_MODE_INACTIVE
                      }`}
                    >
                      {t(`settings.calibrationMode_${mode}`)}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div key={key} className="flex items-center justify-between gap-3">
                <div>
                  <span className="text-sm text-white">{label}</span>
                  <p className="text-xs text-bambu-gray">{desc}</p>
                </div>
                <div className="flex gap-1 shrink-0">
                  {BOOLEAN_MODES.map((mode) => {
                    const active = (options[key as 'vibration_cali'] ? 'on' : 'off') === mode;
                    return (
                      <button
                        key={mode}
                        type="button"
                        onClick={() => handleToggle(key, mode === 'on')}
                        className={`px-2.5 py-1 text-xs rounded transition-colors ${
                          active ? CALIBRATION_MODE_ACTIVE[mode] : CALIBRATION_MODE_INACTIVE
                        }`}
                      >
                        {t(`settings.calibrationMode_${mode}`)}
                      </button>
                    );
                  })}
                </div>
              </div>
            ),
          )}

          {/* Preheat / heat-soak per-item override (#1468). Defaults to
              'inherit' which means the global Settings → Workflow toggle
              decides. Forcing 'on' or 'off' overrides per-print; the chamber
              target override (optional °C input, visible when not 'off')
              bypasses the per-filament-type derivation. */}
          <div className="pt-2 mt-1 border-t border-bambu-dark-tertiary/60">
            <div className="flex items-center gap-2 mb-1.5">
              <Flame className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400" />
              <span className="text-sm text-white">{t('settings.preheatTitle', 'Preheat & Heat Soak')}</span>
            </div>
            <p className="text-xs text-bambu-gray mb-2">
              {t('settings.preheatPerItemDesc', 'Heat the bed and chamber before this print starts. Defaults to the global Settings → Workflow toggle.')}
            </p>
            <div className="flex gap-1.5 mb-2">
              {(['inherit', 'on', 'off'] as PreheatOverride[]).map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => handlePreheatOverride(opt)}
                  className={`flex-1 px-2 py-1.5 text-xs rounded transition-colors ${
                    options.preheat_override === opt
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark-tertiary text-bambu-gray hover:text-white'
                  }`}
                >
                  {t(`settings.preheatOverride_${opt}`, opt === 'inherit' ? 'Inherit' : opt === 'on' ? 'On' : 'Off')}
                </button>
              ))}
            </div>
            {options.preheat_override !== 'off' && (
              <div className="flex items-center gap-2">
                <label className="text-xs text-bambu-gray flex-1">
                  {t('settings.preheatTargetOverride', 'Chamber target override (°C, blank = filament default)')}
                </label>
                <input
                  type="number"
                  min={0}
                  max={60}
                  step={1}
                  value={options.preheat_chamber_target_override ?? ''}
                  onChange={(e) => handlePreheatTarget(e.target.value)}
                  placeholder="—"
                  className="w-16 px-2 py-1 bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded text-white text-xs text-right focus:outline-none focus:border-bambu-green"
                />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
