import type { CatalogDisplayColor, ColorSectionProps } from './types';
import { KNOWN_VARIANTS, MATERIALS } from './constants';
import { colorSortKey } from '../../utils/colors';

export type CatalogColorOrder = 'name' | 'color';

export type CatalogColorEntry = ColorSectionProps['catalogColors'][number];

const toDisplay = (c: CatalogColorEntry): CatalogDisplayColor => ({
  name: c.color_name,
  hex: c.hex_color.replace('#', '').substring(0, 6),
  manufacturer: c.manufacturer,
  material: typeof c.material === 'string' ? c.material : undefined,
  extra_colors: c.extra_colors ?? null,
  effect_type: c.effect_type ?? null,
  sortHex: c.hex_color.replace('#', ''),
});

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// Words that describe the filament rather than name a product line, lowercase.
// "Carbon Fiber PLA" and "CF PLA" are not plain PLA; "PolyTerra PLA" is (#3619).
const MODIFIER_WORDS = new Set([
  ...KNOWN_VARIANTS.flatMap(v => v.toLowerCase().split(/\s+/)),
  ...MATERIALS.map(m => m.toLowerCase()),
  'carbon', 'fiber', 'fibre', 'glass', 'cf', 'gf',
]);

/** Is the brand itself a variant or material word ("Silk", "Basic", "PLA")? Such a
 *  "brand" must not pull in every product line that merely contains the word. */
const isVariantWord = (brandLower: string) => MODIFIER_WORDS.has(brandLower) || KNOWN_VARIANTS.some(v => v.toLowerCase() === brandLower);

const hasWord = (text: string, word: string) =>
  new RegExp(`(^|[^a-z0-9])${escapeRegExp(word)}($|[^a-z0-9])`).test(text);

/** Does `text` end with `word` as a whole word, behind at most a product-line
 *  prefix? "PolyTerra PLA" ends with "PLA"; "PLA-CF", "PLA+" and "XPLA" do not, and
 *  neither does "Carbon Fiber PLA" or "CF PLA" unless `formText` (the form's material
 *  and subtype) names that prefix. Both lowercase. (#3619) */
function endsWithWord(text: string, word: string, formText: string): boolean {
  if (!word) return false;
  const m = new RegExp(`^(.*?)(?:^|\\s)${escapeRegExp(word)}$`).exec(text);
  if (!m) return false;
  const prefix = m[1].trim();
  if (!prefix) return true;
  if (hasWord(formText, prefix)) return true;
  const tokens = prefix.split(/\s+/);
  return tokens.length === 1 && !MODIFIER_WORDS.has(tokens[0]);
}

/** Brand shown as a product line: does `text` contain the whole brand phrase as
 *  a whole word ("PolyTerra" in "PolyTerra PLA", not in "PolyTerraX")? (#3619) */
function containsBrandPhrase(text: string, brandLower: string): boolean {
  if (brandLower.length < 2) return false;
  return new RegExp(`(^|[^a-z0-9])${escapeRegExp(brandLower)}($|[^a-z0-9])`).test(text);
}

/** Brand split into words (>= 2 chars) for word-based matching. */
const brandWordsOf = (brand: string | undefined) =>
  brand ? brand.toLowerCase().split(/[\s\-_]+/).filter(w => w.length >= 2) : [];

/**
 * Filter catalog colors by the selected brand + material + subtype.
 *
 * Brand matching is word-based: "mz - Bambu" matches "Bambu Lab" because both contain "Bambu".
 * A catalog entry also belongs to the brand when the brand is one of the manufacturer's
 * product lines, i.e. the whole brand phrase appears in the catalog material
 * ("PolyTerra" -> "PolyTerra PLA" of manufacturer Polymaker) (#3619).
 *
 * Material matching: try exact "PETG Basic" first, fall back to base material "PETG" prefix.
 * Additionally a material that ENDS with the form material as a whole word matches
 * ("PolyTerra PLA" and "PolyLite PLA" for "PLA"; never "PLA-CF" or "PLA+") (#3619).
 */
export function matchCatalogColors(
  catalogColors: CatalogColorEntry[],
  formBrand: string | undefined,
  formMaterial: string | undefined,
  formSubtype: string | undefined,
): CatalogDisplayColor[] {
  if (catalogColors.length === 0) return [];
  const brand = formBrand?.trim();
  const material = formMaterial?.toLowerCase().trim();
  const subtype = formSubtype?.toLowerCase().trim();
  if (!brand && !material) return [];

  const brandWords = brandWordsOf(brand);
  const brandLower = brand?.toLowerCase() ?? '';

  const formText = `${material ?? ''} ${subtype ?? ''}`.trim();
  const brandIsVariantWord = isVariantWord(brandLower);

  const manufacturerMatches = (manufacturer: string) => {
    if (brandWords.length === 0) return true; // no brand filter
    const mfrLower = manufacturer.toLowerCase();
    // A variant word as the "brand" ("Matte") must not match inside a manufacturer name ("MatterHackers").
    if (brandIsVariantWord) return hasWord(mfrLower, brandLower);
    // Any significant brand word found in manufacturer name
    return brandWords.some(w => mfrLower.includes(w));
  };

  // Manufacturer matches, or the brand is a product line named in the material (#3619)
  const brandMatches = (c: CatalogColorEntry) =>
    manufacturerMatches(c.manufacturer) ||
    (brandWords.length > 0 && !brandIsVariantWord && !!c.material && containsBrandPhrase(c.material.toLowerCase(), brandLower));

  // If only brand is provided, return all colors for that manufacturer / product line
  if (brand && !material) {
    const byBrand = catalogColors.filter(brandMatches);
    if (byBrand.length > 0) return byBrand.map(toDisplay);
  }

  // Build the combined material+subtype string to match catalog entries
  const fullMaterial = material && subtype ? `${material} ${subtype}` : '';

  const materialIs = (c: CatalogColorEntry, wanted: string) => {
    const m = c.material?.toLowerCase();
    return !!m && (m === wanted || (brandWords.length > 0 && endsWithWord(m, wanted, formText)));
  };

  // First pass: try exact fullMaterial match (e.g. "PETG Basic")
  if (fullMaterial) {
    const exact = catalogColors.filter(c => brandMatches(c) && materialIs(c, fullMaterial));
    if (exact.length > 0) return exact.map(toDisplay);
    // Try without trailing "+" (e.g. "PLA Silk+" -> "PLA Silk")
    const normalized = fullMaterial.replace(/\+$/, '');
    if (normalized !== fullMaterial) {
      const normMatch = catalogColors.filter(c => brandMatches(c) && materialIs(c, normalized));
      if (normMatch.length > 0) return normMatch.map(toDisplay);
    }
  }

  // Second pass: match base material prefix (e.g. "PETG" matches "PETG Basic", "PETG-HF"),
  // or a product-line material ending with it ("PolyTerra PLA")
  if (material) {
    const byMaterial = catalogColors.filter(c =>
      brandMatches(c) &&
      (!c.material ||
        c.material.toLowerCase().startsWith(material) ||
        (brandWords.length > 0 && endsWithWord(c.material.toLowerCase(), material, formText))),
    );
    if (byMaterial.length > 0) return byMaterial.map(toDisplay);
  }

  return [];
}

/**
 * Every catalog colour of the brand (manufacturer or product line), whatever the
 * material, with the colours matching brand + material first. Backs the
 * "Show all colours" toggle; empty without a brand (#3619).
 */
export function allBrandCatalogColors(
  catalogColors: CatalogColorEntry[],
  formBrand: string | undefined,
  matched: CatalogDisplayColor[],
): CatalogDisplayColor[] {
  const brand = formBrand?.trim();
  // A brand without a word of its own ("X") would match everything.
  if (!brand || brandWordsOf(brand).length === 0) return [];
  const all = matchCatalogColors(catalogColors, brand, '', '');
  const keyOf = (c: CatalogDisplayColor) => `${c.hex}|${c.name}|${c.manufacturer ?? ''}|${c.material ?? ''}`;
  const seen = new Set(matched.map(keyOf));
  return [...matched, ...all.filter(c => !seen.has(keyOf(c)))];
}

// Base materials of the catalog's material names: the form's MATERIALS plus the
// ones the colour catalog uses beyond them ("PAHT-CF", "CPE", "NylonX", "PHA", "XT").
const BASE_MATERIALS = new Set([...MATERIALS.map(m => m.toLowerCase().split('-')[0]), 'paht', 'cpe', 'pha', 'xt']);
const isBaseMaterial = (base: string) => BASE_MATERIALS.has(base) || /^pa\d+$/.test(base) || base.startsWith('nylon');

// Does a word of a catalog material name the base material? "PLA-CF", "PETG+",
// "rPETG/ECOMAX" (recycled, with a suffix) and "NylonX" do; "PolyTerra" does not.
const isMaterialToken = (token: string) => {
  const base = token.toLowerCase().split(/[-+/]/)[0];
  return isBaseMaterial(base) || (base.startsWith('r') && isBaseMaterial(base.slice(1)));
};

/**
 * Other names the chosen brand goes by in the colour catalog, for matching
 * spool names (#3619). Data-driven, nothing hard-coded:
 *  - a brand that is a manufacturer ("Polymaker") also goes by the product lines
 *    its catalog materials start with ("PolyTerra PLA" -> "PolyTerra"), minus the
 *    material word and any variant or material word ("PLA Silk" -> nothing);
 *  - a brand that is such a product line ("PolyTerra") also goes by the manufacturers
 *    whose catalog materials contain it as a whole word ("Polymaker").
 * A brand that is itself a variant or material word has no aliases.
 */
export function brandAliases(catalogColors: CatalogColorEntry[], formBrand: string | undefined): string[] {
  const brand = formBrand?.trim();
  if (!brand || catalogColors.length === 0) return [];
  const brandLower = brand.toLowerCase();
  if (isVariantWord(brandLower)) return [];
  const brandWords = brandWordsOf(brand);
  if (brandWords.length === 0) return [];
  const found = new Map<string, string>();
  const add = (alias: string) => {
    const key = alias.trim().toLowerCase();
    if (key && key !== brandLower && !found.has(key)) found.set(key, alias.trim());
  };
  for (const c of catalogColors) {
    if (!c.material) continue;
    const materialLower = c.material.toLowerCase();
    // Whole words: "XT" is not a way to say 3DXTECH.
    if (brandWords.some(w => hasWord(c.manufacturer.toLowerCase(), w))) {
      const tokens = c.material.trim().split(/\s+/);
      const end = tokens.findIndex(isMaterialToken);
      const lead = (end === -1 ? tokens : tokens.slice(0, end)).slice(0, 3);
      if (lead.length > 0 && !lead.some(tk => MODIFIER_WORDS.has(tk.toLowerCase()))) add(lead.join(' '));
    }
    if (containsBrandPhrase(materialLower, brandLower)) add(c.manufacturer);
  }
  return [...found.values()];
}

/**
 * Catalog colours in the chosen order (#3633). 'name' keeps the input (server)
 * order; 'color' sorts into rainbow order with `colorSortKey`, the same order as
 * the inventory's Color column. A gradient sorts by its first colour (`hex`). The
 * key is taken from the catalog hex with alpha so a Clear entry sorts last, and
 * the sort is stable: equal colours keep their input order.
 */
export function sortCatalogColors(colors: CatalogDisplayColor[], order: CatalogColorOrder): CatalogDisplayColor[] {
  if (order !== 'color') return colors;
  return colors
    .map((c, i) => ({ c, i, key: colorSortKey(c.sortHex ?? c.hex) }))
    .sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.i - b.i))
    .map(e => e.c);
}
