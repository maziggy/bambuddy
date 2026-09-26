import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import type { SpoolPickerPrinter, SpoolPickerSlot } from '../utils/sliceSpoolPicker';

interface SliceSpoolPickerProps {
  printers: SpoolPickerPrinter[];
  loading: boolean;
  failed: boolean;
  onPick: (slot: SpoolPickerSlot) => void;
  onClose: () => void;
}

export function SliceSpoolPicker({
  printers,
  loading,
  failed,
  onPick,
  onClose,
}: SliceSpoolPickerProps) {
  const { t } = useTranslation();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="absolute inset-0 z-20 flex items-start justify-center overflow-y-auto bg-black/55 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('slice.spoolPicker.title')}
        className="my-auto w-full max-w-5xl rounded-lg border border-bambu-dark-tertiary bg-bambu-dark-secondary shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-bambu-dark-tertiary/50 px-4 py-3">
          <h4 className="text-sm font-medium text-white">{t('slice.spoolPicker.title')}</h4>
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-bambu-gray hover:text-white"
          >
            {t('slice.spoolPicker.close')}
          </button>
        </div>
        <div className="max-h-[60vh] space-y-4 overflow-y-auto px-4 py-3">
          {loading && (
            <p className="text-sm text-bambu-gray">{t('slice.spoolPicker.loading')}</p>
          )}
          {!loading && failed && (
            <p className="text-sm text-bambu-gray">{t('slice.spoolPicker.unavailable')}</p>
          )}
          {!loading && !failed && printers.length === 0 && (
            <p className="text-sm text-bambu-gray">{t('slice.spoolPicker.noneOnline')}</p>
          )}
          {!loading && !failed && printers.map((printer) => (
            <section key={printer.id} className="space-y-2">
              <h5 className="text-xs font-medium uppercase tracking-wide text-bambu-gray">
                {printer.name}
              </h5>
              {printer.units.length === 0 && (
                <p className="text-xs text-bambu-gray/70">{t('slice.spoolPicker.noAms')}</p>
              )}
              {printer.units.map((unit) => (
                <div key={unit.key} className="space-y-1.5">
                  <p className="text-xs text-white">{unit.title}</p>
                  <div
                    className="grid gap-1.5"
                    style={{ gridTemplateColumns: `repeat(${unit.columns}, minmax(0, 1fr))` }}
                  >
                    {unit.slots.map((slot) => (
                      <SlotCard key={slot.key} slot={slot} onPick={onPick} />
                    ))}
                  </div>
                </div>
              ))}
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}

function SlotCard({
  slot,
  onPick,
}: {
  slot: SpoolPickerSlot;
  onPick: (slot: SpoolPickerSlot) => void;
}) {
  const { t } = useTranslation();
  if (slot.empty) {
    return (
      <div className="rounded-md border border-dashed border-bambu-dark-tertiary/70 px-2 py-2 text-center">
        <span className="block text-[10px] uppercase tracking-wide text-bambu-gray/50">{slot.label}</span>
        <span className="mt-2 block text-xs text-bambu-gray/50">{t('slice.spoolPicker.empty')}</span>
      </div>
    );
  }
  const selectable = slot.preset != null;
  return (
    <button
      type="button"
      disabled={!selectable}
      title={selectable ? undefined : t('slice.spoolPicker.noProfile')}
      onClick={() => onPick(slot)}
      className={`rounded-md border px-2 py-1.5 text-left ${
        selectable
          ? 'border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-green'
          : 'cursor-not-allowed border-bambu-dark-tertiary/60 bg-bambu-dark/40 opacity-60'
      }`}
    >
      <span
        className="mb-1.5 block h-6 rounded-sm border border-black/20"
        style={{ backgroundColor: slot.color ?? '#3a3a3a' }}
        aria-hidden
      />
      <span className="block text-[10px] uppercase tracking-wide text-bambu-gray">{slot.label}</span>
      <span className="block truncate text-xs font-medium text-white">{slot.material}</span>
      {slot.profileName && (
        <span className="block truncate text-[11px] text-white/80">{slot.profileName}</span>
      )}
      {slot.brand && (
        <span className="block truncate text-[11px] text-bambu-gray">{slot.brand}</span>
      )}
    </button>
  );
}
