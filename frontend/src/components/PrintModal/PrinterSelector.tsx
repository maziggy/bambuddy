import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import {
  Printer as PrinterIcon,
  Loader2,
  AlertCircle,
  AlertTriangle,
  Check,
  Circle,
  RefreshCw,
  Wand2,
} from 'lucide-react';
import { api } from '../../api/client';
import { getColorName } from '../../utils/colors';
import {
  normalizeColorForCompare,
  colorsAreSimilar,
} from '../../utils/amsHelpers';
import type { PrinterSelectorProps } from './types';
import type { PrinterMappingResult, PerPrinterConfig } from '../../hooks/useMultiPrinterFilamentMapping';
import type { FilamentRequirement, LoadedFilament } from '../../hooks/useFilamentMapping';

interface PrinterSelectorWithMappingProps extends PrinterSelectorProps {
  /** Per-printer mapping results (only used when multiple printers selected) */
  printerMappingResults?: PrinterMappingResult[];
  /** Filament requirements for the print */
  filamentReqs?: { filaments: FilamentRequirement[] };
  /** Callback to auto-configure a printer */
  onAutoConfigurePrinter?: (printerId: number) => void;
  /** Callback to update printer config */
  onUpdatePrinterConfig?: (printerId: number, config: Partial<PerPrinterConfig>) => void;
}

/**
 * Inline AMS mapping editor for a single printer.
 */
function InlineMappingEditor({
  printerResult,
  filamentReqs,
  onUpdateConfig,
}: {
  printerResult: PrinterMappingResult;
  filamentReqs: FilamentRequirement[];
  onUpdateConfig: (config: Partial<PerPrinterConfig>) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const handleSlotChange = (slotId: number, value: string) => {
    if (slotId <= 0) return;

    const newMappings = { ...printerResult.config.manualMappings };
    if (value === '') {
      delete newMappings[slotId];
    } else {
      newMappings[slotId] = parseInt(value, 10);
    }

    onUpdateConfig({
      useDefault: false,
      manualMappings: newMappings,
      autoConfigured: false,
    });
  };

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await api.refreshPrinterStatus(printerResult.printerId);
      await new Promise((r) => setTimeout(r, 500));
      await queryClient.refetchQueries({ queryKey: ['printer-status', printerResult.printerId] });
    } finally {
      setIsRefreshing(false);
    }
  };

  // Compute current slot assignments
  const slotAssignments = filamentReqs.map((req) => {
    const slotId = req.slot_id || 0;
    const currentMapping = printerResult.config.manualMappings[slotId];

    let loaded: LoadedFilament | undefined;
    let isManual = false;

    if (currentMapping !== undefined) {
      loaded = printerResult.loadedFilaments.find((f) => f.globalTrayId === currentMapping);
      isManual = true;
    } else {
      // Auto-match logic
      const usedTrayIds = new Set<number>(Object.values(printerResult.config.manualMappings));

      const exactMatch = printerResult.loadedFilaments.find(
        (f) =>
          !usedTrayIds.has(f.globalTrayId) &&
          f.type?.toUpperCase() === req.type?.toUpperCase() &&
          normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
      );
      const similarMatch = exactMatch
        ? undefined
        : printerResult.loadedFilaments.find(
            (f) =>
              !usedTrayIds.has(f.globalTrayId) &&
              f.type?.toUpperCase() === req.type?.toUpperCase() &&
              colorsAreSimilar(f.color, req.color)
          );
      const typeOnlyMatch =
        exactMatch || similarMatch
          ? undefined
          : printerResult.loadedFilaments.find(
              (f) => !usedTrayIds.has(f.globalTrayId) && f.type?.toUpperCase() === req.type?.toUpperCase()
            );
      loaded = exactMatch ?? similarMatch ?? typeOnlyMatch;
    }

    // Determine status
    let status: 'match' | 'type_only' | 'mismatch' = 'mismatch';
    if (loaded) {
      const typeMatch = loaded.type?.toUpperCase() === req.type?.toUpperCase();
      const colorMatch =
        normalizeColorForCompare(loaded.color) === normalizeColorForCompare(req.color) ||
        colorsAreSimilar(loaded.color, req.color);

      if (typeMatch && colorMatch) {
        status = 'match';
      } else if (typeMatch) {
        status = 'type_only';
      }
    }

    return { req, loaded, status, isManual };
  });

  return (
    <div className="mt-2 bg-bambu-dark rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-bambu-gray">Custom slot mapping</span>
        <button
          type="button"
          onClick={handleRefresh}
          className="flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray hover:text-white"
          disabled={isRefreshing}
        >
          <RefreshCw className={`w-3 h-3 ${isRefreshing ? 'animate-spin' : ''}`} />
          <span>Re-read</span>
        </button>
      </div>

      {slotAssignments.map(({ req, loaded, status, isManual }, idx) => (
        <div
          key={idx}
          className="grid items-center gap-2 text-xs"
          style={{ gridTemplateColumns: '16px minmax(70px, 1fr) auto 2fr 16px' }}
        >
          <span title={`Required: ${req.type} - ${getColorName(req.color)}`}>
            <Circle className="w-3 h-3" fill={req.color} stroke={req.color} />
          </span>
          <span className="text-white truncate">
            {req.type} <span className="text-bambu-gray">({req.used_grams}g)</span>
          </span>
          <span className="text-bambu-gray">→</span>
          <select
            value={loaded?.globalTrayId ?? ''}
            onChange={(e) => handleSlotChange(req.slot_id || 0, e.target.value)}
            className={`flex-1 px-2 py-1 rounded border text-xs bg-bambu-dark-secondary focus:outline-none focus:ring-1 focus:ring-bambu-green ${
              status === 'match'
                ? 'border-bambu-green/50 text-bambu-green'
                : status === 'type_only'
                ? 'border-yellow-400/50 text-yellow-400'
                : 'border-orange-400/50 text-orange-400'
            } ${isManual ? 'ring-1 ring-blue-400/50' : ''}`}
            title={isManual ? 'Manually selected' : 'Auto-matched'}
          >
            <option value="" className="bg-bambu-dark text-bambu-gray">
              -- Select slot --
            </option>
            {printerResult.loadedFilaments.map((f) => (
              <option key={f.globalTrayId} value={f.globalTrayId} className="bg-bambu-dark text-white">
                {f.label}: {f.type} ({f.colorName})
              </option>
            ))}
          </select>
          {status === 'match' ? (
            <Check className="w-3 h-3 text-bambu-green" />
          ) : status === 'type_only' ? (
            <span title={t('printModal.sameTypeDifferentColor')}>
              <AlertTriangle className="w-3 h-3 text-yellow-400" />
            </span>
          ) : (
            <span title={t('printModal.filamentTypeNotLoaded')}>
              <AlertTriangle className="w-3 h-3 text-orange-400" />
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

/**
 * Printer selection component with grid-based UI.
 * Supports single or multi-select modes.
 * When multiple printers are selected, shows per-printer mapping overrides.
 */
export function PrinterSelector({
  printers,
  selectedPrinterIds,
  onMultiSelect,
  isLoading = false,
  allowMultiple = false,
  showInactive = false,
  printerMappingResults,
  filamentReqs,
  onAutoConfigurePrinter,
  onUpdatePrinterConfig,
}: PrinterSelectorWithMappingProps) {
  // Filter printers based on showInactive flag
  const displayPrinters = showInactive ? printers : printers.filter((p) => p.is_active);

  const showMappingOptions = allowMultiple &&
    selectedPrinterIds.length > 1 &&
    printerMappingResults &&
    filamentReqs?.filaments &&
    filamentReqs.filaments.length > 0 &&
    onAutoConfigurePrinter &&
    onUpdatePrinterConfig;

  if (isLoading) {
    return (
      <div className="flex justify-center py-8">
        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
      </div>
    );
  }

  if (displayPrinters.length === 0) {
    return (
      <div className="flex items-center gap-2 text-red-400 text-sm mb-4">
        <AlertCircle className="w-4 h-4" />
        No {showInactive ? '' : 'active '}printers available
      </div>
    );
  }

  const handlePrinterClick = (printerId: number) => {
    if (allowMultiple) {
      if (selectedPrinterIds.includes(printerId)) {
        onMultiSelect(selectedPrinterIds.filter((id) => id !== printerId));
      } else {
        onMultiSelect([...selectedPrinterIds, printerId]);
      }
    } else {
      onMultiSelect([printerId]);
    }
  };

  const handleSelectAll = () => {
    onMultiSelect(displayPrinters.map((p) => p.id));
  };

  const handleDeselectAll = () => {
    onMultiSelect([]);
  };

  const handleOverrideToggle = (printerId: number, enabled: boolean, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!onAutoConfigurePrinter || !onUpdatePrinterConfig) return;

    if (enabled) {
      onAutoConfigurePrinter(printerId);
    } else {
      onUpdatePrinterConfig(printerId, {
        useDefault: true,
        manualMappings: {},
        autoConfigured: false,
      });
    }
  };

  const isSelected = (printerId: number) => selectedPrinterIds.includes(printerId);
  const selectedCount = selectedPrinterIds.length;

  const getPrinterMappingResult = (printerId: number) => {
    return printerMappingResults?.find((r) => r.printerId === printerId);
  };

  return (
    <div className="space-y-2 mb-6">
      {/* Multi-select header */}
      {allowMultiple && displayPrinters.length > 1 && (
        <div className="flex items-center justify-between text-xs text-bambu-gray mb-2">
          <span>
            {selectedCount === 0
              ? 'Select printers'
              : `${selectedCount} printer${selectedCount !== 1 ? 's' : ''} selected`}
          </span>
          <div className="flex gap-2">
            {selectedCount < displayPrinters.length && (
              <button
                type="button"
                onClick={handleSelectAll}
                className="text-bambu-green hover:text-bambu-green/80 transition-colors"
              >
                Select all
              </button>
            )}
            {selectedCount > 0 && (
              <button
                type="button"
                onClick={handleDeselectAll}
                className="text-bambu-gray hover:text-white transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}

      {displayPrinters.map((printer) => {
        const selected = isSelected(printer.id);
        const mappingResult = getPrinterMappingResult(printer.id);
        const hasOverride = mappingResult && !mappingResult.config.useDefault;

        return (
          <div key={printer.id}>
            {/* Printer selection button */}
            <button
              type="button"
              onClick={() => handlePrinterClick(printer.id)}
              className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-colors ${
                selected
                  ? 'border-bambu-green bg-bambu-green/10'
                  : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
              } ${!printer.is_active ? 'opacity-60' : ''}`}
            >
              <div
                className={`p-2 rounded-lg ${
                  selected ? 'bg-bambu-green/20' : 'bg-bambu-dark-tertiary'
                }`}
              >
                <PrinterIcon
                  className={`w-5 h-5 ${
                    selected ? 'text-bambu-green' : 'text-bambu-gray'
                  }`}
                />
              </div>
              <div className="text-left flex-1">
                <p className="text-white font-medium">
                  {printer.name}
                  {!printer.is_active && <span className="text-bambu-gray text-xs ml-2">(inactive)</span>}
                </p>
                <p className="text-xs text-bambu-gray">
                  {printer.model || 'Unknown model'} • {printer.ip_address}
                </p>
              </div>
              {allowMultiple && (
                <div
                  className={`w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
                    selected
                      ? 'bg-bambu-green border-bambu-green'
                      : 'border-bambu-gray/50'
                  }`}
                >
                  {selected && <Check className="w-3 h-3 text-white" />}
                </div>
              )}
            </button>

            {/* Per-printer override checkbox + mapping (only when selected and multi-printer) */}
            {selected && showMappingOptions && mappingResult && (
              <div className="ml-4 mt-2 mb-3">
                {/* Override checkbox row */}
                <div className="flex items-center gap-2">
                  <label
                    className="flex items-center gap-2 cursor-pointer"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <input
                      type="checkbox"
                      checked={hasOverride}
                      onChange={(e) => handleOverrideToggle(printer.id, e.target.checked, e as unknown as React.MouseEvent)}
                      className="w-3.5 h-3.5 rounded border-bambu-gray/30 bg-bambu-dark-secondary text-bambu-green focus:ring-bambu-green focus:ring-offset-0"
                    />
                    <span className="text-xs text-bambu-gray">Custom mapping</span>
                  </label>

                  {/* Match status indicator */}
                  <span className={`text-xs ml-2 ${
                    mappingResult.matchStatus === 'full'
                      ? 'text-bambu-green'
                      : mappingResult.matchStatus === 'partial'
                      ? 'text-yellow-400'
                      : 'text-orange-400'
                  }`}>
                    ({mappingResult.exactMatches}/{mappingResult.totalSlots} matched)
                  </span>

                  {/* Loading indicator */}
                  {mappingResult.isLoading && (
                    <RefreshCw className="w-3 h-3 text-bambu-gray animate-spin" />
                  )}

                  {/* Auto-configure button (when override is enabled) */}
                  {hasOverride && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onAutoConfigurePrinter!(printer.id);
                      }}
                      className="ml-auto flex items-center gap-1 px-2 py-0.5 text-xs rounded border border-bambu-gray/30 hover:border-bambu-gray hover:bg-bambu-dark-tertiary transition-colors text-bambu-gray hover:text-white"
                    >
                      <Wand2 className="w-3 h-3" />
                      Auto
                    </button>
                  )}
                </div>

                {/* Inline mapping editor (shown when override is checked) */}
                {hasOverride && (
                  <InlineMappingEditor
                    printerResult={mappingResult}
                    filamentReqs={filamentReqs!.filaments}
                    onUpdateConfig={(config) => onUpdatePrinterConfig!(printer.id, config)}
                  />
                )}
              </div>
            )}
          </div>
        );
      })}

      {/* Warning when no printer selected */}
      {selectedCount === 0 && (
        <p className="text-xs text-orange-400 mt-1 flex items-center gap-1">
          <AlertCircle className="w-3 h-3" />
          Select at least one printer
        </p>
      )}
    </div>
  );
}
