import type { SlicerSetting, LocalPreset } from '../../api/client';
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
    'PETG', 'PLA', 'ABS', 'ASA', 'PC', 'PA', 'TPU', 'PVA', 'HIPS', 'BVOH', 'PPS', 'PCTG', 'PEEK', 'PEI',
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

// Build filament options from local presets (OrcaSlicer imports)
function buildLocalFilamentOptions(localPresets: LocalPreset[]): FilamentOption[] {
  const filamentPresets = localPresets.filter(p => p.preset_type === 'filament');
  if (filamentPresets.length === 0) return [];

  const presetsMap = new Map<string, FilamentOption>();
  for (const preset of filamentPresets) {
    const baseName = preset.name.replace(/@.*$/, '').trim();
    const existing = presetsMap.get(baseName);
    if (existing) {
      existing.allCodes.push(String(preset.id));
    } else {
      // Use filament_type as the code if available (e.g. "GFL00"), otherwise use the id
      const code = preset.filament_type || String(preset.id);
      presetsMap.set(baseName, {
        code,
        name: baseName,
        displayName: baseName,
        isCustom: false,
        allCodes: [code],
      });
    }
  }
  return Array.from(presetsMap.values()).sort((a, b) => a.displayName.localeCompare(b.displayName));
}

// Build filament options: cloud presets → local presets → hardcoded fallback
export function buildFilamentOptions(
  cloudPresets: SlicerSetting[],
  configuredPrinterModels: Set<string>,
  localPresets?: LocalPreset[],
): FilamentOption[] {
  // 1. Cloud presets (highest priority)
  if (cloudPresets.length > 0) {
    const customPresets: FilamentOption[] = [];
    const defaultPresetsMap = new Map<string, FilamentOption>();

    for (const preset of cloudPresets) {
      if (preset.is_custom) {
        // Custom presets: include if matches configured printers or no printer filter
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
        }
      } else {
        // Default presets: deduplicate by base name
        const baseName = preset.name.replace(/@.*$/, '').trim();
        const existing = defaultPresetsMap.get(baseName);
        if (existing) {
          existing.allCodes.push(preset.setting_id);
        } else {
          defaultPresetsMap.set(baseName, {
            code: preset.setting_id,
            name: baseName,
            displayName: baseName,
            isCustom: false,
            allCodes: [preset.setting_id],
          });
        }
      }
    }

    return [
      ...customPresets,
      ...Array.from(defaultPresetsMap.values()),
    ].sort((a, b) => a.displayName.localeCompare(b.displayName));
  }

  // 2. Local presets (OrcaSlicer imports)
  if (localPresets && localPresets.length > 0) {
    const localOptions = buildLocalFilamentOptions(localPresets);
    if (localOptions.length > 0) return localOptions;
  }

  // 3. Hardcoded fallback
  return FALLBACK_PRESETS;
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

// Check if a calibration matches based on brand, material, and variant
export function isMatchingCalibration(
  cal: { name?: string; filament_id?: string },
  formData: { material: string; brand: string; subtype: string },
): boolean {
  if (!formData.material) return false;

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
