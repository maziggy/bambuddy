import type { InventorySpool } from '../api/client';

/**
 * Return true when spool matches the search query across all searchable text fields.
 * Case-insensitive. Empty query always returns true.
 */
export function spoolMatchesQuery(spool: InventorySpool, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;

  // A hash-prefixed number is an explicit spool ID lookup. Keep plain numeric
  // searches backwards-compatible ("3" can still match #3, #13, #30, ...),
  // while "#3" selects only the physical spool whose label says #3.
  const exactIdQuery = q.match(/^#(\d+)$/);
  if (exactIdQuery) {
    return spool.id === Number(exactIdQuery[1]);
  }

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
