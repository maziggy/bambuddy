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

export type SliceSource =
  | { kind: 'libraryFile'; id: number; filename: string }
  | { kind: 'archive'; id: number; filename: string };

interface SliceModalProps {
  source: SliceSource;
  onClose: () => void;
}

type Slot = 'printer' | 'process' | 'filament';

function pickDefault(by: UnifiedPresetsResponse, slot: Slot): PresetRef | null {
  // Cloud > local > standard. The endpoint already deduplicates by name, so
  // no name-collision handling needed here — first non-empty tier wins.
  for (const tier of ['cloud', 'local', 'standard'] as const) {
    const list = by[tier][slot];
    if (list.length > 0) {
      return { source: list[0].source, id: list[0].id };
    }
  }
  return null;
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
  const [filamentPreset, setFilamentPreset] = useState<PresetRef | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const presetsQuery = useQuery({
    queryKey: ['slicerPresets'],
    queryFn: () => api.getSlicerPresets(),
    staleTime: 60_000,
  });

  // Default selection: cloud > local > standard. Runs only on the first
  // successful load; subsequent re-renders preserve the user's manual choice.
  useEffect(() => {
    if (!presetsQuery.data) return;
    if (printerPreset == null) setPrinterPreset(pickDefault(presetsQuery.data, 'printer'));
    if (processPreset == null) setProcessPreset(pickDefault(presetsQuery.data, 'process'));
    if (filamentPreset == null) setFilamentPreset(pickDefault(presetsQuery.data, 'filament'));
    // Intentionally exclude state-setters and current selections from deps —
    // we only want the auto-pick to fire once when data first arrives.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [presetsQuery.data]);

  const enqueueMutation = useMutation({
    mutationFn: async () => {
      if (!printerPreset || !processPreset || !filamentPreset) {
        throw new Error('All three presets must be selected');
      }
      const body = {
        printer_preset: printerPreset,
        process_preset: processPreset,
        filament_preset: filamentPreset,
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

  const isReady = printerPreset != null && processPreset != null && filamentPreset != null;
  const isEnqueuing = enqueueMutation.isPending;

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
          {presetsQuery.isLoading && (
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
              <PresetDropdown
                label={t('slice.filament', 'Filament profile')}
                slot="filament"
                data={presetsQuery.data}
                value={filamentPreset}
                onChange={setFilamentPreset}
                disabled={isEnqueuing}
              />
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
}

function PresetDropdown({ label, slot, data, value, onChange, disabled }: PresetDropdownProps) {
  const { t } = useTranslation();

  const sections: { tierLabel: string; entries: UnifiedPreset[] }[] = useMemo(() => {
    const tiers: { key: keyof UnifiedPresetsResponse; tier: 'cloud' | 'local' | 'standard'; label: string; fallback: string }[] = [
      { key: 'cloud', tier: 'cloud', label: 'slice.tier.cloud', fallback: 'Cloud' },
      { key: 'local', tier: 'local', label: 'slice.tier.local', fallback: 'Imported' },
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
      <span className="block text-xs text-bambu-gray mb-1">{label}</span>
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
