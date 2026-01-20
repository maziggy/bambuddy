import { useMemo } from 'react';
import { getColorName } from '../utils/colors';
import {
  normalizeColor,
  normalizeColorForCompare,
  colorsAreSimilar,
  formatSlotLabel,
  getGlobalTrayId,
} from '../utils/amsHelpers';
import type { PrinterStatus } from '../api/client';

/**
 * Build loaded filaments list from printer status (non-hook version).
 * Extracts filaments from all AMS units (regular and HT) and external spool.
 */
export function buildLoadedFilaments(printerStatus: PrinterStatus | undefined): LoadedFilament[] {
  const filaments: LoadedFilament[] = [];

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
}

/**
 * Compute AMS mapping for a printer given filament requirements and printer status.
 * This is a non-hook version that can be called imperatively (e.g., in a loop for multiple printers).
 *
 * @param filamentReqs - Required filaments from the 3MF file
 * @param printerStatus - Current printer status with AMS information
 * @returns AMS mapping array or undefined if no mapping needed
 */
export function computeAmsMapping(
  filamentReqs: { filaments: FilamentRequirement[] } | undefined,
  printerStatus: PrinterStatus | undefined
): number[] | undefined {
  if (!filamentReqs?.filaments || filamentReqs.filaments.length === 0) return undefined;

  const loadedFilaments = buildLoadedFilaments(printerStatus);
  if (loadedFilaments.length === 0) return undefined;

  // Track which trays have been assigned to avoid duplicates
  const usedTrayIds = new Set<number>();

  const comparisons = filamentReqs.filaments.map((req) => {
    // Auto-match: Find a loaded filament that matches by TYPE
    // Priority: exact color match > similar color match > type-only match
    const exactMatch = loadedFilaments.find(
      (f) =>
        !usedTrayIds.has(f.globalTrayId) &&
        f.type?.toUpperCase() === req.type?.toUpperCase() &&
        normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
    );
    const similarMatch =
      !exactMatch &&
      loadedFilaments.find(
        (f) =>
          !usedTrayIds.has(f.globalTrayId) &&
          f.type?.toUpperCase() === req.type?.toUpperCase() &&
          colorsAreSimilar(f.color, req.color)
      );
    const typeOnlyMatch =
      !exactMatch &&
      !similarMatch &&
      loadedFilaments.find(
        (f) =>
          !usedTrayIds.has(f.globalTrayId) && f.type?.toUpperCase() === req.type?.toUpperCase()
      );
    const loaded = exactMatch || similarMatch || typeOnlyMatch || undefined;

    // Mark this tray as used so it won't be assigned to another slot
    if (loaded) {
      usedTrayIds.add(loaded.globalTrayId);
    }

    return {
      slot_id: req.slot_id,
      globalTrayId: loaded?.globalTrayId ?? -1,
    };
  });

  // Find the max slot_id to determine array size
  const maxSlotId = Math.max(...comparisons.map((f) => f.slot_id || 0));
  if (maxSlotId <= 0) return undefined;

  // Create array with -1 for all positions
  const mapping = new Array(maxSlotId).fill(-1);

  // Fill in tray IDs at correct positions (slot_id - 1)
  comparisons.forEach((f) => {
    if (f.slot_id && f.slot_id > 0) {
      mapping[f.slot_id - 1] = f.globalTrayId;
    }
  });

  return mapping;
}

/**
 * Represents a loaded filament in the printer's AMS/HT/External spool holder.
 */
export interface LoadedFilament {
  type: string;
  color: string;
  colorName: string;
  amsId: number;
  trayId: number;
  isHt: boolean;
  isExternal: boolean;
  label: string;
  globalTrayId: number;
}

/**
 * Represents a required filament from the 3MF file.
 */
export interface FilamentRequirement {
  slot_id: number;
  type: string;
  color: string;
  used_grams: number;
}

/**
 * Status of filament comparison between required and loaded.
 */
export type FilamentStatus = 'match' | 'type_only' | 'mismatch' | 'empty';

/**
 * Result of comparing a required filament with loaded filaments.
 */
export interface FilamentComparison extends FilamentRequirement {
  loaded: LoadedFilament | undefined;
  hasFilament: boolean;
  typeMatch: boolean;
  colorMatch: boolean;
  status: FilamentStatus;
  isManual: boolean;
}

interface FilamentRequirementsResponse {
  filaments: FilamentRequirement[];
}

interface UseFilamentMappingResult {
  /** List of all filaments loaded in the printer */
  loadedFilaments: LoadedFilament[];
  /** Comparison results for each required filament */
  filamentComparison: FilamentComparison[];
  /** AMS mapping array for the print command */
  amsMapping: number[] | undefined;
  /** Whether any required filament type is not loaded */
  hasTypeMismatch: boolean;
  /** Whether any required filament has a color mismatch */
  hasColorMismatch: boolean;
}

/**
 * Hook to build loaded filaments list from printer status.
 * Extracts filaments from all AMS units (regular and HT) and external spool.
 */
export function useLoadedFilaments(
  printerStatus: PrinterStatus | undefined
): LoadedFilament[] {
  return useMemo(() => {
    const filaments: LoadedFilament[] = [];

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
}

/**
 * Hook to compare required filaments with loaded filaments and build AMS mapping.
 * Handles both auto-matching and manual overrides.
 *
 * @param filamentReqs - Required filaments from the 3MF file
 * @param printerStatus - Current printer status with AMS information
 * @param manualMappings - Manual slot overrides (slot_id -> globalTrayId)
 */
export function useFilamentMapping(
  filamentReqs: FilamentRequirementsResponse | undefined,
  printerStatus: PrinterStatus | undefined,
  manualMappings: Record<number, number>
): UseFilamentMappingResult {
  const loadedFilaments = useLoadedFilaments(printerStatus);

  const filamentComparison = useMemo(() => {
    if (!filamentReqs?.filaments || filamentReqs.filaments.length === 0) return [];

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
          const colorMatch =
            normalizeColorForCompare(manualLoaded.color) === normalizeColorForCompare(req.color) ||
            colorsAreSimilar(manualLoaded.color, req.color);

          let status: FilamentStatus;
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
        (f) =>
          !usedTrayIds.has(f.globalTrayId) &&
          f.type?.toUpperCase() === req.type?.toUpperCase() &&
          normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
      );
      const similarMatch =
        !exactMatch &&
        loadedFilaments.find(
          (f) =>
            !usedTrayIds.has(f.globalTrayId) &&
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            colorsAreSimilar(f.color, req.color)
        );
      const typeOnlyMatch =
        !exactMatch &&
        !similarMatch &&
        loadedFilaments.find(
          (f) =>
            !usedTrayIds.has(f.globalTrayId) && f.type?.toUpperCase() === req.type?.toUpperCase()
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
      let status: FilamentStatus;
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

  // Build AMS mapping from matched filaments
  // Format: array matching 3MF filament slot structure
  // Position = slot_id - 1 (0-indexed), value = global tray ID or -1 for unused
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
  const hasColorMismatch = filamentComparison.some((f) => f.status === 'type_only');

  return {
    loadedFilaments,
    filamentComparison,
    amsMapping,
    hasTypeMismatch,
    hasColorMismatch,
  };
}
