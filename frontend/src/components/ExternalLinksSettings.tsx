import { useMemo, useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Eye, EyeOff, Link2, Plus, Pencil, Trash2, GripVertical, Loader2, ExternalLink as ExternalLinkIcon, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../api/client';
import type { ExternalLink } from '../api/client';
import { Card, CardContent, CardHeader } from './Card';
import { Button } from './Button';
import { Toggle } from './Toggle';
import { AddExternalLinkModal } from './AddExternalLinkModal';
import { ConfirmModal } from './ConfirmModal';
import { getIconByName } from './IconPicker';
import { defaultNavItems } from './Layout';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import {
  getHiddenSidebarSystemItemIds,
  getSidebarOrder,
  saveHiddenSidebarSystemItemIds,
  saveSidebarOrder,
} from '../utils/sidebarLayout';

type SidebarLayoutItem =
  | { type: 'system'; id: string; navItem: typeof defaultNavItems[number] }
  | { type: 'external'; id: string; link: ExternalLink };

export function ExternalLinksSettings() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { authEnabled, hasPermission } = useAuth();
  const { showToast } = useToast();
  const [showAddModal, setShowAddModal] = useState(false);
  const [editingLink, setEditingLink] = useState<ExternalLink | null>(null);
  const [deletingLink, setDeletingLink] = useState<ExternalLink | null>(null);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [dragOverId, setDragOverId] = useState<string | null>(null);
  const [hiddenSystemItemIds, setHiddenSystemItemIds] = useState<string[]>(getHiddenSidebarSystemItemIds);
  const [sidebarOrder, setSidebarOrder] = useState<string[]>(() => getSidebarOrder(defaultNavItems.map(i => i.id)));

  const { data: links, isLoading } = useQuery({
    queryKey: ['external-links'],
    queryFn: api.getExternalLinks,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteExternalLink(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['external-links'] });
    },
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
    enabled: authEnabled && hasPermission('settings:update'),
  });

  const updateDefaultSidebarMutation = useMutation({
    mutationFn: (defaultSidebarOrder: string) => api.updateSettings({ default_sidebar_order: defaultSidebarOrder }),
    onSuccess: (_data, defaultSidebarOrder) => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['default-sidebar-order'] });
      showToast(
        defaultSidebarOrder
          ? t('settings.sidebarDefaultSet')
          : t('settings.sidebarDefaultCleared'),
        'success'
      );
    },
    onError: () => {
      showToast(t('settings.sidebarDefaultFailed'), 'error');
    },
  });

  const layoutItems = useMemo<SidebarLayoutItem[]>(() => {
    const navItemsMap = new Map(defaultNavItems.map(item => [item.id, item]));
    const externalLinksMap = new Map((links || []).map(link => [`ext-${link.id}`, link]));
    const result: SidebarLayoutItem[] = [];
    const seen = new Set<string>();

    const addItem = (id: string) => {
      if (seen.has(id)) return;

      const navItem = navItemsMap.get(id);
      if (navItem) {
        result.push({ type: 'system', id, navItem });
        seen.add(id);
        return;
      }

      const link = externalLinksMap.get(id);
      if (link) {
        result.push({ type: 'external', id, link });
        seen.add(id);
      }
    };

    sidebarOrder.forEach(addItem);
    defaultNavItems.forEach(item => addItem(item.id));
    (links || []).forEach(link => addItem(`ext-${link.id}`));

    return result;
  }, [links, sidebarOrder]);

  const persistSidebarOrder = (order: string[]) => {
    setSidebarOrder(order);
    saveSidebarOrder(order);
  };

  const handleDragStart = (e: React.DragEvent, id: string) => {
    setDraggedId(id);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
  };

  const handleDragOver = (e: React.DragEvent, id: string) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDragOverId(id);
  };

  const handleDrop = (e: React.DragEvent, targetId: string) => {
    e.preventDefault();
    if (draggedId === null || draggedId === targetId) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }

    const currentOrder = layoutItems.map(item => item.id);
    const draggedIndex = currentOrder.indexOf(draggedId);
    const targetIndex = currentOrder.indexOf(targetId);

    if (draggedIndex === -1 || targetIndex === -1) {
      setDraggedId(null);
      setDragOverId(null);
      return;
    }

    currentOrder.splice(draggedIndex, 1);
    currentOrder.splice(targetIndex, 0, draggedId);
    persistSidebarOrder(currentOrder);

    setDraggedId(null);
    setDragOverId(null);
  };

  const handleDragEnd = () => {
    setDraggedId(null);
    setDragOverId(null);
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

  const resetSidebarLayout = () => {
    const resetOrder = [
      ...defaultNavItems.map(item => item.id),
      ...(links || []).map(link => `ext-${link.id}`),
    ];

    setHiddenSystemItemIds([]);
    setSidebarOrder(resetOrder);
    saveHiddenSidebarSystemItemIds([]);
    saveSidebarOrder(resetOrder);
  };

  const handleToggleDefaultSidebarOrder = (enabled: boolean) => {
    const currentOrder = layoutItems.map(item => item.id);
    updateDefaultSidebarMutation.mutate(enabled ? JSON.stringify({
      order: currentOrder,
      hiddenSystemItemIds,
    }) : '');
  };

  const toggleSystemItemVisibility = (id: string) => {
    if (id === 'settings') return;

    const isHidden = hiddenSystemItemIds.includes(id);
    const nextIds = isHidden
      ? hiddenSystemItemIds.filter(hiddenId => hiddenId !== id)
      : [...hiddenSystemItemIds, id];

    setHiddenSystemItemIds(nextIds);
    saveHiddenSidebarSystemItemIds(nextIds);
  };

  const canSetDefaultSidebarOrder = authEnabled && hasPermission('settings:update');
  const isDefaultSidebarEnabled = !!settings?.default_sidebar_order;

  return (
    <>
      <Card id="card-sidebar-links">
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-y-2 gap-x-3">
            <div className="flex items-center gap-2">
              <Link2 className="w-5 h-5 text-bambu-green" />
              <h2 className="text-lg font-semibold text-white">{t('externalLinks.sidebarLayout')}</h2>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {canSetDefaultSidebarOrder && (
                <label className="flex items-center gap-2 text-sm text-bambu-gray">
                  <span>{t('settings.setDefault')}</span>
                  <Toggle
                    checked={isDefaultSidebarEnabled}
                    onChange={handleToggleDefaultSidebarOrder}
                    disabled={updateDefaultSidebarMutation.isPending}
                  />
                </label>
              )}
              <Button variant="secondary" size="sm" onClick={resetSidebarLayout} className="whitespace-nowrap">
                <RotateCcw className="w-4 h-4" />
                {t('settings.reset')}
              </Button>
              <Button size="sm" onClick={() => setShowAddModal(true)} className="whitespace-nowrap">
                <Plus className="w-4 h-4" />
                Add Link
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-bambu-gray mb-4">
            {t('externalLinks.sidebarLayoutDescription')}
          </p>

          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
            </div>
          ) : (
            <div className="space-y-2">
              {layoutItems.map((item) => {
                const isHidden = item.type === 'system' && hiddenSystemItemIds.includes(item.id);
                const isSettings = item.id === 'settings';

                if (item.type === 'system') {
                  const Icon = item.navItem.icon;
                  return (
                    <div
                      key={item.id}
                      draggable
                      onDragStart={(e) => handleDragStart(e, item.id)}
                      onDragOver={(e) => handleDragOver(e, item.id)}
                      onDragLeave={() => setDragOverId(null)}
                      onDrop={(e) => handleDrop(e, item.id)}
                      onDragEnd={handleDragEnd}
                      className={`relative flex items-center gap-3 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary transition-colors ${
                        draggedId === item.id ? 'opacity-50' : isHidden ? 'opacity-60' : ''
                      } ${
                        dragOverId === item.id && draggedId !== item.id
                          ? 'before:absolute before:left-3 before:right-3 before:top-0 before:h-0.5 before:bg-bambu-green'
                          : ''
                      }`}
                    >
                      <GripVertical className="w-6 h-6 md:w-4 md:h-4 text-bambu-gray cursor-grab flex-shrink-0" />
                      <div className="p-2 rounded-lg bg-bambu-dark-tertiary text-bambu-gray">
                        <Icon className="w-4 h-4" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <span className="text-white font-medium truncate block">{t(item.navItem.labelKey)}</span>
                        <span className="text-sm text-bambu-gray truncate block">
                          {isSettings
                            ? t('externalLinks.requiredInSidebar')
                            : isHidden
                              ? t('externalLinks.hiddenFromSidebar')
                              : t('externalLinks.visibleInSidebar')}
                        </span>
                      </div>
                      <button
                        onClick={() => toggleSystemItemVisibility(item.id)}
                        disabled={isSettings}
                        className="p-2 rounded-lg hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors flex-shrink-0 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:hover:text-bambu-gray"
                        title={isSettings ? t('externalLinks.settingsCannotBeHidden') : isHidden ? t('externalLinks.showPage') : t('externalLinks.hidePage')}
                        aria-label={isSettings ? t('externalLinks.settingsCannotBeHidden') : isHidden ? t('externalLinks.showPage') : t('externalLinks.hidePage')}
                      >
                        {isHidden ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                  );
                }

                const link = item.link;
                const Icon = getIconByName(link.icon);
                return (
                  <div
                    key={item.id}
                    draggable
                    onDragStart={(e) => handleDragStart(e, item.id)}
                    onDragOver={(e) => handleDragOver(e, item.id)}
                    onDragLeave={() => setDragOverId(null)}
                    onDrop={(e) => handleDrop(e, item.id)}
                    onDragEnd={handleDragEnd}
                    className={`relative flex items-center gap-3 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary transition-colors ${
                      draggedId === item.id ? 'opacity-50' : ''
                    } ${
                      dragOverId === item.id && draggedId !== item.id
                        ? 'before:absolute before:left-3 before:right-3 before:top-0 before:h-0.5 before:bg-bambu-green'
                        : ''
                    }`}
                  >
                    <GripVertical className="w-6 h-6 md:w-4 md:h-4 text-bambu-gray cursor-grab flex-shrink-0" />
                    <div className="p-2 rounded-lg bg-bambu-dark-tertiary text-bambu-gray">
                      {link.custom_icon ? (
                        <img
                          src={api.getExternalLinkIconUrl(link.id)}
                          alt=""
                          className="w-4 h-4"
                        />
                      ) : (
                        <Icon className="w-4 h-4" />
                      )}
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
                        title={t('externalLinks.deleteLink')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {(showAddModal || editingLink) && (
        <AddExternalLinkModal
          link={editingLink}
          onClose={() => {
            setShowAddModal(false);
            setEditingLink(null);
          }}
        />
      )}

      {deletingLink && (
        <ConfirmModal
          title="Delete Link"
          message={`Are you sure you want to delete "${deletingLink.name}"? This action cannot be undone.`}
          confirmText="Delete"
          cancelText="Cancel"
          variant="danger"
          onConfirm={confirmDelete}
          onCancel={() => setDeletingLink(null)}
        />
      )}
    </>
  );
}
