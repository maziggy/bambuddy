import type { InventorySpool } from '../api/client';
import { resolveSpoolColorName } from './colors';

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
    // Both the stored name and the displayed one. They differ often: a Bambu
    // tag may carry no colour name or an internal code, and Spoolman has no
    // such field, so what the list shows is usually resolved from the swatch's
    // hex. Searching only the stored value means typing what you can plainly
    // read finds nothing (#3090).
    (spool.color_name?.toLowerCase().includes(q) ?? false) ||
    (resolveSpoolColorName(spool.color_name, spool.rgba, spool.color_name_is_synthesized)
      ?.toLowerCase()
      .includes(q) ??
      false) ||
    (spool.subtype?.toLowerCase().includes(q) ?? false) ||
    (spool.note?.toLowerCase().includes(q) ?? false) ||
    (spool.slicer_filament_name?.toLowerCase().includes(q) ?? false) ||
    (spool.storage_location?.toLowerCase().includes(q) ?? false) ||
    // Typed code columns: searching "17600" must find every roll of that
    // SKU (and likewise GTIN / ASIN / the user's own codes).
    (spool.gtin_code?.toLowerCase().includes(q) ?? false) ||
    (spool.sku_code?.toLowerCase().includes(q) ?? false) ||
    (spool.asin_code?.toLowerCase().includes(q) ?? false) ||
    (spool.other_code?.toLowerCase().includes(q) ?? false)
  );
}

/** Filter a spool list by a free-text search query. */
export function filterSpoolsByQuery(spools: InventorySpool[], query: string): InventorySpool[] {
  if (!query) return spools;
  return spools.filter((spool) => spoolMatchesQuery(spool, query));
}
