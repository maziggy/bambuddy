/**
 * Multi-material `filament_type` arrives joined two ways: `", "` from the 3MF
 * parser and `","` from the spool-based rewrites. Both must split the same way
 * the backend's /archives/stats does (#3262).
 */

import { describe, it, expect } from 'vitest';
import { splitFilamentTypes } from '../../utils/filamentTypes';

describe('splitFilamentTypes (#3262)', () => {
  it('splits a value written from spools (no space)', () => {
    expect(splitFilamentTypes('PLA Basic,PLA')).toEqual(['PLA Basic', 'PLA']);
  });

  it('splits a value written by the 3MF parser (comma + space)', () => {
    expect(splitFilamentTypes('PLA, PLA-S')).toEqual(['PLA', 'PLA-S']);
  });

  it('keeps a single material, including one with a space in its name', () => {
    expect(splitFilamentTypes('PLA Basic')).toEqual(['PLA Basic']);
  });

  it('drops empty parts and repeats', () => {
    expect(splitFilamentTypes(' PLA ,, PETG, PLA,')).toEqual(['PLA', 'PETG']);
  });

  it('returns nothing for a missing value', () => {
    expect(splitFilamentTypes(null)).toEqual([]);
    expect(splitFilamentTypes(undefined)).toEqual([]);
    expect(splitFilamentTypes('')).toEqual([]);
  });
});
