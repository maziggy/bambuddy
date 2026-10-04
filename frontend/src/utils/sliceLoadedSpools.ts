// Matching what is loaded in the connected printers against the SliceModal's
// profile lists (#3172).
//
// Two questions are answered here:
//
//   1. Which printer models are online, and does a printer profile belong to
//      one of them? A profile whose model can't be read from its name (a
//      user's own "My farm printer") is never ruled out.
//   2. Which filament profile does a loaded slot correspond to, for the
//      printer profile selected in the dialog? Strongest evidence first:
//
//      a. the profile Bambuddy saved for the slot, by its id, when it still
//         describes the slot (#3216) and fits the selected printer;
//      b. the same profile under another printer's tag, found by base name
//         (the name before its "@printer" suffix) — a cloud profile saved for
//         an X1C reaches its H2D copy this way, and bundled profiles, which
//         are addressed by name, are only reachable this way;
//      c. for a slot nobody configured through Bambuddy, the spool's own
//         brand text ("PLA Basic" → "Bambu PLA Basic") or, with none,
//         "Generic <material>".
//
//      A slot with no match is not guessed at: the picker shows it greyed out
//      rather than turning its bare material into some profile.

import type {
  LoadedSpoolPreset,
  LoadedSpoolPrinter,
  LoadedSpoolTray,
  PresetRef,
  PresetSource,
  UnifiedPreset,
  UnifiedPresetsResponse,
} from '../api/client';
import { slotPresetDescribesTray } from './amsHelpers';
import { SLICE_MODAL_TIER_ORDER, statesDifferentMaterial } from './slicePresetPicker';
import {
  matchesPrinterModelSuffix,
  presetCompatibility,
  type PrinterCompatibilityIndex,
} from './slicerPrinterMatch';

export function refValue(ref: PresetRef): string {
  return `${ref.source}:${ref.id}`;
}

/**
 * The model short name of a printer profile ("Bambu Lab X1 Carbon 0.4
 * nozzle" → "X1C"), or null when the name isn't a Bambu printer profile.
 * ``printerModels`` is the backend's long → short registry.
 */
export function printerPresetModel(
  name: string | null | undefined,
  printerModels: Record<string, string>,
): string | null {
  if (!name) return null;
  const m = name.replace(/^#\s*/, '').trim().match(/^Bambu Lab\s+(.+?)(?:\s+[\d.]+\s*nozzle)?$/i);
  if (!m) return null;
  const fragment = m[1].trim();
  const full = `bambu lab ${fragment}`.toLowerCase();
  for (const [long, short] of Object.entries(printerModels)) {
    if (long.toLowerCase() === full) return short;
  }
  return fragment;
}

export function sameModel(a: string | null | undefined, b: string | null | undefined): boolean {
  if (!a || !b) return false;
  return matchesPrinterModelSuffix(a.trim(), b.trim());
}

/** Connected printers of ``model``; every connected printer when it's unknown. */
export function printersOfModel(
  printers: LoadedSpoolPrinter[],
  model: string | null,
): LoadedSpoolPrinter[] {
  if (!model) return printers;
  return printers.filter((p) => sameModel(p.model, model));
}

/** Whether a printer profile belongs to a connected model, or can't be told. */
export function isConnectedModelPreset(
  preset: Pick<UnifiedPreset, 'name'>,
  connectedModels: (string | null)[],
  printerModels: Record<string, string>,
): boolean {
  const model = printerPresetModel(preset.name, printerModels);
  if (model === null) return true;
  return connectedModels.some((c) => sameModel(c, model));
}

/**
 * A printer profile for one of the connected models, for the auto-pick while
 * "only online printers" is on. Prefers the 0.4 nozzle, Bambu's default, as
 * nothing here says which nozzle is fitted.
 */
export function pickConnectedPrinterPreset(
  data: UnifiedPresetsResponse,
  connectedModels: (string | null)[],
  printerModels: Record<string, string>,
): PresetRef | null {
  let fallback: PresetRef | null = null;
  for (const source of SLICE_MODAL_TIER_ORDER) {
    for (const preset of data[source].printer) {
      const model = printerPresetModel(preset.name, printerModels);
      if (model === null || !connectedModels.some((c) => sameModel(c, model))) continue;
      const ref = { source, id: preset.id };
      if (/\b0\.4\s*nozzle\b/i.test(preset.name)) return ref;
      fallback ??= ref;
    }
  }
  return fallback;
}

/** A profile name without its "# " clone prefix and "@printer" suffix, case-folded. */
export function presetBaseName(name: string): string {
  let s = name.replace(/^#\s*/, '');
  const at = s.lastIndexOf('@');
  if (at > 0) s = s.slice(0, at);
  return s.replace(/\s+/g, ' ').trim().toLowerCase();
}

/** The same, keeping the case, for display. */
export function presetDisplayName(name: string): string {
  let s = name.replace(/^#\s*/, '');
  const at = s.lastIndexOf('@');
  if (at > 0) s = s.slice(0, at);
  return s.replace(/\s+/g, ' ').trim();
}

export function isLoaded(tray: LoadedSpoolTray): boolean {
  return Boolean(tray.tray_type);
}

/** The saved profile, unless the slot has been reconfigured since (#3216). */
export function savedPresetFor(tray: LoadedSpoolTray): LoadedSpoolPreset | null {
  const saved = tray.saved_preset;
  if (!saved) return null;
  return slotPresetDescribesTray(saved.preset_id, tray.tray_info_idx, saved.tray_info_idx) ? saved : null;
}

function savedRef(saved: LoadedSpoolPreset): PresetRef | null {
  if (saved.preset_source === 'local') {
    const m = saved.preset_id.match(/^local_(\d+)$/);
    return m ? { source: 'local', id: m[1] } : null;
  }
  if (saved.preset_source === 'cloud' || saved.preset_source === 'orca_cloud') {
    return saved.preset_id ? { source: saved.preset_source, id: saved.preset_id } : null;
  }
  // "builtin_<filament id>" names a bundled filament by its id, which the
  // bundled listing doesn't carry; its saved name is matched below instead.
  return null;
}

function nameCandidates(tray: LoadedSpoolTray, saved: LoadedSpoolPreset | null): string[] {
  const out: string[] = [];
  if (saved?.preset_name) out.push(saved.preset_name);
  const sub = (tray.tray_sub_brands ?? '').trim();
  if (sub) {
    // Bambu's own spools report "PLA Basic"; the profile is "Bambu PLA Basic".
    // Only for Bambu filament ids, so a third-party "PLA Basic" isn't renamed.
    if (/^GF/i.test(tray.tray_info_idx ?? '') && !/^bambu\b/i.test(sub)) out.push(`Bambu ${sub}`);
    out.push(sub);
  } else if (tray.tray_type) {
    out.push(`Generic ${tray.tray_type}`);
  }
  const seen = new Set<string>();
  return out.filter((n) => {
    const key = presetBaseName(n);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

interface IndexedPreset {
  source: PresetSource;
  preset: UnifiedPreset;
}

/** Filament profiles by base name, each list in the dialog's tier order. */
export type FilamentNameIndex = Map<string, IndexedPreset[]>;

export function buildFilamentNameIndex(data: UnifiedPresetsResponse): FilamentNameIndex {
  const index: FilamentNameIndex = new Map();
  for (const source of SLICE_MODAL_TIER_ORDER) {
    for (const preset of data[source].filament) {
      const key = presetBaseName(preset.name);
      const list = index.get(key);
      if (list) list.push({ source, preset });
      else index.set(key, [{ source, preset }]);
    }
  }
  return index;
}

export interface SlotMatch {
  ref: PresetRef;
  preset: UnifiedPreset;
}

/**
 * The filament profile for ``tray`` on the selected printer, or null when
 * nothing fits. See the header for the order evidence is tried in.
 */
export function matchSlotPreset(
  tray: LoadedSpoolTray,
  data: UnifiedPresetsResponse,
  index: FilamentNameIndex,
  selectedPrinterName: string | null,
  compatIndex: PrinterCompatibilityIndex,
): SlotMatch | null {
  if (!isLoaded(tray)) return null;
  const saved = savedPresetFor(tray);
  const fits = (p: UnifiedPreset) => presetCompatibility(p, 'filament', selectedPrinterName, compatIndex);

  if (saved) {
    const ref = savedRef(saved);
    const preset = ref ? data[ref.source].filament.find((p) => p.id === ref.id) : undefined;
    // The user picked this profile for the slot, so its material isn't
    // second-guessed — only whether it is for the selected printer.
    if (ref && preset && fits(preset) !== 'mismatch') return { ref, preset };
  }

  const savedSource = saved ? savedRef(saved)?.source : undefined;
  for (const name of nameCandidates(tray, saved)) {
    const entries = index.get(presetBaseName(name)) ?? [];
    // The tier the saved profile came from goes first, then the usual order.
    const ordered = savedSource
      ? [...entries.filter((e) => e.source === savedSource), ...entries.filter((e) => e.source !== savedSource)]
      : entries;
    let unknown: SlotMatch | null = null;
    for (const { source, preset } of ordered) {
      if (statesDifferentMaterial(preset, tray.tray_type ?? '')) continue;
      const fit = fits(preset);
      if (fit === 'match') return { ref: { source, id: preset.id }, preset };
      if (fit === 'unknown' && !unknown) unknown = { ref: { source, id: preset.id }, preset };
    }
    if (unknown) return unknown;
  }
  return null;
}

/** Every loaded tray of ``printers``, AMS units first, then external holders. */
export function loadedTrays(printers: LoadedSpoolPrinter[]): LoadedSpoolTray[] {
  const out: LoadedSpoolTray[] = [];
  for (const printer of printers) {
    for (const unit of printer.ams) {
      for (const tray of unit.trays) if (isLoaded(tray)) out.push(tray);
    }
    for (const tray of printer.external) if (isLoaded(tray)) out.push(tray);
  }
  return out;
}

/** Ref values of the profiles matched by at least one loaded tray. */
export function matchedFilamentRefs(
  printers: LoadedSpoolPrinter[],
  data: UnifiedPresetsResponse,
  index: FilamentNameIndex,
  selectedPrinterName: string | null,
  compatIndex: PrinterCompatibilityIndex,
): Set<string> {
  const out = new Set<string>();
  for (const tray of loadedTrays(printers)) {
    const match = matchSlotPreset(tray, data, index, selectedPrinterName, compatIndex);
    if (match) out.add(refValue(match.ref));
  }
  return out;
}

/** "#RRGGBB" from the printer's RRGGBBAA, or null when it isn't one. */
export function trayColourHex(tray: Pick<LoadedSpoolTray, 'tray_color'>): string | null {
  const raw = (tray.tray_color ?? '').replace(/^#/, '');
  return /^[0-9a-fA-F]{6}/.test(raw) ? `#${raw.slice(0, 6).toUpperCase()}` : null;
}
