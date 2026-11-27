import type { DesignOverride } from '../types/plates';
import type { SettingValue } from '../types/slicerSettings';

/**
 * Process options whose integer value names a filament slot rather than a
 * quantity: 0 = "default / the object's own filament", 1..N = slot. The
 * support pair is on the Support page; the rest are the Multimaterial page's
 * per-region pickers.
 */
const SUPPORT_SLOT_KEYS = ['support_filament', 'support_interface_filament'];
const REGION_SLOT_KEYS = [
  'outer_wall_filament_id',
  'inner_wall_filament_id',
  'top_surface_filament_id',
  'bottom_surface_filament_id',
  'internal_solid_filament_id',
  'sparse_infill_filament_id',
];

export const FILAMENT_SLOT_OPTIONS: ReadonlySet<string> = new Set([...SUPPORT_SLOT_KEYS, ...REGION_SLOT_KEYS]);

/**
 * What a slice will run with, in the precedence the settings panel shows: the
 * picked preset underneath, the designer's value for each key that is ticked,
 * and anything typed in the dialog on top.
 */
export function effectiveProcessValues(
  presetValues: Record<string, SettingValue> | undefined,
  sourceOverrides: DesignOverride[],
  sourceSelected: ReadonlySet<string> | undefined,
  values: Record<string, SettingValue>,
): Record<string, SettingValue> {
  const merged: Record<string, SettingValue> = { ...(presetValues ?? {}) };
  for (const o of sourceOverrides) {
    if (sourceSelected?.has(o.key)) merged[o.key] = o.value as SettingValue;
  }
  // An emptied field is not a value — leaving it in would read as "" and
  // send the config reader to the schema default, past the preset.
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== '') merged[key] = value;
  }
  return merged;
}

const single = (value: SettingValue | undefined) =>
  Array.isArray(value) && value.length === 1 ? value[0] : value;

const SUPPORTS_OFF = new Set<unknown>([false, 0, '0', 'false', 'False', '', undefined]);

/**
 * The filament slots these process settings print with beyond the object
 * geometry (#3736): the support slots while supports are on, and every slot a
 * region picker names. The backend reads the same keys the same way.
 */
export function processFilamentSlots(values: Record<string, SettingValue>): Set<number> {
  const supportsOn = !SUPPORTS_OFF.has(single(values.enable_support));
  const keys = supportsOn ? [...REGION_SLOT_KEYS, ...SUPPORT_SLOT_KEYS] : REGION_SLOT_KEYS;
  const slots = new Set<number>();
  for (const key of keys) {
    const slot = Number(single(values[key]));
    if (Number.isInteger(slot) && slot > 0) slots.add(slot);
  }
  return slots;
}
