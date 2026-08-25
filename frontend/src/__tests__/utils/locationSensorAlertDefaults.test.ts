/**
 * The per-category alert defaults are server-backed (#2824 review round 4).
 *
 * They seed the alert rule written onto each sensor row when one is bound, so
 * two admins binding sensors from different browsers must not seed different
 * rules, and a backup/restore has to carry them. Only `showOnCard` stays in
 * localStorage: show_on_card is decided per sensor and this is nothing more
 * than the form's pre-selection.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  defaultLocationSensorDefaults,
  loadLocationSensorDefaults,
  saveLocationSensorShowOnCardDefaults,
  serializeLocationSensorAlertDefaults,
} from '../../utils/locationSensorDefaults';

beforeEach(() => {
  vi.mocked(localStorage.getItem).mockReset();
  vi.mocked(localStorage.setItem).mockReset();
});

describe('location sensor alert defaults — server-backed', () => {
  it('falls back to the built-ins when the setting is empty', () => {
    expect(loadLocationSensorDefaults('')).toEqual(defaultLocationSensorDefaults());
    expect(loadLocationSensorDefaults(undefined)).toEqual(defaultLocationSensorDefaults());
    expect(loadLocationSensorDefaults(null)).toEqual(defaultLocationSensorDefaults());
  });

  it('takes the alert fields from the server value', () => {
    const json = JSON.stringify({
      humidity: { alertAbove: '45', alertBelow: '15', notifyOnAlert: true },
    });

    const defaults = loadLocationSensorDefaults(json);

    expect(defaults.humidity.alertAbove).toBe('45');
    expect(defaults.humidity.alertBelow).toBe('15');
    expect(defaults.humidity.notifyOnAlert).toBe(true);
    // Categories absent from the setting keep their built-ins.
    expect(defaults.temperature).toEqual(defaultLocationSensorDefaults().temperature);
  });

  it('round-trips through serialize', () => {
    const original = defaultLocationSensorDefaults();
    original.temperature.alertAbove = '35';
    original.battery.notifyOnAlert = true;

    const reloaded = loadLocationSensorDefaults(serializeLocationSensorAlertDefaults(original));

    expect(reloaded.temperature.alertAbove).toBe('35');
    expect(reloaded.battery.notifyOnAlert).toBe(true);
  });

  it('never puts showOnCard in the server value — it is per sensor, not per installation', () => {
    const defaults = defaultLocationSensorDefaults();
    defaults.humidity.showOnCard = false;

    const parsed = JSON.parse(serializeLocationSensorAlertDefaults(defaults));

    expect(parsed.humidity).not.toHaveProperty('showOnCard');
    expect(Object.keys(parsed.humidity).sort()).toEqual(['alertAbove', 'alertBelow', 'notifyOnAlert']);
  });

  // setup.ts replaces localStorage with bare vi.fn() stubs that store nothing,
  // so these drive it explicitly rather than round-tripping through it.
  it('writes only the per-category booleans to localStorage', () => {
    const defaults = defaultLocationSensorDefaults();
    defaults.humidity.showOnCard = false;

    saveLocationSensorShowOnCardDefaults(defaults);

    expect(localStorage.setItem).toHaveBeenCalledWith(
      'bambuddy-location-sensor-show-on-card-defaults',
      JSON.stringify({ temperature: true, humidity: false, battery: true })
    );
  });

  it('applies the stored showOnCard over the built-in, independent of the server value', () => {
    vi.mocked(localStorage.getItem).mockReturnValue(JSON.stringify({ humidity: false }));

    const reloaded = loadLocationSensorDefaults(
      serializeLocationSensorAlertDefaults(defaultLocationSensorDefaults())
    );

    expect(reloaded.humidity.showOnCard).toBe(false);
    expect(reloaded.temperature.showOnCard).toBe(true);
  });

  it('survives a corrupted setting instead of blocking the dialog', () => {
    expect(loadLocationSensorDefaults('not json at all')).toEqual(defaultLocationSensorDefaults());
  });

  it('ignores unexpected keys and wrong types in the stored value', () => {
    const json = JSON.stringify({
      humidity: { alertAbove: 45, notifyOnAlert: 'yes', showOnCard: false, injected: 'x' },
    });

    const defaults = loadLocationSensorDefaults(json);

    // Wrong types are rejected, built-ins survive.
    expect(defaults.humidity.alertAbove).toBe(defaultLocationSensorDefaults().humidity.alertAbove);
    expect(defaults.humidity.notifyOnAlert).toBe(false);
    // showOnCard from the server value must not win over the local preference.
    expect(defaults.humidity.showOnCard).toBe(true);
    expect(defaults.humidity).not.toHaveProperty('injected');
  });
});
