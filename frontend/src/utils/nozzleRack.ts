import type { NozzleRackSlot } from '../api/client';
import type { RackGroupInfo, RackPositionOption } from '../components/PrintModal/types';

/**
 * H2C nozzle-rack position helpers (#1784).
 *
 * A rack position as everyone counts it — the printer card, BambuStudio, the
 * operator — is 1-based, and the physical nozzle ID the printer is sent is 15
 * higher. Measured 2026-08-14: the same plate dispatched with R1+R2 picked sent
 * `[16, 1, 17]`, and with R1+R3 picked sent `[16, 1, 18]`.
 *
 * The eligibility rule here mirrors `_rack_slot_is_eligible` in
 * `backend/app/services/bambu_mqtt.py`. It is duplicated rather than fetched
 * because the picker has to grey out an option as the user looks at it, but the
 * backend re-checks the same rule at dispatch and refuses the print if it no
 * longer holds — so this copy being briefly stale can only mislead, never
 * misprint.
 */

/** Physical nozzle IDs of the six rack positions. */
export const RACK_POSITION_BASE = 15;
export const RACK_SIZE = 6;
export const RACK_POSITIONS = Array.from({ length: RACK_SIZE }, (_, i) => i + 1);

/** The two carriage entries the printer reports alongside the rack itself. */
const RACK_CARRIAGE_NOZZLE_ID = 0;

export function rackPositionToNozzleId(position: number): number | null {
  if (!Number.isInteger(position) || position < 1 || position > RACK_SIZE) return null;
  return RACK_POSITION_BASE + position;
}

/**
 * Live rack contents by 1-based position, including the mounted nozzle.
 *
 * The firmware omits a rack ID entirely while that nozzle is picked up onto the
 * carriage (#943) rather than sending an empty placeholder, so taking the gap at
 * face value would grey out the nozzle most likely to be wanted — the one the
 * last print left mounted. A single gap alongside a loaded carriage is that
 * carriage's nozzle; two or more gaps are genuinely ambiguous (four nozzles in
 * six positions looks identical) and stay absent.
 */
export function rackByPosition(slots: NozzleRackSlot[] | undefined): Map<number, NozzleRackSlot> {
  const byPosition = new Map<number, NozzleRackSlot>();
  let carriage: NozzleRackSlot | undefined;

  for (const slot of slots ?? []) {
    if (slot.id === RACK_CARRIAGE_NOZZLE_ID) {
      carriage = slot;
      continue;
    }
    const position = slot.id - RACK_POSITION_BASE;
    if (position >= 1 && position <= RACK_SIZE) byPosition.set(position, slot);
  }

  const missing = RACK_POSITIONS.filter((p) => !byPosition.has(p));
  if (missing.length === 1 && carriage && (carriage.nozzle_diameter || carriage.nozzle_type)) {
    byPosition.set(missing[0], carriage);
  }
  return byPosition;
}

/** Whether a live rack slot can print a group wanting this nozzle. */
export function isRackSlotEligible(
  slot: NozzleRackSlot | undefined,
  group: Pick<RackGroupInfo, 'nozzle_diameter' | 'volume_type'>,
): boolean {
  if (!slot) return false;
  if (!slot.nozzle_diameter && !slot.nozzle_type) return false;

  // "0.40" and "0.4" name the same nozzle — the 3MF pads, the printer does not.
  const slotDiameter = Number.parseFloat(slot.nozzle_diameter);
  const wantedDiameter = Number.parseFloat(group.nozzle_diameter);
  if (!Number.isFinite(slotDiameter) || !Number.isFinite(wantedDiameter)) return false;
  if (Math.abs(slotDiameter - wantedDiameter) > 0.005) return false;

  // Flow type: the printer reports a code ("HS", "HH01"), the slice a name
  // ("High Flow"). Compared only when both are stated, so a printer that omits
  // the code is not thereby ruled out.
  const wanted = (group.volume_type || '').trim().toLowerCase();
  if (wanted && slot.nozzle_type) {
    const isHighFlow = slot.nozzle_type.toUpperCase().startsWith('HH');
    if (wanted.startsWith('high flow') !== isHighFlow) return false;
  }
  return true;
}

/**
 * The six positions as pickable options for one group, each with the reason it
 * cannot be used when it cannot. Always returns all six: an operator looking for
 * position 4 should find it greyed out with an explanation, not missing.
 */
export function rackOptionsForGroup(
  slots: NozzleRackSlot[] | undefined,
  group: RackGroupInfo,
  translate: (key: string, opts?: Record<string, unknown>) => string,
): RackPositionOption[] {
  const byPosition = rackByPosition(slots);

  return RACK_POSITIONS.map((position) => {
    const slot = byPosition.get(position);
    const eligible = isRackSlotEligible(slot, group);
    let reason: string | undefined;
    if (!eligible) {
      reason = slot
        ? translate('printModal.rackWrongNozzle', {
            has: `${slot.nozzle_diameter || '?'} ${slot.nozzle_type || ''}`.trim(),
            needs: `${group.nozzle_diameter} ${group.volume_type}`.trim(),
          })
        : translate('printModal.rackEmptyPosition');
    }
    return {
      position,
      diameter: slot?.nozzle_diameter ?? '',
      nozzleType: slot?.nozzle_type ?? '',
      filamentColor: slot?.filament_color ?? '',
      eligible,
      reason,
    };
  });
}

/**
 * A position for every rack-bound group, preferring one already loaded with the
 * group's own colour. Mirrors the dispatcher's assignment so the dialog shows
 * what will actually happen rather than leaving every picker blank.
 *
 * Returns null when some group cannot be placed — the caller then leaves the
 * pickers empty and lets the backend fall back, rather than showing a partial
 * assignment that reads as a decision.
 */
export function autoAssignRackPositions(
  slots: NozzleRackSlot[] | undefined,
  groups: Map<number, RackGroupInfo>,
  pinned: Record<number, number> = {},
): Record<number, number> | null {
  const byPosition = rackByPosition(slots);
  const assigned: Record<number, number> = {};
  const taken = new Set<number>();

  const rackGroupIds = [...groups.keys()].filter((id) => groups.get(id)!.on_rack).sort((a, b) => a - b);

  // Explicit picks are placed first so an auto-assignment yields to them
  // instead of claiming a position the operator asked for.
  for (const groupId of rackGroupIds) {
    const position = pinned[groupId];
    if (position == null) continue;
    if (taken.has(position) || !isRackSlotEligible(byPosition.get(position), groups.get(groupId)!)) return null;
    assigned[groupId] = position;
    taken.add(position);
  }

  for (const groupId of rackGroupIds) {
    if (assigned[groupId] != null) continue;
    const group = groups.get(groupId)!;
    const eligible = RACK_POSITIONS.filter((p) => !taken.has(p) && isRackSlotEligible(byPosition.get(p), group));
    if (eligible.length === 0) return null;

    const wanted = normaliseColor(group.filament_color);
    const preferred = wanted
      ? eligible.find((p) => normaliseColor(byPosition.get(p)?.filament_color) === wanted)
      : undefined;
    const chosen = preferred ?? eligible[0];
    assigned[groupId] = chosen;
    taken.add(chosen);
  }
  return assigned;
}

/** Hex colours arrive as `#RRGGBB` from the 3MF and `RRGGBBAA` from the printer. */
function normaliseColor(value: string | undefined): string {
  return (value ?? '').trim().replace(/^#/, '').slice(0, 6).toUpperCase();
}
