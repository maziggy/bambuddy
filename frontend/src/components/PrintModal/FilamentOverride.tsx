import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Circle, RotateCcw } from 'lucide-react';
import { getColorName } from '../../utils/colors';
import type { FilamentReqsData } from './types';

interface FilamentOverrideProps {
  filamentReqs: FilamentReqsData | undefined;
  availableFilaments: Array<{ type: string; color: string; tray_info_idx: string; extruder_id: number | null }>;
  overrides: Record<number, { type: string; color: string }>;
  onChange: (overrides: Record<number, { type: string; color: string }>) => void;
}

/**
 * Filament override UI for model-based queue assignment.
 * Allows users to override the 3MF's original filament choices with
 * filaments available across printers of the selected model.
 */
export function FilamentOverride({
  filamentReqs,
  availableFilaments,
  overrides,
  onChange,
}: FilamentOverrideProps) {
  const { t } = useTranslation();

  // Index available filaments by type (uppercased) for per-slot filtering
  const filamentsByType = useMemo(() => {
    const map: Record<string, Array<{ type: string; color: string; tray_info_idx: string; extruder_id: number | null }>> = {};
    for (const f of availableFilaments) {
      const key = f.type.toUpperCase();
      if (!map[key]) map[key] = [];
      map[key].push(f);
    }
    return map;
  }, [availableFilaments]);

  const filaments = filamentReqs?.filaments;
  if (!filaments || filaments.length === 0 || availableFilaments.length === 0) {
    return null;
  }

  const handleChange = (slotId: number, value: string) => {
    if (value === '') {
      // Reset to original
      const next = { ...overrides };
      delete next[slotId];
      onChange(next);
    } else {
      // Parse "TYPE|COLOR" value
      const [type, color] = value.split('|');
      onChange({ ...overrides, [slotId]: { type, color } });
    }
  };

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 text-sm text-bambu-gray mb-2">
        <span>{t('printModal.filamentOverride')}</span>
      </div>
      <p className="text-xs text-bambu-gray mb-2">{t('printModal.filamentOverrideHint')}</p>
      <div className="bg-bambu-dark rounded-lg p-3 space-y-2">
        {filaments.map((req) => {
          const override = overrides[req.slot_id];
          const isOverridden = !!override;
          // Only show filaments of the same type AND compatible nozzle/extruder
          const sameType = filamentsByType[req.type.toUpperCase()] || [];
          // On dual-nozzle printers (H2D), filter to filaments on the correct extruder.
          // nozzle_id from 3MF maps to extruder_id from AMS. If nozzle_id is undefined
          // (single-nozzle) or extruder_id is null, no nozzle filtering is needed.
          const compatible = req.nozzle_id != null
            ? sameType.filter((f) => f.extruder_id == null || f.extruder_id === req.nozzle_id)
            : sameType;

          return (
            <div
              key={req.slot_id}
              className="grid items-center gap-2 text-xs"
              style={{ gridTemplateColumns: '16px minmax(70px, 1fr) auto 2fr 20px' }}
            >
              {/* Original color swatch */}
              <span title={`${t('printModal.originalFilament')}: ${req.type} - ${getColorName(req.color)}`}>
                <Circle className="w-3 h-3" fill={req.color} stroke={req.color} />
              </span>
              {/* Original type + grams */}
              <span className="text-white truncate">
                {req.type} <span className="text-bambu-gray">({req.used_grams}g)</span>
              </span>
              {/* Arrow */}
              <span className="text-bambu-gray">→</span>
              {/* Override dropdown — only compatible (same-type) filaments */}
              <select
                value={isOverridden ? `${override.type}|${override.color}` : ''}
                onChange={(e) => handleChange(req.slot_id, e.target.value)}
                disabled={compatible.length === 0}
                className={`flex-1 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary focus:outline-none focus:ring-1 focus:ring-bambu-green ${
                  isOverridden
                    ? 'border-blue-400/50 text-blue-400'
                    : 'border-bambu-gray/30 text-bambu-gray'
                }`}
              >
                <option value="" className="bg-bambu-dark text-bambu-gray">
                  {t('printModal.originalFilament')}: {req.type} ({getColorName(req.color)})
                </option>
                {compatible.map((f, idx) => (
                  <option
                    key={`${f.type}-${f.color}-${idx}`}
                    value={`${f.type}|${f.color}`}
                    className="bg-bambu-dark text-white"
                  >
                    {f.type} ({getColorName(f.color)})
                  </option>
                ))}
              </select>
              {/* Reset button */}
              {isOverridden ? (
                <button
                  type="button"
                  onClick={() => handleChange(req.slot_id, '')}
                  className="text-bambu-gray hover:text-white transition-colors"
                  title={t('printModal.resetToOriginal')}
                >
                  <RotateCcw className="w-3 h-3" />
                </button>
              ) : (
                <span className="w-3" />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
