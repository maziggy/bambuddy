import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Save, Link, Camera, Trash2, Loader2, Plus, ExternalLink, StickyNote } from 'lucide-react';
import { api } from '../api/client';
import type { LibraryFileListItem } from '../api/client';
import { Button } from './Button';
import { PhotoGalleryModal } from './PhotoGalleryModal';
import { useToast } from '../contexts/ToastContext';
import { formatDate, formatDuration } from '../utils/date';
import { formatFileSize } from '../utils/file';

interface LibraryFileDetailsModalProps {
  file: LibraryFileListItem;
  // Notes, link and photos are edits to the file, so they follow the same
  // ownership gate as rename (`canModify('library', 'update', ...)`).
  canEdit: boolean;
  onClose: () => void;
}

// Notes, an external link and photos of the printed result on a library
// file (#3077) — the same trio EditArchiveModal offers for an archive. Photos
// are saved as they are added; notes and the link on Save.
export function LibraryFileDetailsModal({ file, canEdit, onClose }: LibraryFileDetailsModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const { data: details } = useQuery({
    queryKey: ['library-file', file.id],
    queryFn: () => api.getLibraryFile(file.id),
  });

  const [notes, setNotes] = useState('');
  const [externalUrl, setExternalUrl] = useState('');
  const [photos, setPhotos] = useState<string[]>([]);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  // The photo the lightbox opens on, or null while it is closed.
  const [galleryIndex, setGalleryIndex] = useState<number | null>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);

  // Seed the form once per file. Later refetches (photo changes below, window
  // focus) must not overwrite notes or a link the user is still typing.
  const seededForId = useRef<number | null>(null);
  useEffect(() => {
    if (!details || seededForId.current === details.id) return;
    seededForId.current = details.id;
    setNotes(details.notes ?? '');
    setExternalUrl(details.external_url ?? '');
    setPhotos(details.photos ?? []);
  }, [details]);

  // Escape closes the modal, as it does in the rest of the app. Not while the
  // lightbox is open on top of it — that handles Escape itself, and a second
  // listener here would close both at once.
  useEffect(() => {
    if (galleryIndex !== null) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [galleryIndex, onClose]);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['library-files'] });
    queryClient.invalidateQueries({ queryKey: ['library-file', file.id] });
  };

  const saveMutation = useMutation({
    mutationFn: () => api.updateLibraryFile(file.id, { notes, external_url: externalUrl.trim() }),
    onSuccess: () => {
      invalidate();
      showToast(t('fileManager.details.saved'), 'success');
      onClose();
    },
    onError: () => {
      showToast(t('fileManager.details.saveFailed'), 'error');
    },
  });

  const hasChanges =
    !!details && (notes !== (details.notes ?? '') || externalUrl.trim() !== (details.external_url ?? ''));

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = e.target.files?.[0];
    if (!picked) return;
    setUploadingPhoto(true);
    try {
      const result = await api.uploadLibraryFilePhoto(file.id, picked);
      setPhotos(result.photos);
      invalidate();
    } catch {
      showToast(t('fileManager.details.uploadFailed'), 'error');
    } finally {
      setUploadingPhoto(false);
      if (photoInputRef.current) {
        photoInputRef.current.value = '';
      }
    }
  };

  const handlePhotoDelete = async (filename: string) => {
    try {
      const result = await api.deleteLibraryFilePhoto(file.id, filename);
      const remaining = result.photos ?? [];
      setPhotos(remaining);
      // Deleting the last photo unmounts the lightbox through the render
      // guard below before its own "nothing left to show" branch can call
      // onClose, so the index has to be cleared here. Left set, it keeps
      // Escape disabled for good and re-opens the lightbox unasked as soon
      // as another photo is uploaded.
      if (remaining.length === 0) setGalleryIndex(null);
      invalidate();
    } catch {
      showToast(t('fileManager.details.deleteFailed'), 'error');
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canEdit || !hasChanges) return;
    saveMutation.mutate();
  };

  const trimmedUrl = externalUrl.trim();
  const facts: Array<{ label: string; value: React.ReactNode }> = [
    { label: t('fileManager.details.size'), value: formatFileSize(file.file_size) },
    { label: t('fileManager.details.type'), value: file.file_type.toUpperCase() },
  ];
  if (details?.print_name) facts.push({ label: t('fileManager.details.printName'), value: details.print_name });
  if (details?.print_time_seconds) {
    facts.push({ label: t('fileManager.details.printTime'), value: formatDuration(details.print_time_seconds) });
  }
  if (details?.filament_used_grams) {
    facts.push({ label: t('fileManager.details.filament'), value: `${details.filament_used_grams.toFixed(1)} g` });
  }
  if (details?.sliced_for_model) facts.push({ label: t('fileManager.details.slicedFor'), value: details.sliced_for_model });
  if (details?.source_url) {
    facts.push({
      label: t('fileManager.details.source'),
      value: (
        <a
          href={details.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-bambu-green hover:underline inline-flex items-center gap-1 min-w-0"
        >
          <span className="truncate">{details.source_url}</span>
          <ExternalLink className="w-3 h-3 flex-shrink-0" />
        </a>
      ),
    });
  }
  facts.push({ label: t('fileManager.details.created'), value: formatDate(file.created_at) });
  facts.push({
    label: t('fileManager.details.modified'),
    value: formatDate(file.fs_modified_at ?? details?.updated_at ?? file.created_at),
  });

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="min-w-0">
            <p className="text-xs text-bambu-gray">{t('fileManager.details.title')}</p>
            <div className="flex items-center gap-2 min-w-0">
              <h2 className="text-lg font-semibold text-white truncate" title={file.filename}>
                {file.filename}
              </h2>
              <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded font-medium bg-bambu-dark-tertiary text-bambu-gray">
                {file.file_type.toUpperCase()}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors flex-shrink-0"
            aria-label={t('common.close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto flex-1">
          {/* Facts */}
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {facts.map((fact) => (
              <div key={fact.label} className="contents">
                <dt className="text-bambu-gray whitespace-nowrap">{fact.label}</dt>
                <dd className="text-white min-w-0 truncate">{fact.value}</dd>
              </div>
            ))}
          </dl>

          {/* Notes */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <StickyNote className="w-4 h-4 inline mr-1" />
              {t('fileManager.details.notes')}
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              disabled={!canEdit}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none resize-none disabled:opacity-60"
              placeholder={t('fileManager.details.notesPlaceholder')}
            />
          </div>

          {/* External link */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Link className="w-4 h-4 inline mr-1" />
              {t('fileManager.details.externalLink')}
            </label>
            <div className="flex items-center gap-2">
              <input
                type="url"
                value={externalUrl}
                onChange={(e) => setExternalUrl(e.target.value)}
                disabled={!canEdit}
                className="flex-1 min-w-0 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none disabled:opacity-60"
                placeholder={t('fileManager.details.externalLinkPlaceholder')}
              />
              {trimmedUrl && (
                <a
                  href={trimmedUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="p-2 rounded-lg text-bambu-gray hover:text-bambu-green hover:bg-bambu-dark transition-colors"
                  title={t('fileManager.details.openLink')}
                  aria-label={t('fileManager.details.openLink')}
                >
                  <ExternalLink className="w-4 h-4" />
                </a>
              )}
            </div>
          </div>

          {/* Photos */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Camera className="w-4 h-4 inline mr-1" />
              {t('fileManager.details.photos')}
            </label>
            <div className="flex flex-wrap gap-2">
              {photos.map((filename, index) => (
                <div key={filename} className="relative group">
                  <button
                    type="button"
                    onClick={() => setGalleryIndex(index)}
                    className="block w-20 h-20 rounded-lg overflow-hidden border border-bambu-dark-tertiary hover:border-bambu-green transition-colors"
                  >
                    <img
                      src={api.getLibraryFilePhotoUrl(file.id, filename)}
                      alt={t('fileManager.details.photos')}
                      className="w-full h-full object-cover"
                    />
                  </button>
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => handlePhotoDelete(filename)}
                      className="absolute -top-1 -right-1 p-1 bg-red-500 rounded-full can-hover:opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
                      title={t('fileManager.details.deletePhoto')}
                      aria-label={t('fileManager.details.deletePhoto')}
                    >
                      <Trash2 className="w-3 h-3 text-white" />
                    </button>
                  )}
                </div>
              ))}
              {canEdit && (
                <label
                  className="w-20 h-20 flex items-center justify-center border-2 border-dashed border-bambu-dark-tertiary rounded-lg cursor-pointer hover:border-bambu-green transition-colors"
                  title={t('fileManager.details.addPhoto')}
                >
                  <input
                    ref={photoInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    onChange={handlePhotoUpload}
                    className="hidden"
                    disabled={uploadingPhoto}
                    aria-label={t('fileManager.details.addPhoto')}
                  />
                  {uploadingPhoto ? (
                    <Loader2 className="w-6 h-6 text-bambu-gray animate-spin" />
                  ) : (
                    <Plus className="w-6 h-6 text-bambu-gray" />
                  )}
                </label>
              )}
              {photos.length === 0 && !canEdit && (
                <p className="text-xs text-bambu-gray">{t('fileManager.details.noPhotos')}</p>
              )}
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
              {t('common.close')}
            </Button>
            {canEdit && (
              <Button
                type="submit"
                variant="primary"
                className="flex-1"
                disabled={!hasChanges || saveMutation.isPending}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Save className="w-4 h-4" />
                )}
                {t('fileManager.details.save')}
              </Button>
            )}
          </div>
        </form>
      </div>

      {galleryIndex !== null && photos.length > 0 && (
        <PhotoGalleryModal
          archiveName={file.filename}
          photos={photos}
          initialIndex={galleryIndex}
          getPhotoUrl={(filename) => api.getLibraryFilePhotoUrl(file.id, filename)}
          onClose={() => setGalleryIndex(null)}
          onDelete={canEdit ? handlePhotoDelete : undefined}
        />
      )}
    </div>
  );
}
