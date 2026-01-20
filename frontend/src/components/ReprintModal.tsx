import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Printer, Loader2, AlertTriangle, Check, Circle, RefreshCw, ChevronDown, ChevronUp, Settings, Layers } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { getColorName } from '../utils/colors';

interface ReprintModalProps {
  archiveId: number;
  archiveName: string;
  onClose: () => void;
  onSuccess: () => void;
}

// Print options with defaults
interface PrintOptions {
  timelapse: boolean;
  bed_levelling: boolean;
  flow_cali: boolean;
  vibration_cali: boolean;
  layer_inspect: boolean;
}

const DEFAULT_PRINT_OPTIONS: PrintOptions = {
  bed_levelling: true,
  flow_cali: false,
  vibration_cali: true,
  layer_inspect: false,
  timelapse: false,
};

// Format seconds to human readable time
const formatTime = (seconds: number | null | undefined): string => {
  if (!seconds) return '';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
};

export function ReprintModal({ archiveId, archiveName, onClose, onSuccess }: ReprintModalProps) {
  const queryClient = useQueryClient();
  const [selectedPrinter, setSelectedPrinter] = useState<number | null>(null);
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [showOptions, setShowOptions] = useState(false);
  const [printOptions, setPrintOptions] = useState<PrintOptions>(DEFAULT_PRINT_OPTIONS);
  // Manual slot overrides: slot_id (1-indexed) -> globalTrayId
  const [manualMappings, setManualMappings] = useState<Record<number, number>>({});

  // Clear manual mappings when printer or plate changes
  useEffect(() => {
    setManualMappings({});
  }, [selectedPrinter, selectedPlate]);

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const { data: printers, isLoading: loadingPrinters } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Fetch available plates from the archived 3MF
  const { data: platesData } = useQuery({
    queryKey: ['archive-plates', archiveId],
    queryFn: () => api.getArchivePlates(archiveId),
  });

  // Auto-select the first plate for single-plate files, or require selection for multi-plate
  useEffect(() => {
    if (platesData?.plates?.length === 1) {
      setSelectedPlate(platesData.plates[0].index);
    }
  }, [platesData]);

  // Fetch filament requirements from the archived 3MF (filtered by plate if selected)
  const { data: filamentReqs } = useQuery({
    queryKey: ['archive-filaments', archiveId, selectedPlate],
    queryFn: () => api.getArchiveFilamentRequirements(archiveId, selectedPlate ?? undefined),
    enabled: selectedPlate !== null || !platesData?.is_multi_plate,
  });

  // Fetch printer status when a printer is selected
  const { data: printerStatus } = useQuery({
    queryKey: ['printer-status', selectedPrinter],
    queryFn: () => api.getPrinterStatus(selectedPrinter!),
    enabled: !!selectedPrinter,
  });

  const reprintMutation = useMutation({
    mutationFn: () => {
      if (!selectedPrinter) throw new Error('No printer selected');
      return api.reprintArchive(archiveId, selectedPrinter, {
        plate_id: selectedPlate ?? undefined,
        ams_mapping: amsMapping,
        ...printOptions,
      });
    },
    onSuccess: () => {
      onSuccess();
      onClose();
    },
  });

  const activePrinters = printers?.filter((p) => p.is_active) || [];
  const isMultiPlate = platesData?.is_multi_plate ?? false;
  const plates = platesData?.plates ?? [];

  // Helper to normalize color format (API returns "RRGGBBAA", 3MF uses "#RRGGBB")
  const normalizeColor = (color: string | null | undefined): string => {
    if (!color) return '#808080';
    // Remove alpha channel if present (8-char hex to 6-char)
    const hex = color.replace('#', '').substring(0, 6);
    return `#${hex}`;
  };

  // Helper to format slot label for display
  const formatSlotLabel = (amsId: number, trayId: number, isHt: boolean, isExternal: boolean): string => {
    if (isExternal) return 'External';
    const letter = String.fromCharCode(65 + (amsId >= 128 ? amsId - 128 : amsId)); // A, B, C, D
    if (isHt) return `HT-${letter}`;
    return `AMS-${letter} Slot ${trayId + 1}`;
  };

  // Calculate global tray ID for MQTT command
  // Regular AMS: (ams_id * 4) + slot_id, External: 254
  const getGlobalTrayId = (amsId: number, trayId: number, isExternal: boolean): number => {
    if (isExternal) return 254;
    return amsId * 4 + trayId;
  };

  // Build a list of all loaded filaments from printer's AMS/HT/External with location info
  const loadedFilaments = useMemo(() => {
    const filaments: Array<{
      type: string;
      color: string;
      colorName: string;
      amsId: number;
      trayId: number;
      isHt: boolean;
      isExternal: boolean;
      label: string;
      globalTrayId: number;
    }> = [];

    // Add filaments from all AMS units (regular and HT)
    printerStatus?.ams?.forEach((amsUnit) => {
      const isHt = amsUnit.tray.length === 1; // AMS-HT has single tray
      amsUnit.tray.forEach((tray) => {
        if (tray.tray_type) {
          const color = normalizeColor(tray.tray_color);
          filaments.push({
            type: tray.tray_type,
            color,
            colorName: getColorName(color),
            amsId: amsUnit.id,
            trayId: tray.id,
            isHt,
            isExternal: false,
            label: formatSlotLabel(amsUnit.id, tray.id, isHt, false),
            globalTrayId: getGlobalTrayId(amsUnit.id, tray.id, false),
          });
        }
      });
    });

    // Add external spool if loaded
    if (printerStatus?.vt_tray?.tray_type) {
      const color = normalizeColor(printerStatus.vt_tray.tray_color);
      filaments.push({
        type: printerStatus.vt_tray.tray_type,
        color,
        colorName: getColorName(color),
        amsId: -1,
        trayId: 0,
        isHt: false,
        isExternal: true,
        label: 'External',
        globalTrayId: 254,
      });
    }

    return filaments;
  }, [printerStatus]);

  // Compare required filaments with loaded filaments
  // Match by filament TYPE (not slot), since the printer dynamically maps slots
  // Respects manual overrides when set
  const filamentComparison = useMemo(() => {
    if (!filamentReqs?.filaments || filamentReqs.filaments.length === 0) return [];

    // Helper to normalize color for comparison (case-insensitive, strip #)
    const normalizeColorForCompare = (color: string | undefined): string => {
      if (!color) return '';
      return color.replace('#', '').toLowerCase().substring(0, 6); // Strip alpha
    };

    // Helper to check if two colors are similar (within threshold)
    const colorsAreSimilar = (color1: string | undefined, color2: string | undefined, threshold = 40): boolean => {
      const hex1 = normalizeColorForCompare(color1);
      const hex2 = normalizeColorForCompare(color2);
      if (!hex1 || !hex2 || hex1.length < 6 || hex2.length < 6) return false;

      const r1 = parseInt(hex1.substring(0, 2), 16);
      const g1 = parseInt(hex1.substring(2, 4), 16);
      const b1 = parseInt(hex1.substring(4, 6), 16);
      const r2 = parseInt(hex2.substring(0, 2), 16);
      const g2 = parseInt(hex2.substring(2, 4), 16);
      const b2 = parseInt(hex2.substring(4, 6), 16);

      // Check if each RGB component is within threshold
      return Math.abs(r1 - r2) <= threshold &&
             Math.abs(g1 - g2) <= threshold &&
             Math.abs(b1 - b2) <= threshold;
    };

    // Track which trays have been assigned to avoid duplicates
    // First, mark all manually assigned trays as used
    const usedTrayIds = new Set<number>(Object.values(manualMappings));

    return filamentReqs.filaments.map((req) => {
      const slotId = req.slot_id || 0;

      // Check if there's a manual override for this slot
      if (slotId > 0 && manualMappings[slotId] !== undefined) {
        const manualTrayId = manualMappings[slotId];
        const manualLoaded = loadedFilaments.find((f) => f.globalTrayId === manualTrayId);

        if (manualLoaded) {
          const typeMatch = manualLoaded.type?.toUpperCase() === req.type?.toUpperCase();
          const colorMatch = normalizeColorForCompare(manualLoaded.color) === normalizeColorForCompare(req.color) ||
                            colorsAreSimilar(manualLoaded.color, req.color);

          let status: 'match' | 'type_only' | 'mismatch' | 'empty';
          if (typeMatch && colorMatch) {
            status = 'match';
          } else if (typeMatch) {
            status = 'type_only';
          } else {
            status = 'mismatch';
          }

          return {
            ...req,
            loaded: manualLoaded,
            hasFilament: true,
            typeMatch,
            colorMatch,
            status,
            isManual: true,
          };
        }
      }

      // Auto-match: Find a loaded filament that matches by TYPE
      // Priority: exact color match > similar color match > type-only match
      // IMPORTANT: Exclude trays that are already assigned (manually or auto)
      const exactMatch = loadedFilaments.find(
        (f) => !usedTrayIds.has(f.globalTrayId) &&
               f.type?.toUpperCase() === req.type?.toUpperCase() &&
               normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
      );
      const similarMatch = !exactMatch && loadedFilaments.find(
        (f) => !usedTrayIds.has(f.globalTrayId) &&
               f.type?.toUpperCase() === req.type?.toUpperCase() &&
               colorsAreSimilar(f.color, req.color)
      );
      const typeOnlyMatch = !exactMatch && !similarMatch && loadedFilaments.find(
        (f) => !usedTrayIds.has(f.globalTrayId) &&
               f.type?.toUpperCase() === req.type?.toUpperCase()
      );
      const loaded = exactMatch || similarMatch || typeOnlyMatch || undefined;

      // Mark this tray as used so it won't be assigned to another slot
      if (loaded) {
        usedTrayIds.add(loaded.globalTrayId);
      }

      const hasFilament = !!loaded;
      const typeMatch = hasFilament;
      const colorMatch = !!exactMatch || !!similarMatch;

      // Status: match (type+color or similar), type_only (type ok, color very different), mismatch (type not found)
      let status: 'match' | 'type_only' | 'mismatch' | 'empty';
      if (exactMatch || similarMatch) {
        status = 'match';
      } else if (typeOnlyMatch) {
        status = 'type_only';
      } else {
        status = 'mismatch';
      }

      return {
        ...req,
        loaded,
        hasFilament,
        typeMatch,
        colorMatch,
        status,
        isManual: false,
      };
    });
  }, [filamentReqs, loadedFilaments, manualMappings]);

  // Build AMS mapping from auto-matched filaments
  // Format: array matching 3MF filament slot structure
  // Position = slot_id - 1 (0-indexed), value = global tray ID or -1 for unused
  // e.g., slots 1 and 3 used with trays 5 and 2 → [5, -1, 2, -1]
  const amsMapping = useMemo(() => {
    if (filamentComparison.length === 0) return undefined;

    // Find the max slot_id to determine array size
    const maxSlotId = Math.max(...filamentComparison.map((f) => f.slot_id || 0));
    if (maxSlotId <= 0) return undefined;

    // Create array with -1 for all positions
    const mapping = new Array(maxSlotId).fill(-1);

    // Fill in tray IDs at correct positions (slot_id - 1)
    filamentComparison.forEach((f) => {
      if (f.slot_id && f.slot_id > 0) {
        mapping[f.slot_id - 1] = f.loaded?.globalTrayId ?? -1;
      }
    });

    return mapping;
  }, [filamentComparison]);

  const hasTypeMismatch = filamentComparison.some((f) => f.status === 'mismatch');

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-8">
      <Card className="w-full max-w-lg">
        <CardContent>
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white">Re-print</h2>
            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="w-5 h-5" />
            </Button>
          </div>

          <p className="text-sm text-bambu-gray mb-4">
            Send <span className="text-white">{archiveName}</span> to a printer
          </p>

          {/* Printer selection */}
          {loadingPrinters ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : activePrinters.length === 0 ? (
            <div className="text-center py-8 text-bambu-gray">
              No active printers available
            </div>
          ) : (
            <div className="space-y-2 mb-6">
              {activePrinters.map((printer) => (
                <button
                  key={printer.id}
                  onClick={() => setSelectedPrinter(printer.id)}
                  className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                    selectedPrinter === printer.id
                      ? 'border-bambu-green bg-bambu-green/10'
                      : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                  }`}
                >
                  <div
                    className={`p-2 rounded-lg ${
                      selectedPrinter === printer.id
                        ? 'bg-bambu-green/20'
                        : 'bg-bambu-dark-tertiary'
                    }`}
                  >
                    <Printer
                      className={`w-5 h-5 ${
                        selectedPrinter === printer.id
                          ? 'text-bambu-green'
                          : 'text-bambu-gray'
                      }`}
                    />
                  </div>
                  <div className="text-left">
                    <p className="text-white font-medium">{printer.name}</p>
                    <p className="text-xs text-bambu-gray">
                      {printer.model || 'Unknown model'} • {printer.ip_address}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          )}

          {/* Plate selection - show when multi-plate file detected */}
          {isMultiPlate && plates.length > 1 && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <Layers className="w-4 h-4 text-bambu-gray" />
                <span className="text-sm text-bambu-gray">Select Plate to Print</span>
                {!selectedPlate && (
                  <span className="text-xs text-orange-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Selection required
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2">
                {plates.map((plate) => (
                  <button
                    key={plate.index}
                    onClick={() => setSelectedPlate(plate.index)}
                    className={`flex items-center gap-2 p-2 rounded-lg border transition-colors text-left ${
                      selectedPlate === plate.index
                        ? 'border-bambu-green bg-bambu-green/10'
                        : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
                    }`}
                  >
                    {plate.has_thumbnail && plate.thumbnail_url ? (
                      <img
                        src={plate.thumbnail_url}
                        alt={`Plate ${plate.index}`}
                        className="w-10 h-10 rounded object-cover bg-bambu-dark-tertiary"
                      />
                    ) : (
                      <div className="w-10 h-10 rounded bg-bambu-dark-tertiary flex items-center justify-center">
                        <Layers className="w-5 h-5 text-bambu-gray" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="text-sm text-white font-medium truncate">
                        {plate.name || `Plate ${plate.index}`}
                      </p>
                      <p className="text-xs text-bambu-gray truncate">
                        {plate.objects?.length > 0
                          ? plate.objects.slice(0, 3).join(', ') + (plate.objects.length > 3 ? '...' : '')
                          : `${plate.filaments.length} filament${plate.filaments.length !== 1 ? 's' : ''}`}
                        {plate.print_time_seconds ? ` • ${formatTime(plate.print_time_seconds)}` : ''}
                      </p>
                    </div>
                    {selectedPlate === plate.index && (
                      <Check className="w-4 h-4 text-bambu-green flex-shrink-0" />
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Filament comparison - show when printer selected and has filament requirements */}
          {selectedPrinter && (isMultiPlate ? selectedPlate !== null : true) && filamentComparison.length > 0 && (
            <div className="mb-4">
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm text-bambu-gray">Filament Check</span>
                <button
                  onClick={async () => {
                    if (!selectedPrinter) return;
                    setIsRefreshing(true);
                    try {
                      // Request fresh data from printer via MQTT pushall command
                      await api.refreshPrinterStatus(selectedPrinter);
                      // Wait a moment for printer to respond, then refetch
                      await new Promise((r) => setTimeout(r, 500));
                      await queryClient.refetchQueries({ queryKey: ['printer-status', selectedPrinter] });
                    } finally {
                      setIsRefreshing(false);
                    }
                  }}
                  className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray hover:text-white"
                  title="Re-read AMS status from printer"
                  disabled={isRefreshing}
                >
                  <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
                  <span>Re-read</span>
                </button>
                {hasTypeMismatch ? (
                  <span className="text-xs text-orange-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Type not found
                  </span>
                ) : filamentComparison.some((f) => f.status === 'type_only') ? (
                  <span className="text-xs text-yellow-400 flex items-center gap-1">
                    <AlertTriangle className="w-3 h-3" />
                    Color mismatch
                  </span>
                ) : (
                  <span className="text-xs text-bambu-green flex items-center gap-1">
                    <Check className="w-3 h-3" />
                    Ready
                  </span>
                )}
              </div>
              <div className="bg-bambu-dark rounded-lg p-3 space-y-2 text-xs">
                {filamentComparison.map((item, idx) => (
                  <div
                    key={idx}
                    className="grid items-center gap-2"
                    style={{ gridTemplateColumns: '16px minmax(70px, 1fr) auto 2fr 16px' }}
                  >
                    {/* Required color */}
                    <span title={`Required: ${item.type} - ${getColorName(item.color)}`}>
                      <Circle
                        className="w-3 h-3 flex-shrink-0"
                        fill={item.color}
                        stroke={item.color}
                      />
                    </span>
                    {/* Required type + grams */}
                    <span className="text-white truncate">
                      {item.type} <span className="text-bambu-gray">({item.used_grams}g)</span>
                    </span>
                    {/* Arrow */}
                    <span className="text-bambu-gray">→</span>
                    {/* Slot selector dropdown */}
                    <select
                      value={item.loaded?.globalTrayId ?? ''}
                      onChange={(e) => {
                        const slotId = item.slot_id || 0;
                        if (slotId > 0) {
                          const value = e.target.value;
                          if (value === '') {
                            // Clear manual override
                            setManualMappings((prev) => {
                              const next = { ...prev };
                              delete next[slotId];
                              return next;
                            });
                          } else {
                            setManualMappings((prev) => ({
                              ...prev,
                              [slotId]: parseInt(value, 10),
                            }));
                          }
                        }
                      }}
                      className={`flex-1 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary focus:outline-none focus:ring-1 focus:ring-bambu-green ${
                        item.status === 'match'
                          ? 'border-bambu-green/50 text-bambu-green'
                          : item.status === 'type_only'
                          ? 'border-yellow-400/50 text-yellow-400'
                          : 'border-orange-400/50 text-orange-400'
                      } ${item.isManual ? 'ring-1 ring-blue-400/50' : ''}`}
                      title={item.isManual ? 'Manually selected' : 'Auto-matched'}
                    >
                      <option value="" className="bg-bambu-dark text-bambu-gray">
                        -- Select slot --
                      </option>
                      {loadedFilaments.map((f) => (
                        <option
                          key={f.globalTrayId}
                          value={f.globalTrayId}
                          className="bg-bambu-dark text-white"
                        >
                          {f.label}: {f.type} ({f.colorName})
                        </option>
                      ))}
                    </select>
                    {/* Status icon */}
                    {item.status === 'match' ? (
                      <Check className="w-3 h-3 text-bambu-green" />
                    ) : item.status === 'type_only' ? (
                      <span title="Same type, different color">
                        <AlertTriangle className="w-3 h-3 text-yellow-400" />
                      </span>
                    ) : (
                      <span title="Filament type not loaded">
                        <AlertTriangle className="w-3 h-3 text-orange-400" />
                      </span>
                    )}
                  </div>
                ))}
              </div>
              {hasTypeMismatch && (
                <p className="text-xs text-orange-400 mt-2">
                  Required filament type not found in printer.
                </p>
              )}
            </div>
          )}

          {/* Print Options */}
          {selectedPrinter && (
            <div className="mb-4">
              <button
                onClick={() => setShowOptions(!showOptions)}
                className="flex items-center gap-2 text-sm text-bambu-gray hover:text-white transition-colors w-full"
              >
                <Settings className="w-4 h-4" />
                <span>Print Options</span>
                {showOptions ? <ChevronUp className="w-4 h-4 ml-auto" /> : <ChevronDown className="w-4 h-4 ml-auto" />}
              </button>
              {showOptions && (
                <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
                  {[
                    { key: 'bed_levelling', label: 'Bed Levelling', desc: 'Auto-level bed before print' },
                    { key: 'flow_cali', label: 'Flow Calibration', desc: 'Calibrate extrusion flow' },
                    { key: 'vibration_cali', label: 'Vibration Calibration', desc: 'Reduce ringing artifacts' },
                    { key: 'layer_inspect', label: 'First Layer Inspection', desc: 'AI inspection of first layer' },
                    { key: 'timelapse', label: 'Timelapse', desc: 'Record timelapse video' },
                  ].map(({ key, label, desc }) => (
                    <label key={key} className="flex items-center justify-between cursor-pointer group">
                      <div>
                        <span className="text-sm text-white">{label}</span>
                        <p className="text-xs text-bambu-gray">{desc}</p>
                      </div>
                      <div
                        className={`relative w-10 h-5 rounded-full transition-colors ${
                          printOptions[key as keyof PrintOptions] ? 'bg-bambu-green' : 'bg-bambu-dark-tertiary'
                        }`}
                        onClick={() => setPrintOptions((prev) => ({ ...prev, [key]: !prev[key as keyof PrintOptions] }))}
                      >
                        <div
                          className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                            printOptions[key as keyof PrintOptions] ? 'translate-x-5' : 'translate-x-0.5'
                          }`}
                        />
                      </div>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Error message */}
          {reprintMutation.isError && (
            <div className="mb-4 p-3 bg-red-500/20 border border-red-500/50 rounded-lg text-sm text-red-400">
              {(reprintMutation.error as Error).message || 'Failed to start print'}
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              Cancel
            </Button>
            <Button
              onClick={() => reprintMutation.mutate()}
              disabled={!selectedPrinter || (isMultiPlate && !selectedPlate) || reprintMutation.isPending}
              className="flex-1"
            >
              {reprintMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Sending...
                </>
              ) : (
                <>
                  <Printer className="w-4 h-4" />
                  Print
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
