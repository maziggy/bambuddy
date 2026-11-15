import type { BuildPlate } from '../api/client';

// Base-type key -> fallback icon, used until a plate has its own photo (#1306).
const BASE_TYPE_ICONS: Record<string, string> = {
  cool_plate: '/img/bed/bed_cool.png',
  textured_cool_plate: '/img/bed/bed_cool.png',
  supertack: '/img/bed/bed_cool_supertack.png',
  engineering: '/img/bed/bed_engineering.png',
  smooth_pei: '/img/bed/bed_pei_cool.png',
  textured_pei: '/img/bed/bed_pei.png',
};

export const PLATE_TYPE_ANY = 'any';

// Shared react-query key for the build plate catalog.
export const BUILD_PLATES_QUERY_KEY = ['build-plates'];

export function baseTypeIcon(baseType: string | null | undefined): string | null {
  return (baseType && BASE_TYPE_ICONS[baseType]) || null;
}

export function plateImage(plate: Pick<BuildPlate, 'image' | 'base_type'>): string | null {
  return plate.image || baseTypeIcon(plate.base_type);
}

/** Plates grouped by base type, in the order the backend returns them. */
export function groupPlatesByBaseType(plates: BuildPlate[]): Array<{ baseType: string; label: string; plates: BuildPlate[] }> {
  const groups = new Map<string, { baseType: string; label: string; plates: BuildPlate[] }>();
  for (const plate of plates) {
    let group = groups.get(plate.base_type);
    if (!group) {
      group = { baseType: plate.base_type, label: plate.base_type_label, plates: [] };
      groups.set(plate.base_type, group);
    }
    group.plates.push(plate);
  }
  return [...groups.values()];
}

// Mirrors backend/app/utils/bed_types.py: every curr_bed_type spelling -> base type.
const BED_TYPE_ALIASES: Record<string, string> = {
  'cool plate': 'cool_plate',
  'pc plate': 'cool_plate',
  'smooth cool plate': 'cool_plate',
  'textured cool plate': 'textured_cool_plate',
  'supertack plate': 'supertack',
  'cool plate (supertack)': 'supertack',
  'cool plate supertack': 'supertack',
  'bambu cool plate supertack': 'supertack',
  'engineering plate': 'engineering',
  'high temp plate': 'smooth_pei',
  'smooth high temp plate': 'smooth_pei',
  'smooth pei plate': 'smooth_pei',
  'smooth pei plate / high temp plate': 'smooth_pei',
  'textured pei plate': 'textured_pei',
  'pei plate': 'textured_pei',
};

/** Base-type key for a slicer plate name, or null if unknown / Default Plate. */
export function normalizeBedType(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const value = raw.trim();
  if (value in BASE_TYPE_ICONS) return value;
  return BED_TYPE_ALIASES[value.toLowerCase()] ?? null;
}
