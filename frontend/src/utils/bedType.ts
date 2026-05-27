// Map slicer bed-type strings to icon assets shipped under /bed-icons/.
// Source values come straight from the 3MF (curr_bed_type in slice_info.config
// or project_settings.config). BambuStudio and OrcaSlicer disagree on a few
// labels for the same physical plate, so this map normalises both spellings
// to a single icon.

const ICON_BASE = '/img/bed';

interface BedTypeInfo {
  icon: string;
  label: string;
}

const BED_TYPE_MAP: Record<string, BedTypeInfo> = {
  'cool plate': { icon: `${ICON_BASE}/bed_cool.png`, label: 'Cool Plate' },
  'pc plate': { icon: `${ICON_BASE}/bed_cool.png`, label: 'Cool Plate' },
  'cool plate (supertack)': { icon: `${ICON_BASE}/bed_cool_supertack.png`, label: 'Cool Plate SuperTack' },
  'supertack plate': { icon: `${ICON_BASE}/bed_cool_supertack.png`, label: 'Cool Plate SuperTack' },
  'bambu cool plate supertack': { icon: `${ICON_BASE}/bed_cool_supertack.png`, label: 'Cool Plate SuperTack' },
  'engineering plate': { icon: `${ICON_BASE}/bed_engineering.png`, label: 'Engineering Plate' },
  'high temp plate': { icon: `${ICON_BASE}/bed_high_templ.png`, label: 'High Temp Plate' },
  'textured pei plate': { icon: `${ICON_BASE}/bed_pei.png`, label: 'Textured PEI Plate' },
  'pei plate': { icon: `${ICON_BASE}/bed_pei.png`, label: 'PEI Plate' },
  'smooth pei plate': { icon: `${ICON_BASE}/bed_pei_cool.png`, label: 'Smooth PEI Plate' },
};

export function getBedTypeInfo(bedType: string | null | undefined): BedTypeInfo | null {
  if (!bedType) return null;
  return BED_TYPE_MAP[bedType.trim().toLowerCase()] ?? null;
}
