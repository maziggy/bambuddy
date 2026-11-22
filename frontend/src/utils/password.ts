/**
 * Password complexity check matching the backend rules in
 * `backend/app/schemas/auth.py:_validate_password_complexity` plus the
 * implicit `min_length=8` that most server-side schemas enforce.
 *
 * List unmet requirements in the same order as the backend validator.
 */
export type PasswordRequirementKey =
  | 'tooShort'
  | 'needsUppercase'
  | 'needsLowercase'
  | 'needsDigit'
  | 'needsSpecial';

export function getUnmetPasswordRequirements(password: string): PasswordRequirementKey[] {
  const requirements: PasswordRequirementKey[] = [];
  if (password.length < 8) requirements.push('tooShort');
  if (!/[A-Z]/.test(password)) requirements.push('needsUppercase');
  if (!/[a-z]/.test(password)) requirements.push('needsLowercase');
  if (!/\d/.test(password)) requirements.push('needsDigit');
  if (!/[^A-Za-z0-9]/.test(password)) requirements.push('needsSpecial');
  return requirements;
}
