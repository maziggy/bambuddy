/**
 * Unit tests for the formatPrintName helper on PrintersPage.
 *
 * Regression coverage for the #881 follow-up: when the printer card has an
 * archive-linked plate label (resolved from the backend's current_archive_id
 * + the archive's is_multi_plate plate list), the label must take precedence
 * over the gcode_file regex fallback, including for plate 1.
 */

import { describe, it, expect } from 'vitest';
import { formatPrintName } from '../../utils/printName';

// Minimal translator stub: returns the fallback with the plate number interpolated
// the same way i18next would. Keeps these tests independent of the i18n setup.
const t = (_key: string, fallback: string, opts?: Record<string, unknown>) =>
  fallback.replace('{{number}}', String(opts?.number ?? ''));

describe('formatPrintName', () => {
  it('returns the name unchanged when neither plate source is available', () => {
    expect(formatPrintName('Benchy', null, t)).toBe('Benchy');
  });

  it('appends gcode-file plate number only when > 1 (single-plate noise guard)', () => {
    // Plate 1 from gcode_file alone is ambiguous (could be a single-plate 3MF)
    // so the legacy fallback path keeps it silent.
    expect(formatPrintName('Benchy', '/Metadata/plate_1.gcode', t)).toBe('Benchy');
    expect(formatPrintName('Benchy', '/Metadata/plate_2.gcode', t)).toBe('Benchy — Plate 2');
  });

  it('uses plateLabel verbatim when provided, overriding the gcode_file fallback', () => {
    // plateLabel comes from the archive lookup and is already disambiguated
    // (only set when is_multi_plate === true). It must show even for plate 1.
    expect(formatPrintName('Benchy', '/Metadata/plate_1.gcode', t, 'Plate 1')).toBe('Benchy — Plate 1');
    expect(formatPrintName('Benchy', '/Metadata/plate_2.gcode', t, 'Small Parts')).toBe('Benchy — Small Parts');
  });

  it('returns empty string when name is missing, regardless of plate info', () => {
    expect(formatPrintName(null, '/Metadata/plate_2.gcode', t)).toBe('');
    expect(formatPrintName(null, null, t, 'Plate 3')).toBe('');
  });

  it('treats null/empty plateLabel as absent and falls through to gcode_file parsing', () => {
    expect(formatPrintName('Benchy', '/Metadata/plate_2.gcode', t, null)).toBe('Benchy — Plate 2');
    expect(formatPrintName('Benchy', '/Metadata/plate_2.gcode', t, '')).toBe('Benchy — Plate 2');
  });
});
