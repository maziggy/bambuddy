import { describe, it, expect } from 'vitest';
import { getUnmetPasswordRequirements } from '../../utils/password';

describe('getUnmetPasswordRequirements', () => {
  it.each([
    ['', ['tooShort', 'needsUppercase', 'needsLowercase', 'needsDigit', 'needsSpecial']],
    ['Ab1!def', ['tooShort']],
    ['abcdefgh', ['needsUppercase', 'needsDigit', 'needsSpecial']],
    ['abcdefg1', ['needsUppercase', 'needsSpecial']],
    ['abcdefg!', ['needsUppercase', 'needsDigit']],
    ['ABCDEFGH', ['needsLowercase', 'needsDigit', 'needsSpecial']],
    ['ABCDEFG1', ['needsLowercase', 'needsSpecial']],
    ['Abcdefgh', ['needsDigit', 'needsSpecial']],
    ['Abcdefg!', ['needsDigit']],
    ['Abcdefg1', ['needsSpecial']],
    ['Aa1!aaaa', []],
    ['Aa1!Aa1!Aa1!', []],
    ['12345678', ['needsUppercase', 'needsLowercase', 'needsSpecial']],
  ])('lists every missing requirement in validation order for %j', (password, requirements) => {
    expect(getUnmetPasswordRequirements(password)).toEqual(requirements);
  });
});
