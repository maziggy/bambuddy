import { describe, it, expect } from 'vitest';
import { mapModelCode } from '../../utils/printerModel';

describe('mapModelCode', () => {
  // A real P1P 3MF carries C11 next to "Bambu Lab P1P"; the X1 Carbon's code
  // is BL-P001. The C-codes used to be shifted onto the wrong printers, and a
  // discovered printer is saved under the mapped name.
  it.each([
    ['BL-P001', 'X1C'],
    ['BL-P002', 'X1'],
    ['C13', 'X1E'],
    ['C11', 'P1P'],
    ['C12', 'P1S'],
    ['N7', 'P2S'],
    ['N6', 'X2D'],
    ['O1D', 'H2D'],
    ['N2S', 'A1'],
    ['N1', 'A1 Mini'],
    ['A11', 'A1'],
    ['A12', 'A1 Mini'],
    ['A04', 'A1 Mini'],
  ])('maps %s to %s', (code, name) => {
    expect(mapModelCode(code)).toBe(name);
  });

  it('keeps display names and unknown values as they are', () => {
    expect(mapModelCode('P1S')).toBe('P1S');
    expect(mapModelCode('Future printer')).toBe('Future printer');
  });

  it('returns an empty string for a missing model', () => {
    expect(mapModelCode(null)).toBe('');
    expect(mapModelCode('')).toBe('');
  });
});
