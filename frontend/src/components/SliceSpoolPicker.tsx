import { useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { X } from 'lucide-react';
import type { LoadedSpoolPrinter, LoadedSpoolTray, UnifiedPresetsResponse } from '../api/client';
import { FilamentSwatch } from './FilamentSwatch';
import { formatSlotLabel, getAmsLabel, getEmptySlotKind } from '../utils/amsHelpers';
import {
  matchSlotPreset,
  presetDisplayName,
  savedPresetFor,
  trayColourHex,
  type FilamentNameIndex,
  type SlotMatch,
} from '../utils/sliceLoadedSpools';
import type { PrinterCompatibilityIndex } from '../utils/slicerPrinterMatch';

interface SliceSpoolPickerProps {
  /** Connected printers to offer, already narrowed to the selected model. */
  printers: LoadedSpoolPrinter[];
  data: UnifiedPresetsResponse;
  index: FilamentNameIndex;
  selectedPrinterName: string | null;
  compatIndex: PrinterCompatibilityIndex;
  /** The filament row being filled, e.g. "Filament 2 (PLA)". */
  slotLabel: string;
  onPick: (match: SlotMatch, colour: string | null) => void;
  onClose: () => void;
}

/**
 * The spools loaded in the connected printers, laid out like the units
 * themselves, for filling one filament row of the Slice dialog (#3172).
 *
 * Regular AMS units keep their four-slot grid, empty slots included, so the
 * screen matches the machine. AMS-HT units follow, packed four to a row, then
 * the external holders that have a spool in them. A loaded spool with no
 * profile for the selected printer stays visible but can't be picked.
 */
export function SliceSpoolPicker({
  printers,
  data,
  index,
  selectedPrinterName,
  compatIndex,
  slotLabel,
  onPick,
  onClose,
}: SliceSpoolPickerProps) {
  const { t } = useTranslation();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="slice-spool-picker-title"
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary/60"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex-shrink-0 flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-bambu-dark-tertiary/40">
          <div className="min-w-0">
            <h3 id="slice-spool-picker-title" className="text-white font-medium">
              {t('slice.loadedSpools.title', 'Loaded spools')}
            </h3>
            <p className="text-xs text-bambu-gray mt-1 truncate">{slotLabel}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex-shrink-0 text-bambu-gray hover:text-white transition-colors"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {printers.length === 0 ? (
            <p className="text-sm text-bambu-gray">
              {t('slice.loadedSpools.none', 'No printer of this model is online.')}
            </p>
          ) : (
            printers.map((printer) => (
              <PrinterSection
                key={printer.id}
                printer={printer}
                matchFor={(tray) => matchSlotPreset(tray, data, index, selectedPrinterName, compatIndex)}
                onPick={onPick}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

function PrinterSection({
  printer,
  matchFor,
  onPick,
}: {
  printer: LoadedSpoolPrinter;
  matchFor: (tray: LoadedSpoolTray) => SlotMatch | null;
  onPick: (match: SlotMatch, colour: string | null) => void;
}) {
  const { t } = useTranslation();
  const regular = printer.ams.filter((u) => !u.is_ams_ht);
  const ht = printer.ams.filter((u) => u.is_ams_ht);
  const hasAny = printer.ams.length > 0 || printer.external.length > 0;

  const tile = (tray: LoadedSpoolTray, label: string) => (
    <TrayTile key={`${tray.ams_id}-${tray.tray_id}`} tray={tray} label={label} match={matchFor(tray)} onPick={onPick} />
  );

  return (
    <section>
      <h4 className="text-sm text-white font-medium mb-2">
        {printer.name}
        {printer.model && <span className="ml-2 text-xs text-bambu-gray font-normal">{printer.model}</span>}
      </h4>
      {!hasAny && (
        <p className="text-xs text-bambu-gray">{t('slice.loadedSpools.nothingLoaded', 'No AMS and no external spool.')}</p>
      )}
      <div className="space-y-3">
        {regular.map((unit) => (
          <div key={unit.id}>
            <div className="text-xs text-bambu-gray mb-1">{getAmsLabel(unit.id, unit.trays.length)}</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {unit.trays.map((tray) => tile(tray, formatSlotLabel(unit.id, tray.tray_id, false, false)))}
            </div>
          </div>
        ))}
        {ht.length > 0 && (
          <div>
            <div className="text-xs text-bambu-gray mb-1">AMS-HT</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {ht.flatMap((unit) => unit.trays.map((tray) => tile(tray, formatSlotLabel(unit.id, tray.tray_id, true, false))))}
            </div>
          </div>
        )}
        {printer.external.length > 0 && (
          <div>
            <div className="text-xs text-bambu-gray mb-1">{t('printers.external')}</div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {printer.external.map((tray) =>
                tile(
                  tray,
                  printer.external_holders > 1
                    ? (tray.tray_id === 0 ? t('printers.extL') : t('printers.extR'))
                    : t('printers.ext'),
                ),
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

function TrayTile({
  tray,
  label,
  match,
  onPick,
}: {
  tray: LoadedSpoolTray;
  label: string;
  match: SlotMatch | null;
  onPick: (match: SlotMatch, colour: string | null) => void;
}) {
  const { t } = useTranslation();
  const emptyKind = getEmptySlotKind(tray);
  const colour = trayColourHex(tray);

  const profileName = useMemo(() => {
    if (match) return presetDisplayName(match.preset.name);
    const saved = savedPresetFor(tray);
    if (saved?.preset_name) return presetDisplayName(saved.preset_name);
    return tray.tray_sub_brands || null;
  }, [match, tray]);

  if (emptyKind !== null) {
    return (
      <div className="rounded-md border border-dashed border-bambu-dark-tertiary px-2 py-2 text-xs text-bambu-gray/70">
        <div className="font-medium text-bambu-gray">{label}</div>
        <div>
          {emptyKind === 'reset'
            ? t('slice.loadedSpools.unknownSpool', 'Unidentified spool')
            : t('slice.loadedSpools.empty', 'Empty')}
        </div>
      </div>
    );
  }

  const disabled = match === null;
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => match && onPick(match, colour)}
      title={disabled ? t('slice.loadedSpools.noProfile', 'No profile for the selected printer') : profileName ?? undefined}
      className="text-left rounded-md border border-bambu-dark-tertiary bg-bambu-dark px-2 py-2 text-xs transition-colors enabled:hover:border-bambu-green disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <div className="flex items-center gap-1.5">
        <FilamentSwatch rgba={tray.tray_color} className="w-3.5 h-3.5 shrink-0" effectSize="table" />
        <span className="font-medium text-white">{label}</span>
        <span className="ml-auto text-bambu-gray">{tray.tray_type}</span>
      </div>
      <div className="mt-1 truncate text-bambu-gray" title={profileName ?? undefined}>
        {profileName ?? '—'}
      </div>
      {disabled && (
        <div className="mt-0.5 truncate text-[11px] text-amber-600 dark:text-amber-400">
          {t('slice.loadedSpools.noProfileShort', 'No profile for this printer')}
        </div>
      )}
    </button>
  );
}
