// Turns live AMS telemetry into the slice dialog's spool picker.
//
// A slot is selectable only when the profile saved on that slot has a copy
// compatible with the printer profile already chosen (model and nozzle).
// Empty AMS slots stay in the grid so it matches the physical unit. The
// external holder is included only while a spool is actually sitting there.

import type { PresetRef, UnifiedPresetsResponse } from '../api/client';
import {
  formatSlotLabel,
  getAmsLabel,
  getGlobalTrayId,
  normalizeColor,
  slotPresetDescribesTray,
} from './amsHelpers';
import { presetDisplayName } from './filamentPresets';
import {
  pickLoadedFilamentPreset,
  type AmsSlotPreset,
} from './sliceConnectedFilters';
import { matchesPrinterModelSuffix, type PrinterCompatibilityIndex } from './slicerPrinterMatch';

export interface PickerTray {
  id?: number;
  tray_type?: string | null;
  tray_color?: string | null;
  tray_info_idx?: string | null;
}

export interface PickerAmsUnit {
  id: number;
  is_ams_ht?: boolean;
  tray?: PickerTray[];
}

export interface PickerPrinterStatus {
  id: number;
  connected: boolean;
  ams?: PickerAmsUnit[];
  vt_tray?: PickerTray[];
}

export interface PickerPrinter {
  id: number;
  name: string;
  /** Short model code from the printer record, e.g. "H2D". */
  model?: string | null;
}

export interface SpoolBrand {
  brand?: string | null;
}

export interface SpoolPickerSlot {
  key: string;
  label: string;
  empty: boolean;
  /** #RRGGBB when the spool reports a colour, otherwise null. */
  color: string | null;
  material: string;
  /** Saved slicer profile with the printer/nozzle suffix removed. */
  profileName: string;
  brand: string;
  preset: PresetRef | null;
}

export interface SpoolPickerUnit {
  key: string;
  title: string;
  columns: number;
  slots: SpoolPickerSlot[];
}

export interface SpoolPickerPrinter {
  id: number;
  name: string;
  units: SpoolPickerUnit[];
}

export interface SpoolPickerInput {
  printers: readonly PickerPrinter[];
  statuses: readonly PickerPrinterStatus[];
  slotPresets: Readonly<Record<number, Readonly<Record<number, AmsSlotPreset>>>>;
  brands: Readonly<Record<number, Readonly<Record<number, SpoolBrand>>>>;
  presets: UnifiedPresetsResponse | undefined;
  printerName: string | null;
  compatIndex: PrinterCompatibilityIndex;
  externalTitle: string;
  /**
   * When set, only printers of this model are listed. Used while "Only
   * connected printer models" is on, with the model of the selected printer
   * profile. Null keeps every connected printer.
   */
  restrictToModel?: string | null;
}

/** Words in front of the material type, e.g. "SUNLU PLA MATTE" + PLA → "SUNLU". */
export function brandFromProductName(productName: string, materialType: string): string {
  const display = presetDisplayName(productName);
  const type = materialType.trim().toLowerCase();
  if (!display || !type) return '';
  const words = display.split(/\s+/);
  const index = words.findIndex((word) => {
    const token = word.toLowerCase();
    return token === type || token.startsWith(`${type}-`) || token.startsWith(`${type}+`);
  });
  if (index <= 0) return '';
  return words.slice(0, index).join(' ');
}

/** Opaque #RRGGBB for the colour input. Empty and all-zero readings are no colour. */
export function spoolSwatchColor(raw: string | null | undefined): string | null {
  if (!raw?.trim()) return null;
  const clean = raw.replace('#', '').trim();
  if (!clean || /^0+$/.test(clean)) return null;
  const hex = normalizeColor(raw).slice(0, 7);
  return /^#[0-9a-fA-F]{6}$/.test(hex) ? hex.toUpperCase() : null;
}

function isHtUnit(unit: PickerAmsUnit): boolean {
  return Boolean(unit.is_ams_ht) || unit.id >= 128;
}

function trayAt(unit: PickerAmsUnit, index: number): PickerTray | undefined {
  return unit.tray?.[index] ?? unit.tray?.find((tray) => tray.id === index);
}

function trustedPreset(
  preset: AmsSlotPreset | undefined,
  tray: PickerTray | undefined,
): AmsSlotPreset | undefined {
  if (!preset?.presetName && !preset?.presetId) return undefined;
  if (!slotPresetDescribesTray(preset.presetId, tray?.tray_info_idx)) return undefined;
  return preset;
}

function slotBrand(
  material: string,
  preset: AmsSlotPreset | undefined,
  identity: SpoolBrand | undefined,
): string {
  const fromInventory = identity?.brand?.trim() ?? '';
  if (fromInventory) return fromInventory;
  if (!preset?.presetName) return '';
  return brandFromProductName(preset.presetName, material);
}

function filledSlot(
  input: SpoolPickerInput,
  printerId: number,
  key: string,
  label: string,
  tray: PickerTray,
  presetKey: number,
  brandKey: number,
): SpoolPickerSlot {
  const material = tray.tray_type?.trim() ?? '';
  const preset = trustedPreset(input.slotPresets[printerId]?.[presetKey], tray);
  const identities = input.brands[printerId];
  const brand = slotBrand(material, preset, identities?.[brandKey] ?? identities?.[presetKey]);
  const ref = preset && input.presets
    ? pickLoadedFilamentPreset(
        input.presets,
        [{
          type: material,
          subBrand: '',
          presetId: preset.presetId,
          presetName: preset.presetName,
        }],
        input.printerName,
        input.compatIndex,
        '',
      )
    : null;
  return {
    key,
    label,
    empty: false,
    color: spoolSwatchColor(tray.tray_color),
    material,
    profileName: preset?.presetName ? presetDisplayName(preset.presetName) : '',
    brand,
    preset: ref,
  };
}

function emptySlot(key: string, label: string): SpoolPickerSlot {
  return { key, label, empty: true, color: null, material: '', profileName: '', brand: '', preset: null };
}

function externalPresetKey(tray: PickerTray): number {
  const reported = tray.id ?? 254;
  const slot = reported >= 254 ? reported - 254 : reported;
  return 255 * 4 + slot;
}

export function buildSpoolPicker(input: SpoolPickerInput): SpoolPickerPrinter[] {
  const statusById = new Map(input.statuses.map((status) => [status.id, status]));
  const groups: SpoolPickerPrinter[] = [];
  for (const printer of input.printers) {
    const status = statusById.get(printer.id);
    if (!status?.connected) continue;
    const onlyModel = input.restrictToModel?.trim() ?? '';
    if (onlyModel) {
      const reported = printer.model?.trim() ?? '';
      if (!reported || !matchesPrinterModelSuffix(onlyModel, reported)) continue;
    }
    const units: SpoolPickerUnit[] = [];
    // AMS-HT units are one slot each. Pack them into the same 4-column grid a
    // regular AMS uses, wrapping after four, and list them after every AMS.
    const htSlots: SpoolPickerSlot[] = [];
    let loneHtTitle = '';
    for (const unit of status.ams ?? []) {
      const ht = isHtUnit(unit);
      const count = ht ? 1 : 4;
      const slots: SpoolPickerSlot[] = [];
      for (let index = 0; index < count; index += 1) {
        const tray = trayAt(unit, index);
        const label = formatSlotLabel(unit.id, index, ht, false);
        const key = `${printer.id}:${unit.id}:${index}`;
        if (!tray?.tray_type?.trim()) {
          slots.push(emptySlot(key, label));
          continue;
        }
        const globalId = getGlobalTrayId(unit.id, index, false);
        slots.push(filledSlot(
          input,
          printer.id,
          key,
          label,
          tray,
          globalId,
          globalId,
        ));
      }
      if (ht) {
        if (!loneHtTitle) loneHtTitle = getAmsLabel(unit.id, 1);
        htSlots.push(...slots);
        continue;
      }
      units.push({
        key: `${printer.id}:ams:${unit.id}`,
        title: getAmsLabel(unit.id, count),
        columns: count,
        slots,
      });
    }
    if (htSlots.length > 0) {
      units.push({
        key: `${printer.id}:ht`,
        title: htSlots.length > 1 ? 'AMS-HT' : loneHtTitle,
        columns: 4,
        slots: htSlots,
      });
    }
    const loadedExternal = (status.vt_tray ?? []).filter((tray) => tray.tray_type?.trim());
    if (loadedExternal.length > 0) {
      units.push({
        key: `${printer.id}:external`,
        title: input.externalTitle,
        columns: loadedExternal.length > 1 ? 2 : 1,
        slots: loadedExternal.map((tray) => {
          const reported = tray.id ?? 254;
          const slot = reported >= 254 ? reported - 254 : reported;
          return filledSlot(
            input,
            printer.id,
            `${printer.id}:ext:${reported}`,
            loadedExternal.length > 1 ? `Ext ${slot + 1}` : formatSlotLabel(255, slot, false, true),
            tray,
            externalPresetKey(tray),
            reported >= 254 ? reported : 254 + slot,
          );
        }),
      });
    }
    groups.push({ id: printer.id, name: printer.name, units });
  }
  return groups;
}
