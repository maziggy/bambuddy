/**
 * Persistent cache of known printer location names.
 *
 * Locations are free-form strings derived from the `printers.location` column.
 * We keep a localStorage mirror so the UI can suggest previously-used names
 * when creating a new location, even if all printers currently have it unset.
 *
 * Stored as a sorted string array to keep the format simple and diff-friendly.
 */

const LOCATIONS_CACHE_KEY = 'printerLocationsCache';

function readLocations(): string[] {
  try {
    const saved = localStorage.getItem(LOCATIONS_CACHE_KEY);
    if (!saved) return [];
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return [];
    // Only keep valid strings
    return parsed.filter((v) => typeof v === 'string') as string[];
  } catch {
    return [];
  }
}

function writeLocations(locations: string[]): void {
  try {
    localStorage.setItem(LOCATIONS_CACHE_KEY, JSON.stringify(locations));
  } catch {
    // Quota exceeded or private mode — ignore
  }
}

/**
 * Read all cached location names.
 */
export function getCachedPrinterLocations(): string[] {
  return readLocations();
}

/**
 * Add a new location name to the cache (if not already present).
 */
export function addCachedPrinterLocation(name: string): void {
  if (!name.trim()) return;
  const locations = readLocations();
  const trimmed = name.trim();
  if (!locations.includes(trimmed)) {
    locations.push(trimmed);
    writeLocations(locations);
  }
}

/**
 * Remove a location name from the cache.
 */
export function removeCachedPrinterLocation(name: string): void {
  const locations = readLocations();
  const filtered = locations.filter((l) => l !== name);
  if (filtered.length !== locations.length) {
    writeLocations(filtered);
  }
}

/**
 * Sync the cached locations with the set of locations currently present in
 * the printer data.  This ensures that if all printers in a location are
 * moved out (via delete-location), the stale name is removed from the cache.
 */
export function syncCachedPrinterLocations(locations: string[]): void {
  const cache = new Set(readLocations());
  const known = new Set(locations);

  // Remove names that no longer exist anywhere
  const stale = [...cache].filter((l) => !known.has(l));
  if (stale.length > 0) {
    const next = readLocations().filter((l) => !stale.includes(l));
    writeLocations(next);
  }

  // Add newly discovered names
  const newNames = [...known].filter((l) => !cache.has(l));
  if (newNames.length > 0) {
    const next = [...readLocations(), ...newNames];
    writeLocations(next);
  }
}
