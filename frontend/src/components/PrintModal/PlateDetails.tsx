import { CalendarDays, Clock, Layers, Package, Palette } from 'lucide-react';
import { formatTime, normalizeColor } from '../../utils/amsHelpers';
import type { PlateInfo } from './types';

interface PlateDetailsProps {
  plate: PlateInfo | null;
}

export function PlateDetails({ plate }: PlateDetailsProps) {
  if (!plate) return null;

  const printTimeLabel = plate.print_time_seconds != null
    ? formatTime(plate.print_time_seconds)
    : '—';
  const filamentUsedLabel = plate.filament_used_grams != null
    ? `${plate.filament_used_grams.toFixed(1)}g`
    : '—';

  const filamentSummary = plate.filaments.reduce((acc, filament) => {
    const key = `${filament.type}-${filament.color}`;
    const existing = acc.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      acc.set(key, { ...filament, count: 1 });
    }
    return acc;
  }, new Map<string, { type: string; color: string; count: number }>());

  const filamentList = Array.from(filamentSummary.values());

  return (
    <div className="mb-4 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary p-3">
      <div className="flex items-center justify-between mb-3">
        <div>
          <p className="text-xs text-bambu-gray">Selected plate details</p>
          <p className="text-sm text-white font-medium">
            {plate.name || `Plate ${plate.index}`}
          </p>
        </div>
        <span className="text-xs text-bambu-gray">#{plate.index}</span>
      </div>

      <div className="grid grid-cols-2 gap-2 text-xs text-bambu-gray mb-3">
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          <span>Print time: <span className="text-white">{printTimeLabel}</span></span>
        </div>
        <div className="flex items-center gap-1.5">
          <Package className="w-3.5 h-3.5" />
          <span>Filament used: <span className="text-white">{filamentUsedLabel}</span></span>
        </div>
        <div className="flex items-center gap-1.5">
          <Layers className="w-3.5 h-3.5" />
          <span>Objects: <span className="text-white">{plate.objects.length || '—'}</span></span>
        </div>
        <div className="flex items-center gap-1.5">
          <CalendarDays className="w-3.5 h-3.5" />
          <span>Print date: <span className="text-white">Not recorded</span></span>
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-1.5 text-xs text-bambu-gray">
          <Palette className="w-3.5 h-3.5" />
          <span>Filaments</span>
        </div>
        {filamentList.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {filamentList.map((filament) => (
              <div
                key={`${filament.type}-${filament.color}`}
                className="flex items-center gap-1.5 px-2 py-1 rounded-full bg-bambu-dark border border-bambu-dark-tertiary text-xs text-white"
              >
                <span
                  className="w-3 h-3 rounded-full border border-white/20"
                  style={{ backgroundColor: normalizeColor(filament.color) }}
                  title={filament.color}
                />
                <span>{filament.type}</span>
                {filament.count > 1 && (
                  <span className="text-bambu-gray">×{filament.count}</span>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-bambu-gray">No filament details available for this plate.</p>
        )}
      </div>

      {plate.objects.length > 0 && (
        <div className="mt-3">
          <p className="text-xs text-bambu-gray mb-1">Objects</p>
          <p className="text-xs text-white">
            {plate.objects.slice(0, 6).join(', ')}
            {plate.objects.length > 6 ? ` +${plate.objects.length - 6} more` : ''}
          </p>
        </div>
      )}
    </div>
  );
}
