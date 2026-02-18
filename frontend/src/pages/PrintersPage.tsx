import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../contexts/AuthContext';
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
  Fan,
  Wind,
  AirVent,
  Download,
  ScanSearch,
  CheckCircle,
  XCircle,
  User,
  Home,
} from 'lucide-react';

import { useNavigate } from 'react-router-dom';
import { api, discoveryApi, firmwareApi } from '../api/client';
import { formatDateOnly, formatETA, formatDuration, parseUTCDate } from '../utils/date';
import type { Printer, PrinterCreate, AMSUnit, DiscoveredPrinter, FirmwareUpdateInfo, FirmwareUploadStatus, LinkedSpoolInfo, SpoolAssignment } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { FileManagerModal } from '../components/FileManagerModal';
import { EmbeddedCameraViewer } from '../components/EmbeddedCameraViewer';
import { MQTTDebugModal } from '../components/MQTTDebugModal';
import { HMSErrorModal, filterKnownHMSErrors } from '../components/HMSErrorModal';
import { PrinterQueueWidget } from '../components/PrinterQueueWidget';
import { AMSHistoryModal } from '../components/AMSHistoryModal';
import { LinkSpoolModal } from '../components/LinkSpoolModal';
import { AssignSpoolModal } from '../components/AssignSpoolModal';
import { ConfigureAmsSlotModal } from '../components/ConfigureAmsSlotModal';
import { AMSUnitCard } from '../components/AMSUnitCard';
import { useToast } from '../contexts/ToastContext';
import { ChamberLight } from '../components/icons/ChamberLight';
import { SkipObjectsModal, SkipObjectsIcon } from '../components/SkipObjectsModal';
import React from 'react';
// Parse RGBA hex to CSS color (skip if empty or all zeros)
function parseFilamentColor(rgba: string): string | null {
  if (!rgba || rgba === '00000000' || rgba.length < 6) return null;
  const r = rgba.slice(0, 2);
  const g = rgba.slice(2, 4);
  const b = rgba.slice(4, 6);
  const a = rgba.length >= 8 ? parseInt(rgba.slice(6, 8), 16) / 255 : 1;
  if (a === 0) return null;
  return `rgba(${parseInt(r, 16)}, ${parseInt(g, 16)}, ${parseInt(b, 16)}, ${a})`;
}

function isLightFilamentColor(rgba: string): boolean {
  if (!rgba || rgba.length < 6) return false;
  const r = parseInt(rgba.slice(0, 2), 16);
  const g = parseInt(rgba.slice(2, 4), 16);
  const b = parseInt(rgba.slice(4, 6), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6;
}

// Expand nozzle type codes to material names
// Handles full text ("hardened_steel"), 2-char codes ("HS"/"HH"), and 4-char codes ("HS01")
// Material mapping: 00=stainless steel, 01=hardened steel, 05=tungsten carbide
function nozzleTypeName(type: string, t: (key: string) => string): string {
  if (!type) return '';
  // Full text names (from main nozzle info)
  if (type.includes('hardened')) return t('printers.nozzleHardenedSteel');
  if (type.includes('stainless')) return t('printers.nozzleStainlessSteel');
  if (type.includes('tungsten')) return t('printers.nozzleTungstenCarbide');
  // 4-char codes (e.g. "HS01"): last 2 digits = material
  if (type.length >= 4) {
    const material = type.slice(2, 4);
    if (material === '00') return t('printers.nozzleStainlessSteel');
    if (material === '01') return t('printers.nozzleHardenedSteel');
    if (material === '05') return t('printers.nozzleTungstenCarbide');
  }
  // 2-digit numeric codes
  if (type === '00') return t('printers.nozzleStainlessSteel');
  if (type === '01') return t('printers.nozzleHardenedSteel');
  if (type === '05') return t('printers.nozzleTungstenCarbide');
  // 2-char alpha codes: H prefix = hardened steel
  if (type.startsWith('H')) return t('printers.nozzleHardenedSteel');
  return type;
}

// Parse flow type from nozzle type code
// HH = high flow, HS = standard/normal
function nozzleFlowName(type: string, t: (key: string) => string): string {
  if (!type) return '';
  if (type.startsWith('HH')) return t('printers.nozzleHighFlow');
  if (type.startsWith('HS')) return t('printers.nozzleStandardFlow');
  return '';
}

// Per-slot hover card for nozzle rack
// activeStatus: when true, show "Active" instead of "Mounted"/"Docked" (for hotend nozzles)
function NozzleSlotHoverCard({ slot, index, activeStatus, filamentName, children }: {
  slot: import('../api/client').NozzleRackSlot;
  index: number;
  activeStatus?: boolean;
  filamentName?: string;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isEmpty = !slot.nozzle_diameter && !slot.nozzle_type;
  const isMounted = slot.stat === 1;

  useEffect(() => {
    if (isVisible && triggerRef.current && cardRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      const headerHeight = 56;
      const spaceAbove = triggerRect.top - headerHeight;
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      if (spaceAbove < cardHeight + 12 && spaceBelow > spaceAbove) {
        setPosition('bottom');
      } else {
        setPosition('top');
      }
    }
  }, [isVisible]);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };

  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(false), 100);
  };

  useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current);
    };
  }, []);

  const filamentCss = parseFilamentColor(slot.filament_color);
  const typeFull = nozzleTypeName(slot.nozzle_type, t);
  const flowFull = nozzleFlowName(slot.nozzle_type, t);

  return (
    <div
      ref={triggerRef}
      className="relative"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {isVisible && (
        <div
          ref={cardRef}
          className={`
            absolute left-1/2 -translate-x-1/2 z-50
            ${position === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            animate-in fade-in-0 zoom-in-95 duration-150
          `}
          style={{ maxWidth: 'calc(100vw - 24px)' }}
        >
          <div className="w-44 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl overflow-hidden backdrop-blur-sm">
            {isEmpty ? (
              <div className="px-3 py-2 text-xs text-bambu-gray text-center whitespace-nowrap">
                Slot {index + 1} — Empty
              </div>
            ) : (
              <div className="p-2.5 space-y-1.5">
                {/* Diameter */}
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleDiameter')}</span>
                  <span className="text-xs text-white font-semibold">{slot.nozzle_diameter} mm</span>
                </div>

                {/* Type */}
                {typeFull && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleType')}</span>
                    <span className="text-xs text-white font-semibold truncate max-w-[100px]">{typeFull}</span>
                  </div>
                )}

                {/* Flow (hide if empty) */}
                {flowFull && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleFlow')}</span>
                    <span className="text-xs text-white font-semibold">{flowFull}</span>
                  </div>
                )}

                {/* Status badge */}
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleStatus')}</span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${activeStatus || isMounted
                    ? 'bg-green-900/50 text-green-400'
                    : 'bg-bambu-dark-tertiary text-bambu-gray'
                    }`}>
                    {activeStatus ? t('printers.nozzleActive') : isMounted ? t('printers.nozzleMounted') : t('printers.nozzleDocked')}
                  </span>
                </div>

                {/* Wear (hide if null) */}
                {slot.wear != null && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleWear')}</span>
                    <span className="text-xs text-white font-semibold">{slot.wear}%</span>
                  </div>
                )}

                {/* Max Temp (hide if 0) */}
                {slot.max_temp > 0 && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleMaxTemp')}</span>
                    <span className="text-xs text-white font-semibold">{slot.max_temp}°C</span>
                  </div>
                )}

                {/* Serial (hide if empty) */}
                {slot.serial_number && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleSerial')}</span>
                    <span className="text-[10px] text-white font-mono truncate max-w-[80px]">{slot.serial_number}</span>
                  </div>
                )}

                {/* Filament: material type + color swatch (hide if no color) */}
                {(filamentCss || slot.filament_type) && (
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">{t('printers.nozzleFilament')}</span>
                    <div className="flex items-center gap-1">
                      {filamentCss && (
                        <div className="w-3 h-3 rounded-sm border border-white/20" style={{ backgroundColor: filamentCss }} />
                      )}
                      <span className="text-[10px] text-white font-semibold truncate max-w-[100px]">{filamentName || slot.filament_type || slot.filament_id || ''}</span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Arrow pointer */}
          <div
            className={`
              absolute left-1/2 -translate-x-1/2 w-0 h-0
              border-l-[6px] border-l-transparent
              border-r-[6px] border-r-transparent
              ${position === 'top'
                ? 'top-full border-t-[6px] border-t-bambu-dark-tertiary'
                : 'bottom-full border-b-[6px] border-b-bambu-dark-tertiary'}
            `}
          />
        </div>
      )}
    </div>
  );
}

// Dual-nozzle hover card showing L and R nozzle details side by side
function DualNozzleHoverCard({ leftSlot, rightSlot, activeNozzle, filamentInfo, children }: {
  leftSlot?: import('../api/client').NozzleRackSlot;
  rightSlot?: import('../api/client').NozzleRackSlot;
  activeNozzle: 'L' | 'R';
  filamentInfo?: Record<string, { name: string; k: number | null }>;
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [isVisible, setIsVisible] = useState(false);
  const [position, setPosition] = useState<'top' | 'bottom'>('top');
  const triggerRef = useRef<HTMLDivElement>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (isVisible && triggerRef.current && cardRef.current) {
      const triggerRect = triggerRef.current.getBoundingClientRect();
      const cardHeight = cardRef.current.offsetHeight;
      const headerHeight = 56;
      const spaceAbove = triggerRect.top - headerHeight;
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      if (spaceAbove < cardHeight + 12 && spaceBelow > spaceAbove) {
        setPosition('bottom');
      } else {
        setPosition('top');
      }
    }
  }, [isVisible]);

  const handleMouseEnter = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(true), 80);
  };

  const handleMouseLeave = () => {
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setIsVisible(false), 100);
  };

  useEffect(() => {
    return () => { if (timeoutRef.current) clearTimeout(timeoutRef.current); };
  }, []);

  if (!leftSlot && !rightSlot) return <>{children}</>;

  const renderColumn = (slot: import('../api/client').NozzleRackSlot, side: 'L' | 'R') => {
    const isActive = activeNozzle === side;
    const typeFull = nozzleTypeName(slot.nozzle_type, t);
    const flowFull = nozzleFlowName(slot.nozzle_type, t);
    const filamentCss = parseFilamentColor(slot.filament_color);
    const filamentName = slot.filament_id ? filamentInfo?.[slot.filament_id]?.name : undefined;
    return (
      <div className="flex-1 space-y-1.5">
        <div className={`text-[10px] font-bold pb-1 border-b border-bambu-dark-tertiary/50 ${isActive ? 'text-amber-400' : 'text-bambu-gray'}`}>
          {side === 'L' ? t('common.left') : t('common.right')}
        </div>
        {slot.nozzle_diameter && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleDiameter')}</span>
            <span className="text-xs text-white font-semibold">{slot.nozzle_diameter} mm</span>
          </div>
        )}
        {typeFull && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleType')}</span>
            <span className="text-[10px] text-white font-semibold">{typeFull}</span>
          </div>
        )}
        {flowFull && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleFlow')}</span>
            <span className="text-[10px] text-white font-semibold">{flowFull}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-bambu-gray">{t('printers.nozzleStatus')}</span>
          <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${isActive
            ? 'bg-green-900/50 text-green-400'
            : 'bg-bambu-dark-tertiary text-bambu-gray'
            }`}>
            {isActive ? t('printers.nozzleActive') : t('printers.nozzleIdle')}
          </span>
        </div>
        {slot.wear != null && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleWear')}</span>
            <span className="text-xs text-white font-semibold">{slot.wear}%</span>
          </div>
        )}
        {/* Serial and max temp only available on the right (removable) nozzle */}
        {side === 'R' && slot.max_temp > 0 && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleMaxTemp')}</span>
            <span className="text-xs text-white font-semibold">{slot.max_temp}°C</span>
          </div>
        )}
        {side === 'R' && slot.serial_number && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleSerial')}</span>
            <span className="text-[10px] text-white font-mono">{slot.serial_number}</span>
          </div>
        )}
        {(filamentCss || slot.filament_type || slot.filament_id) && (
          <div className="flex items-center justify-between">
            <span className="text-[10px] text-bambu-gray">{t('printers.nozzleFilament')}</span>
            <div className="flex items-center gap-1">
              {filamentCss && (
                <div className="w-3 h-3 rounded-sm border border-white/20" style={{ backgroundColor: filamentCss }} />
              )}
              <span className="text-[10px] text-white font-semibold truncate max-w-[100px]">
                {filamentName || slot.filament_type || slot.filament_id || ''}
              </span>
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      ref={triggerRef}
      className="relative flex-1"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {children}

      {isVisible && (
        <div
          ref={cardRef}
          className={`
            absolute left-1/2 -translate-x-1/2 z-50
            ${position === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'}
            animate-in fade-in-0 zoom-in-95 duration-150
          `}
          style={{ maxWidth: 'calc(100vw - 24px)' }}
        >
          <div className="w-96 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl overflow-hidden backdrop-blur-sm">
            <div className="p-2.5 flex gap-3">
              {leftSlot && renderColumn(leftSlot, 'L')}
              {leftSlot && rightSlot && <div className="w-px bg-bambu-dark-tertiary/50" />}
              {rightSlot && renderColumn(rightSlot, 'R')}
            </div>
          </div>

          {/* Arrow pointer */}
          <div
            className={`
              absolute left-1/2 -translate-x-1/2 w-0 h-0
              border-l-[6px] border-l-transparent
              border-r-[6px] border-r-transparent
              ${position === 'top'
                ? 'top-full border-t-[6px] border-t-bambu-dark-tertiary'
                : 'bottom-full border-b-[6px] border-b-bambu-dark-tertiary'}
            `}
          />
        </div>
      )}
    </div>
  );
}

// H2C Nozzle Rack Card — compact single row showing 6-position tool-changer dock
function NozzleRackCard({ slots, filamentInfo }: { slots: import('../api/client').NozzleRackSlot[]; filamentInfo?: Record<string, { name: string; k: number | null }> }) {
  const { t } = useTranslation();
  // Rack nozzles only (IDs >= 2) — excludes L/R hotend nozzles (IDs 0, 1)
  // H2C rack IDs are 16-21 — map by actual ID so empty slots appear in the correct position
  const rackNozzles = slots.filter(s => s.id >= 2);
  const RACK_SIZE = 6;
  const minRackId = rackNozzles.length > 0 ? Math.min(...rackNozzles.map(s => s.id)) : 16;
  const rackSlots: (import('../api/client').NozzleRackSlot)[] = Array.from(
    { length: RACK_SIZE },
    (_, i) => rackNozzles.find(s => s.id === minRackId + i) ?? {
      id: -(i + 1), nozzle_type: '', nozzle_diameter: '', wear: null, stat: null,
      max_temp: 0, serial_number: '', filament_color: '', filament_id: '', filament_type: '',
    },
  );

  return (
    <div className="text-center px-2.5 py-1.5 bg-bambu-dark rounded-lg flex-[2_1_190px] flex flex-col justify-center">
      <p className="text-[9px] text-bambu-gray mb-1">{t('printers.nozzleRack')}</p>
      <div className="flex gap-[3px] justify-center">
        {rackSlots.map((slot, i) => {
          const isEmpty = !slot.nozzle_diameter && !slot.nozzle_type;
          const filamentBg = !isEmpty ? parseFilamentColor(slot.filament_color) : null;
          const lightBg = filamentBg ? isLightFilamentColor(slot.filament_color) : false;

          return (
            <NozzleSlotHoverCard key={slot.id >= 0 ? slot.id : `empty-${i}`} slot={slot} index={i} filamentName={slot.filament_id ? filamentInfo?.[slot.filament_id]?.name : undefined}>
              <div
                className={`w-7 h-7 rounded flex items-center justify-center cursor-default transition-colors border-b-2 ${isEmpty
                  ? 'bg-bambu-dark-tertiary/20 border-bambu-dark-tertiary/20'
                  : 'bg-bambu-dark-tertiary/40 border-bambu-dark-tertiary/40'
                  }`}
                style={filamentBg ? { backgroundColor: filamentBg } : undefined}
              >
                <span className={`text-[10px] font-semibold ${isEmpty ? 'text-bambu-gray/30' : lightBg ? 'text-black/80' : 'text-white'}`}
                  style={filamentBg && !lightBg ? { textShadow: '0 1px 3px rgba(0,0,0,0.9)' } : undefined}
                >
                  {isEmpty ? '—' : (slot.nozzle_diameter || '?')}
                </span>
              </div>
            </NozzleSlotHoverCard>
          );
        })}
      </div>
    </div>
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
        <rect x="4.5" y="3" width="3" height="9.5" fill={fillColor} rx="0.5" />
        <circle cx="6" cy="15" r="2" fill={fillColor} />
        <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke={fillColor} strokeWidth="1" fill="none" />
      </svg>
    );
  }

  // Empty thermometer - heater is OFF
  return (
    <svg className={className} viewBox="0 0 12 20" fill="none" xmlns="http://www.w3.org/2000/svg">
      <path d="M6 0.5C4.6 0.5 3.5 1.6 3.5 3V12.1C2.6 12.8 2 13.9 2 15C2 17.2 3.8 19 6 19C8.2 19 10 17.2 10 15C10 13.9 9.4 12.8 8.5 12.1V3C8.5 1.6 7.4 0.5 6 0.5Z" stroke={fillColor} strokeWidth="1" fill="none" />
      <circle cx="6" cy="15" r="2.5" stroke={fillColor} strokeWidth="1" fill="none" />
    </svg>
  );
}


function getPrinterImage(model: string | null | undefined): string {
  if (!model) return '/img/printers/default.png';

  const modelLower = model.toLowerCase().replace(/\s+/g, '');

  // Map model names to image files
  if (modelLower.includes('x1e')) return '/img/printers/x1e.png';
  if (modelLower.includes('x1c') || modelLower.includes('x1carbon')) return '/img/printers/x1c.png';
  if (modelLower.includes('x1')) return '/img/printers/x1c.png';
  if (modelLower.includes('h2dpro') || modelLower.includes('h2d-pro')) return '/img/printers/h2dpro.png';
  if (modelLower.includes('h2d')) return '/img/printers/h2d.png';
  if (modelLower.includes('h2c')) return '/img/printers/h2c.png';
  if (modelLower.includes('h2s')) return '/img/printers/h2d.png';
  if (modelLower.includes('p2s')) return '/img/printers/p1s.png';
  if (modelLower.includes('p1s')) return '/img/printers/p1s.png';
  if (modelLower.includes('p1p')) return '/img/printers/p1p.png';
  if (modelLower.includes('a1mini')) return '/img/printers/a1mini.png';
  if (modelLower.includes('a1')) return '/img/printers/a1.png';

  return '/img/printers/default.png';
}

function getWifiStrength(rssi: number): { labelKey: string; color: string; bars: number } {
  if (rssi >= -50) return { labelKey: 'printers.wifiSignal.excellent', color: 'text-bambu-green', bars: 4 };
  if (rssi >= -60) return { labelKey: 'printers.wifiSignal.good', color: 'text-bambu-green', bars: 3 };
  if (rssi >= -70) return { labelKey: 'printers.wifiSignal.fair', color: 'text-yellow-400', bars: 2 };
  if (rssi >= -80) return { labelKey: 'printers.wifiSignal.weak', color: 'text-orange-400', bars: 1 };
  return { labelKey: 'printers.wifiSignal.veryWeak', color: 'text-red-400', bars: 1 };
}

function CoverImage({ url, printName }: { url: string | null; printName?: string }) {
  const { t } = useTranslation();
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);
  const [showOverlay, setShowOverlay] = useState(false);

  // Cache-bust the image URL when the print name changes so the browser
  // fetches the new cover instead of serving the stale cached image.
  const cacheBustedUrl = useMemo(() => {
    if (!url) return null;
    const sep = url.includes('?') ? '&' : '?';
    return `${url}${sep}v=${encodeURIComponent(printName || Date.now().toString())}`;
  }, [url, printName]);

  // Reset loaded/error state when the image URL changes
  useEffect(() => {
    setLoaded(false);
    setError(false);
  }, [cacheBustedUrl]);

  return (
    <>
      <div
        className={`w-20 h-20 flex-shrink-0 rounded-lg overflow-hidden bg-bambu-dark-tertiary flex items-center justify-center ${cacheBustedUrl && loaded ? 'cursor-pointer' : ''}`}
        onClick={() => cacheBustedUrl && loaded && setShowOverlay(true)}
      >
        {cacheBustedUrl && !error ? (
          <>
            <img
              src={cacheBustedUrl}
              alt={t('printers.printPreview')}
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
      {showOverlay && cacheBustedUrl && (
        <div
          className="fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-8"
          onClick={() => setShowOverlay(false)}
        >
          <div className="relative max-w-2xl max-h-full">
            <img
              src={cacheBustedUrl}
              alt={t('printers.printPreview')}
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

  // Subscribe to query cache changes to re-render when status updates
  // Throttled to prevent rapid re-renders from causing tab crashes
  const [cacheTick, setCacheTick] = useState(0);
  useEffect(() => {
    let pending = false;
    const unsubscribe = queryClient.getQueryCache().subscribe(() => {
      if (!pending) {
        pending = true;
        requestAnimationFrame(() => {
          setCacheTick(t => t + 1);
          pending = false;
        });
      }
    });
    return () => unsubscribe();
  }, [queryClient]);

  const { counts, nextFinish } = useMemo(() => {
    let printing = 0;
    let idle = 0;
    let offline = 0;
    let loading = 0;
    let nextPrinterName: string | null = null;
    let nextRemainingMin: number | null = null;
    let nextProgress: number = 0;

    printers?.forEach((printer) => {
      const status = queryClient.getQueryData<{ connected: boolean; state: string | null; remaining_time: number | null; progress: number | null }>(['printerStatus', printer.id]);
      if (status === undefined) {
        // Status not yet loaded - don't count as offline yet
        loading++;
      } else if (!status.connected) {
        offline++;
      } else if (status.state === 'RUNNING') {
        printing++;
        if (status.remaining_time != null && status.remaining_time > 0) {
          if (nextRemainingMin === null || status.remaining_time < nextRemainingMin) {
            nextRemainingMin = status.remaining_time;
            nextPrinterName = printer.name;
            nextProgress = status.progress || 0;
          }
        }
      } else {
        idle++;
      }
    });

    return {
      counts: { printing, idle, offline, loading, total: (printers?.length || 0) },
      nextFinish: nextPrinterName && nextRemainingMin ? { name: nextPrinterName, remainingMin: nextRemainingMin, progress: nextProgress } : null,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printers, queryClient, cacheTick]);

  if (!printers?.length) return null;

  return (
    <div className="flex flex-wrap items-center gap-4 gap-y-2 text-sm">
      <div className="flex items-center gap-1.5">
        <div className={`w-2 h-2 rounded-full ${counts.idle > 0 ? 'bg-bambu-green' : 'bg-gray-500'}`} />
        <span className="text-bambu-gray">
          <span className="text-white font-medium">{counts.idle}</span> {t('printers.status.available').toLowerCase()}
        </span>
      </div>
      {counts.printing > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-bambu-green animate-pulse" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.printing}</span> {t('printers.status.printing').toLowerCase()}
          </span>
        </div>
      )}
      {counts.offline > 0 && (
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-gray-400" />
          <span className="text-bambu-gray">
            <span className="text-white font-medium">{counts.offline}</span> {t('printers.status.offline').toLowerCase()}
          </span>
        </div>
      )}
      {nextFinish && (
        <>
          <div className="w-px h-4 bg-bambu-dark-tertiary" />
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-2">
            <div className="flex items-center gap-2">
              <span className="text-bambu-green font-medium">{t('printers.nextAvailable')}:</span>
              <span className="text-white font-medium">{nextFinish.name}</span>
            </div>
            <div className="flex items-center gap-2 w-full sm:w-auto">
              <div className="w-full sm:w-16 bg-bambu-dark-tertiary rounded-full h-1.5">
                <div
                  className="bg-bambu-green h-1.5 rounded-full transition-all"
                  style={{ width: `${nextFinish.progress}%` }}
                />
              </div>
              <span className="text-white font-medium">{Math.round(nextFinish.progress)}%</span>
              <span className="text-bambu-gray">({formatDuration(nextFinish.remainingMin * 60)})</span>
            </div>
          </div>
        </>
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

// Map SSDP model codes to display names
function mapModelCode(ssdpModel: string | null): string {
  if (!ssdpModel) return '';
  const modelMap: Record<string, string> = {
    // H2 Series
    'O1D': 'H2D',
    'O1E': 'H2D Pro',
    'O2D': 'H2D Pro',
    'O1C': 'H2C',
    'O1C2': 'H2C',
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
    'H2D Pro': 'H2D Pro',
    'H2C': 'H2C',
    'H2S': 'H2S',
  };
  return modelMap[ssdpModel] || ssdpModel;
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
  linkedSpools,
  spoolmanUrl,
  onGetAssignment,
  onUnassignSpool,
  timeFormat = 'system',
  cameraViewMode = 'window',
  onOpenEmbeddedCamera,
  checkPrinterFirmware = true,
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
  linkedSpools?: Record<string, LinkedSpoolInfo>;
  spoolmanUrl?: string | null;
  spoolAssignments?: SpoolAssignment[];
  onGetAssignment?: (printerId: number, amsId: number, trayId: number) => SpoolAssignment | undefined;
  onUnassignSpool?: (printerId: number, amsId: number, trayId: number) => void;
  timeFormat?: 'system' | '12h' | '24h';
  cameraViewMode?: 'window' | 'embedded';
  onOpenEmbeddedCamera?: (printerId: number, printerName: string) => void;
  checkPrinterFirmware?: boolean;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
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
    tagUid: string;
    trayUuid: string;
    printerId: number;
    amsId: number;
    trayId: number;
  } | null>(null);
  const [assignSpoolModal, setAssignSpoolModal] = useState<{
    printerId: number;
    amsId: number;
    trayId: number;
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
    extruderId?: number;
    caliIdx?: number | null;
    savedPresetId?: string;
  } | null>(null);
  const [showFirmwareModal, setShowFirmwareModal] = useState(false);
  const [plateCheckResult, setPlateCheckResult] = useState<{
    is_empty: boolean;
    confidence: number;
    difference_percent: number;
    message: string;
    debug_image_url?: string;
    needs_calibration: boolean;
    light_warning?: boolean;
    reference_count?: number;
    max_references?: number;
    roi?: { x: number; y: number; w: number; h: number };
  } | null>(null);
  const [isCheckingPlate, setIsCheckingPlate] = useState(false);
  const [isCalibrating, setIsCalibrating] = useState(false);
  const [editingRoi, setEditingRoi] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [isSavingRoi, setIsSavingRoi] = useState(false);
  const [plateCheckLightWasOff, setPlateCheckLightWasOff] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['printerStatus', printer.id],
    queryFn: () => api.getPrinterStatus(printer.id),
    refetchInterval: 30000, // Fallback polling, WebSocket handles real-time
  });

  // Check for firmware updates (cached for 5 minutes, can be disabled in settings)
  const { data: firmwareInfo } = useQuery({
    queryKey: ['firmwareUpdate', printer.id],
    queryFn: () => firmwareApi.checkPrinterUpdate(printer.id),
    staleTime: 5 * 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
    enabled: checkPrinterFirmware && hasPermission('firmware:read'),
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
    for (const vt of status?.vt_tray ?? []) {
      if (vt.tray_info_idx) ids.add(vt.tray_info_idx);
    }
    if (status?.nozzle_rack) {
      for (const slot of status.nozzle_rack) {
        if (slot.filament_id) {
          ids.add(slot.filament_id);
        }
      }
    }
    return Array.from(ids);
  }, [status?.ams, status?.vt_tray, status?.nozzle_rack]);

  // Collect loaded filament types for queue widget filtering
  const loadedFilamentTypes = useMemo(() => {
    const types = new Set<string>();
    if (status?.ams) {
      for (const ams of status.ams) {
        for (const tray of ams.tray || []) {
          if (tray.tray_type) types.add(tray.tray_type.toUpperCase());
        }
      }
    }
    for (const vt of status?.vt_tray ?? []) {
      if (vt.tray_type) types.add(vt.tray_type.toUpperCase());
    }
    return types;
  }, [status?.ams, status?.vt_tray]);

  // Collect loaded filament type+color pairs for queue widget override matching
  // Format: "TYPE:rrggbb" (e.g., "PETG:ffffff") — mirrors backend _count_override_color_matches()
  const loadedFilaments = useMemo(() => {
    const filaments = new Set<string>();
    if (status?.ams) {
      for (const ams of status.ams) {
        for (const tray of ams.tray || []) {
          if (tray.tray_type && tray.tray_color) {
            const color = tray.tray_color.replace('#', '').toLowerCase().slice(0, 6);
            filaments.add(`${tray.tray_type.toUpperCase()}:${color}`);
          }
        }
      }
    }
    for (const vt of status?.vt_tray ?? []) {
      if (vt.tray_type && vt.tray_color) {
        const color = vt.tray_color.replace('#', '').toLowerCase().slice(0, 6);
        filaments.add(`${vt.tray_type.toUpperCase()}:${color}`);
      }
    }
    return filaments;
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

  // Cache tray_now to prevent flickering when undefined values come in
  // Valid tray IDs: 0-253 for AMS, 254 for external spool
  // tray_now=255 means "no tray loaded" (Bambu protocol sentinel) — never active
  const cachedTrayNow = useRef<number | undefined>(undefined);
  const currentTrayNow = status?.tray_now;
  // Update cache: 255 means "no tray" so clear cache; valid values get cached
  if (currentTrayNow !== undefined && currentTrayNow !== 255) {
    cachedTrayNow.current = currentTrayNow;
  } else if (currentTrayNow === 255) {
    cachedTrayNow.current = undefined;
  }
  const effectiveTrayNow = (currentTrayNow !== undefined && currentTrayNow !== 255)
    ? currentTrayNow
    : cachedTrayNow.current;

  // Fetch smart plug for this printer
  const { data: smartPlug } = useQuery({
    queryKey: ['smartPlugByPrinter', printer.id],
    queryFn: () => api.getSmartPlugByPrinter(printer.id),
  });

  // Fetch script plugs for this printer (for multi-device control)
  const { data: scriptPlugs } = useQuery({
    queryKey: ['scriptPlugsByPrinter', printer.id],
    queryFn: () => api.getScriptPlugsByPrinter(printer.id),
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
  // Filter queue items by filament compatibility (same logic as PrinterQueueWidget)
  // so the badge only shows on printers that can actually run the queued jobs.
  const queueCount = useMemo(() => {
    if (!queueItems?.length) return 0;
    return queueItems.filter(item => {
      if (item.required_filament_types?.length && loadedFilamentTypes?.size) {
        if (!item.required_filament_types.every((t: string) => loadedFilamentTypes.has(t.toUpperCase()))) {
          return false;
        }
      }
      if (item.filament_overrides?.length && loadedFilaments?.size) {
        const hasColorMatch = item.filament_overrides.some((o: { type?: string; color?: string }) => {
          const oType = (o.type || '').toUpperCase();
          const oColor = (o.color || '').replace('#', '').toLowerCase().slice(0, 6);
          return loadedFilaments.has(`${oType}:${oColor}`);
        });
        if (!hasColorMatch) return false;
      }
      return true;
    }).length;
  }, [queueItems, loadedFilamentTypes, loadedFilaments]);

  // Fetch currently printing queue item to show who started it (Issue #206)
  const { data: printingQueueItems } = useQuery({
    queryKey: ['queue', printer.id, 'printing'],
    queryFn: () => api.getQueue(printer.id, 'printing'),
    enabled: status?.state === 'RUNNING',
  });

  // Fetch reprint user info (for prints started via Reprint, not queue - Issue #206)
  const { data: reprintUser } = useQuery({
    queryKey: ['currentPrintUser', printer.id],
    queryFn: () => api.getCurrentPrintUser(printer.id),
    enabled: status?.state === 'RUNNING',
  });

  // Combine both sources: queue item user takes precedence, then reprint user
  const currentPrintUser = printingQueueItems?.[0]?.created_by_username || reprintUser?.username;

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
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToDelete'), 'error'),
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

  // Run script mutation
  const runScriptMutation = useMutation({
    mutationFn: (scriptId: number) => api.controlSmartPlug(scriptId, 'on'),
    onSuccess: () => {
      showToast(t('printers.toast.scriptTriggered'));
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToRunScript'), 'error'),
  });

  // Print control mutations
  const stopPrintMutation = useMutation({
    mutationFn: () => api.stopPrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printStopped'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToStopPrint'), 'error'),
  });

  const pausePrintMutation = useMutation({
    mutationFn: () => api.pausePrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printPaused'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToPausePrint'), 'error'),
  });

  const resumePrintMutation = useMutation({
    mutationFn: () => api.resumePrint(printer.id),
    onSuccess: () => {
      showToast(t('printers.toast.printResumed'));
      queryClient.invalidateQueries({ queryKey: ['printerStatus', printer.id] });
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToResumePrint'), 'error'),
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
      showToast(`Chamber light ${on ? 'on' : 'off'}`);
    },
    onError: (error: Error, _, context) => {
      // Rollback on error
      if (context?.previousStatus) {
        queryClient.setQueryData(['printerStatus', printer.id], context.previousStatus);
      }
      showToast(error.message || t('printers.toast.failedToControlChamberLight'), 'error');
    },
  });

  // Plate detection setting mutation
  const plateDetectionMutation = useMutation({
    mutationFn: (enabled: boolean) => api.updatePrinter(printer.id, { plate_detection_enabled: enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(plateDetectionMutation.variables ? t('printers.toast.plateCheckEnabled') : t('printers.toast.plateCheckDisabled'));
    },
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToUpdateSetting'), 'error'),
  });

  // Query for printable objects (for skip functionality)
  // Fetch when printing with 2+ objects OR when modal is open
  const isPrintingWithObjects = (status?.state === 'RUNNING' || status?.state === 'PAUSE') && (status?.printable_objects_count ?? 0) >= 2;
  const { data: objectsData } = useQuery({
    queryKey: ['printableObjects', printer.id],
    queryFn: () => api.getPrintableObjects(printer.id),
    enabled: showSkipObjectsModal || isPrintingWithObjects,
    refetchInterval: showSkipObjectsModal ? 5000 : (isPrintingWithObjects ? 30000 : false), // 5s when modal open, 30s otherwise
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
      showToast(error.message || t('printers.toast.failedToRereadRfid'), 'error');
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current);
      }
      setRefreshingSlot(null);
    },
  });

  // Plate references state
  const [plateReferences, setPlateReferences] = useState<{
    references: Array<{ index: number; label: string; timestamp: string; has_image: boolean; thumbnail_url: string }>;
    max_references: number;
  } | null>(null);
  const [editingRefLabel, setEditingRefLabel] = useState<{ index: number; label: string } | null>(null);

  // Fetch plate references
  const fetchPlateReferences = async () => {
    try {
      const data = await api.getPlateReferences(printer.id);
      setPlateReferences(data);
    } catch {
      // Ignore errors - references will show as empty
    }
  };

  // Toggle plate detection enabled/disabled
  const handleTogglePlateDetection = () => {
    plateDetectionMutation.mutate(!printer.plate_detection_enabled);
  };

  // Open plate detection management modal (for calibration/references)
  const handleOpenPlateManagement = async () => {
    setIsCheckingPlate(true);
    setPlateCheckResult(null);

    // Auto-turn on light if it's off
    const lightWasOff = status?.chamber_light === false;
    setPlateCheckLightWasOff(lightWasOff);
    if (lightWasOff) {
      await api.setChamberLight(printer.id, true);
      // Wait for light to physically turn on and camera to adjust exposure
      // (MQTT command is async, light takes ~1s to turn on, camera needs time to adjust)
      await new Promise(resolve => setTimeout(resolve, 2500));
    }

    try {
      const result = await api.checkPlateEmpty(printer.id, { includeDebugImage: true });
      setPlateCheckResult(result);
      fetchPlateReferences();
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('printers.toast.failedToCheckPlate'), 'error');
      // Restore light if check failed
      if (lightWasOff) {
        await api.setChamberLight(printer.id, false);
        setPlateCheckLightWasOff(false);
      }
    } finally {
      setIsCheckingPlate(false);
    }
  };

  // Close plate check modal and restore light state
  const closePlateCheckModal = useCallback(async () => {
    setPlateCheckResult(null);
    // Restore light to original state if we turned it on
    if (plateCheckLightWasOff) {
      await api.setChamberLight(printer.id, false);
      setPlateCheckLightWasOff(false);
    }
  }, [plateCheckLightWasOff, printer.id]);

  // Calibrate plate detection handler
  const handleCalibratePlate = async (label?: string) => {
    setIsCalibrating(true);
    try {
      const result = await api.calibratePlateDetection(printer.id, { label });
      if (result.success) {
        showToast(result.message || t('printers.toast.calibrationSaved'), 'success');
        // Refresh references and re-check
        fetchPlateReferences();
        const checkResult = await api.checkPlateEmpty(printer.id, { includeDebugImage: true });
        setPlateCheckResult(checkResult);
      } else {
        showToast(result.message || t('printers.toast.calibrationFailed'), 'error');
      }
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('printers.toast.calibrationFailed'), 'error');
    } finally {
      setIsCalibrating(false);
    }
  };

  // Update reference label
  const handleUpdateRefLabel = async (index: number, label: string) => {
    try {
      await api.updatePlateReferenceLabel(printer.id, index, label);
      setEditingRefLabel(null);
      fetchPlateReferences();
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('printers.toast.failedToUpdateLabel'), 'error');
    }
  };

  // Delete reference
  const handleDeleteRef = async (index: number) => {
    try {
      await api.deletePlateReference(printer.id, index);
      showToast(t('printers.toast.referenceDeleted'), 'success');
      fetchPlateReferences();
      // Re-check to update counts
      const checkResult = await api.checkPlateEmpty(printer.id, { includeDebugImage: true });
      setPlateCheckResult(checkResult);
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('printers.toast.failedToDeleteReference'), 'error');
    }
  };

  // Save ROI settings
  const handleSaveRoi = async () => {
    if (!editingRoi) return;
    setIsSavingRoi(true);
    try {
      await api.updatePrinter(printer.id, { plate_detection_roi: editingRoi });
      showToast(t('printers.toast.detectionAreaSaved'), 'success');
      setEditingRoi(null);
      // Re-check to see new ROI in action
      const checkResult = await api.checkPlateEmpty(printer.id, { includeDebugImage: true });
      setPlateCheckResult(checkResult);
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('printers.toast.failedToSaveDetectionArea'), 'error');
    } finally {
      setIsSavingRoi(false);
    }
  };

  // Close plate check modal on Escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && plateCheckResult) {
        closePlateCheckModal();
      }
    };
    window.addEventListener('keydown', handleEscape);
    return () => window.removeEventListener('keydown', handleEscape);
  }, [plateCheckResult, closePlateCheckModal]);

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
      case 1: return 'w-12 h-12';
      case 2: return 'w-16 h-16';
      case 3: return 'w-20 h-20';
      case 4: return 'w-24 h-24';
      default: return 'w-16 h-16';
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
      case 1: return 'mb-1';
      case 2: return 'mb-2';
      case 3: return 'mb-4';
      case 4: return 'mb-5';
      default: return 'mb-2';
    }
  };

  return (
    <Card className="relative">
      <CardContent className={`flex flex-col h-full gap-2 lg:p-6 ${cardSize >= 3 ? 'p-5' : 'p-3'}`}>
        {/* Header */}
        <div className={getSpacing()}>
          {/* Top row: Image, Name, Menu */}
          <div className="flex items-start justify-between gap-2">
            <div className="flex items-center gap-3 min-w-0 flex-1">
              {/* Printer Model Image */}
              <img
                src={getPrinterImage(printer.model)}
                alt={printer.model || t('common.printer')}
                className={`p-1 object-contain rounded-lg bg-bambu-dark flex-shrink-0 ${getImageSize()}`}
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <h3 className={`font-semibold text-white ${getTitleSize()}`}>{printer.name}</h3>
                  {/* Connection indicator dot for compact mode */}
                  {viewMode === 'compact' && (
                    <div
                      className={`w-2 h-2 rounded-full flex-shrink-0 ${status?.connected ? 'bg-status-ok' : 'bg-status-error'
                        }`}
                      title={status?.connected ? t('printers.connection.connected') : t('printers.connection.offline')}
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
                <div className="absolute right-0 mt-2 w-48 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-20">
                  <button
                    className={`w-full px-4 py-2 text-left text-sm flex items-center gap-2 ${hasPermission('printers:update')
                      ? 'hover:bg-bambu-dark-tertiary'
                      : 'opacity-50 cursor-not-allowed'
                      }`}
                    onClick={() => {
                      if (!hasPermission('printers:update')) return;
                      setShowEditModal(true);
                      setShowMenu(false);
                    }}
                    title={!hasPermission('printers:update') ? t('printers.permission.noEdit') : undefined}
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
                    {t('printers.reconnect')}
                  </button>
                  <button
                    className="w-full px-4 py-2 text-left text-sm hover:bg-bambu-dark-tertiary flex items-center gap-2"
                    onClick={() => {
                      setShowMQTTDebug(true);
                      setShowMenu(false);
                    }}
                  >
                    <Terminal className="w-4 h-4" />
                    {t('printers.mqttDebug')}
                  </button>
                  <button
                    className={`w-full px-4 py-2 text-left text-sm flex items-center gap-2 ${hasPermission('printers:delete')
                      ? 'text-red-400 hover:bg-bambu-dark-tertiary'
                      : 'text-red-400/50 cursor-not-allowed'
                      }`}
                    onClick={() => {
                      if (!hasPermission('printers:delete')) return;
                      setShowDeleteConfirm(true);
                      setShowMenu(false);
                    }}
                    title={!hasPermission('printers:delete') ? t('printers.permission.noDelete') : undefined}
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
                className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs ${status?.connected
                  ? 'bg-status-ok/20 text-status-ok'
                  : 'bg-status-error/20 text-status-error'
                  }`}
              >
                {status?.connected ? (
                  <Link className="w-3 h-3" />
                ) : (
                  <Unlink className="w-3 h-3" />
                )}
                {status?.connected ? t('printers.connection.connected') : t('printers.connection.offline')}
              </span>
              {/* WiFi signal strength indicator */}
              {status?.connected && wifiSignal != null && (
                <span
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs ${wifiSignal >= -50
                    ? 'bg-status-ok/20 text-status-ok'
                    : wifiSignal >= -60
                      ? 'bg-status-ok/20 text-status-ok'
                      : wifiSignal >= -70
                        ? 'bg-status-warning/20 text-status-warning'
                        : wifiSignal >= -80
                          ? 'bg-orange-500/20 text-orange-600'
                          : 'bg-status-error/20 text-status-error'
                    }`}
                  title={`WiFi: ${wifiSignal} dBm - ${t(getWifiStrength(wifiSignal).labelKey)}`}
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
                    className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${knownErrors.length > 0
                      ? knownErrors.some(e => e.severity <= 2)
                        ? 'bg-status-error/20 text-status-error'
                        : 'bg-status-warning/20 text-status-warning'
                      : 'bg-status-ok/20 text-status-ok'
                      }`}
                    title={t('printers.clickToViewHmsErrors')}
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
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs cursor-pointer hover:opacity-80 transition-opacity ${maintenanceInfo.due_count > 0
                    ? 'bg-status-error/20 text-status-error'
                    : maintenanceInfo.warning_count > 0
                      ? 'bg-status-warning/20 text-status-warning'
                      : 'bg-status-ok/20 text-status-ok'
                    }`}
                  title={
                    maintenanceInfo.due_count > 0 || maintenanceInfo.warning_count > 0
                      ? `${maintenanceInfo.due_count > 0 ? `${maintenanceInfo.due_count} maintenance due` : ''}${maintenanceInfo.due_count > 0 && maintenanceInfo.warning_count > 0 ? ', ' : ''}${maintenanceInfo.warning_count > 0 ? `${maintenanceInfo.warning_count} due soon` : ''} - Click to view`
                      : t('printers.maintenanceUpToDate')
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
                  title={t('printers.queue.inQueue', { count: queueCount })}
                >
                  <Layers className="w-3 h-3" />
                  {queueCount}
                </button>
              )}
              {/* Firmware Version Badge */}
              {checkPrinterFirmware && firmwareInfo?.current_version && firmwareInfo?.latest_version ? (
                <button
                  onClick={() => setShowFirmwareModal(true)}
                  className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs hover:opacity-80 transition-opacity ${firmwareInfo.update_available
                    ? 'bg-orange-500/20 text-orange-400'
                    : 'bg-status-ok/20 text-status-ok'
                    }`}
                  title={
                    firmwareInfo.update_available
                      ? t('printers.firmwareUpdateAvailable', { current: firmwareInfo.current_version, latest: firmwareInfo.latest_version })
                      : t('printers.firmwareUpToDate', { version: firmwareInfo.current_version })
                  }
                >
                  {firmwareInfo.update_available ? <Download className="w-3 h-3" /> : <CheckCircle className="w-3 h-3" />}
                  {firmwareInfo.current_version}
                </button>
              ) : status?.firmware_version ? (
                <span className="flex items-center gap-1 px-2 py-1 rounded-full text-xs bg-bambu-dark-tertiary/50 text-bambu-gray">
                  {status.firmware_version}
                </span>
              ) : null}
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
                      <span className="text-sm text-white">{t('printers.deleteArchives')}</span>
                      <p className="text-xs text-bambu-gray mt-0.5">
                        {deleteArchives
                          ? t('printers.confirm.deleteArchivesNote')
                          : t('printers.confirm.keepArchivesNote')}
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
                {(status.state === 'RUNNING' || status.state === 'PAUSE') ? (
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
                <div className="p-3 bg-bambu-dark rounded-lg relative">
                  {/* Skip Objects button - top right corner, always visible */}
                  <button
                    onClick={() => setShowSkipObjectsModal(true)}
                    disabled={!(status.state === 'RUNNING' || status.state === 'PAUSE') || (status.printable_objects_count ?? 0) < 2 || !hasPermission('printers:control')}
                    className={`absolute top-2 right-2 p-1.5 rounded transition-colors z-10 ${
                      (status.state === 'RUNNING' || status.state === 'PAUSE') && (status.printable_objects_count ?? 0) >= 2 && hasPermission('printers:control')
                        ? 'text-bambu-gray hover:text-white hover:bg-white/10'
                        : 'text-bambu-gray/30 cursor-not-allowed'
                    }`}
                    title={
                      !hasPermission('printers:control')
                        ? t('printers.permission.noControl')
                        : !(status.state === 'RUNNING' || status.state === 'PAUSE')
                          ? t('printers.skipObjects.onlyWhilePrinting')
                          : (status.printable_objects_count ?? 0) >= 2
                            ? t('printers.skipObjects.tooltip')
                            : t('printers.skipObjects.requiresMultiple')
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
                      url={(status.state === 'RUNNING' || status.state === 'PAUSE') ? status.cover_url : null}
                      printName={(status.state === 'RUNNING' || status.state === 'PAUSE') ? (status.subtask_name || status.current_print || undefined) : undefined}
                    />
                    {/* Print Info */}
                    <div className="flex-1 min-w-0">
                      {status.current_print && (status.state === 'RUNNING' || status.state === 'PAUSE') ? (
                        <>
                          <p className="text-sm text-bambu-gray mb-1">{getStatusDisplay(status.state, status.stg_cur_name)}</p>
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
                                  {formatDuration(status.remaining_time * 60)}
                                </span>
                                <span className="text-bambu-green font-medium" title={t('printers.estimatedCompletion')}>
                                  ETA {formatETA(status.remaining_time, timeFormat, t)}
                                </span>
                              </>
                            )}
                            {status.layer_num != null && status.total_layers != null && status.total_layers > 0 && (
                              <span className="flex items-center gap-1">
                                <Layers className="w-3 h-3" />
                                {status.layer_num}/{status.total_layers}
                              </span>
                            )}
                            {currentPrintUser && (
                              <span className="flex items-center gap-1" title={`Started by ${currentPrintUser}`}>
                                <User className="w-3 h-3" />
                                {currentPrintUser}
                              </span>
                            )}
                          </div>
                        </>
                      ) : (
                        <>
                          <p className="text-sm text-bambu-gray mb-1">{t('printers.sort.status')}</p>
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

                {/* Queue Widget - always visible when there are pending items */}
                <PrinterQueueWidget printerId={printer.id} printerModel={printer.model} printerState={status.state} plateCleared={status.plate_cleared} loadedFilamentTypes={loadedFilamentTypes} loadedFilaments={loadedFilaments} />
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
              // Extended nozzle data from nozzle_rack (H2 series: wear, serial, max_temp, etc.)
              // nozzle_rack id 0 = extruder 0 = RIGHT, id 1 = extruder 1 = LEFT
              const leftNozzleSlot = status.nozzle_rack?.find(s => s.id === 1);
              const rightNozzleSlot = status.nozzle_rack?.find(s => s.id === 0);
              // Single-nozzle models (H2D, H2C): use the primary nozzle (id 0)
              const singleNozzleSlot = rightNozzleSlot || leftNozzleSlot;

              return (
                <div className="flex items-stretch gap-1.5 flex-wrap">
                  {/* Nozzle temp - combined for dual nozzle */}
                  <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1 flex flex-col justify-center items-center">
                    <HeaterThermometer className="w-3.5 h-3.5 mb-0.5" color="text-orange-400" isHeating={nozzleHeating} />
                    {status.temperatures.nozzle_2 !== undefined ? (
                      <>
                        <p className="text-[9px] text-bambu-gray">L / R</p>
                        <p className="text-[11px] text-white">
                          {Math.round(status.temperatures.nozzle || 0)}° / {Math.round(status.temperatures.nozzle_2 || 0)}°
                        </p>
                      </>
                    ) : singleNozzleSlot ? (
                      <NozzleSlotHoverCard slot={singleNozzleSlot} index={0} activeStatus filamentName={singleNozzleSlot.filament_id ? filamentInfo?.[singleNozzleSlot.filament_id]?.name : undefined}>
                        <div className="cursor-default">
                          <p className="text-[9px] text-bambu-gray">{t('printers.temperatures.nozzle')}</p>
                          <p className="text-[11px] text-white">
                            {Math.round(status.temperatures.nozzle || 0)}°C
                          </p>
                        </div>
                      </NozzleSlotHoverCard>
                    ) : (
                      <>
                        <p className="text-[9px] text-bambu-gray">{t('printers.temperatures.nozzle')}</p>
                        <p className="text-[11px] text-white">
                          {Math.round(status.temperatures.nozzle || 0)}°C
                        </p>
                      </>
                    )}
                  </div>
                  <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1 flex flex-col justify-center items-center">
                    <HeaterThermometer className="w-3.5 h-3.5 mb-0.5" color="text-blue-400" isHeating={bedHeating} />
                    <p className="text-[9px] text-bambu-gray">{t('printers.temperatures.bed')}</p>
                    <p className="text-[11px] text-white">
                      {Math.round(status.temperatures.bed || 0)}°C
                    </p>
                  </div>
                  {status.temperatures.chamber !== undefined && (
                    <div className="text-center px-2 py-1.5 bg-bambu-dark rounded-lg flex-1 flex flex-col justify-center items-center">
                      <HeaterThermometer className="w-3.5 h-3.5 mb-0.5" color="text-green-400" isHeating={chamberHeating} />
                      <p className="text-[9px] text-bambu-gray">{t('printers.temperatures.chamber')}</p>
                      <p className="text-[11px] text-white">
                        {Math.round(status.temperatures.chamber || 0)}°C
                      </p>
                    </div>
                  )}
                  {/* Active nozzle indicator for dual-nozzle printers */}
                  {isDualNozzle && (
                    <DualNozzleHoverCard
                      leftSlot={leftNozzleSlot}
                      rightSlot={rightNozzleSlot}
                      activeNozzle={activeNozzle}
                      filamentInfo={filamentInfo}
                    >
                      <div className="text-center px-3 py-1.5 bg-bambu-dark rounded-lg h-full flex flex-col justify-center items-center cursor-default" title={t('printers.activeNozzle', { nozzle: activeNozzle === 'L' ? t('common.left') : t('common.right') })}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`text-[11px] font-bold ${activeNozzle === 'L' ? 'text-amber-400' : 'text-gray-500'}`}>
                            L{leftNozzleSlot?.nozzle_diameter ? ` ${leftNozzleSlot.nozzle_diameter}` : ''}
                          </span>
                          <span className="text-[9px] text-bambu-gray/40">·</span>
                          <span className={`text-[11px] font-bold ${activeNozzle === 'R' ? 'text-amber-400' : 'text-gray-500'}`}>
                            R{rightNozzleSlot?.nozzle_diameter ? ` ${rightNozzleSlot.nozzle_diameter}` : ''}
                          </span>
                        </div>
                        <p className="text-[9px] text-bambu-gray">{t('printers.temperatures.nozzle')}</p>
                      </div>
                    </DualNozzleHoverCard>
                  )}
                  {/* H2C nozzle rack (tool-changer dock) — only show when rack nozzles exist (IDs >= 2) */}
                  {status.nozzle_rack && status.nozzle_rack.some(s => s.id >= 2) && (
                    <NozzleRackCard slots={status.nozzle_rack} filamentInfo={filamentInfo} />
                  )}
                </div>
              );
            })()}

            {/* Controls - Fans + Print Buttons */}
            {viewMode === 'expanded' && (() => {
              // Determine print state for control buttons
              const isRunning = status.state === 'RUNNING';
              const isPaused = status.state === 'PAUSE';
              const isPrinting = isRunning || isPaused;
              const isControlBusy = stopPrintMutation.isPending || pausePrintMutation.isPending || resumePrintMutation.isPending;

              // Fan data
              const partFan = status.cooling_fan_speed;
              const auxFan = status.big_fan1_speed;
              const chamberFan = status.big_fan2_speed;

              return (
                <div className="mt-1">
                  {/* Section Header */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('printers.controls')}
                    </span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary/30" />
                  </div>

                  <div className="flex items-center justify-between gap-2 max-[550px]:items-start">
                    {/* Left: Fan Status - always visible, dynamic coloring */}
                    <div className="flex items-center gap-2 min-w-0 max-[550px]:flex-wrap max-[550px]:items-start max-[550px]:gap-1.5">
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
                    <div className="flex items-center gap-2 flex-shrink-0 max-[550px]:self-start">
                      {/* Stop button */}
                      <button
                        onClick={() => setShowStopConfirm(true)}
                        disabled={!isPrinting || isControlBusy || !hasPermission('printers:control')}
                        className={`
                          flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                          transition-colors
                          ${isPrinting && hasPermission('printers:control')
                            ? 'bg-red-500/20 text-red-400 hover:bg-red-500/30'
                            : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                          }
                        `}
                        title={!hasPermission('printers:control') ? t('printers.permission.noControl') : t('printers.stop')}
                      >
                        <Square className="w-3 h-3" />
                        {t('printers.stop')}
                      </button>

                      {/* Pause/Resume button */}
                      <button
                        onClick={() => isPaused ? setShowResumeConfirm(true) : setShowPauseConfirm(true)}
                        disabled={!isPrinting || isControlBusy || !hasPermission('printers:control')}
                        className={`
                          flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium
                          transition-colors
                          ${isPrinting && hasPermission('printers:control')
                            ? isPaused
                              ? 'bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30'
                              : 'bg-yellow-500/20 text-yellow-400 hover:bg-yellow-500/30'
                            : 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                          }
                        `}
                        title={!hasPermission('printers:control') ? t('printers.permission.noControl') : (isPaused ? t('printers.resume') : t('printers.pause'))}
                      >
                        {isPaused ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                        {isPaused ? t('printers.resume') : t('printers.pause')}
                      </button>
                    </div>
                  </div>
                </div>
              );
            })()}

            {/* AMS Units */}
            {(amsData?.length > 0 || status.vt_tray.length > 0) && viewMode === 'expanded' && (() => {
              const isDualNozzle = printer.nozzle_count === 2 || status?.temperatures?.nozzle_2 !== undefined;
              return (
                <div className='mt-1 @container'>
                  {/* Section Header */}
                  <div className="flex items-center gap-2 mb-2">
                    <span className="text-[10px] uppercase tracking-wider text-bambu-gray font-medium">
                      {t('printers.filaments')}
                    </span>
                    <div className="flex-1 h-px bg-bambu-dark-tertiary/30" />
                  </div>

                  {/* AMS Content */}
                  <div className={`space-y-1.5 ${isDualNozzle && '@sm:items-start grid grid-cols-1 @sm:grid-cols-[1fr_1px_1fr] gap-1.5'}`}>
                    {Array.from({ length: printer.nozzle_count }, (_, i) => i).reverse().map((extruderIndex, i) => (
                      <React.Fragment key={extruderIndex}>
                        {i > 0 && <div className="bg-bambu-dark-tertiary @sm:h-full h-px @sm:w-auto w-full"></div>}
                        <div className="flex flex-wrap gap-1.5">
                          {/* AMS */}
                          {amsData.sort((a, b) => (b.tray.length - a.tray.length)).filter((ams) => (amsExtruderMap[String(ams.id)] !== undefined ? amsExtruderMap[String(ams.id)] : ams.id >= 128 ? ams.id - 128 : ams.id) === extruderIndex).map((ams) => (
                            <AMSUnitCard
                              key={ams.id}
                              ams={ams}
                              isDualNozzle={isDualNozzle}
                              amsExtruderMap={amsExtruderMap}
                              effectiveTrayNow={effectiveTrayNow}
                              filamentInfo={filamentInfo}
                              slotPresets={slotPresets}
                              amsThresholds={amsThresholds}
                              printerId={printer.id}
                              printerState={status?.state}
                              spoolmanEnabled={spoolmanEnabled}
                              hasUnlinkedSpools={hasUnlinkedSpools}
                              linkedSpools={linkedSpools}
                              spoolmanUrl={spoolmanUrl}
                              onGetAssignment={onGetAssignment}
                              onUnassignSpool={onUnassignSpool}
                              amsSlotMenu={amsSlotMenu}
                              setAmsSlotMenu={setAmsSlotMenu}
                              refreshingSlot={refreshingSlot}
                              onRefreshSlot={(amsId, slotId) => refreshAmsSlotMutation.mutate({ amsId, slotId })}
                              hasPermission={hasPermission}
                              onOpenAmsHistory={(amsId, amsLabel, mode) => setAmsHistoryModal({ amsId, amsLabel, mode })}
                              onOpenLinkSpool={(tagUid, trayUuid, pId, aId, tId) => setLinkSpoolModal({ tagUid, trayUuid, printerId: pId, amsId: aId, trayId: tId })}
                              onOpenAssignSpool={(pId, aId, tId, trayInfo) => setAssignSpoolModal({ printerId: pId, amsId: aId, trayId: tId, trayInfo })}
                              onOpenConfigureSlot={(config) => setConfigureSlotModal(config)}

                            />
                          ))}
                          {/* External spool(s) */}
                          <AMSUnitCard
                            ams={{ id: 255, humidity: null, temp: null, is_ams_ht: false, tray: [status.vt_tray[extruderIndex]] }}
                            isDualNozzle={isDualNozzle}
                            amsExtruderMap={amsExtruderMap}
                            effectiveTrayNow={effectiveTrayNow}
                            filamentInfo={filamentInfo}
                            slotPresets={slotPresets}
                            amsThresholds={amsThresholds}
                            printerId={printer.id}
                            printerState={status?.state}
                            spoolmanEnabled={spoolmanEnabled}
                            hasUnlinkedSpools={hasUnlinkedSpools}
                            linkedSpools={linkedSpools}
                            spoolmanUrl={spoolmanUrl}
                            onGetAssignment={onGetAssignment}
                            onUnassignSpool={onUnassignSpool}
                            amsSlotMenu={amsSlotMenu}
                            setAmsSlotMenu={setAmsSlotMenu}
                            refreshingSlot={refreshingSlot}
                            onRefreshSlot={(amsId, slotId) => refreshAmsSlotMutation.mutate({ amsId, slotId })}
                            hasPermission={hasPermission}
                            onOpenAmsHistory={(amsId, amsLabel, mode) => setAmsHistoryModal({ amsId, amsLabel, mode })}
                            onOpenLinkSpool={(tagUid, trayUuid, pId, aId, tId) => setLinkSpoolModal({ tagUid, trayUuid, printerId: pId, amsId: aId, trayId: tId })}
                            onOpenAssignSpool={(pId, aId, tId, trayInfo) => setAssignSpoolModal({ printerId: pId, amsId: aId, trayId: tId, trayInfo })}
                            onOpenConfigureSlot={(config) => setConfigureSlotModal(config)}
                          />
                        </div>
                      </React.Fragment>
                    ))}
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
                    className={`text-xs px-1.5 py-0.5 rounded flex-shrink-0 ${plugStatus.state === 'ON'
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
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'ON' || !hasPermission('smart_plugs:control')}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${!hasPermission('smart_plugs:control')
                    ? 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                    : plugStatus?.state === 'ON'
                      ? 'bg-bambu-green text-white'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                    }`}
                  title={!hasPermission('smart_plugs:control') ? t('printers.permission.noSmartPlugControl') : undefined}
                >
                  <Power className="w-3 h-3" />
                  On
                </button>
                <button
                  onClick={() => setShowPowerOffConfirm(true)}
                  disabled={powerControlMutation.isPending || plugStatus?.state === 'OFF' || !hasPermission('smart_plugs:control')}
                  className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${!hasPermission('smart_plugs:control')
                    ? 'bg-bambu-dark text-bambu-gray/50 cursor-not-allowed'
                    : plugStatus?.state === 'OFF'
                      ? 'bg-red-500/30 text-red-400'
                      : 'bg-bambu-dark text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary'
                    }`}
                  title={!hasPermission('smart_plugs:control') ? t('printers.permission.noSmartPlugControl') : undefined}
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
                  disabled={toggleAutoOffMutation.isPending || smartPlug.auto_off_executed || !hasPermission('smart_plugs:control')}
                  title={!hasPermission('smart_plugs:control') ? t('printers.permission.noSmartPlugControl') : (smartPlug.auto_off_executed ? t('printers.autoOffExecuted') : t('printers.autoOffAfterPrint'))}
                  className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${!hasPermission('smart_plugs:control')
                    ? 'bg-bambu-dark-tertiary/50 cursor-not-allowed'
                    : smartPlug.auto_off_executed
                      ? 'bg-bambu-green/50 cursor-not-allowed'
                      : smartPlug.auto_off ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                    }`}
                >
                  <span
                    className={`absolute top-[2px] left-[2px] w-4 h-4 bg-white rounded-full transition-transform ${smartPlug.auto_off || smartPlug.auto_off_executed ? 'translate-x-4' : 'translate-x-0'
                      }`}
                  />
                </button>
              </div>
            </div>

            {/* HA entity buttons row */}
            {scriptPlugs && scriptPlugs.length > 0 && (
              <div className="flex items-center gap-2 mt-2 pt-2 border-t border-bambu-dark-tertiary/50">
                <Home className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />
                <span className="text-xs text-bambu-gray">HA:</span>
                <div className="flex flex-wrap gap-1">
                  {scriptPlugs.map(script => (
                    <button
                      key={script.id}
                      onClick={() => runScriptMutation.mutate(script.id)}
                      disabled={runScriptMutation.isPending}
                      title={`Run ${script.ha_entity_id}`}
                      className="px-2 py-0.5 text-xs bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 rounded transition-colors flex items-center gap-1"
                    >
                      <Play className="w-2.5 h-2.5" />
                      {script.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Connection Info & Actions - hidden in compact mode */}
        {viewMode === 'expanded' && (
          <div className="mt-auto pt-4 border-t border-bambu-dark-tertiary flex flex-col gap-2 lg:flex-row lg:justify-between">
            <div className="flex justify-between text-xs text-bambu-gray lg:flex-col">
              <p>{printer.ip_address}</p>
              <p className="truncate">{printer.serial_number}</p>
            </div>
            <div className="flex justify-end items-center gap-2 flex-wrap lg:justify-start">
              {/* Chamber Light Toggle */}
              <Button
                variant="secondary"
                size="sm"
                onClick={() => chamberLightMutation.mutate(!status?.chamber_light)}
                disabled={!status?.connected || chamberLightMutation.isPending || !hasPermission('printers:control')}
                title={!hasPermission('printers:control') ? t('printers.permission.noControl') : (status?.chamber_light ? t('printers.chamberLightOff') : t('printers.chamberLightOn'))}
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
                disabled={!status?.connected || !hasPermission('camera:view')}
                title={!hasPermission('camera:view') ? t('printers.permission.noCamera') : (cameraViewMode === 'embedded' ? t('printers.openCameraOverlay') : t('printers.openCameraWindow'))}
              >
                <Video className="w-4 h-4" />
              </Button>
              {/* Split button: main part toggles detection, chevron opens modal */}
              <div className={`inline-flex rounded-md ${printer.plate_detection_enabled ? 'ring-1 ring-green-500' : ''}`}>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleTogglePlateDetection}
                  disabled={!status?.connected || plateDetectionMutation.isPending || !hasPermission('printers:update')}
                  title={!hasPermission('printers:update') ? t('printers.plateDetection.noPermission') : (printer.plate_detection_enabled ? t('printers.plateDetection.enabledClick') : t('printers.plateDetection.disabledClick'))}
                  className={`!rounded-r-none !border-r-0 ${printer.plate_detection_enabled ? "!border-green-500 !text-green-400 hover:!bg-green-500/20" : ""}`}
                >
                  {plateDetectionMutation.isPending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <ScanSearch className="w-4 h-4" />
                  )}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleOpenPlateManagement}
                  disabled={!status?.connected || isCheckingPlate || !hasPermission('printers:update')}
                  title={!hasPermission('printers:update') ? t('printers.plateDetection.noPermission') : t('printers.plateDetection.manageCalibration')}
                  className={`!rounded-l-none !px-1.5 ${printer.plate_detection_enabled ? "!border-green-500 !text-green-400 hover:!bg-green-500/20" : ""}`}
                >
                  {isCheckingPlate ? (
                    <Loader2 className="w-3 h-3 animate-spin" />
                  ) : (
                    <ChevronDown className="w-3 h-3" />
                  )}
                </Button>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowFileManager(true)}
                disabled={!hasPermission('printers:files')}
                title={!hasPermission('printers:files') ? t('printers.permission.noFiles') : t('printers.browseFiles')}
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

      {/* Plate Check Result Modal */}
      {plateCheckResult && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={() => closePlateCheckModal()}>
          <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-w-lg w-full" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
              <div className="flex items-center gap-2">
                {plateCheckResult.needs_calibration ? (
                  <ScanSearch className="w-5 h-5 text-blue-500" />
                ) : plateCheckResult.is_empty ? (
                  <CheckCircle className="w-5 h-5 text-green-500" />
                ) : (
                  <XCircle className="w-5 h-5 text-yellow-500" />
                )}
                <h2 className="text-lg font-semibold text-white">
                  Build Plate Check
                </h2>
                {plateCheckResult.reference_count !== undefined && plateCheckResult.max_references && (
                  <span className="text-xs text-bambu-gray bg-bambu-dark-tertiary px-2 py-1 rounded">
                    {plateCheckResult.reference_count}/{plateCheckResult.max_references} refs
                  </span>
                )}
              </div>
              <button
                onClick={() => closePlateCheckModal()}
                className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-4 space-y-4">
              {plateCheckResult.needs_calibration ? (
                <>
                  <div className="p-3 rounded-lg bg-blue-500/20 border border-blue-500/50">
                    <p className="font-medium text-blue-400">
                      {t('printers.plateDetection.calibrationRequired')}
                    </p>
                    <p className="text-sm text-bambu-gray mt-1" dangerouslySetInnerHTML={{ __html: t('printers.plateDetection.calibrationInstructions') }} />
                  </div>
                  <div className="text-sm text-bambu-gray space-y-2">
                    <p>{t('printers.plateDetection.calibrationDescription')}</p>
                    <p dangerouslySetInnerHTML={{ __html: t('printers.plateDetection.calibrationTip') }} />
                  </div>
                </>
              ) : (
                <>
                  <div className={`p-3 rounded-lg ${plateCheckResult.is_empty ? 'bg-green-500/20 border border-green-500/50' : 'bg-yellow-500/20 border border-yellow-500/50'}`}>
                    <p className={`font-medium ${plateCheckResult.is_empty ? 'text-green-400' : 'text-yellow-400'}`}>
                      {plateCheckResult.is_empty ? t('printers.plateDetection.plateEmpty') : t('printers.plateDetection.objectsDetected')}
                    </p>
                    <p className="text-sm text-bambu-gray mt-1">
                      {t('printers.plateDetection.confidence')}: {Math.round(plateCheckResult.confidence * 100)}% | {t('printers.plateDetection.difference')}: {plateCheckResult.difference_percent.toFixed(1)}%
                    </p>
                  </div>
                  {plateCheckResult.debug_image_url && (
                    <div>
                      <p className="text-sm text-bambu-gray mb-2">{t('printers.plateDetection.analysisPreview')}</p>
                      <img
                        src={plateCheckResult.debug_image_url}
                        alt={t('printers.plateDetection.analysisPreview')}
                        className="w-full rounded-lg border border-bambu-dark-tertiary"
                      />
                      <p className="text-xs text-bambu-gray mt-2">
                        {t('printers.plateDetection.analysisLegend')}
                      </p>
                    </div>
                  )}
                  <p className="text-xs text-bambu-gray">
                    {plateCheckResult.message}
                  </p>
                </>
              )}

              {/* Saved References Grid */}
              {plateReferences && plateReferences.references.length > 0 && (
                <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
                  <p className="text-sm font-medium text-white mb-2">
                    {t('printers.plateDetection.savedReferences', { count: plateReferences.references.length, max: plateReferences.max_references })}
                  </p>
                  <div className="grid grid-cols-5 gap-2">
                    {plateReferences.references.map((ref) => (
                      <div key={ref.index} className="relative group">
                        <img
                          src={api.getPlateReferenceThumbnailUrl(printer.id, ref.index)}
                          alt={ref.label || `Reference ${ref.index + 1}`}
                          className="w-full aspect-video object-cover rounded border border-bambu-dark-tertiary"
                        />
                        {/* Delete button */}
                        <button
                          onClick={() => handleDeleteRef(ref.index)}
                          className="absolute top-1 right-1 p-0.5 bg-red-500/80 rounded opacity-0 group-hover:opacity-100 transition-opacity"
                          title={t('printers.plateDetection.deleteReference')}
                        >
                          <X className="w-3 h-3 text-white" />
                        </button>
                        {/* Label */}
                        {editingRefLabel?.index === ref.index ? (
                          <input
                            type="text"
                            value={editingRefLabel.label}
                            onChange={(e) => setEditingRefLabel({ ...editingRefLabel, label: e.target.value })}
                            onBlur={() => handleUpdateRefLabel(ref.index, editingRefLabel.label)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleUpdateRefLabel(ref.index, editingRefLabel.label);
                              if (e.key === 'Escape') setEditingRefLabel(null);
                            }}
                            className="w-full mt-1 px-1 py-0.5 text-xs bg-bambu-dark-tertiary border border-bambu-green rounded text-white"
                            autoFocus
                            placeholder={t('printers.plateDetection.labelPlaceholder')}
                          />
                        ) : (
                          <p
                            className="text-xs text-bambu-gray mt-1 truncate cursor-pointer hover:text-white"
                            onClick={() => setEditingRefLabel({ index: ref.index, label: ref.label })}
                            title={ref.label ? t('printers.plateDetection.clickToEdit', { label: ref.label }) : t('printers.plateDetection.clickToAddLabel')}
                          >
                            {ref.label || <span className="italic opacity-50">{t('printers.noLabel')}</span>}
                          </p>
                        )}
                        {/* Timestamp */}
                        <p className="text-[10px] text-bambu-gray/60">
                          {ref.timestamp ? parseUTCDate(ref.timestamp)?.toLocaleDateString() ?? '' : ''}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ROI Editor */}
              {!plateCheckResult.needs_calibration && (
                <div className="mt-4 pt-4 border-t border-bambu-dark-tertiary">
                  <div className="flex items-center justify-between mb-2">
                    <p className="text-sm font-medium text-white">{t('printers.roi.title')}</p>
                    {!editingRoi ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingRoi(plateCheckResult.roi || { x: 0.15, y: 0.35, w: 0.70, h: 0.55 })}
                      >
                        <Pencil className="w-3 h-3 mr-1" />
                        {t('common.edit')}
                      </Button>
                    ) : (
                      <div className="flex gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditingRoi(null)}
                          disabled={isSavingRoi}
                        >
                          {t('common.cancel')}
                        </Button>
                        <Button
                          size="sm"
                          onClick={handleSaveRoi}
                          disabled={isSavingRoi}
                        >
                          {isSavingRoi ? <Loader2 className="w-3 h-3 animate-spin" /> : t('common.save')}
                        </Button>
                      </div>
                    )}
                  </div>
                  {editingRoi ? (
                    <div className="space-y-3 bg-bambu-dark-tertiary/50 p-3 rounded-lg">
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs text-bambu-gray">{t('printers.roi.xStart')}</label>
                          <input
                            type="range"
                            min="0"
                            max="0.9"
                            step="0.01"
                            value={editingRoi.x}
                            onChange={(e) => setEditingRoi({ ...editingRoi, x: parseFloat(e.target.value) })}
                            className="w-full h-1.5 bg-bambu-dark-tertiary rounded-lg cursor-pointer accent-green-500"
                          />
                          <span className="text-xs text-bambu-gray">{Math.round(editingRoi.x * 100)}%</span>
                        </div>
                        <div>
                          <label className="text-xs text-bambu-gray">{t('printers.roi.yStart')}</label>
                          <input
                            type="range"
                            min="0"
                            max="0.9"
                            step="0.01"
                            value={editingRoi.y}
                            onChange={(e) => setEditingRoi({ ...editingRoi, y: parseFloat(e.target.value) })}
                            className="w-full h-1.5 bg-bambu-dark-tertiary rounded-lg cursor-pointer accent-green-500"
                          />
                          <span className="text-xs text-bambu-gray">{Math.round(editingRoi.y * 100)}%</span>
                        </div>
                        <div>
                          <label className="text-xs text-bambu-gray">{t('printers.width')}</label>
                          <input
                            type="range"
                            min="0.1"
                            max="1"
                            step="0.01"
                            value={editingRoi.w}
                            onChange={(e) => setEditingRoi({ ...editingRoi, w: parseFloat(e.target.value) })}
                            className="w-full h-1.5 bg-bambu-dark-tertiary rounded-lg cursor-pointer accent-green-500"
                          />
                          <span className="text-xs text-bambu-gray">{Math.round(editingRoi.w * 100)}%</span>
                        </div>
                        <div>
                          <label className="text-xs text-bambu-gray">{t('printers.height')}</label>
                          <input
                            type="range"
                            min="0.1"
                            max="1"
                            step="0.01"
                            value={editingRoi.h}
                            onChange={(e) => setEditingRoi({ ...editingRoi, h: parseFloat(e.target.value) })}
                            className="w-full h-1.5 bg-bambu-dark-tertiary rounded-lg cursor-pointer accent-green-500"
                          />
                          <span className="text-xs text-bambu-gray">{Math.round(editingRoi.h * 100)}%</span>
                        </div>
                      </div>
                      <p className="text-xs text-bambu-gray">
                        {t('printers.roi.instruction')}
                      </p>
                    </div>
                  ) : (
                    <p className="text-xs text-bambu-gray">
                      Current: X={Math.round((plateCheckResult.roi?.x || 0.15) * 100)}%, Y={Math.round((plateCheckResult.roi?.y || 0.35) * 100)}%,
                      W={Math.round((plateCheckResult.roi?.w || 0.70) * 100)}%, H={Math.round((plateCheckResult.roi?.h || 0.55) * 100)}%
                    </p>
                  )}
                </div>
              )}
            </div>
            <div className="flex justify-end gap-2 p-4 border-t border-bambu-dark-tertiary">
              {plateCheckResult.needs_calibration ? (
                <>
                  <Button variant="ghost" onClick={() => closePlateCheckModal()}>
                    {t('common.cancel')}
                  </Button>
                  <Button
                    onClick={() => handleCalibratePlate()}
                    disabled={isCalibrating}
                  >
                    {isCalibrating ? (
                      <>
                        <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                        Calibrating...
                      </>
                    ) : (
                      'Calibrate Empty Plate'
                    )}
                  </Button>
                </>
              ) : (
                <>
                  <Button variant="ghost" onClick={() => handleCalibratePlate()} disabled={isCalibrating}>
                    {isCalibrating ? 'Adding...' : `Add Reference (${plateReferences?.references.length || 0}/${plateReferences?.max_references || 5})`}
                  </Button>
                  <Button onClick={() => closePlateCheckModal()}>
                    Close
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Power On Confirmation */}
      {showPowerOnConfirm && smartPlug && (
        <ConfirmModal
          title={t('printers.confirm.powerOnTitle')}
          message={t('printers.confirm.powerOnMessage', { name: printer.name })}
          confirmText={t('printers.confirm.powerOnButton')}
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
          confirmText={t('printers.confirm.powerOffButton')}
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

      {/* Skip Objects Modal */}
      <SkipObjectsModal
        printerId={printer.id}
        isOpen={showSkipObjectsModal}
        onClose={() => setShowSkipObjectsModal(false)}
      />

      {/* HMS Error Modal */}
      {showHMSModal && (
        <HMSErrorModal
          printerName={printer.name}
          errors={status?.hms_errors || []}
          onClose={() => setShowHMSModal(false)}
          printerId={printer.id}
          hasPermission={hasPermission}
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
          tagUid={linkSpoolModal.tagUid}
          trayUuid={linkSpoolModal.trayUuid}
          printerId={linkSpoolModal.printerId}
          amsId={linkSpoolModal.amsId}
          trayId={linkSpoolModal.trayId}
        />
      )}

      {/* Assign Spool Modal */}
      {assignSpoolModal && (
        <AssignSpoolModal
          isOpen={!!assignSpoolModal}
          onClose={() => setAssignSpoolModal(null)}
          printerId={assignSpoolModal.printerId}
          amsId={assignSpoolModal.amsId}
          trayId={assignSpoolModal.trayId}
          trayInfo={assignSpoolModal.trayInfo}
        />
      )}

      {/* Configure AMS Slot Modal */}
      {configureSlotModal && (
        <ConfigureAmsSlotModal
          isOpen={!!configureSlotModal}
          onClose={() => setConfigureSlotModal(null)}
          printerId={printer.id}
          slotInfo={configureSlotModal}
          printerModel={mapModelCode(printer.model) || undefined}
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
    location: '',
    auto_archive: true,
  });

  // Discovery state
  const [discovering, setDiscovering] = useState(false);
  const [discovered, setDiscovered] = useState<DiscoveredPrinter[]>([]);
  const [discoveryError, setDiscoveryError] = useState('');
  const [hasScanned, setHasScanned] = useState(false);
  const [isDocker, setIsDocker] = useState(false);
  const [detectedSubnets, setDetectedSubnets] = useState<string[]>([]);
  const [subnet, setSubnet] = useState('');
  const [scanProgress, setScanProgress] = useState({ scanned: 0, total: 0 });

  // Fetch discovery info on mount
  useEffect(() => {
    discoveryApi.getInfo().then(info => {
      setIsDocker(info.is_docker);
      if (info.subnets.length > 0) {
        setDetectedSubnets(info.subnets);
        setSubnet(info.subnets[0]);
      }
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
      setDiscoveryError(e instanceof Error ? e.message : t('printers.discovery.failedToStart'));
      setDiscovering(false);
      setHasScanned(true);
    }
  };

  // Reuse module-level mapModelCode

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
      discoveryApi.stopDiscovery().catch(() => { });
      discoveryApi.stopSubnetScan().catch(() => { });
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
                {detectedSubnets.length > 0 ? (
                  <select
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                    value={subnet}
                    onChange={(e) => setSubnet(e.target.value)}
                    disabled={discovering}
                  >
                    {detectedSubnets.map(s => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                ) : (
                  <input
                    type="text"
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none text-sm"
                    value={subnet}
                    onChange={(e) => setSubnet(e.target.value)}
                    placeholder="192.168.1.0/24"
                    disabled={discovering}
                  />
                )}
                <p className="mt-1 text-xs text-bambu-gray">
                  {t('printers.discovery.dockerNote')}
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
                    ? t('printers.discovery.scanProgress', { scanned: scanProgress.scanned, total: scanProgress.total })
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
                        {mapModelCode(printer.model) || t('printers.discovery.unknown')} • {printer.ip_address}
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
                {isDocker ? t('printers.discovery.noPrintersFoundSubnet') : t('printers.discovery.noPrintersFoundNetwork')}
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
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.name')}</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('printers.modal.myPrinter')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.ipAddress')}</label>
              <input
                type="text"
                required
                pattern="(\d{1,3}(\.\d{1,3}){3}|[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                placeholder="192.168.1.100 or printer.local"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.serialNumber')}</label>
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
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.accessCode')}</label>
              <input
                type="password"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder={t('printers.modal.fromPrinterSettings')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.modal.modelOptional')}</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model || ''}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">{t('printers.modal.selectModel')}</option>
                <optgroup label="H2 Series">
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2D Pro">H2D Pro</option>
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
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.modal.locationGroup')}</label>
              <input
                type="text"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.location || ''}
                onChange={(e) => setForm({ ...form, location: e.target.value })}
                placeholder={t('printers.modal.locationPlaceholder')}
              />
              <p className="text-xs text-bambu-gray mt-1">{t('printers.locationHelp')}</p>
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
                {t('printers.modal.autoArchiveLabel')}
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
  const { hasPermission } = useAuth();
  const canUpdate = hasPermission('firmware:update');
  const [uploadStatus, setUploadStatus] = useState<FirmwareUploadStatus | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [pollInterval, setPollInterval] = useState<NodeJS.Timeout | null>(null);

  // Prepare check query (only when update available and user can update)
  const { data: prepareInfo, isLoading: isPreparing } = useQuery({
    queryKey: ['firmwarePrepare', printer.id],
    queryFn: () => firmwareApi.prepareUpload(printer.id),
    staleTime: 30000,
    enabled: firmwareInfo.update_available && canUpdate,
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
              showToast(t('printers.firmwareModal.uploadedToast'), 'success');
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
      showToast(t('printers.firmwareModal.uploadFailed', { error: error.message }), 'error');
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
            <div className={`p-2 rounded-full ${firmwareInfo.update_available ? 'bg-orange-500/20' : 'bg-status-ok/20'}`}>
              {firmwareInfo.update_available
                ? <Download className="w-5 h-5 text-orange-400" />
                : <CheckCircle className="w-5 h-5 text-status-ok" />}
            </div>
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-white">
                {firmwareInfo.update_available ? t('printers.firmwareModal.title') : t('printers.firmwareModal.titleUpToDate')}
              </h3>
              <p className="text-sm text-bambu-gray mt-1">
                {printer.name}
              </p>
            </div>
          </div>

          {/* Version Info */}
          <div className="bg-bambu-dark rounded-lg p-3 mb-4">
            <div className="flex justify-between items-center text-sm">
              <span className="text-bambu-gray">{t('printers.firmwareModal.currentVersion')}</span>
              <span className={`font-mono ${firmwareInfo.update_available ? 'text-white' : 'text-status-ok'}`}>
                {firmwareInfo.current_version || t('common.unknown')}
              </span>
            </div>
            {firmwareInfo.update_available && (
              <div className="flex justify-between items-center text-sm mt-1">
                <span className="text-bambu-gray">{t('printers.firmwareModal.latestVersion')}</span>
                <span className="text-orange-400 font-mono">{firmwareInfo.latest_version}</span>
              </div>
            )}
            {firmwareInfo.release_notes && (
              <details className="mt-3 text-sm" open={!firmwareInfo.update_available}>
                <summary className={`cursor-pointer hover:underline ${firmwareInfo.update_available ? 'text-orange-400' : 'text-status-ok'}`}>
                  {t('printers.firmwareModal.releaseNotes')}
                </summary>
                <div className="mt-2 text-bambu-gray text-xs max-h-40 overflow-y-auto whitespace-pre-wrap">
                  {firmwareInfo.release_notes}
                </div>
              </details>
            )}
          </div>

          {/* Status / Progress (only when update available) */}
          {!firmwareInfo.update_available ? null : isPreparing ? (
            <div className="flex items-center gap-2 text-bambu-gray text-sm mb-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('printers.firmwareModal.checkingPrereqs')}
            </div>
          ) : prepareInfo && !isUploading && !uploadStatus ? (
            <div className="mb-4">
              {prepareInfo.can_proceed ? (
                <div className="flex items-center gap-2 text-bambu-green text-sm">
                  <Box className="w-4 h-4" />
                  {t('printers.firmwareModal.sdCardReady')}
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
                  className={`h-2 rounded-full transition-all ${uploadStatus.status === 'error' ? 'bg-status-error' :
                    uploadStatus.status === 'complete' ? 'bg-status-ok' : 'bg-orange-500'
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
                {t('printers.firmwareModal.uploadedSuccess')}
              </p>
              <p className="text-xs text-bambu-gray">
                {t('printers.firmwareModal.applyInstructions')}
              </p>
              <ol className="text-xs text-bambu-gray mt-1 list-decimal list-inside space-y-1">
                <li dangerouslySetInnerHTML={{ __html: t('printers.firmwareModal.step1') }} />
                <li dangerouslySetInnerHTML={{ __html: t('printers.firmwareModal.step2') }} />
                <li dangerouslySetInnerHTML={{ __html: t('printers.firmwareModal.step3') }} />
                <li>{t('printers.firmwareModal.step4')}</li>
              </ol>
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onClose}>
              {uploadStatus?.status === 'complete' ? t('printers.firmwareModal.done') : t('common.cancel')}
            </Button>
            {prepareInfo?.can_proceed && !isUploading && uploadStatus?.status !== 'complete' && canUpdate && (
              <Button
                onClick={handleStartUpload}
                disabled={uploadMutation.isPending}
              >
                {uploadMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin mr-2" />
                    {t('printers.firmwareModal.starting')}
                  </>
                ) : (
                  <>
                    <Download className="w-4 h-4 mr-2" />
                    {t('printers.firmwareModal.uploadFirmware')}
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
  const { showToast } = useToast();
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
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToUpdate'), 'error'),
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
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.name')}</label>
              <input
                type="text"
                required
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('printers.modal.myPrinter')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.ipAddress')}</label>
              <input
                type="text"
                required
                pattern="(\d{1,3}(\.\d{1,3}){3}|[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.ip_address}
                onChange={(e) => setForm({ ...form, ip_address: e.target.value })}
                placeholder="192.168.1.100 or printer.local"
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.serialNumber')}</label>
              <input
                type="text"
                disabled
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray cursor-not-allowed"
                value={printer.serial_number}
              />
              <p className="text-xs text-bambu-gray mt-1">{t('printers.serialCannotBeChanged')}</p>
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.accessCode')}</label>
              <input
                type="password"
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.access_code}
                onChange={(e) => setForm({ ...form, access_code: e.target.value })}
                placeholder={t('printers.accessCodePlaceholder')}
              />
            </div>
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('printers.model')}</label>
              <select
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              >
                <option value="">{t('printers.modal.selectModel')}</option>
                <optgroup label="H2 Series">
                  <option value="H2C">H2C</option>
                  <option value="H2D">H2D</option>
                  <option value="H2D Pro">H2D Pro</option>
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
                placeholder={t('printers.modal.locationPlaceholder')}
              />
              <p className="text-xs text-bambu-gray mt-1">{t('printers.locationHelp')}</p>
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
                {t('printers.modal.autoArchiveLabel')}
              </label>
            </div>
            <div className="flex gap-3 pt-4">
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                {t('common.cancel')}
              </Button>
              <Button type="submit" className="flex-1" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? t('common.saving') : t('printers.modal.saveChanges')}
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
            className={`text-xs px-1.5 py-0.5 rounded ${plugStatus.state === 'ON'
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
        className={`px-2 py-1 text-xs rounded transition-colors flex items-center gap-1 ${plugStatus?.state === 'ON'
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
  // On small screens, clamp cardSize to max 2 (M) since L/XL are hidden
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)');
    const handler = () => {
      if (!mql.matches) {
        setCardSize(prev => {
          if (prev > 2) {
            localStorage.setItem('printerCardSize', '2');
            return 2;
          }
          return prev;
        });
      }
    };
    handler();
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);
  // Derive viewMode from cardSize: S=compact, M/L/XL=expanded
  const viewMode: ViewMode = cardSize === 1 ? 'compact' : 'expanded';
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
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

  // Fetch linked spools map (tag -> spool_id) to know which spools are already in Spoolman
  const { data: linkedSpoolsData } = useQuery({
    queryKey: ['linked-spools'],
    queryFn: api.getLinkedSpools,
    enabled: !!spoolmanEnabled,
    staleTime: 30 * 1000, // 30 seconds
  });
  const linkedSpools = linkedSpoolsData?.linked;

  // Fetch spool assignments for inventory feature
  const { data: spoolAssignments } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    staleTime: 30 * 1000,
  });

  const unassignMutation = useMutation({
    mutationFn: ({ printerId, amsId, trayId }: { printerId: number; amsId: number; trayId: number }) =>
      api.unassignSpool(printerId, amsId, trayId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
    },
  });

  // Helper to find assignment for a specific slot
  const getAssignment = (printerId: number, amsId: number | string, trayId: number | string): SpoolAssignment | undefined => {
    return spoolAssignments?.find(
      (a) => a.printer_id === printerId && a.ams_id === Number(amsId) && a.tray_id === Number(trayId)
    );
  };

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
    onError: (error: Error) => showToast(error.message || t('printers.toast.failedToAdd'), 'error'),
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
      const location = printer.location || 'Ungrouped';
      if (!groups[location]) groups[location] = [];
      groups[location].push(printer);
    });
    return groups;
  }, [sortBy, sortedPrinters]);

  return (
    <div className="p-4 md:p-8">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">{t('printers.title')}</h1>
          <StatusSummaryBar printers={printers} />
        </div>
        <div className="flex items-center gap-2 sm:gap-3 flex-wrap">
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
                  className={`px-2 py-1.5 text-xs font-medium transition-colors ${label === 'L' || label === 'XL' ? 'hidden lg:block' : ''} ${index === 0 ? 'rounded-l-lg' : ''
                    } ${index === cardSizeLabels.length - 1 ? 'rounded-r-lg' : ''
                    } ${label === 'M' ? 'rounded-r-lg lg:rounded-r-none' : ''
                    } ${isSelected
                      ? 'bg-bambu-green text-white'
                      : 'text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white'
                    }`}
                  title={label === 'S' ? t('printers.cardSize.small') : label === 'M' ? t('printers.cardSize.medium') : label === 'L' ? t('printers.cardSize.large') : t('printers.cardSize.extraLarge')}
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
                {t('printers.powerOn')}
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
                      {t('printers.offlinePrintersWithPlugs')}
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
          <Button
            onClick={() => setShowAddModal(true)}
            disabled={!hasPermission('printers:create')}
            title={!hasPermission('printers:create') ? t('printers.permission.noAdd') : undefined}
          >
            <Plus className="w-4 h-4" />
            {t('printers.addPrinter')}
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-bambu-gray">{t('common.loading')}</div>
      ) : printers?.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12">
            <p className="text-bambu-gray mb-4">{t('printers.noPrintersConfigured')}</p>
            <Button
              onClick={() => setShowAddModal(true)}
              disabled={!hasPermission('printers:create')}
              title={!hasPermission('printers:create') ? t('printers.permission.noAdd') : undefined}
            >
              <Plus className="w-4 h-4" />
              {t('printers.addPrinter')}
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
              <div className={`grid gap-4 ${getGridClasses()}`}>
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
                    linkedSpools={linkedSpools}
                    spoolmanUrl={spoolmanStatus?.url}
                    onGetAssignment={getAssignment}
                    onUnassignSpool={(pid, aid, tid) => unassignMutation.mutate({ printerId: pid, amsId: aid, trayId: tid })}
                    timeFormat={settings?.time_format || 'system'}
                    cameraViewMode={settings?.camera_view_mode || 'window'}
                    onOpenEmbeddedCamera={(id, name) => setEmbeddedCameraPrinters(prev => new Map(prev).set(id, { id, name }))}
                    checkPrinterFirmware={settings?.check_printer_firmware !== false}
                  />
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Regular grid view */
        <div className={`grid gap-4 ${getGridClasses()}`}>
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
              linkedSpools={linkedSpools}
              spoolmanUrl={spoolmanStatus?.url}
              onGetAssignment={getAssignment}
              onUnassignSpool={(pid, aid, tid) => unassignMutation.mutate({ printerId: pid, amsId: aid, trayId: tid })}
              amsThresholds={settings ? {
                humidityGood: Number(settings.ams_humidity_good) || 40,
                humidityFair: Number(settings.ams_humidity_fair) || 60,
                tempGood: Number(settings.ams_temp_good) || 28,
                tempFair: Number(settings.ams_temp_fair) || 35,
              } : undefined}
              timeFormat={settings?.time_format || 'system'}
              cameraViewMode={settings?.camera_view_mode || 'window'}
              onOpenEmbeddedCamera={(id, name) => setEmbeddedCameraPrinters(prev => new Map(prev).set(id, { id, name }))}
              checkPrinterFirmware={settings?.check_printer_firmware !== false}
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
