export type DryingStartMode = 'now' | 'delay' | 'at_time';

// Returns the UTC ISO start instant for a drying run, or null for "start now".
// atTime is the raw value of an <input type="datetime-local"> (local timezone).
export function computeStartAfter(
  mode: DryingStartMode,
  delayMinutes: number,
  atTime: string,
  now: Date = new Date(),
): string | null {
  if (mode === 'now') return null;
  if (mode === 'delay') return new Date(now.getTime() + delayMinutes * 60_000).toISOString();
  return new Date(atTime).toISOString();
}
