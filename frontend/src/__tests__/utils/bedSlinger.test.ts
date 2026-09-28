import { describe, it, expect } from 'vitest';
import { isBedSlinger } from '../../utils/bedSlinger';

describe('isBedSlinger', () => {
  it.each([
    'A1',
    'A1 Mini',
    'A1 mini',
    'A1MINI',
    'A1-MINI',
    'a1 mini',
    'A1M',
    'A2L',
    'a2l',
    'N1',
    'N2S',
    'N9',
    'A04',
    'A11',
    'A12',
  ])('classifies %s as a bed-slinger', model => {
    expect(isBedSlinger(model)).toBe(true);
  });

  it.each([
    'X1',
    'X1C',
    'X1E',
    'X2D',
    'P1P',
    'P1S',
    'P2S',
    'H2D',
    'H2D Pro',
    'H2C',
    'H2S',
    'C11',
    'C12',
    'C13',
    'N6',
    'N7',
    'O1D',
    'O1E',
    'O2D',
    'O1C',
    'O1C2',
    'O1S',
  ])('classifies %s as bed-on-Z', model => {
    expect(isBedSlinger(model)).toBe(false);
  });

  it('treats an unknown or missing model as bed-on-Z', () => {
    // Bed-on-Z is what almost the whole fleet is, so it is the right default
    // for a name we do not recognise. It is not a free choice though: nothing
    // clamps a jog (#2579), so a misclassified bed-slinger sends its toolhead
    // at the plate on the first click of "up". Unknown A-series names are the
    // ones to watch when a new machine ships.
    expect(isBedSlinger(null)).toBe(false);
    expect(isBedSlinger(undefined)).toBe(false);
    expect(isBedSlinger('')).toBe(false);
    expect(isBedSlinger('Voron 2.4')).toBe(false);
  });

  it('does not sweep in other A-series names by prefix', () => {
    // The list is explicit on purpose — a prefix match would claim every
    // future "A<something>" before anyone has checked which way its Z goes.
    expect(isBedSlinger('A3')).toBe(false);
    expect(isBedSlinger('A1 Pro')).toBe(false);
  });
});
