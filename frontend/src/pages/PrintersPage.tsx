import { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useTheme } from '../contexts/ThemeContext';
import {
  Plus,
  Link,
  Unlink,
  Signal,
  Clock,
  MoreVertical,
  Trash2,
  RefreshCw,
  Box,
  HardDrive,
  AlertTriangle,
  AlertCircle,
  Terminal,
  Power,
  PowerOff,
  Zap,
  Wrench,
  ChevronDown,
  Pencil,
  ArrowUp,
  ArrowDown,
  Layers,
  Video,
  Search,
  Loader2,
  Square,
  Pause,
  Play,
  X,
  Monitor,
  Fan,
  Wind,
  AirVent,
  Download,
} from 'lucide-react';

// Custom Skip Objects icon - arrow jumping over boxes
const SkipObjectsIcon = ({ className }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
    {/* Three boxes at the bottom */}
    <rect x="2" y="15" width="5" height="5" rx="0.5" />
    <rect x="9.5" y="15" width="5" height="5" rx="0.5" fill="currentColor" opacity="0.3" />
    <rect x="17" y="15" width="5" height="5" rx="0.5" />
    {/* Curved arrow jumping over first box */}
    <path d="M4 12 C4 6, 14 6, 14 12" />
    <polyline points="12,10 14,12 12,14" />
  </svg>
);
import { useNavigate } from 'react-router-dom';
import { api, discoveryApi, firmwareApi } from '../api/client';
import { formatDateOnly } from '../utils/date';
import type { Printer, PrinterCreate, AMSUnit, DiscoveredPrinter, FirmwareUpdateInfo, FirmwareUploadStatus } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { FileManagerModal } from '../components/FileManagerModal';
import { EmbeddedCameraViewer } from '../components/EmbeddedCameraViewer';
import { MQTTDebugModal } from '../components/MQTTDebugModal';
import { HMSErrorModal, filterKnownHMSErrors } from '../components/HMSErrorModal';
import { PrinterQueueWidget } from '../components/PrinterQueueWidget';
import { AMSHistoryModal } from '../components/AMSHistoryModal';
import { FilamentHoverCard, EmptySlotHoverCard } from '../components/FilamentHoverCard';
import { LinkSpoolModal } from '../components/LinkSpoolModal';
import { ConfigureAmsSlotModal } from '../components/ConfigureAmsSlotModal';
import { useToast } from '../contexts/ToastContext';
import { ChamberLight } from '../components/icons/ChamberLight';

// Complete Bambu Lab filament color mapping by tray_id_name
// Source: https://github.com/queengooborg/Bambu-Lab-RFID-Library
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
function hexToBasicColorName(hex: string | null | undefined, t: (key: string) => string): string {
  if (!hex || hex.length < 6) return t('printers.colors.unknown');

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
  if (l < 0.15) return t('printers.colors.black');
  if (l > 0.85) return t('printers.colors.white');

  // Low saturation = gray
  if (s < 0.15) {
    if (l < 0.4) return t('printers.colors.darkGray');
    if (l > 0.6) return t('printers.colors.lightGray');
    return t('printers.colors.gray');
  }

  // Classify by hue
  // Brown is orange/yellow hue with lower lightness
  if (h >= 15 && h < 45 && l < 0.45) return t('printers.colors.brown');
  if (h >= 45 && h < 70 && l < 0.40) return t('printers.colors.brown');

  if (h < 15 || h >= 345) return t('printers.colors.red');
  if (h < 45) return t('printers.colors.orange');
  if (h < 70) return t('printers.colors.yellow');
  if (h < 150) return t('printers.colors.green');
  if (h < 200) return t('printers.colors.cyan');
  if (h < 260) return t('printers.colors.blue');
  if (h < 290) return t('printers.colors.purple');
  if (h < 345) return t('printers.colors.pink');

  return t('printers.colors.unknown');
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

// Water drop SVG - empty outline (Bambu Lab style from bambu-humidity)
function WaterDropEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.8131 0.00538C18.4463 -0.15091 20.3648 3.14642 20.8264 3.84781C25.4187 10.816 35.3089 26.9368 35.9383 34.8694C37.4182 53.5822 11.882 61.3357 2.53721 45.3789C-1.73471 38.0791 0.016 32.2049 3.178 25.0232C6.99221 16.3662 12.6411 7.90372 17.8131 0.00538ZM18.3738 7.24807L17.5881 7.48441C14.4452 12.9431 10.917 18.2341 8.19369 23.9368C4.6808 31.29 1.18317 38.5479 7.69403 45.5657C17.3058 55.9228 34.9847 46.8808 31.4604 32.8681C29.2558 24.0969 22.4207 15.2913 18.3776 7.24807H18.3738Z" fill="#C3C2C1"/>
    </svg>
  );
}

// Water drop SVG - half filled with blue water (Bambu Lab style from bambu-humidity)
function WaterDropHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 35 53" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.3165 0.0038C17.932 -0.14959 19.7971 3.08645 20.2458 3.77481C24.7103 10.6135 34.3251 26.4346 34.937 34.2198C36.3757 52.5848 11.5505 60.1942 2.46584 44.534C-1.68714 37.3735 0.0148 31.6085 3.08879 24.5603C6.79681 16.0605 12.2884 7.75907 17.3165 0.0038ZM17.8615 7.11561L17.0977 7.34755C14.0423 12.7048 10.6124 17.8974 7.96483 23.4941C4.54975 30.7107 1.14949 37.8337 7.47908 44.721C16.8233 54.8856 34.01 46.0117 30.5838 32.2595C28.4405 23.6512 21.7957 15.0093 17.8652 7.11561H17.8615Z" fill="#C3C2C1"/>
      <path d="M5.03547 30.112C9.64453 30.4936 11.632 35.7985 16.4154 35.791C19.6339 35.7873 20.2161 33.2283 22.3853 31.6197C31.6776 24.7286 33.5835 37.4894 27.9881 44.4254C18.1878 56.5653 -1.16063 44.6013 5.03917 30.1158L5.03547 30.112Z" fill="#1F8FEB"/>
    </svg>
  );
}

// Water drop SVG - fully filled with blue water (Bambu Lab style from bambu-humidity)
function WaterDropFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 36 54" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M17.9625 4.48059L4.77216 26.3154L2.08228 40.2175L10.0224 50.8414H23.1594L33.3246 42.1693V30.2455L17.9625 4.48059Z" fill="#1F8FEB"/>
      <path d="M17.7948 0.00538C18.4273 -0.15091 20.3438 3.14642 20.8048 3.84781C25.3921 10.816 35.2715 26.9368 35.9001 34.8694C37.3784 53.5822 11.8702 61.3357 2.53562 45.3789C-1.73163 38.0829 0.0134 32.2087 3.1757 25.027C6.98574 16.3662 12.6284 7.90372 17.7948 0.00538ZM18.3549 7.24807L17.57 7.48441C14.4306 12.9431 10.9063 18.2341 8.1859 23.9368C4.67686 31.29 1.18305 38.5479 7.68679 45.5657C17.2881 55.9228 34.9476 46.8808 31.4271 32.8681C29.2249 24.0969 22.3974 15.2913 18.3587 7.24807H18.3549Z" fill="#C3C2C1"/>
    </svg>
  );
}

// Thermometer SVG - empty outline
function ThermometerEmpty({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
      <circle cx="6" cy="15" r="2.5" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

// Thermometer SVG - half filled (gold - same as humidity fair)
function ThermometerHalf({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="8" width="3" height="4.5" fill="#d4a017" rx="0.5"/>
      <circle cx="6" cy="15" r="2" fill="#d4a017"/>
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

// Thermometer SVG - fully filled (red - same as humidity bad)
function ThermometerFull({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect x="4.5" y="3" width="3" height="9.5" fill="#c62828" rx="0.5"/>
      <circle cx="6" cy="15" r="2" fill="#c62828"/>
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke="#C3C2C1" strokeWidth="1" fill="none"/>
    </svg>
  );
}

// Heater thermometer icon - filled when heating, outline when off
interface HeaterThermometerProps {
  className?: string;
  color: string;  // The color class (e.g., "text-orange-400")
  isHeating: boolean;
}

function HeaterThermometer({ className, color, isHeating }: HeaterThermometerProps) {
  // Extract the actual color from Tailwind class for SVG fill
  const colorMap: Record<string, string> = {
    'text-orange-400': '#fb923c',
    'text-blue-400': '#60a5fa',
    'text-green-400': '#4ade80',
  };
  const fillColor = colorMap[color] || '#888';

  // Glow style when heating
  const glowStyle = isHeating ? {
    filter: `drop-shadow(0 0 4px ${fillColor}) drop-shadow(0 0 8px ${fillColor})`,
  } : {};

  if (isHeating) {
    // Filled thermometer with glow - heater is ON
    return (
      <svg className={className} style={glowStyle} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="4.5" y="3" width="3" height="9.5" fill={fillColor} rx="0.5"/>
        <circle cx="6" cy="15" r="2" fill={fillColor}/>
        <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke={fillColor} strokeWidth="1" fill="none"/>
      </svg>
    );
  }

  // Empty thermometer - heater is OFF
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke={fillColor} strokeWidth="1" fill="none"/>
      <circle cx="6" cy="15" r="2.5" stroke={fillColor} strokeWidth="1" fill="none"/>
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
  const { t } = useTranslation();
  const humidityValue = typeof humidity === 'string' ? parseInt(humidity, 10) : humidity;
  const good = typeof goodThreshold === 'number' ? goodThreshold : 40;
  const fair = typeof fairThreshold === 'number' ? fairThreshold : 60;

  // Status thresholds (configurable via settings)
  // Good: ≤goodThreshold (green #22a352), Fair: ≤fairThreshold (gold #d4a017), Bad: >fairThreshold (red #c62828)
  let textColor: string;
  let statusText: string;

  if (isNaN(humidityValue)) {
    textColor = '#C3C2C1';
    statusText = t('printers.humidity.unknown');
  } else if (humidityValue <= good) {
    textColor = '#22a352'; // Green - Good
    statusText = t('printers.humidity.good');
  } else if (humidityValue <= fair) {
    textColor = '#d4a017'; // Gold - Fair
    statusText = t('printers.humidity.fair');
  } else {
    textColor = '#c62828'; // Red - Bad
    statusText = t('printers.humidity.bad');
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

// Temperature indicator with dynamic icon and coloring
interface TemperatureIndicatorProps {
  temp: number;
  goodThreshold?: number;  // <= this is blue
  fairThreshold?: number;  // <= this is orange, > is red
  onClick?: () => void;
  compact?: boolean;  // Smaller version for grid layout
}

function TemperatureIndicator({ temp, goodThreshold = 28, fairThreshold = 35, onClick, compact }: TemperatureIndicatorProps) {
  const { t } = useTranslation();
  // Ensure thresholds are numbers
  const good = typeof goodThreshold === 'number' ? goodThreshold : 28;
  const fair = typeof fairThreshold === 'number' ? fairThreshold : 35;

  let textColor: string;
  let statusText: string;
  let ThermoComponent: React.FC<{ className?: string }>;

  if (temp <= good) {
    textColor = '#22a352'; // Green - good (same as humidity)
    statusText = t('printers.temperature.good');
    ThermoComponent = ThermometerEmpty;
  } else if (temp <= fair) {
    textColor = '#d4a017'; // Gold - fair (same as humidity)
    statusText = t('printers.temperature.fair');
    ThermoComponent = ThermometerHalf;
  } else {
    textColor = '#c62828'; // Red - bad (same as humidity)
    statusText = t('printers.temperature.bad');
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

function formatTime(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
}

function formatETA(remainingMinutes: number, timeFormat: 'system' | '12h' | '24h' = 'system'): string {
  const now = new Date();
  const eta = new Date(now.getTime() + remainingMinutes * 60 * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const etaDay = new Date(eta);
  etaDay.setHours(0, 0, 0, 0);

  // Build time format options based on setting
  const timeOptions: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit' };
  if (timeFormat === '12h') {
    timeOptions.hour12 = true;
  } else if (timeFormat === '24h') {
    timeOptions.hour12 = false;
  }
  // 'system' leaves hour12 undefined, letting the browser decide

  const timeStr = eta.toLocaleTimeString([], timeOptions);

  // Check if it's tomorrow or later
  const dayDiff = Math.floor((etaDay.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  if (dayDiff === 0) {
    return timeStr;
  } else if (dayDiff === 1) {
    return `Tomorrow ${timeStr}`;
  } else {
    return eta.toLocaleDateString([], { weekday: 'short' }) + ' ' + timeStr;
  }
}

function getPrinterImage(model: string | null | undefined): string {
  if (!model) return '/img/printers/default.png';

  const modelLower = model.toLowerCase().replace(/\s+/g, '');

  // Map model names to image files
  if (modelLower.includes('x1e')) return '/img/printers/x1e.png';
  if (modelLower.includes('x1c') || modelLower.includes('x1carbon')) return '/img/printers/x1c.png';
  if (modelLower.includes('x1')) return '/img/printers/x1c.png';
  if (modelLower.includes('h2d')) return '/img/printers/h2d.png';
  if (modelLower.includes('h2c') || modelLower.includes('h2s')) return '/img/printers/h2d.png';
  if (modelLower.includes('p2s')) return '/img/printers/p1s.png';
  if (modelLower.includes('p1s')) return '/img/printers/p1s.png';
  if (modelLower.includes('p1p')) return '/img/printers/p1p.png';
  if (modelLower.includes('a1mini')) return '/img/printers/a1mini.png';
  if (modelLower.includes('a1')) return '/img/printers/a1.png';

  return '/img/printers/default.png';
}

function getWifiStrength(rssi: number | null | undefined, t: (key: string) => string): { label: string; color: string; bars: number } {
  if (rssi == null) return { label: '', color: 'text-bambu-gray', bars: 0 };
  if (rssi >= -50) return { label: t('printers.wifi.excellent'), color: 'text-bambu-green', bars: 4 };
  if (rssi >= -60) return { label: t('printers.wifi.good'), color: 'text-bambu-green', bars: 3 };
  if (rssi >= -70) return { label: t('printers.wifi.fair'), color: 'text-yellow-400', bars: 2 };
  if (rssi >= -80) return { label: t('printers.wifi.weak'), color: 'text-orange-400', bars: 1 };
  return { label: t('printers.wifi.veryWeak'), color: 'text-red-400', bars: 1 };
}

/**
 * Check if a tray contains a Bambu Lab spool.
 * Uses same logic as backend: tray_info_idx (GF*), tray_uuid, or tag_uid.
 */
function isBambuLabSpool(tray: {
  tray_uuid?: string | null;
  tag_uid?: string | null;
  tray_info_idx?: string | null;
} | null | undefined): boolean {
  if (!tray) return false;

  // Check tray_info_idx first (most reliable - Bambu preset IDs start with "GF")
  if (tray.tray_info_idx && tray.tray_info_idx.startsWith('GF')) {
    return true;
  }

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

function CoverImage({ url, printName }: { url: string | null; printName?: string }) {
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [showOverlay, setShowOverlay] = useState(false);

  return (
    <>
      <div
        className={`w-20 h-20 flex-shrink-0 rounded-lg overflow-hidden bg-bambu-dark-tertiary flex items-center justify-center ${url && loaded ? 'cursor-pointer' : ''}`}
        onClick={() => url && loaded && setShowOverlay(true)}
      >
        {url && !error ? (
          <>
            <img
              src={url}
              alt="Print preview"
              className={`w-full h-full object-cover ${loaded ? 'block' : 'hidden'}`}
              onLoad={() => setLoaded(true)}
              onError={() => setError(true)}
            />
            {!loaded && <Box className="w-8 h-8 text-bambu-gray" />}
          </>
        ) : (
          <Box className="w-8 h-8 text-bambu-gray" />
        )}
      </div>

      {/* Cover Image Overlay */}
      {showOverlay && url && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-8"
          onClick={() => setShowOverlay(false)}
        >
          <div className="relative max-w-2xl max-h-full">
            <img
              src={url}
              alt="Print preview"
              className="max-w-full max-h-[80vh] rounded-lg shadow-2xl"
            />
            {printName && (
              <p className="text-white text-center mt-4 text-lg">{printName}</p>
            )}
          </div>
        </div>
      )}
    </>
  );
}

interface PrinterMaintenanceInfo {
  due_count: number;
  warning_count: number;
  total_print_hours: number;
}

// Status summary bar component - uses queryClient to read cached statuses
function StatusSummaryBar({ printers }: { printers: Printer[] | undefined }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();

  const counts = useMemo(() => {
    let printing = 0;
    let idle = 0;
    let offline = 0;
    let loading = 0;

    printers?.forEach((printer) => {
      const status = queryClient.getQueryData<{ connected: boolean; state: string | null }>(['printerStatus', printer.id]);
      if (status === undefined) {
        // Status not yet loaded - don't count as offline yet
        loading++;
      } else if (!status.connected) {
        offline++;
      } else if (status.state === 'RUNNING') {
        printing++;
      } else {
        idle++;
      }
    });

    return { printing, idle, offline, loading, total: (printers?.length || 0) };
  }, [printers, queryClient]);

  // Subscribe to query cache changes to re-render when status updates
  // Throttled to prevent rapid re-renders from causing tab crashes
  const [, setTick] = useState(0);
  useEffect(() => {
    let pending = false;
    const unsubscribe = queryClient.getQueryCache().subscribe(() => {
      if (!pending) {
        pending = true;
        requestAnimationFrame(() => {
          setTick(t => t + 1);
          pending = false;
        });
      }
    });
    return () => unsubscribe();
  }, [queryClient]);

  if (!printers?.length) return null;

  return (
    <div className="flex items-center gap-4 text-sm">
      {counts.printing > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-bambu-green animate-pulse" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.printing}</span> {t('printers.statusSummary.printing')}
          </span>
        </div>
      )}
      {counts.idle > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-blue-400" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.idle}</span> {t('printers.statusSummary.idle')}
          </span>
        </div>
      )}
      {counts.offline > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-gray-400" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.offline}</span> {t('printers.statusSummary.offline')}
          </span>
        </div>
      )}
    </div>
  );
}

type SortOption = 'name' | 'status' | 'model' | 'location';
type ViewMode = 'expanded' | 'compact';

/**
 * Get human-readable status display text for a printer.
 * Uses stg_cur_name for detailed calibration/preparation stages,
 * otherwise formats the gcode_state nicely.
 */
function getStatusDisplay(state: string | null | undefined, stg_cur_name: string | null | undefined, t: (key: string) => string): string {
  // If we have a specific stage name (calibration, heating, etc.), use it
  if (stg_cur_name) {
    return stg_cur_name;
  }

  // Format the gcode_state nicely
  switch (state) {
    case 'RUNNING':
      return t('printers.status.printing');
    case 'PAUSE':
      return t('printers.status.paused');
    case 'FINISH':
      return t('printers.status.finished');
    case 'FAILED':
      return t('printers.status.failed');
    case 'IDLE':
      return t('printers.status.idle');
    default:
      return state ? state.charAt(0) + state.slice(1).toLowerCase() : t('printers.status.idle');
  }
}

function PrinterCard({
  printer,
  hideIfDisconnected,
  maintenanceInfo,
  viewMode = 'expanded',
  cardSize = 2,
  amsThresholds,
  spoolmanEnabled = false,
  hasUnlinkedSpools = false,
  timeFormat = 'system',
  cameraViewMode = 'window',
  onOpenEmbeddedCamera,
}: {
  printer: Printer;
  hideIfDisconnected?: boolean;
  maintenanceInfo?: PrinterMaintenanceInfo;
  viewMode?: ViewMode;
  cardSize?: number;
  amsThresholds?: {
    humidityGood: number;
    humidityFair: number;
    tempGood: number;
    tempFair: number;
  };
  spoolmanEnabled?: boolean;
  hasUnlinkedSpools?: boolean;
  timeFormat?: 'system' | '12h' | '24h';
  cameraViewMode?: 'window' | 'embedded';
  onOpenEmbeddedCamera?: (printerId: number, printerName: string) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const [showMenu, setShowMenu] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteArchives, setDeleteArchives] = useState(true);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showFileManager, setShowFileManager] = useState(false);
  const [showMQTTDebug, setShowMQTTDebug] = useState(false);
  const [showPowerOnConfirm, setShowPowerOnConfirm] = useState(false);
  const [showPowerOffConfirm, setShowPowerOffConfirm] = useState(false);
  const [showHMSModal, setShowHMSModal] = useState(false);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [showPauseConfirm, setShowPauseConfirm] = useState(false);
  const [showResumeConfirm, setShowResumeConfirm] = useState(false);
  const [showSkipObjectsModal, setShowSkipObjectsModal] = useState(false);
  const [amsHistoryModal, setAmsHistoryModal] = useState<{
    amsId: number;
    amsLabel: string;
    mode: 'humidity' | 'temperature';
  } | null>(null);
  const [linkSpoolModal, setLinkSpoolModal] = useState<{
    trayUuid: string;
    trayInfo: { type: string; color: string; location: string };
  } | null>(null);
  const [configureSlotModal, setConfigureSlotModal] = useState<{
    amsId: number;
    trayId: number;
    trayCount: number;
    trayType?: string;
    trayColor?: string;
    traySubBrands?: string;
    trayInfoIdx?: string;
  } | null>(null);
  const [showFirmwareModal, setShowFirmwareModal] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    refetchInterval: 30000, // Fallback polling, WebSocket handles real-time
  });

  // Check for firmware updates (cached for 5 minutes)
  const { data: firmwareInfo } = useQuery({
    queryKey: ['firmwareUpdate', printer.id],
    queryFn: () => firmwareApi.checkPrinterUpdate(printer.id),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });

  // Collect unique tray_info_idx values for cloud filament info lookup
  const trayInfoIds = useMemo(() => {
    const ids = new Set<string>();
    if (status?.ams) {
      for (const ams of status.ams) {
        for (const tray of ams.tray || []) {
          if (tray.tray_info_idx) {
            ids.add(tray.tray_info_idx);
          }
        }
      }
    }
    if (status?.vt_tray?.tray_info_idx) {
      ids.add(status.vt_tray.tray_info_idx);
    }
    return Array.from(ids);
  }, [status?.ams, status?.vt_tray]);

  // Fetch cloud filament info for tooltips (name includes color, also has K value)
  const { data: filamentInfo } = useQuery({
    queryKey: ['filamentInfo', trayInfoIds],
    queryFn: () => api.getFilamentInfo(trayInfoIds),
    enabled: trayInfoIds.length > 0,
    staleTime: 5 * 60 * 1000, // 5 minutes
  });

  // Fetch slot preset mappings (stores preset name for user-configured slots)
  const { data: slotPresets } = useQuery({
    queryKey: ['slotPresets', printer.id],
    queryFn: () => api.getSlotPresets(printer.id),
    staleTime: 2 * 60 * 1000, // 2 minutes
  });

  // Cache WiFi signal to prevent it disappearing on updates
  const [cachedWifiSignal, setCachedWifiSignal] = useState<number | null>(null);
  useEffect(() => {
    if (status?.wifi_signal != null) {
      setCachedWifiSignal(status.wifi_signal);
    }
  }, [status?.wifi_signal]);
  const wifiSignal = status?.wifi_signal ?? cachedWifiSignal;

  // Cache connected state to prevent flicker when status briefly becomes undefined
  const cachedConnected = useRef<boolean | undefined>(undefined);
  useEffect(() => {
    if (status?.connected !== undefined) {
      cachedConnected.current = status.connected;
    }
  }, [status?.connected]);
  const isConnected = status?.connected ?? cachedConnected.current;

  // Cache ams_extruder_map to prevent L/R indicators bouncing on updates
  const cachedAmsExtruderMap = useRef<Record<string, number>>({});
  useEffect(() => {
    if (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0) {
      cachedAmsExtruderMap.current = status.ams_extruder_map;
    }
  }, [status?.ams_extruder_map]);
  const amsExtruderMap = (status?.ams_extruder_map && Object.keys(status.ams_extruder_map).length > 0)
    ? status.ams_extruder_map
    : cachedAmsExtruderMap.current;

  // Cache AMS data to prevent it disappearing on idle/offline printers
  const cachedAmsData = useRef<AMSUnit[]>([]);
  useEffect(() => {
    if (status?.ams && status.ams.length > 0) {
      cachedAmsData.current = status.ams;
    }
  }, [status?.ams]);
  const amsData = (status?.ams && status.ams.length > 0) ? status.ams : cachedAmsData.current;

  // Cache tray_now to prevent flickering when 255 (unloaded) or undefined values come in
  // Only update cache when we get a valid tray ID (0-253 or 254 for external)
  const cachedTrayNow = useRef<number>(255);
  const currentTrayNow = status?.tray_now;
  // Update cache synchronously during render if we have a valid value
  if (currentTrayNow !== undefined && currentTrayNow !== 255) {
    cachedTrayNow.current = currentTrayNow;
  }
  // Use cached value if current is 255/undefined but we had a valid value before
  const effectiveTrayNow = (currentTrayNow === undefined || currentTrayNow === 255)
    ? cachedTrayNow.current
    : currentTrayNow;

  // Fetch smart plug for this printer
  const { data: smartPlug } = useQuery({
    queryKey: ['smartPlugByPrinter', printer.id],
    queryFn: () => api.getSmartPlugByPrinter(printer.id),
  });

  // Fetch smart plug status if plug exists (faster refresh for energy monitoring)
  const { data: plugStatus } = useQuery({
    queryKey: ['smartPlugStatus', smartPlug?.id],
    queryFn: () => smartPlug ? api.getSmartPlugStatus(smartPlug.id) : null,
    enabled: !!smartPlug,
    refetchInterval: 10000, // 10 seconds for real-time power display
  });

  // Fetch queue count for this printer
  const { data: queueItems } = useQuery({
    queryKey: ['queue', printer.id, 'pending'],
    queryFn: () => api.getQueue(printer.id, 'pending'),
  });
  const queueCount = queueItems?.length || 0;

  // Fetch last completed print for this printer
  const { data: lastPrints } = useQuery({
    queryKey: ['archives', printer.id, 'last'],
    queryFn: () => api.getArchives(printer.id, 1, 0),
    enabled: status?.connected && status?.state !== 'RUNNING',
  });
  const lastPrint = lastPrints?.[0];

  // Determine if this card should be hidden (use cached connected state to prevent flicker)
  const shouldHide = hideIfDisconnected && isConnected === false;

  const deleteMutation = useMutation({
    mutationFn: (options: { deleteArchives: boolean }) =>
      api.deletePrinter(printer.id, options.deleteArchives),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
    },
  });

  const connectMutation = useMutation({
    mutationFn: () => api.connectPrinter(printer.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
  });

  // Smart plug control mutations
  const powerControlMutation = useMutation({
    mutationFn: (action: 'on' | 'off') =>
      smartPlug ? api.controlSmartPlug(smartPlug.id, action) : Promise.reject('No plug'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smartPlugStatus', smartPlug?.id] });
    },
  });

  const toggleAutoOffMutation = useMutation({
    mutationFn: (enabled: boolean) =>
      smartPlug ? api.updateSmartPlug(smartPlug.id, { auto_off: enabled }) : Promise.reject('No plug'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smartPlugByPrinter', printer.id] });
      // Also invalidate the smart-plugs list to keep Settings page in sync
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
    },
  });

  // Print control mutations
  const stopPrintMutation = useMutation({
    mutationFn: () => api.stopPrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printStopped'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToStop'), 'error'),
  });

  const pausePrintMutation = useMutation({
    mutationFn: () => api.pausePrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printPaused'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToPause'), 'error'),
  });

  const resumePrintMutation = useMutation({
    mutationFn: () => api.resumePrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printResumed'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToResume'), 'error'),
  });

  // Chamber light mutation with optimistic update
  const chamberLightMutation = useMutation({
    mutationFn: (on: boolean) => api.setChamberLight(printer.id, on),
    onMutate: async (on) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: ['printerStatus', printer.id] });
      // Snapshot the previous value
      const previousStatus = queryClient.getQueryData(['printerStatus', printer.id]);
      // Optimistically update
      queryClient.setQueryData(['printerStatus', printer.id], (old: typeof status) => ({
        ...old,
        chamber_light: on,
      }));
      return { previousStatus };
    },
    onSuccess: (_, on) => {
      showToast(t(on ? 'printers.toast.chamberLightOn' : 'printers.toast.chamberLightOff'));
    },
    onError: (error: Error, _, context) => {
      // Rollback on error
      if (context?.previousStatus) {
        queryClient.setQueryData(['printerStatus', printer.id], context.previousStatus);
      }
      showToast(error.message || t('printers.toast.failedChamberLight'), 'error');
    },
  });

  // Query for printable objects (for skip functionality)
  // Fetch when printing with 2+ objects OR when modal is open
  const isPrintingWithObjects = (status?.state === 'RUNNING' || status?.state === 'PAUSE' || status?.state === 'PAUSED') && (status?.printable_objects_count ?? 0) >= 2;
  const { data: objectsData, refetch: refetchObjects } = useQuery({
    queryKey: ['printableObjects', printer.id],
    queryFn: () => api.getPrintableObjects(printer.id),
    enabled: showSkipObjectsModal || isPrintingWithObjects,
    refetchInterval: showSkipObjectsModal ? 5000 : (isPrintingWithObjects ? 30000 : false), // 5s when modal open, 30s otherwise
  });

  // Skip objects mutation
  const skipObjectsMutation = useMutation({
    mutationFn: (objectIds: number[]) => api.skipObjects(printer.id, objectIds),
    onSuccess: (data) => {
      showToast(data.message || t('printers.toast.objectsSkipped'));
      refetchObjects();
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToSkipObjects'), 'error'),
  });

  // State for tracking which AMS slot is being refreshed
  const [refreshingSlot, setRefreshingSlot] = useState<{ amsId: number; slotId: number } | null>(null);
  // Track if we've seen the printer enter "busy" state (ams_status_main !== 0)
  const seenBusyStateRef = useRef<boolean>(false);
  // Fallback timeout ref
  const refreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Minimum display time passed
  const minTimePassedRef = useRef<boolean>(false);

  // AMS slot refresh mutation
  const refreshAmsSlotMutation = useMutation({
    mutationFn: ({ amsId, slotId }: { amsId: number; slotId: number }) =>
      api.refreshAmsSlot(printer.id, amsId, slotId),
    onMutate: ({ amsId, slotId }) => {
      // Clear any existing timeout
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
      // Reset state
      seenBusyStateRef.current = false;
      minTimePassedRef.current = false;
      setRefreshingSlot({ amsId, slotId });
      // Minimum display time (2 seconds)
      setTimeout(() => {
        minTimePassedRef.current = true;
      }, 2000);
      // Fallback timeout (30 seconds max)
      refreshTimeoutRef.current = setTimeout(() => {
        setRefreshingSlot(null);
      }, 30000);
    },
    onSuccess: (data) => {
      showToast(data.message || t('printers.toast.rfidRereadInitiated'));
    },
    onError: (error: Error) => {
      showToast(error.message || t('printers.toast.failedRfidReread'), 'error');
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
      setRefreshingSlot(null);
    },
  });

  // Watch ams_status_main to detect when RFID read completes
  // ams_status_main: 0=idle, 2=rfid_identifying
  const deferredClearRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!refreshingSlot) return;

    const amsStatus = status?.ams_status_main ?? 0;

    // Track when we see non-idle state (printer is working)
    if (amsStatus !== 0) {
      seenBusyStateRef.current = true;
      // Cancel any deferred clear since we're back to busy
      if (deferredClearRef.current) {
        clearTimeout(deferredClearRef.current);
        deferredClearRef.current = null;
      }
    }

    // When we've seen busy and now idle, clear (with min time check)
    if (seenBusyStateRef.current && amsStatus === 0) {
      if (minTimePassedRef.current) {
        // Min time passed - clear now
        if (refreshTimeoutRef.current) {
          clearTimeout(refreshTimeoutRef.current);
        }
        setRefreshingSlot(null);
      } else {
        // Schedule clear after min time (2 seconds from start)
        if (!deferredClearRef.current) {
          deferredClearRef.current = setTimeout(() => {
            if (refreshTimeoutRef.current) {
              clearTimeout(refreshTimeoutRef.current);
            }
            setRefreshingSlot(null);
          }, 2000);
        }
      }
    }

    return () => {
      if (deferredClearRef.current) {
        clearTimeout(deferredClearRef.current);
      }
    };
  }, [status?.ams_status_main, refreshingSlot]);

  // State for AMS slot menu
  const [amsSlotMenu, setAmsSlotMenu] = useState<{ amsId: number; slotId: number } | null>(null);

  if (shouldHide) {
    return null;
  }

  // Size-based styling helpers
  const getImageSize = () => {
    switch (cardSize) {
      case 1: return 'w-10 h-10';
      case 2: return 'w-14 h-14';
      case 3: return 'w-16 h-16';
      case 4: return 'w-20 h-20';
      default: return 'w-14 h-14';
    }
  };
  const getTitleSize = () => {
    switch (cardSize) {
      case 1: return 'text-base truncate';
      case 2: return 'text-lg';
      case 3: return 'text-xl';
      case 4: return 'text-2xl';
      default: return 'text-lg';
    }
  };
  const getSpacing = () => {
    switch (cardSize) {
      case 1: return 'mb-2';
      case 2: return 'mb-4';
      case 3: return 'mb-5';
      case 4: return 'mb-6';
      default: return 'mb-4';
    }
  };

  return (
    <Card className="relative">
      <CardContent className={cardSize >= 3 ? 'p-5' : ''}>
        {/* Header */}
        <div className={getSpacing()}>
          {/* Top row: Image, Name, Menu */}
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              {/* Printer Model Image */}
              <img
                src={getPrinterImage(printer.model)}
                alt={printer.model || 'Printer'}
                className={`object-contain rounded-lg bg-bambu-dark flex-shrink-0 ${getImageSize()}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className={`font-semibold text-white ${getTitleSize()}`}>{printer.name}</h3>
                  {/* Connection indicator dot for compact mode */}
                  {viewMode === 'compact' && (
                    <div
                      className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        status?.connected ? 'bg-bambu-green' : 'bg-red-500'
                      }`}
                      title={status?.connected ? t('printers.connected') : t('printers.offline')}
                    />
                  )}
                </div>
                <p className="text-sm text-bambu-gray">
                  {printer.model || t('printers.unknownModel')}
                  {/* Nozzle Info - only in expanded */}
                  {viewMode === 'expanded' && status?.nozzles && status.nozzles[0]?.nozzle_diameter && (
                    <span className="ml-1.5 text-bambu-gray" title={status.nozzles[0].nozzle_type || 'Nozzle'}>
                      • {status.nozzles[0].nozzle_diameter}mm
                    </span>
                  )}
                  {viewMode === 'expanded' && maintenanceInfo && maintenanceInfo.total_print_hours > 0 && (
                    <span className="ml-2 text-bambu-gray">
                      <Clock className="w-3 h-3 inline-block mr-1" />
                      {Math.round(maintenanceInfo.total_print_hours)}h
                    </span>
                  )}
                </p>
              </div>
            </div>
            {/* Menu button */}
            <div className="relative flex-shrink-0">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowMenu(!showMenu)}
              >
                <MoreVertical className="w-4 h-4" />
              </Button>
              {showMenu && (
                <div className="absolute right-0 mt-2 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-10">
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowEditModal(true);
                      setShowMenu(false);
                    }}
                  >
                    <Pencil className="w-4 h-4" />
                    {t('common.edit')}
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      connectMutation.mutate();
                      setShowMenu(false);
                    }}
                  >
                    <RefreshCw className="w-4 h-4" />
                    {t('printers.menu.reconnect')}
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowMQTTDebug(true);
                      setShowMenu(false);
                    }}
                  >
                    <Terminal className="w-4 h-4" />
                    {t('printers.menu.mqttDebug')}
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm text-red-400 hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowDeleteConfirm(true);
                      setShowMenu(false);
                    }}
                  >
                    <Trash2 className="w-4 h-4" />
                    {t('common.delete')}
                  </button>
                </div>
              )}
            </div>
          </div>

          {/* Badges row - only in expanded mode */}
          {viewMode === 'expanded' && (
            <div className="flex flex-wrap items-center gap-2 mt-2">
              {/* Connection status badge */}
              <span
                className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs ${
                  status?.connected
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : 'bg-red-500/20 text-red-400'
                }`}
              >
                {status?.connected ? (
                  <Link className="w-3 h-3" />
                ) : (
                  <Unlink className="w-3 h-3" />
                )}
                {status?.connected ? t('printers.connected') : t('printers.offline')}
              </span>
              {/* WiFi signal strength indicator */}
              {status?.connected && wifiSignal != null && (
                <span
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs ${
                    wifiSignal >= -50
                      ? 'bg-bambu-green/20 text-bambu-green'
                      : wifiSignal >= -60
                      ? 'bg-bambu-green/20 text-bambu-green'
                      : wifiSignal >= -70
                      ? 'bg-amber-500/20 text-amber-600'
                      : wifiSignal >= -80
                      ? 'bg-orange-500/20 text-orange-600'
                      : 'bg-red-500/20 text-red-600'
                  }`}
                  title={`WiFi: ${wifiSignal} dBm - ${getWifiStrength(wifiSignal, t).label}`}
                >
                  <Signal className="w-3 h-3" />
                  {wifiSignal}dBm
                </span>
              )}
              {/* HMS Status Indicator */}
              {status?.connected && (() => {
                const knownErrors = status.hms_errors ? filterKnownHMSErrors(status.hms_errors) : [];
                return (
                  <button
                    onClick={() => setShowHMSModal(true)}
                    className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${
                      knownErrors.length > 0
                        ? knownErrors.some(e => e.severity <= 2)
                          ? 'bg-red-500/20 text-red-400'
                          : 'bg-orange-500/20 text-orange-400'
                        : 'bg-bambu-green/20 text-bambu-green'
                    }`}
                    title={t('printers.hmsErrors')}
                  >
                    <AlertTriangle className="w-3 h-3" />
                    {knownErrors.length > 0 ? knownErrors.length : 'OK'}
                  </button>
                );
              })()}
              {/* Maintenance Status Indicator */}
              {maintenanceInfo && (
                <button
                  onClick={() => navigate('/maintenance')}
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${
                    maintenanceInfo.due_count > 0
                      ? 'bg-red-500/20 text-red-400'
                      : maintenanceInfo.warning_count > 0
                      ? 'bg-orange-500/20 text-orange-400'
                      : 'bg-bambu-green/20 text-bambu-green'
                  }`}
                  title={
                    maintenanceInfo.due_count > 0 || maintenanceInfo.warning_count > 0
                      ? `${maintenanceInfo.due_count > 0 ? t('printers.maintenanceDue', { count: maintenanceInfo.due_count }) : ''}${maintenanceInfo.due_count > 0 && maintenanceInfo.warning_count > 0 ? ', ' : ''}${maintenanceInfo.warning_count > 0 ? t('printers.maintenanceDueSoon', { count: maintenanceInfo.warning_count }) : ''} - ${t('printers.clickToView')}`
                      : `${t('printers.maintenanceUpToDate')} - ${t('printers.clickToView')}`
                  }
                >
                  <Wrench className="w-3 h-3" />
                  {maintenanceInfo.due_count > 0 || maintenanceInfo.warning_count > 0
                    ? maintenanceInfo.due_count + maintenanceInfo.warning_count
                    : 'OK'}
                </button>
              )}
              {/* Queue Count Badge */}
              {queueCount > 0 && (
                <button
                  onClick={() => navigate('/queue')}
                  className="flex items-center gap-1 px-2 py-1 rounded-full text-xs bg-purple-500/20 text-purple-400 hover:opacity-80 transition-opacity"
                  title={t('printers.printsInQueue', { count: queueCount })}
                >
                  <Layers className="w-3 h-3" />
                  {queueCount}
                </button>
              )}
              {/* Firmware Update Badge */}
              {firmwareInfo?.update_available && (
                <button
                  onClick={() => setShowFirmwareModal(true)}
                  className="flex items-center gap-1 px-2 py-1 rounded-full text-xs bg-orange-500/20 text-orange-400 hover:opacity-80 transition-opacity"
                  title={t('printers.firmwareUpdateAvailable', { current: firmwareInfo.current_version, latest: firmwareInfo.latest_version })}
                >
                  <Download className="w-3 h-3" />
                  {t('printers.update')}
                </button>
              )}
            </div>
          )}
        </div>

        {/* Delete Confirmation */}
        {showDeleteConfirm && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <Card className="w-full max-w-md mx-4">
              <CardContent>
                <div className="flex items-start gap-3 mb-4">
                  <div className="p-2 rounded-full bg-red-500/20">
                    <AlertTriangle className="w-5 h-5 text-red-400" />
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold text-white">{t('printers.confirm.deleteTitle')}</h3>
                    <p className="text-sm text-bambu-gray mt-1">
                      {t('printers.confirm.deleteMessage', { name: printer.name })}
                    </p>
                  </div>
                </div>

                <div className="bg-bambu-dark rounded-lg p-3 mb-4">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={deleteArchives}
                      onChange={(e) => setDeleteArchives(e.target.checked)}
                      className="mt-0.5 w-4 h-4 rounded border-bambu-gray bg-bambu-dark-secondary text-bambu-green focus:ring-bambu-green focus:ring-offset-0"
                    />
                    <div>
                      <span className="text-sm text-white">{t('printers.confirm.deleteArchives')}</span>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {deleteArchives
                          ? t('printers.confirm.deleteArchivesYes')
                          : t('printers.confirm.deleteArchivesNo')}
                      </p>
                    </div>
                  </label>
                </div>

                <div className="flex justify-end gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setShowDeleteConfirm(false);
                      setDeleteArchives(true);
                    }}
                  >
                    {t('common.cancel')}
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => {
                      deleteMutation.mutate({ deleteArchives });
                      setShowDeleteConfirm(false);
                      setDeleteArchives(true);
                    }}
                  >
                    {t('common.delete')}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Status */}
        {status?.connected && (
          <>
            {/* Compact: Simple status bar */}
            {viewMode === 'compact' ? (
              <div className="mt-2">
                {status.state === 'RUNNING' ? (
                  <div className="flex items-center gap-2">
                    <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-1.5">
                      <div
                        className="bg-bambu-green h-1.5 rounded-full transition-all"
                        style={{ width: `${status.progress || 0}%` }}
                      />
                    </div>
                    <span className="text-xs text-white">{Math.round(status.progress || 0)}%</span>
                  </div>
                ) : (
                  <p className="text-xs text-bambu-gray">{getStatusDisplay(status.state, status.stg_cur_name, t)}</p>
                )}
              </div>
            ) : (
              /* Expanded: Full status section */
              <>
                {/* Current Print or Idle Placeholder */}
                <div className="mb-4 p-3 bg-bambu-dark rounded-lg relative">
                  {/* Skip Objects button - top right corner, always visible */}
                  <button
                    onClick={() => setShowSkipObjectsModal(true)}
                    disabled={!(status.state === 'RUNNING' || status.state === 'PAUSE' || status.state === 'PAUSED') || (status.printable_objects_count ?? 0) < 2}
                    className={`absolute top-2 right-2 p-1.5 rounded transition-colors z-10 ${
                      (status.state === 'RUNNING' || status.state === 'PAUSE' || status.state === 'PAUSED') && (status.printable_objects_count ?? 0) >= 2
                        ? 'text-bambu-gray hover:text-white hover:bg-white/10'
                        : 'text-bambu-gray/30 cursor-not-allowed'
                    }`}
                    title={
                      !(status.state === 'RUNNING' || status.state === 'PAUSE' || status.state === 'PAUSED')
                        ? t('printers.skipObjects.onlyWhilePrinting')
                        : (status.printable_objects_count ?? 0) >= 2
                          ? t('printers.skipObjects.title')
                          : t('printers.skipObjects.requires2Objects')
                    }
                  >
                    <SkipObjectsIcon className="w-4 h-4" />
                    {/* Badge showing skipped count */}
                    {objectsData && objectsData.skipped_count > 0 && (
                      <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 flex items-center justify-center text-[10px] font-bold bg-red-500 text-white rounded-full">
                        {objectsData.skipped_count}
                      </span>
                    )}
                  </button>
                  <div className="flex gap-3">
                    {/* Cover Image */}
                    <CoverImage
                      url={status.state === 'RUNNING' ? status.cover_url : null}
                      printName={status.state === 'RUNNING' ? (status.subtask_name || status.current_print || undefined) : undefined}
                    />
                    {/* Print Info */}
                    <div className="flex-1 min-w-0">
                      {status.current_print && status.state === 'RUNNING' ? (
                        <>
                          <p className="text-sm text-bambu-gray mb-1">{status.stg_cur_name || t('printers.status.printing')}</p>
                          <p className="text-white text-sm mb-2 truncate">
                            {status.subtask_name || status.current_print}
                          </p>
                          <div className="flex items-center justify-between text-sm">
                            <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-2 mr-3">
                              <div
                                className="bg-bambu-green h-2 rounded-full transition-all"
                                style={{ width: `${status.progress || 0}%` }}
                              />
                            </div>
                            <span className="text-white">{Math.round(status.progress || 0)}%</span>
                          </div>
                          <div className="flex items-center gap-3 mt-2 text-xs text-bambu-gray">
                            {status.remaining_time != null && status.remaining_time > 0 && (
                              <>
                                <span className="flex items-center gap-1">
                                  <Clock className="w-3 h-3" />
                                  {formatTime(status.remaining_time * 60)}
                                </span>
                                <span className="text-bambu-green font-medium" title={t('printers.estimatedCompletion')}>
                                  ETA {formatETA(status.remaining_time, timeFormat)}
                                </span>
                              </>
                            )}
                            {status.layer_num != null && status.total_layers != null && status.total_layers > 0 && (
                              <span className="flex items-center gap-1">
                                <Layers className="w-3 h-3" />
                                {status.layer_num}/{status.total_layers}
                              </span>
                            )}
                          </div>
                        </>
                      ) : (
                        <>
                          <p className="text-sm text-bambu-gray mb-1">{t('printers.statusLabel')}</p>
                          <p className="text-white text-sm mb-2">
                            {getStatusDisplay(status.state, status.stg_cur_name, t)}
                          </p>
                          <div className="flex items-center justify-between text-sm">
                            <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-2 mr-3">
                              <div className="bg-bambu-dark-tertiary h-2 rounded-full" />
                            </div>
                            <span className="text-bambu-gray">—</span>
                          </div>
                          {lastPrint ? (
                            <p className="text-xs text-bambu-gray mt-2 truncate" title={lastPrint.print_name || lastPrint.filename}>
                              {t('printers.lastPrint')}: {lastPrint.print_name || lastPrint.filename}
                              {lastPrint.completed_at && (
                                <span className="ml-1 text-bambu-gray/60">
                                  • {formatDateOnly(lastPrint.completed_at, { month: 'short', day: 'numeric' })}
                                </span>
                              )}
                            </p>
                          ) : (
                            <p className="text-xs text-bambu-gray mt-2">{t('printers.readyToPrint')}</p>
                          )}
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {/* Queue Widget - shows next scheduled print */}
                {status.state !== 'RUNNING' && (
                  <PrinterQueueWidget printerId={printer.id} />
                )}
              </>
            )}

            {/* Temperatures */}
            {status.temperatures && viewMode === 'expanded' && (() => {
              // Use actual heater states from MQTT stream
              const nozzleHeating = status.temperatures.nozzle_heating || status.temperatures.nozzle_2_heating || false;
              const bedHeating = status.temperatures.bed_heating || false;
              const chamberHeating = status.temperatures.chamber_heating || false;
              const isDualNozzle = printer.nozzle_count === 2 || status.temperatures.nozzle_2 !== undefined;
              // active_extruder: 0=right, 1=left
              const activeNozzle = status.active_extruder === 1 ? 'L' : 'R';

              return (
                <div className="flex items-center gap-1.5">
                  {/* Nozzle temp - combined for dual nozzle */}
                  <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1">
                    <HeaterThermometer className="w-3.5 h-3.5 mx-auto mb-0.5" color="text-orange-400" isHeating={nozzleHeating} />
                    {status.temperatures.nozzle_2 !== undefined ? (
                      <>
                        <p className="text-[9px] text-bambu-gray">L / R</p>
                        <p className="text-[11px] text-white">
                          {Math.round(status.temperatures.nozzle || 0)}° / {Math.round(status.temperatures.nozzle_2 || 0)}°
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="text-[9px] text-bambu-gray">{t('printers.temps.nozzle')}</p>
                        <p className="text-[11px] text-white">
                          {Math.round(status.temperatures.nozzle || 0)}°C
                        </p>
                      </>
                    )}
                  </div>
                  <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1">
                    <HeaterThermometer className="w-3.5 h-3.5 mx-auto mb-0.5" color="text-blue-400" isHeating={bedHeating} />
                    <p className="text-[9px] text-bambu-gray">{t('printers.temps.bed')}</p>
                    <p className="text-[11px] text-white">
                      {Math.round(status.temperatures.bed || 0)}°C
                    </p>
                  </div>
                  {status.temperatures.chamber !== undefined && (
                    <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1">
                      <HeaterThermometer className="w-3.5 h-3.5 mx-auto mb-0.5" color="text-green-400" isHeating={chamberHeating} />
                      <p className="text-[9px] text-bambu-gray">{t('printers.temps.chamber')}</p>
                      <p className="text-[11px] text-white">
                        {Math.round(status.temperatures.chamber || 0)}°C
                      </p>
                    </div>
                  )}
                  {/* Active nozzle indicator for dual-nozzle printers */}
                  {isDualNozzle && (
                    <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg" title={t('printers.temps.activeNozzle', { side: activeNozzle === 'L' ? t('printers.temps.left') : t('printers.temps.right') })}>
                      <p className={`text-[11px] font-bold ${activeNozzle === 'L' ? 'text-amber-400' : 'text-gray-500'}`}>L</p>
                      <p className="text-[9px] text-bambu-gray">{t('printers.temps.nozzle')}</p>
                      <p className={`text-[11px] font-bold ${activeNozzle === 'R' ? 'text-amber-400' : 'text-gray-500'}`}>R</p>
                    </div>
                  )}
                </div>
              );
            })()}

            {/* Controls - Fans + Print Buttons */}
            {viewMode === 'expanded' && (() => {
              // Determine print state for control buttons
              const isRunning = status.state === 'RUNNING';
              const isPaused = status.state === 'PAUSED' || status.state === 'PAUSE';
              const isPrinting = isRunning || isPaused;
              const isControlBusy = stopPrintMutation.isPending || pausePrintMutation.isPending || resumePrintMutation.isPending;

              // Fan data
              const partFan = status.cooling_fan_speed;
              const auxFan = status.big_fan1_speed;
              const chamberFan = status.big_fan2_speed;

              return (
                <div className="mt-3">
                  {/* Section Header */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('printers.controls')}
                    </span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary/30" />
                  </div>

                  <div className="flex items-center justify-between gap-2">
                    {/* Left: Fan Status - always visible, dynamic coloring */}
                    <div className="flex items-center gap-2">
                      {/* Part Cooling Fan */}
                      <div
                        className={`flex items-center gap-1 px-1.5 py-1 rounded ${partFan && partFan > 0 ? 'bg-cyan-500/10' : 'bg-bambu-dark'}`}
                        title={t('printers.fans.partCooling')}
                      >
                        <Fan className={`w-3.5 h-3.5 ${partFan && partFan > 0 ? 'text-cyan-400' : 'text-bambu-gray/50'}`} />
                        <span className={`text-[10px] ${partFan && partFan > 0 ? 'text-cyan-400' : 'text-bambu-gray/50'}`}>
                          {partFan ?? 0}%
                        </span>
                      </div>

                      {/* Auxiliary Fan */}
                      <div
                        className={`flex items-center gap-1 px-1.5 py-1 rounded ${auxFan && auxFan > 0 ? 'bg-blue-500/10' : 'bg-bambu-dark'}`}
                        title={t('printers.fans.auxiliary')}
                      >
                        <Wind className={`w-3.5 h-3.5 ${auxFan && auxFan > 0 ? 'text-blue-400' : 'text-bambu-gray/50'}`} />
                        <span className={`text-[10px] ${auxFan && auxFan > 0 ? 'text-blue-400' : 'text-bambu-gray/50'}`}>
                          {auxFan ?? 0}%
                        </span>
                      </div>

                      {/* Chamber Fan */}
                      <div
                        className={`flex items-center gap-1 px-1.5 py-1 rounded ${chamberFan && chamberFan > 0 ? 'bg-green-500/10' : 'bg-bambu-dark'}`}
                        title={t('printers.fans.chamber')}
                      >
                        <AirVent className={`w-3.5 h-3.5 ${chamberFan && chamberFan > 0 ? 'text-green-400' : 'text-bambu-gray/50'}`} />
                        <span className={`text-[10px] ${chamberFan && chamberFan > 0 ? 'text-green-400' : 'text-bambu-gray/50'}`}>
                          {chamberFan ?? 0}%
                        </span>
                      </div>
                    </div>

                    {/* Right: Print Control Buttons */}
                    <div className="flex items-center gap-2">
                      {/* Stop button */}
                      <button
                        onClick={() => setShowStopConfirm(true)}
                        disabled={!isPrinting || isControlBusy}
                        className={`
                          flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                          transition-colors
                          ${isPrinting
                            ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                            : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                          }
                        `}
                        title={t('printers.printControl.stopPrint')}
                      >
                        <Square className="w-3 h-3" />
                        {t('printers.printControl.stop')}
                      </button>

                      {/* Pause/Resume button */}
                      <button
                        onClick={() => isPaused ? setShowResumeConfirm(true) : setShowPauseConfirm(true)}
                        disabled={!isPrinting || isControlBusy}
                        className={`
                          flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                          transition-colors
                          ${isPrinting
                            ? isPaused
                              ? 'bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30'
                              : 'bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30'
                            : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                          }
                        `}
                        title={isPaused ? t('printers.printControl.resumePrint') : t('printers.printControl.pausePrint')}
                      >
                        {isPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                        {isPaused ? t('printers.printControl.resume') : t('printers.printControl.pause')}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* AMS Units - 2-Column Grid Layout */}
            {amsData && amsData.length > 0 && viewMode === 'expanded' && (() => {
              // Separate regular AMS (4-tray) from HT AMS (1-tray)
              const regularAms = amsData.filter(ams => ams.tray.length > 1);
              const htAms = amsData.filter(ams => ams.tray.length === 1);
              const isDualNozzle = printer.nozzle_count === 2 || status?.temperatures?.nozzle_2 !== undefined;

              return (
                <div className="mt-3">
                  {/* Section Header */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('printers.filaments')}
                    </span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary/30" />
                  </div>

                  {/* AMS Content */}
                  <div className="space-y-3">
                    {/* Row 1-2: Regular AMS (4-tray) in 2-column grid */}
                    {regularAms.length > 0 && (
                      <div className="grid grid-cols-2 gap-3">
                        {regularAms.map((ams) => {
                        const mappedExtruderId = amsExtruderMap[String(ams.id)];
                        const normalizedId = ams.id >= 128 ? ams.id - 128 : ams.id;
                        const extruderId = mappedExtruderId !== undefined ? mappedExtruderId : normalizedId;
                        const isLeftNozzle = extruderId === 1;
                        const isRightNozzle = extruderId === 0;

                        return (
                          <div key={ams.id} className="p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
                            {/* Header: Label + Stats (no icon) */}
                            <div className="flex items-center justify-between mb-2">
                              <div className="flex items-center gap-1.5">
                                <span className="text-[10px] text-white font-medium">
                                  {getAmsLabel(ams.id, ams.tray.length)}
                                </span>
                                {isDualNozzle && (isLeftNozzle || isRightNozzle) && (
                                  <NozzleBadge side={isLeftNozzle ? 'L' : 'R'} />
                                )}
                              </div>
                              {(ams.humidity != null || ams.temp != null) && (
                                <div className="flex items-center gap-1.5">
                                  {ams.humidity != null && (
                                    <HumidityIndicator
                                      humidity={ams.humidity}
                                      goodThreshold={amsThresholds?.humidityGood}
                                      fairThreshold={amsThresholds?.humidityFair}
                                      onClick={() => setAmsHistoryModal({
                                        amsId: ams.id,
                                        amsLabel: getAmsLabel(ams.id, ams.tray.length),
                                        mode: 'humidity',
                                      })}
                                      compact
                                    />
                                  )}
                                  {ams.temp != null && (
                                    <TemperatureIndicator
                                      temp={ams.temp}
                                      goodThreshold={amsThresholds?.tempGood}
                                      fairThreshold={amsThresholds?.tempFair}
                                      onClick={() => setAmsHistoryModal({
                                        amsId: ams.id,
                                        amsLabel: getAmsLabel(ams.id, ams.tray.length),
                                        mode: 'temperature',
                                      })}
                                      compact
                                    />
                                  )}
                                </div>
                              )}
                            </div>
                            {/* Slots grid: 4 columns - always render 4 slots */}
                            <div className="grid grid-cols-4 gap-1.5">
                              {[0, 1, 2, 3].map((slotIdx) => {
                                // Find tray data for this slot (may be undefined if data incomplete)
                                // Use array index if available, as tray.id may not always be set
                                const tray = ams.tray[slotIdx] || ams.tray.find(t => t.id === slotIdx);
                                const hasFillLevel = tray?.tray_type && tray.remain >= 0;
                                const isEmpty = !tray?.tray_type;
                                // Check if this is the currently loaded tray
                                // Global tray ID = ams.id * 4 + slot index (for standard AMS)
                                const globalTrayId = ams.id * 4 + slotIdx;
                                const isActive = effectiveTrayNow === globalTrayId;
                                // Get cloud preset info if available
                                const cloudInfo = tray?.tray_info_idx ? filamentInfo?.[tray.tray_info_idx] : null;
                                // Get saved slot preset mapping (for user-configured slots)
                                const slotPreset = slotPresets?.[globalTrayId];

                                // Build filament data for hover card
                                const filamentData = tray?.tray_type ? {
                                  vendor: (isBambuLabSpool(tray) ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                                  profile: cloudInfo?.name || slotPreset?.preset_name || tray.tray_sub_brands || tray.tray_type,
                                  colorName: getBambuColorName(tray.tray_id_name) || hexToBasicColorName(tray.tray_color, t),
                                  colorHex: tray.tray_color || null,
                                  kFactor: formatKValue(tray.k),
                                  fillLevel: hasFillLevel ? tray.remain : null,
                                  trayUuid: tray.tray_uuid || null,
                                } : null;

                                // Check if this specific slot is being refreshed
                                const isRefreshing = refreshingSlot?.amsId === ams.id &&
                                  refreshingSlot?.slotId === slotIdx;

                                // Slot visual content (goes inside hover card)
                                const slotVisual = (
                                  <div
                                    className={`bg-bambu-dark-tertiary rounded p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${isActive ? 'ring-2 ring-bambu-green ring-offset-1 ring-offset-bambu-dark' : ''}`}
                                  >
                                    <div
                                      className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2"
                                      style={{
                                        backgroundColor: tray?.tray_color ? `#${tray.tray_color}` : (tray?.tray_type ? '#333' : 'transparent'),
                                        borderColor: isEmpty ? '#666' : 'rgba(255,255,255,0.1)',
                                        borderStyle: isEmpty ? 'dashed' : 'solid',
                                      }}
                                    />
                                    <div className="text-[9px] text-white font-bold truncate">
                                      {tray?.tray_type || '—'}
                                    </div>
                                    {/* Fill bar */}
                                    <div className="mt-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
                                      {hasFillLevel && tray ? (
                                        <div
                                          className="h-full rounded-full transition-all"
                                          style={{
                                            width: `${tray.remain}%`,
                                            backgroundColor: getFillBarColor(tray.remain),
                                          }}
                                        />
                                      ) : tray?.tray_type ? (
                                        <div className="h-full w-full rounded-full bg-white/50 dark:bg-gray-500/40" />
                                      ) : null}
                                    </div>
                                  </div>
                                );

                                // Wrapper with menu button, dropdown, and loading overlay (outside hover card)
                                return (
                                  <div key={slotIdx} className="relative group">
                                    {/* Loading overlay during RFID re-read */}
                                    {isRefreshing && (
                                      <div className="absolute inset-0 bg-bambu-dark-tertiary/80 rounded flex items-center justify-center z-20">
                                        <RefreshCw className="w-4 h-4 text-bambu-green animate-spin" />
                                      </div>
                                    )}
                                    {/* Menu button - appears on hover, hidden when printer busy */}
                                    {status?.state !== 'RUNNING' && (
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
                                    {/* Dropdown menu */}
                                    {status?.state !== 'RUNNING' && amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === slotIdx && (
                                      <div className="absolute top-full left-0 mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
                                        <button
                                          className="w-full px-3 py-1.5 text-left text-xs text-white hover:bg-bambu-dark-tertiary flex items-center gap-2"
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            refreshAmsSlotMutation.mutate({ amsId: ams.id, slotId: slotIdx });
                                            setAmsSlotMenu(null);
                                          }}
                                          disabled={isRefreshing}
                                        >
                                          <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
                                          {t('printers.rereadRfid')}
                                        </button>
                                      </div>
                                    )}
                                    {/* Hover card wraps only the visual content */}
                                    {filamentData ? (
                                      <FilamentHoverCard
                                        data={filamentData}
                                        spoolman={{
                                          enabled: spoolmanEnabled,
                                          hasUnlinkedSpools,
                                          onLinkSpool: spoolmanEnabled && filamentData.trayUuid ? (uuid) => {
                                            setLinkSpoolModal({
                                              trayUuid: uuid,
                                              trayInfo: {
                                                type: filamentData.profile,
                                                color: filamentData.colorHex || '',
                                                location: `${getAmsLabel(ams.id, ams.tray.length)} Slot ${slotIdx + 1}`,
                                              },
                                            });
                                          } : undefined,
                                        }}
                                        configureSlot={{
                                          enabled: true,
                                          onConfigure: () => setConfigureSlotModal({
                                            amsId: ams.id,
                                            trayId: slotIdx,
                                            trayCount: ams.tray.length,
                                            trayType: tray?.tray_type || undefined,
                                            trayColor: tray?.tray_color || undefined,
                                            traySubBrands: tray?.tray_sub_brands || undefined,
                                            trayInfoIdx: tray?.tray_info_idx || undefined,
                                          }),
                                        }}
                                      >
                                        {slotVisual}
                                      </FilamentHoverCard>
                                    ) : (
                                      <EmptySlotHoverCard
                                        configureSlot={{
                                          enabled: true,
                                          onConfigure: () => setConfigureSlotModal({
                                            amsId: ams.id,
                                            trayId: slotIdx,
                                            trayCount: ams.tray.length,
                                          }),
                                        }}
                                      >
                                        {slotVisual}
                                      </EmptySlotHoverCard>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                    {/* Row 3: HT AMS + External spools (same style as regular AMS, 4 across) */}
                    {(htAms.length > 0 || (status.vt_tray && status.vt_tray.tray_type)) && (
                      <div className="grid grid-cols-4 gap-3">
                      {/* HT AMS units - name/badge top, slot left, stats right */}
                      {htAms.map((ams) => {
                        const mappedExtruderId = amsExtruderMap[String(ams.id)];
                        const normalizedId = ams.id >= 128 ? ams.id - 128 : ams.id;
                        const extruderId = mappedExtruderId !== undefined ? mappedExtruderId : normalizedId;
                        const isLeftNozzle = extruderId === 1;
                        const isRightNozzle = extruderId === 0;
                        const tray = ams.tray[0];
                        const hasFillLevel = tray?.tray_type && tray.remain >= 0;
                        const isEmpty = !tray?.tray_type;
                        // Check if this is the currently loaded tray
                        // Global tray ID = ams.id * 4 + tray.id
                        const globalTrayId = ams.id * 4 + (tray?.id ?? 0);
                        const isActive = effectiveTrayNow === globalTrayId;
                        // Get cloud preset info if available
                        const cloudInfo = tray?.tray_info_idx ? filamentInfo?.[tray.tray_info_idx] : null;
                        // Get saved slot preset mapping (for user-configured slots)
                        const slotPreset = slotPresets?.[globalTrayId];

                        // Build filament data for hover card
                        const filamentData = tray?.tray_type ? {
                          vendor: (isBambuLabSpool(tray) ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                          profile: cloudInfo?.name || slotPreset?.preset_name || tray.tray_sub_brands || tray.tray_type,
                          colorName: getBambuColorName(tray.tray_id_name) || hexToBasicColorName(tray.tray_color, t),
                          colorHex: tray.tray_color || null,
                          kFactor: formatKValue(tray.k),
                          fillLevel: hasFillLevel ? tray.remain : null,
                          trayUuid: tray.tray_uuid || null,
                        } : null;

                        const htSlotId = tray?.id ?? 0;
                        // Check if this specific slot is being refreshed
                        const isHtRefreshing = refreshingSlot?.amsId === ams.id &&
                          refreshingSlot?.slotId === htSlotId;

                        // Slot visual content (goes inside hover card)
                        const slotVisual = (
                          <div
                            className={`bg-bambu-dark-tertiary rounded p-1 text-center ${isEmpty ? 'opacity-50' : ''} ${isActive ? 'ring-2 ring-bambu-green ring-offset-1 ring-offset-bambu-dark' : ''}`}
                          >
                            <div
                              className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2"
                              style={{
                                backgroundColor: tray?.tray_color ? `#${tray.tray_color}` : (tray?.tray_type ? '#333' : 'transparent'),
                                borderColor: isEmpty ? '#666' : 'rgba(255,255,255,0.1)',
                                borderStyle: isEmpty ? 'dashed' : 'solid',
                              }}
                            />
                            <div className="text-[9px] text-white font-bold truncate">
                              {tray?.tray_type || '—'}
                            </div>
                            {/* Fill bar */}
                            <div className="mt-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
                              {hasFillLevel ? (
                                <div
                                  className="h-full rounded-full transition-all"
                                  style={{
                                    width: `${tray.remain}%`,
                                    backgroundColor: getFillBarColor(tray.remain),
                                  }}
                                />
                              ) : tray?.tray_type ? (
                                <div className="h-full w-full rounded-full bg-white/50 dark:bg-gray-500/40" />
                              ) : null}
                            </div>
                          </div>
                        );

                        return (
                          <div key={ams.id} className="p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
                            {/* Row 1: Label + Nozzle */}
                            <div className="flex items-center gap-1 mb-2">
                              <span className="text-[10px] text-white font-medium">
                                {getAmsLabel(ams.id, ams.tray.length)}
                              </span>
                              {isDualNozzle && (isLeftNozzle || isRightNozzle) && (
                                <NozzleBadge side={isLeftNozzle ? 'L' : 'R'} />
                              )}
                            </div>
                            {/* Row 2: Slot (left) + Stats (right stacked) */}
                            <div className="flex gap-1.5">
                              {/* Slot wrapper with menu button, dropdown, and loading overlay */}
                              <div className="relative group flex-1">
                                {/* Loading overlay during RFID re-read */}
                                {isHtRefreshing && (
                                  <div className="absolute inset-0 bg-bambu-dark-tertiary/80 rounded flex items-center justify-center z-20">
                                    <RefreshCw className="w-4 h-4 text-bambu-green animate-spin" />
                                  </div>
                                )}
                                {/* Menu button - appears on hover, hidden when printer busy */}
                                {status?.state !== 'RUNNING' && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setAmsSlotMenu(
                                        amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === htSlotId
                                          ? null
                                          : { amsId: ams.id, slotId: htSlotId }
                                      );
                                    }}
                                    className="absolute -top-1 -right-1 w-4 h-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity z-10 hover:bg-bambu-dark-tertiary"
                                    title={t('printers.slotOptions')}
                                  >
                                    <MoreVertical className="w-2.5 h-2.5 text-bambu-gray" />
                                  </button>
                                )}
                                {/* Dropdown menu */}
                                {status?.state !== 'RUNNING' && amsSlotMenu?.amsId === ams.id && amsSlotMenu?.slotId === htSlotId && (
                                  <div className="absolute top-full left-0 mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
                                    <button
                                      className="w-full px-3 py-1.5 text-left text-xs text-white hover:bg-bambu-dark-tertiary flex items-center gap-2"
                                      onClick={(e) => {
                                        e.stopPropagation();
                                        refreshAmsSlotMutation.mutate({ amsId: ams.id, slotId: htSlotId });
                                        setAmsSlotMenu(null);
                                      }}
                                      disabled={isHtRefreshing}
                                    >
                                      <RefreshCw className={`w-3 h-3 ${isHtRefreshing ? 'animate-spin' : ''}`} />
                                      Re-read RFID
                                    </button>
                                  </div>
                                )}
                                {/* Hover card wraps only the visual content */}
                                {filamentData ? (
                                  <FilamentHoverCard
                                    data={filamentData}
                                    spoolman={{
                                      enabled: spoolmanEnabled,
                                      hasUnlinkedSpools,
                                      onLinkSpool: spoolmanEnabled && filamentData.trayUuid ? (uuid) => {
                                        setLinkSpoolModal({
                                          trayUuid: uuid,
                                          trayInfo: {
                                            type: filamentData.profile,
                                            color: filamentData.colorHex || '',
                                            location: getAmsLabel(ams.id, ams.tray.length),
                                          },
                                        });
                                      } : undefined,
                                    }}
                                    configureSlot={{
                                      enabled: true,
                                      onConfigure: () => setConfigureSlotModal({
                                        amsId: ams.id,
                                        trayId: htSlotId,
                                        trayCount: ams.tray.length,
                                        trayType: tray?.tray_type || undefined,
                                        trayColor: tray?.tray_color || undefined,
                                        traySubBrands: tray?.tray_sub_brands || undefined,
                                        trayInfoIdx: tray?.tray_info_idx || undefined,
                                      }),
                                    }}
                                  >
                                    {slotVisual}
                                  </FilamentHoverCard>
                                ) : (
                                  <EmptySlotHoverCard
                                    configureSlot={{
                                      enabled: true,
                                      onConfigure: () => setConfigureSlotModal({
                                        amsId: ams.id,
                                        trayId: htSlotId,
                                        trayCount: ams.tray.length,
                                      }),
                                    }}
                                  >
                                    {slotVisual}
                                  </EmptySlotHoverCard>
                                )}
                              </div>
                              {/* Stats stacked vertically: Temp on top, Humidity below */}
                              {(ams.humidity != null || ams.temp != null) && (
                                <div className="flex flex-col justify-center gap-1 shrink-0">
                                  {ams.temp != null && (
                                    <TemperatureIndicator
                                      temp={ams.temp}
                                      goodThreshold={amsThresholds?.tempGood}
                                      fairThreshold={amsThresholds?.tempFair}
                                      onClick={() => setAmsHistoryModal({
                                        amsId: ams.id,
                                        amsLabel: getAmsLabel(ams.id, ams.tray.length),
                                        mode: 'temperature',
                                      })}
                                      compact
                                    />
                                  )}
                                  {ams.humidity != null && (
                                    <HumidityIndicator
                                      humidity={ams.humidity}
                                      goodThreshold={amsThresholds?.humidityGood}
                                      fairThreshold={amsThresholds?.humidityFair}
                                      onClick={() => setAmsHistoryModal({
                                        amsId: ams.id,
                                        amsLabel: getAmsLabel(ams.id, ams.tray.length),
                                        mode: 'humidity',
                                      })}
                                      compact
                                    />
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                        );
                      })}
                      {/* External spool - name top, slot below (no stats) */}
                      {status.vt_tray && status.vt_tray.tray_type && (() => {
                        const extTray = status.vt_tray;
                        // Check if external spool is active (tray_now = 254)
                        const isExtActive = effectiveTrayNow === 254;
                        // Get cloud preset info if available
                        const extCloudInfo = extTray.tray_info_idx ? filamentInfo?.[extTray.tray_info_idx] : null;
                        // Get saved slot preset mapping (external spool uses amsId=255, trayId=0)
                        const extSlotPreset = slotPresets?.[255 * 4 + 0];

                        // Build filament data for hover card
                        const extFilamentData = {
                          vendor: (isBambuLabSpool(extTray) ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                          profile: extCloudInfo?.name || extSlotPreset?.preset_name || extTray.tray_sub_brands || extTray.tray_type || t('common.unknown'),
                          colorName: getBambuColorName(extTray.tray_id_name) || hexToBasicColorName(extTray.tray_color, t),
                          colorHex: extTray.tray_color || null,
                          kFactor: formatKValue(extTray.k),
                          fillLevel: null, // External spool has unknown fill level
                          trayUuid: extTray.tray_uuid || null,
                        };

                        const extSlotContent = (
                          <div className={`bg-bambu-dark-tertiary rounded p-1 text-center cursor-default ${isExtActive ? 'ring-2 ring-bambu-green ring-offset-1 ring-offset-bambu-dark' : ''}`}>
                            <div
                              className="w-3.5 h-3.5 rounded-full mx-auto mb-0.5 border-2"
                              style={{
                                backgroundColor: extTray.tray_color ? `#${extTray.tray_color}` : '#333',
                                borderColor: isExtActive ? 'var(--accent)' : 'rgba(255,255,255,0.1)',
                              }}
                            />
                            <div className="text-[9px] text-white font-bold truncate">
                              {extTray.tray_type || 'Spool'}
                            </div>
                            {/* Unknown fill level - subtle bar */}
                            <div className="mt-1 h-1.5 bg-black/30 rounded-full overflow-hidden">
                              <div className="h-full w-full rounded-full bg-white/50 dark:bg-gray-500/40" />
                            </div>
                          </div>
                        );

                        return (
                          <div className="p-2.5 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary/30">
                            {/* Row 1: Label */}
                            <div className="flex items-center gap-1 mb-2">
                              <span className="text-[10px] text-white font-medium">{t('printers.external')}</span>
                            </div>
                            {/* Row 2: Slot (full width since no stats) */}
                            <FilamentHoverCard
                              data={extFilamentData}
                              spoolman={{
                                enabled: spoolmanEnabled,
                                hasUnlinkedSpools,
                                onLinkSpool: spoolmanEnabled && extFilamentData.trayUuid ? (uuid) => {
                                  setLinkSpoolModal({
                                    trayUuid: uuid,
                                    trayInfo: {
                                      type: extFilamentData.profile,
                                      color: extFilamentData.colorHex || '',
                                      location: t('printers.externalSpool'),
                                    },
                                  });
                                } : undefined,
                              }}
                              configureSlot={{
                                enabled: true,
                                onConfigure: () => setConfigureSlotModal({
                                  amsId: 255, // External spool indicator
                                  trayId: 0,
                                  trayCount: 1, // External = single slot
                                  trayType: extTray.tray_type || undefined,
                                  trayColor: extTray.tray_color || undefined,
                                  traySubBrands: extTray.tray_sub_brands || undefined,
                                  trayInfoIdx: extTray.tray_info_idx || undefined,
                                }),
                              }}
                            >
                              {extSlotContent}
                            </FilamentHoverCard>
                          </div>
                        );
                      })()}
                      </div>
                    )}
                  </div>
                </div>
              );
            })()}
          </>
        )}

        {/* Smart Plug Controls - hidden in compact mode */}
        {smartPlug && viewMode === 'expanded' && (
          <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
            <div className="flex items-center gap-3">
              {/* Plug name and status */}
              <div className="flex items-center gap-2 min-w-0">
                <Zap className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                <span className="text-sm text-white truncate">{smartPlug.name}</span>
                {plugStatus && (
                  <span
                    className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${
                      plugStatus.state === 'ON'
                        ? 'bg-bambu-green/20 text-bambu-green'
                        : plugStatus.state === 'OFF'
                        ? 'bg-red-500/20 text-red-400'
                        : 'bg-bambu-gray/20 text-bambu-gray'
                    }`}
                  >
                    {plugStatus.state || '?'}
                    {plugStatus.state === 'ON' && plugStatus.energy?.power != null && (
                      <span className="text-yellow-400 ml-1.5">· {plugStatus.energy.power}W</span>
                    )}
                  </span>
                )}
              </div>

              {/* Spacer */}
              <div className="flex-1" />

              {/* Power buttons */}
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setShowPowerOnConfirm(true)}
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'ON'}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                    plugStatus?.state === 'ON'
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <Power className="w-3 h-3" />
                  {t('printers.power.on')}
                </button>
                <button
                  onClick={() => setShowPowerOffConfirm(true)}
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'OFF'}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
                    plugStatus?.state === 'OFF'
                      ? 'bg-red-500/30 text-red-400'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  <PowerOff className="w-3 h-3" />
                  {t('printers.power.off')}
                </button>
              </div>

              {/* Auto-off toggle */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`text-xs hidden sm:inline ${smartPlug.auto_off_executed ? 'text-bambu-green' : 'text-bambu-gray'}`}>
                  {smartPlug.auto_off_executed ? t('printers.power.autoOffDone') : t('printers.power.autoOff')}
                </span>
                <button
                  onClick={() => toggleAutoOffMutation.mutate(!smartPlug.auto_off)}
                  disabled={toggleAutoOffMutation.isPending || smartPlug.auto_off_executed}
                  title={smartPlug.auto_off_executed ? t('printers.power.autoOffExecutedTooltip') : t('printers.power.autoOffTooltip')}
                  className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${
                    smartPlug.auto_off_executed
                      ? 'bg-bambu-green/50 cursor-not-allowed'
                      : smartPlug.auto_off ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                  }`}
                >
                  <span
                    className={`absolute top-[2px] left-[2px] w-4 h-4 bg-white rounded-full transition-transform ${
                      smartPlug.auto_off || smartPlug.auto_off_executed ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Connection Info & Actions - hidden in compact mode */}
        {viewMode === 'expanded' && (
          <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary flex items-center justify-between">
            <div className="text-xs text-bambu-gray">
              <p>{printer.ip_address}</p>
              <p className="truncate">{printer.serial_number}</p>
            </div>
            <div className="flex items-center gap-2">
              {/* Chamber Light Toggle */}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => chamberLightMutation.mutate(!status?.chamber_light)}
                disabled={!status?.connected || chamberLightMutation.isPending}
                title={status?.chamber_light ? t('printers.chamberLight.turnOff') : t('printers.chamberLight.turnOn')}
                className={status?.chamber_light ? 'bg-yellow-500/20 hover:bg-yellow-500/30 border-yellow-500/30' : ''}
              >
                <ChamberLight on={status?.chamber_light ?? false} className="w-4 h-4" />
              </Button>
              {/* Camera Button */}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  if (cameraViewMode === 'embedded' && onOpenEmbeddedCamera) {
                    onOpenEmbeddedCamera(printer.id, printer.name);
                  } else {
                    // Use saved window state or defaults
                    const saved = localStorage.getItem('cameraWindowState');
                    const state = saved ? JSON.parse(saved) : { width: 640, height: 400 };
                    const features = [
                      `width=${state.width}`,
                      `height=${state.height}`,
                      state.left !== undefined ? `left=${state.left}` : '',
                      state.top !== undefined ? `top=${state.top}` : '',
                      'menubar=no,toolbar=no,location=no,status=no,noopener',
                    ].filter(Boolean).join(',');
                    window.open(`/camera/${printer.id}`, `camera-${printer.id}`, features);
                  }
                }}
                disabled={!status?.connected}
                title={cameraViewMode === 'embedded' ? t('printers.camera.openOverlay') : t('printers.camera.openWindow')}
              >
                <Video className="w-4 h-4" />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowFileManager(true)}
                title={t('printers.browseFiles')}
              >
                <HardDrive className="w-4 h-4" />
                {t('printers.files')}
              </Button>
            </div>
          </div>
        )}
      </CardContent>

      {/* File Manager Modal */}
      {showFileManager && (
        <FileManagerModal
          printerId={printer.id}
          printerName={printer.name}
          onClose={() => setShowFileManager(false)}
        />
      )}

      {/* MQTT Debug Modal */}
      {showMQTTDebug && (
        <MQTTDebugModal
          printerId={printer.id}
          printerName={printer.name}
          onClose={() => setShowMQTTDebug(false)}
        />
      )}

      {/* Power On Confirmation */}
      {showPowerOnConfirm && smartPlug && (
        <ConfirmModal
          title={t('printers.confirm.powerOnTitle')}
          message={t('printers.confirm.powerOnMessage', { name: printer.name })}
          confirmText={t('printers.confirm.powerOn')}
          variant="default"
          onConfirm={() => {
            powerControlMutation.mutate('on');
            setShowPowerOnConfirm(false);
          }}
          onCancel={() => setShowPowerOnConfirm(false)}
        />
      )}

      {/* Power Off Confirmation */}
      {showPowerOffConfirm && smartPlug && (
        <ConfirmModal
          title={t('printers.confirm.powerOffTitle')}
          message={
            status?.state === 'RUNNING'
              ? t('printers.confirm.powerOffWarning', { name: printer.name })
              : t('printers.confirm.powerOffMessage', { name: printer.name })
          }
          confirmText={t('printers.confirm.powerOff')}
          variant="danger"
          onConfirm={() => {
            powerControlMutation.mutate('off');
            setShowPowerOffConfirm(false);
          }}
          onCancel={() => setShowPowerOffConfirm(false)}
        />
      )}

      {/* Stop Print Confirmation */}
      {showStopConfirm && (
        <ConfirmModal
          title={t('printers.confirm.stopTitle')}
          message={t('printers.confirm.stopMessage', { name: printer.name })}
          confirmText={t('printers.confirm.stopButton')}
          variant="danger"
          onConfirm={() => {
            stopPrintMutation.mutate();
            setShowStopConfirm(false);
          }}
          onCancel={() => setShowStopConfirm(false)}
        />
      )}

      {/* Pause Print Confirmation */}
      {showPauseConfirm && (
        <ConfirmModal
          title={t('printers.confirm.pauseTitle')}
          message={t('printers.confirm.pauseMessage', { name: printer.name })}
          confirmText={t('printers.confirm.pauseButton')}
          variant="default"
          onConfirm={() => {
            pausePrintMutation.mutate();
            setShowPauseConfirm(false);
          }}
          onCancel={() => setShowPauseConfirm(false)}
        />
      )}

      {/* Resume Print Confirmation */}
      {showResumeConfirm && (
        <ConfirmModal
          title={t('printers.confirm.resumeTitle')}
          message={t('printers.confirm.resumeMessage', { name: printer.name })}
          confirmText={t('printers.confirm.resumeButton')}
          variant="default"
          onConfirm={() => {
            resumePrintMutation.mutate();
            setShowResumeConfirm(false);
          }}
          onCancel={() => setShowResumeConfirm(false)}
        />
      )}

      {/* Skip Objects Popup */}
      {showSkipObjectsModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          onClick={() => setShowSkipObjectsModal(false)}
          onKeyDown={(e) => e.key === 'Escape' && setShowSkipObjectsModal(false)}
          tabIndex={-1}
          ref={(el) => el?.focus()}
        >
          {/* Backdrop */}
          <div className="absolute inset-0 bg-black/50 z-0" />
          {/* Modal */}
          <div
            className="relative z-10 bg-white dark:bg-bambu-dark border border-gray-200 dark:border-bambu-dark-tertiary rounded-xl shadow-2xl w-[560px] max-h-[85vh] flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-bambu-dark-tertiary bg-gray-50 dark:bg-bambu-dark">
            <div className="flex items-center gap-2">
              <SkipObjectsIcon className="w-4 h-4 text-bambu-green" />
              <span className="text-sm font-medium text-gray-900 dark:text-white">{t('printers.skipObjects.title')}</span>
            </div>
            <button
              onClick={() => setShowSkipObjectsModal(false)}
              className="p-1 text-gray-500 dark:text-bambu-gray hover:text-gray-900 dark:hover:text-white rounded transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>

          {!objectsData ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-5 h-5 animate-spin text-bambu-gray" />
            </div>
          ) : objectsData.objects.length === 0 ? (
            <div className="text-center py-8 px-4 text-bambu-gray">
              <p className="text-sm">{t('printers.skipObjects.noObjects')}</p>
              <p className="text-xs mt-1 opacity-70">{t('printers.skipObjects.objectsLoadedOnStart')}</p>
            </div>
          ) : (
            <div className="flex flex-col overflow-hidden">
              {/* Info Banner */}
              <div className="flex items-center gap-3 px-4 py-2.5 bg-blue-50 dark:bg-blue-500/10 border-b border-gray-200 dark:border-bambu-dark-tertiary">
                <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-blue-100 dark:bg-blue-500/20 flex items-center justify-center">
                  <Monitor className="w-4 h-4 text-blue-500 dark:text-blue-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-blue-600 dark:text-blue-300">{t('printers.skipObjects.matchIds')}</p>
                  <p className="text-[10px] text-blue-500/70 dark:text-blue-300/60">{t('printers.skipObjects.printerScreenShows')}</p>
                </div>
                <div className="flex-shrink-0 text-xs text-gray-500 dark:text-bambu-gray">
                  {t('printers.skipObjects.skippedCount', { skipped: objectsData.skipped_count, total: objectsData.total })}
                </div>
              </div>

              {/* Layer Warning */}
              {(status?.layer_num ?? 0) <= 1 && (
                <div className="flex items-center gap-2 px-4 py-2 bg-amber-50 dark:bg-amber-500/10 border-b border-gray-200 dark:border-bambu-dark-tertiary">
                  <AlertCircle className="w-4 h-4 text-amber-500 dark:text-amber-400 flex-shrink-0" />
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    {t('printers.skipObjects.waitForLayer', { layer: status?.layer_num ?? 0 })}
                  </p>
                </div>
              )}

              {/* Content: Image + List side by side */}
              <div className="flex flex-1 overflow-hidden">
                {/* Left: Preview Image with object markers */}
                <div className="w-52 flex-shrink-0 p-4 border-r border-gray-200 dark:border-bambu-dark-tertiary bg-gray-50 dark:bg-bambu-dark-secondary overflow-y-auto">
                  <div className="relative">
                    {status?.cover_url ? (
                      <img
                        src={`${status.cover_url}?view=top`}
                        alt="Print preview"
                        className="w-full aspect-square object-contain rounded-lg bg-gray-900 dark:bg-gray-900 border border-gray-300 dark:border-gray-600"
                      />
                    ) : (
                      <div className="w-full aspect-square rounded-lg bg-gray-100 dark:bg-bambu-dark flex items-center justify-center">
                        <Box className="w-8 h-8 text-gray-300 dark:text-bambu-gray/30" />
                      </div>
                    )}
                    {/* Object ID markers overlay - positioned based on object data */}
                    {objectsData.objects.length > 0 && (
                      <div className="absolute inset-0 pointer-events-none">
                        {objectsData.objects.map((obj, idx) => {
                          let x: number, y: number;

                          // Use position data if available, otherwise fall back to grid
                          if (obj.x != null && obj.y != null && objectsData.bbox_all) {
                            // bbox_all defines the visible area in the top_N.png image
                            // Format: [x_min, y_min, x_max, y_max] in mm
                            const [xMin, yMin, xMax, yMax] = objectsData.bbox_all;
                            const bboxWidth = xMax - xMin;
                            const bboxHeight = yMax - yMin;

                            // The image shows bbox_all area with some padding (~5-10%)
                            const padding = 8;
                            const contentArea = 100 - (padding * 2);

                            // Map object position to image percentage
                            x = padding + ((obj.x - xMin) / bboxWidth) * contentArea;
                            // Y axis: image Y increases downward, but 3D Y increases toward back
                            y = padding + ((yMax - obj.y) / bboxHeight) * contentArea;

                            // Clamp to valid range
                            x = Math.max(5, Math.min(95, x));
                            y = Math.max(5, Math.min(95, y));
                          } else if (obj.x != null && obj.y != null) {
                            // Fallback: use full build plate (256mm)
                            const buildPlate = 256;
                            x = (obj.x / buildPlate) * 100;
                            y = 100 - (obj.y / buildPlate) * 100;
                            x = Math.max(5, Math.min(95, x));
                            y = Math.max(5, Math.min(95, y));
                          } else {
                            // Fallback: arrange in a grid pattern over the build plate area
                            const cols = Math.ceil(Math.sqrt(objectsData.objects.length));
                            const row = Math.floor(idx / cols);
                            const col = idx % cols;
                            const rows = Math.ceil(objectsData.objects.length / cols);
                            x = 15 + (col * (70 / cols)) + (35 / cols);
                            y = 15 + (row * (70 / rows)) + (35 / rows);
                          }

                          return (
                            <div
                              key={obj.id}
                              className={`absolute flex items-center justify-center w-6 h-6 rounded-full text-[10px] font-bold shadow-lg ${
                                obj.skipped
                                  ? 'bg-red-500 text-white line-through'
                                  : 'bg-bambu-green text-black'
                              }`}
                              style={{
                                left: `${x}%`,
                                top: `${y}%`,
                                transform: 'translate(-50%, -50%)'
                              }}
                              title={obj.name}
                            >
                              {obj.id}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {/* Object count overlay */}
                    <div className="absolute bottom-2 right-2 px-2 py-1 bg-white/90 dark:bg-black/80 rounded text-[10px] text-gray-700 dark:text-white shadow-sm">
                      {t('printers.skipObjects.activeCount', { count: objectsData.objects.filter(o => !o.skipped).length })}
                    </div>
                  </div>
                </div>

                {/* Right: Object List with prominent IDs */}
                <div className="flex-1 min-w-0 overflow-y-auto">
                  {objectsData.objects.map((obj) => (
                    <div
                      key={obj.id}
                      className={`
                        flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-bambu-dark-tertiary/50 last:border-0
                        ${obj.skipped ? 'bg-red-50 dark:bg-red-500/10' : 'hover:bg-gray-50 dark:hover:bg-bambu-dark/50'}
                      `}
                    >
                      {/* Large prominent ID badge */}
                      <div className={`
                        w-12 h-12 flex-shrink-0 rounded-lg flex flex-col items-center justify-center
                        ${obj.skipped
                          ? 'bg-red-100 dark:bg-red-500/20 border border-red-300 dark:border-red-500/40'
                          : 'bg-green-100 dark:bg-bambu-green/20 border border-green-300 dark:border-bambu-green/40'}
                      `}>
                        <span className={`text-lg font-mono font-bold ${obj.skipped ? 'text-red-500 dark:text-red-400' : 'text-green-600 dark:text-bambu-green'}`}>
                          {obj.id}
                        </span>
                        <span className={`text-[8px] uppercase tracking-wider ${obj.skipped ? 'text-red-400/60' : 'text-green-500/60 dark:text-bambu-green/60'}`}>
                          ID
                        </span>
                      </div>

                      {/* Object name and status */}
                      <div className="flex-1 min-w-0">
                        <span className={`block text-sm truncate ${obj.skipped ? 'text-red-500 dark:text-red-400 line-through' : 'text-gray-900 dark:text-white'}`}>
                          {obj.name}
                        </span>
                        {obj.skipped && (
                          <span className="text-[10px] text-red-400/60">{t('printers.skipObjects.willBeSkipped')}</span>
                        )}
                      </div>

                      {/* Skip button */}
                      {!obj.skipped ? (
                        <button
                          onClick={() => skipObjectsMutation.mutate([obj.id])}
                          disabled={skipObjectsMutation.isPending || (status?.layer_num ?? 0) <= 1}
                          className={`px-4 py-2 text-xs font-medium rounded-lg transition-colors ${
                            (status?.layer_num ?? 0) <= 1
                              ? 'bg-gray-100 dark:bg-bambu-dark text-gray-400 dark:text-bambu-gray/50 cursor-not-allowed'
                              : 'bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-500/30 border border-red-300 dark:border-red-500/30'
                          }`}
                          title={(status?.layer_num ?? 0) <= 1 ? t('printers.skipObjects.waitForLayer2') : t('printers.skipObjects.skipThisObject')}
                        >
                          {t('printers.skipObjects.skip')}
                        </button>
                      ) : (
                        <span className="px-4 py-2 text-xs text-red-500 dark:text-red-400/70 bg-red-100 dark:bg-red-500/10 rounded-lg">
                          {t('printers.skipObjects.skipped')}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
          </div>
        </div>
      )}

      {/* HMS Error Modal */}
      {showHMSModal && (
        <HMSErrorModal
          printerName={printer.name}
          errors={status?.hms_errors || []}
          onClose={() => setShowHMSModal(false)}
        />
      )}

      {/* AMS History Modal */}
      {amsHistoryModal && (
        <AMSHistoryModal
          isOpen={!!amsHistoryModal}
          onClose={() => setAmsHistoryModal(null)}
          printerId={printer.id}
          printerName={printer.name}
          amsId={amsHistoryModal.amsId}
          amsLabel={amsHistoryModal.amsLabel}
          initialMode={amsHistoryModal.mode}
          thresholds={amsThresholds}
        />
      )}

      {/* Link Spool Modal */}
      {linkSpoolModal && (
        <LinkSpoolModal
          isOpen={!!linkSpoolModal}
          onClose={() => setLinkSpoolModal(null)}
          trayUuid={linkSpoolModal.trayUuid}
          trayInfo={linkSpoolModal.trayInfo}
        />
      )}

      {/* Configure AMS Slot Modal */}
      {configureSlotModal && (
        <ConfigureAmsSlotModal
          isOpen={!!configureSlotModal}
          onClose={() => setConfigureSlotModal(null)}
          printerId={printer.id}
          slotInfo={configureSlotModal}
          onSuccess={() => {
            // Refresh slot presets to show updated profile name
            queryClient.invalidateQueries({ queryKey: ['slotPresets', printer.id] });
            // Printer status will update automatically via WebSocket when AMS data changes
            queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
          }}
        />
      )}

      {/* Edit Printer Modal */}
      {showEditModal && (
        <EditPrinterModal
          printer={printer}
          onClose={() => setShowEditModal(false)}
        />
      )}

      {/* Firmware Update Modal */}
      {showFirmwareModal && firmwareInfo && (
        <FirmwareUpdateModal
          printer={printer}
          firmwareInfo={firmwareInfo}
          onClose={() => setShowFirmwareModal(false)}
        />
      )}

      {/* AMS Slot Menu Backdrop - closes menu when clicking outside */}
      {amsSlotMenu && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setAmsSlotMenu(null)}
        />
      )}
    </Card>
  );
}

function AddPrinterModal({
  onClose,
  onAdd,
  existingSerials,
}: {
  onClose: () => void;
  onAdd: (data: PrinterCreate) => void;
  existingSerials: string[];
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<PrinterCreate>({
    name: '',
    serial_number: '',
    ip_address: '',
    access_code: '',
    model: '',
    auto_archive: true,
  });

  // Discovery state
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredPrinter[]>([]);
  const [discoveryError, setDiscoveryError] = useState('');
  const [hasScanned, setHasScanned] = useState(false);
  const [isDocker, setIsDocker] = useState(false);
  const [subnet, setSubnet] = useState('192.168.1.0/24');
  const [scanProgress, setScanProgress] = useState({ scanned: 0, total: 0 });

  // Fetch discovery info on mount
  useEffect(() => {
    discoveryApi.getInfo().then(info => {
      setIsDocker(info.is_docker);
    }).catch(() => {
      // Ignore errors, assume not Docker
    });
  }, []);

  // Filter out already-added printers
  const newPrinters = discovered.filter(p => !existingSerials.includes(p.serial));

  const startDiscovery = async () => {
    setDiscoveryError('');
    setDiscovered([]);
    setDiscovering(true);
    setHasScanned(false);
    setScanProgress({ scanned: 0, total: 0 });

    try {
      if (isDocker) {
        // Use subnet scanning for Docker
        await discoveryApi.startSubnetScan(subnet);

        // Poll for scan status and results
        const pollInterval = setInterval(async () => {
          try {
            const status = await discoveryApi.getScanStatus();
            setScanProgress({ scanned: status.scanned, total: status.total });

            const printers = await discoveryApi.getDiscoveredPrinters();
            setDiscovered(printers);

            if (!status.running) {
              clearInterval(pollInterval);
              setDiscovering(false);
              setHasScanned(true);
            }
          } catch (e) {
            console.error('Failed to get scan status:', e);
          }
        }, 500);
      } else {
        // Use SSDP discovery for native installs
        await discoveryApi.startDiscovery(10);

        // Poll for discovered printers every second
        const pollInterval = setInterval(async () => {
          try {
            const printers = await discoveryApi.getDiscoveredPrinters();
            setDiscovered(printers);
          } catch (e) {
            console.error('Failed to get discovered printers:', e);
          }
        }, 1000);

        // Stop after 10 seconds
        setTimeout(async () => {
          clearInterval(pollInterval);
          try {
            await discoveryApi.stopDiscovery();
          } catch {
            // Ignore stop errors
          }
          setDiscovering(false);
          setHasScanned(true);
          // Final fetch
          try {
            const printers = await discoveryApi.getDiscoveredPrinters();
            setDiscovered(printers);
          } catch (e) {
            console.error('Failed to get final discovered printers:', e);
          }
        }, 10000);
      }
    } catch (e) {
      console.error('Failed to start discovery:', e);
      setDiscoveryError(e instanceof Error ? e.message : 'Failed to start discovery');
      setDiscovering(false);
      setHasScanned(true);
    }
  };

  // Map SSDP model codes to dropdown values
  const mapModelCode = (ssdpModel: string | null): string => {
    if (!ssdpModel) return '';
    const modelMap: Record<string, string> = {
      // H2 Series
      'O1D': 'H2D',
      'O1C': 'H2C',
      'O1S': 'H2S',
      // X1 Series
      'BL-P001': 'X1C',
      'BL-P002': 'X1',
      'BL-P003': 'X1E',
      // P Series
      'C11': 'P1S',
      'C12': 'P1P',
      'C13': 'P2S',
      // A1 Series
      'N2S': 'A1',
      'N1': 'A1 Mini',
      // Direct matches
      'X1C': 'X1C',
      'X1': 'X1',
      'X1E': 'X1E',
      'P1S': 'P1S',
      'P1P': 'P1P',
      'P2S': 'P2S',
      'A1': 'A1',
      'A1 Mini': 'A1 Mini',
      'H2D': 'H2D',
      'H2C': 'H2C',
      'H2S': 'H2S',
    };
    return modelMap[ssdpModel] || ssdpModel;
  };

  const selectPrinter = (printer: DiscoveredPrinter) => {
    // Don't pre-fill serial if it's a placeholder (unknown-*) - user needs to enter actual serial
    const serialNumber = printer.serial.startsWith('unknown-') ? '' : printer.serial;
    setForm({
      ...form,
      name: printer.name || '',
      serial_number: serialNumber,
      ip_address: printer.ip_address,
      model: mapModelCode(printer.model),
    });
    // Clear discovery results after selection
    setDiscovered([]);
  };

  // Cleanup discovery on unmount
  useEffect(() => {
    return () => {
      discoveryApi.stopDiscovery().catch(() => {});
      discoveryApi.stopSubnetScan().catch(() => {});
    };
  }, []);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <h2 className="text-xl font-semibold mb-4">{t('printers.addPrinter')}</h2>

          {/* Discovery Section */}
          <div className="mb-4 pb-4 border-b border-bambu-dark-tertiary">
            {isDocker && (
              <div className="mb-3">
                <label className="block text-sm text-bambu-gray mb-1">
                  {t('printers.discovery.subnetToScan')}
                </label>
                <input
                  type="text"
                  className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                  value={subnet}
                  onChange={(e) => setSubnet(e.target.value)}
                  placeholder="192.168.1.0/24"
                  disabled={discovering}
                />
                <p className="mt-1 text-xs text-bambu-gray">
                  {t('printers.discovery.dockerSubnetHint')}
                </p>
              </div>
            )}

            <Button
              type="button"
              variant="secondary"
              onClick={startDiscovery}
              disabled={discovering}
              className="w-full"
            >
              {discovering ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {isDocker && scanProgress.total > 0
                    ? t('printers.discovery.scanningProgress', { scanned: scanProgress.scanned, total: scanProgress.total })
                    : t('printers.discovery.scanning')}
                </>
              ) : (
                <>
                  <Search className="w-4 h-4" />
                  {isDocker ? t('printers.discovery.scanSubnet') : t('printers.discovery.discoverNetwork')}
                </>
              )}
            </Button>

            {discoveryError && (
              <div className="mt-2 text-sm text-red-400">{discoveryError}</div>
            )}

            {newPrinters.length > 0 && (
              <div className="mt-3 space-y-2 max-h-40 overflow-y-auto">
                {newPrinters.map((printer) => (
                  <div
                    key={printer.serial}
                    className="flex items-center justify-between p-2 bg-bambu-dark rounded-lg hover:bg-bambu-dark-secondary cursor-pointer transition-colors"
                    onClick={() => selectPrinter(printer)}
                  >
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-white text-sm truncate">
                        {printer.name || printer.serial}
                      </p>
                      <p className="text-xs text-bambu-gray truncate">
                        {mapModelCode(printer.model) || t('common.unknown')} • {printer.ip_address}
                        {printer.serial.startsWith('unknown-') && (
                          <span className="text-yellow-500"> • {t('printers.discovery.serialRequired')}</span>
                        )}
                      </p>
                    </div>
                    <ChevronDown className="w-4 h-4 text-bambu-gray -rotate-90 flex-shrink-0 ml-2" />
                  </div>
                ))}
              </div>
            )}

            {discovering && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                {isDocker ? t('printers.discovery.scanningSubnet') : t('printers.discovery.scanningNetwork')}
              </p>
            )}

            {hasScanned && !discovering && discovered.length === 0 && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                {isDocker ? t('printers.discovery.noPrintersSubnet') : t('printers.discovery.noPrintersNetwork')}
              </p>
            )}

            {hasScanned && !discovering && discovered.length > 0 && newPrinters.length === 0 && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                {t('printers.discovery.allConfigured')}
              </p>
            )}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              onAdd(form);
            }}
            className="space-y-4"
          >
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.name')}</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('printers.form.namePlaceholder')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.ipAddress')}</label>
              <input
                type="text"
                required
                pattern="\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                placeholder="192.168.1.100"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.serialNumber')}</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.serial_number}
                onChange={(e) => setForm({ ...form, serial_number: e.target.value })}
                placeholder="01P00A000000000"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.accessCode')}</label>
              <input
                type="password"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder={t('printers.form.accessCodePlaceholder')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.modelOptional')}</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model || ''}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">{t('printers.form.selectModel')}</option>
                <optgroup label={t('printers.h2Series')}>
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2S">H2S</option>
                </optgroup>
                <optgroup label={t('printers.x1Series')}>
                  <option value="X1E">X1E</option>
                  <option value="X1C">X1 Carbon</option>
                  <option value="X1">X1</option>
                </optgroup>
                <optgroup label={t('printers.pSeries')}>
                  <option value="P2S">P2S</option>
                  <option value="P1S">P1S</option>
                  <option value="P1P">P1P</option>
                </optgroup>
                <optgroup label={t('printers.a1Series')}>
                  <option value="A1">A1</option>
                  <option value="A1 Mini">A1 Mini</option>
                </optgroup>
              </select>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="auto_archive"
                checked={form.auto_archive}
                onChange={(e) => setForm({ ...form, auto_archive: e.target.checked })}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <label htmlFor="auto_archive" className="text-sm text-bambu-gray">
                {t('printers.form.autoArchive')}
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                {t('common.cancel')}
              </Button>
              <Button type="submit" className="flex-1">
                {t('printers.addPrinter')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function FirmwareUpdateModal({
  printer,
  firmwareInfo,
  onClose,
}: {
  printer: Printer;
  firmwareInfo: FirmwareUpdateInfo;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [uploadStatus, setUploadStatus] = useState<FirmwareUploadStatus | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [pollInterval, setPollInterval] = useState<NodeJS.Timeout | null>(null);

  // Prepare check query
  const { data: prepareInfo, isLoading: isPreparing } = useQuery({
    queryKey: ['firmwarePrepare', printer.id],
    queryFn: () => firmwareApi.prepareUpload(printer.id),
    staleTime: 30000,
  });

  // Start upload mutation
  const uploadMutation = useMutation({
    mutationFn: () => firmwareApi.startUpload(printer.id),
    onSuccess: () => {
      setIsUploading(true);
      // Start polling for status
      const interval = setInterval(async () => {
        try {
          const status = await firmwareApi.getUploadStatus(printer.id);
          setUploadStatus(status);
          if (status.status === 'complete' || status.status === 'error') {
            clearInterval(interval);
            setPollInterval(null);
            setIsUploading(false);
            if (status.status === 'complete') {
              showToast(t('printers.firmware.uploadSuccess'), 'success');
              queryClient.invalidateQueries({ queryKey: ['firmwareUpdate', printer.id] });
            }
          }
        } catch {
          // Ignore errors during polling
        }
      }, 2000);
      setPollInterval(interval);
    },
    onError: (error: Error) => {
      showToast(`Failed to start upload: ${error.message}`, 'error');
      setIsUploading(false);
    },
  });

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollInterval) clearInterval(pollInterval);
    };
  }, [pollInterval]);

  const handleStartUpload = () => {
    setUploadStatus(null);
    uploadMutation.mutate();
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-md mx-4">
        <CardContent>
          <div className="flex items-start gap-3 mb-4">
            <div className="p-2 rounded-full bg-orange-500/20">
              <Download className="w-5 h-5 text-orange-400" />
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-white">{t('printers.firmware.title')}</h3>
              <p className="text-sm text-bambu-gray mt-1">
                {printer.name}
              </p>
            </div>
          </div>

          {/* Version Info */}
          <div className="bg-bambu-dark rounded-lg p-3 mb-4">
            <div className="flex justify-between items-center text-sm">
              <span className="text-bambu-gray">{t('printers.firmware.current')}:</span>
              <span className="text-white font-mono">{firmwareInfo.current_version || t('common.unknown')}</span>
            </div>
            <div className="flex justify-between items-center text-sm mt-1">
              <span className="text-bambu-gray">{t('printers.firmware.latest')}:</span>
              <span className="text-orange-400 font-mono">{firmwareInfo.latest_version}</span>
            </div>
            {firmwareInfo.release_notes && (
              <details className="mt-3 text-sm">
                <summary className="text-orange-400 cursor-pointer hover:underline">
                  {t('printers.firmware.releaseNotes')}
                </summary>
                <div className="mt-2 text-bambu-gray text-xs max-h-40 overflow-y-auto whitespace-pre-wrap">
                  {firmwareInfo.release_notes}
                </div>
              </details>
            )}
          </div>

          {/* Status / Progress */}
          {isPreparing ? (
            <div className="flex items-center gap-2 text-bambu-gray text-sm mb-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('printers.firmware.checkingPrerequisites')}
            </div>
          ) : prepareInfo && !isUploading && !uploadStatus ? (
            <div className="mb-4">
              {prepareInfo.can_proceed ? (
                <div className="flex items-center gap-2 text-bambu-green text-sm">
                  <Box className="w-4 h-4" />
                  {t('printers.firmware.sdCardReady')}
                </div>
              ) : (
                <div className="space-y-1">
                  {prepareInfo.errors.map((error, i) => (
                    <div key={i} className="flex items-center gap-2 text-red-400 text-sm">
                      <AlertCircle className="w-4 h-4 flex-shrink-0" />
                      {error}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          {/* Upload Progress */}
          {(isUploading || uploadStatus) && uploadStatus && (
            <div className="mb-4">
              <div className="flex items-center justify-between text-sm mb-1">
                <span className="text-bambu-gray capitalize">{uploadStatus.status}</span>
                <span className="text-white">{uploadStatus.progress}%</span>
              </div>
              <div className="w-full bg-bambu-dark-tertiary rounded-full h-2">
                <div
                  className={`h-2 rounded-full transition-all ${
                    uploadStatus.status === 'error' ? 'bg-red-500' :
                    uploadStatus.status === 'complete' ? 'bg-bambu-green' : 'bg-orange-500'
                  } ${uploadStatus.status === 'uploading' ? 'animate-pulse' : ''}`}
                  style={{ width: `${uploadStatus.progress}%` }}
                />
              </div>
              <p className="text-xs text-bambu-gray mt-1">{uploadStatus.message}</p>
              {uploadStatus.error && (
                <p className="text-xs text-red-400 mt-1">{uploadStatus.error}</p>
              )}
            </div>
          )}

          {/* Success Message */}
          {uploadStatus?.status === 'complete' && (
            <div className="bg-bambu-green/10 border border-bambu-green/30 rounded-lg p-3 mb-4">
              <p className="text-sm text-bambu-green font-medium mb-2">
                {t('printers.firmware.uploadedToSd')}
              </p>
              <p className="text-xs text-bambu-gray">
                {t('printers.firmware.applyInstructions')}
              </p>
              <ol className="text-xs text-bambu-gray mt-1 list-decimal list-inside space-y-1">
                <li>{t('printers.firmware.step1')}</li>
                <li>{t('printers.firmware.step2')}</li>
                <li>{t('printers.firmware.step3')}</li>
                <li>{t('printers.firmware.step4')}</li>
              </ol>
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onClose}>
              {uploadStatus?.status === 'complete' ? t('common.done') : t('common.cancel')}
            </Button>
            {prepareInfo?.can_proceed && !isUploading && uploadStatus?.status !== 'complete' && (
              <Button
                onClick={handleStartUpload}
                disabled={uploadMutation.isPending}
              >
                {uploadMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin mr-2" />
                    {t('printers.firmware.starting')}
                  </>
                ) : (
                  <>
                    <Download className="w-4 h-4 mr-2" />
                    {t('printers.firmware.uploadFirmware')}
                  </>
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function EditPrinterModal({
  printer,
  onClose,
}: {
  printer: Printer;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({
    name: printer.name,
    ip_address: printer.ip_address,
    access_code: '',
    model: printer.model || '',
    location: printer.location || '',
    auto_archive: printer.auto_archive,
  });

  const updateMutation = useMutation({
    mutationFn: (data: Partial<PrinterCreate>) => api.updatePrinter(printer.id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
      onClose();
    },
  });

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const data: Partial<PrinterCreate> = {
      name: form.name,
      ip_address: form.ip_address,
      model: form.model || undefined,
      location: form.location || undefined,
      auto_archive: form.auto_archive,
    };
    // Only include access_code if it was changed
    if (form.access_code) {
      data.access_code = form.access_code;
    }
    updateMutation.mutate(data);
  };

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <Card className="w-full max-w-md" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <h2 className="text-xl font-semibold mb-4">{t('printers.editPrinter')}</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.name')}</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('printers.form.namePlaceholder')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.ipAddress')}</label>
              <input
                type="text"
                required
                pattern="\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                placeholder="192.168.1.100"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.serialNumber')}</label>
              <input
                type="text"
                disabled
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray cursor-not-allowed"
                value={printer.serial_number}
              />
              <p className="text-xs text-bambu-gray mt-1">{t('printers.form.serialCannotChange')}</p>
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.accessCode')}</label>
              <input
                type="password"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder={t('printers.form.accessCodeKeepCurrent')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.model')}</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">{t('printers.form.selectModel')}</option>
                <optgroup label={t('printers.h2Series')}>
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2S">H2S</option>
                </optgroup>
                <optgroup label={t('printers.x1Series')}>
                  <option value="X1E">X1E</option>
                  <option value="X1C">X1 Carbon</option>
                  <option value="X1">X1</option>
                </optgroup>
                <optgroup label={t('printers.pSeries')}>
                  <option value="P2S">P2S</option>
                  <option value="P1S">P1S</option>
                  <option value="P1P">P1P</option>
                </optgroup>
                <optgroup label={t('printers.a1Series')}>
                  <option value="A1">A1</option>
                  <option value="A1 Mini">A1 Mini</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.form.locationGroup')}</label>
              <input
                type="text"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.location}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder={t('printers.form.locationPlaceholder')}
              />
              <p className="text-xs text-bambu-gray mt-1">{t('printers.form.locationHint')}</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                id="edit_auto_archive"
                checked={form.auto_archive}
                onChange={(e) => setForm({ ...form, auto_archive: e.target.checked })}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <label htmlFor="edit_auto_archive" className="text-sm text-bambu-gray">
                {t('printers.form.autoArchive')}
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                {t('common.cancel')}
              </Button>
              <Button type="submit" className="flex-1" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? t('common.saving') : t('common.saveChanges')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

// Component to check if a printer is offline (for power dropdown)
function usePrinterOfflineStatus(printerId: number) {
  const { data: status } = useQuery({
    queryKey: ['printerStatus', printerId],
    queryFn: () => api.getPrinterStatus(printerId),
    refetchInterval: 30000,
  });
  return !status?.connected;
}

// Power dropdown item for an offline printer
function PowerDropdownItem({
  printer,
  plug,
  onPowerOn,
  isPowering,
}: {
  printer: Printer;
  plug: { id: number; name: string };
  onPowerOn: (plugId: number) => void;
  isPowering: boolean;
}) {
  const isOffline = usePrinterOfflineStatus(printer.id);

  // Fetch plug status
  const { data: plugStatus } = useQuery({
    queryKey: ['smartPlugStatus', plug.id],
    queryFn: () => api.getSmartPlugStatus(plug.id),
    refetchInterval: 10000,
  });

  // Only show if printer is offline
  if (!isOffline) {
    return null;
  }

  return (
    <div className="flex items-center justify-between px-3 py-2 hover:bg-gray-100 dark:hover:bg-bambu-dark-tertiary">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm text-gray-900 dark:text-white truncate">{printer.name}</span>
        {plugStatus && (
          <span
            className={`text-xs px-1.5 py-0.5 rounded ${
              plugStatus.state === 'ON'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-red-500/20 text-red-400'
            }`}
          >
            {plugStatus.state || '?'}
          </span>
        )}
      </div>
      <button
        onClick={() => onPowerOn(plug.id)}
        disabled={isPowering || plugStatus?.state === 'ON'}
        className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${
          plugStatus?.state === 'ON'
            ? 'bg-bambu-green/20 text-bambu-green cursor-default'
            : 'bg-bambu-green/20 text-bambu-green hover:bg-bambu-green hover:text-white'
        }`}
      >
        <Power className="w-3 h-3" />
        {isPowering ? '...' : 'On'}
      </button>
    </div>
  );
}

export function PrintersPage() {
  const { t } = useTranslation();
  const [showAddModal, setShowAddModal] = useState(false);
  const [hideDisconnected, setHideDisconnected] = useState(() => {
    return localStorage.getItem('hideDisconnectedPrinters') === 'true';
  });
  const [showPowerDropdown, setShowPowerDropdown] = useState(false);
  const [poweringOn, setPoweringOn] = useState<number | null>(null);
  const [sortBy, setSortBy] = useState<SortOption>(() => {
    return (localStorage.getItem('printerSortBy') as SortOption) || 'name';
  });
  const [sortAsc, setSortAsc] = useState<boolean>(() => {
    return localStorage.getItem('printerSortAsc') !== 'false';
  });
  // Card size: 1=small, 2=medium, 3=large, 4=xl
  const [cardSize, setCardSize] = useState<number>(() => {
    const saved = localStorage.getItem('printerCardSize');
    return saved ? parseInt(saved, 10) : 2; // Default to medium
  });
  // Derive viewMode from cardSize: S=compact, M/L/XL=expanded
  const viewMode: ViewMode = cardSize === 1 ? 'compact' : 'expanded';
  const queryClient = useQueryClient();
  // Embedded camera viewer state - supports multiple simultaneous viewers
  // Persisted to localStorage so cameras reopen after navigation
  const [embeddedCameraPrinters, setEmbeddedCameraPrinters] = useState<Map<number, { id: number; name: string }>>(() => {
    // Initialize from localStorage if camera_view_mode is embedded
    const saved = localStorage.getItem('openEmbeddedCameras');
    if (saved) {
      try {
        const cameras = JSON.parse(saved) as Array<{ id: number; name: string }>;
        return new Map(cameras.map(c => [c.id, c]));
      } catch {
        return new Map();
      }
    }
    return new Map();
  });

  // Persist open cameras to localStorage when they change
  useEffect(() => {
    const cameras = Array.from(embeddedCameraPrinters.values());
    if (cameras.length > 0) {
      localStorage.setItem('openEmbeddedCameras', JSON.stringify(cameras));
    } else {
      localStorage.removeItem('openEmbeddedCameras');
    }
  }, [embeddedCameraPrinters]);

  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch app settings for AMS thresholds
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  // Close embedded cameras if mode changes to 'window'
  useEffect(() => {
    if (settings?.camera_view_mode === 'window' && embeddedCameraPrinters.size > 0) {
      setEmbeddedCameraPrinters(new Map());
    }
  }, [settings?.camera_view_mode, embeddedCameraPrinters.size]);

  // Fetch all smart plugs to know which printers have them
  const { data: smartPlugs } = useQuery({
    queryKey: ['smart-plugs'],
    queryFn: api.getSmartPlugs,
  });

  // Fetch maintenance overview for all printers to show badges
  const { data: maintenanceOverview } = useQuery({
    queryKey: ['maintenanceOverview'],
    queryFn: api.getMaintenanceOverview,
    staleTime: 60 * 1000, // 1 minute
  });

  // Fetch Spoolman status to enable link spool feature
  const { data: spoolmanStatus } = useQuery({
    queryKey: ['spoolman-status'],
    queryFn: api.getSpoolmanStatus,
    staleTime: 60 * 1000, // 1 minute
  });
  const spoolmanEnabled = spoolmanStatus?.enabled && spoolmanStatus?.connected;

  // Fetch unlinked spools to know if link button should be enabled
  const { data: unlinkedSpools } = useQuery({
    queryKey: ['unlinked-spools'],
    queryFn: api.getUnlinkedSpools,
    enabled: !!spoolmanEnabled,
    staleTime: 30 * 1000, // 30 seconds
  });
  const hasUnlinkedSpools = unlinkedSpools && unlinkedSpools.length > 0;

  // Create a map of printer_id -> maintenance info for quick lookup
  const maintenanceByPrinter = maintenanceOverview?.reduce(
    (acc, overview) => {
      acc[overview.printer_id] = {
        due_count: overview.due_count,
        warning_count: overview.warning_count,
        total_print_hours: overview.total_print_hours,
      };
      return acc;
    },
    {} as Record<number, PrinterMaintenanceInfo>
  ) || {};

  // Create a map of printer_id -> smart plug
  const smartPlugByPrinter = smartPlugs?.reduce(
    (acc, plug) => {
      if (plug.printer_id) {
        acc[plug.printer_id] = plug;
      }
      return acc;
    },
    {} as Record<number, typeof smartPlugs[0]>
  ) || {};

  const addMutation = useMutation({
    mutationFn: api.createPrinter,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      queryClient.invalidateQueries({ queryKey: ['maintenanceOverview'] });
      setShowAddModal(false);
    },
  });

  const powerOnMutation = useMutation({
    mutationFn: (plugId: number) => api.controlSmartPlug(plugId, 'on'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['smart-plugs'] });
      setPoweringOn(null);
    },
    onError: () => {
      setPoweringOn(null);
    },
  });

  const toggleHideDisconnected = () => {
    const newValue = !hideDisconnected;
    setHideDisconnected(newValue);
    localStorage.setItem('hideDisconnectedPrinters', String(newValue));
  };

  const handleSortChange = (newSort: SortOption) => {
    setSortBy(newSort);
    localStorage.setItem('printerSortBy', newSort);
  };

  const toggleSortDirection = () => {
    const newAsc = !sortAsc;
    setSortAsc(newAsc);
    localStorage.setItem('printerSortAsc', String(newAsc));
  };

  // Grid classes based on card size (1=small, 2=medium, 3=large, 4=xl)
  const getGridClasses = () => {
    switch (cardSize) {
      case 1: return 'grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5'; // S: many small cards
      case 2: return 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3'; // M: medium cards
      case 3: return 'grid-cols-1 lg:grid-cols-2'; // L: large cards, 2 columns max
      case 4: return 'grid-cols-1'; // XL: single column, full width
      default: return 'grid-cols-1 md:grid-cols-2 xl:grid-cols-3';
    }
  };

  const cardSizeLabels = ['S', 'M', 'L', 'XL'];

  // Sort printers based on selected option
  const sortedPrinters = useMemo(() => {
    if (!printers) return [];
    const sorted = [...printers];

    switch (sortBy) {
      case 'name':
        sorted.sort((a, b) => a.name.localeCompare(b.name));
        break;
      case 'model':
        sorted.sort((a, b) => (a.model || '').localeCompare(b.model || ''));
        break;
      case 'location':
        // Sort by location, with ungrouped printers last
        sorted.sort((a, b) => {
          const locA = a.location || '';
          const locB = b.location || '';
          if (!locA && locB) return 1;
          if (locA && !locB) return -1;
          return locA.localeCompare(locB) || a.name.localeCompare(b.name);
        });
        break;
      case 'status':
        // Sort by status: printing > idle > offline
        sorted.sort((a, b) => {
          const statusA = queryClient.getQueryData<{ connected: boolean; state: string | null }>(['printerStatus', a.id]);
          const statusB = queryClient.getQueryData<{ connected: boolean; state: string | null }>(['printerStatus', b.id]);

          const getPriority = (s: typeof statusA) => {
            if (!s?.connected) return 2; // offline
            if (s.state === 'RUNNING') return 0; // printing
            return 1; // idle
          };

          return getPriority(statusA) - getPriority(statusB);
        });
        break;
    }

    // Apply ascending/descending
    if (!sortAsc) {
      sorted.reverse();
    }

    return sorted;
  }, [printers, sortBy, sortAsc, queryClient]);

  // Group printers by location when sorted by location
  const groupedPrinters = useMemo(() => {
    if (sortBy !== 'location') return null;

    const groups: Record<string, typeof sortedPrinters> = {};
    sortedPrinters.forEach(printer => {
      const location = printer.location || t('printers.ungrouped');
      if (!groups[location]) groups[location] = [];
      groups[location].push(printer);
    });
    return groups;
  }, [sortBy, sortedPrinters]);

  return (
    <div className="p-4 md:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('printers.title')}</h1>
          <StatusSummaryBar printers={printers} />
        </div>
        <div className="flex items-center gap-3">
          {/* Sort dropdown */}
          <div className="flex items-center gap-1">
            <select
              value={sortBy}
              onChange={(e) => handleSortChange(e.target.value as SortOption)}
              className="text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg px-2 py-1.5 text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="name">{t('printers.sort.name')}</option>
              <option value="status">{t('printers.sort.status')}</option>
              <option value="model">{t('printers.sort.model')}</option>
              <option value="location">{t('printers.sort.location')}</option>
            </select>
            <button
              onClick={toggleSortDirection}
              className="p-1.5 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
              title={sortAsc ? t('printers.sort.descending') : t('printers.sort.ascending')}
            >
              {sortAsc ? (
                <ArrowUp className="w-4 h-4 text-bambu-gray" />
              ) : (
                <ArrowDown className="w-4 h-4 text-bambu-gray" />
              )}
            </button>
          </div>

          {/* Card size selector */}
          <div className="flex items-center bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
            {cardSizeLabels.map((label, index) => {
              const size = index + 1;
              const isSelected = cardSize === size;
              return (
                <button
                  key={label}
                  onClick={() => {
                    setCardSize(size);
                    localStorage.setItem('printerCardSize', String(size));
                  }}
                  className={`px-2 py-1.5 text-xs font-medium transition-colors ${
                    index === 0 ? 'rounded-l-lg' : ''
                  } ${
                    index === cardSizeLabels.length - 1 ? 'rounded-r-lg' : ''
                  } ${
                    isSelected
                      ? 'bg-bambu-green text-white'
                      : 'text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white'
                  }`}
                  title={`${label === 'S' ? t('printers.cardSize.small') : label === 'M' ? t('printers.cardSize.medium') : label === 'L' ? t('printers.cardSize.large') : t('printers.cardSize.extraLarge')} ${t('printers.cardSize.cards')}`}
                >
                  {label}
                </button>
              );
            })}
          </div>

          <div className="w-px h-6 bg-bambu-dark-tertiary" />

          <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
            <input
              type="checkbox"
              checked={hideDisconnected}
              onChange={toggleHideDisconnected}
              className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
            />
            {t('printers.hideOffline')}
          </label>
          {/* Power dropdown for offline printers with smart plugs */}
          {hideDisconnected && Object.keys(smartPlugByPrinter).length > 0 && (
            <div className="relative">
              <button
                onClick={() => setShowPowerDropdown(!showPowerDropdown)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-white dark:bg-bambu-dark-secondary border border-gray-200 dark:border-bambu-dark-tertiary rounded-lg text-gray-600 dark:text-bambu-gray hover:text-gray-900 dark:hover:text-white hover:border-bambu-green transition-colors"
              >
                <Power className="w-4 h-4" />
                {t('printers.power.powerOn')}
                <ChevronDown className={`w-3 h-3 transition-transform ${showPowerDropdown ? 'rotate-180' : ''}`} />
              </button>
              {showPowerDropdown && (
                <>
                  {/* Backdrop to close dropdown */}
                  <div
                    className="fixed inset-0 z-10"
                    onClick={() => setShowPowerDropdown(false)}
                  />
                  <div className="absolute right-0 mt-2 w-56 bg-white dark:bg-bambu-dark-secondary border border-gray-200 dark:border-bambu-dark-tertiary rounded-lg shadow-lg z-20 py-1">
                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-bambu-gray border-b border-gray-200 dark:border-bambu-dark-tertiary">
                      {t('printers.power.offlineWithPlugs')}
                    </div>
                    {printers?.filter(p => smartPlugByPrinter[p.id]).map(printer => (
                      <PowerDropdownItem
                        key={printer.id}
                        printer={printer}
                        plug={smartPlugByPrinter[printer.id]}
                        onPowerOn={(plugId) => {
                          setPoweringOn(plugId);
                          powerOnMutation.mutate(plugId);
                        }}
                        isPowering={poweringOn === smartPlugByPrinter[printer.id]?.id}
                      />
                    ))}
                    {printers?.filter(p => smartPlugByPrinter[p.id]).length === 0 && (
                      <div className="px-3 py-2 text-sm text-bambu-gray">
                        {t('printers.power.noPlugs')}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
          <Button onClick={() => setShowAddModal(true)}>
            <Plus className="w-4 h-4" />
            {t('printers.addPrinter')}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">{t('printers.loading')}</div>
      ) : printers?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray mb-4">{t('printers.empty.noPrinters')}</p>
            <Button onClick={() => setShowAddModal(true)}>
              <Plus className="w-4 h-4" />
              {t('printers.empty.addFirst')}
            </Button>
          </CardContent>
        </Card>
      ) : groupedPrinters ? (
        /* Grouped by location view */
        <div className="space-y-6">
          {Object.entries(groupedPrinters).map(([location, locationPrinters]) => (
            <div key={location}>
              <h2 className="text-lg font-semibold text-white mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-bambu-green" />
                {location}
                <span className="text-sm font-normal text-bambu-gray">({locationPrinters.length})</span>
              </h2>
              <div className={`grid gap-4 ${cardSize >= 3 ? 'gap-6' : ''} ${getGridClasses()}`}>
                {locationPrinters.map((printer) => (
                  <PrinterCard
                    key={printer.id}
                    printer={printer}
                    hideIfDisconnected={hideDisconnected}
                    maintenanceInfo={maintenanceByPrinter[printer.id]}
                    viewMode={viewMode}
                    cardSize={cardSize}
                    amsThresholds={settings ? {
                      humidityGood: Number(settings.ams_humidity_good) || 40,
                      humidityFair: Number(settings.ams_humidity_fair) || 60,
                      tempGood: Number(settings.ams_temp_good) || 28,
                      tempFair: Number(settings.ams_temp_fair) || 35,
                    } : undefined}
                    spoolmanEnabled={spoolmanEnabled}
                    hasUnlinkedSpools={hasUnlinkedSpools}
                    timeFormat={settings?.time_format || 'system'}
                    cameraViewMode={settings?.camera_view_mode || 'window'}
                    onOpenEmbeddedCamera={(id, name) => setEmbeddedCameraPrinters(prev => new Map(prev).set(id, { id, name }))}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Regular grid view */
        <div className={`grid gap-4 ${cardSize >= 3 ? 'gap-6' : ''} ${getGridClasses()}`}>
          {sortedPrinters.map((printer) => (
            <PrinterCard
              key={printer.id}
              printer={printer}
              hideIfDisconnected={hideDisconnected}
              maintenanceInfo={maintenanceByPrinter[printer.id]}
              viewMode={viewMode}
              cardSize={cardSize}
              spoolmanEnabled={spoolmanEnabled}
              hasUnlinkedSpools={hasUnlinkedSpools}
              amsThresholds={settings ? {
                humidityGood: Number(settings.ams_humidity_good) || 40,
                humidityFair: Number(settings.ams_humidity_fair) || 60,
                tempGood: Number(settings.ams_temp_good) || 28,
                tempFair: Number(settings.ams_temp_fair) || 35,
              } : undefined}
              timeFormat={settings?.time_format || 'system'}
              cameraViewMode={settings?.camera_view_mode || 'window'}
              onOpenEmbeddedCamera={(id, name) => setEmbeddedCameraPrinters(prev => new Map(prev).set(id, { id, name }))}
            />
          ))}
        </div>
      )}

      {showAddModal && (
        <AddPrinterModal
          onClose={() => setShowAddModal(false)}
          onAdd={(data) => addMutation.mutate(data)}
          existingSerials={printers?.map(p => p.serial_number) || []}
        />
      )}

      {/* Embedded Camera Viewers - multiple viewers can be open simultaneously */}
      {Array.from(embeddedCameraPrinters.values()).map((camera, index) => (
        <EmbeddedCameraViewer
          key={camera.id}
          printerId={camera.id}
          printerName={camera.name}
          viewerIndex={index}
          onClose={() => setEmbeddedCameraPrinters(prev => {
            const next = new Map(prev);
            next.delete(camera.id);
            return next;
          })}
        />
      ))}
    </div>
  );
}
