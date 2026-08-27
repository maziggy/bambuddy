/**
 * Persistent color mapping for printer locations.
 *
 * Maps location names to CSS color values.
 * Stored as a JSON object: { "Workshop": "#ef4444", "Office": "#3b82f6", ... }
 */

const COLORS_KEY = 'printerLocationColors';

const PRESET_COLORS = [
  '#ef4444', // red
  '#f97316', // orange
  '#eab308', // yellow
  '#22c55e', // green
  '#14b8a6', // teal
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#6b7280', // gray
];

function readColors(): Record<string, string> {
  try {
    const saved = localStorage.getItem(COLORS_KEY);
    if (!saved) return {};
    const parsed: unknown = JSON.parse(saved);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
    const result: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string' && value.startsWith('#')) {
        result[key] = value;
      }
    }
    return result;
  } catch {
    return {};
  }
}

function writeColors(colors: Record<string, string>): void {
  try {
    localStorage.setItem(COLORS_KEY, JSON.stringify(colors));
  } catch {
    // Quota exceeded or private mode — ignore
  }
}

/**
 * Get preset colors.
 */
export function getPresetColors(): string[] {
  return PRESET_COLORS;
}

/**
 * Get the color for a location.
 */
export function getLocationColor(locationName: string): string | undefined {
  return readColors()[locationName];
}

/**
 * Set the color for a location.
 */
export function setLocationColor(locationName: string, color: string): void {
  const colors = readColors();
  if (color) {
    colors[locationName] = color;
  } else {
    delete colors[locationName];
  }
  writeColors(colors);
}

/**
 * Remove the color for a location (e.g. when location is deleted).
 */
export function removeLocationColor(locationName: string): void {
  const colors = readColors();
  delete colors[locationName];
  writeColors(colors);
}
