// Slice-modal filters that narrow the printer and filament dropdowns to what
// is actually on the network.
//
// Printer filter: keep profiles whose model is one of the printers currently
// connected. Nozzle size is deliberately not part of that test — an H2D with
// a 0.4 mm nozzle installed still offers the 0.2 / 0.6 / 0.8 profiles, because
// the nozzle can be swapped and each size is its own printer preset.
//
// Filament filter: keep the filament profile assigned to each spool sitting
// in an AMS of a connected printer (the slot's saved preset, e.g.
// "SUNLU PLA MATTE GEN2"), including that same profile's other nozzle copies.
// It does not list every filament profile tagged for the printer model.

import type { PresetRef, UnifiedPresetsResponse } from '../api/client';
import { getGlobalTrayId, slotPresetDescribesTray } from './amsHelpers';
import { presetDisplayName } from './filamentPresets';
import { SLICE_MODAL_TIER_ORDER, statesDifferentMaterial } from './slicePresetPicker';
import {
  extractPresetModel,
  matchesPrinterModelSuffix,
  presetCompatibility,
  type PrinterCompatibilityIndex,
} from './slicerPrinterMatch';

const CONNECTED_PRINTERS_KEY = 'bambuddy.slice.onlyConnectedPrinters';
const LOADED_SPOOLS_KEY = 'bambuddy.slice.onlyLoadedSpools';

export type SliceFleetFilter = 'connected' | 'loaded';

export function readSliceFleetFilter(which: SliceFleetFilter): boolean {
  try {
    const key = which === 'connected' ? CONNECTED_PRINTERS_KEY : LOADED_SPOOLS_KEY;
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}

export function writeSliceFleetFilter(which: SliceFleetFilter, on: boolean): void {
  try {
    const key = which === 'connected' ? CONNECTED_PRINTERS_KEY : LOADED_SPOOLS_KEY;
    localStorage.setItem(key, on ? '1' : '0');
  } catch {
    /* private mode / disabled storage — the toggle still works for this open */
  }
}

export interface FleetPrinter {
  id: number;
  model: string | null;
}

export interface FleetTray {
  tray_type?: string | null;
  tray_sub_brands?: string | null;
  /** Printer filament id (`GFA00`). Compared with a saved `GFS…` preset id. */
  tray_info_idx?: string | null;
}

export interface FleetStatus {
  id: number;
  connected: boolean;
  ams?: { id: number; tray?: FleetTray[] }[];
  nozzles?: { nozzle_diameter?: string | null }[];
  nozzle_rack?: { nozzle_diameter?: string | null }[];
}

/** The filament profile Bambuddy has saved for one AMS slot. */
export interface AmsSlotPreset {
  presetId: string;
  presetName: string;
}

export interface LoadedSpool {
  type: string;
  subBrand: string;
  presetId: string;
  presetName: string;
}

export interface InstalledNozzle {
  model: string;
  diameters: string[];
}

function statusById(statuses: readonly FleetStatus[]): Map<number, FleetStatus> {
  const out = new Map<number, FleetStatus>();
  for (const status of statuses) out.set(status.id, status);
  return out;
}

/** Short model codes of printers whose live status says they are connected. */
export function connectedModelsFromFleet(
  printers: readonly FleetPrinter[],
  statuses: readonly FleetStatus[],
): string[] {
  const byId = statusById(statuses);
  const seen = new Set<string>();
  const models: string[] = [];
  for (const printer of printers) {
    const model = printer.model?.trim();
    if (!model) continue;
    if (!byId.get(printer.id)?.connected) continue;
    const key = model.toUpperCase();
    if (seen.has(key)) continue;
    seen.add(key);
    models.push(model);
  }
  models.sort((a, b) => a.localeCompare(b));
  return models;
}

/** Nozzle diameters reported by connected printers, grouped by printer model. */
export function installedNozzlesFromFleet(
  printers: readonly FleetPrinter[],
  statuses: readonly FleetStatus[],
): InstalledNozzle[] {
  const byId = statusById(statuses);
  const grouped = new Map<string, { model: string; diameters: Set<string> }>();
  for (const printer of printers) {
    const model = printer.model?.trim();
    if (!model) continue;
    const status = byId.get(printer.id);
    if (!status?.connected) continue;
    let row = grouped.get(model.toUpperCase());
    if (!row) {
      row = { model, diameters: new Set() };
      grouped.set(model.toUpperCase(), row);
    }
    for (const nozzle of [...(status.nozzles ?? []), ...(status.nozzle_rack ?? [])]) {
      const diameter = nozzle.nozzle_diameter?.trim();
      if (diameter) row.diameters.add(diameter);
    }
  }
  return [...grouped.values()].map((row) => ({
    model: row.model,
    diameters: [...row.diameters],
  }));
}

/**
 * Distinct AMS spools on connected printers.
 *
 * ``slotPresets`` is keyed by printer id, then by the global tray id
 * ``getGlobalTrayId`` uses. A saved slot preset is the profile that spool was
 * configured with, but only while ``slotPresetDescribesTray`` says it still
 * matches the printer's ``tray_info_idx``. A swapped official Bambu spool
 * keeps the previous profile until that row is refreshed; using it here would
 * list the old filament and hide the one actually loaded. A preset that no
 * longer describes the tray is dropped, and the printer-reported sub-brand
 * narrows the list instead. Trays with no saved profile do the same. Empty
 * slots are skipped. The external spool is not an AMS unit and is left out.
 */
export function loadedSpoolsFromFleet(
  printers: readonly FleetPrinter[],
  statuses: readonly FleetStatus[],
  slotPresets: Readonly<Record<number, Readonly<Record<number, AmsSlotPreset>>>> = {},
): LoadedSpool[] {
  const byId = statusById(statuses);
  const seen = new Set<string>();
  const spools: LoadedSpool[] = [];
  for (const printer of printers) {
    const status = byId.get(printer.id);
    if (!status?.connected) continue;
    const presets = slotPresets[printer.id] ?? {};
    for (const unit of status.ams ?? []) {
      (unit.tray ?? []).forEach((tray, trayIndex) => {
        const type = tray.tray_type?.trim() ?? '';
        if (!type) return;
        const saved = presets[getGlobalTrayId(unit.id, trayIndex, false)];
        const preset = saved && slotPresetDescribesTray(saved.presetId, tray.tray_info_idx)
          ? saved
          : undefined;
        const presetName = preset?.presetName?.trim() ?? '';
        const presetId = preset?.presetId?.trim() ?? '';
        const subBrand = tray.tray_sub_brands?.trim() ?? '';
        const key = (presetName || presetId || subBrand).toLowerCase();
        if (!key || seen.has(key)) return;
        seen.add(key);
        spools.push({ type, subBrand, presetId, presetName });
      });
    }
  }
  return spools;
}

/**
 * True when a printer preset belongs to one of ``connectedModels``.
 *
 * A name that doesn't resolve to a model stays allowed: hiding a custom
 * profile we can't classify would make it impossible to pick. Nozzle size is
 * ignored, so every nozzle profile of a connected model stays in the list.
 */
export function isPrinterPresetForModels(
  presetName: string,
  connectedModels: readonly string[],
  printerModelsLongToShort: Record<string, string>,
): boolean {
  if (connectedModels.length === 0) return true;
  const extracted = extractPresetModel(presetName, printerModelsLongToShort);
  if (!extracted) return true;
  return connectedModels.some((model) => matchesPrinterModelSuffix(extracted, model));
}

const NOZZLE_IN_NAME = /([\d.]+)\s*nozzle/i;

export function nozzleDiameterFromPresetName(name: string): string | null {
  const match = NOZZLE_IN_NAME.exec(name);
  if (!match) return null;
  const size = Number.parseFloat(match[1]);
  if (!Number.isFinite(size) || size < 0.1 || size > 2) return null;
  return match[1];
}

function sameNozzle(a: string, b: string): boolean {
  const x = Number.parseFloat(a);
  const y = Number.parseFloat(b);
  if (Number.isNaN(x) || Number.isNaN(y)) return false;
  return x === y;
}

function installedDiametersFor(
  extractedModel: string,
  installed: readonly InstalledNozzle[],
): string[] {
  const sizes: string[] = [];
  for (const row of installed) {
    if (matchesPrinterModelSuffix(extractedModel, row.model)) sizes.push(...row.diameters);
  }
  return sizes;
}

/**
 * Replacement printer preset when the current pick isn't a connected model.
 *
 * Prefers a profile whose nozzle matches one installed on a connected printer
 * of that model, then the first remaining profile in the usual tier order.
 * Returns null when nothing classified matches — the caller keeps the current
 * pick rather than blanking the dropdown.
 */
export function pickConnectedPrinterPreset(
  by: UnifiedPresetsResponse,
  connectedModels: readonly string[],
  printerModelsLongToShort: Record<string, string>,
  installedNozzles: readonly InstalledNozzle[],
): PresetRef | null {
  if (connectedModels.length === 0) return null;
  let fallback: PresetRef | null = null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    for (const preset of by[tier].printer) {
      const extracted = extractPresetModel(preset.name, printerModelsLongToShort);
      if (!extracted) continue;
      if (!connectedModels.some((model) => matchesPrinterModelSuffix(extracted, model))) continue;
      const ref = { source: preset.source, id: preset.id };
      if (!fallback) fallback = ref;
      const nozzle = nozzleDiameterFromPresetName(preset.name);
      const installed = installedDiametersFor(extracted, installedNozzles);
      if (nozzle && installed.some((size) => sameNozzle(size, nozzle))) return ref;
    }
  }
  return fallback;
}

function normalizeFilamentName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9+]+/g, ' ')
    .trim()
    // "Bambu" and "Bambu Lab" are one brand written two ways. "Generic" is a
    // different profile family, so it stays: stripping it made "Generic PLA
    // Basic" look like "Bambu PLA Basic".
    .replace(/^(bambu lab|bambu)\s+/, '');
}

/** Phrase match on whitespace-separated tokens, so "pa" does not hit "pla". */
function containsPhrase(haystack: string, phrase: string): boolean {
  if (!haystack || !phrase) return false;
  if (haystack === phrase) return true;
  return (
    haystack.startsWith(`${phrase} `)
    || haystack.endsWith(` ${phrase}`)
    || haystack.includes(` ${phrase} `)
  );
}

function sameFilamentProduct(a: string, b: string): boolean {
  const left = normalizeFilamentName(presetDisplayName(a));
  const right = normalizeFilamentName(presetDisplayName(b));
  return Boolean(left) && left === right;
}

/** Cloud setting ids sometimes grow a variant suffix ("SUN20010" / "SUN20010_01"). */
function samePresetId(presetId: string, spoolPresetId: string): boolean {
  const preset = presetId.trim().toUpperCase();
  const spool = spoolPresetId.trim().toUpperCase();
  if (!preset || !spool) return false;
  return preset === spool || preset.startsWith(`${spool}_`) || spool.startsWith(`${preset}_`);
}

function spoolMatchesPreset(
  preset: { id?: string; name: string; filament_type?: string | null },
  spool: LoadedSpool,
): boolean {
  // A saved AMS profile identifies the spool. Match that product — the same
  // name with a different nozzle or printer tag still counts — and do not
  // fall through to "any PLA profile for this printer".
  if (spool.presetName || spool.presetId) {
    if (spool.presetName && sameFilamentProduct(preset.name, spool.presetName)) return true;
    if (spool.presetId && preset.id && samePresetId(preset.id, spool.presetId)) return true;
    return false;
  }
  const sub = normalizeFilamentName(spool.subBrand);
  if (!sub) return false;
  const display = normalizeFilamentName(presetDisplayName(preset.name));
  return containsPhrase(display, sub);
}

export function filamentPresetMatchesLoadedSpools(
  preset: { name: string; filament_type?: string | null },
  spools: readonly LoadedSpool[],
): boolean {
  if (spools.length === 0) return false;
  return spools.some((spool) => spoolMatchesPreset(preset, spool));
}

/**
 * A filament preset for a loaded spool, compatible with the selected printer
 * and with the plate slot's material when the slot states one.
 *
 * Returns null when nothing loaded fits — the caller keeps the current pick
 * rather than clearing the slot.
 */
export function pickLoadedFilamentPreset(
  by: UnifiedPresetsResponse,
  spools: readonly LoadedSpool[],
  printerName: string | null,
  compatIndex: PrinterCompatibilityIndex,
  requiredType: string,
): PresetRef | null {
  if (spools.length === 0) return null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    for (const preset of by[tier].filament) {
      if (!filamentPresetMatchesLoadedSpools(preset, spools)) continue;
      if (statesDifferentMaterial(preset, requiredType)) continue;
      if (presetCompatibility(preset, 'filament', printerName, compatIndex) === 'mismatch') continue;
      return { source: preset.source, id: preset.id };
    }
  }
  return null;
}
