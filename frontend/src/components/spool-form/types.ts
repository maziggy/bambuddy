import type { Printer, SpoolKProfile } from '../../api/client';

// Form data structure
export interface SpoolFormData {
  material: string;
  subtype: string;
  brand: string;
  color_name: string;
  rgba: string;
  label_weight: number;
  core_weight: number;
  core_weight_catalog_id: number | null;
  weight_used: number;
  slicer_filament: string;
  note: string;
}

export const defaultFormData: SpoolFormData = {
  material: '',
  subtype: '',
  brand: '',
  color_name: '',
  rgba: '808080FF',
  label_weight: 1000,
  core_weight: 250,
  core_weight_catalog_id: null,
  weight_used: 0,
  slicer_filament: '',
  note: '',
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
}

// Color section props
export interface ColorSectionProps extends SectionProps {
  recentColors: ColorPreset[];
  onColorUsed: (color: ColorPreset) => void;
  catalogColors: { manufacturer: string; color_name: string; hex_color: string; material: string | null }[];
}

// Additional section props
export interface AdditionalSectionProps extends SectionProps {
  spoolCatalog: { id: number; name: string; weight: number }[];
}

// PA Profile section props
export interface PAProfileSectionProps extends SectionProps {
  printersWithCalibrations: PrinterWithCalibrations[];
  selectedProfiles: Set<string>;
  setSelectedProfiles: React.Dispatch<React.SetStateAction<Set<string>>>;
  expandedPrinters: Set<string>;
  setExpandedPrinters: React.Dispatch<React.SetStateAction<Set<string>>>;
}

// Validation result
export interface ValidationResult {
  isValid: boolean;
  errors: Partial<Record<keyof SpoolFormData, string>>;
}

export function validateForm(formData: SpoolFormData): ValidationResult {
  const errors: Partial<Record<keyof SpoolFormData, string>> = {};

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
