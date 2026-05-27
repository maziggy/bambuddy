import { useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Copy, Trash2, Loader2, ShieldCheck } from 'lucide-react';
import { api } from '../api/client';
import type { Archive } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface DeduplicateModalProps {
  archives: Archive[];
  onClose: () => void;
}

export function DeduplicateModal({ archives, onClose }: DeduplicateModalProps) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  // Build groups: originals and their duplicates, keyed by original id
  const originals = archives.filter(a => a.duplicate_sequence === 0 && a.duplicate_count > 0);
  const duplicatesByOriginal = new Map<number, Archive[]>();
  for (const archive of archives) {
    if (archive.duplicate_sequence > 0 && archive.original_archive_id !== null) {
      const list = duplicatesByOriginal.get(archive.original_archive_id) ?? [];
      list.push(archive);
      duplicatesByOriginal.set(archive.original_archive_id, list);
    }
  }
  const totalDuplicates = [...duplicatesByOriginal.values()].reduce((sum, d) => sum + d.length, 0);

  const deduplicateMutation = useMutation({
    mutationFn: api.deduplicateArchives,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(
        `Removed ${data.deleted} duplicate${data.deleted !== 1 ? 's' : ''}`,
        'success',
      );
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || 'Failed to remove duplicates', 'error');
    },
  });

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4"
      onClick={onClose}
    >
      <Card
        className="w-full max-w-lg max-h-[80vh] flex flex-col"
        onClick={(e: React.MouseEvent) => e.stopPropagation()}
      >
        <CardContent className="p-0 flex flex-col min-h-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
            <div className="flex items-center gap-2">
              <Copy className="w-5 h-5 text-purple-400" />
              <h2 className="text-xl font-semibold text-white">Remove Duplicates</h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Summary */}
          <div className="p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
            {originals.length === 0 ? (
              <p className="text-bambu-gray text-sm">No duplicates found in your archive.</p>
            ) : (
              <p className="text-bambu-gray text-sm">
                Found{' '}
                <span className="text-white font-medium">
                  {totalDuplicates} duplicate{totalDuplicates !== 1 ? 's' : ''}
                </span>{' '}
                across{' '}
                <span className="text-white font-medium">
                  {originals.length} print{originals.length !== 1 ? 's' : ''}
                </span>
                . The earliest archive of each will be kept.
              </p>
            )}
          </div>

          {/* Duplicate groups */}
          <div className="flex-1 overflow-y-auto min-h-0 p-4">
            {originals.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 gap-2 text-bambu-gray">
                <ShieldCheck className="w-8 h-8" />
                <p className="text-sm">Your archive is clean</p>
              </div>
            ) : (
              <div className="space-y-3">
                {originals.map((original) => {
                  const dupes = duplicatesByOriginal.get(original.id) ?? [];
                  return (
                    <div
                      key={original.id}
                      className="rounded-lg bg-bambu-dark border border-bambu-dark-tertiary overflow-hidden"
                    >
                      {/* Original — kept */}
                      <div className="flex items-center gap-3 px-3 py-2 border-b border-bambu-dark-tertiary">
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-green-500/20 text-green-400 flex-shrink-0">
                          Keep
                        </span>
                        <span className="text-white text-sm truncate flex-1">
                          {original.print_name || original.filename}
                        </span>
                        <span className="text-bambu-gray text-xs flex-shrink-0">
                          {new Date(original.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      {/* Duplicates — deleted */}
                      {dupes.map((dupe) => (
                        <div
                          key={dupe.id}
                          className="flex items-center gap-3 px-3 py-2 border-b border-bambu-dark-tertiary last:border-b-0"
                        >
                          <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-500/20 text-red-400 flex-shrink-0">
                            Delete
                          </span>
                          <span className="text-bambu-gray text-sm truncate flex-1">
                            {dupe.print_name || dupe.filename}
                          </span>
                          <span className="text-bambu-gray text-xs flex-shrink-0">
                            {new Date(dupe.created_at).toLocaleDateString()}
                          </span>
                        </div>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
            <Button
              variant="secondary"
              onClick={onClose}
              className="flex-1"
              disabled={deduplicateMutation.isPending}
            >
              Cancel
            </Button>
            {originals.length > 0 && (
              <Button
                variant="danger"
                onClick={() => deduplicateMutation.mutate()}
                className="flex-1"
                disabled={deduplicateMutation.isPending}
              >
                {deduplicateMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Removing...
                  </>
                ) : (
                  <>
                    <Trash2 className="w-4 h-4 mr-2" />
                    Remove {totalDuplicates} Duplicate{totalDuplicates !== 1 ? 's' : ''}
                  </>
                )}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
