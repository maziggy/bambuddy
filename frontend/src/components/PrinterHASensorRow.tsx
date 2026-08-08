import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  AlertTriangle,
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
import { useTranslation } from 'react-i18next';

import { api } from '../api/client';
import type { PrinterHASensorReading } from '../api/client';

/**
 * The Home Assistant sensors bound to a printer, on its card (#1148, #448).
 *
 * Read-only by design — these are contacts and thermometers, not switches, so
 * nothing here is clickable. The sibling "HA:" row above handles the entities
 * you can actually operate.
 */

// Home Assistant's own device_class decides the wording, so a door reads
// "Open"/"Closed" rather than the "on"/"off" the API actually carries. Classes
// absent from this map fall through to on/off, which is what HA itself shows
// for a binary_sensor with no class.
const BINARY_LABELS: Record<string, { on: string; off: string }> = {
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

const ICONS: Record<string, LucideIcon> = {
  door: DoorOpen,
  garage_door: DoorOpen,
  window: DoorOpen,
  opening: DoorOpen,
  lock: LockOpen,
  temperature: Thermometer,
  humidity: Droplets,
  moisture: Droplets,
  motion: Activity,
  occupancy: Activity,
  presence: Activity,
  smoke: AlertTriangle,
  gas: AlertTriangle,
  problem: AlertTriangle,
  safety: AlertTriangle,
  running: Wind,
};

function iconFor(reading: PrinterHASensorReading): LucideIcon {
  const deviceClass = reading.device_class ?? '';
  // A closed door wants the closed-door glyph — the map is keyed by class, so
  // the two states that have a distinct "off" icon are special-cased here.
  if (reading.state === 'off') {
    if (ICONS[deviceClass] === DoorOpen) return DoorClosed;
    if (ICONS[deviceClass] === LockOpen) return Lock;
  }
  return ICONS[deviceClass] ?? (reading.kind === 'numeric' ? Gauge : Activity);
}

interface Props {
  printerId: number;
}

export function PrinterHASensorRow({ printerId }: Props) {
  const { t } = useTranslation();

  const { data: readings } = useQuery({
    queryKey: ['haSensorReadings', printerId],
    queryFn: () => api.getHASensorReadings(printerId),
    // Served from the backend poller's cache, so this costs a local request
    // and never a Home Assistant round trip. Matched to the poller's own
    // cadence — refetching faster would only re-read the same reading.
    refetchInterval: 15000,
  });

  if (!readings?.length) return null;

  const describe = (reading: PrinterHASensorReading): string => {
    if (!reading.reachable || reading.state === null) return t('haSensors.unavailable');
    if (reading.kind === 'numeric') {
      if (reading.value === null) return reading.state;
      return reading.unit ? `${reading.value} ${reading.unit}` : String(reading.value);
    }
    const labels = BINARY_LABELS[reading.device_class ?? ''];
    const key = labels ? labels[reading.state === 'on' ? 'on' : 'off'] : reading.state;
    return t(`haSensors.states.${key}`, { defaultValue: key });
  };

  return (
    <div className="flex items-center gap-2 mt-2">
      <Gauge className="w-[var(--pc-i35,0.875rem)] h-[var(--pc-i35,0.875rem)] text-blue-600 dark:text-blue-400 flex-shrink-0" />
      <span className="text-xs text-bambu-gray">{t('haSensors.label')}</span>
      <div className="h-[2px] w-5 bg-bambu-dark-tertiary/50" />
      <div className="flex flex-wrap gap-1">
        {readings.map((reading) => {
          const Icon = iconFor(reading);
          const unreachable = !reading.reachable || reading.state === null;
          return (
            <span
              key={reading.id}
              title={
                reading.block_print
                  ? t('haSensors.blocksPrints', { entity: reading.entity_id })
                  : reading.entity_id
              }
              className={`px-2 py-0.5 text-xs rounded flex items-center gap-1 ${
                unreachable
                  ? 'bg-bambu-dark-tertiary/50 text-bambu-gray'
                  : reading.alerting
                    ? 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400'
                    : 'bg-bambu-dark-tertiary text-bambu-gray'
              }`}
            >
              <Icon className="w-[var(--pc-i25,0.625rem)] h-[var(--pc-i25,0.625rem)]" />
              <span>{reading.name}</span>
              <span className="font-medium">{describe(reading)}</span>
            </span>
          );
        })}
      </div>
    </div>
  );
}
