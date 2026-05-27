import { describe, it, expect } from 'vitest';
import { checkPasswordComplexity } from '../../utils/password';

describe('checkPasswordComplexity', () => {
  it('rejects passwords shorter than 8 characters', () => {
    expect(checkPasswordComplexity('Ab1!def')).toBe('tooShort');
    expect(checkPasswordComplexity('')).toBe('tooShort');
  });

  it('flags missing uppercase first (matches backend validator order)', () => {
    // Matches backend/app/schemas/auth.py:_validate_password_complexity which
    // returns the uppercase error before checking lowercase/digit/special.
    expect(checkPasswordComplexity('abcdefgh')).toBe('needsUppercase');
    expect(checkPasswordComplexity('abcdefg1')).toBe('needsUppercase');
    expect(checkPasswordComplexity('abcdefg!')).toBe('needsUppercase');
  });

  it('flags missing lowercase when uppercase is present', () => {
    expect(checkPasswordComplexity('ABCDEFGH')).toBe('needsLowercase');
    expect(checkPasswordComplexity('ABCDEFG1')).toBe('needsLowercase');
  });

  it('flags missing digit when letters are present', () => {
    expect(checkPasswordComplexity('Abcdefgh')).toBe('needsDigit');
    expect(checkPasswordComplexity('Abcdefg!')).toBe('needsDigit');
  });

  it('flags missing special character', () => {
    expect(checkPasswordComplexity('Abcdefg1')).toBe('needsSpecial');
  });

  it('returns null for a password that meets every rule', () => {
    expect(checkPasswordComplexity('Aa1!aaaa')).toBeNull();
    expect(checkPasswordComplexity('Aa1!Aa1!Aa1!')).toBeNull();
  });

  it('handles a password from the #1303 user (8 digits) — the original failure mode', () => {
    // The reporter typed an 8-character all-digits password and the backend
    // returned 422 "Password must contain at least one uppercase letter".
    // The FE check now produces the same verdict locally without a round-trip.
    expect(checkPasswordComplexity('12345678')).toBe('needsUppercase');
  });
});
