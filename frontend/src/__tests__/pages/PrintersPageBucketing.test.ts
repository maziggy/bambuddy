/**
 * Regression tests for the printer-status bucketing logic in PrintersPage.tsx.
 *
 * The bug: a printer in gcode_state="FAILED" with no active HMS errors was
 * being counted as a "problem" in the header badge — this is the post-cancel
 * terminal state, not a real fault. After cancelling a print on h2d-1 the
 * printer card kept showing "1 problem" forever even after the HMS list was
 * empty, until the next print started.
 *
 * The fix: FAILED-without-HMS is bucketed as "finished" (same operator
 * meaning: print ended, plate may need clearing). FAILED-with-HMS still
 * counts as a problem because there's a real fault to investigate.
 *
 * Mirrors the classifyPrinterStatus helper in PrintersPage.tsx as an inline
 * copy so this test doesn't need it exported. The HMS filter is the real one:
 * a copy of it here once kept the old catalogue rule after the rule changed.
 */
import { describe, it, expect } from 'vitest';
import { filterKnownHMSErrors } from '../../components/HMSErrorModal';
import type { HMSError } from '../../api/client';

type Status = {
  connected: boolean;
  state: string | null;
  hms_errors?: HMSError[];
};

type Bucket = 'printing' | 'paused' | 'finished' | 'idle' | 'offline' | 'error';

function classifyPrinterStatus(status: Status | undefined): Bucket {
  if (!status?.connected) return 'offline';
  const knownHms = filterKnownHMSErrors(status.hms_errors ?? []);
  if (knownHms.length > 0) return 'error';
  switch (status.state) {
    case 'RUNNING': return 'printing';
    case 'PAUSE': return 'paused';
    case 'FINISH': return 'finished';
    case 'FAILED': return 'finished';
    default: return 'idle';
  }
}

describe('FAILED-without-HMS bucketing', () => {
  it('classifies FAILED with no HMS errors as "finished" (post-cancel terminal state, not a problem)', () => {
    const cancelledPrinter: Status = {
      connected: true,
      state: 'FAILED',
      hms_errors: [],
    };
    expect(classifyPrinterStatus(cancelledPrinter)).toBe('finished');
  });

  it('classifies FAILED + active known HMS as "error"', () => {
    const reallyFailedPrinter: Status = {
      connected: true,
      state: 'FAILED',
      hms_errors: [{ code: '0x4057', attr: 0x0300_4057, severity: 1, full_code: '03004057', description: 'Z-axis step loss detected.' }],
    };
    expect(classifyPrinterStatus(reallyFailedPrinter)).toBe('error');
  });

  it('classifies FAILED + only unknown HMS as "finished" (unknown codes are not "real" problems by our taxonomy)', () => {
    const cancelEcho: Status = {
      connected: true,
      state: 'FAILED',
      // The H2S post-cancel echo 0C00-0100-0002-001B: Bambu lists it with no text.
      hms_errors: [{ code: '0x2001b', attr: 0x0C00_0100, severity: 2, full_code: '0C0001000002001B', description: null }],
    };
    expect(classifyPrinterStatus(cancelEcho)).toBe('finished');
  });

  it('classifies PAUSE + uncataloged HMS WITH actions as "error" (#1840: H2C 0500_809C carries actions but isnt in the bundled catalog)', () => {
    const h2cActionableFault: Status = {
      connected: true,
      state: 'PAUSE',
      hms_errors: [{
        code: '0x809c',
        attr: 0x0500_809C,
        severity: 3,
        actions: ['IGNORE_RESUME', 'PROBLEM_SOLVED_RESUME'],
      }],
    };
    expect(classifyPrinterStatus(h2cActionableFault)).toBe('error');
  });

  it('classifies FINISH as "finished" (unchanged baseline)', () => {
    const completedPrinter: Status = { connected: true, state: 'FINISH' };
    expect(classifyPrinterStatus(completedPrinter)).toBe('finished');
  });

  it('classifies disconnected printer as "offline" (HMS / state irrelevant)', () => {
    const offline: Status = {
      connected: false,
      state: 'FAILED',
      hms_errors: [{ code: '0x4057', attr: 0x0300_4057, severity: 1, full_code: '03004057', description: 'Z-axis step loss detected.' }],
    };
    expect(classifyPrinterStatus(offline)).toBe('offline');
  });
});
