import type { FocusEvent } from 'react';

/** How the backend shows a saved secret. Sent back unchanged, it keeps the secret. */
export const SECRET_PLACEHOLDER = '********';

/** onFocus for a secret input: typing replaces the placeholder instead of adding to it. */
export function selectPlaceholderOnFocus(e: FocusEvent<HTMLInputElement | HTMLTextAreaElement>) {
  if (e.target.value === SECRET_PLACEHOLDER) e.target.select();
}

/**
 * Secrets still shown as the placeholder whose destination changed. The
 * backend refuses to send a saved secret to a new destination, so these must
 * be entered again before saving.
 */
export function secretsToReenter(
  baseline: object,
  current: object,
  destinations: Record<string, readonly string[]>,
): string[] {
  const before = baseline as Record<string, unknown>;
  const now = current as Record<string, unknown>;
  return Object.entries(destinations)
    .filter(
      ([secret, fields]) =>
        now[secret] === SECRET_PLACEHOLDER &&
        fields.some((field) => String(before[field] ?? '') !== String(now[field] ?? '')),
    )
    .map(([secret]) => secret);
}
