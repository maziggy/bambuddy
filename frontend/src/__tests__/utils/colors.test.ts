import { describe, it, expect } from 'vitest';
import { hexToColorName, getColorName, resolveSpoolColorName } from '../../utils/colors';

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
  it('looks up Bambu hex colors before HSL fallback', () => {
    // 5f6367 is in BAMBU_HEX_COLORS as "Titan Gray"
    expect(getColorName('5f6367')).toBe('Titan Gray');
    // Also with uppercase
    expect(getColorName('5F6367')).toBe('Titan Gray');
  });

  it('looks up alternative Titan Gray hex', () => {
    // 565656 is also mapped to "Titan Gray" in BAMBU_HEX_COLORS
    expect(getColorName('565656')).toBe('Titan Gray');
  });

  it('falls back to HSL for unknown hex colors', () => {
    // A hex that is not in the Bambu database
    expect(getColorName('123456')).toBe('Blue');
  });

  it('returns "Unknown" for empty string', () => {
    expect(getColorName('')).toBe('Unknown');
  });

  it('handles hex with # prefix', () => {
    expect(getColorName('#5f6367')).toBe('Titan Gray');
  });
});

describe('resolveSpoolColorName', () => {
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
