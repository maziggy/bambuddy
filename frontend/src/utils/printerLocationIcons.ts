/**
 * Persistent icon mapping for printer locations.
 *
 * Maps location names to icon names (from AVAILABLE_ICONS in IconPicker).
 * Stored as a JSON object: { "Workshop": "wrench", "Office": "coffee", ... }
 */

const ICONS_KEY = 'printerLocationIcons';

function readIcons(): Record<string, string> {
  try {
    const saved = localStorage.getItem(ICONS_KEY);
    if (!saved) return {};
    const parsed: unknown = JSON.parse(saved);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
    // Only keep string values
    const result: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string') {
        result[key] = value;
      }
    }
    return result;
  } catch {
    return {};
  }
}

function writeIcons(Icons: Record<string, string>): void {
  try {
    localStorage.setItem(ICONS_KEY, JSON.stringify(Icons));
  } catch {
    // Quota exceeded or private mode — ignore
  }
}

/**
 * Get the icon name for a location.
 */
export function getLocationIcon(locationName: string): string | undefined {
  return readIcons()[locationName];
}

/**
 * Set the icon name for a location.
 */
export function setLocationIcon(locationName: string, iconName: string): void {
  const Icons = readIcons();
  if (iconName) {
    Icons[locationName] = iconName;
  } else {
    delete Icons[locationName];
  }
  writeIcons(Icons);
}

/**
 * Remove the icon for a location (e.g. when location is deleted).
 */
export function removeLocationIcon(locationName: string): void {
  const Icons = readIcons();
  delete Icons[locationName];
  writeIcons(Icons);
}
