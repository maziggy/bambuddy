/**
 * AMS (Automatic Material System) helper utilities for Bambu Lab printers.
 * These functions handle color normalization, slot labeling, and tray ID calculations
 * for AMS, AMS-HT, and external spool configurations.
 */
import { parseUTCDate } from './date';

/**
 * Normalize color format from various sources for CSS rendering.
 * API returns "RRGGBBAA" (8-char), 3MF uses "#RRGGBB" (7-char with hash).
 * Result is "#RRGGBB" for opaque colors and "#RRGGBBAA" when alpha < FF —
 * CSS accepts both forms on `fill` / `backgroundColor`, and preserving alpha
 * lets transparent filaments render translucent instead of collapsing to
 * solid black (#1545). Comparison helpers use normalizeColorForCompare which
 * still strips alpha, so type/colour matching is unaffected.
 */
export function normalizeColor(color: string | null | undefined): string {
  if (!color) return '#808080';
  const clean = color.replace('#', '');
  if (clean.length >= 8 && clean.substring(6, 8).toLowerCase() !== 'ff') {
    return `#${clean.substring(0, 8)}`;
  }
  return `#${clean.substring(0, 6)}`;
}

/**
 * Normalize color for comparison (case-insensitive, strip hash and alpha).
 */
export function normalizeColorForCompare(color: string | undefined): string {
  if (!color) return '';
  return color.replace('#', '').toLowerCase().substring(0, 6);
}

/**
 * Filament type equivalence groups.
 * Types within the same group are interchangeable on the printer side
 * (e.g., Bambu Lab firmware treats PA-CF and PA12-CF as compatible).
 */
const FILAMENT_TYPE_GROUPS: string[][] = [
  ['PA-CF', 'PA12-CF', 'PAHT-CF'],
];

const _equivalenceMap: Record<string, string> = {};
for (const group of FILAMENT_TYPE_GROUPS) {
  const canonical = group[0];
  for (const t of group) {
    _equivalenceMap[t.toUpperCase()] = canonical.toUpperCase();
  }
}

/**
 * Get the canonical filament type for equivalence matching.
 * Types in the same group (e.g., PA-CF / PA12-CF / PAHT-CF) return the same canonical type.
 */
export function canonicalFilamentType(type: string | undefined): string {
  if (!type) return '';
  const upper = type.toUpperCase();
  return _equivalenceMap[upper] ?? upper;
}

/**
 * Check if two filament types are compatible (same type or same equivalence group).
 */
export function filamentTypesCompatible(a: string | undefined, b: string | undefined): boolean {
  return canonicalFilamentType(a) === canonicalFilamentType(b);
}

/**
 * Check if two colors are visually similar within a threshold.
 * Uses RGB component comparison with configurable tolerance.
 * @param color1 - First hex color
 * @param color2 - Second hex color
 * @param threshold - Maximum difference per RGB component (default: 40)
 */
export function colorsAreSimilar(
  color1: string | undefined,
  color2: string | undefined,
  threshold = 40
): boolean {
  const hex1 = normalizeColorForCompare(color1);
  const hex2 = normalizeColorForCompare(color2);
  if (!hex1 || !hex2 || hex1.length < 6 || hex2.length < 6) return false;

  const r1 = parseInt(hex1.substring(0, 2), 16);
  const g1 = parseInt(hex1.substring(2, 4), 16);
  const b1 = parseInt(hex1.substring(4, 6), 16);
  const r2 = parseInt(hex2.substring(0, 2), 16);
  const g2 = parseInt(hex2.substring(2, 4), 16);
  const b2 = parseInt(hex2.substring(4, 6), 16);

  return (
    Math.abs(r1 - r2) <= threshold &&
    Math.abs(g1 - g2) <= threshold &&
    Math.abs(b1 - b2) <= threshold
  );
}

/**
 * Format slot label for display in the UI.
 * @param amsId - AMS unit ID (0-3 for regular AMS, 128+ for AMS-HT)
 * @param trayId - Tray/slot ID within the AMS unit (0-3)
 * @param isHt - Whether this is an AMS-HT unit (single tray)
 * @param isExternal - Whether this is the external spool holder
 */
export function formatSlotLabel(
  amsId: number,
  trayId: number,
  isHt: boolean,
  isExternal: boolean
): string {
  if (isExternal) return 'Ext';
  // Convert AMS ID to letter (A, B, C, D)
  // AMS-HT uses IDs starting at 128
  const letter = String.fromCharCode(65 + (amsId >= 128 ? amsId - 128 : amsId));
  if (isHt) return `HT-${letter}`;
  return `${letter}${trayId + 1}`;
}

/**
 * Calculate global tray ID for MQTT command.
 * Used in the ams_mapping array sent to the printer.
 * @param amsId - AMS unit ID (0-3 for regular AMS, 128+ for AMS-HT)
 * @param trayId - Tray/slot ID within the AMS unit
 * @param isExternal - Whether this is the external spool holder
 * @returns Global tray ID (0-15 for AMS, 128+ for AMS-HT, 254 for external)
 */
export function getGlobalTrayId(
  amsId: number,
  trayId: number,
  isExternal: boolean
): number {
  if (isExternal) return 254 + trayId;
  // AMS-HT units have IDs starting at 128 with a single tray — use ID directly
  if (amsId >= 128) return amsId;
  return amsId * 4 + trayId;
}

/**
 * Get fill bar color based on spool fill level.
 * Matches PrintersPage thresholds and Bambu Lab brand green.
 */
export function getFillBarColor(fillLevel: number): string {
  if (fillLevel > 50) return '#00ae42'; // Green - good
  if (fillLevel >= 15) return '#f59e0b'; // Amber - warning (<= 50%)
  return '#ef4444'; // Red - critical (< 15%)
}

/**
 * Calculate fill level from Spoolman weight data.
 * Used as the first source in the Spoolman → Inventory → AMS fill chain.
 */
export function getSpoolmanFillLevel(
  linkedSpool: { remaining_weight: number | null; filament_weight: number | null } | undefined
): number | null {
  if (!linkedSpool?.remaining_weight || !linkedSpool?.filament_weight
      || linkedSpool.filament_weight <= 0) return null;
  return Math.min(100, Math.round(
    (linkedSpool.remaining_weight / linkedSpool.filament_weight) * 100
  ));
}

function toFixedHex(value: number, width: number): string {
  const safe = Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
  return safe.toString(16).toUpperCase().padStart(width, '0').slice(-width);
}

// 32-bit FNV-1a hash -> 8-char hex (stable for alphanumeric serials)
function hashSerialToHex32(serial: string): string {
  const input = (serial || '').trim().toUpperCase();
  let hash = 0x811c9dc5;
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).toUpperCase().padStart(8, '0');
}

/**
 * Generate a stable fallback spool tag for slots without RFID identifiers.
 * Returns a 16-char hex string derived from the printer serial + slot position.
 */
export function getFallbackSpoolTag(printerSerial: string, amsId: number, trayId: number): string {
  return `${hashSerialToHex32(printerSerial)}${toFixedHex(amsId, 4)}${toFixedHex(trayId, 4)}`;
}

/**
 * Get minimum datetime for scheduling (now + 1 minute).
 * Returns ISO string format for datetime-local input.
 */
export function getMinDateTime(): string {
  const now = new Date();
  now.setMinutes(now.getMinutes() + 1);
  return now.toISOString().slice(0, 16);
}

/**
 * Check if a scheduled time is a placeholder far-future date.
 * Placeholder dates (more than 6 months out) are treated as ASAP.
 */
export function isPlaceholderDate(scheduledTime: string | null | undefined): boolean {
  if (!scheduledTime) return false;
  const sixMonthsFromNow = Date.now() + 180 * 24 * 60 * 60 * 1000;
  return (parseUTCDate(scheduledTime)?.getTime() ?? 0) > sixMonthsFromNow;
}

/**
 * Banding tie-break for `preferLowestSortKey`, mirroring backend
 * `PrintScheduler._slot_priority` so regular AMS < AMS-HT < external on ties
 * regardless of the raw `ams_id`. In particular, `ams_id = -1` (VT / external
 * in `buildLoadedFilaments`) MUST NOT sort to a negative number or it would
 * beat AMS slot 0 — backend clamps to 10_000.
 */
function slotPriority(amsId: number | undefined, trayId: number | undefined): number {
  if (amsId == null || amsId < 0) return 10_000;
  if (amsId >= 128) return 1_000 + (amsId - 128) * 4 + (trayId ?? 0);
  return amsId * 4 + (trayId ?? 0);
}

/**
 * Two-tier sort key for the "Prefer Lowest Remaining Filament" preference (#1766).
 *
 * Mirrors backend `_prefer_lowest_sort_key` in `print_scheduler.py:1161` so the
 * client-side sort that PrintModal pre-computes lines up with the dispatch-time
 * sort. Inventory-bound spools sort before MQTT-only ones (tier 0 vs tier 1) so
 * the user's tracked grams beat the printer's per-cent estimate; within each
 * tier the lowest value wins, with the slot-position tie-break above so the
 * order is deterministic across identical spools.
 *
 * `inventoryByTrayId` is the `globalTrayId -> grams_remaining` map derived from
 * the user's spool assignments. Pass `undefined` to fall back to remain%-only
 * sorting (preserves pre-#1766 behaviour for callers that don't yet wire it in).
 */
export function preferLowestSortKey(
  f: { globalTrayId: number; amsId?: number; trayId?: number; remain?: number },
  inventoryByTrayId: Map<number, number> | undefined,
): [number, number, number] {
  const slot = slotPriority(f.amsId, f.trayId);
  if (inventoryByTrayId && inventoryByTrayId.has(f.globalTrayId)) {
    return [0, inventoryByTrayId.get(f.globalTrayId) ?? 0, slot];
  }
  const remain = f.remain ?? -1;
  return [1, remain >= 0 ? remain : 101, slot];
}

/** Tuple compare for `preferLowestSortKey` outputs. */
export function compareSortKeys(
  a: [number, number, number],
  b: [number, number, number],
): number {
  return a[0] - b[0] || a[1] - b[1] || a[2] - b[2];
}

/**
 * Effective "Prefer lowest remaining filament" preference for a given printer,
 * gated on its AMS Filament Backup state (#1766).
 *
 * Without backup, the printer can't switch to a second spool when the picked
 * one runs out — so even with the user setting on, sorting toward the lowest
 * leaves the print at risk. Mirrors the backend gate in
 * `print_scheduler.py::_compute_ams_mapping_for_printer`. `null`/`undefined`
 * (unknown state, e.g. A1 family) preserves today's behaviour intentionally.
 */
export function effectivePreferLowest(
  setting: boolean | undefined,
  amsFilamentBackup: boolean | null | undefined,
): boolean {
  if (!setting) return false;
  return amsFilamentBackup !== false;
}

/**
 * Auto-match a filament requirement to a loaded filament, respecting nozzle constraints.
 * Used by both single-printer (FilamentMapping) and multi-printer (InlineMappingEditor) paths.
 */
export function autoMatchFilament(
  req: { type?: string; color?: string; nozzle_id?: number | null },
  loadedFilaments: { globalTrayId: number; amsId?: number; trayId?: number; type?: string; color?: string; extruderId?: number; remain?: number }[],
  usedTrayIds: Set<number>,
  preferLowest?: boolean,
  inventoryByTrayId?: Map<number, number>,
): typeof loadedFilaments[number] | undefined {
  let nozzleFilaments = filterFilamentsByNozzle(loadedFilaments, req.nozzle_id);

  if (preferLowest) {
    nozzleFilaments = [...nozzleFilaments].sort((a, b) =>
      compareSortKeys(
        preferLowestSortKey(a, inventoryByTrayId),
        preferLowestSortKey(b, inventoryByTrayId),
      ),
    );
  }

  const exactMatch = nozzleFilaments.find(
    (f) =>
      !usedTrayIds.has(f.globalTrayId) &&
      filamentTypesCompatible(f.type, req.type) &&
      normalizeColorForCompare(f.color) === normalizeColorForCompare(req.color)
  );
  const similarMatch = exactMatch
    ? undefined
    : nozzleFilaments.find(
        (f) =>
          !usedTrayIds.has(f.globalTrayId) &&
          filamentTypesCompatible(f.type, req.type) &&
          colorsAreSimilar(f.color, req.color)
      );
  const typeOnlyMatch =
    exactMatch || similarMatch
      ? undefined
      : nozzleFilaments.find(
          (f) => !usedTrayIds.has(f.globalTrayId) && filamentTypesCompatible(f.type, req.type)
        );
  return exactMatch ?? similarMatch ?? typeOnlyMatch;
}

/**
 * Filter loaded filaments to those valid for a given nozzle requirement.
 * For single-nozzle printers (nozzle_id is null/undefined), returns all filaments.
 */
export function filterFilamentsByNozzle<T extends { extruderId?: number }>(
  loadedFilaments: T[],
  nozzleId: number | undefined | null,
): T[] {
  return loadedFilaments.filter(
    (f) => nozzleId == null || f.extruderId === nozzleId
  );
}

/**
 * Detect Bambu Lab RFID-tagged spool by tray_uuid (32 hex) or tag_uid (16 hex).
 *
 * Permissive zero-string check: any non-zero non-empty value returns true. The
 * function exists to suppress assign/unassign actions on RFID-managed slots
 * whose state is owned by the printer firmware — manual changes there would be
 * overwritten on the next RFID re-read (eye → pen icon in BambuStudio).
 */
export function isBambuLabSpool(tray: {
  tray_uuid?: string | null;
  tag_uid?: string | null;
} | null | undefined): boolean {
  if (!tray) return false;
  if (tray.tray_uuid && tray.tray_uuid !== '00000000000000000000000000000000') return true;
  if (tray.tag_uid && tray.tag_uid !== '0000000000000000') return true;
  return false;
}

export interface AmsTrayLike {
  id: number;
  tray_type: string | null | undefined;
  tray_sub_brands: string | null | undefined;
  tray_color: string | null | undefined;
  tray_info_idx: string | null | undefined;
}

export interface AmsUnitLike {
  id: number;
  tray: AmsTrayLike[];
}

/**
 * One row in the AMS Backup modal: a group of slots that back each other up
 * (length >= 2), or a single non-empty slot with no peer (length === 1).
 */
export interface BackupGroup {
  /** Stable key — same across renders for the same material+extruder. */
  key: string;
  /** Bambu preset ID (tray_info_idx) when matched on preset; null otherwise. */
  presetId: string | null;
  /** 0 = right / single, 1 = left. Scoping field for dual-nozzle. */
  extruder: number;
  /** Display name from the first slot's tray_sub_brands (or tray_type). */
  displayName: string;
  /** Tray colour from the first slot, for the swatch in the modal. */
  trayColor: string | null;
  /** Member slots, in (ams_id, slot_idx) order. */
  members: Array<{ amsId: number; slotIdx: number; globalTrayId: number }>;
}

/**
 * Canonicalise a hex colour for identity comparison. Mirrors the backend
 * `_normalize_color_for_id`. Strips the leading `#`, uppercases, and drops
 * the alpha channel when 8 chars long so `1A1A1AFF` matches `1A1A1A`.
 */
function normalizeColorForId(raw: string | null | undefined): string {
  let s = (raw || '').trim().replace(/^#/, '').toUpperCase();
  if (s.length === 8) s = s.slice(0, 6);
  return s;
}

/**
 * Compute backup pairs for the AMS Backup modal (#1762).
 *
 * Strict identity rule (mirrors backend `_material_identity_internal` /
 * `_material_identity_spoolman`): slots pair ONLY when they share the same
 * Bambu preset ID (`tray_info_idx`, e.g. "GFA00") AND the same colour. The
 * preset identifies the filament profile (PETG HF, PLA Basic, etc.); the
 * colour pins the variant — three PETG HF spools in different colours
 * absolutely don't back each other up. User-tagged spools without a preset
 * never pair — Bambu's firmware backup logic relies on the preset, and
 * pairing on cosmetic name/colour match alone would let two visually-
 * identical but materially-different spools be treated as backups.
 *
 * Empty slots are skipped entirely. Every non-empty slot is returned — slots
 * without a peer come back as 1-member entries so the modal can list them as
 * "Slots without a backup peer".
 *
 * On dual-extruder printers (H2D / H2C / X2D), pairs are scoped per extruder
 * side — the firmware can't cross extruders even with the global backup bit
 * set.
 */
export function computeBackupGroups(
  amsUnits: AmsUnitLike[] | undefined,
  amsExtruderMap: Record<string, number> | undefined,
  isDualNozzle: boolean,
): BackupGroup[] {
  if (!amsUnits || amsUnits.length === 0) return [];

  // Defensive dedup: ``status.ams`` is expected to be unique by `ams.id`, but
  // observed in the wild to occasionally contain duplicate entries (e.g. on
  // VP-aggregated switch printers or during MQTT partial-update merges). A
  // duplicate would surface as "AMS-A slot 1" rendered twice with different
  // materials, which is impossible physically and visually broken. First
  // occurrence per `ams.id` wins.
  const seenIds = new Set<number>();
  const uniqueAms: AmsUnitLike[] = [];
  for (const ams of amsUnits) {
    if (seenIds.has(ams.id)) continue;
    seenIds.add(ams.id);
    uniqueAms.push(ams);
  }

  const byKey = new Map<string, BackupGroup>();

  for (const ams of uniqueAms) {
    const extruder = isDualNozzle ? Number(amsExtruderMap?.[String(ams.id)] ?? 0) : 0;
    ams.tray.forEach((tray, slotIdx) => {
      if (!tray?.tray_type) return; // empty slot
      const preset = (tray.tray_info_idx || '').trim();
      const globalTrayId = getGlobalTrayId(ams.id, slotIdx, false);
      const member = { amsId: ams.id, slotIdx, globalTrayId };

      let key: string;
      let presetId: string | null;
      if (preset) {
        // Same Bambu profile is necessary but NOT sufficient — different colours
        // of the same PETG HF profile can't back each other up. Bake the colour
        // into the identity key, normalised to strip alpha and case.
        const color = normalizeColorForId(tray.tray_color);
        key = `preset:${preset}|color:${color}#${extruder}`;
        presetId = preset;
      } else {
        // No preset → never group with anything else. Unique-per-slot key.
        key = `unmatched:${ams.id}:${slotIdx}#${extruder}`;
        presetId = null;
      }

      const existing = byKey.get(key);
      if (existing) {
        existing.members.push(member);
      } else {
        byKey.set(key, {
          key,
          presetId,
          extruder,
          displayName: tray.tray_sub_brands || tray.tray_type || '',
          trayColor: tray.tray_color ?? null,
          members: [member],
        });
      }
    });
  }

  // Stable sort: extruder first (so the modal can section per side on
  // dual-nozzle), then pairs before lone slots, then by name, then by first
  // member's global tray id for deterministic rendering.
  return Array.from(byKey.values()).sort((a, b) => {
    if (a.extruder !== b.extruder) return a.extruder - b.extruder;
    const aLone = a.members.length === 1 ? 1 : 0;
    const bLone = b.members.length === 1 ? 1 : 0;
    if (aLone !== bLone) return aLone - bLone;
    if (a.displayName !== b.displayName) return a.displayName.localeCompare(b.displayName);
    return a.members[0].globalTrayId - b.members[0].globalTrayId;
  });
}
