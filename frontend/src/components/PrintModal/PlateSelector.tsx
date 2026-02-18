import { Layers, Check, AlertTriangle } from 'lucide-react';
import type { PlateSelectorProps } from './types';
import { formatDuration } from '../../utils/date';

/**
 * Plate selection grid for multi-plate 3MF files.
 * Shows thumbnails, names, objects, and print times for each plate.
 */
export function PlateSelector({
  plates,
  isMultiPlate,
  selectedPlate,
  onSelect,
}: PlateSelectorProps) {
  // Only show for multi-plate files with multiple plates
  if (!isMultiPlate || plates.length <= 1) {
    return null;
  }

  return (
    <div className="mb-4">
      <div className="flex items-center gap-2 mb-2">
        <Layers className="w-4 h-4 text-bambu-gray" />
        <span className="text-sm text-bambu-gray">Select Plate to Print</span>
        {!selectedPlate && (
          <span className="text-xs text-orange-400 flex items-center gap-1">
            <AlertTriangle className="w-3 h-3" />
            Selection required
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-2">
        {plates.map((plate) => (
          <button
            key={plate.index}
            type="button"
            onClick={() => onSelect(plate.index)}
            className={`flex items-center gap-2 p-2 rounded-lg border transition-colors text-left ${
              selectedPlate === plate.index
                ? 'border-bambu-green bg-bambu-green/10'
                : 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray'
            }`}
          >
            {plate.has_thumbnail && plate.thumbnail_url != null ? (
              <img
                src={plate.thumbnail_url}
                alt={`Plate ${plate.index}`}
                className="w-10 h-10 rounded object-cover bg-bambu-dark-tertiary"
              />
            ) : (
              <div className="w-10 h-10 rounded bg-bambu-dark-tertiary flex items-center justify-center">
                <Layers className="w-5 h-5 text-bambu-gray" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm text-white font-medium truncate">
                {plate.name || `Plate ${plate.index}`}
              </p>
              <p className="text-xs text-bambu-gray truncate">
                {plate.objects.length > 0
                  ? plate.objects.slice(0, 3).join(', ') +
                    (plate.objects.length > 3 ? '...' : '')
                  : `${plate.filaments.length} filament${plate.filaments.length !== 1 ? 's' : ''}`}
                {plate.print_time_seconds != null ? ` • ${formatDuration(plate.print_time_seconds)}` : ''}
              </p>
            </div>
            {selectedPlate === plate.index && (
              <Check className="w-4 h-4 text-bambu-green flex-shrink-0" />
            )}
          </button>
        ))}
      </div>
    </div>
  );
}
