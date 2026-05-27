import { describe, it, expect, beforeEach } from 'vitest';
import {
  hexToColorName,
  getColorName,
  resolveSpoolColorName,
  setColorCatalog,
  __resetColorCatalogForTests,
} from '../../utils/colors';

describe('hexToColorName', () => {
  it('returns "Unknown" for null/empty input', () => {
    expect(hexToColorName(null)).toBe('Unknown');
    expect(hexToColorName('')).toBe('Unknown');
    expect(hexToColorName(undefined)).toBe('Unknown');
  });

  it('classifies dark low-saturation colors as Dark Gray', () => {
    // Titan Gray hex (5F6367) — low saturation, lightness < 0.4
    expect(hexToColorName('5F6367')).toBe('Dark Gray');
  });

  it('classifies black hex as Black', () => {
    expect(hexToColorName('000000')).toBe('Black');
  });

  it('classifies white hex as White', () => {
    expect(hexToColorName('FFFFFF')).toBe('White');
  });
});

describe('getColorName', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
  });

  it('looks up the runtime color catalog before HSL fallback', () => {
    setColorCatalog({ '5f6367': 'Titan Gray' });
    expect(getColorName('5f6367')).toBe('Titan Gray');
    expect(getColorName('5F6367')).toBe('Titan Gray');
  });

  it('falls back to HSL when hex is not in the runtime catalog', () => {
    // No catalog entry for 123456; HSL bucketing puts it in Blue.
    expect(getColorName('123456')).toBe('Blue');
  });

  it('returns "Unknown" for empty string', () => {
    expect(getColorName('')).toBe('Unknown');
  });

  it('handles hex with # prefix', () => {
    setColorCatalog({ '5f6367': 'Titan Gray' });
    expect(getColorName('#5f6367')).toBe('Titan Gray');
  });

  it('normalizes catalog keys (strips # and lowercases)', () => {
    // Provider can pass keys in any case / with or without '#'; the utility
    // must normalize so lookups succeed regardless of input shape.
    setColorCatalog({ '#F5B6CD': 'Cherry Pink' });
    expect(getColorName('F5B6CD')).toBe('Cherry Pink');
    expect(getColorName('f5b6cd')).toBe('Cherry Pink');
  });

  it('resolves #857 regression — A17-R1 / F5B6CD is Cherry Pink, not Scarlet Red', () => {
    setColorCatalog({ 'f5b6cd': 'Cherry Pink' });
    expect(getColorName('F5B6CDFF')).toBe('Cherry Pink');
  });
});

describe('resolveSpoolColorName', () => {
  beforeEach(() => {
    __resetColorCatalogForTests();
    setColorCatalog({ '5f6367': 'Titan Gray' });
  });

  it('returns readable color name directly', () => {
    expect(resolveSpoolColorName('Titan Gray', '5F6367FF')).toBe('Titan Gray');
  });

  it('looks up hex when color_name is a Bambu code', () => {
    expect(resolveSpoolColorName('A06-D0', '5F6367FF')).toBe('Titan Gray');
  });

  it('returns null when color_name is a code and hex is unknown', () => {
    expect(resolveSpoolColorName('A99-Z9', '12345600')).toBeNull();
  });
});
