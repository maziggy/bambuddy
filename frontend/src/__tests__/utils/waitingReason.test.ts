/**
 * The frontend half of the `waiting_reason` shape contract (#3074).
 *
 * The scheduler encodes "starts by itself" as a reason made only of `Busy: ...`
 * clauses, joined with ` | `, and decides notifications on that basis. The UI
 * reads the same bit to decide what still belongs on a forecast. These cases
 * are the exact strings both branches of the scheduler emit, so a reword on
 * either side lands here first.
 */

import { describe, it, expect } from 'vitest';
import { formatWaitingReason, isBusyOnlyWaitingReason } from '../../utils/waitingReason';

describe('isBusyOnlyWaitingReason', () => {
  it('treats nothing as not-busy rather than busy', () => {
    // An item with no reason is dispatchable, not "waiting its turn"; callers
    // check the reason's presence separately.
    expect(isBusyOnlyWaitingReason(null)).toBe(false);
    expect(isBusyOnlyWaitingReason(undefined)).toBe(false);
    expect(isBusyOnlyWaitingReason('')).toBe(false);
  });

  it.each([
    'Busy: X1C-01',
    'Busy: X1C-01 (drying)',
    'Busy: X1C-01, X1C-02',
    'Busy: X1C-01 | Busy: X1C-02',
  ])('reads %s as waiting its turn', reason => {
    expect(isBusyOnlyWaitingReason(reason)).toBe(true);
  });

  it.each([
    'Waiting for plate confirmation: X1C-01',
    'Offline, no Auto On smart plug: X1C-01',
    'Offline: X1C-01 — the smart plug could not power it on',
    'Waiting on Enclosure Door',
    'Waiting for filament: X1C-01 (needs PETG)',
    'No available X1C printers',
    'Every file for this job has been deleted — add a file back or remove the item',
  ])('reads %s as waiting for the user', reason => {
    expect(isBusyOnlyWaitingReason(reason)).toBe(false);
  });

  it('needs every clause to be busy, not just the first', () => {
    // The scheduler only joins with " | " when every clause is busy, but the
    // reader must not assume that -- one clause needing the user makes the
    // whole reason need the user.
    expect(isBusyOnlyWaitingReason('Busy: X1C-01 | Offline: X1C-02')).toBe(false);
  });
});

describe('formatWaitingReason (#3587)', () => {
  // Echo the key and params so the test sees exactly what was asked for.
  const t = (key: string, options?: Record<string, unknown>) =>
    options ? `${key}(${Object.values(options).join(',')})` : key;

  it('translates the "priority in N h" suffix and the filament prefix', () => {
    expect(formatWaitingReason('Waiting for filament: PLA Earth Brown · priority in 5 h', t)).toBe(
      'queue.waiting.filament(PLA Earth Brown) · queue.waiting.priorityIn(5)',
    );
  });

  it('translates the "has priority" suffix', () => {
    expect(formatWaitingReason('Waiting for filament: PETG · has priority', t)).toBe(
      'queue.waiting.filament(PETG) · queue.waiting.hasPriority',
    );
  });

  it('translates a filament part and leaves busy/offline parts untouched', () => {
    expect(
      formatWaitingReason('Busy: P1 | Waiting for filament: P2 (needs PLA) | Offline: P3 · priority in 1 h', t),
    ).toBe('Busy: P1 | queue.waiting.filament(P2 (needs PLA)) | Offline: P3 · queue.waiting.priorityIn(1)');
  });

  it('translates the filament prefix after a model label in cross-model reasons', () => {
    expect(
      formatWaitingReason(
        'H2S: Busy: P1 (kept free for a job waiting for filament); H2C: Waiting for filament: P2 (needs PLA; PETG) · priority in 2 h',
        t,
      ),
    ).toBe(
      'H2S: Busy: P1 (kept free for a job waiting for filament); H2C: queue.waiting.filament(P2 (needs PLA; PETG)) · queue.waiting.priorityIn(2)',
    );
  });

  it('translates the no-match reason, alone and behind a model label', () => {
    expect(formatWaitingReason('No matching material/color. Waiting on P1, P2', t)).toBe(
      'queue.waiting.noMatch(P1, P2)',
    );
    expect(
      formatWaitingReason('H2S: No matching material/color. Waiting on P1; H2C: Busy: P3', t),
    ).toBe('H2S: queue.waiting.noMatch(P1); H2C: Busy: P3');
  });

  it('leaves busy-only and plain strings untouched', () => {
    expect(formatWaitingReason('Busy: P1 | Busy: P2', t)).toBe('Busy: P1 | Busy: P2');
    expect(formatWaitingReason('Waiting for matching printer', t)).toBe('Waiting for matching printer');
  });
});
