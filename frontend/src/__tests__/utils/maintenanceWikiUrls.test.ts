/**
 * Unit tests for getMaintenanceWikiUrl — model-aware wiki URL resolver.
 *
 * Covers the X2D classification (#988): X2D has hardened steel rods like
 * P2S, NOT carbon rods and NOT linear rails. It must resolve to the P2S
 * wiki pages for steel-rod-specific tasks.
 *
 * Also guards against regressions for the existing families (X1, P1, A1,
 * H2D, P2S) so that broadening the rod-type bucket for X2D did not
 * accidentally change their mappings.
 */

import { describe, it, expect } from 'vitest';
import { getMaintenanceWikiUrl } from '../../utils/maintenanceWikiUrls';

describe('getMaintenanceWikiUrl', () => {
  describe('X2D (#988)', () => {
    it('resolves "Lubricate Steel Rods" to the P2S wiki page', () => {
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', 'X2D')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis',
      );
    });

    it('resolves "Clean Steel Rods" to the P2S wiki page', () => {
      expect(getMaintenanceWikiUrl('Clean Steel Rods', 'X2D')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis',
      );
    });

    it('resolves belt tension to the P2S wiki page', () => {
      expect(getMaintenanceWikiUrl('Check Belt Tension', 'X2D')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/belt-tension',
      );
    });

    it('resolves nozzle cold pull to the P2S wiki page', () => {
      expect(getMaintenanceWikiUrl('Clean Nozzle/Hotend', 'X2D')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/cold-pull-maintenance-hotend',
      );
    });

    it('does not return a carbon-rod wiki URL for X2D', () => {
      // "Clean Carbon Rods" is X1/P1-only; X2D must resolve to null so the
      // task button renders without a link rather than pointing at the wrong page.
      expect(getMaintenanceWikiUrl('Clean Carbon Rods', 'X2D')).toBeNull();
    });

    it('does not return a linear-rail wiki URL for X2D', () => {
      // "Lubricate Linear Rails" is A1/H2-only.
      expect(getMaintenanceWikiUrl('Lubricate Linear Rails', 'X2D')).toBeNull();
    });
  });

  describe('regression: P2S still maps to P2S wiki pages', () => {
    it('still resolves Lubricate Steel Rods for P2S', () => {
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', 'P2S')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis',
      );
    });

    it('still resolves belt tension for P2S', () => {
      expect(getMaintenanceWikiUrl('Check Belt Tension', 'P2S')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/belt-tension',
      );
    });
  });

  describe('regression: other families untouched', () => {
    it('X1C belt tension unchanged', () => {
      expect(getMaintenanceWikiUrl('Check Belt Tension', 'X1C')).toBe(
        'https://wiki.bambulab.com/en/x1/maintenance/belt-tension',
      );
    });

    it('H2D belt tension unchanged', () => {
      expect(getMaintenanceWikiUrl('Check Belt Tension', 'H2D')).toBe(
        'https://wiki.bambulab.com/en/h2/maintenance/belt-tension',
      );
    });

    it('A1 Mini linear rails unchanged', () => {
      expect(getMaintenanceWikiUrl('Lubricate Linear Rails', 'A1 Mini')).toBe(
        'https://wiki.bambulab.com/en/a1-mini/maintenance/lubricate-y-axis',
      );
    });

    it('X1C carbon rods unchanged', () => {
      expect(getMaintenanceWikiUrl('Clean Carbon Rods', 'X1C')).toBe(
        'https://wiki.bambulab.com/en/general/carbon-rods-clearance',
      );
    });

    it('P2S still does not resolve linear-rail task', () => {
      // Sanity check: the X2D broadening must not have widened P2S into
      // unrelated task categories.
      expect(getMaintenanceWikiUrl('Lubricate Linear Rails', 'P2S')).toBeNull();
    });
  });

  describe('model name normalisation', () => {
    it('matches X2D regardless of hyphens or spaces', () => {
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', 'x-2d')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis',
      );
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', 'x 2d')).toBe(
        'https://wiki.bambulab.com/en/p2s/maintenance/lubricate-x-y-z-axis',
      );
    });

    it('returns null for empty model', () => {
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', null)).toBeNull();
      expect(getMaintenanceWikiUrl('Lubricate Steel Rods', '')).toBeNull();
    });
  });
});
