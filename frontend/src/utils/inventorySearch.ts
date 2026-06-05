import type { InventorySpool } from '../api/client';

/**
 * Distinct, trimmed, sorted, non-empty `storage_location` values across spools.
 * Used for storage-location autocomplete / filter options. Trims so accidental
 * trailing whitespace doesn't surface as a separate option (#1400).
 */
export function distinctStorageLocations(spools: InventorySpool[] | undefined): string[] {
  return Array.from(
    new Set((spools ?? []).map((s) => s.storage_location?.trim()).filter((x): x is string => !!x)),
  ).sort();
}

/**
 * Return true when spool matches the search query across all searchable text fields.
 * Case-insensitive. Empty query always returns true.
 */
export function spoolMatchesQuery(spool: InventorySpool, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    String(spool.id).includes(q) ||
    spool.material.toLowerCase().includes(q) ||
    (spool.brand?.toLowerCase().includes(q) ?? false) ||
    (spool.color_name?.toLowerCase().includes(q) ?? false) ||
    (spool.subtype?.toLowerCase().includes(q) ?? false) ||
    (spool.note?.toLowerCase().includes(q) ?? false) ||
    (spool.slicer_filament_name?.toLowerCase().includes(q) ?? false) ||
    (spool.storage_location?.toLowerCase().includes(q) ?? false)
  );
}

/** Filter a spool list by a free-text search query. */
export function filterSpoolsByQuery(spools: InventorySpool[], query: string): InventorySpool[] {
  if (!query) return spools;
  return spools.filter((spool) => spoolMatchesQuery(spool, query));
}
