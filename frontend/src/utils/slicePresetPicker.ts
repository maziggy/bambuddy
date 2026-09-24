// Pure-function helpers for the SliceModal's per-slot preset selection.
//
// Extracted out of `SliceModal.tsx` so they can be unit-tested directly and
// so the modal component file only exports React components (the
// `react-refresh/only-export-components` lint rule requires this for HMR to
// work correctly — exporting a non-component from a component file breaks
// fast-refresh).
//
// Selection rules:
// - Tier order is local → orca_cloud → cloud → standard. Local imports
//   outrank everything else because the user explicitly imported them
//   for this install; standard (bundled) is the final fallback.
// - The backend does NOT dedup tiers, so each helper walks all four
//   and the caller relies on the order, not a single merged list.
// - `pickProcessDefault` honours a 3MF's embedded process preset when
//   it exists and isn't printer-incompatible; otherwise prefers a
//   match-on-printer pick, then unknown-compat, then plain priority --
//   preferring a middle-of-the-road layer height within each of those
//   (#2982).
// - `pickFilamentForSlot` partitions candidates into compatible/unknown
//   vs mismatch buckets and only consults the mismatch bucket when
//   the compatible bucket is empty (#1851). Material type partitions the
//   same way: a preset that states a different material than the plate
//   asks for is never the right answer (#2982).

import type {
  PresetRef,
  PresetSource,
  UnifiedPreset,
  UnifiedPresetsResponse,
} from '../api/client';
import { colorsAreSimilar, normalizeColorForCompare } from './amsHelpers';
import {
  presetCompatibility,
  type PrinterCompatibilityIndex,
} from './slicerPrinterMatch';

export type Slot = 'printer' | 'process' | 'filament';

export const SLICE_MODAL_TIER_ORDER = ['local', 'orca_cloud', 'cloud', 'standard'] as const;

const TIER_BONUS: Record<PresetSource, number> = {
  local: 1.75,
  orca_cloud: 1.5,
  cloud: 1.0,
  standard: 0.5,
};

export function pickDefault(by: UnifiedPresetsResponse, slot: Slot): PresetRef | null {
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    const list = by[tier][slot];
    if (list.length > 0) {
      return { source: list[0].source, id: list[0].id };
    }
  }
  return null;
}

// Resolve a PresetRef back to its UnifiedPreset within the named slot, or
// null if it no longer resolves (e.g. the preset was deleted between the
// listing fetch and selection).
export function findPreset(
  by: UnifiedPresetsResponse,
  ref: PresetRef | null,
  slot: Slot,
): UnifiedPreset | null {
  if (!ref) return null;
  return by[ref.source][slot].find((p) => p.id === ref.id) ?? null;
}

// Find a preset by exact name across tiers (local → cloud → standard). Used
// to honour the printer / process preset names a 3MF was prepared with.
export function findPresetByName(
  by: UnifiedPresetsResponse,
  slot: Slot,
  name: string | null | undefined,
): PresetRef | null {
  if (!name) return null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    const p = by[tier][slot].find((x) => x.name === name);
    if (p) return { source: p.source, id: p.id };
  }
  return null;
}

// Process default: honour the process preset the 3MF was prepared with
// (preferredName) when it's available and not incompatible with the selected
// printer; otherwise the first preset compatible with the printer in tier
// order, then the first whose compatibility is merely unknown, then plain
// priority. Keeps the pre-pick honest with both the embedded config and the
// printer filter instead of blindly taking list[0] (#1325).
export function pickProcessDefault(
  by: UnifiedPresetsResponse,
  printerName: string | null,
  compatIndex: PrinterCompatibilityIndex,
  preferredName?: string | null,
): PresetRef | null {
  const preferred = findPresetByName(by, 'process', preferredName);
  if (preferred) {
    const p = findPreset(by, preferred, 'process');
    if (p && presetCompatibility(p, 'process', printerName, compatIndex) !== 'mismatch') {
      return preferred;
    }
  }
  for (const wanted of ['match', 'unknown'] as const) {
    for (const tier of SLICE_MODAL_TIER_ORDER) {
      const candidates = by[tier].process.filter(
        (p) => presetCompatibility(p, 'process', printerName, compatIndex) === wanted,
      );
      const chosen = preferDefaultLayerHeight(candidates);
      if (chosen) return { source: chosen.source, id: chosen.id };
    }
  }
  return pickDefault(by, 'process');
}

// Bambu's default layer height, and the one its own presets are named
// "Standard" at. Used only to order candidates that are equally valid.
const DEFAULT_LAYER_HEIGHT_MM = 0.2;

// Leading "<n>mm" on a process preset name — "0.20mm Standard @BBL X1C".
const PROCESS_LAYER_HEIGHT = /^\s*([\d.]+)\s*mm\b/i;

/**
 * Pick the best of a set of process presets that are all equally compatible.
 *
 * Tier order says which *source* to prefer, but within one tier the list is
 * alphabetical, and alphabetical order on Bambu's naming scheme puts the
 * finest layer height first. So the auto-pick landed on `0.08mm Extra Fine`
 * for an X1C and `0.06mm Fine` for an A1 mini — a correct preset, but the
 * slowest one the slicer ships, silently chosen for every slice that didn't
 * name its own process (#2982).
 *
 * Preferring the height nearest 0.2mm gives the same answer a person would:
 * `0.20mm Standard` where it exists, and the closest thing to it otherwise.
 * Ties break toward the coarser height, then toward the earlier name, so the
 * result stays deterministic. A name with no readable height sorts last but
 * is still eligible — an imported preset called "My Draft" must remain
 * pickable when it is the only candidate.
 */
function preferDefaultLayerHeight(candidates: UnifiedPreset[]): UnifiedPreset | null {
  let best: UnifiedPreset | null = null;
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestHeight = Number.NEGATIVE_INFINITY;
  for (const p of candidates) {
    const match = PROCESS_LAYER_HEIGHT.exec(p.name);
    const height = match ? Number.parseFloat(match[1]) : Number.NaN;
    const usable = Number.isFinite(height) && height > 0;
    const distance = usable
      ? Math.abs(height - DEFAULT_LAYER_HEIGHT_MM)
      : Number.POSITIVE_INFINITY;
    if (best == null || distance < bestDistance
      || (distance === bestDistance && usable && height > bestHeight)) {
      best = p;
      bestDistance = distance;
      bestHeight = usable ? height : Number.NEGATIVE_INFINITY;
    }
  }
  return best;
}

/**
 * True when ``preset`` states a material and it is not the one the plate slot
 * asks for.
 *
 * Deliberately three-valued in effect: a preset with no stated material is not
 * "different", it is unknown, and stays eligible. 32 profiles in the shipped
 * BBL bundle inherit from a parent the bundle doesn't contain, so their
 * material genuinely cannot be resolved — excluding those would leave slots
 * with nothing to pick.
 */
export function statesDifferentMaterial(
  preset: Pick<UnifiedPreset, 'filament_type'>,
  requiredType: string,
): boolean {
  const required = requiredType.trim().toUpperCase();
  const stated = (preset.filament_type ?? '').trim().toUpperCase();
  return Boolean(required) && Boolean(stated) && required !== stated;
}

export function pickFilamentForSlot(
  by: UnifiedPresetsResponse,
  required: { type: string; color: string },
  printerName: string | null,
  compatIndex: PrinterCompatibilityIndex,
): PresetRef | null {
  // Score every filament preset against the plate slot's required (type,
  // colour) and pick the highest. Mirrors the AMS slot-mapping match in the
  // print/schedule modal: type match dominates, exact-colour-match bumps over
  // similar-colour-match, and a small per-tier bonus breaks ties so cloud
  // user customisations win over standard bundled fallbacks of equal merit.
  //
  // Compatibility is a hard partition, not a soft penalty (#1851). The legacy
  // -100 demote let a printer-mismatched preset still win when the plate's
  // (type, colour) happened to match it better than the colour-default
  // standard preset on the right printer — e.g. an unused slot whose embedded
  // colour matched `Generic PLA @BBL H2C` but not the off-the-shelf
  // `Bambu PLA Basic @BBL A1`. The propagated slot-1 then poisoned every
  // unused slot via `substitute_unused_plate_filaments`, and the CLI rejected
  // the slice with "filament preset Generic PLA @BBL H2C (slot 1) is not
  // compatible with printer Bambu Lab A1 0.4 nozzle". Hard-skipping mismatches
  // while we still have any compatible/unknown candidate eliminates that
  // poisoning at the source; the mismatch tier is only consulted when no
  // printer-correct alternative exists, which preserves the graceful-degrade
  // behaviour for presets registries that genuinely have nothing for the
  // selected printer.
  //
  // Material is the second hard partition (#2982). A preset that states a
  // material the plate did not ask for is not a worse answer, it is the wrong
  // one: printing a PLA plate with a PETG profile means the wrong nozzle
  // temperature, the wrong bed temperature and the wrong flow. It used to be
  // only a missed +10 bonus, which a colour hit plus a tier bonus could
  // outweigh — and did, every time, once the standard tier's `filament_type`
  // turned out to be null for every preset it listed: an A1 mini offered
  // `Bambu PETG Basic` for a PLA plate, a P1S offered `Bambu PC`. Fixing the
  // sidecar to report the material restores the signal; skipping a stated
  // mismatch is what stops a wrong material from ever winning on colour again,
  // whatever a future registry reports.
  //
  // A preset that states NO material stays eligible — that is "don't know",
  // not "different", and 32 profiles in the shipped bundle inherit from a
  // parent it doesn't contain, so their material is genuinely unknown. The
  // same asymmetry `presetCompatibility` applies to printers.
  const reqType = required.type.trim().toUpperCase();
  const reqColor = normalizeColorForCompare(required.color);

  let bestCompatible: { ref: PresetRef; score: number } | null = null;
  let bestMismatch: { ref: PresetRef; score: number } | null = null;
  let bestWrongType: { ref: PresetRef; score: number } | null = null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    for (const p of by[tier].filament) {
      let score = 0;
      const presetType = (p.filament_type ?? '').trim().toUpperCase();
      const presetColor = normalizeColorForCompare(p.filament_colour ?? '');
      if (reqType && presetType && reqType === presetType) score += 10;
      if (reqColor && presetColor) {
        if (presetColor === reqColor) score += 5;
        else if (colorsAreSimilar(p.filament_colour ?? '', required.color)) score += 2;
      }
      score += TIER_BONUS[tier];
      const ref = { source: p.source, id: p.id };
      if (statesDifferentMaterial(p, reqType)) {
        if (bestWrongType == null || score > bestWrongType.score) {
          bestWrongType = { ref, score };
        }
      } else if (presetCompatibility(p, 'filament', printerName, compatIndex) === 'mismatch') {
        if (bestMismatch == null || score > bestMismatch.score) {
          bestMismatch = { ref, score };
        }
      } else if (bestCompatible == null || score > bestCompatible.score) {
        bestCompatible = { ref, score };
      }
    }
  }
  if (bestCompatible != null) return bestCompatible.ref;
  if (bestMismatch != null) return bestMismatch.ref;
  // Nothing of the right material anywhere. Better a wrong-material preset the
  // user can see and change in the dropdown than a null the modal renders as
  // an empty slot, which is what shipped before the partition existed.
  if (bestWrongType != null) return bestWrongType.ref;
  // Final fallback when there are no filament presets at all (empty
  // registry) — pickDefault returns null in that case too, but keeping the
  // call mirrors the rest of the picker logic for shape consistency.
  return pickDefault(by, 'filament');
}
