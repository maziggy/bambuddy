import type { InventorySpool, UnifiedPreset } from '../api/client';

/**
 * The two pieces of identity an inventory spool can retain for its slicer
 * filament. IDs are authoritative when they still exist; names let the same
 * filament match a printer-specific copy of a preset whose ID differs.
 */
export interface FilamentInventoryIdentity {
  ids: ReadonlySet<string>;
  names: ReadonlySet<string>;
}

/**
 * Reduce a slicer filament name to the part identifying the filament itself.
 *
 * Bambu and Orca append the target printer after `@BBL` / `@Bambu Lab`, while
 * custom presets may also carry a leading `#`. Removing those decorations
 * makes, for example, the A1 and X1C copies of Bambu PLA Basic equivalent
 * without treating an unrelated `@` in a user-authored name as a separator.
 */
export function normalizeFilamentPresetName(name: string): string {
  return name
    .replace(/^\s*#\s*/, '')
    .replace(/\s+@(?:BBL|Bambu\s+Lab)\b.*$/i, '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();
}

/** Build the usable preset identities represented by active inventory. */
export function buildFilamentInventoryIdentity(
  spools: readonly Pick<InventorySpool, 'slicer_filament' | 'slicer_filament_name'>[],
): FilamentInventoryIdentity | null {
  const ids = new Set<string>();
  const names = new Set<string>();

  for (const spool of spools) {
    const id = spool.slicer_filament?.trim();
    if (id) ids.add(id);

    const name = normalizeFilamentPresetName(spool.slicer_filament_name ?? '');
    if (name) names.add(name);
  }

  // An inventory with no linked slicer profiles cannot provide a meaningful
  // filter. Returning null tells every caller to preserve the complete list.
  return ids.size > 0 || names.size > 0 ? { ids, names } : null;
}

/** True when a unified slicer preset is represented by active inventory. */
export function filamentPresetIsInInventory(
  preset: Pick<UnifiedPreset, 'id' | 'name'>,
  inventory: FilamentInventoryIdentity | null | undefined,
): boolean {
  if (!inventory) return false;
  if (inventory.ids.has(preset.id)) return true;
  const name = normalizeFilamentPresetName(preset.name);
  return Boolean(name) && inventory.names.has(name);
}
