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
  // Alpha=00 → fully transparent. Name it 'Clear' before falling through to
  // RGB-based naming, otherwise #00000000 (Bambu's transparent code) would
  // resolve to 'Black' via the HSL fallback (#1545).
  if (cleanHex.length === 8 && cleanHex.substring(6, 8).toLowerCase() === '00') {
    return 'Clear';
  }
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
  const clean = hexColor.replace('#', '').toLowerCase();
  if (clean.length === 8 && clean.substring(6, 8) === '00') return 'Clear';
  const hex = clean.substring(0, 6);
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
  if (rgba && rgba.length >= 6) {
    const clean = rgba.replace('#', '').toLowerCase();
    // Transparent rgba: don't fall through to RGB-based lookup that would
    // return 'Black' for #00000000 (#1545).
    if (clean.length === 8 && clean.substring(6, 8) === '00') return 'Clear';
    const hex = clean.substring(0, 6);
    const mapped = runtimeColorCatalog[hex];
    if (mapped) return mapped;
  }
  // Return null (displayed as "-") — better than showing a code
  return null;
}

/**
 * Build a hex string suitable for SVG `fill=` / props that take a single
 * colour value. Preserves the alpha byte when alpha < FF so a transparent
 * spool renders translucent in SVG / CSS rather than collapsing to solid
 * black (#1545). Null / malformed input falls back to `#808080`.
 *
 * Prefer `getSwatchStyle` for `style` objects that paint a div background —
 * that helper paints a visible checkerboard under transparent fills.
 */
export function spoolColorString(rgba: string | null | undefined): string {
  if (!rgba) return '#808080';
  const clean = rgba.replace(/^#/, '');
  if (clean.length < 6) return '#808080';
  if (clean.length >= 8 && clean.substring(6, 8).toLowerCase() !== 'ff') {
    return `#${clean.substring(0, 8)}`;
  }
  return `#${clean.substring(0, 6)}`;
}

/**
 * Build an inline-style object for a simple filament swatch (a div / button
 * background) given a spool's rgba. Opaque colours return a plain
 * `backgroundColor`; transparent (alpha=00) returns a small checkerboard
 * pattern so the user can see the swatch instead of an invisible element
 * (#1545). Null / unparseable input falls back to the neutral `#808080` used
 * elsewhere in the codebase.
 *
 * Use this anywhere a quick swatch was previously painted via
 * `style={{ backgroundColor: '#' + rgba.slice(0, 6) }}` — alpha-stripping
 * silently turned `Clear` spools into solid black.
 *
 * NOTE: `FilamentSwatch` already paints a richer checkerboard underlay
 * automatically for translucent colours; prefer that for new code and use
 * this helper only when retro-fitting an existing simple swatch site.
 */
export function getSwatchStyle(rgba: string | null | undefined): {
  backgroundColor?: string;
  backgroundImage?: string;
  backgroundSize?: string;
} {
  if (!rgba) return { backgroundColor: '#808080' };
  const clean = rgba.replace(/^#/, '');
  if (clean.length < 6) return { backgroundColor: '#808080' };
  if (clean.length >= 8 && clean.substring(6, 8).toLowerCase() === '00') {
    return {
      backgroundImage: 'repeating-conic-gradient(#979797 0% 25%, #f5f5f5 0% 50%)',
      backgroundSize: '8px 8px',
    };
  }
  return { backgroundColor: `#${clean.substring(0, 6)}` };
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
  // Transparent swatches are painted over the light/mid-gray checkerboard
  // underlay, so treat them as light for text-contrast purposes (#1545).
  if (cleanHex.length === 8 && cleanHex.slice(6, 8).toLowerCase() === '00') return true;
  const r = parseInt(cleanHex.slice(0, 2), 16);
  const g = parseInt(cleanHex.slice(2, 4), 16);
  const b = parseInt(cleanHex.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}
