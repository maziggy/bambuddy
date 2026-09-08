import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { Store, Plus, Trash2, Loader2, Pencil, Check, X, Search, ExternalLink } from 'lucide-react';
import { api, ApiError } from '../api/client';
import type { Supplier } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { ConfirmModal } from './ConfirmModal';

interface SuppliersModalProps {
  open: boolean;
  onClose: () => void;
}

// Managed supplier master list (#2988) — where filament is bought, distinct
// from brand (who made it). Inventory master data that spools reference, so
// it opens from the Inventory toolbar exactly like Locations, in both the
// built-in and the Spoolman inventory.
export function SuppliersModal({ open, onClose }: SuppliersModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');

  // Add/Edit form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formName, setFormName] = useState('');
  const [formWebsite, setFormWebsite] = useState('');
  const [formCustomerNumber, setFormCustomerNumber] = useState('');
  const [formNote, setFormNote] = useState('');
  const [saving, setSaving] = useState(false);

  const [deleteSupplier, setDeleteSupplier] = useState<Supplier | null>(null);

  const loadSuppliers = useCallback(async () => {
    setLoading(true);
    try {
      setSuppliers(await api.getSuppliers());
    } catch (err) {
      console.error('SuppliersModal.loadSuppliers failed:', err);
      showToast(t('settings.suppliers.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast, t]);

  useEffect(() => {
    if (open) loadSuppliers();
  }, [open, loadSuppliers]);

  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const filtered = suppliers.filter((s) => s.name.toLowerCase().includes(search.toLowerCase()));

  const resetForm = () => {
    setFormName('');
    setFormWebsite('');
    setFormCustomerNumber('');
    setFormNote('');
  };

  const formPayload = () => ({
    name: formName.trim(),
    website: formWebsite.trim() || null,
    customer_number: formCustomerNumber.trim() || null,
    note: formNote.trim() || null,
  });

  const handleAdd = async () => {
    if (!formName.trim()) {
      showToast(t('settings.suppliers.nameRequired'), 'error');
      return;
    }
    setSaving(true);
    try {
      const supplier = await api.createSupplier(formPayload());
      setSuppliers((prev) => [...prev, supplier].sort((a, b) => a.name.localeCompare(b.name)));
      setShowAddForm(false);
      resetForm();
      showToast(t('settings.suppliers.added'), 'success');
    } catch (err) {
      console.error('SuppliersModal.handleAdd failed:', err);
      showToast(t('settings.suppliers.addFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (supplier: Supplier) => {
    setEditingId(supplier.id);
    setFormName(supplier.name);
    setFormWebsite(supplier.website ?? '');
    setFormCustomerNumber(supplier.customer_number ?? '');
    setFormNote(supplier.note ?? '');
  };

  const cancelEdit = () => {
    setEditingId(null);
    resetForm();
  };

  const handleUpdate = async (id: number) => {
    if (!formName.trim()) {
      showToast(t('settings.suppliers.nameRequired'), 'error');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.updateSupplier(id, formPayload());
      setSuppliers((prev) => prev.map((s) => (s.id === id ? updated : s)).sort((a, b) => a.name.localeCompare(b.name)));
      cancelEdit();
      showToast(t('settings.suppliers.updated'), 'success');
    } catch (err) {
      console.error('SuppliersModal.handleUpdate failed:', err);
      showToast(t('settings.suppliers.updateFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteSupplier) return;
    try {
      await api.deleteSupplier(deleteSupplier.id);
      setSuppliers((prev) => prev.filter((s) => s.id !== deleteSupplier.id));
      showToast(t('settings.suppliers.deleted'), 'success');
    } catch (err) {
      console.error('SuppliersModal.handleDelete failed:', err);
      // 409: still referenced by spools — surface the guard, not a generic error.
      if (err instanceof ApiError && err.status === 409) {
        showToast(t('settings.suppliers.deleteBlocked', { count: deleteSupplier.spool_count }), 'error');
      } else {
        showToast(t('settings.suppliers.deleteFailed'), 'error');
      }
    } finally {
      setDeleteSupplier(null);
    }
  };

  const formFields = (
    <div className="space-y-2">
      <input
        type="text"
        className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
        placeholder={t('settings.suppliers.namePlaceholder')}
        value={formName}
        maxLength={200}
        onChange={(e) => setFormName(e.target.value)}
      />
      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 min-w-0 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
          placeholder={t('settings.suppliers.websitePlaceholder')}
          value={formWebsite}
          maxLength={500}
          onChange={(e) => setFormWebsite(e.target.value)}
        />
        <input
          type="text"
          className="w-40 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
          placeholder={t('settings.suppliers.customerNumberPlaceholder')}
          value={formCustomerNumber}
          maxLength={100}
          onChange={(e) => setFormCustomerNumber(e.target.value)}
        />
      </div>
      <input
        type="text"
        className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
        placeholder={t('settings.suppliers.notePlaceholder')}
        value={formNote}
        maxLength={500}
        onChange={(e) => setFormNote(e.target.value)}
      />
    </div>
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div
        className="relative w-full max-w-3xl mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="suppliers-modal-title"
      >
        <div className="flex items-center justify-between gap-4 px-6 py-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Store className="w-5 h-5 text-bambu-gray" />
            <h2 id="suppliers-modal-title" className="text-lg font-semibold text-white">
              {t('settings.suppliers.title')}
            </h2>
            <span className="text-sm text-bambu-gray">({suppliers.length})</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => { setShowAddForm(true); setEditingId(null); resetForm(); }}
              className="px-3 py-1.5 text-sm bg-bambu-green text-white rounded-lg hover:bg-bambu-green/80 transition-colors flex items-center gap-1.5"
            >
              <Plus className="w-4 h-4" />
              <span className="hidden sm:inline">{t('common.add')}</span>
            </button>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
              aria-label={t('common.close')}
            >
              <X className="w-5 h-5" />
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-4 space-y-4">
          <p className="text-sm text-bambu-gray">{t('settings.suppliers.description')}</p>

          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              className="w-full pl-10 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
              placeholder={t('settings.suppliers.search')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          {showAddForm && (
            <div className="p-4 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary space-y-3">
              <h3 className="text-sm font-medium text-white">{t('settings.suppliers.addNew')}</h3>
              {formFields}
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => { setShowAddForm(false); resetForm(); }}
                  className="px-3 py-2 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary"
                >
                  {t('common.cancel')}
                </button>
                <button
                  onClick={handleAdd}
                  disabled={saving}
                  className="px-3 py-2 bg-bambu-green text-white rounded-lg hover:bg-bambu-green/80 flex items-center gap-1"
                >
                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                  {t('common.add')}
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-8 text-bambu-gray">
              <Loader2 className="w-5 h-5 animate-spin mr-2" />
              {t('common.loading')}
            </div>
          ) : (
            <div className="border border-bambu-dark-tertiary rounded-lg">
              <table className="w-full text-sm">
                <thead className="bg-bambu-dark sticky top-0">
                  <tr>
                    <th className="px-4 py-2 text-left text-bambu-gray font-medium">{t('common.name')}</th>
                    <th className="px-4 py-2 text-left text-bambu-gray font-medium hidden sm:table-cell">
                      {t('settings.suppliers.customerNumber')}
                    </th>
                    <th className="px-4 py-2 text-right text-bambu-gray font-medium w-20">
                      {t('settings.suppliers.spools')}
                    </th>
                    <th className="px-4 py-2 w-24"></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="px-4 py-8 text-center text-bambu-gray">
                        {search ? t('settings.suppliers.noMatch') : t('settings.suppliers.empty')}
                      </td>
                    </tr>
                  ) : (
                    filtered.map((supplier) => (
                      <tr key={supplier.id} className="border-t border-bambu-dark-tertiary hover:bg-bambu-dark align-top">
                        {editingId === supplier.id ? (
                          <>
                            <td className="px-4 py-2" colSpan={3}>{formFields}</td>
                            <td className="px-4 py-2">
                              <div className="flex justify-end gap-1">
                                <button
                                  onClick={() => handleUpdate(supplier.id)}
                                  disabled={saving}
                                  className="p-1.5 rounded hover:bg-green-500/20 text-green-500"
                                >
                                  {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                                </button>
                                <button onClick={cancelEdit} className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray">
                                  <X className="w-4 h-4" />
                                </button>
                              </div>
                            </td>
                          </>
                        ) : (
                          <>
                            <td className="px-4 py-2 text-white">
                              <div className="flex items-center gap-1.5">
                                <span>{supplier.name}</span>
                                {supplier.website && (
                                  <a
                                    href={supplier.website}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="text-bambu-gray hover:text-bambu-green"
                                    title={supplier.website}
                                  >
                                    <ExternalLink className="w-3.5 h-3.5" />
                                  </a>
                                )}
                              </div>
                              {supplier.note && <div className="text-xs text-bambu-gray mt-0.5">{supplier.note}</div>}
                            </td>
                            <td className="px-4 py-2 text-bambu-gray hidden sm:table-cell">
                              {supplier.customer_number || '-'}
                            </td>
                            <td className="px-4 py-2 text-right font-mono text-bambu-gray">{supplier.spool_count}</td>
                            <td className="px-4 py-2">
                              <div className="flex justify-end gap-1">
                                <button
                                  onClick={() => { startEdit(supplier); setShowAddForm(false); }}
                                  className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white"
                                >
                                  <Pencil className="w-4 h-4" />
                                </button>
                                <button
                                  onClick={() => setDeleteSupplier(supplier)}
                                  className="p-1.5 rounded bg-red-500/10 hover:bg-red-500/20 text-red-500"
                                >
                                  <Trash2 className="w-4 h-4" />
                                </button>
                              </div>
                            </td>
                          </>
                        )}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {deleteSupplier && (
        <ConfirmModal
          title={t('settings.suppliers.deleteTitle')}
          message={
            deleteSupplier.spool_count > 0
              ? t('settings.suppliers.deleteConfirmReferenced', {
                  name: deleteSupplier.name,
                  count: deleteSupplier.spool_count,
                })
              : t('settings.suppliers.deleteConfirm', { name: deleteSupplier.name })
          }
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={handleDelete}
          onCancel={() => setDeleteSupplier(null)}
        />
      )}
    </div>
  );
}
