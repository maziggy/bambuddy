import { useState, useEffect, useMemo, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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
  Terminal,
  Power,
  PowerOff,
  Zap,
  Wrench,
  ChevronDown,
  Pencil,
  ArrowUp,
  ArrowDown,
  LayoutGrid,
  LayoutList,
  Layers,
  Video,
  Search,
  Loader2,
  Square,
  Pause,
  Play,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api, discoveryApi } from '../api/client';
import type { Printer, PrinterCreate, AMSUnit, DiscoveredPrinter } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { FileManagerModal } from '../components/FileManagerModal';
import { MQTTDebugModal } from '../components/MQTTDebugModal';
import { HMSErrorModal, filterKnownHMSErrors } from '../components/HMSErrorModal';
import { PrinterQueueWidget } from '../components/PrinterQueueWidget';
import { AMSHistoryModal } from '../components/AMSHistoryModal';
import { FilamentHoverCard, EmptySlotHoverCard } from '../components/FilamentHoverCard';
import { useToast } from '../contexts/ToastContext';

// Bambu Lab color code mapping (color suffix from tray_id_name -> color name)
// tray_id_name format: "A00-Y2" where Y2 is the color code
const BAMBU_COLOR_CODES: Record<string, string> = {
  // Yellows
  'Y0': 'Yellow',
  'Y1': 'Savana Yellow',
  'Y2': 'Sunflower Yellow',
  'Y3': 'Lemon Yellow',
  // Oranges
  'O0': 'Orange',
  'O1': 'Mandarin Orange',
  'O2': 'Coral Orange',
  // Reds
  'R0': 'Red',
  'R1': 'Scarlet Red',
  'R2': 'Magenta',
  'R3': 'Sakura Pink',
  'R4': 'Raspberry Red',
  // Pinks
  'P0': 'Pink',
  'P1': 'Sakura Pink',
  // Purples
  'V0': 'Purple',
  'V1': 'Violet',
  'V2': 'Lilac Purple',
  // Blues
  'B0': 'Blue',
  'B1': 'Sky Blue',
  'B2': 'Navy Blue',
  'B3': 'Ice Blue',
  'B4': 'Cyan',
  // Greens
  'G0': 'Green',
  'G1': 'Grass Green',
  'G2': 'Lime Green',
  'G3': 'Mint Green',
  'G4': 'Olive Green',
  'G5': 'Jungle Green',
  'G6': 'Bambu Green',
  // Browns
  'N0': 'Brown',
  'N1': 'Peanut Brown',
  'N2': 'Coffee Brown',
  'N3': 'Caramel Brown',
  // Grays
  'A0': 'Gray',
  'A1': 'Charcoal Gray',
  'A2': 'Silver Gray',
  'A3': 'Titan Gray',
  // Blacks
  'K0': 'Black',
  'K1': 'Black',
  // Whites
  'W0': 'White',
  'W1': 'Jade White',
  'W2': 'Ivory White',
  // Special
  'T0': 'Transparent',
  'C0': 'Marble',
  'X0': 'Bronze',
  'X1': 'Gold',
  'X2': 'Silver',
};

// Get color name from Bambu Lab tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
function getBambuColorName(trayIdName: string | null | undefined): string | null {
  if (!trayIdName) return null;
  // Extract color code after the dash (e.g., "A00-Y2" -> "Y2")
  const parts = trayIdName.split('-');
  if (parts.length < 2) return null;
  const colorCode = parts[1];
  return BAMBU_COLOR_CODES[colorCode] || null;
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
  if (h < 15 || h >= 345) return 'Red';
  if (h < 45) return 'Orange';
  if (h < 70) return 'Yellow';
  if (h < 150) return 'Green';
  if (h < 200) return 'Cyan';
  if (h < 260) return 'Blue';
  if (h < 290) return 'Purple';
  if (h < 345) return 'Pink';

  return 'Unknown';
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

function formatETA(remainingMinutes: number): string {
  const now = new Date();
  const eta = new Date(now.getTime() + remainingMinutes * 60 * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const etaDay = new Date(eta);
  etaDay.setHours(0, 0, 0, 0);

  const timeStr = eta.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

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

function getWifiStrength(rssi: number | null | undefined): { label: string; color: string; bars: number } {
  if (rssi == null) return { label: '', color: 'text-bambu-gray', bars: 0 };
  if (rssi >= -50) return { label: 'Excellent', color: 'text-bambu-green', bars: 4 };
  if (rssi >= -60) return { label: 'Good', color: 'text-bambu-green', bars: 3 };
  if (rssi >= -70) return { label: 'Fair', color: 'text-yellow-400', bars: 2 };
  if (rssi >= -80) return { label: 'Weak', color: 'text-orange-400', bars: 1 };
  return { label: 'Very weak', color: 'text-red-400', bars: 1 };
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
            <span className="text-white font-medium">{counts.printing}</span> printing
          </span>
        </div>
      )}
      {counts.idle > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-blue-400" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.idle}</span> idle
          </span>
        </div>
      )}
      {counts.offline > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-gray-400" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.offline}</span> offline
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
function getStatusDisplay(state: string | null | undefined, stg_cur_name: string | null | undefined): string {
  // If we have a specific stage name (calibration, heating, etc.), use it
  if (stg_cur_name) {
    return stg_cur_name;
  }

  // Format the gcode_state nicely
  switch (state) {
    case 'RUNNING':
      return 'Printing';
    case 'PAUSE':
      return 'Paused';
    case 'FINISH':
      return 'Finished';
    case 'FAILED':
      return 'Failed';
    case 'IDLE':
      return 'Idle';
    default:
      return state ? state.charAt(0) + state.slice(1).toLowerCase() : 'Idle';
  }
}

function PrinterCard({
  printer,
  hideIfDisconnected,
  maintenanceInfo,
  viewMode = 'expanded',
  amsThresholds,
}: {
  printer: Printer;
  hideIfDisconnected?: boolean;
  maintenanceInfo?: PrinterMaintenanceInfo;
  viewMode?: ViewMode;
  amsThresholds?: {
    humidityGood: number;
    humidityFair: number;
    tempGood: number;
    tempFair: number;
  };
}) {
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
  const [amsHistoryModal, setAmsHistoryModal] = useState<{
    amsId: number;
    amsLabel: string;
    mode: 'humidity' | 'temperature';
  } | null>(null);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    refetchInterval: 30000, // Fallback polling, WebSocket handles real-time
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
      showToast('Print stopped');
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || 'Failed to stop print', 'error'),
  });

  const pausePrintMutation = useMutation({
    mutationFn: () => api.pausePrint(printer.id),
    onSuccess: () => {
      showToast('Print paused');
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || 'Failed to pause print', 'error'),
  });

  const resumePrintMutation = useMutation({
    mutationFn: () => api.resumePrint(printer.id),
    onSuccess: () => {
      showToast('Print resumed');
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || 'Failed to resume print', 'error'),
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
      showToast(data.message || 'RFID re-read initiated');
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to re-read RFID', 'error');
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

  return (
    <Card className="relative">
      <CardContent>
        {/* Header */}
        <div className={viewMode === 'compact' ? 'mb-2' : 'mb-4'}>
          {/* Top row: Image, Name, Menu */}
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              {/* Printer Model Image */}
              <img
                src={getPrinterImage(printer.model)}
                alt={printer.model || 'Printer'}
                className={`object-contain rounded-lg bg-bambu-dark flex-shrink-0 ${viewMode === 'compact' ? 'w-10 h-10' : 'w-14 h-14'}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className={`font-semibold text-white ${viewMode === 'compact' ? 'text-base truncate' : 'text-lg'}`}>{printer.name}</h3>
                  {/* Connection indicator dot for compact mode */}
                  {viewMode === 'compact' && (
                    <div
                      className={`w-2 h-2 rounded-full flex-shrink-0 ${
                        status?.connected ? 'bg-bambu-green' : 'bg-red-500'
                      }`}
                      title={status?.connected ? 'Connected' : 'Offline'}
                    />
                  )}
                </div>
                <p className="text-sm text-bambu-gray">
                  {printer.model || 'Unknown Model'}
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
                    Edit
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      connectMutation.mutate();
                      setShowMenu(false);
                    }}
                  >
                    <RefreshCw className="w-4 h-4" />
                    Reconnect
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowMQTTDebug(true);
                      setShowMenu(false);
                    }}
                  >
                    <Terminal className="w-4 h-4" />
                    MQTT Debug
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm text-red-400 hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowDeleteConfirm(true);
                      setShowMenu(false);
                    }}
                  >
                    <Trash2 className="w-4 h-4" />
                    Delete
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
                {status?.connected ? 'Connected' : 'Offline'}
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
                  title={`WiFi: ${wifiSignal} dBm - ${getWifiStrength(wifiSignal).label}`}
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
                    title="Click to view HMS errors"
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
                      ? `${maintenanceInfo.due_count > 0 ? `${maintenanceInfo.due_count} maintenance due` : ''}${maintenanceInfo.due_count > 0 && maintenanceInfo.warning_count > 0 ? ', ' : ''}${maintenanceInfo.warning_count > 0 ? `${maintenanceInfo.warning_count} due soon` : ''} - Click to view`
                      : 'All maintenance up to date - Click to view'
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
                  title={`${queueCount} print${queueCount > 1 ? 's' : ''} in queue`}
                >
                  <Layers className="w-3 h-3" />
                  {queueCount}
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
                    <h3 className="text-lg font-semibold text-white">Delete Printer</h3>
                    <p className="text-sm text-bambu-gray mt-1">
                      Are you sure you want to delete "{printer.name}"? This will remove all connection settings.
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
                      <span className="text-sm text-white">Delete print archives</span>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {deleteArchives
                          ? 'All print history for this printer will be permanently deleted.'
                          : 'Print history will be kept but no longer associated with this printer.'}
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
                    Cancel
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => {
                      deleteMutation.mutate({ deleteArchives });
                      setShowDeleteConfirm(false);
                      setDeleteArchives(true);
                    }}
                  >
                    Delete
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
                  <p className="text-xs text-bambu-gray">{getStatusDisplay(status.state, status.stg_cur_name)}</p>
                )}
              </div>
            ) : (
              /* Expanded: Full status section */
              <>
                {/* Current Print or Idle Placeholder */}
                <div className="mb-4 p-3 bg-bambu-dark rounded-lg">
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
                          <p className="text-sm text-bambu-gray mb-1">{status.stg_cur_name || 'Printing'}</p>
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
                                <span className="text-bambu-green font-medium" title="Estimated completion time">
                                  ETA {formatETA(status.remaining_time)}
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
                          <p className="text-sm text-bambu-gray mb-1">Status</p>
                          <p className="text-white text-sm mb-2">
                            {getStatusDisplay(status.state, status.stg_cur_name)}
                          </p>
                          <div className="flex items-center justify-between text-sm">
                            <div className="flex-1 bg-bambu-dark-tertiary rounded-full h-2 mr-3">
                              <div className="bg-bambu-dark-tertiary h-2 rounded-full" />
                            </div>
                            <span className="text-bambu-gray">—</span>
                          </div>
                          {lastPrint ? (
                            <p className="text-xs text-bambu-gray mt-2 truncate" title={lastPrint.print_name || lastPrint.filename}>
                              Last: {lastPrint.print_name || lastPrint.filename}
                              {lastPrint.completed_at && (
                                <span className="ml-1 text-bambu-gray/60">
                                  • {new Date(lastPrint.completed_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                                </span>
                              )}
                            </p>
                          ) : (
                            <p className="text-xs text-bambu-gray mt-2">Ready to print</p>
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

            {/* Temperatures + Print Controls */}
            {status.temperatures && viewMode === 'expanded' && (() => {
              // Use actual heater states from MQTT stream
              const nozzleHeating = status.temperatures.nozzle_heating || status.temperatures.nozzle_2_heating || false;
              const bedHeating = status.temperatures.bed_heating || false;
              const chamberHeating = status.temperatures.chamber_heating || false;

              // Determine print state for control buttons
              const isRunning = status.state === 'RUNNING';
              const isPaused = status.state === 'PAUSED' || status.state === 'PAUSE';
              const isPrinting = isRunning || isPaused;
              const isControlBusy = stopPrintMutation.isPending || pausePrintMutation.isPending || resumePrintMutation.isPending;

              return (
                <div className="flex gap-3">
                  {/* Temperature cards */}
                  <div className="flex-1 grid grid-cols-3 gap-2">
                    {/* Nozzle temp - combined for dual nozzle */}
                    <div className="text-center p-2 bg-bambu-dark rounded-lg">
                      <HeaterThermometer className="w-4 h-4 mx-auto mb-1" color="text-orange-400" isHeating={nozzleHeating} />
                      {status.temperatures.nozzle_2 !== undefined ? (
                        <>
                          <p className="text-[10px] text-bambu-gray">L / R</p>
                          <p className="text-xs text-white">
                            {Math.round(status.temperatures.nozzle || 0)}° / {Math.round(status.temperatures.nozzle_2 || 0)}°
                          </p>
                        </>
                      ) : (
                        <>
                          <p className="text-[10px] text-bambu-gray">Nozzle</p>
                          <p className="text-xs text-white">
                            {Math.round(status.temperatures.nozzle || 0)}°C
                          </p>
                        </>
                      )}
                    </div>
                    <div className="text-center p-2 bg-bambu-dark rounded-lg">
                      <HeaterThermometer className="w-4 h-4 mx-auto mb-1" color="text-blue-400" isHeating={bedHeating} />
                      <p className="text-[10px] text-bambu-gray">Bed</p>
                      <p className="text-xs text-white">
                        {Math.round(status.temperatures.bed || 0)}°C
                      </p>
                    </div>
                    {status.temperatures.chamber !== undefined ? (
                      <div className="text-center p-2 bg-bambu-dark rounded-lg">
                        <HeaterThermometer className="w-4 h-4 mx-auto mb-1" color="text-green-400" isHeating={chamberHeating} />
                        <p className="text-[10px] text-bambu-gray">Chamber</p>
                        <p className="text-xs text-white">
                          {Math.round(status.temperatures.chamber || 0)}°C
                        </p>
                      </div>
                    ) : (
                      <div /> /* Empty placeholder to maintain grid */
                    )}
                  </div>

                  {/* Print control buttons */}
                  <div className="flex flex-col justify-center gap-1.5 w-20">
                    {/* Stop button - visible when printing/paused */}
                    <button
                      onClick={() => setShowStopConfirm(true)}
                      disabled={!isPrinting || isControlBusy}
                      className={`
                        flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium
                        transition-colors
                        ${isPrinting
                          ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                          : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                        }
                      `}
                      title="Stop print"
                    >
                      <Square className="w-3 h-3" />
                      Stop
                    </button>

                    {/* Pause/Resume button */}
                    <button
                      onClick={() => isPaused ? setShowResumeConfirm(true) : setShowPauseConfirm(true)}
                      disabled={!isPrinting || isControlBusy}
                      className={`
                        flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-medium
                        transition-colors
                        ${isPrinting
                          ? isPaused
                            ? 'bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30'
                            : 'bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30'
                          : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                        }
                      `}
                      title={isPaused ? 'Resume print' : 'Pause print'}
                    >
                      {isPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                      {isPaused ? 'Resume' : 'Pause'}
                    </button>
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
                <div className="mt-4 pt-3 border-t border-bambu-dark-tertiary/50">
                  {/* Section Header */}
                  <div className="flex items-center gap-2 mb-3">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      Filaments
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

                                // Build filament data for hover card
                                const filamentData = tray?.tray_type ? {
                                  vendor: (tray.tray_uuid ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                                  profile: cloudInfo?.name || tray.tray_sub_brands || tray.tray_type,
                                  colorName: getBambuColorName(tray.tray_id_name) || hexToBasicColorName(tray.tray_color),
                                  colorHex: tray.tray_color || null,
                                  kFactor: formatKValue(tray.k),
                                  fillLevel: hasFillLevel ? tray.remain : null,
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
                                        title="Slot options"
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
                                          Re-read RFID
                                        </button>
                                      </div>
                                    )}
                                    {/* Hover card wraps only the visual content */}
                                    {filamentData ? (
                                      <FilamentHoverCard data={filamentData}>
                                        {slotVisual}
                                      </FilamentHoverCard>
                                    ) : (
                                      <EmptySlotHoverCard>
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

                        // Build filament data for hover card
                        const filamentData = tray?.tray_type ? {
                          vendor: (tray.tray_uuid ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                          profile: cloudInfo?.name || tray.tray_sub_brands || tray.tray_type,
                          colorName: getBambuColorName(tray.tray_id_name) || hexToBasicColorName(tray.tray_color),
                          colorHex: tray.tray_color || null,
                          kFactor: formatKValue(tray.k),
                          fillLevel: hasFillLevel ? tray.remain : null,
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
                                    title="Slot options"
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
                                  <FilamentHoverCard data={filamentData}>
                                    {slotVisual}
                                  </FilamentHoverCard>
                                ) : (
                                  <EmptySlotHoverCard>
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

                        // Build filament data for hover card
                        const extFilamentData = {
                          vendor: (extTray.tray_uuid ? 'Bambu Lab' : 'Generic') as 'Bambu Lab' | 'Generic',
                          profile: extCloudInfo?.name || extTray.tray_sub_brands || extTray.tray_type || 'Unknown',
                          colorName: getBambuColorName(extTray.tray_id_name) || hexToBasicColorName(extTray.tray_color),
                          colorHex: extTray.tray_color || null,
                          kFactor: formatKValue(extTray.k),
                          fillLevel: null, // External spool has unknown fill level
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
                              <span className="text-[10px] text-white font-medium">External</span>
                            </div>
                            {/* Row 2: Slot (full width since no stats) */}
                            <FilamentHoverCard data={extFilamentData}>
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
                  </span>
                )}
                {/* Power consumption display */}
                {plugStatus?.energy?.power != null && plugStatus.state === 'ON' && (
                  <span className="text-xs text-yellow-400 font-medium flex-shrink-0">
                    {plugStatus.energy.power}W
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
                  On
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
                  Off
                </button>
              </div>

              {/* Auto-off toggle */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={`text-xs hidden sm:inline ${smartPlug.auto_off_executed ? 'text-bambu-green' : 'text-bambu-gray'}`}>
                  {smartPlug.auto_off_executed ? 'Auto-off done' : 'Auto-off'}
                </span>
                <button
                  onClick={() => toggleAutoOffMutation.mutate(!smartPlug.auto_off)}
                  disabled={toggleAutoOffMutation.isPending || smartPlug.auto_off_executed}
                  title={smartPlug.auto_off_executed ? 'Auto-off was executed - turn printer on to reset' : 'Auto power-off after print'}
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
              <Button
                variant="secondary"
                size="sm"
                onClick={() => {
                  // Use saved window state or defaults
                  const saved = localStorage.getItem('cameraWindowState');
                  const state = saved ? JSON.parse(saved) : { width: 640, height: 400 };
                  const features = [
                    `width=${state.width}`,
                    `height=${state.height}`,
                    state.left !== undefined ? `left=${state.left}` : '',
                    state.top !== undefined ? `top=${state.top}` : '',
                    'menubar=no,toolbar=no,location=no,status=no',
                  ].filter(Boolean).join(',');
                  window.open(`/camera/${printer.id}`, `camera-${printer.id}`, features);
                }}
                disabled={!status?.connected}
                title="Open camera in new window"
              >
                <Video className="w-4 h-4" />
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowFileManager(true)}
                title="Browse printer files"
              >
                <HardDrive className="w-4 h-4" />
                Files
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
          title="Power On Printer"
          message={`Are you sure you want to turn ON the power for "${printer.name}"?`}
          confirmText="Power On"
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
          title="Power Off Printer"
          message={
            status?.state === 'RUNNING'
              ? `WARNING: "${printer.name}" is currently printing! Are you sure you want to turn OFF the power? This will interrupt the print and may damage the printer.`
              : `Are you sure you want to turn OFF the power for "${printer.name}"?`
          }
          confirmText="Power Off"
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
          title="Stop Print"
          message={`Are you sure you want to stop the current print on "${printer.name}"? This will cancel the print job.`}
          confirmText="Stop Print"
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
          title="Pause Print"
          message={`Are you sure you want to pause the current print on "${printer.name}"?`}
          confirmText="Pause Print"
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
          title="Resume Print"
          message={`Are you sure you want to resume the print on "${printer.name}"?`}
          confirmText="Resume Print"
          variant="default"
          onConfirm={() => {
            resumePrintMutation.mutate();
            setShowResumeConfirm(false);
          }}
          onCancel={() => setShowResumeConfirm(false)}
        />
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

      {/* Edit Printer Modal */}
      {showEditModal && (
        <EditPrinterModal
          printer={printer}
          onClose={() => setShowEditModal(false)}
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
          } catch (e) {
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
    setForm({
      ...form,
      name: printer.name || '',
      serial_number: printer.serial,
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
          <h2 className="text-xl font-semibold mb-4">Add Printer</h2>

          {/* Discovery Section */}
          <div className="mb-4 pb-4 border-b border-bambu-dark-tertiary">
            {isDocker && (
              <div className="mb-3">
                <label className="block text-sm text-bambu-gray mb-1">
                  Subnet to scan
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
                  Docker detected. Enter your printer's subnet in CIDR notation.
                  Requires <code className="text-bambu-green">network_mode: host</code> in docker-compose.yml.
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
                    ? `Scanning... ${scanProgress.scanned}/${scanProgress.total}`
                    : 'Scanning...'}
                </>
              ) : (
                <>
                  <Search className="w-4 h-4" />
                  {isDocker ? 'Scan Subnet for Printers' : 'Discover Printers on Network'}
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
                        {mapModelCode(printer.model) || 'Unknown'} • {printer.ip_address}
                      </p>
                    </div>
                    <ChevronDown className="w-4 h-4 text-bambu-gray -rotate-90 flex-shrink-0 ml-2" />
                  </div>
                ))}
              </div>
            )}

            {discovering && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                {isDocker ? 'Scanning subnet for Bambu printers...' : 'Scanning network...'}
              </p>
            )}

            {hasScanned && !discovering && discovered.length === 0 && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                No printers found{isDocker ? ' in the specified subnet' : ' on the network'}.
              </p>
            )}

            {hasScanned && !discovering && discovered.length > 0 && newPrinters.length === 0 && (
              <p className="mt-2 text-sm text-bambu-gray text-center">
                All discovered printers are already configured.
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
              <label className="block text-sm text-bambu-gray mb-1">Name</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Printer"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">IP Address</label>
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
              <label className="block text-sm text-bambu-gray mb-1">Serial Number</label>
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
              <label className="block text-sm text-bambu-gray mb-1">Access Code</label>
              <input
                type="password"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder="From printer settings"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Model (optional)</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model || ''}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">Select model...</option>
                <optgroup label="H2 Series">
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2S">H2S</option>
                </optgroup>
                <optgroup label="X1 Series">
                  <option value="X1E">X1E</option>
                  <option value="X1C">X1 Carbon</option>
                  <option value="X1">X1</option>
                </optgroup>
                <optgroup label="P Series">
                  <option value="P2S">P2S</option>
                  <option value="P1S">P1S</option>
                  <option value="P1P">P1P</option>
                </optgroup>
                <optgroup label="A1 Series">
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
                Auto-archive completed prints
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                Cancel
              </Button>
              <Button type="submit" className="flex-1">
                Add Printer
              </Button>
            </div>
          </form>
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
          <h2 className="text-xl font-semibold mb-4">Edit Printer</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Name</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="My Printer"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">IP Address</label>
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
              <label className="block text-sm text-bambu-gray mb-1">Serial Number</label>
              <input
                type="text"
                disabled
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray cursor-not-allowed"
                value={printer.serial_number}
              />
              <p className="text-xs text-bambu-gray mt-1">Serial number cannot be changed</p>
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Access Code</label>
              <input
                type="password"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder="Leave empty to keep current"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Model</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">Select model...</option>
                <optgroup label="H2 Series">
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2S">H2S</option>
                </optgroup>
                <optgroup label="X1 Series">
                  <option value="X1E">X1E</option>
                  <option value="X1C">X1 Carbon</option>
                  <option value="X1">X1</option>
                </optgroup>
                <optgroup label="P Series">
                  <option value="P2S">P2S</option>
                  <option value="P1S">P1S</option>
                  <option value="P1P">P1P</option>
                </optgroup>
                <optgroup label="A1 Series">
                  <option value="A1">A1</option>
                  <option value="A1 Mini">A1 Mini</option>
                </optgroup>
              </select>
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Location / Group</label>
              <input
                type="text"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.location}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder="e.g., Workshop, Office, Basement"
              />
              <p className="text-xs text-bambu-gray mt-1">Used to group printers on the dashboard</p>
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
                Auto-archive completed prints
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                Cancel
              </Button>
              <Button type="submit" className="flex-1" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
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
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    return (localStorage.getItem('printerViewMode') as ViewMode) || 'expanded';
  });
  const queryClient = useQueryClient();

  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch app settings for AMS thresholds
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

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

  const toggleViewMode = () => {
    const newMode = viewMode === 'expanded' ? 'compact' : 'expanded';
    setViewMode(newMode);
    localStorage.setItem('printerViewMode', newMode);
  };

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
      const location = printer.location || 'Ungrouped';
      if (!groups[location]) groups[location] = [];
      groups[location].push(printer);
    });
    return groups;
  }, [sortBy, sortedPrinters]);

  return (
    <div className="p-4 md:p-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Printers</h1>
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
              <option value="name">Name</option>
              <option value="status">Status</option>
              <option value="model">Model</option>
              <option value="location">Location</option>
            </select>
            <button
              onClick={toggleSortDirection}
              className="p-1.5 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
              title={sortAsc ? 'Sort descending' : 'Sort ascending'}
            >
              {sortAsc ? (
                <ArrowUp className="w-4 h-4 text-bambu-gray" />
              ) : (
                <ArrowDown className="w-4 h-4 text-bambu-gray" />
              )}
            </button>
          </div>

          {/* View mode toggle */}
          <button
            onClick={toggleViewMode}
            className="p-1.5 rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
            title={viewMode === 'expanded' ? 'Switch to compact view' : 'Switch to expanded view'}
          >
            {viewMode === 'expanded' ? (
              <LayoutList className="w-5 h-5 text-bambu-gray" />
            ) : (
              <LayoutGrid className="w-5 h-5 text-bambu-gray" />
            )}
          </button>

          <div className="w-px h-6 bg-bambu-dark-tertiary" />

          <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
            <input
              type="checkbox"
              checked={hideDisconnected}
              onChange={toggleHideDisconnected}
              className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
            />
            Hide offline
          </label>
          {/* Power dropdown for offline printers with smart plugs */}
          {hideDisconnected && Object.keys(smartPlugByPrinter).length > 0 && (
            <div className="relative">
              <button
                onClick={() => setShowPowerDropdown(!showPowerDropdown)}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-white dark:bg-bambu-dark-secondary border border-gray-200 dark:border-bambu-dark-tertiary rounded-lg text-gray-600 dark:text-bambu-gray hover:text-gray-900 dark:hover:text-white hover:border-bambu-green transition-colors"
              >
                <Power className="w-4 h-4" />
                Power On
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
                      Offline printers with smart plugs
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
                        No printers with smart plugs
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
          <Button onClick={() => setShowAddModal(true)}>
            <Plus className="w-4 h-4" />
            Add Printer
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">Loading printers...</div>
      ) : printers?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray mb-4">No printers configured yet</p>
            <Button onClick={() => setShowAddModal(true)}>
              <Plus className="w-4 h-4" />
              Add Your First Printer
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
              <div className={`grid gap-4 ${
                viewMode === 'compact'
                  ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
                  : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6'
              }`}>
                {locationPrinters.map((printer) => (
                  <PrinterCard
                    key={printer.id}
                    printer={printer}
                    hideIfDisconnected={hideDisconnected}
                    maintenanceInfo={maintenanceByPrinter[printer.id]}
                    viewMode={viewMode}
                    amsThresholds={settings ? {
                      humidityGood: Number(settings.ams_humidity_good) || 40,
                      humidityFair: Number(settings.ams_humidity_fair) || 60,
                      tempGood: Number(settings.ams_temp_good) || 28,
                      tempFair: Number(settings.ams_temp_fair) || 35,
                    } : undefined}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Regular grid view */
        <div className={`grid gap-4 ${
          viewMode === 'compact'
            ? 'grid-cols-1 md:grid-cols-2 lg:grid-cols-4'
            : 'grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6'
        }`}>
          {sortedPrinters.map((printer) => (
            <PrinterCard
              key={printer.id}
              printer={printer}
              hideIfDisconnected={hideDisconnected}
              maintenanceInfo={maintenanceByPrinter[printer.id]}
              viewMode={viewMode}
              amsThresholds={settings ? {
                humidityGood: Number(settings.ams_humidity_good) || 40,
                humidityFair: Number(settings.ams_humidity_fair) || 60,
                tempGood: Number(settings.ams_temp_good) || 28,
                tempFair: Number(settings.ams_temp_fair) || 35,
              } : undefined}
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
    </div>
  );
}
