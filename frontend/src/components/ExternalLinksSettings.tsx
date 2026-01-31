import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link2, Plus, Pencil, Trash2, GripVertical, Loader2, ExternalLink as ExternalLinkIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { ExternalLink } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { AddExternalLinkModal } from './AddExternalLinkModal';
import { ConfirmModal } from './ConfirmModal';
import { getIconByName } from './IconPicker';

export function ExternalLinksSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingLink, setEditingLink] = useState<ExternalLink | null>(null);
  const [deletingLink, setDeletingLink] = useState<ExternalLink | null>(null);
  const [draggedId, setDraggedId] = useState<number | null>(null);

  // Fetch external links
  const { data: links, isLoading } = useQuery({
    queryKey: ['external-links'],
    queryFn: api.getExternalLinks,
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteExternalLink(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['external-links'] });
    },
  });

  // Reorder mutation
  const reorderMutation = useMutation({
    mutationFn: (ids: number[]) => api.reorderExternalLinks(ids),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['external-links'] });
    },
  });

  const handleDragStart = (e: React.DragEvent, id: number) => {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  };

  const handleDrop = (e: React.DragEvent, targetId: number) => {
    e.preventDefault();
    if (draggedId === null || draggedId === targetId || !links) return;

    const currentIds = links.map((l) => l.id);
    const draggedIndex = currentIds.indexOf(draggedId);
    const targetIndex = currentIds.indexOf(targetId);

    if (draggedIndex === -1 || targetIndex === -1) return;

    // Reorder
    const newIds = [...currentIds];
    newIds.splice(draggedIndex, 1);
    newIds.splice(targetIndex, 0, draggedId);

    reorderMutation.mutate(newIds);
    setDraggedId(null);
  };

  const handleDelete = (link: ExternalLink) => {
    setDeletingLink(link);
  };

  const confirmDelete = () => {
    if (deletingLink) {
      deleteMutation.mutate(deletingLink.id);
      setDeletingLink(null);
    }
  };

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Link2 className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">{t('settings.sidebarLinks')}</h2>
            </div>
            <Button size="sm" onClick={() => setShowAddModal(true)}>
              <Plus className="w-4 h-4" />
              {t('settings.addLink')}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-bambu-gray mb-4">
            {t('settings.sidebarLinksDescription')}
          </p>

          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : links && links.length > 0 ? (
            <div className="space-y-2">
              {links.map((link) => {
                const Icon = getIconByName(link.icon);
                return (
                  <div
                    key={link.id}
                    draggable
                    onDragStart={(e) => handleDragStart(e, link.id)}
                    onDragOver={handleDragOver}
                    onDrop={(e) => handleDrop(e, link.id)}
                    className={`flex items-center gap-3 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary transition-colors ${
                      draggedId === link.id ? 'opacity-50' : ''
                    }`}
                  >
                    <GripVertical className="w-6 h-6 md:w-4 md:h-4 text-bambu-gray cursor-grab flex-shrink-0" />
                    <div className="p-2 rounded-lg bg-bambu-dark-tertiary text-bambu-gray">
                      <Icon className="w-4 h-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-white font-medium truncate">{link.name}</span>
                        <ExternalLinkIcon className="w-3 h-3 text-bambu-gray flex-shrink-0" />
                      </div>
                      <span className="text-sm text-bambu-gray truncate block">{link.url}</span>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <button
                        onClick={() => setEditingLink(link)}
                        className="p-2 rounded-lg hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
                        title={t('common.edit')}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDelete(link)}
                        disabled={deleteMutation.isPending}
                        className="p-2 rounded-lg hover:bg-red-500/20 text-bambu-gray hover:text-red-400 transition-colors disabled:opacity-50"
                        title={t('common.delete')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-center py-8 text-bambu-gray">
              <Link2 className="w-8 h-8 mx-auto mb-2 opacity-50" />
              <p>{t('settings.noExternalLinks')}</p>
              <p className="text-sm">{t('settings.clickAddLink')}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Add/Edit Modal */}
      {(showAddModal || editingLink) && (
        <AddExternalLinkModal
          link={editingLink}
          onClose={() => {
            setShowAddModal(false);
            setEditingLink(null);
          }}
        />
      )}

      {/* Delete Confirmation Modal */}
      {deletingLink && (
        <ConfirmModal
          title={t('settings.deleteLink')}
          message={t('settings.deleteLinkConfirm', { name: deletingLink.name })}
          confirmText={t('common.delete')}
          cancelText={t('common.cancel')}
          variant="danger"
          onConfirm={confirmDelete}
          onCancel={() => setDeletingLink(null)}
        />
      )}
    </>
  );
}
