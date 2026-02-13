import type { ColorPreset } from './types';

// Material options
export const MATERIALS = [
  'PLA', 'PETG', 'ABS', 'TPU', 'ASA', 'PC', 'PA', 'PVA', 'HIPS',
  'PA-CF', 'PETG-CF', 'PLA-CF',
];

// Common spool weights
export const WEIGHTS = [250, 500, 750, 1000, 2000, 3000];

// Default brand options (will be augmented with cloud presets)
export const DEFAULT_BRANDS = [
  'Bambu', 'PolyLite', 'PolyTerra', 'eSUN', 'Overture',
  'Fiberon', 'SUNLU', 'Inland', 'Hatchbox', 'Generic',
];

// Known filament variants/subtypes
export const KNOWN_VARIANTS = [
  'Basic', 'Matte', 'Silk', 'Tough', 'HF', 'High Flow', 'Engineering',
  'Galaxy', 'Glow', 'Marble', 'Metal', 'Rainbow', 'Sparkle', 'Wood',
  'Translucent', 'Transparent', 'Clear', 'Lite', 'Pro', 'Plus', 'Max',
  'Super', 'Ultra', 'Flex', 'Soft', 'Hard', 'Strong', 'Impact',
  'Heat Resistant', 'UV Resistant', 'ESD', 'Conductive', 'Magnetic',
  'Gradient', 'Dual Color', 'Tri Color', 'Multicolor',
];

// Quick color swatches - most common colors (shown by default)
export const QUICK_COLORS: ColorPreset[] = [
  { name: 'Black', hex: '000000' },
  { name: 'White', hex: 'FFFFFF' },
  { name: 'Gray', hex: '808080' },
  { name: 'Red', hex: 'FF0000' },
  { name: 'Orange', hex: 'FFA500' },
  { name: 'Yellow', hex: 'FFFF00' },
  { name: 'Green', hex: '00AE42' },
  { name: 'Blue', hex: '0066FF' },
  { name: 'Purple', hex: '8B00FF' },
  { name: 'Pink', hex: 'FF69B4' },
  { name: 'Brown', hex: '8B4513' },
  { name: 'Silver', hex: 'C0C0C0' },
];

// Extended color palette (shown when expanded)
export const EXTENDED_COLORS: ColorPreset[] = [
  // Reds
  { name: 'Dark Red', hex: '8B0000' },
  { name: 'Crimson', hex: 'DC143C' },
  { name: 'Coral', hex: 'FF7F50' },
  { name: 'Salmon', hex: 'FA8072' },
  // Oranges
  { name: 'Dark Orange', hex: 'FF8C00' },
  { name: 'Peach', hex: 'FFDAB9' },
  // Yellows
  { name: 'Gold', hex: 'FFD700' },
  { name: 'Khaki', hex: 'F0E68C' },
  { name: 'Lemon', hex: 'FFF44F' },
  // Greens
  { name: 'Lime', hex: '32CD32' },
  { name: 'Forest Green', hex: '228B22' },
  { name: 'Olive', hex: '808000' },
  { name: 'Mint', hex: '98FF98' },
  { name: 'Teal', hex: '008080' },
  // Blues
  { name: 'Navy', hex: '000080' },
  { name: 'Sky Blue', hex: '87CEEB' },
  { name: 'Royal Blue', hex: '4169E1' },
  { name: 'Cyan', hex: '00FFFF' },
  { name: 'Turquoise', hex: '40E0D0' },
  // Purples
  { name: 'Violet', hex: 'EE82EE' },
  { name: 'Magenta', hex: 'FF00FF' },
  { name: 'Indigo', hex: '4B0082' },
  { name: 'Lavender', hex: 'E6E6FA' },
  { name: 'Plum', hex: 'DDA0DD' },
  // Pinks
  { name: 'Hot Pink', hex: 'FF69B4' },
  { name: 'Rose', hex: 'FF007F' },
  { name: 'Blush', hex: 'FFB6C1' },
  // Browns
  { name: 'Chocolate', hex: 'D2691E' },
  { name: 'Tan', hex: 'D2B48C' },
  { name: 'Beige', hex: 'F5F5DC' },
  { name: 'Maroon', hex: '800000' },
  // Neutrals
  { name: 'Dark Gray', hex: '404040' },
  { name: 'Light Gray', hex: 'D3D3D3' },
  { name: 'Charcoal', hex: '36454F' },
  { name: 'Ivory', hex: 'FFFFF0' },
  // Bambu specific
  { name: 'Bambu Green', hex: '00AE42' },
  { name: 'Jade White', hex: 'E8E8E8' },
  { name: 'Titan Gray', hex: '5A5A5A' },
];

// All colors combined
export const ALL_COLORS: ColorPreset[] = [...QUICK_COLORS, ...EXTENDED_COLORS];

// Local storage keys
export const RECENT_COLORS_KEY = 'bambuddy-recent-colors';
export const MAX_RECENT_COLORS = 8;
