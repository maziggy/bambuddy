import { Cog, Loader2, X } from 'lucide-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, type LocalPreset } from '../api/client';
import { useSliceJobTracker } from '../contexts/SliceJobTrackerContext';

export type SliceSource =
  | { kind: 'libraryFile'; id: number; filename: string }
  | { kind: 'archive'; id: number; filename: string };

interface SliceModalProps {
  source: SliceSource;
  onClose: () => void;
}

export function SliceModal({ source, onClose }: SliceModalProps) {
  const { t } = useTranslation();
  const { trackJob } = useSliceJobTracker();

  const [printerPresetId, setPrinterPresetId] = useState<number | null>(null);
  const [processPresetId, setProcessPresetId] = useState<number | null>(null);
  const [filamentPresetId, setFilamentPresetId] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const presetsQuery = useQuery({
    queryKey: ['localPresets'],
    queryFn: () => api.getLocalPresets(),
    staleTime: 60_000,
  });

  const enqueueMutation = useMutation({
    mutationFn: async () => {
      if (printerPresetId == null || processPresetId == null || filamentPresetId == null) {
        throw new Error('All three presets must be selected');
      }
      const body = {
        printer_preset_id: printerPresetId,
        process_preset_id: processPresetId,
        filament_preset_id: filamentPresetId,
      };
      if (source.kind === 'libraryFile') {
        return api.sliceLibraryFile(source.id, body);
      }
      return api.sliceArchive(source.id, body);
    },
    onSuccess: (enqueue) => {
      // Hand the job off to the global tracker — polling, toasts, and
      // query invalidation continue across navigation.
      trackJob(enqueue.job_id, source.kind, source.filename);
      onClose();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(msg);
    },
  });

  const isReady = printerPresetId != null && processPresetId != null && filamentPresetId != null;
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
                'Failed to load presets. Open Settings → Profiles to import them first.',
              )}
            </div>
          )}

          {presetsQuery.data && (
            <>
              <PresetDropdown
                label={t('slice.printer', 'Printer profile')}
                presets={presetsQuery.data.printer}
                value={printerPresetId}
                onChange={setPrinterPresetId}
                disabled={isEnqueuing}
              />
              <PresetDropdown
                label={t('slice.process', 'Process profile')}
                presets={presetsQuery.data.process}
                value={processPresetId}
                onChange={setProcessPresetId}
                disabled={isEnqueuing}
              />
              <PresetDropdown
                label={t('slice.filament', 'Filament profile')}
                presets={presetsQuery.data.filament}
                value={filamentPresetId}
                onChange={setFilamentPresetId}
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

interface PresetDropdownProps {
  label: string;
  presets: LocalPreset[];
  value: number | null;
  onChange: (id: number | null) => void;
  disabled?: boolean;
}

function PresetDropdown({ label, presets, value, onChange, disabled }: PresetDropdownProps) {
  const { t } = useTranslation();
  return (
    <label className="block">
      <span className="block text-xs text-bambu-gray mb-1">{label}</span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        disabled={disabled}
        className="w-full px-3 py-2 rounded-md bg-bambu-dark border border-bambu-dark-tertiary text-white text-sm focus:outline-none focus:border-bambu-gray disabled:opacity-50"
      >
        <option value="">{t('slice.selectPreset', '— Select a preset —')}</option>
        {presets.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  );
}
