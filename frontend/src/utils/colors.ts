// Runtime color-name catalog, populated once at app startup by ColorCatalogProvider
// from /api/inventory/colors/map. The backend color_catalog table is the single
// source of truth — no hardcoded hex→name tables live on the frontend anymore.
//
// Keyed by lowercase 6-char hex (no leading '#'). Lookups before the provider has
// fetched the catalog fall through to hexToColorName (HSL-based bucketing). A
// subscribe/getSnapshot pair lets React components re-render via
// useSyncExternalStore when the catalog loads, so pages that mount before the
// fetch resolves (InventoryPage, PrintersPage) update to the catalog name once it
// arrives instead of staying stuck on the HSL fallback.

let runtimeColorCatalog: Record<string, string> = {};
let catalogVersion = 0;
const catalogListeners = new Set<() => void>();

export function setColorCatalog(map: Record<string, string>): void {
  // Normalize keys to lowercase 6-char hex (no '#'), defensively. Backend already
  // does this, but the frontend contract is explicit so callers from tests or
  // future integrations can't accidentally break lookups.
  const normalized: Record<string, string> = {};
  for (const [key, value] of Object.entries(map)) {
    if (!key || !value) continue;
    const hex = key.replace('#', '').toLowerCase().slice(0, 6);
    if (hex.length === 6) normalized[hex] = value;
  }
  runtimeColorCatalog = normalized;
  catalogVersion += 1;
  // Snapshot listeners to avoid mutation-during-iteration if a listener unsubscribes.
  for (const listener of Array.from(catalogListeners)) {
    listener();
  }
}

export function subscribeColorCatalog(listener: () => void): () => void {
  catalogListeners.add(listener);
  return () => {
    catalogListeners.delete(listener);
  };
}

export function getColorCatalogVersion(): number {
  return catalogVersion;
}

/** Test-only hook: reset the catalog to empty so unit tests can exercise fallbacks. */
export function __resetColorCatalogForTests(): void {
  runtimeColorCatalog = {};
  catalogVersion = 0;
  catalogListeners.clear();
}

/**
 * Convert hex color to basic color name using HSL analysis.
 * Used as fallback when hex is not in the runtime catalog.
 */
export function hexToColorName(hex: string | null | undefined): string {
  if (!hex || hex.length < 6) return 'Unknown';
  const cleanHex = hex.replace('#', '');
  const r = parseInt(cleanHex.substring(0, 2), 16);
  const g = parseInt(cleanHex.substring(2, 4), 16);
  const b = parseInt(cleanHex.substring(4, 6), 16);

  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  const l = (max + min) / 2;

  let h = 0;
  let s = 0;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    const rNorm = r / 255, gNorm = g / 255, bNorm = b / 255;
    if (max === rNorm) h = ((gNorm - bNorm) / d + (gNorm < bNorm ? 6 : 0)) / 6;
    else if (max === gNorm) h = ((bNorm - rNorm) / d + 2) / 6;
    else h = ((rNorm - gNorm) / d + 4) / 6;
  }
  h = h * 360;

  if (l < 0.15) return 'Black';
  if (l > 0.85) return 'White';
  if (s < 0.15) {
    if (l < 0.4) return 'Dark Gray';
    if (l > 0.6) return 'Light Gray';
    return 'Gray';
  }
  // Brown is orange/yellow hue with lower lightness
  if (h >= 15 && h < 45 && l < 0.45) return 'Brown';
  if (h >= 45 && h < 70 && l < 0.40) return 'Brown';
  if (h < 15 || h >= 345) return 'Red';
  if (h < 45) return 'Orange';
  if (h < 70) return 'Yellow';
  if (h < 150) return 'Green';
  if (h < 200) return 'Cyan';
  if (h < 260) return 'Blue';
  if (h < 290) return 'Purple';
  return 'Pink';
}

/**
 * Get color name from hex color.
 * Looks up the runtime color catalog (backend-sourced), then falls back to HSL.
 */
export function getColorName(hexColor: string): string {
  if (!hexColor) return hexToColorName(hexColor);
  const hex = hexColor.replace('#', '').toLowerCase().substring(0, 6);
  const mapped = runtimeColorCatalog[hex];
  if (mapped) return mapped;
  return hexToColorName(hexColor);
}

/**
 * Resolve a spool's display color name.
 * Tries: stored color_name (if it's a readable name) → runtime catalog via rgba → null.
 * Detects Bambu internal codes (e.g. "A06-D0") and ignores them in favor of hex lookup
 * because the same code is not globally unique across material families (#857).
 */
export function resolveSpoolColorName(colorName: string | null, rgba: string | null): string | null {
  // If color_name looks like a readable name (no pattern like "X00-Y0"), use it directly
  if (colorName && !/^[A-Z]\d+-[A-Z]\d+$/.test(colorName)) {
    return colorName;
  }
  // Try hex color lookup from rgba via the runtime catalog
  if (rgba && rgba.length >= 6) {
    const hex = rgba.substring(0, 6).toLowerCase();
    const mapped = runtimeColorCatalog[hex];
    if (mapped) return mapped;
  }
  // Return null (displayed as "-") — better than showing a code
  return null;
}

/**
 * Parse an RGBA hex string (e.g., "FF0000FF") to a CSS rgba() color.
 * Returns null for empty, all-zero, or fully transparent colors.
 */
export function parseFilamentColor(rgba: string): string | null {
  if (!rgba || rgba === '00000000' || rgba.length < 6) return null;
  const r = rgba.slice(0, 2);
  const g = rgba.slice(2, 4);
  const b = rgba.slice(4, 6);
  const a = rgba.length >= 8 ? parseInt(rgba.slice(6, 8), 16) / 255 : 1;
  if (a === 0) return null;
  return `rgba(${parseInt(r, 16)}, ${parseInt(g, 16)}, ${parseInt(b, 16)}, ${a})`;
}

/**
 * Check if a hex color is light (for choosing text contrast).
 * Uses luminance formula: 0.299*R + 0.587*G + 0.114*B.
 */
export function isLightColor(hex: string | null): boolean {
  if (!hex || hex.length < 6) return false;
  const cleanHex = hex.replace('#', '');
  const r = parseInt(cleanHex.slice(0, 2), 16);
  const g = parseInt(cleanHex.slice(2, 4), 16);
  const b = parseInt(cleanHex.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}
