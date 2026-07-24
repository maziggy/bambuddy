/**
 * Tests for getPrinterImage — model → printer card image resolver.
 *
 * X2D support (#988): both the display name "X2D" and the internal SSDP
 * code "N6" must resolve to /img/printers/x2d.png so the Printers page
 * and PrinterInfoModal show the correct artwork instead of falling back
 * to default.png.
 */

import { describe, it, expect } from 'vitest';
import { getPrinterImage, isGcodeCompatible, filterCompatibleQueueItems } from '../../utils/printer';
import type { PrintQueueItem } from '../../api/client';

describe('getPrinterImage', () => {
  describe('X2D (#988)', () => {
    it('resolves display name "X2D" to x2d.png', () => {
      expect(getPrinterImage('X2D')).toBe('/img/printers/x2d.png');
    });

    it('resolves case-insensitive variants', () => {
      expect(getPrinterImage('x2d')).toBe('/img/printers/x2d.png');
      expect(getPrinterImage(' X2D ')).toBe('/img/printers/x2d.png');
    });

    it('resolves the internal SSDP code "N6" to x2d.png', () => {
      expect(getPrinterImage('N6')).toBe('/img/printers/x2d.png');
    });

    it('does not match X2D on unrelated model strings', () => {
      // Regression guard: a hypothetical future "X2" model must not
      // silently pick up x2d.png until it's explicitly mapped.
      expect(getPrinterImage('X2E')).toBe('/img/printers/default.png');
    });
  });

  describe('A2L (#1684)', () => {
    it('resolves display name "A2L" to a2l.png', () => {
      expect(getPrinterImage('A2L')).toBe('/img/printers/a2l.png');
    });

    it('resolves case-insensitive variants', () => {
      expect(getPrinterImage('a2l')).toBe('/img/printers/a2l.png');
      expect(getPrinterImage(' A2L ')).toBe('/img/printers/a2l.png');
    });

    it('resolves the internal SSDP code "N9" to a2l.png', () => {
      expect(getPrinterImage('N9')).toBe('/img/printers/a2l.png');
    });

    it('does not match A2L on unrelated A-series strings', () => {
      // Regression guard: a hypothetical future "A2M" or similar must not
      // silently pick up a2l.png until it's explicitly mapped, and "A1" /
      // "A1 Mini" must still resolve to their own artwork.
      expect(getPrinterImage('A2M')).toBe('/img/printers/default.png');
      expect(getPrinterImage('A1')).toBe('/img/printers/a1.png');
      expect(getPrinterImage('A1 Mini')).toBe('/img/printers/a1mini.png');
    });
  });

  describe('regression: existing families unchanged', () => {
    it('X1C → x1c.png', () => {
      expect(getPrinterImage('X1C')).toBe('/img/printers/x1c.png');
    });

    it('X1E → x1e.png', () => {
      expect(getPrinterImage('X1E')).toBe('/img/printers/x1e.png');
    });

    it('H2D → h2d.png', () => {
      expect(getPrinterImage('H2D')).toBe('/img/printers/h2d.png');
    });

    it('H2D Pro → h2dpro.png', () => {
      expect(getPrinterImage('H2D Pro')).toBe('/img/printers/h2dpro.png');
    });

    it('P2S → p1s.png (shared with P1S)', () => {
      // Pre-existing behaviour: P2S currently reuses the P1S artwork. Not
      // changed by the X2D diff; asserted to catch accidental regressions.
      expect(getPrinterImage('P2S')).toBe('/img/printers/p1s.png');
    });

    it('A1 Mini → a1mini.png (not a1.png)', () => {
      // The "a1mini" branch must run before the generic "a1" branch —
      // the X2D branch was inserted above both and must not break order.
      expect(getPrinterImage('A1 Mini')).toBe('/img/printers/a1mini.png');
    });

    it('null / undefined → default.png', () => {
      expect(getPrinterImage(null)).toBe('/img/printers/default.png');
      expect(getPrinterImage(undefined)).toBe('/img/printers/default.png');
    });

    it('unknown model → default.png', () => {
      expect(getPrinterImage('SomeFuturePrinter')).toBe(
        '/img/printers/default.png',
      );
    });
  });
});

// Mirrors backend/tests/unit/test_scheduler_model_mismatch.py — the frontend
// table must stay in sync with backend GCODE_COMPAT_FAMILIES (#2578).
describe('isGcodeCompatible', () => {
  it('accepts same model', () => {
    expect(isGcodeCompatible('X1C', 'X1C')).toBe(true);
    expect(isGcodeCompatible('H2D', 'H2D')).toBe(true);
  });

  it('accepts the X1/P1 interchange family', () => {
    expect(isGcodeCompatible('X1C', 'P1S')).toBe(true);
    expect(isGcodeCompatible('X1C', 'P1P')).toBe(true);
    expect(isGcodeCompatible('P1S', 'X1E')).toBe(true);
    expect(isGcodeCompatible('X1', 'P1P')).toBe(true);
  });

  it('rejects cross-family targets', () => {
    // The reporter's case: X1C-sliced G-code targeted at an H2D (#2578)
    expect(isGcodeCompatible('X1C', 'H2D')).toBe(false);
    expect(isGcodeCompatible('P1S', 'H2D')).toBe(false);
    expect(isGcodeCompatible('X1C', 'A1')).toBe(false);
    expect(isGcodeCompatible('A1', 'A1 Mini')).toBe(false);
    expect(isGcodeCompatible('H2D', 'H2S')).toBe(false);
    expect(isGcodeCompatible('X1C', 'P2S')).toBe(false);
  });

  it('is fail-safe on unknown metadata', () => {
    expect(isGcodeCompatible(null, 'H2D')).toBe(true);
    expect(isGcodeCompatible('X1C', null)).toBe(true);
    expect(isGcodeCompatible(undefined, undefined)).toBe(true);
    expect(isGcodeCompatible('', 'H2D')).toBe(true);
  });

  it('normalizes case, spaces and dashes', () => {
    expect(isGcodeCompatible('x1c', 'X1C')).toBe(true);
    expect(isGcodeCompatible('A1 Mini', 'A1-MINI')).toBe(true);
    expect(isGcodeCompatible('H2D Pro', 'H2DPRO')).toBe(true);
  });
});

describe('filterCompatibleQueueItems — force-color PLA variant (#2650)', () => {
  const makeItem = (
    overrides: Array<{ slot_id: number; type: string; color: string; tray_info_idx?: string; force_color_match?: boolean }>,
  ): PrintQueueItem => ({ id: 1, filament_overrides: overrides } as unknown as PrintQueueItem);

  // A job sliced for White PLA Matte (GFA01).
  const matteJob = makeItem([
    { slot_id: 1, type: 'PLA', color: '#FFFFFF', tray_info_idx: 'GFA01', force_color_match: true },
  ]);
  const loadedTypes = new Set(['PLA']);
  const loaded = new Set(['PLA:ffffff']);

  it('rejects a printer loaded only with other white PLA variants (Basic/Silk)', () => {
    const variants = new Set(['PLA:ffffff:GFA00', 'PLA:ffffff:GFA06']);
    expect(filterCompatibleQueueItems([matteJob], loadedTypes, loaded, variants)).toHaveLength(0);
  });

  it('accepts a printer loaded with the matching variant (Matte GFA01)', () => {
    const variants = new Set(['PLA:ffffff:GFA00', 'PLA:ffffff:GFA01']);
    expect(filterCompatibleQueueItems([matteJob], loadedTypes, loaded, variants)).toHaveLength(1);
  });

  it('accepts a same-colour spool that reports no tray_info_idx (custom/third-party)', () => {
    const variants = new Set(['PLA:ffffff:']);
    expect(filterCompatibleQueueItems([matteJob], loadedTypes, loaded, variants)).toHaveLength(1);
  });

  it('falls back to type+colour when no variant data is supplied', () => {
    // loadedVariants omitted → the hint is never stricter than the data it has.
    expect(filterCompatibleQueueItems([matteJob], loadedTypes, loaded)).toHaveLength(1);
  });

  it('an override without a tray_info_idx keeps the old type+colour behaviour', () => {
    const noIdxJob = makeItem([{ slot_id: 1, type: 'PLA', color: '#FFFFFF', force_color_match: true }]);
    const variants = new Set(['PLA:ffffff:GFA06']);
    expect(filterCompatibleQueueItems([noIdxJob], loadedTypes, loaded, variants)).toHaveLength(1);
  });
});
