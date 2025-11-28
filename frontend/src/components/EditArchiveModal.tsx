import { useState, useEffect, useRef } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { X, Save, Tag, Camera, Trash2, Loader2, Plus } from 'lucide-react';
import { api } from '../api/client';
import type { Archive } from '../api/client';
import { Button } from './Button';

const FAILURE_REASONS = [
  'Adhesion failure',
  'Spaghetti / Detached',
  'Layer shift',
  'Clogged nozzle',
  'Filament runout',
  'Warping',
  'Stringing',
  'Under-extrusion',
  'Power failure',
  'User cancelled',
  'Other',
];

interface EditArchiveModalProps {
  archive: Archive;
  onClose: () => void;
  existingTags?: string[];
}

export function EditArchiveModal({ archive, onClose, existingTags = [] }: EditArchiveModalProps) {
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
  const [notes, setNotes] = useState(archive.notes || '');
  const [tags, setTags] = useState(archive.tags || '');
  const [failureReason, setFailureReason] = useState(archive.failure_reason || '');
  const [photos, setPhotos] = useState<string[]>(archive.photos || []);
  const [uploadingPhoto, setUploadingPhoto] = useState(false);
  const [showTagSuggestions, setShowTagSuggestions] = useState(false);
  const tagInputRef = useRef<HTMLInputElement>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const blurTimeoutRef = useRef<number | null>(null);

  const { data: printers } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Get all archives to extract existing tags if not provided
  const { data: archives } = useQuery({
    queryKey: ['archives'],
    queryFn: () => api.getArchives(undefined, 1000, 0),
    enabled: existingTags.length === 0,
  });

  // Extract unique tags from all archives
  const allTags = existingTags.length > 0
    ? existingTags
    : [...new Set(
        archives?.flatMap(a => a.tags?.split(',').map(t => t.trim()) || []).filter(Boolean) || []
      )].sort();

  // Get current tags as array
  const currentTags = tags.split(',').map(t => t.trim()).filter(Boolean);

  // Filter suggestions based on what's not already added
  const tagSuggestions = allTags.filter(t => !currentTags.includes(t));

  // Add a tag
  const addTag = (tag: string) => {
    if (!currentTags.includes(tag)) {
      const newTags = [...currentTags, tag].join(', ');
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
      queryClient.invalidateQueries({ queryKey: ['archives'] });
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
    updateMutation.mutate({
      print_name: printName || undefined,
      printer_id: printerId,
      notes: notes || undefined,
      tags: tags || undefined,
      failure_reason: archive.status === 'failed' ? (failureReason || undefined) : undefined,
    });
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
          <h2 className="text-lg font-semibold text-white">Edit Archive</h2>
          <button
            onClick={onClose}
            className="text-bambu-gray hover:text-white transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4 overflow-y-auto flex-1">
          {/* Print Name */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Name</label>
            <input
              type="text"
              value={printName}
              onChange={(e) => setPrintName(e.target.value)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              placeholder="Print name"
            />
          </div>

          {/* Printer */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Printer</label>
            <select
              value={printerId ?? ''}
              onChange={(e) => setPrinterId(e.target.value ? Number(e.target.value) : null)}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
            >
              <option value="">No printer</option>
              {printers?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Notes</label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none resize-none"
              placeholder="Add notes about this print..."
            />
          </div>

          {/* Tags */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">Tags</label>
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
                placeholder={currentTags.length > 0 ? "Add more tags..." : "Add tags..."}
              />
              {/* Suggestions dropdown */}
              {showTagSuggestions && tagSuggestions.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg z-10 max-h-40 overflow-y-auto">
                  <div className="p-2 text-xs text-bambu-gray border-b border-bambu-dark-tertiary">
                    Existing tags (click to add)
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

          {/* Failure Reason - only show for failed prints */}
          {archive.status === 'failed' && (
            <div>
              <label className="block text-sm text-bambu-gray mb-1">Failure Reason</label>
              <select
                value={failureReason}
                onChange={(e) => setFailureReason(e.target.value)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white focus:border-bambu-green focus:outline-none"
              >
                <option value="">Select reason...</option>
                {FAILURE_REASONS.map((reason) => (
                  <option key={reason} value={reason}>
                    {reason}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Photos */}
          <div>
            <label className="block text-sm text-bambu-gray mb-1">
              <Camera className="w-4 h-4 inline mr-1" />
              Photos of Printed Result
            </label>
            {/* Photo grid */}
            <div className="flex flex-wrap gap-2 mb-2">
              {photos.map((filename) => (
                <div key={filename} className="relative group">
                  <img
                    src={api.getArchivePhotoUrl(archive.id, filename)}
                    alt="Print result"
                    className="w-20 h-20 object-cover rounded-lg border border-bambu-dark-tertiary"
                  />
                  <button
                    type="button"
                    onClick={() => handlePhotoDelete(filename)}
                    className="absolute -top-1 -right-1 p-1 bg-red-500 rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
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
            <p className="text-xs text-bambu-gray">Click + to add photos of your printed result</p>
          </div>

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <Button
              type="button"
              variant="secondary"
              onClick={onClose}
              className="flex-1"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={updateMutation.isPending}
              className="flex-1"
            >
              <Save className="w-4 h-4" />
              {updateMutation.isPending ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
