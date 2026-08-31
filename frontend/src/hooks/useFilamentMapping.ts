import { useMemo } from 'react';
import { getColorName } from '../utils/colors';
import type { RackGroupInfo } from '../components/PrintModal/types';
import {
  normalizeColor,
  normalizeColorForCompare,
  colorsAreSimilar,
  filamentTypesCompatible,
  findNearestSimilar,
  formatSlotLabel,
  getGlobalTrayId,
  preferLowestSortKey,
  compareSortKeys,
} from '../utils/amsHelpers';
import type { PrinterStatus, SlotSpoolIdentity } from '../api/client';

/** Global-tray-id → the identity of the spool Bambuddy has bound to that slot. */
export type SlotSpoolIdentities = Map<number, SlotSpoolIdentity>;

/**
 * Name a slot after the spool bound to it, the way the printer card does.
 *
 * Telemetry can only ever say "PLA" plus a colour hex for anything that isn't
 * a Bambu spool: the tray record has no brand field, `tray_sub_brands` stays
 * empty, and the hex gets resolved against Bambu's own colour catalogue. So a
 * Devil Design PLA Basic Orange the operator assigned in Bambuddy read back as
 * "PLA (Sunflower Yellow)" here while the printer card named it correctly.
 *
 * Returns null when the slot has no binding or the binding says nothing
 * useful, and the caller keeps the telemetry description it had before.
 */
function spoolDisplayName(identity: SlotSpoolIdentity | undefined): string | null {
  if (!identity) return null;
  // Same order the hover card prints: brand, then material, then subtype.
  const parts = [identity.brand, identity.material, identity.subtype].filter(Boolean);
  return parts.length > 0 ? parts.join(' ') : null;
}

/**
 * Build loaded filaments list from printer status (non-hook version).
 * Extracts filaments from all AMS units (regular and HT) and external spool.
 *
 * `slotSpools` is optional and display-only: it renames slots after their
 * bound spool without touching `type`, `color` or `trayInfoIdx`, so matching
 * and the dispatcher keep drawing on the printer's own telemetry.
 */
export function buildLoadedFilaments(
  printerStatus: PrinterStatus | undefined,
  slotSpools?: SlotSpoolIdentities,
): LoadedFilament[] {
  const filaments: LoadedFilament[] = [];
  const amsExtruderMap = printerStatus?.ams_extruder_map;
  // Dual-nozzle detection. The backend always emits a 2-entry nozzles array
  // (default-stub second entry for single-nozzle printers), so length is not
  // a reliable signal. Real second-nozzle hardware sets `nozzle_diameter` from
  // the MQTT `right_nozzle_diameter` field (bambu_mqtt.py:2619-2621); without
  // that field, nozzles[1] stays at its empty default. Belt-and-braces: a
  // populated ams_extruder_map (dual-nozzle with AMS) and >1 vt_tray (only
  // dual-nozzle hardware exposes multiple external feeds) each independently
  // imply dual-nozzle — keep them as fallbacks for any firmware rev that
  // surfaces one signal but not the other. (#1257)
  const hasDualNozzle =
    Boolean(printerStatus?.nozzles?.[1]?.nozzle_diameter)
    || (amsExtruderMap && Object.keys(amsExtruderMap).length > 0)
    || (printerStatus?.vt_tray?.length ?? 0) > 1;

  // Add filaments from all AMS units (regular and HT)
  printerStatus?.ams?.forEach((amsUnit) => {
    const isHt = amsUnit.tray.length === 1; // AMS-HT has single tray
    amsUnit.tray.forEach((tray) => {
      if (tray.tray_type) {
        const color = normalizeColor(tray.tray_color);
        const globalTrayId = getGlobalTrayId(amsUnit.id, tray.id, false);
        const identity = slotSpools?.get(globalTrayId);
        filaments.push({
          type: tray.tray_type,
          color,
          colorName: identity?.color_name?.trim() || getColorName(color, tray.tray_sub_brands),
          amsId: amsUnit.id,
          trayId: tray.id,
          isHt,
          isExternal: false,
          label: formatSlotLabel(amsUnit.id, tray.id, isHt, false),
          globalTrayId,
          trayInfoIdx: tray.tray_info_idx || '',
          traySubBrands: tray.tray_sub_brands || '',
          spoolName: spoolDisplayName(identity) ?? undefined,
          extruderId: amsExtruderMap?.[String(amsUnit.id)],
          remain: tray.remain ?? -1,
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
      // The external holder's global tray id IS the tray id (254 / 255), which
      // is how the backend keys it too — see `_ams_key_to_global`.
      const identity = slotSpools?.get(trayId);
      filaments.push({
        type: extTray.tray_type,
        color,
        colorName: identity?.color_name?.trim() || getColorName(color, extTray.tray_sub_brands),
        amsId: -1,
        trayId: trayId - 254,
        isHt: false,
        isExternal: true,
        label: hasDualExternal ? (trayId === 254 ? 'Ext-L' : 'Ext-R') : 'External',
        globalTrayId: trayId,
        trayInfoIdx: extTray.tray_info_idx || '',
        traySubBrands: extTray.tray_sub_brands || '',
        spoolName: spoolDisplayName(identity) ?? undefined,
        extruderId: hasDualNozzle ? (255 - trayId) : undefined,
        remain: extTray.remain ?? -1,
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
  printerStatus: PrinterStatus | undefined,
  preferLowest?: boolean,
  inventoryByTrayId?: Map<number, number>,
): number[] | undefined {
  const loadedFilaments = buildLoadedFilaments(printerStatus);
  if (loadedFilaments.length === 0) return undefined;

  // FTS routes any AMS slot to any extruder, so per-nozzle slot restriction
  // doesn't apply when it's installed (#1162).
  const ftsActive = printerStatus?.fila_switch?.installed === true;

  // No manual overrides on this path — it maps a printer the user is not looking
  // at (per-printer fan-out), so there is no panel to override a slot in.
  return buildAmsMapping(
    buildFilamentComparison(filamentReqs, loadedFilaments, {}, preferLowest, inventoryByTrayId, ftsActive),
  );
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
  /** Filament subtype name (e.g., "PLA Basic", "PLA Matte", "PETG HF") */
  traySubBrands?: string;
  /** "Devil Design PLA Basic" — the spool Bambuddy has bound to this slot,
   *  when there is one. Display only; prefer it over `traySubBrands`, which
   *  the printer leaves empty for everything that isn't a Bambu spool. */
  spoolName?: string;
  /** Extruder ID for dual-nozzle printers (0=right, 1=left) */
  extruderId?: number;
  /** Remaining filament percentage (0-100), -1 = unknown */
  remain: number;
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
  /** Filament group this slot prints in, on a nozzle-rack machine (#1784).
   *  The group is the slicer's logical nozzle, so it is what a rack position
   *  gets chosen for. Absent on every other model. */
  group_id?: number;
  /** What that group needs of a hotend, for filtering the rack positions it
   *  can be sent to. */
  group?: RackGroupInfo;
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

export interface FilamentRequirementsResponse {
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
  printerStatus: PrinterStatus | undefined,
  slotSpools?: SlotSpoolIdentities,
): LoadedFilament[] {
  return useMemo(() => {
    return buildLoadedFilaments(printerStatus, slotSpools);
  }, [printerStatus, slotSpools]);
}

/**
 * Does the tray we picked actually carry the colour the slice asked for?
 *
 * Shared by the manual and auto branches below so the two can never disagree
 * about the same tray again (#2687). Exact hex first, then the perceptual
 * tolerance, so a spool the printer reports one shade off still reads as a
 * match.
 *
 * A requirement with no colour at all is not a mismatch — the 3MF simply
 * didn't ask for one (`filament_requirements.py` defaults it to `""`), so any
 * loaded colour satisfies it. Loaded trays always have a colour: buildLoaded-
 * Filaments falls back to grey when MQTT reports none.
 */
function coloursMatch(loadedColor: string | undefined, requiredColor: string | undefined): boolean {
  const required = normalizeColorForCompare(requiredColor);
  if (!required) return true;
  return (
    normalizeColorForCompare(loadedColor) === required || colorsAreSimilar(loadedColor, requiredColor)
  );
}

/**
 * Compare required filaments with loaded filaments (non-hook version).
 *
 * Tray assignment is stateful across the list — a tray matched to one slot is
 * not offered to the next — so this must be run over exactly the slots of one
 * print, never a union of several plates: two plates that share a colour on
 * different slots would otherwise compete for the same tray and one of them
 * would fall through to a worse match, or to none (#2551 follow-up).
 */
export function buildFilamentComparison(
  filamentReqs: FilamentRequirementsResponse | undefined,
  loadedFilaments: LoadedFilament[],
  manualMappings: Record<number, number>,
  preferLowest?: boolean,
  inventoryByTrayId?: Map<number, number>,
  ftsActive = false,
): FilamentComparison[] {
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
        const typeMatch = filamentTypesCompatible(manualLoaded.type, req.type);
        const colorMatch = coloursMatch(manualLoaded.color, req.color);

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

    // Nozzle-aware filtering: restrict to trays on the correct nozzle.
    // This is a hard filter — cross-nozzle assignment causes print failures.
    // Skip when an FTS is installed: it can route any slot to either extruder.
    if (req.nozzle_id != null && !ftsActive) {
      available = available.filter((f) => f.extruderId === req.nozzle_id);
    }

    // Sort lowest-first when the preference is on. Inventory-tracked spools
    // sort before MQTT-only ones; see preferLowestSortKey for the rationale.
    if (preferLowest) {
      available = [...available].sort((a, b) =>
        compareSortKeys(
          preferLowestSortKey(a, inventoryByTrayId),
          preferLowestSortKey(b, inventoryByTrayId),
        ),
      );
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
        if (preferLowest) {
          idxMatches.sort((a, b) =>
            compareSortKeys(
              preferLowestSortKey(a, inventoryByTrayId),
              preferLowestSortKey(b, inventoryByTrayId),
            ),
          );
        }
        exactMatch = idxMatches.find(
          (f) =>
            filamentTypesCompatible(f.type, req.type) &&
            normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
        );
        if (!exactMatch) {
          similarMatch = findNearestSimilar(
            idxMatches.filter((f) => filamentTypesCompatible(f.type, req.type)),
            req.color,
            (f) => f.color,
          );
        }
        if (!exactMatch && !similarMatch) {
          typeOnlyMatch = idxMatches.find(
            (f) => filamentTypesCompatible(f.type, req.type)
          );
        }
      }
    }

    // If no idx match, do standard type/color matching on all available trays
    if (!idxMatch && !exactMatch && !similarMatch && !typeOnlyMatch) {
      exactMatch = available.find(
        (f) =>
          filamentTypesCompatible(f.type, req.type) &&
          normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
      );
      if (!exactMatch) {
        similarMatch = findNearestSimilar(
          available.filter((f) => filamentTypesCompatible(f.type, req.type)),
          req.color,
          (f) => f.color,
        );
      }
      if (!exactMatch && !similarMatch) {
        typeOnlyMatch = available.find(
          (f) => filamentTypesCompatible(f.type, req.type)
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
    // #2687: judge the colour on the tray we actually picked, never on which
    // branch found it. tray_info_idx identifies the filament *variant* — GFA00
    // is PLA Basic, GFA01 PLA Matte, GFA17 PLA Translucent — not an individual
    // spool, so one Matte spool idx-matches every Matte requirement whatever
    // colour it is. The old rule ("same spool = same color") therefore reported
    // red-required-on-green-loaded as a match, while manually picking that same
    // tray reported the mismatch honestly. Variant still decides *selection*
    // (#2650: Basic is not Matte) — it just no longer decides the verdict.
    const colorMatch = hasFilament && coloursMatch(loaded.color, req.color);

    // No tray of the required type at all is a type mismatch; otherwise the
    // colour decides between a full match and type-only.
    let status: FilamentStatus;
    if (!hasFilament) {
      status = 'mismatch';
    } else if (colorMatch) {
      status = 'match';
    } else {
      status = 'type_only';
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
}

/**
 * Build the AMS mapping array the print command carries (non-hook version).
 * Position = slot_id - 1 (0-indexed), value = global tray ID, or -1 for a slot
 * with no matching tray. Indexed by the 3MF's own slot ids, which are global to
 * the file, so a plate that only prints slot 3 still emits `[-1, -1, tray]`.
 */
export function buildAmsMapping(filamentComparison: FilamentComparison[]): number[] | undefined {
  if (filamentComparison.length === 0) return undefined;

  const maxSlotId = Math.max(...filamentComparison.map((f) => f.slot_id || 0));
  if (maxSlotId <= 0) return undefined;

  const mapping = new Array(maxSlotId).fill(-1);
  filamentComparison.forEach((f) => {
    if (f.slot_id && f.slot_id > 0) {
      mapping[f.slot_id - 1] = f.loaded?.globalTrayId ?? -1;
    }
  });

  return mapping;
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
  manualMappings: Record<number, number>,
  preferLowest?: boolean,
  inventoryByTrayId?: Map<number, number>,
  slotSpools?: SlotSpoolIdentities,
): UseFilamentMappingResult {
  const loadedFilaments = useLoadedFilaments(printerStatus, slotSpools);

  // FTS routes any AMS slot to any extruder, so per-nozzle slot restriction
  // doesn't apply when it's installed (#1162).
  const ftsActive = printerStatus?.fila_switch?.installed === true;

  const filamentComparison = useMemo(
    () =>
      buildFilamentComparison(
        filamentReqs,
        loadedFilaments,
        manualMappings,
        preferLowest,
        inventoryByTrayId,
        ftsActive,
      ),
    [filamentReqs, loadedFilaments, manualMappings, preferLowest, ftsActive, inventoryByTrayId],
  );

  // Don't emit a mapping until the printer's trays are known. With no loaded
  // filaments (e.g. printerStatus still loading), buildFilamentComparison marks
  // every required slot unmatched and buildAmsMapping would serialize an
  // all-[-1] array — which the backend used to treat as an explicit
  // external-spool selection, silently printing to an empty feed (#2589).
  // Return undefined instead so the scheduler resolves the mapping from live
  // status at dispatch. Mirrors the guard in computeAmsMapping.
  const amsMapping = useMemo(
    () => (loadedFilaments.length === 0 ? undefined : buildAmsMapping(filamentComparison)),
    [filamentComparison, loadedFilaments.length],
  );

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
