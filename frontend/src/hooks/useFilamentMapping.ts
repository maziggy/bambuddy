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
  const amsExtruderMap = printerStatus?.ams_extruder_map;
  const hasDualNozzle = amsExtruderMap && Object.keys(amsExtruderMap).length > 0;

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
          trayInfoIdx: tray.tray_info_idx || '',
          extruderId: amsExtruderMap?.[String(amsUnit.id)],
        });
      }
    });
  });

  // Add external spool(s) if loaded
  for (const extTray of printerStatus?.vt_tray ?? []) {
    if (extTray.tray_type) {
      const color = normalizeColor(extTray.tray_color);
      const trayId = extTray.id ?? 254;
      const hasDualExternal = (printerStatus?.vt_tray?.length ?? 0) > 1;
      filaments.push({
        type: extTray.tray_type,
        color,
        colorName: getColorName(color),
        amsId: -1,
        trayId: trayId - 254,
        isHt: false,
        isExternal: true,
        label: hasDualExternal ? (trayId === 254 ? 'Ext-L' : 'Ext-R') : 'External',
        globalTrayId: trayId,
        trayInfoIdx: extTray.tray_info_idx || '',
        extruderId: hasDualNozzle ? (trayId - 254) : undefined,
      });
    }
  }

  return filaments;
}

/**
 * Compute AMS mapping for a printer given filament requirements and printer status.
 * This is a non-hook version that can be called imperatively (e.g., in a loop for multiple printers).
 *
 * Priority: unique tray_info_idx match > exact color match > similar color match > type-only match
 *
 * The tray_info_idx is a filament type identifier stored in the 3MF file when the user
 * slices (e.g., "GFA00" for generic PLA, "P4d64437" for custom presets). If the same
 * tray_info_idx appears in only ONE available tray, we use that tray. If multiple trays
 * have the same tray_info_idx (e.g., two spools of generic PLA), we fall back to color
 * matching among those trays.
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
    const reqTrayInfoIdx = req.tray_info_idx || '';

    // Get available trays (not already used)
    let available = loadedFilaments.filter((f) => !usedTrayIds.has(f.globalTrayId));

    // Nozzle-aware filtering: restrict to trays on the correct nozzle
    if (req.nozzle_id != null) {
      const nozzleFiltered = available.filter((f) => f.extruderId === req.nozzle_id);
      if (nozzleFiltered.length > 0) {
        available = nozzleFiltered;
      }
    }

    let idxMatch: LoadedFilament | undefined;
    let exactMatch: LoadedFilament | undefined;
    let similarMatch: LoadedFilament | undefined;
    let typeOnlyMatch: LoadedFilament | undefined;

    // Check if tray_info_idx is unique among available trays
    if (reqTrayInfoIdx) {
      const idxMatches = available.filter((f) => f.trayInfoIdx === reqTrayInfoIdx);
      if (idxMatches.length === 1) {
        // Unique tray_info_idx - use it as definitive match
        idxMatch = idxMatches[0];
      } else if (idxMatches.length > 1) {
        // Multiple trays with same tray_info_idx - use color matching among them
        exactMatch = idxMatches.find(
          (f) =>
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
        );
        if (!exactMatch) {
          similarMatch = idxMatches.find(
            (f) =>
              f.type?.toUpperCase() === req.type?.toUpperCase() &&
              colorsAreSimilar(f.color, req.color)
          );
        }
        if (!exactMatch && !similarMatch) {
          typeOnlyMatch = idxMatches.find(
            (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
          );
        }
      }
    }

    // If no idx match, do standard type/color matching on all available trays
    if (!idxMatch && !exactMatch && !similarMatch && !typeOnlyMatch) {
      exactMatch = available.find(
        (f) =>
          f.type?.toUpperCase() === req.type?.toUpperCase() &&
          normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
      );
      if (!exactMatch) {
        similarMatch = available.find(
          (f) =>
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            colorsAreSimilar(f.color, req.color)
        );
      }
      if (!exactMatch && !similarMatch) {
        typeOnlyMatch = available.find(
          (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
        );
      }
    }

    const loaded = idxMatch || exactMatch || similarMatch || typeOnlyMatch || undefined;

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
  /** Unique spool identifier (e.g., "GFA00", "P4d64437") */
  trayInfoIdx?: string;
  /** Extruder ID for dual-nozzle printers (0=right, 1=left) */
  extruderId?: number;
}

/**
 * Represents a required filament from the 3MF file.
 */
export interface FilamentRequirement {
  slot_id: number;
  type: string;
  color: string;
  used_grams: number;
  /** Unique spool identifier from slicing (e.g., "GFA00", "P4d64437") */
  tray_info_idx?: string;
  /** Target nozzle for dual-nozzle printers (0=right, 1=left) */
  nozzle_id?: number;
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
    return buildLoadedFilaments(printerStatus);
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

      // Auto-match: Find a loaded filament
      // Priority: unique tray_info_idx match > exact color match > similar color match > type-only match
      // IMPORTANT: Exclude trays that are already assigned (manually or auto)
      const reqTrayInfoIdx = req.tray_info_idx || '';

      // Get available trays (not already used)
      let available = loadedFilaments.filter((f) => !usedTrayIds.has(f.globalTrayId));

      // Nozzle-aware filtering: restrict to trays on the correct nozzle
      if (req.nozzle_id != null) {
        const nozzleFiltered = available.filter((f) => f.extruderId === req.nozzle_id);
        if (nozzleFiltered.length > 0) {
          available = nozzleFiltered;
        }
      }

      let idxMatch: LoadedFilament | undefined;
      let exactMatch: LoadedFilament | undefined;
      let similarMatch: LoadedFilament | undefined;
      let typeOnlyMatch: LoadedFilament | undefined;

      // Check if tray_info_idx is unique among available trays
      if (reqTrayInfoIdx) {
        const idxMatches = available.filter((f) => f.trayInfoIdx === reqTrayInfoIdx);
        if (idxMatches.length === 1) {
          // Unique tray_info_idx - use it as definitive match
          idxMatch = idxMatches[0];
        } else if (idxMatches.length > 1) {
          // Multiple trays with same tray_info_idx - use color matching among them
          exactMatch = idxMatches.find(
            (f) =>
              f.type?.toUpperCase() === req.type?.toUpperCase() &&
              normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
          );
          if (!exactMatch) {
            similarMatch = idxMatches.find(
              (f) =>
                f.type?.toUpperCase() === req.type?.toUpperCase() &&
                colorsAreSimilar(f.color, req.color)
            );
          }
          if (!exactMatch && !similarMatch) {
            typeOnlyMatch = idxMatches.find(
              (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
            );
          }
        }
      }

      // If no idx match, do standard type/color matching on all available trays
      if (!idxMatch && !exactMatch && !similarMatch && !typeOnlyMatch) {
        exactMatch = available.find(
          (f) =>
            f.type?.toUpperCase() === req.type?.toUpperCase() &&
            normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
        );
        if (!exactMatch) {
          similarMatch = available.find(
            (f) =>
              f.type?.toUpperCase() === req.type?.toUpperCase() &&
              colorsAreSimilar(f.color, req.color)
          );
        }
        if (!exactMatch && !similarMatch) {
          typeOnlyMatch = available.find(
            (f) => f.type?.toUpperCase() === req.type?.toUpperCase()
          );
        }
      }

      const loaded = idxMatch || exactMatch || similarMatch || typeOnlyMatch || undefined;

      // Mark this tray as used so it won't be assigned to another slot
      if (loaded) {
        usedTrayIds.add(loaded.globalTrayId);
      }

      const hasFilament = !!loaded;
      const typeMatch = hasFilament;
      // idxMatch is always considered a color match (same spool = same color)
      const colorMatch = !!idxMatch || !!exactMatch || !!similarMatch;

      // Status: match (tray_info_idx, type+color, or similar color), type_only (type ok, color very different), mismatch (type not found)
      let status: FilamentStatus;
      if (idxMatch || exactMatch || similarMatch) {
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
