/**
 * The hex to print next to a swatch in the slot picker (#3159).
 *
 * Shown because two slots whose colour names resolved the same way -- both
 * "Orange", one a beige -- are otherwise indistinguishable in the list, and
 * comparing them against the slice's colour is the reason the picker exists.
 *
 * Alpha is kept only when it carries information: a translucent spool prints
 * all eight digits, an opaque one prints six, so a Clear filament is not shown
 * as if it were solid. Returns null for anything unreadable, and the caller
 * then prints no hex rather than a misleading one.
 */
export function displayHex(color: string | null | undefined): string | null {
  if (!color) return null;
  const clean = color.trim().replace(/^#/, '');
  if (!/^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(clean)) return null;
  const keepAlpha = clean.length === 8 && clean.substring(6, 8).toLowerCase() !== 'ff';
  return `#${(keepAlpha ? clean : clean.substring(0, 6)).toUpperCase()}`;
}
