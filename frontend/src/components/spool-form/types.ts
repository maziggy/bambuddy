
import type { LinkedCode, Printer, SpoolKProfile } from '../../api/client';

// Catalog color display type (moved from component)
export interface CatalogDisplayColor {
  name: string;
  hex: string;
  manufacturer?: string;
  material?: string;
  // #1340: a catalog entry can carry a gradient + visual effect. When the user
  // picks the entry, we copy these onto the spool's color metadata — the bug
  // was that they were never propagated past the API layer.
  extra_colors?: string | null;
  effect_type?: string | null;
}

// Form data structure
export interface SpoolFormData {
  material: string;
  subtype: string;
  brand: string;
  color_name: string;
  rgba: string;
  // #1154: extra gradient stops + visual effect. Stored as the canonical
  // server form ("ec984c,6cd4bc,..." — no `#`, lowercase). Empty string means
  // solid (the default).
  extra_colors: string;
  effect_type: string;
  label_weight: number;
  core_weight: number;
  core_weight_catalog_id: number | null;
  weight_used: number;
  slicer_filament: string;
  note: string;
  cost_per_kg: number | null;
  // User-defined category + per-spool low-stock threshold override (#729).
  category: string;
  low_stock_threshold_pct: number | null;
  location_id: number | null;
  // When set the spool is linked to a specific Spoolman filament catalog entry;
  // the backend skips find_or_create_filament() and uses this ID directly.
  spoolman_filament_id: number | null;
  // Scanned UPC/EAN, populated automatically by the scan-to-add barcode/label
  // flow, or entered manually to teach the native barcode lookup a mapping
  // ahead of time. Optional and editable — not tied to any single source.
  barcode: string;
}

export const defaultFormData: SpoolFormData = {
  material: '',
  subtype: '',
  brand: '',
  color_name: '',
  rgba: '808080FF',
  extra_colors: '',
  effect_type: '',
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 0,
  slicer_filament: '',
  note: '',
  cost_per_kg: null,
  category: '',
  low_stock_threshold_pct: null,
  location_id: null,
  spoolman_filament_id: null,
  barcode: '',
};

// Printer with calibrations type
export interface PrinterWithCalibrations {
  printer: Printer & { connected?: boolean };
  calibrations: CalibrationProfile[];
}

// Calibration profile from printer status
export interface CalibrationProfile {
  cali_idx: number;
  filament_id: string;
  setting_id: string;
  name: string;
  k_value: number;
  n_coef: number;
  extruder_id?: number | null;
  nozzle_diameter?: string;
}

// Filament option from presets
export interface FilamentOption {
  code: string;
  name: string;
  displayName: string;
  isCustom: boolean;
  allCodes: string[];
}

// Color preset
export interface ColorPreset {
  name: string;
  hex: string;
}

// Section props base
export interface SectionProps {
  formData: SpoolFormData;
  updateField: <K extends keyof SpoolFormData>(key: K, value: SpoolFormData[K]) => void;
}

// Filament section props
export interface FilamentSectionProps extends SectionProps {
  cloudAuthenticated: boolean;
  loadingCloudPresets: boolean;
  presetInputValue: string;
  setPresetInputValue: (value: string) => void;
  selectedPresetOption?: FilamentOption;
  filamentOptions: FilamentOption[];
  availableBrands: string[];
  availableMaterials: string[];
  quickAdd: boolean;
  quantity: number;
  onQuantityChange: (value: number) => void;
  errors?: Partial<Record<keyof SpoolFormData, string>>;
}

// Color section props
export interface ColorSectionProps extends SectionProps {
  recentColors: ColorPreset[];
  onColorUsed: (color: ColorPreset) => void;
  catalogColors: {
    manufacturer: string;
    color_name: string;
    hex_color: string;
    material: string | null;
    extra_colors?: string | null;
    effect_type?: string | null;
  }[];
}

// Additional section props
export interface AdditionalSectionProps extends SectionProps {
  spoolCatalog: { id: number; name: string; weight: number }[];
  currencySymbol: string;
  // Categories already used on other spools — drives the category autocomplete
  // datalist so users naturally re-use existing names instead of creating
  // near-duplicates ("Production" vs "production" vs "prod"). #729
  availableCategories: string[];
  // Global low-stock threshold (%); shown as placeholder on the per-spool
  // override input so users see what they're overriding. #729
  globalLowStockThreshold: number;
  availableLocations?: { id: number; name: string }[];
  onCreateLocation?: (name: string) => Promise<{ id: number; name: string } | null>;
  // When true the empty-spool weight is managed by Spoolman on the filament
  // object, so SpoolWeightPicker is hidden and an info notice is shown instead.
  spoolmanMode?: boolean;
  // Sibling GTIN/SKU codes discovered by cross-referencing OFD/SpoolmanDB-Community
  // against the primary Barcode field's value (see _resolve_barcode in
  // routes/inventory.py) — read-only display only, never submitted with the form.
  linkedCodes?: LinkedCode[];
}

// PA Profile section props
export interface PAProfileSectionProps extends SectionProps {
  printersWithCalibrations: PrinterWithCalibrations[];
  selectedProfiles: Set<string>;
  setSelectedProfiles: React.Dispatch<React.SetStateAction<Set<string>>>;
  expandedPrinters: Set<string>;
  setExpandedPrinters: React.Dispatch<React.SetStateAction<Set<string>>>;
}

// Fields that are prefilled by SpoolmanFilamentPicker. A manual edit to any of
// these breaks the Spoolman catalog link (clears spoolman_filament_id).
// Defined at module scope to avoid stale-closure issues if handlers are memoised.
export const SPOOLMAN_LINKED_FIELDS = new Set<keyof SpoolFormData>([
  'material',
  'subtype',
  'brand',
  'rgba',
  'color_name',
  'label_weight',
]);

// Validation result
export interface ValidationResult {
  isValid: boolean;
  errors: Partial<Record<keyof SpoolFormData, string>>;
}

export function validateForm(
  formData: SpoolFormData,
  quickAdd = false,
  spoolmanMode = false,
): ValidationResult {
  const errors: Partial<Record<keyof SpoolFormData, string>> = {};

  // Quick-add and Spoolman mode only require material (unless a catalog entry is pre-selected)
  if (quickAdd || spoolmanMode) {
    if (!formData.material && !formData.spoolman_filament_id) {
      errors.material = 'Material is required';
    }
    return {
      isValid: Object.keys(errors).length === 0,
      errors,
    };
  }

  if (!formData.slicer_filament) {
    errors.slicer_filament = 'Slicer preset is required';
  }

  if (!formData.material) {
    errors.material = 'Material is required';
  }

  if (!formData.brand) {
    errors.brand = 'Brand is required';
  }

  if (!formData.subtype) {
    errors.subtype = 'Subtype is required';
  }

  return {
    isValid: Object.keys(errors).length === 0,
    errors,
  };
}

// Existing K-profile for a spool (from saved data)
export interface SavedKProfile extends SpoolKProfile {
  printer_serial?: string;
}
