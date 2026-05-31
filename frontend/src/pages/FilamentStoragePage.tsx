import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Thermometer, Droplets, Plus, Pencil, Trash2, Flame, Package, BarChart2, Loader2 } from 'lucide-react';
import { api, type StorageUnit, type StorageUnitCreate } from '../api/client';
import { Card, CardContent } from '../components/Card';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { StorageHistoryModal } from '../components/StorageHistoryModal';
import { useToast } from '../contexts/ToastContext';

// ── Unit type filter ──────────────────────────────────────────────────────────

type FilterType = 'all' | 'dryer' | 'storage';

// ── Add / Edit Modal ──────────────────────────────────────────────────────────

function StorageUnitModal({
  unit,
  onClose,
  onSave,
  isSaving,
}: {
  unit: StorageUnit | null;
  onClose: () => void;
  onSave: (data: StorageUnitCreate) => void;
  isSaving: boolean;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState<StorageUnitCreate>({
    name: unit?.name ?? '',
    unit_type: unit?.unit_type ?? 'storage',
    ha_temp_entity: unit?.ha_temp_entity ?? '',
    ha_humidity_entity: unit?.ha_humidity_entity ?? '',
    notes: unit?.notes ?? '',
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({
      ...form,
      ha_temp_entity: form.ha_temp_entity?.trim() || null,
      ha_humidity_entity: form.ha_humidity_entity?.trim() || null,
      notes: form.notes?.trim() || null,
    });
  };

  return (
    <div
      className="fixed inset-0 bg-black/60 flex items-start sm:items-center justify-center z-50 p-4 overflow-y-auto"
      onClick={onClose}
    >
      <Card className="w-full max-w-md my-auto" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent>
          <h2 className="text-xl font-semibold mb-4 text-white">
            {unit ? t('storage.editUnit') : t('storage.addUnit')}
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('storage.modalNameLabel')}</label>
              <input
                type="text"
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder={t('storage.modalNamePlaceholder')}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none"
              />
            </div>

            {/* Type */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('storage.modalTypeLabel')}</label>
              <div className="flex gap-2">
                {(['dryer', 'storage'] as const).map((type) => (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setForm({ ...form, unit_type: type })}
                    className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg border text-sm font-medium transition-colors ${
                      form.unit_type === type
                        ? 'bg-bambu-green border-bambu-green text-white'
                        : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:text-white'
                    }`}
                  >
                    {type === 'dryer' ? <Flame className="w-4 h-4" /> : <Package className="w-4 h-4" />}
                    {type === 'dryer' ? t('storage.typeDryer') : t('storage.typeStorage')}
                  </button>
                ))}
              </div>
            </div>

            {/* HA Temperature entity */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('storage.modalTempEntityLabel')}</label>
              <input
                type="text"
                value={form.ha_temp_entity ?? ''}
                onChange={(e) => setForm({ ...form, ha_temp_entity: e.target.value })}
                placeholder={t('storage.modalTempEntityPlaceholder')}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono focus:border-bambu-green focus:outline-none"
              />
            </div>

            {/* HA Humidity entity */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('storage.modalHumidityEntityLabel')}</label>
              <input
                type="text"
                value={form.ha_humidity_entity ?? ''}
                onChange={(e) => setForm({ ...form, ha_humidity_entity: e.target.value })}
                placeholder={t('storage.modalHumidityEntityPlaceholder')}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm font-mono focus:border-bambu-green focus:outline-none"
              />
            </div>

            {/* Notes */}
            <div>
              <label className="block text-sm text-bambu-gray mb-1">{t('storage.modalNotesLabel')}</label>
              <textarea
                value={form.notes ?? ''}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
                placeholder={t('storage.modalNotesPlaceholder')}
                rows={2}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:border-bambu-green focus:outline-none resize-none"
              />
            </div>

            <div className="flex gap-2 pt-1">
              <Button type="submit" disabled={isSaving} className="flex-1">
                {isSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {t('storage.modalSave')}
              </Button>
              <Button type="button" variant="secondary" onClick={onClose} className="flex-1">
                {t('storage.modalCancel')}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

// ── Storage unit card ─────────────────────────────────────────────────────────

function StorageUnitCard({
  unit,
  onEdit,
  onDelete,
  onShowHistory,
}: {
  unit: StorageUnit;
  onEdit: () => void;
  onDelete: () => void;
  onShowHistory: () => void;
}) {
  const { t } = useTranslation();
  const isDryer = unit.unit_type === 'dryer';
  const hasEntities = unit.ha_temp_entity || unit.ha_humidity_entity;
  const hasData = unit.current_temp !== null || unit.current_humidity !== null;

  return (
    <Card>
      <CardContent>
        {/* Header */}
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2 min-w-0">
            {isDryer
              ? <Flame className="w-5 h-5 text-orange-400 shrink-0" />
              : <Package className="w-5 h-5 text-blue-400 shrink-0" />}
            <div className="min-w-0">
              <h3 className="text-base font-semibold text-white truncate">{unit.name}</h3>
              <span className="text-xs text-bambu-gray">
                {isDryer ? t('storage.typeDryer') : t('storage.typeStorage')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1 shrink-0 ml-2">
            <button
              onClick={onShowHistory}
              className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
              title={t('storage.historyTitle')}
            >
              <BarChart2 className="w-4 h-4" />
            </button>
            <button
              onClick={onEdit}
              className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white transition-colors"
              title={t('storage.editUnit')}
            >
              <Pencil className="w-4 h-4" />
            </button>
            <button
              onClick={onDelete}
              className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-red-400 transition-colors"
              title={t('storage.deleteUnit')}
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Readings */}
        {!hasEntities ? (
          <p className="text-xs text-bambu-gray/60 mt-2">{t('storage.noEntitiesHint')}</p>
        ) : !hasData ? (
          <p className="text-xs text-yellow-500/70 mt-2">{t('storage.noDataHint')}</p>
        ) : (
          <div className="flex gap-3 mt-1">
            {unit.current_temp !== null && (
              <div className="flex items-center gap-2 flex-1 bg-bambu-dark rounded-lg px-3 py-2">
                <Thermometer className="w-4 h-4 text-orange-400 shrink-0" />
                <div>
                  <p className="text-[10px] text-bambu-gray uppercase tracking-wider">{t('storage.temperature')}</p>
                  <p className="text-sm font-semibold text-white">
                    {Math.round(unit.current_temp)}{unit.temp_unit}
                  </p>
                </div>
              </div>
            )}
            {unit.current_humidity !== null && (
              <div className="flex items-center gap-2 flex-1 bg-bambu-dark rounded-lg px-3 py-2">
                <Droplets className="w-4 h-4 text-blue-400 shrink-0" />
                <div>
                  <p className="text-[10px] text-bambu-gray uppercase tracking-wider">{t('storage.humidity')}</p>
                  <p className="text-sm font-semibold text-white">
                    {Math.round(unit.current_humidity)}{unit.humidity_unit}
                  </p>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Notes */}
        {unit.notes && (
          <p className="text-xs text-bambu-gray mt-2 truncate" title={unit.notes}>{unit.notes}</p>
        )}
      </CardContent>
    </Card>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function FilamentStoragePage() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [filter, setFilter] = useState<FilterType>('all');
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [editUnit, setEditUnit] = useState<StorageUnit | null>(null);
  const [deleteUnit, setDeleteUnit] = useState<StorageUnit | null>(null);
  const [historyUnit, setHistoryUnit] = useState<StorageUnit | null>(null);

  const { data: units = [], isLoading } = useQuery<StorageUnit[]>({
    queryKey: ['storage-units'],
    queryFn: () => api.listStorageUnits(),
    refetchInterval: 60_000,
  });

  const createMutation = useMutation({
    mutationFn: (data: StorageUnitCreate) => api.createStorageUnit(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storage-units'] });
      setAddModalOpen(false);
      showToast(t('storage.savedToast'), 'success');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<StorageUnitCreate> }) =>
      api.updateStorageUnit(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storage-units'] });
      setEditUnit(null);
      showToast(t('storage.savedToast'), 'success');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteStorageUnit(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storage-units'] });
      setDeleteUnit(null);
      showToast(t('storage.deletedToast'), 'success');
    },
  });

  const filtered = units.filter((u) => filter === 'all' || u.unit_type === filter);

  const filters: { key: FilterType; labelKey: string }[] = [
    { key: 'all', labelKey: 'storage.filterAll' },
    { key: 'dryer', labelKey: 'storage.filterDryers' },
    { key: 'storage', labelKey: 'storage.filterStorage' },
  ];

  return (
    <div className="p-4 sm:p-6 max-w-5xl mx-auto">
      {/* Page header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Thermometer className="w-6 h-6 text-bambu-green" />
          <h1 className="text-2xl font-bold text-white">{t('storage.title')}</h1>
        </div>
        <Button onClick={() => setAddModalOpen(true)}>
          <Plus className="w-4 h-4" />
          {t('storage.addUnit')}
        </Button>
      </div>

      {/* Filter bar */}
      {units.length > 0 && (
        <div className="flex gap-2 mb-5">
          {filters.map(({ key, labelKey }) => (
            <button
              key={key}
              onClick={() => setFilter(key)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                filter === key
                  ? 'bg-bambu-green text-white'
                  : 'bg-bambu-dark-secondary border border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
            >
              {t(labelKey)}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-20">
          <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
        </div>
      ) : units.length === 0 ? (
        <Card>
          <CardContent>
            <div className="text-center py-12">
              <Thermometer className="w-12 h-12 text-bambu-gray/40 mx-auto mb-3" />
              <p className="text-bambu-gray">{t('storage.emptyState')}</p>
            </div>
          </CardContent>
        </Card>
      ) : filtered.length === 0 ? (
        <Card>
          <CardContent>
            <p className="text-center py-8 text-bambu-gray">{t('storage.emptyState')}</p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((unit) => (
            <StorageUnitCard
              key={unit.id}
              unit={unit}
              onEdit={() => setEditUnit(unit)}
              onDelete={() => setDeleteUnit(unit)}
              onShowHistory={() => setHistoryUnit(unit)}
            />
          ))}
        </div>
      )}

      {/* Add modal */}
      {addModalOpen && (
        <StorageUnitModal
          unit={null}
          onClose={() => setAddModalOpen(false)}
          onSave={(data) => createMutation.mutate(data)}
          isSaving={createMutation.isPending}
        />
      )}

      {/* Edit modal */}
      {editUnit && (
        <StorageUnitModal
          unit={editUnit}
          onClose={() => setEditUnit(null)}
          onSave={(data) => updateMutation.mutate({ id: editUnit.id, data })}
          isSaving={updateMutation.isPending}
        />
      )}

      {/* Delete confirm */}
      {deleteUnit && (
        <ConfirmModal
          title={t('storage.deleteUnit')}
          message={t('storage.deleteConfirm', { name: deleteUnit.name })}
          confirmText={t('storage.deleteUnit')}
          onConfirm={() => deleteMutation.mutate(deleteUnit.id)}
          onCancel={() => setDeleteUnit(null)}
          variant="danger"
        />
      )}

      {/* History modal */}
      {historyUnit && (
        <StorageHistoryModal
          isOpen={!!historyUnit}
          onClose={() => setHistoryUnit(null)}
          unitId={historyUnit.id}
          unitName={historyUnit.name}
        />
      )}
    </div>
  );
}
