import type { InventorySpool } from '../api/client';

/**
 * Return true when spool matches the search query across material, brand, color_name, and subtype.
 * Case-insensitive. Empty query always returns true.
 */
export function spoolMatchesQuery(spool: InventorySpool, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return (
    spool.material.toLowerCase().includes(q) ||
    (spool.brand?.toLowerCase().includes(q) ?? false) ||
    (spool.color_name?.toLowerCase().includes(q) ?? false) ||
    (spool.subtype?.toLowerCase().includes(q) ?? false)
  );
}

/** Filter a spool list by a free-text search query. */
export function filterSpoolsByQuery(spools: InventorySpool[], query: string): InventorySpool[] {
  if (!query) return spools;
  return spools.filter((spool) => spoolMatchesQuery(spool, query));
}
