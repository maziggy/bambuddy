import { useTranslation } from 'react-i18next';
import { MoreVertical, RefreshCw } from 'lucide-react';
import { FilamentHoverCard, EmptySlotHoverCard } from './FilamentHoverCard';
import { useTheme } from '../contexts/ThemeContext';
import type { AMSUnit, AMSTray, LinkedSpoolInfo, SpoolAssignment, Permission } from '../api/client';

const BAMBU_FILAMENT_COLORS: Record<string, string> = {
  // PLA Basic (A00)
  'A00-W1': 'Jade White',
  'A00-P0': 'Beige',
  'A00-D2': 'Light Gray',
  'A00-Y0': 'Yellow',
  'A00-Y2': 'Sunflower Yellow',
  'A00-A1': 'Pumpkin Orange',
  'A00-A0': 'Orange',
  'A00-Y4': 'Gold',
  'A00-G3': 'Bright Green',
  'A00-G1': 'Bambu Green',
  'A00-G2': 'Mistletoe Green',
  'A00-R3': 'Hot Pink',
  'A00-P6': 'Magenta',
  'A00-R0': 'Red',
  'A00-R2': 'Maroon Red',
  'A00-P5': 'Purple',
  'A00-P2': 'Indigo Purple',
  'A00-B5': 'Turquoise',
  'A00-B8': 'Cyan',
  'A00-B3': 'Cobalt Blue',
  'A00-N0': 'Brown',
  'A00-N1': 'Cocoa Brown',
  'A00-Y3': 'Bronze',
  'A00-D0': 'Gray',
  'A00-D1': 'Silver',
  'A00-B1': 'Blue Grey',
  'A00-D3': 'Dark Gray',
  'A00-K0': 'Black',
  // PLA Basic Gradient (A00-M*)
  'A00-M3': 'Pink Citrus',
  'A00-M6': 'Dusk Glare',
  'A00-M0': 'Arctic Whisper',
  'A00-M1': 'Solar Breeze',
  'A00-M5': 'Blueberry Bubblegum',
  'A00-M4': 'Mint Lime',
  'A00-M2': 'Ocean to Meadow',
  'A00-M7': 'Cotton Candy Cloud',
  // PLA Lite (A18)
  'A18-K0': 'Black',
  'A18-D0': 'Gray',
  'A18-W0': 'White',
  'A18-R0': 'Red',
  'A18-Y0': 'Yellow',
  'A18-B0': 'Cyan',
  'A18-B1': 'Blue',
  'A18-P0': 'Matte Beige',
  // PLA Matte (A01)
  'A01-W2': 'Ivory White',
  'A01-W3': 'Bone White',
  'A01-Y2': 'Lemon Yellow',
  'A01-A2': 'Mandarin Orange',
  'A01-P3': 'Sakura Pink',
  'A01-P4': 'Lilac Purple',
  'A01-R3': 'Plum',
  'A01-R1': 'Scarlet Red',
  'A01-R4': 'Dark Red',
  'A01-G0': 'Apple Green',
  'A01-G1': 'Grass Green',
  'A01-G7': 'Dark Green',
  'A01-B4': 'Ice Blue',
  'A01-B0': 'Sky Blue',
  'A01-B3': 'Marine Blue',
  'A01-B6': 'Dark Blue',
  'A01-Y3': 'Desert Tan',
  'A01-N1': 'Latte Brown',
  'A01-N3': 'Caramel',
  'A01-R2': 'Terracotta',
  'A01-N2': 'Dark Brown',
  'A01-N0': 'Dark Chocolate',
  'A01-D3': 'Ash Gray',
  'A01-D0': 'Nardo Gray',
  'A01-K1': 'Charcoal',
  // PLA Glow (A12)
  'A12-G0': 'Green',
  'A12-R0': 'Pink',
  'A12-A0': 'Orange',
  'A12-Y0': 'Yellow',
  'A12-B0': 'Blue',
  // PLA Marble (A07)
  'A07-R5': 'Red Granite',
  'A07-D4': 'White Marble',
  // PLA Aero (A11)
  'A11-W0': 'White',
  'A11-K0': 'Black',
  // PLA Sparkle (A08)
  'A08-G3': 'Alpine Green Sparkle',
  'A08-D5': 'Slate Gray Sparkle',
  'A08-B7': 'Royal Purple Sparkle',
  'A08-R2': 'Crimson Red Sparkle',
  'A08-K2': 'Onyx Black Sparkle',
  'A08-Y1': 'Classic Gold Sparkle',
  // PLA Metal (A02)
  'A02-B2': 'Cobalt Blue Metallic',
  'A02-G2': 'Oxide Green Metallic',
  'A02-Y1': 'Iridium Gold Metallic',
  'A02-D2': 'Iron Gray Metallic',
  // PLA Translucent (A17)
  'A17-B1': 'Blue',
  'A17-A0': 'Orange',
  'A17-P0': 'Purple',
  // PLA Silk+ (A06)
  'A06-Y1': 'Gold',
  'A06-D0': 'Titan Gray',
  'A06-D1': 'Silver',
  'A06-W0': 'White',
  'A06-R0': 'Candy Red',
  'A06-G0': 'Candy Green',
  'A06-G1': 'Mint',
  'A06-B1': 'Blue',
  'A06-B0': 'Baby Blue',
  'A06-P0': 'Purple',
  'A06-R1': 'Rose Gold',
  'A06-R2': 'Pink',
  'A06-Y0': 'Champagne',
  // PLA Silk Multi-Color (A05)
  'A05-M8': 'Dawn Radiance',
  'A05-M4': 'Aurora Purple',
  'A05-M1': 'South Beach',
  'A05-T3': 'Neon City',
  'A05-T2': 'Midnight Blaze',
  'A05-T1': 'Gilded Rose',
  'A05-T4': 'Blue Hawaii',
  'A05-T5': 'Velvet Eclipse',
  // PLA Galaxy (A15)
  'A15-B0': 'Purple',
  'A15-G0': 'Green',
  'A15-G1': 'Nebulae',
  'A15-R0': 'Brown',
  // PLA Wood (A16)
  'A16-K0': 'Black Walnut',
  'A16-R0': 'Rosewood',
  'A16-N0': 'Clay Brown',
  'A16-G0': 'Classic Birch',
  'A16-W0': 'White Oak',
  'A16-Y0': 'Ochre Yellow',
  // PLA-CF (A50)
  'A50-D6': 'Lava Gray',
  'A50-K0': 'Black',
  'A50-B6': 'Royal Blue',
  // PLA Tough+ (A10)
  'A10-W0': 'White',
  'A10-D0': 'Gray',
  // PLA Tough (A09)
  'A09-B5': 'Lavender Blue',
  'A09-B4': 'Light Blue',
  'A09-A0': 'Orange',
  'A09-D1': 'Silver',
  'A09-R3': 'Vermilion Red',
  'A09-Y0': 'Yellow',
  // PETG HF (G02)
  'G02-K0': 'Black',
  'G02-W0': 'White',
  'G02-R0': 'Red',
  'G02-D0': 'Gray',
  'G02-D1': 'Dark Gray',
  'G02-Y1': 'Cream',
  'G02-Y0': 'Yellow',
  'G02-A0': 'Orange',
  'G02-N1': 'Peanut Brown',
  'G02-G1': 'Lime Green',
  'G02-G0': 'Green',
  'G02-G2': 'Forest Green',
  'G02-B1': 'Lake Blue',
  'G02-B0': 'Blue',
  // PETG Translucent (G01)
  'G01-G1': 'Translucent Teal',
  'G01-B0': 'Translucent Light Blue',
  'G01-C0': 'Clear',
  'G01-D0': 'Translucent Gray',
  'G01-G0': 'Translucent Olive',
  'G01-N0': 'Translucent Brown',
  'G01-A0': 'Translucent Orange',
  'G01-P1': 'Translucent Pink',
  'G01-P0': 'Translucent Purple',
  // PETG-CF (G50)
  'G50-P7': 'Violet Purple',
  'G50-K0': 'Black',
  // ABS (B00)
  'B00-D1': 'Silver',
  'B00-K0': 'Black',
  'B00-W0': 'White',
  'B00-G6': 'Bambu Green',
  'B00-G7': 'Olive',
  'B00-Y1': 'Tangerine Yellow',
  'B00-A0': 'Orange',
  'B00-R0': 'Red',
  'B00-B4': 'Azure',
  'B00-B0': 'Blue',
  'B00-B6': 'Navy Blue',
  // ABS-GF (B50)
  'B50-A0': 'Orange',
  'B50-K0': 'Black',
  // ASA (B01)
  'B01-W0': 'White',
  'B01-K0': 'Black',
  'B01-D0': 'Gray',
  // ASA Aero (B02)
  'B02-W0': 'White',
  // PC (C00)
  'C00-C1': 'Transparent',
  'C00-C0': 'Clear Black',
  'C00-K0': 'Black',
  'C00-W0': 'White',
  // PC FR (C01)
  'C01-K0': 'Black',
  // TPU for AMS (U02)
  'U02-B0': 'Blue',
  'U02-D0': 'Gray',
  'U02-K0': 'Black',
  // PAHT-CF (N04)
  'N04-K0': 'Black',
  // PA6-GF (N08)
  'N08-K0': 'Black',
  // Support for PLA/PETG (S02, S05)
  'S02-W0': 'Nature',
  'S02-W1': 'White',
  'S05-C0': 'Black',
  // Support for ABS (S06)
  'S06-W0': 'White',
  // Support for PA/PET (S03)
  'S03-G1': 'Green',
  // PVA (S04)
  'S04-Y0': 'Clear',
};

// Fallback color codes for unknown material prefixes
const BAMBU_COLOR_CODE_FALLBACK: Record<string, string> = {
  'W0': 'White', 'W1': 'Jade White', 'W2': 'Ivory White', 'W3': 'Bone White',
  'Y0': 'Yellow', 'Y1': 'Gold', 'Y2': 'Sunflower Yellow', 'Y3': 'Bronze', 'Y4': 'Gold',
  'A0': 'Orange', 'A1': 'Pumpkin Orange', 'A2': 'Mandarin Orange',
  'R0': 'Red', 'R1': 'Scarlet Red', 'R2': 'Maroon Red', 'R3': 'Hot Pink', 'R4': 'Dark Red', 'R5': 'Red Granite',
  'P0': 'Beige', 'P1': 'Pink', 'P2': 'Indigo Purple', 'P3': 'Sakura Pink', 'P4': 'Lilac Purple', 'P5': 'Purple', 'P6': 'Magenta', 'P7': 'Violet Purple',
  'B0': 'Blue', 'B1': 'Blue Grey', 'B2': 'Cobalt Blue', 'B3': 'Cobalt Blue', 'B4': 'Ice Blue', 'B5': 'Turquoise', 'B6': 'Navy Blue', 'B7': 'Royal Purple', 'B8': 'Cyan',
  'G0': 'Green', 'G1': 'Grass Green', 'G2': 'Mistletoe Green', 'G3': 'Bright Green', 'G6': 'Bambu Green', 'G7': 'Dark Green',
  'N0': 'Brown', 'N1': 'Peanut Brown', 'N2': 'Dark Brown', 'N3': 'Caramel',
  'D0': 'Gray', 'D1': 'Silver', 'D2': 'Light Gray', 'D3': 'Dark Gray', 'D4': 'White Marble', 'D5': 'Slate Gray', 'D6': 'Lava Gray',
  'K0': 'Black', 'K1': 'Charcoal', 'K2': 'Onyx Black',
  'C0': 'Clear Black', 'C1': 'Transparent',
  'M0': 'Arctic Whisper', 'M1': 'Solar Breeze', 'M2': 'Ocean to Meadow', 'M3': 'Pink Citrus', 'M4': 'Aurora Purple', 'M5': 'Blueberry Bubblegum', 'M6': 'Dusk Glare', 'M7': 'Cotton Candy Cloud', 'M8': 'Dawn Radiance',
  'T1': 'Gilded Rose', 'T2': 'Midnight Blaze', 'T3': 'Neon City', 'T4': 'Blue Hawaii', 'T5': 'Velvet Eclipse',
};

// Get color name from Bambu Lab tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
function getBambuColorName(trayIdName: string | null | undefined): string | null {
  if (!trayIdName) return null;

  // First try exact match with full tray_id_name
  if (BAMBU_FILAMENT_COLORS[trayIdName]) {
    return BAMBU_FILAMENT_COLORS[trayIdName];
  }

  // Fall back to color code suffix lookup for unknown material prefixes
  const parts = trayIdName.split('-');
  if (parts.length < 2) return null;
  const colorCode = parts[1];
  return BAMBU_COLOR_CODE_FALLBACK[colorCode] || null;
}

// Convert hex color to basic color name
function hexToBasicColorName(hex: string | null | undefined): string {
  if (!hex || hex.length < 6) return 'Unknown';

  // Parse RGB from hex (format: RRGGBBAA or RRGGBB)
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);

  // Calculate HSL for better color classification
  const max = Math.max(r, g, b) / 255;
  const min = Math.min(r, g, b) / 255;
  const l = (max + min) / 2;

  let h = 0;
  let s = 0;

  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);

    const rNorm = r / 255;
    const gNorm = g / 255;
    const bNorm = b / 255;

    if (max === rNorm) {
      h = ((gNorm - bNorm) / d + (gNorm < bNorm ? 6 : 0)) / 6;
    } else if (max === gNorm) {
      h = ((bNorm - rNorm) / d + 2) / 6;
    } else {
      h = ((rNorm - gNorm) / d + 4) / 6;
    }
  }

  // Convert to degrees
  h = h * 360;

  // Classify by lightness first
  if (l < 0.15) return 'Black';
  if (l > 0.85) return 'White';

  // Low saturation = gray
  if (s < 0.15) {
    if (l < 0.4) return 'Dark Gray';
    if (l > 0.6) return 'Light Gray';
    return 'Gray';
  }

  // Classify by hue
  // Brown is orange/yellow hue with lower lightness
  if (h >= 15 && h < 45 && l < 0.45) return 'Brown';
  if (h >= 45 && h < 70 && l < 0.40) return 'Brown';

  if (h < 15 || h >= 345) return 'Red';
  if (h < 45) return 'Orange';
  if (h < 70) return 'Yellow';
  if (h < 150) return 'Green';
  if (h < 200) return 'Cyan';
  if (h < 260) return 'Blue';
  if (h < 290) return 'Purple';
  return 'Pink';
}

// Format K value with 3 decimal places, default to 0.020 if null
function formatKValue(k: number | null | undefined): string {
  const value = k ?? 0.020;
  return value.toFixed(3);
}

// Nozzle side indicators (Bambu Lab style - square badge with L/R)
function NozzleBadge({ side }: { side: 'L' | 'R' }) {
  const { mode } = useTheme();
  // Light mode: #e7f5e9 (light green), Dark mode: #1a4d2e (dark green)
  const bgColor = mode === 'dark' ? '#1a4d2e' : '#e7f5e9';
  return (
    <span
      className="inline-flex items-center justify-center w-4 h-4 text-[10px] font-bold rounded"
      style={{ backgroundColor: bgColor, color: '#00ae42' }}
    >
      {side}
    </span>
  );
}

/**
 * Check if a tray contains a Bambu Lab spool (RFID-tagged).
 * Only checks hardware identifiers (tray_uuid, tag_uid) — NOT tray_info_idx,
 * which is a filament profile/preset ID that third-party spools also get when
 * the user selects a generic Bambu preset (e.g. "GFA00" for Generic PLA).
 */
function isBambuLabSpool(tray: {
  tray_uuid?: string | null;
  tag_uid?: string | null;
} | null | undefined): boolean {
  if (!tray) return false;

  // Check tray_uuid (32 hex chars, non-zero)
  if (tray.tray_uuid && tray.tray_uuid !== '00000000000000000000000000000000') {
    return true;
  }

  // Check tag_uid (16 hex chars, non-zero)
  if (tray.tag_uid && tray.tag_uid !== '0000000000000000') {
    return true;
  }

  return false;
}

// Get AMS label: AMS-A/B/C/D for regular AMS, HT-A/B for AMS-HT (single spool)
// Always use tray count as the source of truth (1 tray = AMS-HT, 4 trays = regular AMS)
// AMS-HT uses IDs 128+ while regular AMS uses 0-3
function getAmsLabel(amsId: number | string, trayCount: number): string {
  // Ensure amsId is a number (backend might send string)
  const id = typeof amsId === 'string' ? parseInt(amsId, 10) : amsId;
  const safeId = isNaN(id) ? 0 : id;
  const isHt = trayCount === 1;
  // AMS-HT uses IDs starting at 128, regular AMS uses 0-3
  const normalizedId = safeId >= 128 ? safeId - 128 : safeId;
  const letter = String.fromCharCode(65 + normalizedId); // 0=A, 1=B, 2=C, 3=D
  return isHt ? `HT-${letter}` : `AMS-${letter}`;
}

// Get fill bar color based on spool fill level
function getFillBarColor(fillLevel: number): string {
  if (fillLevel > 50) return '#00ae42'; // Green - good
  if (fillLevel >= 15) return '#f59e0b'; // Amber - warning (<= 50%)
  return '#ef4444'; // Red - critical (< 15%)
}

// Calculate fill level from Spoolman weight data (used as fallback when AMS reports 0%)
function getSpoolmanFillLevel(
  linkedSpool: LinkedSpoolInfo | undefined
): number | null {
  if (!linkedSpool?.remaining_weight || !linkedSpool?.filament_weight
    || linkedSpool.filament_weight <= 0) return null;
  return Math.min(100, Math.round(
    (linkedSpool.remaining_weight / linkedSpool.filament_weight) * 100
  ));
}

// Water drop SVG - empty outline (Bambu Lab style from bambu-humidity)
function WaterDropEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.8131 0.00538C18.4463 -0.15091 20.3648 3.14642 20.8264 3.84781C25.4187 10.816 35.3089 26.9368 35.9383 34.8694C37.4182 53.5822 11.882 61.3357 2.53721 45.3789C-1.73471 38.0791 0.016 32.2049 3.178 25.0232C6.99221 16.3662 12.6411 7.90372 17.8131 0.00538ZM18.3738 7.24807L17.5881 7.48441C14.4452 12.9431 10.917 18.2341 8.19369 23.9368C4.6808 31.29 1.18317 38.5479 7.69403 45.5657C17.3058 55.9228 34.9847 46.8808 31.4604 32.8681C29.2558 24.0969 22.4207 15.2913 18.3776 7.24807H18.3738Z" fill="#C3C2C1" />
    </svg>
  );
}

// Water drop SVG - half filled with blue water (Bambu Lab style from bambu-humidity)
function WaterDropHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 35 53" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.3165 0.0038C17.932 -0.14959 19.7971 3.08645 20.2458 3.77481C24.7103 10.6135 34.3251 26.4346 34.937 34.2198C36.3757 52.5848 11.5505 60.1942 2.46584 44.534C-1.68714 37.3735 0.0148 31.6085 3.08879 24.5603C6.79681 16.0605 12.2884 7.75907 17.3165 0.0038ZM17.8615 7.11561L17.0977 7.34755C14.0423 12.7048 10.6124 17.8974 7.96483 23.4941C4.54975 30.7107 1.14949 37.8337 7.47908 44.721C16.8233 54.8856 34.01 46.0117 30.5838 32.2595C28.4405 23.6512 21.7957 15.0093 17.8652 7.11561H17.8615Z" fill="#C3C2C1" />
      <path d="M5.03547 30.112C9.64453 30.4936 11.632 35.7985 16.4154 35.791C19.6339 35.7873 20.2161 33.2283 22.3853 31.6197C31.6776 24.7286 33.5835 37.4894 27.9881 44.4254C18.1878 56.5653 -1.16063 44.6013 5.03917 30.1158L5.03547 30.112Z" fill="#1F8FEB" />
    </svg>
  );
}

// Water drop SVG - fully filled with blue water (Bambu Lab style from bambu-humidity)
function WaterDropFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.9625 4.48059L4.77216 26.3154L2.08228 40.2175L10.0224 50.8414H23.1594L33.3246 42.1693V30.2455L17.9625 4.48059Z" fill="#1F8FEB" />
      <path d="M17.7948 0.00538C18.4273 -0.15091 20.3438 3.14642 20.8048 3.84781C25.3921 10.816 35.2715 26.9368 35.9001 34.8694C37.3784 53.5822 11.8702 61.3357 2.53562 45.3789C-1.73163 38.0829 0.0134 32.2087 3.1757 25.027C6.98574 16.3662 12.6284 7.90372 17.7948 0.00538ZM18.3549 7.24807L17.57 7.48441C14.4306 12.9431 10.9063 18.2341 8.1859 23.9368C4.67686 31.29 1.18305 38.5479 7.68679 45.5657C17.2881 55.9228 34.9476 46.8808 31.4271 32.8681C29.2249 24.0969 22.3974 15.2913 18.3587 7.24807H18.3549Z" fill="#C3C2C1" />
    </svg>
  );
}

// Humidity indicator with water drop that fills based on level (Bambu Lab style)
// Reference: https://github.com/theicedmango/bambu-humidity
interface HumidityIndicatorProps {
  humidity: number | string;
  goodThreshold?: number;  // <= this is green
  fairThreshold?: number;  // <= this is orange, > is red
  onClick?: () => void;
  compact?: boolean;  // Smaller version for grid layout
}

function HumidityIndicator({ humidity, goodThreshold = 40, fairThreshold = 60, onClick, compact }: HumidityIndicatorProps) {
  const humidityValue = typeof humidity === 'string' ? parseInt(humidity, 10) : humidity;
  const good = typeof goodThreshold === 'number' ? goodThreshold : 40;
  const fair = typeof fairThreshold === 'number' ? fairThreshold : 60;

  // Status thresholds (configurable via settings)
  // Good: ≤goodThreshold (green #22a352), Fair: ≤fairThreshold (gold #d4a017), Bad: >fairThreshold (red #c62828)
  let textColor: string;
  let statusText: string;

  if (isNaN(humidityValue)) {
    textColor = '#C3C2C1';
    statusText = 'Unknown';
  } else if (humidityValue <= good) {
    textColor = '#22a352'; // Green - Good
    statusText = 'Good';
  } else if (humidityValue <= fair) {
    textColor = '#d4a017'; // Gold - Fair
    statusText = 'Fair';
  } else {
    textColor = '#c62828'; // Red - Bad
    statusText = 'Bad';
  }

  // Fill level based on status: Good=Empty (dry), Fair=Half, Bad=Full (wet)
  let DropComponent: React.FC<{ className?: string }>;
  if (isNaN(humidityValue)) {
    DropComponent = WaterDropEmpty;
  } else if (humidityValue <= good) {
    DropComponent = WaterDropEmpty; // Good - empty drop (dry)
  } else if (humidityValue <= fair) {
    DropComponent = WaterDropHalf; // Fair - half filled
  } else {
    DropComponent = WaterDropFull; // Bad - full (too humid)
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1 ${onClick ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}`}
      title={`Humidity: ${humidityValue}% - ${statusText}${onClick ? ' (click for history)' : ''}`}
    >
      <DropComponent className={compact ? "w-2.5 h-3" : "w-3 h-4"} />
      <span className={`font-medium tabular-nums ${compact ? 'text-[10px]' : 'text-xs'}`} style={{ color: textColor }}>{humidityValue}%</span>
    </button>
  );
}

// Thermometer SVG - empty outline
function ThermometerEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none" />
      <circle cx="6" cy="15" r="2.5" stroke="#C3C2C1" strokeWidth="1" fill="none" />
    </svg>
  );
}

// Thermometer SVG - half filled (gold - same as humidity fair)
function ThermometerHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="8" width="3" height="4.5" fill="#d4a017" rx="0.5" />
      <circle cx="6" cy="15" r="2" fill="#d4a017" />
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none" />
    </svg>
  );
}

// Thermometer SVG - fully filled (red - same as humidity bad)
function ThermometerFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="3" width="3" height="9.5" fill="#c62828" rx="0.5" />
      <circle cx="6" cy="15" r="2" fill="#c62828" />
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none" />
    </svg>
  );
}

// Temperature indicator with dynamic icon and coloring
interface TemperatureIndicatorProps {
  temp: number;
  goodThreshold?: number;  // <= this is blue
  fairThreshold?: number;  // <= this is orange, > is red
  onClick?: () => void;
  compact?: boolean;  // Smaller version for grid layout
}

function TemperatureIndicator({ temp, goodThreshold = 28, fairThreshold = 35, onClick, compact }: TemperatureIndicatorProps) {
  // Ensure thresholds are numbers
  const good = typeof goodThreshold === 'number' ? goodThreshold : 28;
  const fair = typeof fairThreshold === 'number' ? fairThreshold : 35;

  let textColor: string;
  let statusText: string;
  let ThermoComponent: React.FC<{ className?: string }>;

  if (temp <= good) {
    textColor = '#22a352'; // Green - good (same as humidity)
    statusText = 'Good';
    ThermoComponent = ThermometerEmpty;
  } else if (temp <= fair) {
    textColor = '#d4a017'; // Gold - fair (same as humidity)
    statusText = 'Fair';
    ThermoComponent = ThermometerHalf;
  } else {
    textColor = '#c62828'; // Red - bad (same as humidity)
    statusText = 'Bad';
    ThermoComponent = ThermometerFull;
  }

  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1 ${onClick ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}`}
      title={`Temperature: ${temp}°C - ${statusText}${onClick ? ' (click for history)' : ''}`}
    >
      <ThermoComponent className={compact ? "w-2.5 h-3" : "w-3 h-4"} />
      <span className={`tabular-nums text-right ${compact ? 'text-[10px] w-8' : 'w-12'}`} style={{ color: textColor }}>{temp}°C</span>
    </button>
  );
}

interface AMSUnitCardProps {
  ams: AMSUnit;
  isDualNozzle: boolean;
  amsExtruderMap: Record<string, number>;
  effectiveTrayNow: number | null | undefined;
  filamentInfo?: Record<string, { name: string; k: number | null }>;
  slotPresets?: Record<number, { preset_id: string; preset_name: string }>;
  amsThresholds?: {
    humidityGood: number;
    humidityFair: number;
    tempGood: number;
    tempFair: number;
  };
  printerId: number;
  printerState?: string | null;
  // Spoolman
  spoolmanEnabled: boolean;
  hasUnlinkedSpools: boolean;
  linkedSpools?: Record<string, LinkedSpoolInfo>;
  spoolmanUrl?: string | null | undefined;
  // Inventory
  onGetAssignment?: (printerId: number, amsId: number, trayId: number) => SpoolAssignment | undefined;
  onUnassignSpool?: (printerId: number, amsId: number, trayId: number) => void;
  // Slot menu state
  amsSlotMenu: { amsId: number; slotId: number } | null;
  setAmsSlotMenu: (menu: { amsId: number; slotId: number } | null) => void;
  // Refreshing
  refreshingSlot: { amsId: number; slotId: number } | null;
  onRefreshSlot: (amsId: number, slotId: number) => void;
  // Permissions
  hasPermission: (permission: Permission) => boolean;
  // Modal openers
  onOpenAmsHistory: (amsId: number, amsLabel: string, mode: 'humidity' | 'temperature') => void;
  onOpenLinkSpool: (tagUid: string, trayUuid: string, printerId: number, amsId: number, trayId: number) => void;
  onOpenAssignSpool: (printerId: number, amsId: number, trayId: number, trayInfo: { type: string; color: string; location: string }) => void;
  onOpenConfigureSlot: (config: {
    amsId: number;
    trayId: number;
    trayCount: number;
    trayType?: string;
    trayColor?: string;
    traySubBrands?: string;
    trayInfoIdx?: string;
    extruderId?: number;
    caliIdx?: number | null;
    savedPresetId?: string;
  }) => void;
}

export function AMSUnitCard({
  ams,
  isDualNozzle,
  amsExtruderMap,
  effectiveTrayNow,
  filamentInfo,
  slotPresets,
  amsThresholds,
  printerId,
  printerState,
  spoolmanEnabled,
  hasUnlinkedSpools,
  linkedSpools,
  spoolmanUrl,
  onGetAssignment,
  onUnassignSpool,
  amsSlotMenu,
  setAmsSlotMenu,
  refreshingSlot,
  onRefreshSlot,
  hasPermission,
  onOpenAmsHistory,
  onOpenLinkSpool,
  onOpenAssignSpool,
  onOpenConfigureSlot,
}: AMSUnitCardProps) {
  const { t } = useTranslation();
  const isExternal = ams.id === 255
  const isHt = ams.tray.length <= 1 && !isExternal;
  const isSingleSlot = isHt || isExternal;
  const mappedExtruderId = amsExtruderMap[String(ams.id)];

  // Resolve tray, slot index, and global tray ID per tray entry
  const resolveSlotInfo = (tray: AMSTray | undefined, arrayIdx: number) => {
    if (isExternal) {
      const extTrayId = tray?.id ?? 254;
      const slotTrayId = extTrayId - 254; // 0 or 1
      return { tray, slotIdx: slotTrayId, globalTrayId: extTrayId, slotPresetKey: 255 * 4 + slotTrayId };
    }
    if (isHt) {
      const htTray = ams.tray[0];
      const htSlotId = htTray?.id ?? 0;
      return { tray: htTray, slotIdx: htSlotId, globalTrayId: ams.id * 4 + htSlotId, slotPresetKey: ams.id * 4 + htSlotId };
    }
    const slotIdx = arrayIdx;
    const resolved = ams.tray[slotIdx] || ams.tray.find(t => t.id === slotIdx);
    const globalTrayId = ams.id * 4 + slotIdx;
    return { tray: resolved, slotIdx, globalTrayId, slotPresetKey: globalTrayId };
  };

  // For external: iterate sorted trays; for HT: single tray; for regular: 4 slots
  const slotEntries = isExternal
    ? [...ams.tray].sort((a, b) => (a.id ?? 254) - (b.id ?? 254))
    : isHt
      ? [ams.tray[0]]
      : [undefined, undefined, undefined, undefined]; // placeholders for [0,1,2,3]

  const renderSlot = (trayEntry: AMSTray | undefined, arrayIdx: number) => {
    const { tray, slotIdx, globalTrayId, slotPresetKey } = resolveSlotInfo(trayEntry, arrayIdx);
    const hasFillLevel = tray?.tray_type && tray.remain >= 0;
    const isEmpty = !tray?.tray_type;
    const isActive = effectiveTrayNow === globalTrayId;
    const cloudInfo = tray?.tray_info_idx ? filamentInfo?.[tray.tray_info_idx] : null;
    const slotPreset = slotPresets?.[slotPresetKey];

    // Fill level fallback chain
    const trayTag = tray?.tray_uuid?.toUpperCase();
    const linkedSpool = trayTag ? linkedSpools?.[trayTag] : undefined;
    const spoolmanFill = getSpoolmanFillLevel(linkedSpool);
    const inventoryAssignment = onGetAssignment?.(printerId, ams.id, slotIdx);
    const inventoryFill = (() => {
      const sp = inventoryAssignment?.spool;
      if (sp && sp.label_weight > 0 && sp.weight_used > 0) {
        return Math.round(Math.max(0, sp.label_weight - sp.weight_used) / sp.label_weight * 100);
      }
      return null;
    })();

    // External spools have no AMS remain; regular/HT use AMS remain as primary
    const effectiveFill = isExternal
      ? (spoolmanFill ?? inventoryFill ?? null)
      : (hasFillLevel && tray.remain > 0
        ? tray.remain
        : (spoolmanFill ?? inventoryFill ?? (hasFillLevel ? tray.remain : null)));
    const fillSource = (hasFillLevel && tray.remain === 0 && (spoolmanFill !== null || inventoryFill !== null))
      ? (spoolmanFill !== null ? 'spoolman' as const : 'inventory' as const)
      : 'ams' as const;

    // Build filament data for hover card
    const filamentData = tray?.tray_type ? {
      vendor: (isBambuLabSpool(tray) ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
      profile: cloudInfo?.name || slotPreset?.preset_name || tray.tray_sub_brands || tray.tray_type,
      colorName: getBambuColorName(tray.tray_id_name) || hexToBasicColorName(tray.tray_color),
      colorHex: tray.tray_color || null,
      kFactor: formatKValue(tray.k),
      fillLevel: effectiveFill,
      trayUuid: tray.tray_uuid || null,
      tagUid: tray.tag_uid || null,
      fillSource,
    } : null;

    // For external empty trays, filamentData is set but we need to know it's truly empty
    const hasFilament = isExternal ? !isEmpty : !!filamentData;

    const isRefreshing = refreshingSlot?.amsId === ams.id &&
      refreshingSlot?.slotId === slotIdx;

    // Nozzle label for external dual-nozzle slots
    const extNozzleLabel = isExternal && isDualNozzle
      ? ((tray?.id ?? 254) === 254 ? t('printers.extL') : t('printers.extR'))
      : '';

    // Location label for assign spool modal
    const locationLabel = isExternal
      ? (extNozzleLabel || t('printers.external'))
      : isHt
        ? getAmsLabel(ams.id, ams.tray.length)
        : `${getAmsLabel(ams.id, ams.tray.length)} Slot ${slotIdx + 1}`;

    // ExtruderId for configure slot
    const configExtruderId = isExternal
      ? (isDualNozzle ? ((tray?.id ?? 254) === 254 ? 1 : 0) : undefined)
      : mappedExtruderId;

    const slotVisual = (
      <div
        className={`bg-bambu-dark-tertiary rounded p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${isActive ? `${isSingleSlot ? 'ring-2' : 'ring-1'} ring-bambu-green ring-offset-1 ring-offset-bambu-dark` : ''}`}
      >
        <div
          className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2"
          style={{
            backgroundColor: tray?.tray_color ? `#${tray.tray_color}` : (tray?.tray_type ? '#333' : 'transparent'),
            borderColor: isEmpty ? '#666' : 'rgba(255,255,255,0.1)',
            borderStyle: isEmpty ? 'dashed' : 'solid',
          }}
        />
        <div className={`text-[9px] font-bold truncate ${isExternal && isEmpty ? 'text-white/40' : 'text-white'}`}>
          {tray?.tray_type || '—'}
        </div>
        {/* Fill bar */}
        <div className="mt-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
          {effectiveFill !== null && effectiveFill >= 0 && (isSingleSlot || tray) && !isEmpty ? (
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${effectiveFill}%`,
                backgroundColor: getFillBarColor(effectiveFill),
              }}
            />
          ) : (tray?.tray_type && !isEmpty) ? (
            <div className="h-full w-full rounded-full bg-white/50 dark:bg-gray-500/40" />
          ) : null}
        </div>
      </div>
    );

    return (
      <div key={isExternal ? (tray?.id ?? arrayIdx) : slotIdx} className={`relative group ${isSingleSlot && !isExternal ? 'flex-1 min-w-0' : ''}`}>
        {/* Loading overlay during RFID re-read */}
        {!isExternal && isRefreshing && (
          <div className="absolute inset-0 bg-bambu-dark-tertiary/80 rounded flex items-center justify-center z-20">
            <RefreshCw className="w-4 h-4 text-bambu-green animate-spin" />
          </div>
        )}
        {/* Menu button - appears on hover, hidden when printer busy (not for external spools) */}
        {!isExternal && printerState !== 'RUNNING' && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setAmsSlotMenu(
                amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === slotIdx
                  ? null
                  : { amsId: ams.id, slotId: slotIdx }
              );
            }}
            className="absolute -top-1 -right-1 w-4 h-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 hover:bg-bambu-dark-tertiary"
            title={t('printers.slotOptions')}
          >
            <MoreVertical className="w-2.5 h-2.5 text-bambu-gray" />
          </button>
        )}
        {/* Dropdown menu (not for external spools) */}
        {!isExternal && printerState !== 'RUNNING' && amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === slotIdx && (
          <div className="absolute top-full left-0 mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
            <button
              className={`w-full px-3 py-1.5 text-left text-xs flex items-center gap-2 ${hasPermission('printers:ams_rfid')
                ? 'text-white hover:bg-bambu-dark-tertiary'
                : 'text-bambu-gray/50 cursor-not-allowed'
                }`}
              onClick={(e) => {
                e.stopPropagation();
                if (!hasPermission('printers:ams_rfid')) return;
                onRefreshSlot(ams.id, slotIdx);
                setAmsSlotMenu(null);
              }}
              disabled={isRefreshing || !hasPermission('printers:ams_rfid')}
              title={!hasPermission('printers:ams_rfid') ? t('printers.permission.noAmsRfid') : undefined}
            >
              <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
              {t('printers.rfid.reread')}
            </button>
          </div>
        )}
        {/* Hover card wraps only the visual content */}
        {hasFilament && filamentData ? (
          <FilamentHoverCard
            data={filamentData}
            spoolman={{
              enabled: spoolmanEnabled,
              hasUnlinkedSpools,
              linkedSpoolId: filamentData.trayUuid ? linkedSpools?.[filamentData.trayUuid.toUpperCase()]?.id : undefined,
              spoolmanUrl,
              onLinkSpool: spoolmanEnabled && filamentData.trayUuid ? (uuid) => {
                onOpenLinkSpool(
                  filamentData.tagUid || '',
                  uuid,
                  printerId,
                  ams.id,
                  slotIdx,
                );
              } : undefined,
            }}
            inventory={spoolmanEnabled ? undefined : (() => {
              const assignment = onGetAssignment?.(printerId, ams.id, slotIdx);
              return {
                assignedSpool: assignment?.spool ? {
                  id: assignment.spool.id,
                  material: assignment.spool.material,
                  brand: assignment.spool.brand,
                  color_name: assignment.spool.color_name,
                  remainingWeightGrams: assignment.spool.label_weight > 0
                    ? Math.max(0, assignment.spool.label_weight - assignment.spool.weight_used)
                    : null,
                } : null,
                onAssignSpool: (isExternal || filamentData.vendor !== 'Bambu Lab') ? () => onOpenAssignSpool(
                  printerId,
                  ams.id,
                  slotIdx,
                  {
                    type: filamentData.profile,
                    color: filamentData.colorHex || '',
                    location: locationLabel,
                  },
                ) : undefined,
                onUnassignSpool: assignment && (isExternal || filamentData.vendor !== 'Bambu Lab') ? () => onUnassignSpool?.(printerId, ams.id, slotIdx) : undefined,
              };
            })()}
            configureSlot={{
              enabled: hasPermission('printers:control'),
              onConfigure: () => onOpenConfigureSlot({
                amsId: ams.id,
                trayId: slotIdx,
                trayCount: isExternal ? 1 : ams.tray.length,
                trayType: tray?.tray_type || undefined,
                trayColor: tray?.tray_color || undefined,
                traySubBrands: tray?.tray_sub_brands || undefined,
                trayInfoIdx: tray?.tray_info_idx || undefined,
                extruderId: configExtruderId,
                caliIdx: tray?.cali_idx,
                savedPresetId: slotPreset?.preset_id,
              }),
            }}
          >
            {slotVisual}
          </FilamentHoverCard>
        ) : (
          <EmptySlotHoverCard
            configureSlot={{
              enabled: hasPermission('printers:control'),
              onConfigure: () => onOpenConfigureSlot({
                amsId: ams.id,
                trayId: slotIdx,
                trayCount: isExternal ? 1 : ams.tray.length,
                extruderId: configExtruderId,
              }),
            }}
          >
            {slotVisual}
          </EmptySlotHoverCard>
        )}
      </div>
    );
  };

  const statsIndicators = (vertical?: boolean) => (
    (ams.humidity != null || ams.temp != null) ? (
      <div className={`flex ${vertical ? 'flex-col' : 'items-center'} gap-1.5 ${vertical ? 'shrink-0' : ''}`}>
        {ams.humidity != null && (
          <HumidityIndicator
            humidity={ams.humidity}
            goodThreshold={amsThresholds?.humidityGood}
            fairThreshold={amsThresholds?.humidityFair}
            onClick={() => onOpenAmsHistory(
              ams.id,
              getAmsLabel(ams.id, ams.tray.length),
              'humidity',
            )}
            compact
          />
        )}
        {ams.temp != null && (
          <TemperatureIndicator
            temp={ams.temp}
            goodThreshold={amsThresholds?.tempGood}
            fairThreshold={amsThresholds?.tempFair}
            onClick={() => onOpenAmsHistory(
              ams.id,
              getAmsLabel(ams.id, ams.tray.length),
              'temperature',
            )}
            compact
          />
        )}
      </div>
    ) : null
  );

  const labelAndNozzle = (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-white font-medium">
        {isExternal ? 'EXT' : getAmsLabel(ams.id, ams.tray.length)}
      </span>
      {isDualNozzle && !isExternal && (
        <NozzleBadge side={mappedExtruderId === 1 ? 'L' : 'R'} />
      )}
      {isDualNozzle && isExternal && ams.tray.map(t => (
        <NozzleBadge key={t.id ?? 254} side={(t.id ?? 254) === 254 ? 'R' : 'L'} />
      ))}
    </div>
  );

  // External spools card
  if (isExternal) {
    const trayCount = ams.tray.length;
    return (
      <div className={`p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30 ${trayCount === 1 ? 'flex-[1] min-w-[50px] max-w-[100px]' : 'flex-[2] min-w-[100px] max-w-[100px]'}`}>
        <div className="flex items-center gap-1 mb-2">
          {labelAndNozzle}
        </div>
        <div className={`grid ${trayCount > 1 ? 'grid-cols-2' : 'grid-cols-1'} gap-1.5`}>
          {slotEntries.map((tray, i) => renderSlot(tray, i))}
        </div>
      </div>
    );
  }

  // HT AMS card (single slot)
  if (isHt) {
    return (
      <div className="flex-[2] min-w-[120px] p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
        <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
          {labelAndNozzle}
          <div className="flex gap-1.5 w-full">
            {slotEntries.map((tray, i) => renderSlot(tray, i))}
            {statsIndicators(true)}
          </div>
        </div>
      </div>
    );
  }

  // Regular AMS card (4 slots)
  return (
    <div className="flex-[4] min-w-[180px] p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
      <div className="flex items-center justify-between mb-2">
        {labelAndNozzle}
        {statsIndicators()}
      </div>
      <div className="grid grid-cols-4 gap-1.5">
        {slotEntries.map((tray, i) => renderSlot(tray, i))}
      </div>
    </div>
  );
}
