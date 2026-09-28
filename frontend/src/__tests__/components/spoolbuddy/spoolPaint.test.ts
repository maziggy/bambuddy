/**
 * The SpoolBuddy screens paint a spool the way the Filament page does (#3033).
 */

import { describe, it, expect } from 'vitest';
import { hasFilamentPaint, spoolSwatchStyle } from '../../../components/spoolbuddy/spoolPaint';

describe('hasFilamentPaint', () => {
  it('is true for extra colour stops or an effect', () => {
    expect(hasFilamentPaint({ rgba: '044482FF', extra_colors: '044482,f8d008' })).toBe(true);
    expect(hasFilamentPaint({ rgba: '111111FF', effect_type: 'marble' })).toBe(true);
  });

  it('is false for a plain spool, a missing spool, or stops that do not parse', () => {
    expect(hasFilamentPaint({ rgba: '00AE42FF', extra_colors: null, effect_type: null })).toBe(false);
    expect(hasFilamentPaint({ rgba: '00AE42FF', extra_colors: '', effect_type: '' })).toBe(false);
    expect(hasFilamentPaint({ rgba: '00AE42FF', extra_colors: 'nothex' })).toBe(false);
    expect(hasFilamentPaint(null)).toBe(false);
    expect(hasFilamentPaint(undefined)).toBe(false);
  });
});

describe('spoolSwatchStyle', () => {
  it('returns the layered background for a dual-colour spool', () => {
    const style = spoolSwatchStyle({ rgba: '044482FF', extra_colors: '044482,f8d008', effect_type: 'dual-color' });
    expect(style).not.toBeNull();
    expect(String(style!.backgroundImage)).toContain('#044482');
    expect(String(style!.backgroundImage)).toContain('#f8d008');
    expect(style!.backgroundSize).toBeTruthy();
  });

  it('returns null for a plain spool so the caller keeps its own style', () => {
    expect(spoolSwatchStyle({ rgba: '00AE42FF' })).toBeNull();
  });
});
