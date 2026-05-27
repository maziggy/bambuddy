/**
 * Tests for getPrinterImage — model → printer card image resolver.
 *
 * X2D support (#988): both the display name "X2D" and the internal SSDP
 * code "N6" must resolve to /img/printers/x2d.png so the Printers page
 * and PrinterInfoModal show the correct artwork instead of falling back
 * to default.png.
 */

import { describe, it, expect } from 'vitest';
import { getPrinterImage } from '../../utils/printer';

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
