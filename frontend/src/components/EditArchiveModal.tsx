import { useState, useEffect, useMemo, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Save, Tag, Camera, Trash2, Loader2, Plus, FolderKanban, Hash, Link, Weight } from 'lucide-react';
import { api } from '../api/client';
import type { Archive } from '../api/client';
import { Button } from './Button';
import { PrintLogTable } from './PrintLogTable';
import { invalidateArchiveAndProjectViews } from '../utils/projectQueries';
import { assignableProjects } from '../utils/projectTree';

// Keys for failure reasons - translated at render time.
// Exported so the Print Log per-row classification editor (#1687 part 4)
// can share the same vocabulary as the Archive Edit modal — the backend
// PATCH /print-log/{id} validator gates writes against this exact list.
export const FAILURE_REASON_KEYS = [
  'adhesionFailure',
  'spaghettiDetached',
  'layerShift',
  'cloggedNozzle',
  'filamentRunout',
  'warping',
  'stringing',
  'underExtrusion',
  'powerFailure',
  'userCancelled',
  'noStatusUpdate',
  'other',
] as const;

// Keys for archive statuses - translated at render time
const ARCHIVE_STATUS_KEYS = ['completed', 'failed', 'aborted', 'printing'] as const;

// Mirrors the API's own bound on filament_used_grams. Clamped here as well so
// the field cannot produce a request the backend would reject with a 422 —
// this modal has no error surface, so a refused save looks like nothing
// happened at all (#1820).
const MAX_FILAMENT_GRAMS = 100000;

interface EditArchiveModalProps {
  archive: Archive;
  onClose: () => void;
  existingTags?: string[];
}

export function EditArchiveModal({ archive, onClose, existingTags = [] }: EditArchiveModalProps) {
  const { t } = useTranslation();

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);
  const queryClient = useQueryClient();
  const [printName, setPrintName] = useState(archive.print_name || '');
  const [printerId, setPrinterId] = useState<number | null>(archive.printer_id);
  const [projectId, setProjectId] = useState<number | null>(archive.project_id ?? null);
  const [notes, setNotes] = useState(archive.notes || '');
  const [tags, setTags] = useState(archive.tags || '');
  // Failure reason is stored as a camelCase key (`filamentRunout`). Three older
  // writers stored other spellings -- English display labels from the backend,
  // this modal's own translated labels, and two prose sentences from the stale
  // archive paths -- and a startup migration folds all of them onto keys
  // (issue #2974). This reverse lookup is the belt to that migration's braces,
  // for a frontend running against a backend that has not restarted yet.
  //
  // A value it cannot resolve is kept rather than dropped. It used to fall back
  // to '', which did not merely look wrong: the empty selection was then saved
  // over the stored text, so opening the editor on an archive whose reason was
  // free text and pressing Save silently destroyed the classification. Anything
  // unrecognised now shows up as its own option (see `unmappedReason` below).
  const [failureReason, setFailureReason] = useState(() => {
    const raw = archive.failure_reason || '';
    if (!raw) return '';
    if ((FAILURE_REASON_KEYS as readonly string[]).includes(raw)) return raw;
    const match = FAILURE_REASON_KEYS.find(
      (k) => t(`editArchive.failureReasons.${k}`) === raw,
    );
    return match || raw;
  });

  // The stored value when it is not part of the vocabulary, so the dropdown can
  // offer it verbatim instead of appearing empty over a reason that exists.
  const unmappedReason =
    failureReason && !(FAILURE_REASON_KEYS as readonly string[]).includes(failureReason)
      ? failureReason
      : null;
  const [status, setStatus] = useState(archive.status);
  const [quantity, setQuantity] = useState(archive.quantity ?? 1);
  // Kept as a string so the field can be genuinely empty: a print archived
  // without its 3MF has no figure at all, and "" has to stay distinguishable
  // from 0 both on the way in and on the way out (#1820).
  const [filamentGrams, setFilamentGrams] = useState(
    archive.filament_used_grams != null ? String(archive.filament_used_grams) : ''
  );
  const [photos, setPhotos] = useState<string[]>(archive.photos || []);
  const [externalUrl, setExternalUrl] = useState(archive.external_url || '');
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [showTagSuggestions, setShowTagSuggestions] = useState(false);
  const tagInputRef = useRef<HTMLInputElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const blurTimeoutRef = useRef<number | null>(null);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    select: (rows) => [...rows].sort((a, b) => a.name.localeCompare(b.name)),
  });

  // Archived projects drop off the list, except the one this archive is
  // already filed under. That one has to stay: a select holding a value with
  // no matching option resets to the first one, so the field would read
  // "No project" for an archive that is in one (#2888).
  const projectOptions = useMemo(
    () => assignableProjects(projects ?? [], archive.project_id),
    [projects, archive.project_id],
  );

  // Fetch all tags using the dedicated API
  const { data: tagsData } = useQuery({
    queryKey: ['tags'],
    queryFn: api.getTags,
    enabled: existingTags.length === 0,
  });

  // Use existing tags prop if provided, otherwise use fetched tags
  const allTags = existingTags.length > 0
    ? existingTags
    : (tagsData?.map(t => t.name) || []);

  // Get current tags as array
  const currentTags = tags.split(',').map(t => t.trim()).filter(Boolean);

  // Get the text being typed after the last comma (for autocomplete filtering)
  const currentInput = tags.includes(',')
    ? tags.substring(tags.lastIndexOf(',') + 1).trim().toLowerCase()
    : tags.trim().toLowerCase();

  // Filter suggestions: not already added AND matches current input (if any)
  const tagSuggestions = allTags.filter(t =>
    !currentTags.includes(t) &&
    (currentInput === '' || t.toLowerCase().includes(currentInput))
  );

  // Add a tag (replaces any partial input with the selected tag)
  const addTag = (tag: string) => {
    // If there's partial input being typed, replace it with the selected tag
    // Otherwise, just append the tag
    let baseTags: string[];
    if (currentInput && !allTags.includes(currentInput)) {
      // User is typing a partial tag - replace it with the selected one
      baseTags = tags.includes(',')
        ? tags.substring(0, tags.lastIndexOf(',')).split(',').map(t => t.trim()).filter(Boolean)
        : [];
    } else {
      // No partial input or input is already a complete tag - append
      baseTags = currentTags;
    }

    if (!baseTags.includes(tag)) {
      const newTags = [...baseTags, tag].join(', ');
      setTags(newTags);
    }
    // Clear any pending blur timeout to prevent hiding suggestions
    if (blurTimeoutRef.current !== null) {
      clearTimeout(blurTimeoutRef.current);
    }
    tagInputRef.current?.focus();
  };

  // Remove a tag
  const removeTag = (tagToRemove: string) => {
    const newTags = currentTags.filter(t => t !== tagToRemove).join(', ');
    setTags(newTags);
  };

  const updateMutation = useMutation({
    mutationFn: (data: Parameters<typeof api.updateArchive>[1]) =>
      api.updateArchive(archive.id, data),
    onSuccess: () => {
      // This form can change the archive's project, so the project detail
      // views need refreshing too — not just the overview cards (#2731).
      invalidateArchiveAndProjectViews(queryClient);
      // Some of what this form writes is mirrored onto the archive's most
      // recent run — status and failure reason since #1444, filament grams
      // since #1820 — and the Print Log this modal renders at its top reads
      // the runs through their own query. Without this it serves the cached
      // pre-edit row, so the correction looks like it did not take.
      queryClient.invalidateQueries({ queryKey: ['archive-runs', archive.id] });
      onClose();
    },
  });

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploadingPhoto(true);
    try {
      const result = await api.uploadArchivePhoto(archive.id, file);
      setPhotos(result.photos);
      queryClient.invalidateQueries({ queryKey: ['archives'] });
    } catch (error) {
      console.error('Failed to upload photo:', error);
    } finally {
      setUploadingPhoto(false);
      if (photoInputRef.current) {
        photoInputRef.current.value = '';
      }
    }
  };

  const handlePhotoDelete = async (filename: string) => {
    try {
      const result = await api.deleteArchivePhoto(archive.id, filename);
      setPhotos(result.photos || []);
      queryClient.invalidateQueries({ queryKey: ['archives'] });
    } catch (error) {
      console.error('Failed to delete photo:', error);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // Build update data
    const updateData: Parameters<typeof api.updateArchive>[1] = {
      print_name: printName || undefined,
      printer_id: printerId,
      project_id: projectId,
      notes: notes || undefined,
      tags: tags || undefined,
      quantity: quantity,
      external_url: externalUrl || null,
    };

    // Only include status if changed
    if (status !== archive.status) {
      updateData.status = status;
    }

    // Sent only when the user actually touched it, so an ordinary save of an
    // archive that has its 3MF cannot overwrite the sliced figure with a
    // rounded one from the input.
    const trimmedGrams = filamentGrams.trim();
    const typedGrams = trimmedGrams === '' ? null : Number(trimmedGrams);
    // An empty field means "no figure" and clears the stored one; a field that
    // holds something unparseable (a lone decimal point, mid-typing) means the
    // user is not finished, and must not read as a clear. Clamped here as well
    // as on blur because Enter submits without the field losing focus.
    const gramsUnparseable = typedGrams !== null && !Number.isFinite(typedGrams);
    const parsedGrams = typedGrams === null ? null : Math.min(Math.max(typedGrams, 0), MAX_FILAMENT_GRAMS);
    const originalGrams = archive.filament_used_grams ?? null;
    if (!gramsUnparseable && parsedGrams !== originalGrams) {
      updateData.filament_used_grams = parsedGrams;
    }

    // Handle failure_reason based on status
    if (status === 'failed' || status === 'aborted') {
      updateData.failure_reason = failureReason || undefined;
    } else if (archive.status === 'failed' || archive.status === 'aborted') {
      // Clear failure_reason when changing from failed/aborted to another status
      updateData.failure_reason = null;
    }

    updateMutation.mutate(updateData);
  };

  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">{t('editArchive.title')}</h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto flex-1">
          {/* Print Log — per-run history pulled from PrintLogEntry (#1378). Shown
              first so users can see which runs contributed to the aggregate stats. */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('archives.runLog.title')}</label>
            <PrintLogTable archiveId={archive.id} />
          </div>

          {/* Print Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('editArchive.name')}</label>
            <input
              type="text"
              value={printName}
              onChange={(e) => setPrintName(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              placeholder={t('editArchive.namePlaceholder')}
            />
          </div>

          {/* Printer */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('editArchive.printer')}</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">{t('editArchive.noPrinter')}</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Project */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <FolderKanban className="w-4 h-4 inline mr-1" />
              {t('editArchive.project')}
            </label>
            <select
              value={projectId ?? ''}
              onChange={(e) => setProjectId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">{t('editArchive.noProject')}</option>
              {projectOptions.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Quantity - number of items printed */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Hash className="w-4 h-4 inline mr-1" />
              {t('editArchive.itemsPrinted')}
            </label>
            <input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              placeholder="1"
            />
            <p className="text-xs text-bambu-gray mt-1">
              {t('editArchive.itemsPrintedHelp')}
            </p>
          </div>

          {/* Filament used - the only way to supply a figure for a print that
              archived without its 3MF, which no rescan can repair (#1820). */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1" htmlFor="archive-filament-grams">
              <Weight className="w-4 h-4 inline mr-1" />
              {t('editArchive.filamentUsed')}
            </label>
            <input
              id="archive-filament-grams"
              type="text"
              inputMode="decimal"
              value={filamentGrams}
              // Text rather than number, and filtered on the way in. A number
              // input reports an empty string for anything the browser judges
              // malformed — including a decimal comma in a locale it doesn't
              // expect — which would read here as "the user cleared it" and
              // wipe a good figure. Filtering keeps what is displayed and what
              // would be sent the same thing, and keeps the value inside the
              // range the API accepts: this modal shows nothing at all when a
              // save is refused, so it must not be able to send a refusable one.
              onChange={(e) => {
                const next = e.target.value.replace(',', '.');
                if (next === '' || /^\d*\.?\d*$/.test(next)) setFilamentGrams(next);
              }}
              onBlur={() => setFilamentGrams((current) => {
                const parsed = Number(current);
                if (current === '' || !Number.isFinite(parsed)) return '';
                return String(Math.min(parsed, MAX_FILAMENT_GRAMS));
              })}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              placeholder={t('editArchive.filamentUsedPlaceholder')}
            />
            <p className="text-xs text-bambu-gray mt-1">
              {t('editArchive.filamentUsedHelp')}
            </p>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('editArchive.notes')}</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none resize-none"
              placeholder={t('editArchive.notesPlaceholder')}
            />
          </div>

          {/* External Link */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Link className="w-4 h-4 inline mr-1" />
              {t('editArchive.externalLink')}
            </label>
            <input
              type="url"
              value={externalUrl}
              onChange={(e) => setExternalUrl(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              placeholder="https://printables.com/model/..."
            />
            <p className="text-xs text-bambu-gray mt-1">
              {t('editArchive.externalLinkHelp')}
            </p>
          </div>

          {/* Tags */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('editArchive.tags')}</label>
            {/* Current tags as chips */}
            {currentTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mb-2">
                {currentTags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 px-2 py-0.5 bg-bambu-dark-tertiary rounded text-sm text-white"
                  >
                    <Tag className="w-3 h-3" />
                    {tag}
                    <button
                      type="button"
                      onClick={() => removeTag(tag)}
                      className="ml-0.5 text-bambu-gray hover:text-white"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            {/* Tag input with suggestions */}
            <div className="relative">
              <input
                ref={tagInputRef}
                type="text"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                onFocus={() => {
                  if (blurTimeoutRef.current !== null) {
                    clearTimeout(blurTimeoutRef.current);
                  }
                  setShowTagSuggestions(true);
                }}
                onBlur={() => {
                  blurTimeoutRef.current = window.setTimeout(() => setShowTagSuggestions(false), 200);
                }}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
                placeholder={currentTags.length > 0 ? t('editArchive.addMoreTags') : t('editArchive.tagsPlaceholder')}
              />
              {/* Suggestions dropdown */}
              {showTagSuggestions && tagSuggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-10 max-h-40 overflow-y-auto">
                  <div className="p-2 text-xs text-bambu-gray border-b border-bambu-dark-tertiary">
                    {currentInput ? t('editArchive.matchingTags', { query: currentInput }) : t('editArchive.existingTags')} {t('editArchive.clickToAdd')}
                  </div>
                  <div className="p-2 flex flex-wrap gap-1.5">
                    {tagSuggestions.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => addTag(tag)}
                        className="px-2 py-0.5 bg-bambu-dark-tertiary hover:bg-bambu-green/20 rounded text-sm text-bambu-gray hover:text-white transition-colors"
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Status */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">{t('editArchive.status')}</label>
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                // Clear failure reason when changing to completed
                if (e.target.value === 'completed') {
                  setFailureReason('');
                }
              }}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              {ARCHIVE_STATUS_KEYS.map((statusKey) => (
                <option key={statusKey} value={statusKey}>
                  {t(`editArchive.statuses.${statusKey}`)}
                </option>
              ))}
            </select>
          </div>

          {/* Failure Reason - only show for failed/aborted prints */}
          {(status === 'failed' || status === 'aborted') && (
            <div>
              <label htmlFor="failure-reason-select" className="block text-sm text-bambu-gray mb-1">{t('editArchive.failureReason')}</label>
              <select
                id="failure-reason-select"
                value={failureReason}
                onChange={(e) => setFailureReason(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              >
                <option value="">{t('editArchive.selectReason')}</option>
                {FAILURE_REASON_KEYS.map((reasonKey) => (
                  <option key={reasonKey} value={reasonKey}>
                    {t(`editArchive.failureReasons.${reasonKey}`)}
                  </option>
                ))}
                {/* A stored reason outside the vocabulary keeps its own option
                    so it stays visible and survives a save (issue #2974). */}
                {unmappedReason && (
                  <option value={unmappedReason}>{unmappedReason}</option>
                )}
              </select>
            </div>
          )}

          {/* Photos */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Camera className="w-4 h-4 inline mr-1" />
              {t('editArchive.photos')}
            </label>
            {/* Photo grid */}
            <div className="flex flex-wrap gap-2 mb-2">
              {photos.map((filename) => (
                <div key={filename} className="relative group">
                  <img
                    src={api.getArchivePhotoUrl(archive.id, filename)}
                    alt={t('editArchive.printResult')}
                    className="w-20 h-20 object-cover rounded-lg border border-bambu-dark-tertiary"
                  />
                  <button
                    type="button"
                    onClick={() => handlePhotoDelete(filename)}
                    className="absolute -top-1 -right-1 p-1 bg-red-500 rounded-full can-hover:opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity"
                  >
                    <Trash2 className="w-3 h-3 text-white" />
                  </button>
                </div>
              ))}
              {/* Upload button */}
              <label className="w-20 h-20 flex items-center justify-center border-2 border-dashed border-bambu-dark-tertiary rounded-lg cursor-pointer hover:border-bambu-green transition-colors">
                <input
                  ref={photoInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={handlePhotoUpload}
                  className="hidden"
                  disabled={uploadingPhoto}
                />
                {uploadingPhoto ? (
                  <Loader2 className="w-6 h-6 text-bambu-gray animate-spin" />
                ) : (
                  <Plus className="w-6 h-6 text-bambu-gray" />
                )}
              </label>
            </div>
            <p className="text-xs text-bambu-gray">{t('editArchive.photosHelp')}</p>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={updateMutation.isPending}
              className="flex-1"
            >
              <Save className="w-4 h-4" />
              {updateMutation.isPending ? t('common.saving') : t('common.save')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
