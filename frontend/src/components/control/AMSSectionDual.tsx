import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { PrinterStatus, AMSUnit, AMSTray, KProfile } from '../../api/client';
import { Loader2, ChevronDown, ChevronUp, RotateCw } from 'lucide-react';
import { AMSHumidityModal } from './AMSHumidityModal';
import { AMSMaterialsModal } from './AMSMaterialsModal';
import { useToast } from '../../contexts/ToastContext';
import { useAmsOperations } from './useAmsOperations';


interface AMSSectionDualProps {
  printerId: number;
  printerModel: string;
  status: PrinterStatus | null | undefined;
  nozzleCount: number;
}

function hexToRgb(hex: string | null): string {
  if (!hex) return 'rgb(128, 128, 128)';
  const cleanHex = hex.replace('#', '').substring(0, 6);
  const rParsed = parseInt(cleanHex.substring(0, 2), 16);
  const gParsed = parseInt(cleanHex.substring(2, 4), 16);
  const bParsed = parseInt(cleanHex.substring(4, 6), 16);
  const r = isNaN(rParsed) ? 128 : rParsed;
  const g = isNaN(gParsed) ? 128 : gParsed;
  const b = isNaN(bParsed) ? 128 : bParsed;
  return `rgb(${r}, ${g}, ${b})`;
}

function isLightColor(hex: string | null): boolean {
  if (!hex) return false;
  const cleanHex = hex.replace('#', '').substring(0, 6);
  // Ensure we have a valid 6-char hex
  if (cleanHex.length < 6) return false;
  const rParsed = parseInt(cleanHex.substring(0, 2), 16);
  const gParsed = parseInt(cleanHex.substring(2, 4), 16);
  const bParsed = parseInt(cleanHex.substring(4, 6), 16);
  // If any parsing fails, treat as dark
  if (isNaN(rParsed) || isNaN(gParsed) || isNaN(bParsed)) return false;
  // Use relative luminance formula (WCAG)
  const luminance = (0.299 * rParsed + 0.587 * gParsed + 0.114 * bParsed) / 255;
  // Lower threshold (0.45) to ensure more colors get white text for better contrast
  return luminance > 0.45;
}

// Bambu Lab color codes from tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
const BAMBU_COLOR_CODES: Record<string, string> = {
  'Y2': 'Sunflower Yellow', 'Y0': 'Yellow', 'Y1': 'Lemon Yellow',
  'K0': 'Black', 'W0': 'White', 'W1': 'Ivory White',
  'R0': 'Red', 'R1': 'Scarlet Red', 'R2': 'Magenta',
  'B0': 'Blue', 'B1': 'Navy Blue', 'B2': 'Sky Blue', 'B3': 'Cyan',
  'G0': 'Green', 'G1': 'Grass Green', 'G2': 'Jade Green',
  'O0': 'Orange', 'O1': 'Mandarin Orange',
  'P0': 'Purple', 'P1': 'Pink', 'P2': 'Sakura Pink',
  'N0': 'Gray', 'N1': 'Silver Gray', 'N2': 'Charcoal',
  'D0': 'Brown', 'D1': 'Chocolate',
  'T0': 'Titan Gray', 'T1': 'Jade White',
};

function getColorNameFromTrayId(trayIdName: string | null): string | null {
  if (!trayIdName) return null;
  // tray_id_name format: "A00-Y2" or "G02-K0" - color code is after the dash
  const parts = trayIdName.split('-');
  if (parts.length < 2) return null;
  const colorCode = parts[1];
  return BAMBU_COLOR_CODES[colorCode] || null;
}

// Find best matching K-profile for a filament using cascading search
// Priority: 1) tray_sub_brands + color, 2) tray_sub_brands only, 3) tray_type only, 4) default
function findBestKProfile(
  profiles: KProfile[],
  traySubBrands: string | null,
  trayType: string | null,
  colorName: string | null
): KProfile | null {
  if (!profiles.length) return null;

  const subBrands = traySubBrands?.toLowerCase() || '';
  const type = trayType?.toUpperCase() || '';
  const color = colorName?.toLowerCase() || '';

  // Priority 1: Match tray_sub_brands AND color (e.g., "PLA Basic" + "Sunflower Yellow")
  if (subBrands && color) {
    const exactColorMatch = profiles.find(p => {
      const name = p.name.toLowerCase();
      return name.includes(subBrands) && name.includes(color);
    });
    if (exactColorMatch) return exactColorMatch;
  }

  // Priority 2: Match tray_sub_brands without color (e.g., "PLA Basic" in "High Flow_Bambu PLA Basic")
  if (subBrands) {
    const subBrandsMatch = profiles.find(p =>
      p.name.toLowerCase().includes(subBrands)
    );
    if (subBrandsMatch) return subBrandsMatch;
  }

  // Priority 3: Match filament type in profile name (e.g., "PLA" in "High Flow_Bambu PLA Basic")
  if (type) {
    const typeMatches = profiles.filter(p =>
      p.name.toUpperCase().includes(type)
    );

    if (typeMatches.length > 0) {
      // Prefer "Basic" profiles for generic type matching
      const basicMatch = typeMatches.find(p =>
        p.name.toLowerCase().includes('basic')
      );
      if (basicMatch) return basicMatch;

      return typeMatches[0];
    }
  }

  // Priority 4: Default profile
  const defaultProfile = profiles.find(p =>
    p.name.toLowerCase() === 'default' || p.slot_id === 0
  );
  return defaultProfile || null;
}

// Single humidity icon that fills based on level
// <25% = empty (dry/good)
// <40% = half filled
// >=40% = full (wet/bad)
function HumidityIcon({ humidity }: { humidity: number }) {
  const getIconSrc = (): string => {
    if (humidity < 25) return '/icons/humidity-empty.svg';
    if (humidity < 40) return '/icons/humidity-half.svg';
    return '/icons/humidity-full.svg';
  };

  return (
    <img
      src={getIconSrc()}
      alt=""
      className="w-2.5 h-[14px]"
    />
  );
}

// Filament change progress card - appears during load/unload operations
interface FilamentChangeCardProps {
  isLoading: boolean;  // true = loading, false = unloading
  amsStatusMain: number;  // AMS status: 0=idle, 1=filament_change, 2=rfid_identifying, etc.
  amsStatusSub: number;  // ams_status_sub from MQTT - step within filament change operation
  trayNow: number;  // Currently loaded tray (255 = none, 254 = external)
  targetTrayId: number | null;  // Target tray we're trying to load (null for unload)
  onComplete: () => void;  // Called when operation completes
  onRetry?: () => void;
}

interface StepInfo {
  label: string;
  status: 'completed' | 'in_progress' | 'pending';
  stepNumber: number;
}

function FilamentChangeCard({ isLoading, amsStatusMain, amsStatusSub, trayNow, targetTrayId, onComplete, onRetry }: FilamentChangeCardProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isCompleted, setIsCompleted] = useState(false);
  const prevAmsStatusRef = useRef(amsStatusMain);
  const prevTrayNowRef = useRef(trayNow);
  const completionTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // ams_status_sub values for filament change steps
  // Observed progression: 5 -> 6 -> 2 -> 7 -> 0
  // 2: Heating nozzle
  // 3: AMS feeding filament to hub
  // 4: Retraction / extruder pulling filament
  // 5: Initial filament push / preparation
  // 6: Load verification / extruder pushing
  // 7: Purging
  const SUB_HEATING = 2;
  const SUB_FEEDING = 3;
  const SUB_RETRACT = 4;
  const SUB_PUSH_PREP = 5;
  const SUB_PUSH = 6;
  const SUB_PURGE = 7;

  // Log status updates for debugging
  useEffect(() => {
    console.log(`[FilamentChangeCard] Status: main=${amsStatusMain}, sub=${amsStatusSub}, trayNow=${trayNow}, isLoading=${isLoading}`);
  }, [amsStatusMain, amsStatusSub, trayNow, isLoading]);

  // Detect completion via ams_status_main transition from 1 (filament_change) to 0 (idle)
  // Also use tray_now as a secondary indicator
  useEffect(() => {
    const wasActive = prevAmsStatusRef.current === 1;
    const isNowIdle = amsStatusMain === 0;
    const trayChanged = trayNow !== prevTrayNowRef.current;

    // Primary completion detection: ams_status_main transitions from 1 to 0
    if (wasActive && isNowIdle) {
      console.log(`[FilamentChangeCard] ams_status_main transitioned 1->0, operation complete!`);
      setIsCompleted(true);
      // Auto-close after brief delay
      completionTimeoutRef.current = setTimeout(() => {
        onComplete();
      }, 1500);
    }
    // Secondary completion detection: tray_now matches target (for load) or becomes 255 (for unload)
    else if (trayChanged) {
      if (isLoading && targetTrayId !== null && trayNow === targetTrayId) {
        console.log(`[FilamentChangeCard] Load completed! tray_now=${trayNow} matches target=${targetTrayId}`);
        setIsCompleted(true);
        completionTimeoutRef.current = setTimeout(() => {
          onComplete();
        }, 1500);
      } else if (!isLoading && trayNow === 255) {
        console.log(`[FilamentChangeCard] Unload completed! tray_now=255 (no filament)`);
        setIsCompleted(true);
        completionTimeoutRef.current = setTimeout(() => {
          onComplete();
        }, 1500);
      }
    }

    prevAmsStatusRef.current = amsStatusMain;
    prevTrayNowRef.current = trayNow;
  }, [amsStatusMain, trayNow, isLoading, targetTrayId, onComplete]);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (completionTimeoutRef.current) {
        clearTimeout(completionTimeoutRef.current);
      }
    };
  }, []);

  // Determine step based on ams_status_sub from MQTT
  // ams_status_sub values indicate the current step in filament change:
  // Load sequence (actual order): 3/6 (push) -> 2 (heating) -> 7 (purging)
  // Unload sequence: 2 (heating) -> 4 (retracting)
  const getStepFromAmsStatusSub = (): number => {
    if (isCompleted) return 99; // All done

    // If not in filament change mode, not started
    if (amsStatusMain !== 1) return 0;

    if (isLoading) {
      // Loading sequence: Push -> Heat -> Purge (matches Bambu Studio/OrcaSlicer display)
      // Observed progression: 5 -> 6 -> 2 -> 7
      // Map sub status to steps: 5/6 -> step 1, 2 -> step 2, 7 -> step 3
      if (amsStatusSub === SUB_PUSH_PREP || amsStatusSub === SUB_PUSH || amsStatusSub === SUB_FEEDING) return 1; // Step 1: Pushing
      if (amsStatusSub === SUB_HEATING) return 2; // Step 2: Heating
      if (amsStatusSub === SUB_PURGE) return 3; // Step 3: Purging
      // Default to step 1 when in filament_change mode
      return 1;
    } else {
      // Unloading sequence: Heat -> Retract
      // Map sub status to steps: 2 -> step 1, 4 -> step 2
      if (amsStatusSub === SUB_HEATING) return 1; // Step 1: Heating
      if (amsStatusSub === SUB_RETRACT) return 2; // Step 2: Retracting
      // Default to step 1 when in filament_change mode
      return 1;
    }
  };

  // Get current step from ams_status_sub
  const currentStep = getStepFromAmsStatusSub();

  // Debug: log step calculation
  console.log(`[FilamentChangeCard] Step calculation: currentStep=${currentStep}, amsStatusSub=${amsStatusSub}`);

  // Determine step status based on ams_status_sub
  const getLoadingSteps = (): StepInfo[] => {
    // Loading sequence: Push -> Heat -> Purge (matches Bambu Studio/OrcaSlicer display order)
    // ams_status_sub progression: 3/6 (push) -> 2 (heating) -> 7 (purging)
    let step1Status: 'completed' | 'in_progress' | 'pending' = 'pending';
    let step2Status: 'completed' | 'in_progress' | 'pending' = 'pending';
    let step3Status: 'completed' | 'in_progress' | 'pending' = 'pending';

    if (currentStep >= 99) {
      // All completed
      step1Status = 'completed';
      step2Status = 'completed';
      step3Status = 'completed';
    } else if (currentStep >= 3) {
      // Purging - steps 1 & 2 done, step 3 active
      step1Status = 'completed';
      step2Status = 'completed';
      step3Status = 'in_progress';
    } else if (currentStep >= 2) {
      // Heating - step 1 done, step 2 active
      step1Status = 'completed';
      step2Status = 'in_progress';
    } else if (currentStep >= 1) {
      // Pushing - step 1 active
      step1Status = 'in_progress';
    }

    return [
      { label: 'Push new filament into extruder', stepNumber: 1, status: step1Status },
      { label: 'Heat the nozzle', stepNumber: 2, status: step2Status },
      { label: 'Purge old filament', stepNumber: 3, status: step3Status },
    ];
  };

  const getUnloadingSteps = (): StepInfo[] => {
    let step1Status: 'completed' | 'in_progress' | 'pending' = 'pending';
    let step2Status: 'completed' | 'in_progress' | 'pending' = 'pending';

    if (currentStep >= 99) {
      // All completed
      step1Status = 'completed';
      step2Status = 'completed';
    } else if (currentStep >= 2) {
      // Retracting - step 1 done, step 2 active
      step1Status = 'completed';
      step2Status = 'in_progress';
    } else if (currentStep >= 1) {
      // Heating - step 1 active
      step1Status = 'in_progress';
    }

    return [
      { label: 'Heat the nozzle', stepNumber: 1, status: step1Status },
      { label: 'Retract filament from extruder', stepNumber: 2, status: step2Status },
    ];
  };

  const steps = isLoading ? getLoadingSteps() : getUnloadingSteps();
  const title = isLoading ? 'Loading' : 'Unloading';
  const headerText = isLoading ? 'Filament loading...' : 'Filament unloading...';

  return (
    <div className="mt-3 border-l-4 border-bambu-green bg-white dark:bg-bambu-dark-secondary rounded-r-lg overflow-hidden">
      {/* Collapsible header */}
      <button
        onClick={() => setIsCollapsed(!isCollapsed)}
        className="w-full flex items-center justify-between px-4 py-2 text-bambu-green hover:bg-gray-50 dark:hover:bg-bambu-dark-tertiary transition-colors"
      >
        <span className="text-sm font-medium">{headerText}</span>
        {isCollapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
      </button>

      {/* Content */}
      {!isCollapsed && (
        <div className="px-4 pb-4">
          <div className="flex gap-6">
            {/* Steps list */}
            <div className="flex-1">
              <h3 className="text-bambu-green font-semibold mb-3">{title}</h3>
              <div className="space-y-2">
                {steps.map((step) => (
                  <div key={step.stepNumber} className="flex items-center gap-2">
                    {/* Step indicator */}
                    {step.status === 'completed' ? (
                      <div className="w-5 h-5 rounded-full bg-bambu-green flex items-center justify-center flex-shrink-0">
                        <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      </div>
                    ) : step.status === 'in_progress' ? (
                      <div className="w-5 h-5 rounded-full bg-bambu-green flex items-center justify-center flex-shrink-0">
                        <span className="text-white text-xs font-bold">{step.stepNumber}</span>
                      </div>
                    ) : (
                      <div className="w-5 h-5 rounded-full border-2 border-gray-400 dark:border-gray-500 flex items-center justify-center flex-shrink-0">
                        <span className="text-gray-400 dark:text-gray-500 text-xs font-medium">{step.stepNumber}</span>
                      </div>
                    )}
                    {/* Step label */}
                    <span className={`text-sm ${
                      step.status === 'in_progress' ? 'text-gray-900 dark:text-white font-semibold' :
                      step.status === 'completed' ? 'text-gray-500 dark:text-gray-400' : 'text-gray-400 dark:text-gray-500'
                    }`}>
                      {step.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Extruder image */}
            <div className="flex-shrink-0">
              <img
                src="/icons/extruder-change-filament.png"
                alt="Extruder"
                className="w-[150px] h-auto"
              />
            </div>
          </div>

          {/* Retry button */}
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-3 px-4 py-1.5 border border-bambu-gray rounded-full text-sm text-bambu-gray hover:bg-bambu-dark-tertiary transition-colors flex items-center gap-1.5"
            >
              <RotateCw className="w-3.5 h-3.5" />
              Retry
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface AMSPanelContentProps {
  units: AMSUnit[];
  side: 'left' | 'right';
  isPrinting: boolean;
  selectedAmsIndex: number;
  onSelectAms: (index: number) => void;
  selectedTray: number | null;
  onSelectTray: (trayId: number | null) => void;
  onHumidityClick: (humidity: number, temp: number) => void;
  onSlotRefresh: (amsId: number, slotId: number) => void;
  onEyeClick: (tray: AMSTray, slotLabel: string, amsId: number) => void;
  refreshingSlot: { amsId: number; trayId: number } | null;
  getKValue: (tray: AMSTray) => number | null;
  kProfilesLoaded: boolean;
}

// Panel content - NO wiring, just slots and info
// Get slot label based on AMS unit ID and tray index
// Regular AMS (ID 0-3): A1, A2, A3, A4 / B1, B2, B3, B4 / etc.
// AMS-HT (ID >= 128): HT-A, HT-B (for first HT unit), HT2-A, HT2-B (for second), etc.
function getSlotLabel(amsId: number, trayIndex: number): string {
  if (amsId >= 128) {
    // AMS-HT unit - uses HT-A, HT-B naming
    const htUnitNumber = amsId - 128; // 0 for first HT, 1 for second, etc.
    const slotLetter = String.fromCharCode(65 + trayIndex); // A, B
    if (htUnitNumber === 0) {
      return `HT-${slotLetter}`;
    }
    return `HT${htUnitNumber + 1}-${slotLetter}`;
  }
  // Regular AMS - uses A1, B2, etc. naming
  const prefix = String.fromCharCode(65 + amsId); // 65 is ASCII for 'A'
  return `${prefix}${trayIndex + 1}`;
}

// Check if AMS unit is an AMS-HT (ID >= 128)
function isAmsHT(amsId: number): boolean {
  return amsId >= 128;
}

function AMSPanelContent({
  units,
  side,
  isPrinting,
  selectedAmsIndex,
  onSelectAms,
  selectedTray,
  onSelectTray,
  onHumidityClick,
  onSlotRefresh,
  onEyeClick,
  refreshingSlot,
  getKValue,
  kProfilesLoaded,
}: AMSPanelContentProps) {
  const selectedUnit = units[selectedAmsIndex];
  const isHT = selectedUnit ? isAmsHT(selectedUnit.id) : false;

  return (
    <div className="flex-1 min-w-0">
      {/* AMS Tab Selectors */}
      <div className="flex gap-1.5 mb-2.5 p-1.5 bg-gray-300 dark:bg-bambu-dark rounded-lg">
        {units.map((unit, index) => (
          <button
            key={unit.id}
            onClick={() => onSelectAms(index)}
            className={`flex items-center p-1.5 rounded border-2 transition-colors ${
              selectedAmsIndex === index
                ? 'border-bambu-green bg-white dark:bg-bambu-dark-tertiary'
                : 'bg-gray-200 dark:bg-bambu-dark-secondary border-transparent hover:border-bambu-gray'
            }`}
          >
            <div className="flex gap-0.5">
              {unit.tray.map((tray) => (
                <div
                  key={tray.id}
                  className="w-2.5 h-2.5 rounded-full"
                  style={{
                    backgroundColor: tray.tray_color ? hexToRgb(tray.tray_color) : '#808080',
                  }}
                />
              ))}
            </div>
          </button>
        ))}
      </div>

      {/* AMS Content */}
      {selectedUnit && (
        <div className="bg-gray-100 dark:bg-bambu-dark-secondary rounded-[10px] p-2.5">
          {/* AMS Header - Humidity & Temp - Centered - Clickable */}
          <button
            onClick={() => onHumidityClick(selectedUnit.humidity ?? 0, selectedUnit.temp ?? 0)}
            className="flex items-center justify-center gap-4 text-xs text-bambu-gray mb-2.5 w-full py-1 hover:bg-gray-50 dark:hover:bg-bambu-dark-tertiary rounded-md transition-colors cursor-pointer"
          >
            {selectedUnit.humidity !== null && (
              <span className="flex items-center gap-1.5">
                <HumidityIcon humidity={selectedUnit.humidity} />
                {selectedUnit.humidity} %
              </span>
            )}
            {selectedUnit.temp !== null && (
              <span className="flex items-center gap-1.5">
                <img src="/icons/temperature.svg" alt="" className="w-3.5 icon-theme" />
                {selectedUnit.temp}°C
              </span>
            )}
          </button>

          {/* Slot Labels */}
          <div className={`flex gap-2 mb-1.5 ${isHT ? 'justify-start pl-2' : 'justify-center'}`}>
            {selectedUnit.tray.map((tray, index) => {
              const slotLabel = getSlotLabel(selectedUnit.id, index);
              const isRefreshing = refreshingSlot?.amsId === selectedUnit.id && refreshingSlot?.trayId === tray.id;
              return (
                <button
                  key={tray.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    onSlotRefresh(selectedUnit.id, tray.id);
                  }}
                  disabled={isRefreshing}
                  className={`w-14 flex items-center justify-center gap-0.5 text-[10px] text-bambu-gray px-1.5 py-[3px] bg-bambu-dark rounded-full border border-bambu-dark-tertiary transition-colors ${
                    isRefreshing ? 'opacity-70 cursor-wait' : 'hover:bg-bambu-dark-tertiary'
                  }`}
                >
                  {slotLabel}
                  <img
                    src="/icons/reload.svg"
                    alt=""
                    className={`w-2.5 h-2.5 icon-theme ${isRefreshing ? 'animate-spin' : ''}`}
                  />
                </button>
              );
            })}
          </div>

          {/* AMS Slots - NO wiring here */}
          <div className={`flex gap-2 ${isHT ? 'justify-start pl-2' : 'justify-center'}`}>
            {selectedUnit.tray.map((tray, index) => {
              const globalTrayId = selectedUnit.id * 4 + tray.id;
              const isSelected = selectedTray === globalTrayId;
              const isEmpty = !tray.tray_type || tray.tray_type === '' || tray.tray_type === 'NONE';
              const isLight = isLightColor(tray.tray_color);
              const slotLabel = getSlotLabel(selectedUnit.id, index);

              return (
                <div
                  key={tray.id}
                  onClick={() => {
                    console.log(`[AMSSectionDual] Slot clicked: AMS ${selectedUnit.id}, tray ${tray.id}, globalTrayId: ${globalTrayId}, isEmpty: ${isEmpty}, isPrinting: ${isPrinting}, isSelected: ${isSelected}`);
                    if (!isEmpty && !isPrinting) {
                      onSelectTray(isSelected ? null : globalTrayId);
                    }
                  }}
                  className={`w-14 h-[80px] rounded-md border-2 overflow-hidden transition-all bg-bambu-dark relative ${
                    isSelected
                      ? 'border-bambu-green'
                      : 'border-bambu-dark-tertiary hover:border-bambu-gray'
                  } ${isEmpty ? 'opacity-50' : 'cursor-pointer'}`}
                >
                  {/* Fill level indicator - show for any filament with valid remain data */}
                  {!isEmpty && tray.remain >= 0 && (
                    <div
                      className="absolute bottom-0 left-0 right-0 transition-all"
                      style={{
                        height: `${Math.min(100, Math.max(0, tray.remain))}%`,
                        backgroundColor: hexToRgb(tray.tray_color),
                      }}
                    />
                  )}
                  {/* Full color background only when no valid remain data */}
                  {!isEmpty && tray.remain < 0 && (
                    <div
                      className="absolute inset-0"
                      style={{
                        backgroundColor: hexToRgb(tray.tray_color),
                      }}
                    />
                  )}
                  {/* Striped pattern for empty slots */}
                  {isEmpty && (
                    <div
                      className="absolute inset-0"
                      style={{
                        background: 'repeating-linear-gradient(45deg, #3a3a3a, #3a3a3a 4px, #4a4a4a 4px, #4a4a4a 8px)',
                      }}
                    />
                  )}
                  {/* Content overlay */}
                  <div className="relative w-full h-full flex flex-col items-center justify-end pb-[5px]">
                    <span
                      className="text-[11px] font-semibold"
                      style={{ color: isLight ? '#000000' : '#ffffff' }}
                    >
                      {isEmpty ? '--' : tray.tray_type}
                    </span>
                    {!isEmpty && kProfilesLoaded && (
                      <span
                        className="text-[10px] font-medium"
                        style={{ color: isLight ? 'rgba(0,0,0,0.8)' : 'rgba(255,255,255,0.85)' }}
                      >
                        K {(getKValue(tray) ?? 0.020).toFixed(3)}
                      </span>
                    )}
                    {!isEmpty && (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onEyeClick(tray, slotLabel, selectedUnit.id);
                        }}
                        className={`w-4 h-4 flex items-center justify-center rounded hover:bg-black/20 transition-colors`}
                      >
                        <img
                          src="/icons/eye.svg"
                          alt="Settings"
                          className={`w-3.5 h-3.5 ${isLight ? '' : 'invert'}`}
                          style={{ opacity: 0.8 }}
                        />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* No AMS message */}
      {units.length === 0 && (
        <div className="bg-bambu-dark-secondary rounded-[10px] p-6 text-center text-bambu-gray text-sm">
          No AMS connected to {side} nozzle
        </div>
      )}
    </div>
  );
}

// Unified wiring layer - draws ALL wiring in one place
interface WiringLayerProps {
  isDualNozzle: boolean;
  leftSlotCount: number;  // Number of slots on left panel (4 for regular AMS, 1-2 for AMS-HT)
  rightSlotCount: number; // Number of slots on right panel
  leftIsHT: boolean;      // Is left panel an AMS-HT
  rightIsHT: boolean;     // Is right panel an AMS-HT
  leftActiveSlot?: number | null;   // Currently active slot index on left panel (0-3)
  rightActiveSlot?: number | null;  // Currently active slot index on right panel (0-3)
  leftFilamentColor?: string | null;  // Filament color for left active path
  rightFilamentColor?: string | null; // Filament color for right active path
}

function WiringLayer({
  isDualNozzle,
  leftSlotCount,
  rightSlotCount,
  leftIsHT,
  rightIsHT,
  leftActiveSlot,
  rightActiveSlot,
  leftFilamentColor,
  rightFilamentColor,
}: WiringLayerProps) {
  if (!isDualNozzle) return null;

  // All measurements relative to this container
  // Container spans full width between panels
  // Regular AMS: slots → hub → down → toward center → down to extruder
  // AMS-HT: single slot on left → direct line down to extruder

  // Regular AMS: Slots are w-14 (56px) with gap-2 (8px), 4 slots = 248px total, centered in each ~300px panel
  // Left panel center ~150, slots start at 150 - 124 = 26
  // Slot centers: 26+28=54, 54+64=118, 118+64=182, 182+64=246

  // AMS-HT: Left aligned with pl-2 (8px), slot starts at 8px + 28px = 36px center
  // For 2 slots: 36, 100 (36 + 64)

  // Right panel calculations for regular AMS:
  // Right panel center ~450, slots start at 450 - 124 = 326
  // Slot centers: 326+28=354, 354+64=418, 418+64=482, 482+64=546

  // Right panel AMS-HT: Left aligned, starts at ~308 (300 panel offset + 8px padding)
  // Slot center: 308 + 28 = 336

  // Determine colors for wiring paths
  const defaultColor = '#909090';
  const leftActiveColor = leftFilamentColor ? hexToRgb(leftFilamentColor) : null;
  const rightActiveColor = rightFilamentColor ? hexToRgb(rightFilamentColor) : null;

  // Slot X positions for regular AMS (4 slots)
  const leftSlotX = [54, 118, 182, 246];
  // Right slot positions
  const rightSlotX = [354, 418, 482, 546];

  return (
    <div className="relative w-full pointer-events-none" style={{ height: '120px' }}>
      <svg
        className="absolute inset-0 w-full h-full"
        viewBox="0 0 600 120"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Left panel wiring */}
        {leftIsHT ? (
          <>
            {/* AMS-HT: Simple direct line from slot to extruder */}
            {/* Slot vertical lines - highlight active slot */}
            <line x1="36" y1="0" x2="36" y2="36" stroke={leftActiveSlot === 0 && leftActiveColor ? leftActiveColor : defaultColor} strokeWidth={leftActiveSlot === 0 && leftActiveColor ? 3 : 2} />
            {leftSlotCount > 1 && (
              <line x1="100" y1="0" x2="100" y2="36" stroke={leftActiveSlot === 1 && leftActiveColor ? leftActiveColor : defaultColor} strokeWidth={leftActiveSlot === 1 && leftActiveColor ? 3 : 2} />
            )}
            {leftSlotCount > 1 && (
              <line x1="36" y1="36" x2="100" y2="36" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />
            )}
            {/* Path to extruder - always colored if filament loaded */}
            <line x1={leftSlotCount > 1 ? "68" : "36"} y1="36" x2="288" y2="36" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />
            <line x1="288" y1="36" x2="288" y2="85" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />
          </>
        ) : (
          <>
            {/* Regular AMS: 4 slots with hub */}
            {/* Vertical lines from 4 slots - highlight active slot */}
            {leftSlotX.map((x, i) => (
              <line key={`left-slot-${i}`} x1={x} y1="0" x2={x} y2="14" stroke={leftActiveSlot === i && leftActiveColor ? leftActiveColor : defaultColor} strokeWidth={leftActiveSlot === i && leftActiveColor ? 3 : 2} />
            ))}

            {/* Horizontal bar connecting left slots - highlight from active slot to hub */}
            {leftActiveSlot !== null && leftActiveSlot !== undefined && leftActiveColor ? (
              <>
                {/* Background bar */}
                <line x1="54" y1="14" x2="246" y2="14" stroke={defaultColor} strokeWidth="2" />
                {/* Highlight segment from active slot to hub (center at 150) */}
                <line
                  x1={Math.min(leftSlotX[leftActiveSlot], 150)}
                  y1="14"
                  x2={Math.max(leftSlotX[leftActiveSlot], 150)}
                  y2="14"
                  stroke={leftActiveColor}
                  strokeWidth="3"
                />
              </>
            ) : (
              <line x1="54" y1="14" x2="246" y2="14" stroke={defaultColor} strokeWidth="2" />
            )}

            {/* Left hub */}
            <rect x="136" y="8" width="28" height="14" rx="2" fill={leftActiveColor ?? '#c0c0c0'} stroke={leftActiveColor ?? defaultColor} strokeWidth="1" />

            {/* Vertical from left hub down */}
            <line x1="150" y1="22" x2="150" y2="36" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />

            {/* Horizontal from left hub toward center */}
            <line x1="150" y1="36" x2="288" y2="36" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />

            {/* Vertical down to left extruder inlet */}
            <line x1="288" y1="36" x2="288" y2="85" stroke={leftActiveColor ?? defaultColor} strokeWidth={leftActiveColor ? 3 : 2} />
          </>
        )}

        {/* Right panel wiring */}
        {rightIsHT ? (
          <>
            {/* AMS-HT: Simple direct line from slot to extruder */}
            <line x1="336" y1="0" x2="336" y2="36" stroke={rightActiveSlot === 0 && rightActiveColor ? rightActiveColor : defaultColor} strokeWidth={rightActiveSlot === 0 && rightActiveColor ? 3 : 2} />
            {rightSlotCount > 1 && (
              <line x1="400" y1="0" x2="400" y2="36" stroke={rightActiveSlot === 1 && rightActiveColor ? rightActiveColor : defaultColor} strokeWidth={rightActiveSlot === 1 && rightActiveColor ? 3 : 2} />
            )}
            {rightSlotCount > 1 && (
              <line x1="336" y1="36" x2="400" y2="36" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />
            )}
            <line x1="312" y1="36" x2={rightSlotCount > 1 ? "368" : "336"} y2="36" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />
            <line x1="312" y1="36" x2="312" y2="85" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />
          </>
        ) : (
          <>
            {/* Regular AMS: 4 slots with hub */}
            {/* Vertical lines from 4 slots - highlight active slot */}
            {rightSlotX.map((x, i) => (
              <line key={`right-slot-${i}`} x1={x} y1="0" x2={x} y2="14" stroke={rightActiveSlot === i && rightActiveColor ? rightActiveColor : defaultColor} strokeWidth={rightActiveSlot === i && rightActiveColor ? 3 : 2} />
            ))}

            {/* Horizontal bar connecting right slots - highlight from active slot to hub */}
            {rightActiveSlot !== null && rightActiveSlot !== undefined && rightActiveColor ? (
              <>
                {/* Background bar */}
                <line x1="354" y1="14" x2="546" y2="14" stroke={defaultColor} strokeWidth="2" />
                {/* Highlight segment from active slot to hub (center at 450) */}
                <line
                  x1={Math.min(rightSlotX[rightActiveSlot], 450)}
                  y1="14"
                  x2={Math.max(rightSlotX[rightActiveSlot], 450)}
                  y2="14"
                  stroke={rightActiveColor}
                  strokeWidth="3"
                />
              </>
            ) : (
              <line x1="354" y1="14" x2="546" y2="14" stroke={defaultColor} strokeWidth="2" />
            )}

            {/* Right hub */}
            <rect x="436" y="8" width="28" height="14" rx="2" fill={rightActiveColor ?? '#c0c0c0'} stroke={rightActiveColor ?? defaultColor} strokeWidth="1" />

            {/* Vertical from right hub down */}
            <line x1="450" y1="22" x2="450" y2="36" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />

            {/* Horizontal from right hub toward center */}
            <line x1="312" y1="36" x2="450" y2="36" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />

            {/* Vertical down to right extruder inlet */}
            <line x1="312" y1="36" x2="312" y2="85" stroke={rightActiveColor ?? defaultColor} strokeWidth={rightActiveColor ? 3 : 2} />
          </>
        )}
      </svg>

      {/* Extruder image container - positioned at bottom center */}
      {/* Image is 56x71 pixels, scaled to h=50px = width ~39px */}
      {/* Scale factor: 50/71 = 0.704 */}
      {/* Green circles in original image: left center ~(15.2,34.2), right center ~(41.0,33.9) */}
      {/* Scaled positions: left x≈10.7, right x≈28.9, y≈24 from top */}
      <div className="absolute left-1/2 -translate-x-1/2 bottom-0 h-[50px] w-[39px]">
        <img
          src="/icons/extruder-left-right.png"
          alt="Extruder"
          className="h-full w-full"
        />
        {/* Extruder inlet indicator circles - overlay on extruder image */}
        {/* Left inlet (extruder 1) - left side of extruder */}
        <div
          className="absolute w-[8px] h-[8px] rounded-full"
          style={{
            left: '7px',
            top: '20px',
            backgroundColor: leftActiveColor ?? 'transparent',
          }}
        />
        {/* Right inlet (extruder 0) - right side of extruder */}
        <div
          className="absolute w-[8px] h-[8px] rounded-full"
          style={{
            left: '25px',
            top: '20px',
            backgroundColor: rightActiveColor ?? 'transparent',
          }}
        />
      </div>
    </div>
  );
}

export function AMSSectionDual({ printerId, printerModel, status, nozzleCount }: AMSSectionDualProps) {
  const { showToast } = useToast();
  const isConnected = status?.connected ?? false;
  const isPrinting = status?.state === 'RUNNING';
  const isDualNozzle = nozzleCount > 1;
  const amsUnits: AMSUnit[] = status?.ams ?? [];
  // Per-AMS extruder map: {ams_id: extruder_id} where extruder 0=right, 1=left
  // This is extracted from each AMS unit's info field bit 8 in the backend
  // Note: JSON keys are always strings, so we use Record<string, number>
  const amsExtruderMap: Record<string, number> = status?.ams_extruder_map ?? {};

  // Get nozzle diameter for K-profile lookup (default to 0.4)
  const nozzleDiameter = status?.nozzles?.[0]?.nozzle_diameter || '0.4';

  // Fetch K-profiles for this printer
  const { data: kProfilesData, isFetched: kProfilesFetched } = useQuery({
    queryKey: ['kprofiles', printerId, nozzleDiameter],
    queryFn: () => api.getKProfiles(printerId, nozzleDiameter),
    enabled: isConnected,
    staleTime: 30000, // Cache for 30 seconds to avoid flicker on re-renders
  });

  // Create K-value lookup using cascading search by tray properties
  // Priority: 1) tray_sub_brands + color, 2) tray_sub_brands only, 3) tray_type only, 4) default
  const getKValueForSlot = (tray: AMSTray): number | null => {
    const profiles = kProfilesData?.profiles || [];
    if (!profiles.length) return null;

    // Get color name from tray_id_name (e.g., "A00-Y2" -> "Sunflower Yellow")
    const colorName = getColorNameFromTrayId(tray.tray_id_name || null);

    // Use cascading search to find best matching K-profile
    const matchedProfile = findBestKProfile(
      profiles,
      tray.tray_sub_brands || null,
      tray.tray_type || null,
      colorName
    );

    if (matchedProfile) {
      return parseFloat(matchedProfile.k_value) || null;
    }
    return null; // No profile found for this filament
  };

  // Distribute AMS units based on ams_extruder_map
  // Each AMS unit's info field tells us which extruder it's connected to:
  // UI layout: Left panel shows extruder 0 AMS units, Right panel shows extruder 1 AMS units
  // Note: Internal nozzle IDs are different (T0=right physical nozzle, T1=left physical nozzle)
  const leftUnits = (() => {
    if (!isDualNozzle) return amsUnits;
    if (Object.keys(amsExtruderMap).length > 0) {
      // Filter AMS units assigned to extruder 0 (left UI panel)
      // JSON keys are strings, so convert unit.id to string
      return amsUnits.filter(unit => amsExtruderMap[String(unit.id)] === 0);
    }
    // Fallback: even indices go to left
    return amsUnits.filter((_, i) => i % 2 === 0);
  })();

  const rightUnits = (() => {
    if (!isDualNozzle) return [];
    if (Object.keys(amsExtruderMap).length > 0) {
      // Filter AMS units assigned to extruder 1 (right UI panel)
      // JSON keys are strings, so convert unit.id to string
      return amsUnits.filter(unit => amsExtruderMap[String(unit.id)] === 1);
    }
    // Fallback: odd indices go to right
    return amsUnits.filter((_, i) => i % 2 === 1);
  })();

  const [leftAmsIndex, setLeftAmsIndex] = useState(0);
  const [rightAmsIndex, setRightAmsIndex] = useState(0);
  const [selectedTray, setSelectedTray] = useState<number | null>(null);

  // Modal states
  const [humidityModal, setHumidityModal] = useState<{ humidity: number; temp: number } | null>(null);
  const [materialsModal, setMaterialsModal] = useState<{ tray: AMSTray; slotLabel: string; amsId: number } | null>(null);

  // Get ams_status values from printer status
  const amsStatusMain = status?.ams_status_main ?? 0;
  const trayNow = status?.tray_now ?? 255;

  // AMS Operations hook - manages state machine for refresh/load/unload
  const amsOps = useAmsOperations({
    printerId,
    amsUnits,
    amsStatusMain,
    trayNow,
    onToast: showToast,
  });

  // Track if we've done initial sync from tray_now
  const initialSyncDone = useRef(false);

  // Sync selectedTray from status.tray_now on initial load
  // tray_now: 255 = no filament loaded, 0-253 = valid tray ID, 254 = external spool
  useEffect(() => {
    if (initialSyncDone.current) return;

    const trayNow = status?.tray_now;
    if (trayNow !== undefined && trayNow !== null) {
      initialSyncDone.current = true;
      if (trayNow !== 255 && trayNow !== 254) {
        // Valid AMS tray is loaded - select it
        // Note: We don't set loadTriggered here because the user may want to load a different slot
        console.log(`[AMSSectionDual] Initializing from tray_now: ${trayNow}`);
        setSelectedTray(trayNow);
      } else {
        // No filament loaded or external spool
        console.log(`[AMSSectionDual] tray_now=${trayNow} (no AMS filament loaded)`);
      }
    }
  }, [status?.tray_now]);

  // Watch for AMS data updates to clear the refresh spinner
  // When the printer reports updated tray data after RFID read, clear the spinner
  const prevAmsDataRef = useRef<string>('');
  useEffect(() => {
    if (!refreshingSlotState) return;

    // Find the refreshing slot's current data
    const { amsId, trayId, startTime } = refreshingSlotState;
    const unit = amsUnits.find(u => u.id === amsId);
    const tray = unit?.tray?.find(t => t.id === trayId);

    if (!tray) return;

    // Create a signature of the tray data to detect changes
    const traySignature = JSON.stringify({
      tag_uid: tray.tag_uid,
      tray_uuid: tray.tray_uuid,
      tray_id_name: tray.tray_id_name,
      tray_type: tray.tray_type,
      tray_color: tray.tray_color,
    });

    // If we have previous data and it changed, the refresh is complete
    // Also require at least 500ms to have passed (to avoid false positives from initial render)
    const elapsed = Date.now() - startTime;
    if (prevAmsDataRef.current && prevAmsDataRef.current !== traySignature && elapsed > 500) {
      console.log(`[AMSSectionDual] RFID refresh complete for AMS ${amsId} tray ${trayId} (took ${elapsed}ms)`);
      setRefreshingSlotState(null);
    }

    // Update the ref for next comparison
    prevAmsDataRef.current = traySignature;
  }, [refreshingSlotState, amsUnits]);

  const loadMutation = useMutation({
    mutationFn: ({ trayId, extruderId }: { trayId: number; extruderId?: number }) =>
      api.amsLoadFilament(printerId, trayId, extruderId),
    onSuccess: (data, { trayId, extruderId }) => {
      console.log(`[AMSSectionDual] Load filament success (tray ${trayId}, extruder ${extruderId}):`, data);
    },
    onError: (error, { trayId, extruderId }) => {
      console.error(`[AMSSectionDual] Load filament error (tray ${trayId}, extruder ${extruderId}):`, error);
    },
  });

  const unloadMutation = useMutation({
    mutationFn: () => api.amsUnloadFilament(printerId),
    onSuccess: (data) => {
      console.log(`[AMSSectionDual] Unload filament success:`, data);
    },
    onError: (error) => {
      console.error(`[AMSSectionDual] Unload filament error:`, error);
    },
  });

  // Handle tray selection
  const handleTraySelect = (trayId: number | null) => {
    setSelectedTray(trayId);
  };

  // Helper to get extruder ID for a given tray
  const getExtruderIdForTray = (trayId: number): number | undefined => {
    // For dual-nozzle printers, calculate which AMS unit the tray belongs to
    // and look up which extruder it's connected to
    if (!isDualNozzle) return undefined;

    // Find which AMS unit contains this tray
    // Global tray ID format: amsId * 4 + slotIndex (for regular AMS)
    // For AMS-HT (id >= 128): amsId * 4 + slotIndex (but only 2 slots)
    for (const unit of amsUnits) {
      const slotsInUnit = unit.id >= 128 ? 2 : 4; // AMS-HT has 2 slots
      const baseSlotId = unit.id * 4;
      if (trayId >= baseSlotId && trayId < baseSlotId + slotsInUnit) {
        // Found the AMS unit - look up its extruder
        const extruderId = amsExtruderMap[String(unit.id)];
        console.log(`[AMSSectionDual] Tray ${trayId} belongs to AMS ${unit.id}, extruder: ${extruderId}`);
        return extruderId;
      }
    }
    return undefined;
  };

  const handleLoad = () => {
    console.log(`[AMSSectionDual] handleLoad called, selectedTray: ${selectedTray}`);
    if (selectedTray !== null) {
      const extruderId = getExtruderIdForTray(selectedTray);
      console.log(`[AMSSectionDual] Calling loadMutation.mutate(tray: ${selectedTray}, extruder: ${extruderId})`);
      // Set ref synchronously FIRST (refs update immediately, before MQTT can respond)
      intendedOperationRef.current = 'load';
      // Show filament change card immediately
      setUserFilamentChange({ isLoading: true, targetTrayId: selectedTray });
      loadMutation.mutate({ trayId: selectedTray, extruderId });
    }
  };

  const handleUnload = () => {
    console.log(`[AMSSectionDual] handleUnload called, printerId: ${printerId}, trayNow: ${status?.tray_now}`);
    // Set ref synchronously FIRST (refs update immediately, before MQTT can respond)
    intendedOperationRef.current = 'unload';
    // Show filament change card immediately (no target tray for unload)
    setUserFilamentChange({ isLoading: false, targetTrayId: null });
    console.log(`[AMSSectionDual] Calling unloadMutation.mutate()`);
    unloadMutation.mutate();
  };

  // Callback for FilamentChangeCard to close itself
  const handleFilamentChangeComplete = () => {
    console.log(`[AMSSectionDual] FilamentChangeCard completed, closing card`);
    intendedOperationRef.current = null; // Clear the synchronous ref
    setUserFilamentChange(null);
  };

  const isLoading = loadMutation.isPending || unloadMutation.isPending;

  // Handlers for modals and actions
  const handleHumidityClick = (humidity: number, temp: number) => {
    setHumidityModal({ humidity, temp });
  };

  const refreshMutation = useMutation({
    mutationFn: ({ amsId, trayId }: { amsId: number; trayId: number }) =>
      api.refreshAmsTray(printerId, amsId, trayId),
    onSuccess: (data, variables) => {
      console.log(`[AMSSectionDual] Tray refresh response (AMS ${variables.amsId}, Tray ${variables.trayId}):`, data);
      if (data.success) {
        showToast(data.message || 'RFID refresh started', 'success');
      } else {
        showToast(data.message || 'Failed to refresh tray', 'error');
      }
    },
    onError: (error, variables) => {
      console.error(`[AMSSectionDual] Tray refresh error (AMS ${variables.amsId}, Tray ${variables.trayId}):`, error);
      showToast('Failed to refresh tray', 'error');
    },
  });

  const handleSlotRefresh = (amsId: number, slotId: number) => {
    // Trigger RFID re-read for the specific tray
    console.log(`[AMSSectionDual] Slot refresh triggered: AMS ${amsId}, Slot ${slotId}, printerId: ${printerId}`);
    // Reset the previous data ref so we can detect the next change
    prevAmsDataRef.current = '';
    // Show spinner immediately - will be cleared when AMS data updates from MQTT
    const startTime = Date.now();
    setRefreshingSlotState({ amsId, trayId: slotId, startTime });
    refreshMutation.mutate({ amsId, trayId: slotId });
    // Fallback timeout (15s) in case data doesn't change (e.g., same spool re-read)
    setTimeout(() => {
      setRefreshingSlotState(prev => {
        if (prev && prev.startTime === startTime) {
          console.log(`[AMSSectionDual] RFID refresh timeout for AMS ${amsId} tray ${slotId}`);
          return null;
        }
        return prev;
      });
    }, 15000);
  };

  const handleEyeClick = (tray: AMSTray, slotLabel: string, amsId: number) => {
    setMaterialsModal({ tray, slotLabel, amsId });
  };

  // Determine if we're in a filament change state (from MQTT ams_status)
  // ams_status_main: 0=idle, 1=filament_change, 2=rfid_identifying, 3=assist, 4=calibration
  // mc_print_sub_stage: step indicator used by BambuStudio/OrcaSlicer for filament change progress
  const amsStatusMain = status?.ams_status_main ?? 0;
  const isMqttFilamentChangeActive = amsStatusMain === 1;

  // Auto-close card when operation completes
  // Track when we transition from filament change active to idle (ams_status_main 1 -> 0)
  const prevAmsStatusMainRef = useRef(amsStatusMain);
  // Track if we've seen ams_status_main = 1 since the user clicked load/unload
  // This prevents premature card closure on brief status glitches
  const operationStartedRef = useRef(false);
  useEffect(() => {
    const wasActive = prevAmsStatusMainRef.current === 1;

    if (isMqttFilamentChangeActive) {
      // MQTT is now reporting filament change - operation has started
      operationStartedRef.current = true;
      // Clear user-triggered state, card will continue showing because isMqttFilamentChangeActive is true
      setUserFilamentChange(null);
    } else if (wasActive && !isMqttFilamentChangeActive && operationStartedRef.current) {
      // Transition from active (1) to idle (0), AND we've confirmed operation started
      // Close the card by clearing user state and the synchronous ref
      console.log(`[AMSSectionDual] ams_status_main transitioned 1->0, operation was started, closing card`);
      intendedOperationRef.current = null;
      operationStartedRef.current = false;
      setUserFilamentChange(null);
    }

    // Update previous status for next comparison
    prevAmsStatusMainRef.current = amsStatusMain;
  }, [isMqttFilamentChangeActive, amsStatusMain]);

  // Show FilamentChangeCard when either MQTT reports active ams_status OR user just clicked load/unload
  const showFilamentChangeCard = isMqttFilamentChangeActive || userFilamentChange !== null;

  // Get the loaded tray info for wire coloring
  // Wire coloring should show the path from the currently loaded filament to the extruder
  // But ONLY if the currently displayed AMS panel is the one with the loaded filament
  const trayNow = status?.tray_now ?? 255;

  // Determine if loading or unloading for the card display
  // Priority: 1) Synchronous ref (set immediately on click), 2) React state, 3) MQTT signals
  // The ref prevents race conditions where MQTT updates arrive before React state updates
  const amsStatusSub = status?.ams_status_sub ?? 0;
  const SUB_RETRACT = 4; // Only happens during unload
  const isFilamentLoading =
    intendedOperationRef.current === 'load' ? true :
    intendedOperationRef.current === 'unload' ? false :
    userFilamentChange !== null ? userFilamentChange.isLoading :
    !(amsStatusSub === SUB_RETRACT || trayNow === 255); // Unload if retracting or tray_now is 255
  const getLoadedTrayInfo = (): {
    leftActiveSlot: number | null;
    rightActiveSlot: number | null;
    leftFilamentColor: string | null;
    rightFilamentColor: string | null;
  } => {
    // tray_now: 255 = no filament, 254 = external spool, 0-253 = valid tray ID
    if (trayNow === 255 || trayNow === 254) {
      return { leftActiveSlot: null, rightActiveSlot: null, leftFilamentColor: null, rightFilamentColor: null };
    }

    // Find which AMS and slot contains the loaded tray
    for (const unit of amsUnits) {
      const slotsInUnit = unit.id >= 128 ? 2 : 4;
      const baseSlotId = unit.id * 4;
      if (trayNow >= baseSlotId && trayNow < baseSlotId + slotsInUnit) {
        const slotIndex = trayNow - baseSlotId;
        const tray = unit.tray[slotIndex];
        const color = tray?.tray_color ?? null;

        // Determine if this AMS is on left or right UI panel
        // UI layout: extruder 0 = left panel, extruder 1 = right panel
        const extruderId = amsExtruderMap[String(unit.id)];

        // Check if this AMS unit is the one currently displayed in the panel
        const currentLeftUnit = leftUnits[leftAmsIndex];
        const currentRightUnit = rightUnits[rightAmsIndex];

        if (extruderId === 0) {
          // Left UI panel (extruder 0) - leftUnits filters for amsExtruderMap === 0
          // Only show colored wiring if the currently displayed AMS unit is the one with loaded filament
          const isDisplayed = currentLeftUnit?.id === unit.id;
          return {
            leftActiveSlot: isDisplayed ? slotIndex : null,
            rightActiveSlot: null,
            leftFilamentColor: isDisplayed ? color : null,  // Hide color if different AMS is selected
            rightFilamentColor: null
          };
        } else {
          // Right UI panel (extruder 1) - rightUnits filters for amsExtruderMap === 1
          const isDisplayed = currentRightUnit?.id === unit.id;
          return {
            leftActiveSlot: null,
            rightActiveSlot: isDisplayed ? slotIndex : null,
            leftFilamentColor: null,
            rightFilamentColor: isDisplayed ? color : null  // Hide color if different AMS is selected
          };
        }
      }
    }

    return { leftActiveSlot: null, rightActiveSlot: null, leftFilamentColor: null, rightFilamentColor: null };
  };

  const { leftActiveSlot, rightActiveSlot, leftFilamentColor, rightFilamentColor } = getLoadedTrayInfo();

  // Use state-based refreshing slot for spinner visibility (minimum 1.5s display time)
  const refreshingSlot = refreshingSlotState;

  return (
    <div className="bg-bambu-dark-tertiary rounded-[10px] p-3">
      {/* Dual Panel Layout - just the panels, no wiring */}
      <div className="flex gap-5">
        <AMSPanelContent
          units={leftUnits}
          side="left"
          isPrinting={isPrinting}
          selectedAmsIndex={leftAmsIndex}
          onSelectAms={setLeftAmsIndex}
          selectedTray={selectedTray}
          onSelectTray={handleTraySelect}
          onHumidityClick={handleHumidityClick}
          onSlotRefresh={handleSlotRefresh}
          onEyeClick={handleEyeClick}
          refreshingSlot={refreshingSlot}
          getKValue={getKValueForSlot}
          kProfilesLoaded={kProfilesFetched}
        />

        {isDualNozzle && (
          <AMSPanelContent
            units={rightUnits}
            side="right"
            isPrinting={isPrinting}
            selectedAmsIndex={rightAmsIndex}
            onSelectAms={setRightAmsIndex}
            selectedTray={selectedTray}
            onSelectTray={handleTraySelect}
            onHumidityClick={handleHumidityClick}
            onSlotRefresh={handleSlotRefresh}
            onEyeClick={handleEyeClick}
            refreshingSlot={refreshingSlot}
            getKValue={getKValueForSlot}
            kProfilesLoaded={kProfilesFetched}
          />
        )}
      </div>

      {/* Unified Wiring Layer - ALL wiring drawn here */}
      <WiringLayer
        isDualNozzle={isDualNozzle}
        leftSlotCount={leftUnits[leftAmsIndex]?.tray?.length ?? 4}
        rightSlotCount={rightUnits[rightAmsIndex]?.tray?.length ?? 4}
        leftIsHT={leftUnits[leftAmsIndex] ? isAmsHT(leftUnits[leftAmsIndex].id) : false}
        rightIsHT={rightUnits[rightAmsIndex] ? isAmsHT(rightUnits[rightAmsIndex].id) : false}
        leftActiveSlot={leftActiveSlot}
        rightActiveSlot={rightActiveSlot}
        leftFilamentColor={leftFilamentColor}
        rightFilamentColor={rightFilamentColor}
      />

      {/* Action Buttons Row - aligned with extruder */}
      <div className="flex items-start -mt-[50px]">
        <div className="flex items-center gap-2">
          <button className="w-10 h-10 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark border border-bambu-dark-tertiary flex items-center justify-center">
            <img src="/icons/ams-settings.svg" alt="Settings" className="w-5 icon-theme" />
          </button>
          <button className="px-[18px] py-2.5 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark border border-bambu-dark-tertiary text-sm text-bambu-gray flex items-center gap-1.5">
            Auto-refill
          </button>
        </div>

        <div className="flex-1" />

        <div className="flex items-center gap-2">
          {/* Unload button: disabled if not connected, printing, mutation pending, or no filament loaded */}
          <button
            onClick={handleUnload}
            disabled={!isConnected || isPrinting || isLoading || trayNow === 255}
            className={`px-7 py-2.5 rounded-lg text-sm transition-colors border ${
              !isConnected || isPrinting || isLoading || trayNow === 255
                ? 'bg-bambu-gray-dark text-gray-500 border-bambu-gray-dark cursor-not-allowed'
                : 'bg-bambu-dark-secondary text-white border-bambu-dark-tertiary hover:bg-bambu-dark'
            }`}
          >
            {unloadMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              'Unload'
            )}
          </button>
          {/* Load button: disabled if not connected, printing, mutation pending, no tray selected, or selected tray is already loaded */}
          <button
            onClick={handleLoad}
            disabled={!isConnected || isPrinting || selectedTray === null || isLoading || selectedTray === trayNow}
            className={`px-7 py-2.5 rounded-lg text-sm transition-colors border ${
              !isConnected || isPrinting || selectedTray === null || isLoading || selectedTray === trayNow
                ? 'bg-bambu-gray-dark text-gray-500 border-bambu-gray-dark cursor-not-allowed'
                : 'bg-bambu-dark-secondary text-white border-bambu-dark-tertiary hover:bg-bambu-dark'
            }`}
          >
            {loadMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              'Load'
            )}
          </button>
        </div>
      </div>

      {/* Error messages */}
      {(loadMutation.error || unloadMutation.error) && (
        <p className="mt-2 text-sm text-red-500 text-center">
          {(loadMutation.error || unloadMutation.error)?.message}
        </p>
      )}

      {/* Filament Change Progress Card - appears during load/unload operations */}
      {showFilamentChangeCard && (
        <FilamentChangeCard
          isLoading={isFilamentLoading}
          amsStatusMain={amsStatusMain}
          amsStatusSub={status?.ams_status_sub ?? 0}
          trayNow={trayNow}
          targetTrayId={userFilamentChange?.targetTrayId ?? null}
          onComplete={handleFilamentChangeComplete}
        />
      )}

      {/* Humidity Modal */}
      {humidityModal && (
        <AMSHumidityModal
          humidity={humidityModal.humidity}
          temperature={humidityModal.temp}
          dryingStatus="idle"
          onClose={() => setHumidityModal(null)}
        />
      )}

      {/* Materials Settings Modal */}
      {materialsModal && (
        <AMSMaterialsModal
          tray={materialsModal.tray}
          amsId={materialsModal.amsId}
          slotLabel={materialsModal.slotLabel}
          printerId={printerId}
          printerModel={printerModel}
          nozzleDiameter={status?.nozzles?.[0]?.nozzle_diameter || '0.4'}
          extruderId={amsExtruderMap[materialsModal.amsId.toString()] ?? 0}
          onClose={() => setMaterialsModal(null)}
        />
      )}
    </div>
  );
}
