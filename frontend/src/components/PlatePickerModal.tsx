import { Layers, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { PlateMetadata } from '../types/plates';
import { withStreamToken } from '../api/client';
import { formatDuration } from '../utils/date';

interface PlatePickerModalProps {
  plates: PlateMetadata[];
  onSelect: (plateIndex: number) => void;
  onClose: () => void;
}

export function PlatePickerModal({ plates, onSelect, onClose }: PlatePickerModalProps) {
  const { t } = useTranslation();
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary/60"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-bambu-dark-tertiary/40">
          <div className="min-w-0">
            <h3 className="text-white font-medium">{t('archives.platePicker.title')}</h3>
            <p className="text-xs text-bambu-gray mt-1">{t('archives.platePicker.hint')}</p>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        {/* Grid */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {plates.map((plate) => (
              <button
                key={plate.index}
                type="button"
                onClick={() => onSelect(plate.index)}
                className="flex items-center gap-2 p-2 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-gray transition-colors text-left"
              >
                {plate.has_thumbnail && plate.thumbnail_url != null ? (
                  <img
                    src={withStreamToken(plate.thumbnail_url)}
                    alt={`Plate ${plate.index}`}
                    className="w-12 h-12 rounded object-cover bg-bambu-dark-tertiary flex-shrink-0"
                  />
                ) : (
                  <div className="w-12 h-12 rounded bg-bambu-dark-tertiary flex items-center justify-center flex-shrink-0">
                    <Layers className="w-5 h-5 text-bambu-gray" />
                  </div>
                )}
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-white font-medium truncate">
                    {plate.name
                      ? `${t('archives.platePicker.plateLabel', { index: plate.index })} — ${plate.name}`
                      : t('archives.platePicker.plateLabel', { index: plate.index })}
                  </p>
                  <p className="text-xs text-bambu-gray truncate">
                    {plate.objects.length > 0
                      ? plate.objects.slice(0, 3).join(', ') +
                        (plate.objects.length > 3 ? '…' : '')
                      : plate.object_count != null && plate.object_count > 0
                      ? t('archives.platePicker.objectCount', { count: plate.object_count })
                      : `${plate.filaments.length} filament${plate.filaments.length !== 1 ? 's' : ''}`}
                    {plate.print_time_seconds != null ? ` • ${formatDuration(plate.print_time_seconds)}` : ''}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
