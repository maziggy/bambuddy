import { canonicalFilamentType, normalizeColorForCompare } from '../../utils/amsHelpers';

/** Picks the row a stored override is (#3590). An override naming a variant
 *  (`tray_info_idx`) accepts only a row with exactly that idx; one without keeps
 *  the loose rule (exact idx preferred, else either side may lack one). */
export function findStoredMatch<T extends { type: string; color: string; tray_info_idx?: string }>(
  rows: readonly T[],
  stored: { type: string; color: string; tray_info_idx?: string },
): T | undefined {
  const exact = rows.find((f) => matchesInventoryEntry(f, stored, true));
  if (exact || stored.tray_info_idx) return exact;
  return rows.find((f) => matchesInventoryEntry(f, stored, false));
}

/** Same type (canonical), same colour (first 6 hex digits) and a compatible
 *  variant: equal, or one side without. Mirrors the scheduler's match (#3590). */
export function matchesInventoryEntry(
  f: { type: string; color: string; tray_info_idx?: string },
  o: { type: string; color: string; tray_info_idx?: string },
  exactIdx: boolean,
): boolean {
  if (canonicalFilamentType(f.type) !== canonicalFilamentType(o.type)) return false;
  if (normalizeColorForCompare(f.color) !== normalizeColorForCompare(o.color)) return false;
  const a = f.tray_info_idx ?? '';
  const b = o.tray_info_idx ?? '';
  return exactIdx ? a === b : !a || !b || a === b;
}
