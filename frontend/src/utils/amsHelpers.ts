/**
 * AMS (Automatic Material System) helper utilities for Bambu Lab printers.
 * These functions handle color normalization, slot labeling, and tray ID calculations
 * for AMS, AMS-HT, and external spool configurations.
 */

/**
 * Normalize color format from various sources.
 * API returns "RRGGBBAA" (8-char), 3MF uses "#RRGGBB" (7-char with hash).
 * This normalizes to "#RRGGBB" format.
 */
export function normalizeColor(color: string | null | undefined): string {
  if (!color) return '#808080';
  // Remove alpha channel if present (8-char hex to 6-char)
  const hex = color.replace('#', '').substring(0, 6);
  return `#${hex}`;
}

/**
 * Normalize color for comparison (case-insensitive, strip hash and alpha).
 */
export function normalizeColorForCompare(color: string | undefined): string {
  if (!color) return '';
  return color.replace('#', '').toLowerCase().substring(0, 6);
}

/**
 * Check if two colors are visually similar within a threshold.
 * Uses RGB component comparison with configurable tolerance.
 * @param color1 - First hex color
 * @param color2 - Second hex color
 * @param threshold - Maximum difference per RGB component (default: 40)
 */
export function colorsAreSimilar(
  color1: string | undefined,
  color2: string | undefined,
  threshold = 40
): boolean {
  const hex1 = normalizeColorForCompare(color1);
  const hex2 = normalizeColorForCompare(color2);
  if (!hex1 || !hex2 || hex1.length < 6 || hex2.length < 6) return false;

  const r1 = parseInt(hex1.substring(0, 2), 16);
  const g1 = parseInt(hex1.substring(2, 4), 16);
  const b1 = parseInt(hex1.substring(4, 6), 16);
  const r2 = parseInt(hex2.substring(0, 2), 16);
  const g2 = parseInt(hex2.substring(2, 4), 16);
  const b2 = parseInt(hex2.substring(4, 6), 16);

  return (
    Math.abs(r1 - r2) <= threshold &&
    Math.abs(g1 - g2) <= threshold &&
    Math.abs(b1 - b2) <= threshold
  );
}

/**
 * Format slot label for display in the UI.
 * @param amsId - AMS unit ID (0-3 for regular AMS, 128+ for AMS-HT)
 * @param trayId - Tray/slot ID within the AMS unit (0-3)
 * @param isHt - Whether this is an AMS-HT unit (single tray)
 * @param isExternal - Whether this is the external spool holder
 */
export function formatSlotLabel(
  amsId: number,
  trayId: number,
  isHt: boolean,
  isExternal: boolean
): string {
  if (isExternal) return 'External';
  // Convert AMS ID to letter (A, B, C, D)
  // AMS-HT uses IDs starting at 128
  const letter = String.fromCharCode(65 + (amsId >= 128 ? amsId - 128 : amsId));
  if (isHt) return `HT-${letter}`;
  return `AMS-${letter} Slot ${trayId + 1}`;
}

/**
 * Calculate global tray ID for MQTT command.
 * Used in the ams_mapping array sent to the printer.
 * @param amsId - AMS unit ID (0-3 for regular AMS, 128+ for AMS-HT)
 * @param trayId - Tray/slot ID within the AMS unit
 * @param isExternal - Whether this is the external spool holder
 * @returns Global tray ID (0-15 for AMS, 128+ for AMS-HT, 254 for external)
 */
export function getGlobalTrayId(
  amsId: number,
  trayId: number,
  isExternal: boolean
): number {
  if (isExternal) return 254 + trayId;
  // AMS-HT units have IDs starting at 128 with a single tray — use ID directly
  if (amsId >= 128) return amsId;
  return amsId * 4 + trayId;
}

/**
 * Get minimum datetime for scheduling (now + 1 minute).
 * Returns ISO string format for datetime-local input.
 */
export function getMinDateTime(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() + 1);
  return now.toISOString().slice(0, 16);
}

/**
 * Check if a scheduled time is a placeholder far-future date.
 * Placeholder dates (more than 6 months out) are treated as ASAP.
 */
export function isPlaceholderDate(scheduledTime: string | null | undefined): boolean {
  if (!scheduledTime) return false;
  const sixMonthsFromNow = Date.now() + 180 * 24 * 60 * 60 * 1000;
  return new Date(scheduledTime).getTime() > sixMonthsFromNow;
}
