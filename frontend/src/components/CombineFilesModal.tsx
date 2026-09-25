import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Combine, Loader2, X } from 'lucide-react';

import { api, type LibraryFileListItem, type LibraryFileUploadResponse } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

// Mirrors MAX_COMBINE_INSTANCES in backend/app/services/mesh_combine.py.
const MAX_OBJECTS = 100;

interface CombineFilesModalProps {
  files: LibraryFileListItem[];
  folderId: number | null;
  // Offer "open the slicer next" only where in-app slicing is available.
  canSlice: boolean;
  onClose: () => void;
  onCombined: (file: LibraryFileUploadResponse, sliceNext: boolean) => void;
}

function stem(filename: string): string {
  const dot = filename.lastIndexOf('.');
  return dot > 0 ? filename.slice(0, dot) : filename;
}

/**
 * Combine selected STLs (or copies of one, #2999) into a single multi-object
 * 3MF in the library, ready to slice onto one plate with auto-arrange. The
 * sidecar slices one file at a time, so this is how several separate models
 * end up on the same plate without a desktop slicer.
 */
export function CombineFilesModal({ files, folderId, canSlice, onClose, onCombined }: CombineFilesModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [copies, setCopies] = useState<Record<number, number>>(() =>
    Object.fromEntries(files.map((f) => [f.id, 1])),
  );
  const [name, setName] = useState<string | null>(null);
  const [sliceNext, setSliceNext] = useState(canSlice);

  const totalObjects = files.reduce((sum, f) => sum + (copies[f.id] || 0), 0);
  // Until the user types a name, keep suggesting one that describes the plate.
  const defaultName = useMemo(() => {
    if (files.length === 1) return `${stem(files[0].filename)} x${copies[files[0].id] || 1}`;
    return t('fileManager.combine.defaultName', { name: stem(files[0]?.filename ?? ''), count: files.length - 1 });
  }, [files, copies, t]);
  const effectiveName = (name ?? defaultName).trim();

  const combineMutation = useMutation({
    mutationFn: () =>
      api.combineLibraryFiles(
        files.map((f) => ({ file_id: f.id, copies: copies[f.id] || 1 })),
        effectiveName,
        folderId,
      ),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      showToast(t('fileManager.combine.done', { filename: result.filename, count: totalObjects }), 'success');
      onCombined(result, sliceNext && canSlice);
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !combineMutation.isPending) onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, combineMutation.isPending]);

  const tooMany = totalObjects > MAX_OBJECTS;
  const invalidCopies = files.some((f) => !copies[f.id] || copies[f.id] < 1);
  const titleId = 'combine-files-title';

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={() => !combineMutation.isPending && onClose()} />
      <div
        className="relative w-full max-w-md mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-bambu-dark-tertiary">
          <h3 id={titleId} className="text-base font-semibold text-white flex items-center gap-2">
            <Combine className="w-4 h-4 text-bambu-green" />
            {t('fileManager.combine.title')}
          </h3>
          <button
            type="button"
            className="p-1.5 text-bambu-gray hover:text-white rounded"
            onClick={onClose}
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <p className="px-5 pt-3 text-sm text-bambu-gray">{t('fileManager.combine.description')}</p>

        <div className="px-5 py-3">
          <label htmlFor="combine-name" className="block text-xs text-bambu-gray mb-1">
            {t('fileManager.combine.nameLabel')}
          </label>
          <div className="flex items-center gap-1">
            <input
              id="combine-name"
              type="text"
              value={name ?? defaultName}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
            />
            <span className="text-sm text-bambu-gray">.3mf</span>
          </div>
        </div>

        <div className="overflow-y-auto flex-1 max-h-[20rem] border-y border-bambu-dark-tertiary">
          <div className="flex justify-between px-5 py-2 text-xs text-bambu-gray">
            <span>{t('fileManager.combine.model')}</span>
            <span>{t('fileManager.combine.copies')}</span>
          </div>
          <ul className="divide-y divide-bambu-dark-tertiary/40">
            {files.map((f) => (
              <li key={f.id} className="flex items-center gap-3 px-5 py-2">
                <span className="text-sm text-white truncate flex-1" title={f.filename}>
                  {f.filename}
                </span>
                <input
                  type="number"
                  min={1}
                  max={MAX_OBJECTS}
                  value={copies[f.id] || ''}
                  onChange={(e) => {
                    const n = parseInt(e.target.value, 10);
                    setCopies((prev) => ({ ...prev, [f.id]: Number.isNaN(n) ? 0 : n }));
                  }}
                  aria-label={t('fileManager.combine.copiesFor', { filename: f.filename })}
                  className="w-20 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white text-right focus:outline-none focus:border-bambu-green"
                />
              </li>
            ))}
          </ul>
        </div>

        <div className="px-5 py-3 space-y-2 text-sm">
          <div className={tooMany ? 'text-red-400' : 'text-bambu-gray'}>
            {tooMany
              ? t('fileManager.combine.tooMany', { max: MAX_OBJECTS })
              : t('fileManager.combine.total', { count: totalObjects })}
          </div>
          {canSlice && (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={sliceNext}
                onChange={(e) => setSliceNext(e.target.checked)}
                className="accent-bambu-green"
              />
              <span className="text-white">{t('fileManager.combine.sliceNext')}</span>
            </label>
          )}
        </div>

        <div className="px-5 py-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={combineMutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            type="button"
            onClick={() => combineMutation.mutate()}
            disabled={combineMutation.isPending || tooMany || invalidCopies || !effectiveName}
          >
            {combineMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            {t('fileManager.combine.submit')}
          </Button>
        </div>
      </div>
    </div>
  );
}
