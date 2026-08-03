/**
 * Fleet-size audience split for the sponsor surfaces.
 *
 * The threshold decides which pitch a user sees, so it is worth pinning: a
 * hobbyist must never be asked to buy a support contract, and a print farm must
 * never be asked to chip in $5.
 */
import { describe, it, expect } from 'vitest';
import {
  BUSINESS_FLEET_THRESHOLD,
  fleetAudience,
  sponsorHref,
} from '../../utils/fleetAudience';

describe('fleetAudience', () => {
  it('treats a fleet below the threshold as personal', () => {
    expect(fleetAudience(0)).toBe('personal');
    expect(fleetAudience(1)).toBe('personal');
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD - 1)).toBe('personal');
  });

  it('treats the threshold itself as business (inclusive boundary)', () => {
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD)).toBe('business');
    expect(fleetAudience(BUSINESS_FLEET_THRESHOLD + 1)).toBe('business');
    expect(fleetAudience(40)).toBe('business');
  });
});

describe('sponsorHref', () => {
  it('sends a personal audience to the sponsor tiers', () => {
    expect(sponsorHref('personal', 'app-settings')).toBe(
      'https://bambuddy.cool/sponsors.html?from=app-settings',
    );
  });

  it('sends a business audience to the commercial page', () => {
    expect(sponsorHref('business', 'app-settings')).toBe(
      'https://bambuddy.cool/business.html?from=app-settings',
    );
  });

  it('preserves the Matomo attribution param on both', () => {
    // The `?from=` tag is how the funnel is measured — losing it would make the
    // whole surface invisible in analytics.
    expect(sponsorHref('personal', 'app-toast-prints-10')).toContain('?from=app-toast-prints-10');
    expect(sponsorHref('business', 'app-toast-prints-10')).toContain('?from=app-toast-prints-10');
  });
});
