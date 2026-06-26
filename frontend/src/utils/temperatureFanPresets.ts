/**
 * Helpers for the user-configurable temperature / fan-speed presets shown in
 * the printer-card popovers and edited under Settings -> Workflow.
 *
 * Storage shape: JSON string of exactly 3 integers per category. Empty string
 * means "use built-in defaults". Validators on the backend (AppSettingsUpdate)
 * enforce the same shape on writes; helpers here are defensive parsers.
 *
 * The "Off" (value 0) button is rendered separately by buildPresetOptions and
 * is not part of the configurable triple.
 */

export type PresetTriple = readonly [number, number, number];

export const NOZZLE_TEMP_DEFAULTS: PresetTriple = [120, 220, 260];
export const BED_TEMP_DEFAULTS: PresetTriple = [55, 75, 90];
export const CHAMBER_TEMP_DEFAULTS: PresetTriple = [35, 45, 60];
export const FAN_SPEED_DEFAULTS: PresetTriple = [50, 75, 100];

export interface PresetCategory {
  key: 'nozzle_temp_presets' | 'bed_temp_presets' | 'chamber_temp_presets' | 'fan_speed_presets';
  defaults: PresetTriple;
  lo: number;
  hi: number;
  unit: 'C' | '%';
}

export const PRESET_CATEGORIES: readonly PresetCategory[] = [
  { key: 'nozzle_temp_presets', defaults: NOZZLE_TEMP_DEFAULTS, lo: 0, hi: 320, unit: 'C' },
  { key: 'bed_temp_presets', defaults: BED_TEMP_DEFAULTS, lo: 0, hi: 140, unit: 'C' },
  { key: 'chamber_temp_presets', defaults: CHAMBER_TEMP_DEFAULTS, lo: 0, hi: 60, unit: 'C' },
  { key: 'fan_speed_presets', defaults: FAN_SPEED_DEFAULTS, lo: 0, hi: 100, unit: '%' },
];

/**
 * Parse a JSON triple from settings, falling back to defaults if empty / malformed
 * / wrong shape. Forward-compat for any storage drift; backend validators are
 * the source of truth.
 */
export function parsePresetTriple(
  raw: string | undefined | null,
  fallback: PresetTriple,
  lo: number,
  hi: number,
): [number, number, number] {
  if (!raw) return [...fallback];
  try {
    const parsed = JSON.parse(raw);
    if (
      Array.isArray(parsed) &&
      parsed.length === 3 &&
      parsed.every((n) => Number.isInteger(n) && n >= lo && n <= hi)
    ) {
      return parsed as [number, number, number];
    }
  } catch {
    // fall through to defaults
  }
  return [...fallback];
}

/** Build the popover options: fixed "Off" button + 3 user-configured presets. */
export function buildPresetOptions(
  values: PresetTriple,
  unit: 'C' | '%',
): Array<{ label: string; value: number }> {
  return [
    { label: 'Off', value: 0 },
    ...values.map((v) => ({ label: `${v} ${unit}`, value: v })),
  ];
}
