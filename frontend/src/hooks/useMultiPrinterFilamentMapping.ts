import { useMemo } from 'react';
import { useQueries } from '@tanstack/react-query';
import { api } from '../api/client';
import type { PrinterStatus, Printer } from '../api/client';
import {
  buildLoadedFilaments,
  computeAmsMapping,
  type LoadedFilament,
  type FilamentRequirement,
} from './useFilamentMapping';
import {
  normalizeColorForCompare,
  colorsAreSimilar,
} from '../utils/amsHelpers';

/**
 * Match status for a single printer's filament configuration.
 */
export type PrinterMatchStatus = 'full' | 'partial' | 'missing';

/**
 * Per-printer configuration for AMS mapping.
 */
export interface PerPrinterConfig {
  /** Whether this printer uses the default mapping or has custom config */
  useDefault: boolean;
  /** Manual slot overrides for this printer (slot_id -> globalTrayId) */
  manualMappings: Record<number, number>;
  /** Whether this mapping was auto-configured */
  autoConfigured: boolean;
}

/**
 * Result of filament mapping for a single printer.
 */
export interface PrinterMappingResult {
  printerId: number;
  printerName: string;
  /** Printer status data */
  status: PrinterStatus | undefined;
  /** Whether status is still loading */
  isLoading: boolean;
  /** List of loaded filaments in this printer */
  loadedFilaments: LoadedFilament[];
  /** Auto-computed AMS mapping for this printer */
  autoMapping: number[] | undefined;
  /** Final AMS mapping (considering manual overrides) */
  finalMapping: number[] | undefined;
  /** Match status: full (all exact), partial (some mismatches), missing (type not found) */
  matchStatus: PrinterMatchStatus;
  /** Number of slots with exact match (type + color) */
  exactMatches: number;
  /** Number of slots with type-only match */
  typeOnlyMatches: number;
  /** Number of slots with missing type */
  missingTypes: number;
  /** Total required slots */
  totalSlots: number;
  /** Per-printer config */
  config: PerPrinterConfig;
}

/**
 * Result of the useMultiPrinterFilamentMapping hook.
 */
export interface UseMultiPrinterFilamentMappingResult {
  /** Results for each selected printer */
  printerResults: PrinterMappingResult[];
  /** Whether any printer data is still loading */
  isLoading: boolean;
  /** Per-printer configurations */
  perPrinterConfigs: Record<number, PerPrinterConfig>;
  /** Update config for a specific printer */
  updatePrinterConfig: (printerId: number, config: Partial<PerPrinterConfig>) => void;
  /** Auto-configure all printers based on their loaded filaments */
  autoConfigureAll: () => void;
  /** Auto-configure a specific printer */
  autoConfigurePrinter: (printerId: number) => void;
  /** Get final mapping for a specific printer (for submission) */
  getFinalMapping: (printerId: number) => number[] | undefined;
  /** Check if all printers have acceptable mappings */
  allPrintersReady: boolean;
}

/**
 * Compute match details for a printer given filament requirements and loaded filaments.
 */
function computeMatchDetails(
  filamentReqs: FilamentRequirement[] | undefined,
  loadedFilaments: LoadedFilament[],
  manualMappings: Record<number, number>
): { exactMatches: number; typeOnlyMatches: number; missingTypes: number; totalSlots: number; status: PrinterMatchStatus } {
  if (!filamentReqs || filamentReqs.length === 0) {
    return { exactMatches: 0, typeOnlyMatches: 0, missingTypes: 0, totalSlots: 0, status: 'full' };
  }

  let exactMatches = 0;
  let typeOnlyMatches = 0;
  let missingTypes = 0;
  const usedTrayIds = new Set<number>(Object.values(manualMappings));

  for (const req of filamentReqs) {
    const slotId = req.slot_id || 0;

    // Check manual override first
    if (slotId > 0 && manualMappings[slotId] !== undefined) {
      const manualTrayId = manualMappings[slotId];
      const manualLoaded = loadedFilaments.find((f) => f.globalTrayId === manualTrayId);

      if (manualLoaded) {
        const typeMatch = manualLoaded.type?.toUpperCase() === req.type?.toUpperCase();
        const colorMatch =
          normalizeColorForCompare(manualLoaded.color) === normalizeColorForCompare(req.color) ||
          colorsAreSimilar(manualLoaded.color, req.color);

        if (typeMatch && colorMatch) {
          exactMatches++;
        } else if (typeMatch) {
          typeOnlyMatches++;
        } else {
          missingTypes++;
        }
        continue;
      }
    }

    // Auto-match with nozzle-aware filtering
    let candidates = loadedFilaments.filter((f) => !usedTrayIds.has(f.globalTrayId));
    if (req.nozzle_id != null) {
      const nozzleFiltered = candidates.filter((f) => f.extruderId === req.nozzle_id);
      if (nozzleFiltered.length > 0) {
        candidates = nozzleFiltered;
      }
    }

    const exactMatch = candidates.find(
      (f) =>
        f.type?.toUpperCase() === req.type?.toUpperCase() &&
        normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
    );
    const similarMatch = exactMatch
      ? undefined
      : candidates.find(
          (f) =>
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            colorsAreSimilar(f.color, req.color)
        );
    const typeOnlyMatch =
      exactMatch || similarMatch
        ? undefined
        : candidates.find(
            (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
          );
    const loaded = exactMatch ?? similarMatch ?? typeOnlyMatch;

    if (loaded) {
      usedTrayIds.add(loaded.globalTrayId);
    }

    if (exactMatch || similarMatch) {
      exactMatches++;
    } else if (typeOnlyMatch) {
      typeOnlyMatches++;
    } else {
      missingTypes++;
    }
  }

  const totalSlots = filamentReqs.length;
  let status: PrinterMatchStatus = 'full';
  if (missingTypes > 0) {
    status = 'missing';
  } else if (typeOnlyMatches > 0) {
    status = 'partial';
  }

  return { exactMatches, typeOnlyMatches, missingTypes, totalSlots, status };
}

/**
 * Compute AMS mapping with manual overrides applied.
 */
function computeMappingWithOverrides(
  filamentReqs: { filaments: FilamentRequirement[] } | undefined,
  printerStatus: PrinterStatus | undefined,
  manualMappings: Record<number, number>
): number[] | undefined {
  if (!filamentReqs?.filaments || filamentReqs.filaments.length === 0) return undefined;

  const loadedFilaments = buildLoadedFilaments(printerStatus);
  if (loadedFilaments.length === 0) return undefined;

  const usedTrayIds = new Set<number>(Object.values(manualMappings));
  const comparisons: { slot_id: number; globalTrayId: number }[] = [];

  for (const req of filamentReqs.filaments) {
    const slotId = req.slot_id || 0;

    // Check manual override first
    if (slotId > 0 && manualMappings[slotId] !== undefined) {
      comparisons.push({ slot_id: slotId, globalTrayId: manualMappings[slotId] });
      continue;
    }

    // Auto-match with nozzle-aware filtering
    let candidates = loadedFilaments.filter((f) => !usedTrayIds.has(f.globalTrayId));
    if (req.nozzle_id != null) {
      const nozzleFiltered = candidates.filter((f) => f.extruderId === req.nozzle_id);
      if (nozzleFiltered.length > 0) {
        candidates = nozzleFiltered;
      }
    }

    const exactMatch = candidates.find(
      (f) =>
        f.type?.toUpperCase() === req.type?.toUpperCase() &&
        normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
    );
    const similarMatch = exactMatch
      ? undefined
      : candidates.find(
          (f) =>
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            colorsAreSimilar(f.color, req.color)
        );
    const typeOnlyMatch =
      exactMatch || similarMatch
        ? undefined
        : candidates.find(
            (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
          );
    const loaded = exactMatch ?? similarMatch ?? typeOnlyMatch;

    if (loaded) {
      usedTrayIds.add(loaded.globalTrayId);
    }

    comparisons.push({ slot_id: slotId, globalTrayId: loaded?.globalTrayId ?? -1 });
  }

  const maxSlotId = Math.max(...comparisons.map((f) => f.slot_id || 0));
  if (maxSlotId <= 0) return undefined;

  const mapping = new Array(maxSlotId).fill(-1);
  comparisons.forEach((f) => {
    if (f.slot_id && f.slot_id > 0) {
      mapping[f.slot_id - 1] = f.globalTrayId;
    }
  });

  return mapping;
}

/**
 * Default per-printer config (use default mapping).
 */
const DEFAULT_PRINTER_CONFIG: PerPrinterConfig = {
  useDefault: true,
  manualMappings: {},
  autoConfigured: false,
};

/**
 * Hook to manage filament mapping for multiple printers.
 * Fetches printer status for all selected printers and computes per-printer mappings.
 */
export function useMultiPrinterFilamentMapping(
  selectedPrinterIds: number[],
  printers: Printer[] | undefined,
  filamentReqs: { filaments: FilamentRequirement[] } | undefined,
  defaultMappings: Record<number, number>,
  perPrinterConfigs: Record<number, PerPrinterConfig>,
  setPerPrinterConfigs: React.Dispatch<React.SetStateAction<Record<number, PerPrinterConfig>>>
): UseMultiPrinterFilamentMappingResult {
  // Fetch printer status for all selected printers in parallel
  const statusQueries = useQueries({
    queries: selectedPrinterIds.map((printerId) => ({
      queryKey: ['printer-status', printerId],
      queryFn: () => api.getPrinterStatus(printerId),
      enabled: selectedPrinterIds.length > 0,
      staleTime: 5000, // Consider data fresh for 5 seconds
    })),
  });

  // Build results for each printer
  const printerResults = useMemo((): PrinterMappingResult[] => {
    return selectedPrinterIds.map((printerId, index) => {
      const query = statusQueries[index];
      const printerStatus = query?.data;
      const printer = printers?.find((p) => p.id === printerId);
      const printerName = printer?.name || `Printer ${printerId}`;

      const loadedFilaments = buildLoadedFilaments(printerStatus);
      const config = perPrinterConfigs[printerId] || DEFAULT_PRINTER_CONFIG;

      // Compute auto mapping for this printer
      const autoMapping = computeAmsMapping(filamentReqs, printerStatus);

      // Determine which mappings to use:
      // If printer has override (useDefault=false), use its custom mappings
      // Otherwise use the default mappings
      const effectiveMappings = !config.useDefault
        ? config.manualMappings
        : defaultMappings;

      // Compute final mapping with overrides
      const finalMapping = computeMappingWithOverrides(filamentReqs, printerStatus, effectiveMappings);

      // Compute match details
      const matchDetails = computeMatchDetails(
        filamentReqs?.filaments,
        loadedFilaments,
        effectiveMappings
      );

      return {
        printerId,
        printerName,
        status: printerStatus,
        isLoading: query?.isLoading ?? false,
        loadedFilaments,
        autoMapping,
        finalMapping,
        matchStatus: matchDetails.status,
        exactMatches: matchDetails.exactMatches,
        typeOnlyMatches: matchDetails.typeOnlyMatches,
        missingTypes: matchDetails.missingTypes,
        totalSlots: matchDetails.totalSlots,
        config,
      };
    });
  }, [selectedPrinterIds, statusQueries, printers, filamentReqs, perPrinterConfigs, defaultMappings]);

  const isLoading = statusQueries.some((q) => q.isLoading);

  // Update config for a specific printer
  const updatePrinterConfig = (printerId: number, updates: Partial<PerPrinterConfig>) => {
    setPerPrinterConfigs((prev) => ({
      ...prev,
      [printerId]: {
        ...(prev[printerId] || DEFAULT_PRINTER_CONFIG),
        ...updates,
      },
    }));
  };

  // Auto-configure a specific printer based on its loaded filaments
  const autoConfigurePrinter = (printerId: number) => {
    const result = printerResults.find((r) => r.printerId === printerId);
    if (!result || !result.status || !filamentReqs?.filaments) return;

    // Compute optimal mapping for this printer
    const autoMapping = computeAmsMapping(filamentReqs, result.status);
    if (!autoMapping) return;

    // Convert autoMapping array to manualMappings record
    const manualMappings: Record<number, number> = {};
    autoMapping.forEach((globalTrayId, index) => {
      if (globalTrayId !== -1) {
        manualMappings[index + 1] = globalTrayId;
      }
    });

    updatePrinterConfig(printerId, {
      useDefault: false,
      manualMappings,
      autoConfigured: true,
    });
  };

  // Auto-configure all printers
  const autoConfigureAll = () => {
    for (const printerId of selectedPrinterIds) {
      autoConfigurePrinter(printerId);
    }
  };

  // Get final mapping for a specific printer (for submission)
  const getFinalMapping = (printerId: number): number[] | undefined => {
    const result = printerResults.find((r) => r.printerId === printerId);
    return result?.finalMapping;
  };

  // Check if all printers have acceptable mappings (no missing types)
  const allPrintersReady = printerResults.every((r) => r.matchStatus !== 'missing');

  return {
    printerResults,
    isLoading,
    perPrinterConfigs,
    updatePrinterConfig,
    autoConfigureAll,
    autoConfigurePrinter,
    getFinalMapping,
    allPrintersReady,
  };
}
