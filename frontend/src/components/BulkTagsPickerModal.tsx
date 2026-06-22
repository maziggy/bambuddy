import { useState, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Tag, Loader2, Plus, X } from 'lucide-react';

import { api, type LibraryTag } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { libraryTagsQueryKey } from '../utils/libraryTagsQuery';

interface BulkTagsPickerModalProps {
  open: boolean;
  fileIds: number[];
  onClose: () => void;
}

type Action = 'add' | 'remove';

/**
 * Multi-file tag application modal (#1268). Opens from the File Manager's
 * multi-select toolbar. Checkbox-list of catalog tags + inline "create new" so
 * the user doesn't have to leave the flow to add a tag they forgot to make.
 *
 * Replace mode is omitted from the UI — it's a destructive op that the user
 * would rarely want for arbitrary multi-selections. The API still exposes it
 * for callers that need it (e.g. a future bulk-edit screen).
 */
export function BulkTagsPickerModal({ open, fileIds, onClose }: BulkTagsPickerModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [action, setAction] = useState<Action>('add');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState('');
  const [newTagName, setNewTagName] = useState('');

  // Reset state on close so re-opening the modal doesn't keep stale selection.
  useEffect(() => {
    if (!open) {
      setAction('add');
      setSelected(new Set());
      setFilter('');
      setNewTagName('');
    }
  }, [open]);

  const { data: tags = [], isLoading } = useQuery({
    queryKey: libraryTagsQueryKey,
    queryFn: api.getLibraryTags,
    enabled: open,
  });

  const filteredTags = useMemo<LibraryTag[]>(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return tags;
    return tags.filter((t) => t.name.toLowerCase().includes(q));
  }, [tags, filter]);

  const toggleTag = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const createTagMutation = useMutation({
    mutationFn: (name: string) => api.createLibraryTag(name),
    onSuccess: (tag) => {
      setSelected((prev) => new Set(prev).add(tag.id));
      setNewTagName('');
      queryClient.invalidateQueries({ queryKey: libraryTagsQueryKey });
    },
    onError: (err: Error) => {
      showToast(err.message || t('fileManager.tags.saveFailed'), 'error');
    },
  });

  const applyMutation = useMutation({
    mutationFn: () =>
      api.bulkAssignLibraryTags(fileIds, Array.from(selected), action),
    onSuccess: (result) => {
      showToast(
        action === 'add'
          ? t('fileManager.tags.applyAddSuccess', {
              count: result.associations_added,
              files: result.files_updated,
            })
          : t('fileManager.tags.applyRemoveSuccess', {
              count: result.associations_removed,
              files: result.files_updated,
            }),
        'success',
      );
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: libraryTagsQueryKey });
      onClose();
    },
    onError: (err: Error) => {
      showToast(err.message || t('fileManager.tags.applyFailed'), 'error');
    },
  });

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !applyMutation.isPending && !createTagMutation.isPending) {
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose, applyMutation.isPending, createTagMutation.isPending]);

  if (!open) return null;

  const createDisabled =
    !newTagName.trim() ||
    createTagMutation.isPending ||
    tags.some((tg) => tg.name.toLowerCase() === newTagName.trim().toLowerCase());

  const titleId = 'bulk-tags-picker-title';

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={() => !applyMutation.isPending && onClose()} />
      <div
        className="relative w-full max-w-md mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex items-center justify-between gap-4 px-5 py-4 border-b border-bambu-dark-tertiary">
          <h3 id={titleId} className="text-base font-semibold text-white flex items-center gap-2">
            <Tag className="w-4 h-4 text-bambu-green" />
            {t('fileManager.tags.bulkTitle', { count: fileIds.length })}
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

        <div className="px-5 py-3 border-b border-bambu-dark-tertiary flex gap-4 text-sm">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="bulk-action"
              checked={action === 'add'}
              onChange={() => setAction('add')}
              className="accent-bambu-green"
            />
            <span className="text-white">{t('fileManager.tags.actionAdd')}</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="bulk-action"
              checked={action === 'remove'}
              onChange={() => setAction('remove')}
              className="accent-bambu-green"
            />
            <span className="text-white">{t('fileManager.tags.actionRemove')}</span>
          </label>
        </div>

        <div className="px-5 py-3 border-b border-bambu-dark-tertiary">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('fileManager.tags.searchPlaceholder')}
            className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
          />
        </div>

        <div className="overflow-y-auto flex-1 min-h-[8rem] max-h-[24rem]">
          {isLoading ? (
            <div className="flex items-center justify-center py-12 text-bambu-gray">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              {t('common.loading')}
            </div>
          ) : filteredTags.length === 0 ? (
            <div className="py-12 text-center text-bambu-gray text-sm">
              {tags.length === 0 ? t('fileManager.tags.empty') : t('fileManager.tags.noMatches')}
            </div>
          ) : (
            <ul className="divide-y divide-bambu-dark-tertiary/40">
              {filteredTags.map((tg) => (
                <li key={tg.id}>
                  <label className="flex items-center gap-3 px-5 py-2 hover:bg-bambu-dark-tertiary/30 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={selected.has(tg.id)}
                      onChange={() => toggleTag(tg.id)}
                      className="accent-bambu-green"
                    />
                    <span className="text-sm text-white truncate flex-1">{tg.name}</span>
                    <span className="text-xs text-bambu-gray">{tg.file_count}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        {action === 'add' && (
          <div className="px-5 py-3 border-t border-bambu-dark-tertiary flex gap-2">
            <input
              type="text"
              value={newTagName}
              onChange={(e) => setNewTagName(e.target.value)}
              placeholder={t('fileManager.tags.createPlaceholder')}
              maxLength={64}
              className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !createDisabled) {
                  e.preventDefault();
                  createTagMutation.mutate(newTagName.trim());
                }
              }}
            />
            <Button
              type="button"
              variant="secondary"
              onClick={() => createTagMutation.mutate(newTagName.trim())}
              disabled={createDisabled}
            >
              {createTagMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
              {t('fileManager.tags.createButton')}
            </Button>
          </div>
        )}

        <div className="px-5 py-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose} disabled={applyMutation.isPending}>
            {t('common.cancel')}
          </Button>
          <Button
            type="button"
            onClick={() => applyMutation.mutate()}
            disabled={selected.size === 0 || applyMutation.isPending || fileIds.length === 0}
          >
            {applyMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
            {action === 'add' ? t('fileManager.tags.applyAdd') : t('fileManager.tags.applyRemove')}
          </Button>
        </div>
      </div>
    </div>
  );
}
