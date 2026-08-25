import { useEffect, useState } from 'react';
import type { LocationHASensorReading } from '../api/client';

export type LocationSensorCategory = 'temperature' | 'humidity' | 'battery';

export interface LocationSensorCategoryDefaults {
  alertAbove: string;
  alertBelow: string;
  notifyOnAlert: boolean;
  showOnCard: boolean;
}

export type LocationSensorDefaults = Record<LocationSensorCategory, LocationSensorCategoryDefaults>;

// The alert fields (alertAbove/alertBelow/notifyOnAlert) live on the server in
// the `location_sensor_alert_defaults` setting, not here: they seed the alert
// rule written onto each sensor row, so two admins binding sensors from
// different browsers must not seed different rules, and a backup has to carry
// them. `showOnCard` stays per-browser — show_on_card is decided per sensor and
// this is only the form's pre-selection, not a rule the installation runs on.
const SHOW_ON_CARD_STORAGE_KEY = 'bambuddy-location-sensor-show-on-card-defaults';

const EMPTY_CATEGORY_DEFAULTS: LocationSensorCategoryDefaults = {
  alertAbove: '',
  alertBelow: '',
  notifyOnAlert: false,
  showOnCard: true,
};

export function defaultLocationSensorDefaults(): LocationSensorDefaults {
  return {
    temperature: { ...EMPTY_CATEGORY_DEFAULTS, alertAbove: '30', alertBelow: '20' },
    humidity: { ...EMPTY_CATEGORY_DEFAULTS, alertAbove: '30', alertBelow: '10' },
    battery: { ...EMPTY_CATEGORY_DEFAULTS, alertBelow: '10' },
  };
}

// Only the three alert fields are read off the server value; anything else in
// the stored JSON is ignored so a hand-edited setting cannot inject keys.
type StoredAlertDefaults = Partial<Record<LocationSensorCategory, Partial<LocationSensorCategoryDefaults>>>;

function readShowOnCardDefaults(): Partial<Record<LocationSensorCategory, boolean>> {
  try {
    const stored = localStorage.getItem(SHOW_ON_CARD_STORAGE_KEY);
    return stored ? (JSON.parse(stored) as Partial<Record<LocationSensorCategory, boolean>>) : {};
  } catch {
    return {};
  }
}

/**
 * Merge built-in defaults, the server's alert defaults and the local
 * show-on-card preference into one shape for the forms.
 *
 * Pass the `location_sensor_alert_defaults` string from the settings query.
 * Omitting it (or passing an empty string) yields the built-in defaults, which
 * is exactly what an installation that has never opened the options dialog
 * gets — no migration needed.
 */
export function loadLocationSensorDefaults(alertDefaultsJson?: string | null): LocationSensorDefaults {
  const defaults = defaultLocationSensorDefaults();

  if (alertDefaultsJson) {
    try {
      const parsed = JSON.parse(alertDefaultsJson) as StoredAlertDefaults;
      (Object.keys(defaults) as LocationSensorCategory[]).forEach((category) => {
        const stored = parsed[category];
        if (!stored) return;
        if (typeof stored.alertAbove === 'string') defaults[category].alertAbove = stored.alertAbove;
        if (typeof stored.alertBelow === 'string') defaults[category].alertBelow = stored.alertBelow;
        if (typeof stored.notifyOnAlert === 'boolean') defaults[category].notifyOnAlert = stored.notifyOnAlert;
      });
    } catch {
      // A corrupted setting falls back to the built-ins rather than blocking
      // the dialog — same posture as the localStorage readers below.
    }
  }

  const showOnCard = readShowOnCardDefaults();
  (Object.keys(defaults) as LocationSensorCategory[]).forEach((category) => {
    if (typeof showOnCard[category] === 'boolean') defaults[category].showOnCard = showOnCard[category];
  });

  return defaults;
}

/** The value to PATCH into `location_sensor_alert_defaults`. */
export function serializeLocationSensorAlertDefaults(defaults: LocationSensorDefaults): string {
  const out: StoredAlertDefaults = {};
  (Object.keys(defaults) as LocationSensorCategory[]).forEach((category) => {
    out[category] = {
      alertAbove: defaults[category].alertAbove,
      alertBelow: defaults[category].alertBelow,
      notifyOnAlert: defaults[category].notifyOnAlert,
    };
  });
  return JSON.stringify(out);
}

export function saveLocationSensorShowOnCardDefaults(defaults: LocationSensorDefaults) {
  try {
    const out: Partial<Record<LocationSensorCategory, boolean>> = {};
    (Object.keys(defaults) as LocationSensorCategory[]).forEach((category) => {
      out[category] = defaults[category].showOnCard;
    });
    localStorage.setItem(SHOW_ON_CARD_STORAGE_KEY, JSON.stringify(out));
  } catch {
    return;
  }
}

const COLORIZE_VALUES_STORAGE_KEY = 'bambuddy-location-sensor-colorize-values';

export function loadLocationSensorColorizeValues(): boolean {
  try {
    const stored = localStorage.getItem(COLORIZE_VALUES_STORAGE_KEY);
    return stored ? stored === 'true' : true;
  } catch {
    return true;
  }
}

// A plain localStorage.setItem never fires the browser's own 'storage'
// event in the tab that made the change (only other tabs get that), and
// these four values are read by an unknown number of already-mounted
// components (the Inventory page, and one SpoolLocationFooter per card).
// This lets useLocationSensorColorPrefs below re-read after a save instead
// of every reader needing its own poll or the page needing a reload.
const COLOR_PREFS_CHANGED_EVENT = 'bambuddy:location-sensor-color-prefs-changed';

function notifyLocationSensorColorPrefsChanged() {
  window.dispatchEvent(new Event(COLOR_PREFS_CHANGED_EVENT));
}

export function saveLocationSensorColorizeValues(value: boolean) {
  try {
    localStorage.setItem(COLORIZE_VALUES_STORAGE_KEY, String(value));
  } catch {
    return;
  }
  notifyLocationSensorColorPrefsChanged();
}

export const LOCATION_SENSOR_ALERT_COLORS = ['red', 'orange', 'yellow', 'green', 'blue', 'purple', 'pink'] as const;
export type LocationSensorAlertColor = (typeof LOCATION_SENSOR_ALERT_COLORS)[number];

export const LOCATION_SENSOR_ALERT_COLOR_CLASSES: Record<LocationSensorAlertColor, string> = {
  red: 'text-red-400',
  orange: 'text-orange-400',
  yellow: 'text-yellow-400',
  green: 'text-green-400',
  blue: 'text-blue-400',
  purple: 'text-purple-400',
  pink: 'text-pink-400',
};

const ALERT_ABOVE_COLOR_STORAGE_KEY = 'bambuddy-location-sensor-alert-above-color';
const ALERT_BELOW_COLOR_STORAGE_KEY = 'bambuddy-location-sensor-alert-below-color';
const ALERT_OPTIMAL_COLOR_STORAGE_KEY = 'bambuddy-location-sensor-alert-optimal-color';

function isAlertColor(value: string | null): value is LocationSensorAlertColor {
  return !!value && (LOCATION_SENSOR_ALERT_COLORS as readonly string[]).includes(value);
}

export function loadLocationSensorAlertAboveColor(): LocationSensorAlertColor {
  try {
    const stored = localStorage.getItem(ALERT_ABOVE_COLOR_STORAGE_KEY);
    return isAlertColor(stored) ? stored : 'purple';
  } catch {
    return 'purple';
  }
}

export function saveLocationSensorAlertAboveColor(color: LocationSensorAlertColor) {
  try {
    localStorage.setItem(ALERT_ABOVE_COLOR_STORAGE_KEY, color);
  } catch {
    return;
  }
  notifyLocationSensorColorPrefsChanged();
}

export function loadLocationSensorAlertBelowColor(): LocationSensorAlertColor {
  try {
    const stored = localStorage.getItem(ALERT_BELOW_COLOR_STORAGE_KEY);
    return isAlertColor(stored) ? stored : 'red';
  } catch {
    return 'red';
  }
}

export function saveLocationSensorAlertBelowColor(color: LocationSensorAlertColor) {
  try {
    localStorage.setItem(ALERT_BELOW_COLOR_STORAGE_KEY, color);
  } catch {
    return;
  }
  notifyLocationSensorColorPrefsChanged();
}

export function loadLocationSensorAlertOptimalColor(): LocationSensorAlertColor {
  try {
    const stored = localStorage.getItem(ALERT_OPTIMAL_COLOR_STORAGE_KEY);
    return isAlertColor(stored) ? stored : 'green';
  } catch {
    return 'green';
  }
}

export function saveLocationSensorAlertOptimalColor(color: LocationSensorAlertColor) {
  try {
    localStorage.setItem(ALERT_OPTIMAL_COLOR_STORAGE_KEY, color);
  } catch {
    return;
  }
  notifyLocationSensorColorPrefsChanged();
}

export interface LocationSensorColorPrefs {
  colorize: boolean;
  aboveColor: LocationSensorAlertColor;
  belowColor: LocationSensorAlertColor;
  optimalColor: LocationSensorAlertColor;
}

function readLocationSensorColorPrefs(): LocationSensorColorPrefs {
  return {
    colorize: loadLocationSensorColorizeValues(),
    aboveColor: loadLocationSensorAlertAboveColor(),
    belowColor: loadLocationSensorAlertBelowColor(),
    optimalColor: loadLocationSensorAlertOptimalColor(),
  };
}

// Reads the four location-sensor colour preferences once and stays live:
// a Settings save dispatches COLOR_PREFS_CHANGED_EVENT, and every mounted
// caller of this hook (the Inventory page, previously also every
// SpoolLocationFooter individually) picks it up without a reload. Callers
// should read these values here and pass them down as props rather than
// each calling this hook themselves — one subscription per page, not one
// per card.
export function useLocationSensorColorPrefs(): LocationSensorColorPrefs {
  const [prefs, setPrefs] = useState<LocationSensorColorPrefs>(readLocationSensorColorPrefs);

  useEffect(() => {
    const onChange = () => setPrefs(readLocationSensorColorPrefs());
    window.addEventListener(COLOR_PREFS_CHANGED_EVENT, onChange);
    return () => window.removeEventListener(COLOR_PREFS_CHANGED_EVENT, onChange);
  }, []);

  return prefs;
}

export type LocationSensorAlertStatus = 'above' | 'below' | 'ok' | null;

export function locationSensorReadingAlertStatus(reading: LocationHASensorReading): LocationSensorAlertStatus {
  if (!reading.reachable || reading.state === null) return null;
  if (reading.kind === 'numeric') {
    if (reading.alert_above === null && reading.alert_below === null) return null;
    if (reading.value === null) return null;
    if (reading.alert_above !== null && reading.value > reading.alert_above) return 'above';
    if (reading.alert_below !== null && reading.value < reading.alert_below) return 'below';
    return 'ok';
  }
  if (reading.alert_state === null) return null;
  return reading.state.toLowerCase() === reading.alert_state ? 'above' : 'ok';
}

export function locationSensorValueColorClass(
  status: LocationSensorAlertStatus,
  aboveColor: LocationSensorAlertColor,
  belowColor: LocationSensorAlertColor,
  optimalColor: LocationSensorAlertColor
): string {
  if (status === 'above') return LOCATION_SENSOR_ALERT_COLOR_CLASSES[aboveColor];
  if (status === 'below') return LOCATION_SENSOR_ALERT_COLOR_CLASSES[belowColor];
  if (status === 'ok') return LOCATION_SENSOR_ALERT_COLOR_CLASSES[optimalColor];
  return '';
}
