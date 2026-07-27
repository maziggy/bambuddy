import { useTranslation } from 'react-i18next';
import {
  DEFAULT_PREHEAT_FILAMENT_TARGETS,
  PREHEAT_FILAMENT_ORDER,
  parsePreheatFilamentTargets,
  serializePreheatFilamentTargets,
} from '../utils/preheatFilamentTargets';

interface Props {
  // JSON-encoded map; empty string means "use bundled defaults".
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}

// Per-filament chamber target editor for Settings → Workflow → Preheat card
// (#1468). Renders one row per filament type with a numeric input clamped to
// 0-60 °C. Stripping back to the bundled defaults is handled by the parent
// (Reset button next to the section title) — passing an empty string upward
// is the canonical "use defaults" signal, which keeps the editor stateless
// across resets.
export function PreheatFilamentTargetsEditor({ value, onChange, disabled = false }: Props) {
  const { t } = useTranslation();
  const map = parsePreheatFilamentTargets(value);

  const updateOne = (key: string, next: number) => {
    const clamped = Math.max(0, Math.min(60, Math.round(next)));
    const updated = { ...map, [key]: clamped };
    onChange(serializePreheatFilamentTargets(updated));
  };

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1.5">
      {PREHEAT_FILAMENT_ORDER.map((key) => {
        const current = map[key] ?? DEFAULT_PREHEAT_FILAMENT_TARGETS[key] ?? 0;
        const label = key === 'default'
          ? t('settings.preheatFilamentTargetsDefaultRow', 'Other / unmapped')
          : key;
        return (
          <div key={key} className="flex items-center justify-between gap-2">
            <span className={`text-xs ${key === 'default' ? 'text-bambu-gray italic' : 'text-bambu-gray'}`}>
              {label}
            </span>
            <div className="flex items-center gap-1">
              <input
                type="number"
                min={0}
                max={60}
                step={1}
                value={current}
                onChange={(e) => updateOne(key, parseInt(e.target.value, 10) || 0)}
                disabled={disabled}
                className="w-16 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-xs text-right focus:outline-none focus:border-bambu-green disabled:opacity-50"
              />
              <span className="text-xs text-bambu-gray">°C</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
