import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Settings, ChevronDown, ChevronUp } from 'lucide-react';
import type { PrintOptionsProps, PrintOptions as PrintOptionsType } from './types';

const PRINT_OPTION_KEYS = [
  { key: 'bed_levelling', i18n: 'bedLevelling' },
  { key: 'flow_cali', i18n: 'flowCali' },
  { key: 'vibration_cali', i18n: 'vibrationCali' },
  { key: 'layer_inspect', i18n: 'layerInspect' },
  { key: 'timelapse', i18n: 'timelapse' },
] as const;

/**
 * Print options toggle panel with collapsible UI.
 * Shows bed levelling, flow/vibration calibration, layer inspection, and timelapse options.
 */
export function PrintOptionsPanel({
  options,
  onChange,
  defaultExpanded = false,
}: PrintOptionsProps) {
  const { t } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(defaultExpanded);

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
        <span>{t('printOptions.title')}</span>
        {isExpanded ? (
          <ChevronUp className="w-4 h-4 ml-auto" />
        ) : (
          <ChevronDown className="w-4 h-4 ml-auto" />
        )}
      </button>
      {isExpanded && (
        <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
          {PRINT_OPTION_KEYS.map(({ key, i18n }) => (
            <label key={key} className="flex items-center justify-between cursor-pointer group">
              <div>
                <span className="text-sm text-white">{t(`printOptions.${i18n}`)}</span>
                <p className="text-xs text-bambu-gray">{t(`printOptions.${i18n}Desc`)}</p>
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
