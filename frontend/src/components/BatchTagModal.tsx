import { useState, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Tag, Plus, Loader2 } from 'lucide-react';
import { api } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface BatchTagModalProps {
  selectedIds: number[];
  existingTags: string[];
  onClose: () => void;
}

export function BatchTagModal({ selectedIds, existingTags, onClose }: BatchTagModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [newTag, setNewTag] = useState('');
  const [selectedTags, setSelectedTags] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<'add' | 'remove'>('add');

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  const batchTagMutation = useMutation({
    mutationFn: async () => {
      const tagsArray = Array.from(selectedTags);
      let successCount = 0;

      // Process sequentially to avoid SQLite database locks
      for (const id of selectedIds) {
        try {
          const archive = await api.getArchive(id);
          const currentTags = archive.tags ? archive.tags.split(',').map(tag => tag.trim()).filter(Boolean) : [];

          let newTags: string[];
          if (mode === 'add') {
            // Add tags that aren't already present
            newTags = [...new Set([...currentTags, ...tagsArray])];
          } else {
            // Remove selected tags
            newTags = currentTags.filter(tag => !selectedTags.has(tag));
          }

          await api.updateArchive(id, { tags: newTags.join(', ') });
          successCount++;
        } catch (err) {
          console.error(`Failed to update archive ${id}:`, err);
          throw new Error(
            t('batchTag.failedOnArchive', {
              id,
              error: err instanceof Error ? err.message : t('common.unknownError'),
            }),
          );
        }
      }
      return { count: successCount, mode, tags: tagsArray };
    },
    onSuccess: ({ count, mode, tags }) => {
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      const tagsText = t('batchTag.tagCount', { count: tags.length });
      const archivesText = t('batchTag.archiveCount', { count });
      showToast(
        mode === 'add'
          ? t('batchTag.addedToast', { tags: tagsText, archives: archivesText })
          : t('batchTag.removedToast', { tags: tagsText, archives: archivesText }),
      );
      onClose();
    },
    onError: (error: Error) => {
      showToast(error.message || t('batchTag.updateFailed'), 'error');
    },
  });

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) {
        next.delete(tag);
      } else {
        next.add(tag);
      }
      return next;
    });
  };

  const addNewTag = () => {
    if (newTag.trim() && !selectedTags.has(newTag.trim())) {
      setSelectedTags((prev) => new Set([...prev, newTag.trim()]));
      setNewTag('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addNewTag();
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardContent className="p-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-2">
              <Tag className="w-5 h-5 text-bambu-green" />
              <h2 className="text-xl font-semibold text-white">
                {mode === 'add' ? t('batchTag.addTags') : t('batchTag.removeTags')}
              </h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Content */}
          <div className="p-4 space-y-4">
            <p className="text-sm text-bambu-gray">
              {mode === 'add'
                ? t('batchTag.addDescription', { count: selectedIds.length })
                : t('batchTag.removeDescription', { count: selectedIds.length })}
            </p>

            {/* Mode toggle */}
            <div className="flex gap-2">
              <Button
                size="sm"
                variant={mode === 'add' ? 'primary' : 'secondary'}
                onClick={() => setMode('add')}
              >
                {t('batchTag.addTags')}
              </Button>
              <Button
                size="sm"
                variant={mode === 'remove' ? 'primary' : 'secondary'}
                onClick={() => setMode('remove')}
              >
                {t('batchTag.removeTags')}
              </Button>
            </div>

            {/* New tag input (only for add mode) */}
            {mode === 'add' && (
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder={t('batchTag.placeholders.newTag')}
                  className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                  value={newTag}
                  onChange={(e) => setNewTag(e.target.value)}
                  onKeyDown={handleKeyDown}
                />
                <Button size="sm" variant="secondary" onClick={addNewTag} disabled={!newTag.trim()}>
                  <Plus className="w-4 h-4" />
                </Button>
              </div>
            )}

            {/* Existing tags */}
            {existingTags.length > 0 && (
              <div>
                <p className="text-xs text-bambu-gray mb-2">{t('batchTag.existingTags')}</p>
                <div className="flex flex-wrap gap-2">
                  {existingTags.map((tag) => (
                    <button
                      key={tag}
                      onClick={() => toggleTag(tag)}
                      className={`px-2 py-1 rounded text-sm transition-colors ${
                        selectedTags.has(tag)
                          ? 'bg-bambu-green text-white'
                          : 'bg-bambu-dark-tertiary text-bambu-gray-light hover:bg-bambu-dark'
                      }`}
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Selected tags preview */}
            {selectedTags.size > 0 && (
              <div>
                <p className="text-xs text-bambu-gray mb-2">
                  {mode === 'add' ? t('batchTag.tagsToAdd') : t('batchTag.tagsToRemove')}
                </p>
                <div className="flex flex-wrap gap-2">
                  {Array.from(selectedTags).map((tag) => (
                    <span
                      key={tag}
                      className={`px-2 py-1 rounded text-sm flex items-center gap-1 ${
                        mode === 'add' ? 'bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-400' : 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400'
                      }`}
                    >
                      {tag}
                      <button onClick={() => toggleTag(tag)} className="hover:opacity-70">
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              {t('common.cancel')}
            </Button>
            <Button
              onClick={() => batchTagMutation.mutate()}
              disabled={selectedTags.size === 0 || batchTagMutation.isPending}
              className="flex-1"
            >
              {batchTagMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('batchTag.processing')}
                </>
              ) : (
                <>
                  <Tag className="w-4 h-4" />
                  {mode === 'add' ? t('batchTag.addTags') : t('batchTag.removeTags')}
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
