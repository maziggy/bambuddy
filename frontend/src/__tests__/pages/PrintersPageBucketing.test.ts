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
 * Mirrors the logic at PrintersPage.tsx:917-948 and the classifyPrinterStatus
 * helper at PrintersPage.tsx:1028 — kept as inline copies so this test
 * doesn't need the helpers to be exported.
 */
import { describe, it, expect } from 'vitest';

type Status = {
  connected: boolean;
  state: string | null;
  hms_errors?: { code: string; attr: number; severity: number }[];
};

type Bucket = 'printing' | 'paused' | 'finished' | 'idle' | 'offline' | 'error';

const KNOWN_HMS_CODES = new Set(['0300_4057', '0500_4038']);

function filterKnownHMSErrors(errors: Status['hms_errors']): NonNullable<Status['hms_errors']> {
  return (errors ?? []).filter((e) => {
    const codeNum = parseInt(e.code.replace('0x', ''), 16) || 0;
    const module = ((e.attr >> 16) & 0xFFFF).toString(16).padStart(4, '0').toUpperCase();
    const code = (codeNum & 0xFFFF).toString(16).padStart(4, '0').toUpperCase();
    return KNOWN_HMS_CODES.has(`${module}_${code}`);
  });
}

function classifyPrinterStatus(status: Status | undefined): Bucket {
  if (!status?.connected) return 'offline';
  const knownHms = filterKnownHMSErrors(status.hms_errors);
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
      hms_errors: [{ code: '0x4057', attr: 0x0300_0000, severity: 1 }],
    };
    expect(classifyPrinterStatus(reallyFailedPrinter)).toBe('error');
  });

  it('classifies FAILED + only unknown HMS as "finished" (unknown codes are not "real" problems by our taxonomy)', () => {
    const cancelEcho: Status = {
      connected: true,
      state: 'FAILED',
      hms_errors: [{ code: '0x2001b', attr: 0x0C00_0C00, severity: 1 }], // 0C00_001B not in known set
    };
    expect(classifyPrinterStatus(cancelEcho)).toBe('finished');
  });

  it('classifies FINISH as "finished" (unchanged baseline)', () => {
    const completedPrinter: Status = { connected: true, state: 'FINISH' };
    expect(classifyPrinterStatus(completedPrinter)).toBe('finished');
  });

  it('classifies disconnected printer as "offline" (HMS / state irrelevant)', () => {
    const offline: Status = {
      connected: false,
      state: 'FAILED',
      hms_errors: [{ code: '0x4057', attr: 0x0300_0000, severity: 1 }],
    };
    expect(classifyPrinterStatus(offline)).toBe('offline');
  });
});
