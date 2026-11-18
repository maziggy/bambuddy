import type { CatalogDisplayColor, ColorSectionProps } from './types';

export type CatalogColorEntry = ColorSectionProps['catalogColors'][number];

// Filter catalog colors by the selected brand + material + subtype
// Brand matching is word-based: "mz - Bambu" matches "Bambu Lab" because both contain "Bambu"
// Material matching: try exact "PETG Basic" first, fall back to base material "PETG" prefix
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

  // Split brand into words (>= 2 chars) for word-based matching
  const brandWords = brand
    ? brand.toLowerCase().split(/[\s\-_]+/).filter(w => w.length >= 2)
    : [];

  const brandMatches = (manufacturer: string) => {
    if (brandWords.length === 0) return true; // no brand filter
    const mfrLower = manufacturer.toLowerCase();
    // Any significant brand word found in manufacturer name
    return brandWords.some(w => mfrLower.includes(w));
  };

  // If only brand is provided, return all colors for that manufacturer
  if (brand && !material) {
    const byBrand = catalogColors.filter(c => brandMatches(c.manufacturer));
    if (byBrand.length > 0) {
      return byBrand.map(c => ({
        name: c.color_name,
        hex: c.hex_color.replace('#', '').substring(0, 6),
        manufacturer: c.manufacturer,
        material: typeof c.material === 'string' ? c.material : undefined,
        extra_colors: c.extra_colors ?? null,
        effect_type: c.effect_type ?? null,
      }));
    }
  }

  // Build the combined material+subtype string to match catalog entries
  const fullMaterial = material && subtype ? `${material} ${subtype}` : '';

  // First pass: try exact fullMaterial match (e.g. "PETG Basic")
  if (fullMaterial) {
    const exact = catalogColors.filter(c =>
      brandMatches(c.manufacturer) &&
      c.material?.toLowerCase() === fullMaterial,
    );
    if (exact.length > 0) {
      return exact.map(c => ({
        name: c.color_name,
        hex: c.hex_color.replace('#', '').substring(0, 6),
        manufacturer: c.manufacturer,
        material: typeof c.material === 'string' ? c.material : undefined,
        extra_colors: c.extra_colors ?? null,
        effect_type: c.effect_type ?? null,
      }));
    }
    // Try without trailing "+" (e.g. "PLA Silk+" -> "PLA Silk")
    const normalized = fullMaterial.replace(/\+$/, '');
    if (normalized !== fullMaterial) {
      const normMatch = catalogColors.filter(c =>
        brandMatches(c.manufacturer) &&
        c.material?.toLowerCase() === normalized,
      );
      if (normMatch.length > 0) {
        return normMatch.map(c => ({
          name: c.color_name,
          hex: c.hex_color.replace('#', '').substring(0, 6),
          manufacturer: c.manufacturer,
          material: typeof c.material === 'string' ? c.material : undefined,
          extra_colors: c.extra_colors ?? null,
          effect_type: c.effect_type ?? null,
        }));
      }
    }
  }

  // Second pass: match base material prefix (e.g. "PETG" matches "PETG Basic", "PETG-HF")
  if (material) {
    const byMaterial = catalogColors.filter(c =>
      brandMatches(c.manufacturer) &&
      (!c.material || c.material.toLowerCase().startsWith(material)),
    );
    if (byMaterial.length > 0) {
      return byMaterial.map(c => ({
        name: c.color_name,
        hex: c.hex_color.replace('#', '').substring(0, 6),
        manufacturer: c.manufacturer,
        material: typeof c.material === 'string' ? c.material : undefined,
        extra_colors: c.extra_colors ?? null,
        effect_type: c.effect_type ?? null,
      }));
    }
  }

  return [];
}
