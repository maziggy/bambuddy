import {
  Activity,
  AlertTriangle,
  Battery,
  DoorClosed,
  DoorOpen,
  Droplets,
  Gauge,
  Lock,
  LockOpen,
  Thermometer,
  Wind,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

/**
 * Display metadata for Home Assistant sensors, shared between the printer
 * and storage-location bindings. Both features read the same device classes
 * off the same kind of entity, so keeping one copy here means a new class
 * only needs adding in one place instead of drifting across every consumer.
 *
 * Merging the printer's map with the location one added `battery` here,
 * which the printer's own map never had — a battery-class printer sensor
 * used to fall back to the generic Gauge icon. It now gets the Battery icon
 * too. Deliberate: nothing about a battery-class reading is printer- or
 * location-specific, and Gauge was an omission in the original map rather
 * than a considered choice, so there's no reason to carve out an exception
 * just to preserve it.
 */

// Home Assistant's own device_class decides the wording, so a door reads
// "Open"/"Closed" rather than the "on"/"off" the API actually carries. Classes
// absent from this map fall through to on/off, which is what HA itself shows
// for a binary_sensor with no class.
export const HA_SENSOR_BINARY_LABELS: Record<string, { on: string; off: string }> = {
  door: { on: 'open', off: 'closed' },
  garage_door: { on: 'open', off: 'closed' },
  window: { on: 'open', off: 'closed' },
  opening: { on: 'open', off: 'closed' },
  lock: { on: 'unlocked', off: 'locked' },
  motion: { on: 'detected', off: 'clear' },
  occupancy: { on: 'detected', off: 'clear' },
  presence: { on: 'detected', off: 'clear' },
  smoke: { on: 'detected', off: 'clear' },
  gas: { on: 'detected', off: 'clear' },
  moisture: { on: 'wet', off: 'dry' },
  problem: { on: 'problem', off: 'ok' },
  safety: { on: 'problem', off: 'ok' },
  running: { on: 'running', off: 'stopped' },
};

export const HA_SENSOR_ICONS: Record<string, LucideIcon> = {
  door: DoorOpen,
  garage_door: DoorOpen,
  window: DoorOpen,
  opening: DoorOpen,
  lock: LockOpen,
  temperature: Thermometer,
  humidity: Droplets,
  moisture: Droplets,
  battery: Battery,
  motion: Activity,
  occupancy: Activity,
  presence: Activity,
  smoke: AlertTriangle,
  gas: AlertTriangle,
  problem: AlertTriangle,
  safety: AlertTriangle,
  running: Wind,
};

interface IconableReading {
  device_class: string | null;
  state: string | null;
  kind: 'binary' | 'numeric';
}

interface DescribableReading {
  device_class: string | null;
  state: string | null;
  kind: 'binary' | 'numeric';
  value: number | null;
  unit: string | null;
  reachable: boolean;
}

// The printer and location features render a reading's value as text the
// same way — unavailable text, binary state label, or a numeric value with
// its unit — and used to each carry their own copy. The one place they
// genuinely differ is decimal places: the printer row shows a sensor's raw
// value (e.g. "23.4"), while location sensor cells fix two decimal places
// so temperature/humidity/battery values line up at a consistent width in a
// table or card grid (e.g. "23.40", "87.00 %") — a deliberate choice, not an
// oversight. `decimals` is how a caller opts into that padding; omit it for
// the printer's raw-value behavior.
export function describeHASensorReading(
  reading: DescribableReading,
  t: (key: string, opts?: Record<string, unknown>) => string,
  options?: { decimals?: number }
): string {
  if (!reading.reachable || reading.state === null) return t('haSensors.unavailable');
  if (reading.kind === 'numeric') {
    if (reading.value === null) return reading.state;
    const formatted = options?.decimals !== undefined ? reading.value.toFixed(options.decimals) : String(reading.value);
    return reading.unit ? `${formatted} ${reading.unit}` : formatted;
  }
  const labels = HA_SENSOR_BINARY_LABELS[reading.device_class ?? ''];
  const key = labels ? labels[reading.state === 'on' ? 'on' : 'off'] : reading.state;
  return t(`haSensors.states.${key}`, { defaultValue: key });
}

// A closed door wants the closed-door glyph — the map is keyed by class, so
// the two states that have a distinct "off" icon are special-cased here.
export function iconForHASensor(reading: IconableReading): LucideIcon {
  const deviceClass = reading.device_class ?? '';
  if (reading.state === 'off') {
    if (HA_SENSOR_ICONS[deviceClass] === DoorOpen) return DoorClosed;
    if (HA_SENSOR_ICONS[deviceClass] === LockOpen) return Lock;
  }
  return HA_SENSOR_ICONS[deviceClass] ?? (reading.kind === 'numeric' ? Gauge : Activity);
}
