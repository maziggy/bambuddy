/**
 * Password complexity check matching the backend rules in
 * `backend/app/schemas/auth.py:_validate_password_complexity` plus the
 * implicit `min_length=8` that most server-side schemas enforce.
 *
 * Returning the FIRST unmet requirement as a translation-key suffix keeps the
 * UI message order identical to what the backend would have returned — the
 * user sees the same rule fail whether the check happens client- or server-
 * side, which avoids the confusion of fixing one issue only to immediately
 * trip another after the round-trip.
 */
export type PasswordRequirementKey =
  | 'tooShort'
  | 'needsUppercase'
  | 'needsLowercase'
  | 'needsDigit'
  | 'needsSpecial';

export function checkPasswordComplexity(password: string): PasswordRequirementKey | null {
  if (password.length < 8) return 'tooShort';
  if (!/[A-Z]/.test(password)) return 'needsUppercase';
  if (!/[a-z]/.test(password)) return 'needsLowercase';
  if (!/\d/.test(password)) return 'needsDigit';
  if (!/[^A-Za-z0-9]/.test(password)) return 'needsSpecial';
  return null;
}
