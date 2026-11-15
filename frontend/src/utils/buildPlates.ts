import type { BuildPlateConfig } from '../api/client';

// Base-type key -> plate icon (#1306).
const BASE_TYPE_ICONS: Record<string, string> = {
  cool_plate: '/img/bed/bed_cool.png',
  textured_cool_plate: '/img/bed/bed_cool.png',
  supertack: '/img/bed/bed_cool_supertack.png',
  engineering: '/img/bed/bed_engineering.png',
  smooth_pei: '/img/bed/bed_pei_cool.png',
  textured_pei: '/img/bed/bed_pei.png',
};

export const PLATE_TYPE_ANY = 'any';

// Shared react-query key for the owned build plates.
export const BUILD_PLATES_QUERY_KEY = ['build-plates'];

export function baseTypeIcon(baseType: string | null | undefined): string | null {
  return (baseType && BASE_TYPE_ICONS[baseType]) || null;
}

/** "Smooth PEI Plate", or "Smooth PEI Plate · Carbon Fiber" with a pattern. */
export function plateLabel(typeLabel: string, pattern?: string | null): string {
  return pattern ? `${typeLabel} · ${pattern}` : typeLabel;
}

export interface PlateOption {
  value: string;  // for a <select>: see plateValue()
  baseType: string;
  pattern: string | null;
  label: string;
}

// A plate as one <select> value. The separator cannot appear in a type key.
const SEPARATOR = '::';

export function plateValue(baseType: string, pattern?: string | null): string {
  return pattern ? `${baseType}${SEPARATOR}${pattern}` : baseType;
}

export function parsePlateValue(value: string): { baseType: string; pattern: string | null } {
  const at = value.indexOf(SEPARATOR);
  return at < 0
    ? { baseType: value, pattern: null }
    : { baseType: value.slice(0, at), pattern: value.slice(at + SEPARATOR.length) };
}

const samePattern = (a: string | null | undefined, b: string | null | undefined) =>
  (a ?? '').trim().toLowerCase() === (b ?? '').trim().toLowerCase();

/**
 * The plates to offer in a picker, as one flat list: each owned type, followed
 * by the patterns under it.
 *
 * `onlyType` limits the list to one type (the print dialog offers only the
 * patterns of the plate the file was sliced for). `keep` is the value already
 * chosen: it stays listed even when its type has been unticked since, so a
 * select never shows a value it has no option for.
 */
export function plateOptions(
  config: BuildPlateConfig | undefined,
  { onlyType, keep }: { onlyType?: string | null; keep?: { baseType: string; pattern: string | null } | null } = {},
): PlateOption[] {
  if (!config) return [];
  const options: PlateOption[] = [];
  for (const type of config.types) {
    if (onlyType && type.key !== onlyType) continue;
    // With onlyType the file decides the type, so whether it is ticked does not matter.
    const offered = Boolean(onlyType) || type.owned;
    const kept = (pattern: string | null) =>
      keep?.baseType === type.key && (pattern ? samePattern(keep.pattern, pattern) : !keep.pattern);
    if (offered || kept(null)) {
      options.push({ value: plateValue(type.key), baseType: type.key, pattern: null, label: type.label });
    }
    for (const pattern of config.patterns) {
      if (pattern.base_type !== type.key || !(offered || kept(pattern.name))) continue;
      options.push({
        value: plateValue(type.key, pattern.name),
        baseType: type.key,
        pattern: pattern.name,
        label: plateLabel(type.label, pattern.name),
      });
    }
  }
  return options;
}

/** The option matching a stored plate (pattern compared ignoring case), if listed. */
export function findPlateOption(
  options: PlateOption[],
  baseType: string | null | undefined,
  pattern: string | null | undefined,
): PlateOption | undefined {
  if (!baseType) return undefined;
  return options.find((o) => o.baseType === baseType && samePattern(o.pattern, pattern));
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
