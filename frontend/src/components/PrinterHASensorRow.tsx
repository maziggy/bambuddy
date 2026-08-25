import { useQuery } from '@tanstack/react-query';
import { Gauge } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { api } from '../api/client';
import type { PrinterHASensorReading } from '../api/client';
import { describeHASensorReading, iconForHASensor } from '../utils/haSensorDisplay';

/**
 * The Home Assistant sensors bound to a printer, on its card (#1148, #448).
 *
 * Read-only by design — these are contacts and thermometers, not switches, so
 * nothing here is clickable. The sibling "HA:" row above handles the entities
 * you can actually operate.
 */

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

  const describe = (reading: PrinterHASensorReading): string => describeHASensorReading(reading, t);

  return (
    <div className="flex items-center gap-2 mt-2">
      <Gauge className="w-[var(--pc-i35,0.875rem)] h-[var(--pc-i35,0.875rem)] text-blue-600 dark:text-blue-400 flex-shrink-0" />
      <span className="text-xs text-bambu-gray">{t('haSensors.label')}</span>
      <div className="h-[2px] w-5 bg-bambu-dark-tertiary/50" />
      <div className="flex flex-wrap gap-1">
        {readings.map((reading) => {
          const Icon = iconForHASensor(reading);
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
