import { useState } from 'react';
import { Settings, ChevronDown, ChevronUp } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { PrintOptionsProps, PrintOptions as PrintOptionsType } from './types';

type OptionConfig = {
  key: keyof PrintOptionsType;
  label: string;
  desc: string;
  dualNozzleOnly?: boolean;
};

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
    { key: 'bed_levelling', label: t('settings.defaultBedLevelling'), desc: t('settings.defaultBedLevellingDesc') },
    { key: 'flow_cali', label: t('settings.defaultFlowCali'), desc: t('settings.defaultFlowCaliDesc') },
    { key: 'vibration_cali', label: t('settings.defaultVibrationCali'), desc: t('settings.defaultVibrationCaliDesc') },
    { key: 'layer_inspect', label: t('settings.defaultLayerInspect'), desc: t('settings.defaultLayerInspectDesc') },
    { key: 'timelapse', label: t('settings.defaultTimelapse'), desc: t('settings.defaultTimelapseDesc') },
    { key: 'nozzle_offset_cali', label: t('settings.defaultNozzleOffsetCali'), desc: t('settings.defaultNozzleOffsetCaliDesc'), dualNozzleOnly: true },
  ];

  const visibleOptions = printOptionsConfig.filter(o => !o.dualNozzleOnly || showDualNozzleOptions);

  const handleToggle = (key: keyof PrintOptionsType) => {
    onChange({ ...options, [key]: !options[key] });
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
          {visibleOptions.map(({ key, label, desc }) => (
            <label key={key} className="flex items-center justify-between cursor-pointer group">
              <div>
                <span className="text-sm text-white">{label}</span>
                <p className="text-xs text-bambu-gray">{desc}</p>
              </div>
              <div
                className={`relative w-10 h-5 rounded-full transition-colors ${
                  options[key] ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                }`}
                onClick={() => handleToggle(key)}
              >
                <div
                  className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                    options[key] ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </div>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
