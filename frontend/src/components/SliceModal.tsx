import { Cloud, CloudOff, Cog, Loader2, X } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  api,
  type PresetRef,
  type PresetSource,
  type SlicerCloudStatus,
  type UnifiedPreset,
  type UnifiedPresetsBySlot,
  type UnifiedPresetsResponse,
} from '../api/client';
import { useSliceJobTracker } from '../contexts/SliceJobTrackerContext';
import { PlatePickerModal } from './PlatePickerModal';
import type { PlateFilament } from '../types/plates';
import { normalizeColorForCompare, colorsAreSimilar } from '../utils/amsHelpers';

export type SliceSource =
  | { kind: 'libraryFile'; id: number; filename: string }
  | { kind: 'archive'; id: number; filename: string };

interface SliceModalProps {
  source: SliceSource;
  onClose: () => void;
}

type Slot = 'printer' | 'process' | 'filament';

// SliceModal-specific tier priority: local (imported) → cloud → standard.
// Imported profiles are surfaced first because they're the user's curated
// picks (often colour/type-tagged), cloud is second since names alone can't
// drive metadata-aware match, standard is the bundled fallback. This is
// distinct from the listing endpoint's dedup order and only affects what
// the SliceModal renders / pre-picks.
const SLICE_MODAL_TIER_ORDER = ['local', 'cloud', 'standard'] as const;

function pickDefault(by: UnifiedPresetsResponse, slot: Slot): PresetRef | null {
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    const list = by[tier][slot];
    if (list.length > 0) {
      return { source: list[0].source, id: list[0].id };
    }
  }
  return null;
}

const TIER_BONUS: Record<PresetSource, number> = {
  local: 1.5,
  cloud: 1.0,
  standard: 0.5,
};

function pickFilamentForSlot(
  by: UnifiedPresetsResponse,
  required: { type: string; color: string },
): PresetRef | null {
  // Score every filament preset against the plate slot's required (type,
  // colour) and pick the highest. Mirrors the AMS slot-mapping match in the
  // print/schedule modal: type match dominates, exact-colour-match bumps over
  // similar-colour-match, and a small per-tier bonus breaks ties so cloud
  // user customisations win over standard bundled fallbacks of equal merit.
  const reqType = required.type.trim().toUpperCase();
  const reqColor = normalizeColorForCompare(required.color);

  let best: { ref: PresetRef; score: number } | null = null;
  for (const tier of SLICE_MODAL_TIER_ORDER) {
    for (const p of by[tier].filament) {
      let score = 0;
      const presetType = (p.filament_type ?? '').trim().toUpperCase();
      const presetColor = normalizeColorForCompare(p.filament_colour ?? '');
      if (reqType && presetType && reqType === presetType) score += 10;
      if (reqColor && presetColor) {
        if (presetColor === reqColor) score += 5;
        else if (colorsAreSimilar(p.filament_colour ?? '', required.color)) score += 2;
      }
      score += TIER_BONUS[tier];
      if (best == null || score > best.score) {
        best = { ref: { source: p.source, id: p.id }, score };
      }
    }
  }
  // Fall back to plain priority pick if every preset scored 0+tier (i.e. no
  // metadata matched). The fallback is exactly the single-color default —
  // first preset in the highest-priority non-empty tier.
  if (best == null) return pickDefault(by, 'filament');
  return best.ref;
}

function toRefValue(ref: PresetRef | null): string {
  // The HTML `<select>` value space is flat strings; encode source + id so
  // the same preset name can live in multiple tiers without collision.
  return ref ? `${ref.source}:${ref.id}` : '';
}

function fromRefValue(raw: string): PresetRef | null {
  if (!raw) return null;
  const idx = raw.indexOf(':');
  if (idx < 0) return null;
  const source = raw.slice(0, idx) as PresetSource;
  const id = raw.slice(idx + 1);
  if (source !== 'cloud' && source !== 'local' && source !== 'standard') return null;
  return { source, id };
}

export function SliceModal({ source, onClose }: SliceModalProps) {
  const { t } = useTranslation();
  const { trackJob } = useSliceJobTracker();

  const [printerPreset, setPrinterPreset] = useState<PresetRef | null>(null);
  const [processPreset, setProcessPreset] = useState<PresetRef | null>(null);
  // One filament ref per plate slot, in plate order. For STL / single-plate /
  // single-color sources this is a one-element array; multi-color 3MFs get one
  // entry per AMS slot the plate uses. Pre-pick (effect below) initialises
  // each slot from the source plate's required (type, colour).
  const [filamentPresets, setFilamentPresets] = useState<(PresetRef | null)[]>([]);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // null = plate not yet picked (or single-plate / non-3MF — picker is skipped
  // and we'll backfill 1 at submit time). Set to a 1-indexed plate number once
  // the user picks one (or implicitly for single-plate sources).
  const [selectedPlate, setSelectedPlate] = useState<number | null>(null);

  const platesQuery = useQuery({
    queryKey: ['slicePlates', source.kind, source.id],
    queryFn: async () => {
      if (source.kind === 'libraryFile') {
        return api.getLibraryFilePlates(source.id);
      }
      return api.getArchivePlates(source.id);
    },
    staleTime: 60_000,
  });

  const isMultiPlate =
    !!platesQuery.data?.is_multi_plate && (platesQuery.data?.plates?.length ?? 0) > 1;
  // Single-plate / non-3MF / fetch failure: skip the picker, default to plate 1
  // at submit time so the backend's existing default behaviour is preserved.
  const needsPlatePicker = isMultiPlate && selectedPlate == null;

  // Per-plate filament requirements via the same endpoint the print/schedule
  // modal uses. Reusing it here keeps the SliceModal honest with whatever
  // logic that endpoint applies (slice_info parsing, future enhancements for
  // unsliced project files, dual-nozzle fields, etc.) instead of duplicating
  // extraction. plate_id is always sent: single-plate falls through to plate
  // 1 server-side; multi-plate uses the user's pick.
  const effectivePlateId = selectedPlate ?? 1;
  const filamentReqsQuery = useQuery({
    queryKey: ['sliceFilamentReqs', source.kind, source.id, effectivePlateId],
    queryFn: async () => {
      if (source.kind === 'libraryFile') {
        return api.getLibraryFileFilamentRequirements(source.id, effectivePlateId);
      }
      return api.getArchiveFilamentRequirements(source.id, effectivePlateId);
    },
    enabled: !needsPlatePicker,
    staleTime: 60_000,
  });

  // Filament slot list for the active plate. Falls back to one synthetic slot
  // for STL/STEP and any "no metadata available" case so the modal still
  // works (single dropdown, mono-color slice).
  const filamentSlots = useMemo<PlateFilament[]>(() => {
    const reqs = filamentReqsQuery.data?.filaments ?? [];
    if (reqs.length > 0) return reqs as PlateFilament[];
    return [
      { slot_id: 1, type: '', color: '', used_grams: 0, used_meters: 0 },
    ];
  }, [filamentReqsQuery.data]);

  const presetsQuery = useQuery({
    queryKey: ['slicerPresets'],
    queryFn: () => api.getSlicerPresets(),
    staleTime: 60_000,
    // Don't fetch presets while the plate picker is on screen — saves a
    // round-trip if the user cancels out of the plate step.
    enabled: !platesQuery.isLoading && !needsPlatePicker,
  });

  // Printer / process pre-pick: see SLICE_MODAL_TIER_ORDER. Runs once when
  // presets first arrive; subsequent re-renders preserve any manual choice.
  useEffect(() => {
    if (!presetsQuery.data) return;
    if (printerPreset == null) setPrinterPreset(pickDefault(presetsQuery.data, 'printer'));
    if (processPreset == null) setProcessPreset(pickDefault(presetsQuery.data, 'process'));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetsQuery.data]);

  // Filament pre-pick: re-runs whenever the active filament-slot count
  // changes (plate selection, single-plate metadata arriving). For each slot
  // we score every available filament preset against the slot's required
  // (type, colour) and keep the highest match. Slot count mismatch → reset
  // and re-pick everything; same length → preserve any user override.
  useEffect(() => {
    if (!presetsQuery.data) return;
    const data = presetsQuery.data;
    setFilamentPresets((current) => {
      if (current.length === filamentSlots.length && current.every((r) => r != null)) {
        return current;
      }
      return filamentSlots.map((slot) =>
        pickFilamentForSlot(data, { type: slot.type, color: slot.color }),
      );
    });
  }, [presetsQuery.data, filamentSlots]);

  const enqueueMutation = useMutation({
    mutationFn: async () => {
      if (
        !printerPreset ||
        !processPreset ||
        filamentPresets.length === 0 ||
        filamentPresets.some((r) => r == null)
      ) {
        throw new Error(t('slice.allPresetsRequired', 'All presets must be selected'));
      }
      const body = {
        printer_preset: printerPreset,
        process_preset: processPreset,
        // The first slot also goes into the legacy singular field so the
        // backend's older callers / clients keep behaving the same — the
        // backend validator prefers `filament_presets` when both are set.
        filament_preset: filamentPresets[0] as PresetRef,
        filament_presets: filamentPresets as PresetRef[],
        // Always send a concrete plate number when the source is multi-plate;
        // omit otherwise so the backend default applies for STL / single-plate
        // 3MF sources where the concept doesn't apply.
        ...(selectedPlate != null ? { plate: selectedPlate } : {}),
      };
      if (source.kind === 'libraryFile') {
        return api.sliceLibraryFile(source.id, body);
      }
      return api.sliceArchive(source.id, body);
    },
    onSuccess: (enqueue) => {
      trackJob(enqueue.job_id, source.kind, source.filename);
      onClose();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(msg);
    },
  });

  const isReady =
    printerPreset != null &&
    processPreset != null &&
    filamentPresets.length > 0 &&
    filamentPresets.every((r) => r != null);
  const isEnqueuing = enqueueMutation.isPending;

  // Step 1: plate picker for multi-plate 3MF sources. Cancelling closes the
  // entire flow (matches the existing PlatePickerModal contract used by the
  // archive g-code-viewer entry point).
  if (needsPlatePicker && platesQuery.data) {
    return (
      <PlatePickerModal
        plates={platesQuery.data.plates}
        onSelect={(plateIndex) => setSelectedPlate(plateIndex)}
        onClose={onClose}
      />
    );
  }

  // Step 2 (or only step for single-plate / non-3MF / load-failure): preset
  // picker. While the plates query is in-flight we still render the shell
  // because the presets query is gated on it; the loader covers both.
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={() => {
        if (!isEnqueuing) onClose();
      }}
    >
      <div
        className="w-full max-w-xl max-h-[85vh] flex flex-col rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary/60"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex-shrink-0 flex items-start justify-between gap-3 px-4 pt-4 pb-3 border-b border-bambu-dark-tertiary/40">
          <div className="min-w-0">
            <h3 className="text-white font-medium flex items-center gap-2">
              <Cog className="w-4 h-4" />
              {t('slice.title', 'Slice model')}
            </h3>
            <p className="text-xs text-bambu-gray mt-1 truncate" title={source.filename}>
              {source.filename}
              {selectedPlate != null
                ? ` • ${t('archives.platePicker.plateLabel', { index: selectedPlate })}`
                : ''}
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={isEnqueuing}
            className="flex-shrink-0 text-bambu-gray hover:text-white transition-colors disabled:opacity-50"
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {(platesQuery.isLoading || presetsQuery.isLoading || filamentReqsQuery.isLoading) && (
            <div className="flex items-center gap-2 text-bambu-gray text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {t('slice.loadingPresets', 'Loading presets…')}
            </div>
          )}

          {presetsQuery.isError && (
            <div className="text-sm text-red-400" role="alert">
              {t(
                'slice.presetsLoadFailed',
                'Failed to load presets. Open Settings → Profiles to import them, or sign in to Bambu Cloud.',
              )}
            </div>
          )}

          {presetsQuery.data && (
            <>
              <CloudStatusBanner status={presetsQuery.data.cloud_status} />
              <PresetDropdown
                label={t('slice.printer', 'Printer profile')}
                slot="printer"
                data={presetsQuery.data}
                value={printerPreset}
                onChange={setPrinterPreset}
                disabled={isEnqueuing}
              />
              <PresetDropdown
                label={t('slice.process', 'Process profile')}
                slot="process"
                data={presetsQuery.data}
                value={processPreset}
                onChange={setProcessPreset}
                disabled={isEnqueuing}
              />
              {filamentSlots.map((slot, idx) => (
                <PresetDropdown
                  key={`filament-${idx}`}
                  label={
                    filamentSlots.length > 1
                      ? t('slice.filamentSlot', {
                          index: idx + 1,
                          type: slot.type,
                          defaultValue: `Filament ${idx + 1} (${slot.type || ''})`,
                        })
                      : t('slice.filament', 'Filament profile')
                  }
                  slot="filament"
                  data={presetsQuery.data}
                  value={filamentPresets[idx] ?? null}
                  onChange={(ref) =>
                    setFilamentPresets((current) => {
                      const next = current.length === filamentSlots.length
                        ? [...current]
                        : filamentSlots.map((_, i) => current[i] ?? null);
                      next[idx] = ref;
                      return next;
                    })
                  }
                  disabled={isEnqueuing}
                  swatchColor={filamentSlots.length > 1 ? slot.color : undefined}
                />
              ))}
            </>
          )}

          {errorMessage && (
            <div className="text-sm text-red-400 bg-red-900/20 border border-red-900/40 rounded p-2" role="alert">
              {errorMessage}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex-shrink-0 flex justify-end gap-2 px-4 py-3 border-t border-bambu-dark-tertiary/40">
          <button
            type="button"
            onClick={onClose}
            disabled={isEnqueuing}
            className="px-3 py-1.5 text-sm rounded-md border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray transition-colors disabled:opacity-50"
          >
            {t('common.cancel', 'Cancel')}
          </button>
          <button
            type="button"
            onClick={() => {
              setErrorMessage(null);
              enqueueMutation.mutate();
            }}
            disabled={!isReady || isEnqueuing}
            className="px-3 py-1.5 text-sm rounded-md bg-bambu-green hover:bg-bambu-green/90 text-bambu-dark font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
          >
            {isEnqueuing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('slice.enqueuing', 'Submitting slice job…')}
              </>
            ) : (
              t('slice.action', 'Slice')
            )}
          </button>
        </div>
      </div>
    </div>
  );
}

function CloudStatusBanner({ status }: { status: SlicerCloudStatus }) {
  const { t } = useTranslation();
  if (status === 'ok') return null;

  // Map each non-ok status to the appropriate icon + tone. None of these are
  // hard errors — the user can still slice using local + standard presets,
  // so we use info / warn styling rather than error red.
  const config: Record<Exclude<SlicerCloudStatus, 'ok'>, { tone: string; icon: typeof Cloud; key: string; fallback: string }> = {
    not_authenticated: {
      tone: 'border-bambu-dark-tertiary/40 bg-bambu-dark text-bambu-gray',
      icon: Cloud,
      key: 'slice.cloud.notAuthenticated',
      fallback: 'Sign in to Bambu Cloud (Settings → Profiles → Cloud) to see your cloud presets.',
    },
    expired: {
      tone: 'border-amber-700/40 bg-amber-900/20 text-amber-200',
      icon: CloudOff,
      key: 'slice.cloud.expired',
      fallback: 'Bambu Cloud session expired — sign in again to refresh your cloud presets.',
    },
    unreachable: {
      tone: 'border-bambu-dark-tertiary/40 bg-bambu-dark text-bambu-gray',
      icon: CloudOff,
      key: 'slice.cloud.unreachable',
      fallback: 'Bambu Cloud is unreachable right now. Local and standard presets still work.',
    },
  };
  const { tone, icon: Icon, key, fallback } = config[status];
  return (
    <div className={`flex items-start gap-2 text-xs rounded-md border p-2 ${tone}`} role="status">
      <Icon className="w-4 h-4 flex-shrink-0 mt-0.5" />
      <span>{t(key, fallback)}</span>
    </div>
  );
}

interface PresetDropdownProps {
  label: string;
  slot: Slot;
  data: UnifiedPresetsResponse;
  value: PresetRef | null;
  onChange: (ref: PresetRef | null) => void;
  disabled?: boolean;
  // Optional colour swatch shown next to the label — used for multi-color
  // filament slots so the user can see at a glance which slot they're
  // configuring against the source 3MF's per-slot colour.
  swatchColor?: string;
}

function PresetDropdown({ label, slot, data, value, onChange, disabled, swatchColor }: PresetDropdownProps) {
  const { t } = useTranslation();

  const sections: { tierLabel: string; entries: UnifiedPreset[] }[] = useMemo(() => {
    // Order matches SLICE_MODAL_TIER_ORDER: imported first, then cloud, then
    // standard fallback. Sections with no entries collapse out so a user
    // without cloud / local presets only sees the tiers they actually have.
    const tiers: { key: keyof UnifiedPresetsResponse; tier: 'cloud' | 'local' | 'standard'; label: string; fallback: string }[] = [
      { key: 'local', tier: 'local', label: 'slice.tier.local', fallback: 'Imported' },
      { key: 'cloud', tier: 'cloud', label: 'slice.tier.cloud', fallback: 'Cloud' },
      { key: 'standard', tier: 'standard', label: 'slice.tier.standard', fallback: 'Standard' },
    ];
    return tiers
      .map(({ key, label: lk, fallback }) => ({
        tierLabel: t(lk, fallback),
        entries: (data[key] as UnifiedPresetsBySlot)[slot],
      }))
      .filter((s) => s.entries.length > 0);
  }, [data, slot, t]);

  const totalEntries = sections.reduce((sum, s) => sum + s.entries.length, 0);

  return (
    <label className="block">
      <span className="flex items-center gap-2 text-xs text-bambu-gray mb-1">
        {swatchColor && (
          <span
            className="inline-block w-3 h-3 rounded-full border border-bambu-dark-tertiary"
            style={{ backgroundColor: swatchColor || 'transparent' }}
            aria-hidden
          />
        )}
        <span>{label}</span>
      </span>
      <select
        value={toRefValue(value)}
        onChange={(e) => onChange(fromRefValue(e.target.value))}
        disabled={disabled || totalEntries === 0}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">
          {totalEntries === 0
            ? t('slice.noPresetsForSlot', 'No presets available')
            : t('slice.selectPreset', '— Select a preset —')}
        </option>
        {sections.map((section) => (
          <optgroup key={section.tierLabel} label={section.tierLabel}>
            {section.entries.map((p) => (
              <option key={`${p.source}:${p.id}`} value={`${p.source}:${p.id}`}>
                {p.name}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}
