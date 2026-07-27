/**
 * Fleet-size audience split for the sponsor surfaces.
 *
 * An install with a large fleet is almost certainly a business (print farm,
 * workshop, makerspace), and a business has no use for a "chip in $5" ask — it
 * wants a support contract, an invoice and a named contact. The sponsor toast
 * and the Settings banner therefore swap their copy and CTA above this
 * threshold instead of showing the personal ask.
 *
 * Counts CONFIGURED printers, not active ones: `is_active` is the
 * maintenance-mode flag, so a farm with half its machines on the bench must not
 * flicker back to the hobbyist pitch.
 */
export const BUSINESS_FLEET_THRESHOLD = 5;

export type SponsorAudience = 'personal' | 'business';

export function fleetAudience(printerCount: number): SponsorAudience {
  return printerCount >= BUSINESS_FLEET_THRESHOLD ? 'business' : 'personal';
}

/** Landing page for each audience, carrying the Matomo `?from=` attribution. */
export function sponsorHref(audience: SponsorAudience, from: string): string {
  const page = audience === 'business' ? 'business.html' : 'sponsors.html';
  return `https://bambuddy.cool/${page}?from=${from}`;
}
