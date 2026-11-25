/**
 * Bambu Studio's global tray numbering (DevFilaSystem::GetTrayIndexMap), the
 * twin of backend/app/utils/tray_ids.py. A global tray id is what tray_now and
 * a print's ams_mapping hold:
 *
 *   AMS / AMS Lite / AMS 2 Pro   ams_id * 4 + slot
 *   A2L AMS Lite                 24 + slot
 *   AMS 3H (rack AMS, type 6)    28 + slot   (28-36)
 *   AMS-B (rack AMS, type 7)     37 + slot
 *   AMS-HT (units 128-135)       ams_id
 *   external input               254 / 255, and 247-255 on a rack printer
 *
 * A rack printer (the H3C) has one external input per hotend: input v feeds
 * hotend 255 - v. Bambuddy stores a slot as (ams_id, tray_id), external inputs
 * under ams_id 255 (tray_id 0 = 254, 1 = 255, 2-8 = 247-253).
 */

export const AmsType = {
  EXT_SPOOL: 0,
  AMS: 1,
  AMS_LITE: 2,
  N3F: 3, // AMS 2 Pro
  N3S: 4, // AMS-HT
  AMS_LITE_MIXED: 5, // the A2L's AMS Lite
  ZMULTI_A: 6, // AMS 3H
  ZMULTI_B: 7, // AMS-B
} as const;

const RACK_TRAY_BASE: Record<number, number> = { [AmsType.ZMULTI_A]: 28, [AmsType.ZMULTI_B]: 37 };
const AMS_LITE_MIXED_TRAY_BASE = 24;
/** One rack AMS slot per hotend of the rack, nine on the H3C. */
export const RACK_SLOT_COUNT = 9;
// The H3C reports its AMS 3H as unit 32; read only when a unit's type or the AMS data is missing.
const AMS3H_UNIT_ID = 32;
// For callers with no live status only (see trayLayoutOf).
const RACK_PRINTER_MODELS = new Set(['H3C', 'N10']);

export const EXTERNAL_AMS_ID = 255;
const EXTERNAL_MAIN_ID = 255;
const EXTERNAL_DEPUTY_ID = 254;
const RACK_EXTERNAL_FIRST = 247;

export function isRackAms(amsType: number | null | undefined): boolean {
  return amsType === AmsType.ZMULTI_A || amsType === AmsType.ZMULTI_B;
}

export function isAmsHtId(amsId: number): boolean {
  return amsId >= 128 && amsId <= 135;
}

/** Unit `amsId`'s AMS type: its `ams_type` when set, else from the id (128+ AMS-HT, 32 AMS 3H, else AMS). */
export function unitAmsType(amsId: number, explicit?: number | null): number {
  if (isAmsHtId(amsId)) return AmsType.N3S;
  if (typeof explicit === 'number' && explicit > AmsType.EXT_SPOOL && explicit <= AmsType.ZMULTI_B) return explicit;
  if (amsId >= 128) return AmsType.N3S;
  if (amsId === AMS3H_UNIT_ID) return AmsType.ZMULTI_A;
  return AmsType.AMS;
}

/** Global tray id of slot `slot` of AMS unit `amsId` (AMS slots only, see externalTrayId). */
export function globalTrayId(amsId: number, slot: number, amsType?: number | null): number {
  const kind = unitAmsType(amsId, amsType);
  if (kind === AmsType.N3S) return amsId;
  if (kind === AmsType.AMS_LITE_MIXED) return AMS_LITE_MIXED_TRAY_BASE + slot;
  if (RACK_TRAY_BASE[kind] !== undefined) return RACK_TRAY_BASE[kind] + slot;
  return amsId * 4 + slot;
}

export function isExternalTray(trayId: number | null | undefined, rack = false): boolean {
  if (typeof trayId !== 'number') return false;
  if (trayId === EXTERNAL_DEPUTY_ID || trayId === EXTERNAL_MAIN_ID) return true;
  return rack && trayId >= RACK_EXTERNAL_FIRST && trayId <= EXTERNAL_MAIN_ID;
}

/** The hotend (rack position 0-8) external input `trayId` feeds on a rack printer. */
export function externalHotend(trayId: number): number {
  return EXTERNAL_MAIN_ID - trayId;
}

/** The tray_id an external input is stored under: 254 -> 0, 255 -> 1, 247-253 -> 2-8. */
export function externalSlotIndex(trayId: number): number {
  if (trayId === EXTERNAL_DEPUTY_ID || trayId === EXTERNAL_MAIN_ID) return trayId - EXTERNAL_DEPUTY_ID;
  return trayId - RACK_EXTERNAL_FIRST + 2;
}

export function externalTrayId(slotIndex: number): number {
  if (slotIndex === 0 || slotIndex === 1) return EXTERNAL_DEPUTY_ID + slotIndex;
  return RACK_EXTERNAL_FIRST + slotIndex - 2;
}

export interface TrayLayout {
  units: { id: number; type: number }[];
  /** A rack printer: per-hotend external inputs 247-255. */
  rack: boolean;
}

/**
 * How one printer numbers its trays, and the one place that decides whether it
 * is a rack printer: it has a rack AMS (by `ams_type`, unit 32 when that is
 * missing) or reports an external input 247-253. `model` is a last resort for
 * callers without live status (a stored assignment): with no AMS units listed,
 * an H3C gets its AMS 3H, like the backend's TrayLayout.from_state.
 */
export function trayLayoutOf(
  units: ReadonlyArray<{ id: number; ams_type?: number | null }> | null | undefined,
  vtTrays?: ReadonlyArray<{ id?: number | null }> | null,
  model?: string | null,
): TrayLayout {
  const typed: TrayLayout['units'] = [];
  for (const unit of units ?? []) {
    if (!typed.some((known) => known.id === unit.id)) typed.push({ id: unit.id, type: unitAmsType(unit.id, unit.ams_type) });
  }
  if (typed.length === 0 && model && RACK_PRINTER_MODELS.has(model.toUpperCase())) {
    return { units: [{ id: AMS3H_UNIT_ID, type: AmsType.ZMULTI_A }], rack: true };
  }
  const rack = typed.some((unit) => isRackAms(unit.type))
    || (vtTrays ?? []).some((vt) => typeof vt.id === 'number' && vt.id >= RACK_EXTERNAL_FIRST && vt.id < EXTERNAL_DEPUTY_ID);
  return { units: typed, rack };
}

/** Type of unit `amsId` on the printer `layout` describes; told from the id when it isn't listed. */
export function layoutAmsType(layout: TrayLayout | null | undefined, amsId: number): number {
  return layout?.units.find((unit) => unit.id === amsId)?.type ?? unitAmsType(amsId);
}

/**
 * [ams_id, slot] of an AMS slot's global tray id, as the backend's slot_key
 * stores it; null for an external input (247-255) or no tray. Unless a rack
 * AMS or a unit 7 is listed, 28-36 are the AMS 3H's, as in Bambu Studio.
 */
export function locateTray(layout: TrayLayout | null | undefined, trayId: number): [number, number] | null {
  if (!Number.isInteger(trayId) || trayId < 0 || isExternalTray(trayId, true)) return null;
  if (trayId >= 128) return [trayId, 0];
  const units = layout?.units ?? [];
  const rackUnits = units.filter((unit) => isRackAms(unit.type));
  for (const unit of rackUnits) {
    const base = RACK_TRAY_BASE[unit.type];
    if (trayId >= base && trayId < base + RACK_SLOT_COUNT) return [unit.id, trayId - base];
  }
  const ams3hBase = RACK_TRAY_BASE[AmsType.ZMULTI_A];
  if (rackUnits.length === 0 && !units.some((unit) => unit.id === 7) && trayId >= ams3hBase && trayId < ams3hBase + RACK_SLOT_COUNT) {
    return [AMS3H_UNIT_ID, trayId - ams3hBase];
  }
  return [Math.floor(trayId / 4), trayId % 4];
}
