/**
 * The materials in an archive's `filament_type`.
 *
 * A multi-material print stores them comma-joined, but with two separators:
 * the 3MF parser writes `", "` and the spool-based rewrites (#2563) write
 * `","`. Splitting on `", "` alone left `"PLA Basic,PLA"` as one material in
 * the statistics and the Archives material filter (#3262). Splits on the
 * comma and trims, like the backend's `/archives/stats`, and drops empty and
 * repeated entries so a print is never counted twice for one material.
 */
export function splitFilamentTypes(value: string | null | undefined): string[] {
  if (!value) return [];
  const types: string[] = [];
  for (const part of value.split(',')) {
    const type = part.trim();
    if (type && !types.includes(type)) types.push(type);
  }
  return types;
}
