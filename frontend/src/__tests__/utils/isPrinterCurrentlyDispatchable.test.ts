/**
 * Tests for the shared "would ASAP mean now" predicate (#2849).
 *
 * Every print goes through the queue, so this is not "can we print at all" —
 * it is whether a queue item aimed at this printer starts immediately or
 * waits. The PrintModal uses it to promise a later start; the printer card
 * uses it to decide whether a dropped file says "Drop to print" or "Drop to
 * queue". They share it so the card cannot promise one thing and the modal
 * immediately contradict it.
 */

import { describe, it, expect } from 'vitest';
import type { PrinterStatus } from '../../api/client';
import { isPrinterCurrentlyDispatchable } from '../../utils/printer';

const status = (over: Partial<PrinterStatus> = {}): PrinterStatus =>
  ({ connected: true, state: 'IDLE', ...over }) as PrinterStatus;

describe('isPrinterCurrentlyDispatchable', () => {
  it('accepts the states that can take a print right now', () => {
    for (const state of ['IDLE', 'FINISH', 'FAILED']) {
      expect(isPrinterCurrentlyDispatchable(status({ state }))).toBe(true);
    }
  });

  it('rejects a printer that is mid-print', () => {
    // The #2849 case: the drop is still allowed, it just queues.
    expect(isPrinterCurrentlyDispatchable(status({ state: 'RUNNING' }))).toBe(false);
    expect(isPrinterCurrentlyDispatchable(status({ state: 'PAUSE' }))).toBe(false);
    expect(isPrinterCurrentlyDispatchable(status({ state: 'PREPARE' }))).toBe(false);
  });

  it('rejects a disconnected printer whatever its last known state', () => {
    expect(isPrinterCurrentlyDispatchable(status({ connected: false, state: 'IDLE' }))).toBe(false);
  });

  it('rejects a printer still awaiting plate clear', () => {
    // FINISH alone would pass; the plate is physically in the way.
    expect(isPrinterCurrentlyDispatchable(status({ state: 'FINISH', awaiting_plate_clear: true }))).toBe(false);
  });

  it('rejects a printer whose AMS is drying', () => {
    expect(
      isPrinterCurrentlyDispatchable(status({ ams: [{ dry_time: 240 }] as PrinterStatus['ams'] }))
    ).toBe(false);
  });

  it('ignores an AMS that is loaded but not drying', () => {
    expect(
      isPrinterCurrentlyDispatchable(status({ ams: [{ dry_time: 0 }] as PrinterStatus['ams'] }))
    ).toBe(true);
  });

  it('treats an unknown or missing status as not dispatchable', () => {
    expect(isPrinterCurrentlyDispatchable(undefined)).toBe(false);
    expect(isPrinterCurrentlyDispatchable(status({ state: undefined }))).toBe(false);
  });
});
