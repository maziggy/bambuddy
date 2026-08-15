import { describe, it, expect } from 'vitest';
import type { NozzleRackSlot } from '../../api/client';
import type { RackGroupInfo } from '../../components/PrintModal/types';
import {
  RACK_POSITIONS,
  autoAssignRackPositions,
  isRackSlotEligible,
  rackByPosition,
  rackOptionsForGroup,
  rackPositionToNozzleId,
} from '../../utils/nozzleRack';

/**
 * Mirrors backend/tests/unit/test_nozzle_rack_positions_1784.py. The two
 * implementations have to agree or the picker greys out an option the
 * dispatcher would have accepted, or worse offers one it will refuse.
 */

const slot = (id: number, over: Partial<NozzleRackSlot> = {}): NozzleRackSlot => ({
  id,
  nozzle_type: 'HH01',
  nozzle_diameter: '0.4',
  wear: null,
  stat: null,
  max_temp: 300,
  serial_number: '',
  filament_color: '',
  filament_id: '',
  filament_type: '',
  ...over,
});

/** Live rack with a nozzle at each named 1-based position, plus the carriages. */
const rack = (present: number[] = [1, 2, 3, 4, 5, 6], over: Record<number, Partial<NozzleRackSlot>> = {}) => [
  slot(1),
  ...present.map((p) => slot(15 + p, over[p] ?? {})),
];

const group = (over: Partial<RackGroupInfo> = {}): RackGroupInfo => ({
  on_rack: true,
  nozzle_diameter: '0.40',
  volume_type: 'High Flow',
  filament_color: '',
  ...over,
});

const t = (key: string) => key;

describe('rack position numbering', () => {
  it('maps position n to physical nozzle id 15 + n', () => {
    expect(RACK_POSITIONS.map(rackPositionToNozzleId)).toEqual([16, 17, 18, 19, 20, 21]);
  });

  it.each([0, -1, 7, 1.5])('rejects %s as a position', (position) => {
    expect(rackPositionToNozzleId(position)).toBeNull();
  });
});

describe('reading the live rack', () => {
  it('recovers the mounted nozzle from the lone gap', () => {
    // Measured 2026-08-14 09:02: IDs [16, 1, 21, 19, 18, 0, 20] — id 17 absent
    // because that nozzle was picked up onto the carriage (#943).
    const slots = [...rack([1, 3, 4, 5, 6]), slot(0)];
    const byPosition = rackByPosition(slots);

    expect([...byPosition.keys()].sort((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6]);
    expect(byPosition.get(2)!.id).toBe(0);
  });

  it('leaves two gaps absent, because which one is mounted is unknowable', () => {
    const byPosition = rackByPosition([...rack([1, 4, 5, 6]), slot(0)]);
    expect([...byPosition.keys()].sort((a, b) => a - b)).toEqual([1, 4, 5, 6]);
  });

  it('fills no gap from an empty carriage', () => {
    const empty = slot(0, { nozzle_diameter: '', nozzle_type: '' });
    expect(rackByPosition([...rack([1, 3, 4, 5, 6]), empty]).has(2)).toBe(false);
  });

  it('ignores the fixed carriage entirely', () => {
    expect(rackByPosition([slot(1)]).size).toBe(0);
  });
});

describe('eligibility', () => {
  it('matches a padded slice diameter against an unpadded printer one', () => {
    expect(isRackSlotEligible(slot(16, { nozzle_diameter: '0.4' }), group())).toBe(true);
  });

  it('rejects the wrong diameter', () => {
    expect(isRackSlotEligible(slot(16, { nozzle_diameter: '0.6' }), group())).toBe(false);
  });

  it('rejects the wrong flow type', () => {
    expect(isRackSlotEligible(slot(16, { nozzle_type: 'HS' }), group())).toBe(false);
  });

  it('does not rule out a printer that reports no flow type', () => {
    expect(isRackSlotEligible(slot(16, { nozzle_type: '' }), group())).toBe(true);
  });

  it('rejects an empty position', () => {
    expect(isRackSlotEligible(slot(16, { nozzle_diameter: '', nozzle_type: '' }), group())).toBe(false);
  });

  it('rejects a position that is not there at all', () => {
    expect(isRackSlotEligible(undefined, group())).toBe(false);
  });
});

describe('the options offered for a group', () => {
  it('always offers all six, so a position is greyed out rather than missing', () => {
    const options = rackOptionsForGroup(rack([1, 2]), group(), t);

    expect(options).toHaveLength(6);
    expect(options.filter((o) => o.eligible).map((o) => o.position)).toEqual([1, 2]);
  });

  it('says why an empty position cannot be used', () => {
    const options = rackOptionsForGroup(rack([1]), group(), t);
    expect(options[1].reason).toBe('printModal.rackEmptyPosition');
  });

  it('says why a wrong nozzle cannot be used', () => {
    const options = rackOptionsForGroup(rack([1, 2], { 2: { nozzle_diameter: '0.6' } }), group(), t);
    expect(options[1].reason).toBe('printModal.rackWrongNozzle');
  });
});

describe('auto-assignment', () => {
  const groups = new Map<number, RackGroupInfo>([
    [0, group({ on_rack: false, filament_color: '#F4EE2A' })],
    [1, group({ filament_color: '#0078BF' })],
    [2, group({ filament_color: '#DE4343' })],
  ]);

  it('prefers the position already loaded with the group colour', () => {
    // Reproduces BambuStudio's own pick for this plate: group 2 (red) to R1,
    // group 1 (blue) to R2 — dispatched as [16, 1, 17] on 2026-08-14.
    const loaded = rack([1, 2, 3, 4, 5, 6], {
      1: { filament_color: 'DE4343FF' },
      2: { filament_color: '0078BFFF' },
    });
    expect(autoAssignRackPositions(loaded, groups)).toEqual({ 1: 2, 2: 1 });
  });

  it('falls back to the lowest free position when no colour matches', () => {
    expect(autoAssignRackPositions(rack(), groups)).toEqual({ 1: 1, 2: 2 });
  });

  it('never reassigns a position the operator pinned', () => {
    expect(autoAssignRackPositions(rack(), groups, { 1: 4 })).toEqual({ 1: 4, 2: 1 });
  });

  it('gives up rather than half-assigning when a group cannot be placed', () => {
    expect(autoAssignRackPositions(rack([1]), groups)).toBeNull();
  });

  it('gives up when a pinned position is no longer eligible', () => {
    expect(autoAssignRackPositions(rack([1, 2]), groups, { 1: 5 })).toBeNull();
  });

  it('assigns nothing, successfully, when no group needs the rack', () => {
    const fixedOnly = new Map<number, RackGroupInfo>([[0, group({ on_rack: false })]]);
    expect(autoAssignRackPositions(rack([]), fixedOnly)).toEqual({});
  });
});
