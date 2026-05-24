import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { MapPin, Plus, Loader2, Pencil, Trash2 } from 'lucide-react';
import { api, type StorageLocation } from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import { inventoryLocationsQueryKey, invalidateInventoryLocations } from '../utils/inventoryQueries';

export default function LocationsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<StorageLocation | null>(null);
  const [name, setName] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<StorageLocation | null>(null);

  const { data: locations = [], isLoading } = useQuery({
    queryKey: inventoryLocationsQueryKey,
    queryFn: api.getLocations,
  });

  const invalidate = () => {
    invalidateInventoryLocations(queryClient);
    queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
    queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const trimmed = name.trim();
      if (!trimmed) throw new Error(t('locations.nameRequired'));
      if (editing) {
        return api.updateLocation(editing.id, { name: trimmed });
      }
      return api.createLocation({ name: trimmed });
    },
    onSuccess: () => {
      showToast(t(editing ? 'locations.updated' : 'locations.created'), 'success');
      setModalOpen(false);
      setEditing(null);
      setName('');
      invalidate();
    },
    onError: (err: Error) => {
      showToast(err.message || t('locations.saveFailed'), 'error');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteLocation(id),
    onSuccess: () => {
      showToast(t('locations.deleted'), 'success');
      setDeleteTarget(null);
      invalidate();
    },
    onError: (err: Error) => {
      showToast(err.message || t('locations.deleteFailed'), 'error');
    },
  });

  const openCreate = () => {
    setEditing(null);
    setName('');
    setModalOpen(true);
  };

  const openEdit = (location: StorageLocation) => {
    setEditing(location);
    setName(location.name);
    setModalOpen(true);
  };

  const closeModal = useCallback(() => {
    if (saveMutation.isPending) return;
    setModalOpen(false);
    setEditing(null);
    setName('');
  }, [saveMutation.isPending]);

  useEffect(() => {
    if (!modalOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !saveMutation.isPending) closeModal();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [modalOpen, closeModal, saveMutation.isPending]);

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();
    saveMutation.mutate();
  };

  const modalTitleId = 'location-modal-title';

  return (
    <div className="p-4 md:p-8 space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <MapPin className="w-7 h-7 text-bambu-green" />
            {t('locations.title')}
          </h1>
          <p className="text-bambu-gray mt-1">{t('locations.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => navigate('/inventory')}>
            {t('locations.backToInventory')}
          </Button>
          <Button onClick={openCreate}>
            <Plus className="w-4 h-4" />
            {t('locations.add')}
          </Button>
        </div>
      </div>

      <div className="bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="flex items-center justify-center py-16 text-bambu-gray">
            <Loader2 className="w-6 h-6 animate-spin mr-2" />
            {t('common.loading')}
          </div>
        ) : locations.length === 0 ? (
          <div className="py-16 text-center text-bambu-gray">{t('locations.empty')}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-bambu-dark-tertiary text-left text-bambu-gray">
                <th className="px-4 py-3 font-medium">{t('locations.name')}</th>
                <th className="px-4 py-3 font-medium text-right">{t('locations.spools')}</th>
                <th className="px-4 py-3 font-medium text-right w-32">{t('common.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {locations.map((loc) => (
                <tr
                  key={loc.id}
                  className="border-b border-bambu-dark-tertiary/60 hover:bg-bambu-dark-tertiary/30 cursor-pointer"
                  onClick={() => navigate(`/inventory?location_id=${loc.id}`)}
                >
                  <td className="px-4 py-3 text-white font-medium">{loc.name}</td>
                  <td className="px-4 py-3 text-right text-bambu-gray">{loc.spool_count}</td>
                  <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      <button
                        type="button"
                        className="p-1.5 text-bambu-gray hover:text-bambu-green rounded"
                        onClick={() => openEdit(loc)}
                        title={t('common.edit')}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        type="button"
                        className="p-1.5 text-bambu-gray hover:text-red-400 rounded disabled:opacity-40"
                        disabled={loc.spool_count > 0}
                        onClick={() => setDeleteTarget(loc)}
                        title={loc.spool_count > 0 ? t('locations.deleteBlocked') : t('common.delete')}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center">
          <div className="absolute inset-0 bg-black/60" onClick={closeModal} />
          <div
            className="relative w-full max-w-md mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl p-6 shadow-2xl"
            role="dialog"
            aria-modal="true"
            aria-labelledby={modalTitleId}
          >
            <h2 id={modalTitleId} className="text-lg font-semibold text-white mb-4">
              {editing ? t('locations.edit') : t('locations.add')}
            </h2>
            <form onSubmit={handleSave}>
              <label className="block text-sm font-medium text-bambu-gray mb-1" htmlFor="location-name">
                {t('locations.name')}
              </label>
              <input
                id="location-name"
                type="text"
                maxLength={255}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green mb-4"
                placeholder={t('locations.createPlaceholder')}
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
              />
              <div className="flex justify-end gap-2">
                <Button type="button" variant="secondary" onClick={closeModal}>
                  {t('common.cancel')}
                </Button>
                <Button type="submit" disabled={saveMutation.isPending || !name.trim()}>
                  {saveMutation.isPending && <Loader2 className="w-4 h-4 animate-spin" />}
                  {t('common.save')}
                </Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteTarget && (
        <ConfirmModal
          title={t('locations.confirmDelete', { name: deleteTarget.name })}
          message={t('locations.confirmDeleteMessage')}
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
