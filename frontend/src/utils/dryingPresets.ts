// Resolving an AMS tray's material to a row in the drying preset table.
//
// The table itself lives with the UI that renders it; only the lookup is here,
// so it can be exercised without dragging a page module into the test.

export type DryingPreset = { n3f: number; n3s: number; n3f_hours: number; n3s_hours: number };

// Materials whose AMS spelling differs from the preset table's key. Bambu
// labels nylon "PA" while its own composites spell the family out, so PA6 and
// PAHT would otherwise miss a table that has a perfectly good PA row.
const DRYING_MATERIAL_ALIASES: Record<string, string> = {
  'NYLON': 'PA',
  'PA6': 'PA',
  'PAHT': 'PA',
};

/**
 * Pick the preset key for a tray's material.
 *
 * The answer is always a key the table actually has, which is the whole point:
 * the drying popover seeds both the temperature and the filament name the start
 * command carries from this, and the dropdown silently falls back to its first
 * option when handed a value that isn't in its list. Seeding it with a raw
 * `tray_type` therefore displayed "PLA" while sending the raw string -- an
 * AMS-HT holding Support for PLA/PETG (`tray_type` "PLA-S") showed PLA in the
 * dropdown and told the printer PLA-S (#2774).
 *
 * `tray_type` carries plenty of spellings the table doesn't list: support
 * materials (PLA-S) and composites (PETG-CF, PLA-CF, ABS-GF, PAHT-CF) all dry
 * as their base material, so the suffix is dropped before giving up. Anything
 * still unrecognised lands on PLA, deliberately the coolest row -- under-drying
 * an exotic filament wastes a cycle, where defaulting to PA's 85 degrees would
 * deform a PLA spool.
 */
export function resolveDryingPresetKey(
  trayType: string | null | undefined,
  presets: Record<string, DryingPreset>,
): string {
  const raw = (trayType || '').split(' ')[0].toUpperCase();
  for (const candidate of [raw, raw.split('-')[0]]) {
    const key = DRYING_MATERIAL_ALIASES[candidate] ?? candidate;
    if (presets[key]) return key;
  }
  return 'PLA';
}
