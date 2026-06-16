import type { SlicerSetting, LocalPreset, BuiltinFilament } from '../../api/client';
import type { ColorPreset, FilamentOption } from './types';
import { KNOWN_VARIANTS, DEFAULT_BRANDS, RECENT_COLORS_KEY, MAX_RECENT_COLORS } from './constants';

// Fallback filament presets when cloud is not available
const FALLBACK_PRESETS: FilamentOption[] = [
  { code: 'GFL00', name: 'Bambu PLA Basic', displayName: 'Bambu PLA Basic', isCustom: false, allCodes: ['GFL00'] },
  { code: 'GFL01', name: 'Bambu PLA Matte', displayName: 'Bambu PLA Matte', isCustom: false, allCodes: ['GFL01'] },
  { code: 'GFL05', name: 'Generic PLA', displayName: 'Generic PLA', isCustom: false, allCodes: ['GFL05'] },
  { code: 'GFG00', name: 'Bambu PETG Basic', displayName: 'Bambu PETG Basic', isCustom: false, allCodes: ['GFG00'] },
  { code: 'GFG05', name: 'Generic PETG', displayName: 'Generic PETG', isCustom: false, allCodes: ['GFG05'] },
  { code: 'GFB00', name: 'Bambu ABS Basic', displayName: 'Bambu ABS Basic', isCustom: false, allCodes: ['GFB00'] },
  { code: 'GFB05', name: 'Generic ABS', displayName: 'Generic ABS', isCustom: false, allCodes: ['GFB05'] },
  { code: 'GFA00', name: 'Bambu ASA Basic', displayName: 'Bambu ASA Basic', isCustom: false, allCodes: ['GFA00'] },
  { code: 'GFU00', name: 'Bambu TPU 95A', displayName: 'Bambu TPU 95A', isCustom: false, allCodes: ['GFU00'] },
  { code: 'GFU05', name: 'Generic TPU', displayName: 'Generic TPU', isCustom: false, allCodes: ['GFU05'] },
  { code: 'GFC00', name: 'Bambu PC Basic', displayName: 'Bambu PC Basic', isCustom: false, allCodes: ['GFC00'] },
  { code: 'GFN00', name: 'Bambu PA Basic', displayName: 'Bambu PA Basic', isCustom: false, allCodes: ['GFN00'] },
  { code: 'GFN05', name: 'Generic PA', displayName: 'Generic PA', isCustom: false, allCodes: ['GFN05'] },
  { code: 'GFS00', name: 'Bambu PLA-CF', displayName: 'Bambu PLA-CF', isCustom: false, allCodes: ['GFS00'] },
  { code: 'GFT00', name: 'Bambu PETG-CF', displayName: 'Bambu PETG-CF', isCustom: false, allCodes: ['GFT00'] },
  { code: 'GFNC0', name: 'Bambu PA-CF', displayName: 'Bambu PA-CF', isCustom: false, allCodes: ['GFNC0'] },
  { code: 'GFV00', name: 'Bambu PVA', displayName: 'Bambu PVA', isCustom: false, allCodes: ['GFV00'] },
];

// Parse a slicer preset name to extract brand, material, and variant
export function parsePresetName(name: string): { brand: string; material: string; variant: string } {
  // Remove @printer suffix (e.g., "@Bambu Lab H2D 0.4 nozzle")
  let cleanName = name.replace(/@.*$/, '').trim();
  // Remove (Custom) tag
  cleanName = cleanName.replace(/\(Custom\)/i, '').trim();
  // Remove leading # or * markers
  cleanName = cleanName.replace(/^[#*]+\s*/, '').trim();

  // Materials list - order matters (longer/more specific first)
  const materials = [
    'PLA-CF', 'PETG-CF', 'ABS-GF', 'ASA-CF', 'PA-CF', 'PAHT-CF', 'PA6-CF', 'PA6-GF',
    'PPA-CF', 'PPA-GF', 'PET-CF', 'PPS-CF', 'PC-CF', 'PC-ABS', 'ABS-GF',
    'PCTG', 'PETG', 'PLA', 'ABS', 'ASA', 'PC', 'PA', 'TPU', 'PVA', 'HIPS', 'BVOH', 'PPS', 'PEEK', 'PEI',
  ];

  // Find material in the name
  let material = '';
  let materialIdx = -1;
  for (const m of materials) {
    const idx = cleanName.toUpperCase().indexOf(m.toUpperCase());
    if (idx !== -1) {
      material = m;
      materialIdx = idx;
      break;
    }
  }

  // Brand is everything before the material
  let brand = '';
  if (materialIdx > 0) {
    brand = cleanName.substring(0, materialIdx).trim();
    brand = brand.replace(/[-_\s]+$/, '');
  }

  // Everything after material is potential variant
  let afterMaterial = '';
  if (materialIdx !== -1 && material) {
    afterMaterial = cleanName.substring(materialIdx + material.length).trim();
    afterMaterial = afterMaterial.replace(/^[-_\s]+/, '');
  }

  // Check for known variant - could be before OR after material
  let variant = '';

  // First check after material (most common)
  for (const v of KNOWN_VARIANTS) {
    if (afterMaterial.toLowerCase().includes(v.toLowerCase())) {
      variant = v;
      break;
    }
  }

  // If no variant found after material, check if brand contains a known variant
  if (!variant && brand) {
    for (const v of KNOWN_VARIANTS) {
      const variantPattern = new RegExp(`\\s+${v}$`, 'i');
      if (variantPattern.test(brand)) {
        variant = v;
        brand = brand.replace(variantPattern, '').trim();
        break;
      }
    }
  }

  return { brand, material, variant };
}

// Extract unique brands from cloud presets and local presets
export function extractBrandsFromPresets(presets: SlicerSetting[], localPresets?: LocalPreset[]): string[] {
  const brandSet = new Set<string>(DEFAULT_BRANDS);

  for (const preset of presets) {
    const { brand } = parsePresetName(preset.name);
    if (brand && brand.length > 1) {
      brandSet.add(brand);
    }
  }

  // Also extract brands from local presets
  if (localPresets) {
    for (const preset of localPresets) {
      if (preset.filament_vendor && preset.filament_vendor.length > 1) {
        brandSet.add(preset.filament_vendor);
      } else {
        const { brand } = parsePresetName(preset.name);
        if (brand && brand.length > 1) {
          brandSet.add(brand);
        }
      }
    }
  }

  return Array.from(brandSet).sort((a, b) => a.localeCompare(b));
}

// Build filament options from local presets (OrcaSlicer / BambuStudio imports).
// Each preset gets its own entry — no base-name collapse — so the spool form
// shows all per-printer/per-nozzle variants the user has imported. The spool
// itself is printer-agnostic, so the variant the user picks just becomes the
// stored slicer_filament code (consumed by normalize_slicer_filament during
// slicing — kept as preset.filament_type when available so the existing
// "GFL05"-style normalisation still resolves).
function buildLocalFilamentOptions(localPresets: LocalPreset[]): FilamentOption[] {
  const filamentPresets = localPresets.filter(p => p.preset_type === 'filament');
  if (filamentPresets.length === 0) return [];

  const options: FilamentOption[] = filamentPresets.map(preset => {
    // Use the unique preset.id (stringified) as the code so each local preset
    // has its own identity. Earlier this was preset.filament_type (e.g. "PLA")
    // which collapsed every PLA local preset onto the same code — picking any
    // of them saved slicer_filament="PLA", a material name the backend cannot
    // resolve back to a specific preset row. The backend handler at
    // inventory.py expects numeric IDs for local-preset slicer_filament values.
    // allCodes still carries the legacy filament_type so findPresetOption
    // resolves existing saved spools that have the old material-name code.
    const code = String(preset.id);
    const legacyCode = preset.filament_type || code;
    const allCodes = Array.from(new Set([code, legacyCode]));
    return {
      code,
      name: preset.name,
      displayName: preset.name,
      isCustom: false,
      allCodes,
    };
  });
  return options.sort((a, b) => a.displayName.localeCompare(b.displayName));
}

// Build filament options by merging cloud presets, local profiles, and built-in
// filaments — matching the behavior of ConfigureAmsSlotModal and the wiki's
// "Where Presets Come From" section. Earlier versions were precedence-based
// (cloud-only when cloud had any presets), which silently hid Local Profiles
// from users logged into Bambu Cloud — see #1248.
export function buildFilamentOptions(
  cloudPresets: SlicerSetting[],
  configuredPrinterModels: Set<string>,
  localPresets?: LocalPreset[],
  builtinFilaments?: BuiltinFilament[],
): FilamentOption[] {
  const customPresets: FilamentOption[] = [];
  const defaultPresets: FilamentOption[] = [];
  const cloudCodes = new Set<string>();

  // 1. Cloud presets — each setting_id gets its own entry. The spool form is
  // printer-agnostic so we deliberately do NOT collapse "@P1S" / "@X1C"
  // variants into a single row; the user picks the variant they want and
  // its setting_id is what gets persisted.
  for (const preset of cloudPresets) {
    if (preset.is_custom) {
      const presetNameUpper = preset.name.toUpperCase();
      const matchesPrinter = configuredPrinterModels.size === 0 ||
        Array.from(configuredPrinterModels).some(model => presetNameUpper.includes(model)) ||
        !presetNameUpper.includes('@');

      if (matchesPrinter) {
        customPresets.push({
          code: preset.setting_id,
          name: preset.name,
          displayName: `${preset.name} (Custom)`,
          isCustom: true,
          allCodes: [preset.setting_id],
        });
        cloudCodes.add(preset.setting_id);
      }
    } else {
      defaultPresets.push({
        code: preset.setting_id,
        name: preset.name,
        displayName: preset.name,
        isCustom: false,
        allCodes: [preset.setting_id],
      });
      cloudCodes.add(preset.setting_id);
    }
  }

  // 2. Local profiles (OrcaSlicer / BambuStudio imports)
  const localOptions = localPresets && localPresets.length > 0
    ? buildLocalFilamentOptions(localPresets)
    : [];

  // 3. Built-in filaments — only those not already represented by a cloud preset.
  // Cloud setting_ids look like "GFSA00", built-in filament_ids look like "GFA00";
  // map between the two so we don't render the same filament twice.
  const builtinOptions: FilamentOption[] = [];
  if (builtinFilaments && builtinFilaments.length > 0) {
    for (const bf of builtinFilaments) {
      const settingId = bf.filament_id.startsWith('GF')
        ? 'GFS' + bf.filament_id.slice(2)
        : bf.filament_id;
      if (cloudCodes.has(bf.filament_id) || cloudCodes.has(settingId)) continue;
      builtinOptions.push({
        code: bf.filament_id,
        name: bf.name,
        displayName: bf.name,
        isCustom: false,
        allCodes: [bf.filament_id, settingId],
      });
    }
  }

  const merged = [
    ...customPresets,
    ...defaultPresets,
    ...localOptions,
    ...builtinOptions,
  ];

  // 4. Hardcoded fallback only when literally every source is empty.
  if (merged.length === 0) return FALLBACK_PRESETS;

  return merged.sort((a, b) => a.displayName.localeCompare(b.displayName));
}

// Find selected preset option
export function findPresetOption(
  slicerFilament: string,
  filamentOptions: FilamentOption[],
): FilamentOption | undefined {
  if (!slicerFilament) return undefined;

  // First try exact match on primary code
  let option = filamentOptions.find(o => o.code === slicerFilament);
  if (!option) {
    // Try matching against any code in allCodes
    option = filamentOptions.find(o => o.allCodes.includes(slicerFilament));
  }
  if (!option) {
    // Try case-insensitive match
    const slicerLower = slicerFilament.toLowerCase();
    option = filamentOptions.find(o =>
      o.code.toLowerCase() === slicerLower ||
      o.allCodes.some(c => c.toLowerCase() === slicerLower),
    );
  }
  return option;
}

// Recent colors management
export function loadRecentColors(): ColorPreset[] {
  try {
    const stored = localStorage.getItem(RECENT_COLORS_KEY);
    if (stored) {
      return JSON.parse(stored) as ColorPreset[];
    }
  } catch {
    // Ignore errors
  }
  return [];
}

export function saveRecentColor(color: ColorPreset, currentRecent: ColorPreset[]): ColorPreset[] {
  const filtered = currentRecent.filter(
    c => c.hex.toUpperCase() !== color.hex.toUpperCase(),
  );
  const updated = [color, ...filtered].slice(0, MAX_RECENT_COLORS);

  try {
    localStorage.setItem(RECENT_COLORS_KEY, JSON.stringify(updated));
  } catch {
    // Ignore errors
  }

  return updated;
}

// Normalise a Bambu filament identifier to its bare filament_id form (#1688).
// Spools store ``slicer_filament`` as a setting_id like "GFSG98_09" (the "_NN"
// suffix is the variant, the "S" infix marks it as a setting_id); printer
// K-profiles store ``filament_id`` as "GFG98" (bare). Both shapes need
// normalising before comparison.
//
// This is the inverse of the filament_id→setting_id mapping at
// ``buildFilamentOptions`` ("GFS" + filament_id.slice(2)), so a round-trip
// stays consistent. Non-Bambu IDs (numeric local-preset IDs, Orca UUIDs)
// are returned unchanged uppercase — they won't match any K-profile's
// filament_id and the caller falls through to name-based matching.
export function toFilamentId(id: string | null | undefined): string {
  if (!id) return '';
  // Drop "_NN" variant suffix.
  let s = id.split('_')[0];
  // Strip the "S" infix in "GFS..." so "GFSG98" → "GFG98".
  if (/^GFS/i.test(s)) s = s.slice(0, 2) + s.slice(3);
  return s.toUpperCase();
}

// "GFx99" identifiers (GFL99, GFG99, GFB99, ...) are Bambu's *generic* filament
// IDs — shared across many different physical filaments. Matching K-profiles
// by an exact generic ID would over-match, so the id-match path skips them and
// the caller falls through to name-based matching.
export function isGenericFilamentId(id: string | null | undefined): boolean {
  return !!id && /^GF[A-Z]99$/i.test(id);
}

// Check if a calibration matches based on brand, material, and variant
export function isMatchingCalibration(
  cal: { name?: string; filament_id?: string },
  formData: { material: string; brand: string; subtype: string; slicer_filament?: string },
): boolean {
  if (!formData.material) return false;

  // Preferred path: exact filament_id match after normalising both sides
  // (#1688). When the spool has a non-generic preset assigned and it agrees
  // with the K-profile's filament_id, this is unambiguous — no name parsing
  // needed. A spool storing "GFSG98_09" matches a K-profile with filament_id
  // "GFG98" without going anywhere near parsePresetName.
  const spoolFid = toFilamentId(formData.slicer_filament);
  const calFid = toFilamentId(cal.filament_id);
  if (spoolFid && calFid && spoolFid === calFid && !isGenericFilamentId(calFid)) {
    return true;
  }

  const profileName = cal.name || '';

  // Remove flow type prefixes
  const cleanName = profileName
    .replace(/^High Flow[_\s]+/i, '')
    .replace(/^Standard[_\s]+/i, '')
    .replace(/^HF[_\s]+/i, '')
    .replace(/^S[_\s]+/i, '')
    .trim();

  const parsed = parsePresetName(cleanName);

  // Match material (required)
  const materialMatch = parsed.material.toUpperCase() === formData.material.toUpperCase();
  if (!materialMatch) return false;

  // Match brand if specified in form
  if (formData.brand) {
    const brandMatch = parsed.brand.toLowerCase().includes(formData.brand.toLowerCase()) ||
      formData.brand.toLowerCase().includes(parsed.brand.toLowerCase());
    if (!brandMatch) return false;
  }

  // Match variant/subtype if specified in form
  if (formData.subtype) {
    const variantMatch = parsed.variant.toLowerCase().includes(formData.subtype.toLowerCase()) ||
      formData.subtype.toLowerCase().includes(parsed.variant.toLowerCase()) ||
      cleanName.toLowerCase().includes(formData.subtype.toLowerCase());
    if (!variantMatch) return false;
  }

  return true;
}
