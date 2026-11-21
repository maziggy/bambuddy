import { describe, it, expect } from 'vitest';
import { colorFamily } from '../../utils/colors';
import shared from './colorFamilyVectors.json';

// The backend names spool colours in notifications with a port of colorFamily
// (backend/app/utils/color_utils.py, color_family). Both sides read this file, so
// a rule changed in one place fails the other's test until it is changed too (#3641).
describe('colorFamily shared vectors', () => {
  it.each(shared.cases)('$hex is $family', ({ hex, family }) => {
    expect(colorFamily(hex)).toBe(family);
  });
});
