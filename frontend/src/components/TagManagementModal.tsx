import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { X, Tag, Pencil, Trash2, Loader2, Search, Check, AlertTriangle } from 'lucide-react';
import { api } from '../api/client';
import type { TagInfo } from '../api/client';
import { Card, CardContent } from './Card';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface TagManagementModalProps {
  onClose: () => void;
}

export function TagManagementModal({ onClose }: TagManagementModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [search, setSearch] = useState('');
  const [editingTag, setEditingTag] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<'count' | 'name'>('count');

  // Close on Escape key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (editingTag) {
          setEditingTag(null);
        } else if (deleteConfirm) {
          setDeleteConfirm(null);
        } else {
          onClose();
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose, editingTag, deleteConfirm]);

  const { data: tags, isLoading } = useQuery({
    queryKey: ['tags'],
    queryFn: api.getTags,
  });

  const renameMutation = useMutation({
    mutationFn: ({ oldName, newName }: { oldName: string; newName: string }) =>
      api.renameTag(oldName, newName),
    onSuccess: (data, { oldName, newName }) => {
      queryClient.invalidateQueries({ queryKey: ['tags'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('tagManagement.renamed', { oldName, newName, count: data.affected }));
      setEditingTag(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('tagManagement.renameFailed'), 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => api.deleteTag(name),
    onSuccess: (data, name) => {
      queryClient.invalidateQueries({ queryKey: ['tags'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] });
      showToast(t('tagManagement.deleted', { name, count: data.affected }));
      setDeleteConfirm(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('tagManagement.deleteFailed'), 'error');
    },
  });

  const startEdit = (tag: TagInfo) => {
    setEditingTag(tag.name);
    setEditValue(tag.name);
    setDeleteConfirm(null);
  };

  const cancelEdit = () => {
    setEditingTag(null);
    setEditValue('');
  };

  const submitEdit = () => {
    if (!editingTag || !editValue.trim()) return;
    const newName = editValue.trim();
    if (newName === editingTag) {
      cancelEdit();
      return;
    }
    renameMutation.mutate({ oldName: editingTag, newName });
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      submitEdit();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      cancelEdit();
    }
  };

  const confirmDelete = (name: string) => {
    setDeleteConfirm(name);
    setEditingTag(null);
  };

  const executeDelete = () => {
    if (deleteConfirm) {
      deleteMutation.mutate(deleteConfirm);
    }
  };

  // Filter and sort tags
  const filteredTags = tags
    ?.filter(tag => tag.name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sortBy === 'count') {
        return b.count - a.count || a.name.localeCompare(b.name);
      }
      return a.name.localeCompare(b.name);
    });

  const totalUsage = tags?.reduce((sum, tag) => sum + tag.count, 0) || 0;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-lg max-h-[80vh] flex flex-col">
        <CardContent className="p-0 flex flex-col min-h-0">
          {/* Header */}
          <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
            <div className="flex items-center gap-2">
              <Tag className="w-5 h-5 text-bambu-green" />
              <h2 className="text-xl font-semibold text-white">{t('tagManagement.title')}</h2>
            </div>
            <button
              onClick={onClose}
              className="text-bambu-gray hover:text-white transition-colors"
            >
              <X className="w-5 h-5" />
            </button>
          </div>

          {/* Search and sort */}
          <div className="p-4 border-b border-bambu-dark-tertiary flex-shrink-0">
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  type="text"
                  placeholder={t('tagManagement.searchTags')}
                  className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <select
                className="px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as 'count' | 'name')}
              >
                <option value="count">{t('tagManagement.sortByCount')}</option>
                <option value="name">{t('tagManagement.sortByName')}</option>
              </select>
            </div>
            {tags && (
              <p className="text-xs text-bambu-gray mt-2">
                {t('tagManagement.summary', {
                  tags: t('tagManagement.tagCount', { count: tags.length }),
                  usages: t('tagManagement.usageCount', { count: totalUsage }),
                })}
              </p>
            )}
          </div>

          {/* Tags list */}
          <div className="flex-1 overflow-y-auto min-h-0 p-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="w-6 h-6 animate-spin text-bambu-gray" />
              </div>
            ) : !filteredTags?.length ? (
              <div className="text-center py-8 text-bambu-gray">
                {search ? t('tagManagement.noMatch') : t('tagManagement.noTags')}
              </div>
            ) : (
              <div className="space-y-2">
                {filteredTags.map((tag) => (
                  <div
                    key={tag.name}
                    className="flex items-center gap-2 p-2 rounded-lg bg-bambu-dark hover:bg-bambu-dark-tertiary transition-colors group"
                  >
                    {editingTag === tag.name ? (
                      // Edit mode
                      <div className="flex-1 flex items-center gap-2">
                        <input
                          type="text"
                          className="flex-1 px-2 py-1 bg-bambu-dark-tertiary border border-bambu-green rounded text-white text-sm focus:outline-none"
                          value={editValue}
                          onChange={(e) => setEditValue(e.target.value)}
                          onKeyDown={handleEditKeyDown}
                          autoFocus
                        />
                        <Button
                          size="sm"
                          variant="primary"
                          onClick={submitEdit}
                          disabled={!editValue.trim() || renameMutation.isPending}
                          className="p-1.5"
                        >
                          {renameMutation.isPending ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Check className="w-4 h-4" />
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={cancelEdit}
                          className="p-1.5"
                        >
                          <X className="w-4 h-4" />
                        </Button>
                      </div>
                    ) : deleteConfirm === tag.name ? (
                      // Delete confirmation
                      <div className="flex-1 flex items-center gap-2">
                        <AlertTriangle className="w-4 h-4 text-yellow-600 dark:text-yellow-400 flex-shrink-0" />
                        <span className="text-sm text-bambu-gray-light flex-1">
                          {t('tagManagement.confirmDelete', { name: tag.name, count: tag.count })}
                        </span>
                        <Button
                          size="sm"
                          variant="danger"
                          onClick={executeDelete}
                          disabled={deleteMutation.isPending}
                          className="p-1.5"
                        >
                          {deleteMutation.isPending ? (
                            <Loader2 className="w-4 h-4 animate-spin" />
                          ) : (
                            <Trash2 className="w-4 h-4" />
                          )}
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setDeleteConfirm(null)}
                          className="p-1.5"
                        >
                          <X className="w-4 h-4" />
                        </Button>
                      </div>
                    ) : (
                      // Normal display
                      <>
                        <Tag className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                        <span className="text-white flex-1 truncate">{tag.name}</span>
                        <span className="px-2 py-0.5 rounded-full bg-bambu-dark-tertiary text-bambu-gray text-xs">
                          {tag.count}
                        </span>
                        <div className="flex items-center gap-1 can-hover:opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
                          <button
                            onClick={() => startEdit(tag)}
                            className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                            title={t('tagManagement.renameTag')}
                          >
                            <Pencil className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => confirmDelete(tag.name)}
                            className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-red-700 dark:hover:text-red-400 transition-colors"
                            title={t('tagManagement.deleteTag')}
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex gap-3 p-4 border-t border-bambu-dark-tertiary flex-shrink-0">
            <Button variant="secondary" onClick={onClose} className="flex-1">
              {t('common.close')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
