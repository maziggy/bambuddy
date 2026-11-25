import type { InventorySpool } from '../api/client';
import { resolveSpoolColorName } from './colors';

/**
 * The spool ID a "#<number>" query asks for, or null for any other query.
 *
 * A hash-prefixed number is an explicit spool ID lookup. Plain numeric
 * searches stay backwards-compatible ("3" can still match #3, #13, #30, ...),
 * while "#3" selects only the physical spool whose label says #3.
 */
export function exactSpoolIdQuery(query: string): number | null {
  const match = query.trim().match(/^#(\d+)$/);
  return match ? Number(match[1]) : null;
}

/**
 * Return true when spool matches the search query across all searchable text fields.
 * Case-insensitive. Empty query always returns true.
 */
export function spoolMatchesQuery(spool: InventorySpool, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;

  const exactId = exactSpoolIdQuery(q);
  if (exactId !== null) return spool.id === exactId;

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
    (spool.material_number?.toLowerCase().includes(q) ?? false) ||
    (spool.suppliers?.some(
      (link) =>
        link.supplier_name.toLowerCase().includes(q) ||
        (link.supplier_article_number?.toLowerCase().includes(q) ?? false)
    ) ?? false)
  );
}

/** Filter a spool list by a free-text search query. */
export function filterSpoolsByQuery(spools: InventorySpool[], query: string): InventorySpool[] {
  if (!query) return spools;
  return spools.filter((spool) => spoolMatchesQuery(spool, query));
}
