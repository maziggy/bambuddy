import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Database, Plus, Trash2, RotateCcw, Loader2, Pencil, Check, X, Search, Download, Upload } from 'lucide-react';
import { api } from '../api/client';
import type { SpoolCatalogEntry } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import { Card, CardHeader, CardContent } from './Card';
import { ConfirmModal } from './ConfirmModal';

export function SpoolCatalogSettings() {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [catalog, setCatalog] = useState<SpoolCatalogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Add/Edit form state
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formName, setFormName] = useState('');
  const [formWeight, setFormWeight] = useState('');
  const [saving, setSaving] = useState(false);

  // Confirmation modals
  const [deleteEntry, setDeleteEntry] = useState<SpoolCatalogEntry | null>(null);
  const [showResetConfirm, setShowResetConfirm] = useState(false);

  const loadCatalog = useCallback(async () => {
    try {
      const entries = await api.getSpoolCatalog();
      setCatalog(entries);
    } catch {
      showToast(t('settings.catalog.loadFailed'), 'error');
    } finally {
      setLoading(false);
    }
  }, [showToast, t]);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  const filteredCatalog = catalog.filter(entry =>
    entry.name.toLowerCase().includes(search.toLowerCase())
  );

  const handleAdd = async () => {
    if (!formName.trim() || !formWeight) {
      showToast(t('settings.catalog.nameWeightRequired'), 'error');
      return;
    }
    setSaving(true);
    try {
      const entry = await api.addCatalogEntry({ name: formName.trim(), weight: parseInt(formWeight) });
      setCatalog(prev => [...prev, entry].sort((a, b) => a.name.localeCompare(b.name)));
      setShowAddForm(false);
      setFormName('');
      setFormWeight('');
      showToast(t('settings.catalog.entryAdded'), 'success');
    } catch {
      showToast(t('settings.catalog.addFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (entry: SpoolCatalogEntry) => {
    setEditingId(entry.id);
    setFormName(entry.name);
    setFormWeight(entry.weight.toString());
  };

  const cancelEdit = () => {
    setEditingId(null);
    setFormName('');
    setFormWeight('');
  };

  const handleUpdate = async (id: number) => {
    if (!formName.trim() || !formWeight) {
      showToast(t('settings.catalog.nameWeightRequired'), 'error');
      return;
    }
    setSaving(true);
    try {
      const updated = await api.updateCatalogEntry(id, { name: formName.trim(), weight: parseInt(formWeight) });
      setCatalog(prev => prev.map(e => e.id === id ? updated : e).sort((a, b) => a.name.localeCompare(b.name)));
      setEditingId(null);
      setFormName('');
      setFormWeight('');
      showToast(t('settings.catalog.entryUpdated'), 'success');
    } catch {
      showToast(t('settings.catalog.updateFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async () => {
    if (!deleteEntry) return;
    try {
      await api.deleteCatalogEntry(deleteEntry.id);
      setCatalog(prev => prev.filter(e => e.id !== deleteEntry.id));
      showToast(t('settings.catalog.entryDeleted'), 'success');
    } catch {
      showToast(t('settings.catalog.deleteFailed'), 'error');
    } finally {
      setDeleteEntry(null);
    }
  };

  const handleReset = async () => {
    setShowResetConfirm(false);
    setLoading(true);
    try {
      await api.resetSpoolCatalog();
      await loadCatalog();
      showToast(t('settings.catalog.resetSuccess'), 'success');
    } catch {
      showToast(t('settings.catalog.resetFailed'), 'error');
      setLoading(false);
    }
  };

  const handleExport = () => {
    const exportData = catalog.map(({ name, weight }) => ({ name, weight }));
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'spool-catalog.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast(t('settings.catalog.exported', { count: catalog.length }), 'success');
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const data = JSON.parse(text) as Array<{ name: string; weight: number }>;
      if (!Array.isArray(data)) throw new Error('Invalid format');

      let added = 0;
      let skipped = 0;
      for (const item of data) {
        if (!item.name || typeof item.weight !== 'number') { skipped++; continue; }
        const exists = catalog.some(c => c.name.toLowerCase() === item.name.toLowerCase());
        if (exists) { skipped++; continue; }
        try {
          const entry = await api.addCatalogEntry({ name: item.name, weight: item.weight });
          setCatalog(prev => [...prev, entry].sort((a, b) => a.name.localeCompare(b.name)));
          added++;
        } catch { skipped++; }
      }
      showToast(t('settings.catalog.imported', { added, skipped }), 'success');
    } catch {
      showToast(t('settings.catalog.importFailed'), 'error');
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2 mb-3">
          <Database className="w-5 h-5 text-bambu-gray" />
          <h2 className="text-lg font-semibold text-white">{t('settings.catalog.spoolCatalog')}</h2>
          <span className="text-sm text-bambu-gray">({catalog.length})</span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={handleExport}
            className="px-3 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray hover:text-white transition-colors flex items-center gap-1.5"
            title={t('settings.catalog.exportTooltip')}
          >
            <Download className="w-4 h-4" />
            <span className="hidden sm:inline">{t('common.export')}</span>
          </button>
          <button
            onClick={() => fileInputRef.current?.click()}
            className="px-3 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray hover:text-white transition-colors flex items-center gap-1.5"
            title={t('settings.catalog.importTooltip')}
          >
            <Upload className="w-4 h-4" />
            <span className="hidden sm:inline">{t('common.import')}</span>
          </button>
          <input ref={fileInputRef} type="file" accept=".json" className="hidden" onChange={handleImport} />
          <button
            onClick={() => setShowResetConfirm(true)}
            className="px-3 py-1.5 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-bambu-gray hover:text-white transition-colors flex items-center gap-1.5"
            title={t('settings.catalog.resetTooltip')}
          >
            <RotateCcw className="w-4 h-4" />
            <span className="hidden sm:inline">{t('common.reset')}</span>
          </button>
          <button
            onClick={() => setShowAddForm(true)}
            className="px-3 py-1.5 text-sm bg-bambu-green text-white rounded-lg hover:bg-bambu-green/80 transition-colors flex items-center gap-1.5"
          >
            <Plus className="w-4 h-4" />
            <span className="hidden sm:inline">{t('common.add')}</span>
          </button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-bambu-gray">
          {t('settings.catalog.spoolCatalogDescription')}
        </p>

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
          <input
            type="text"
            className="w-full pl-10 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
            placeholder={t('settings.catalog.searchCatalog')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {/* Add form */}
        {showAddForm && (
          <div className="p-4 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
            <h3 className="text-sm font-medium text-white mb-3">{t('settings.catalog.addNewEntry')}</h3>
            <div className="flex gap-2 items-center">
              <div className="flex-1 min-w-0">
                <input
                  type="text"
                  className="w-full px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                  placeholder={t('settings.catalog.namePlaceholder')}
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                />
              </div>
              <input
                type="number"
                className="w-20 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white text-center focus:border-bambu-green focus:outline-none"
                placeholder="g"
                value={formWeight}
                onChange={(e) => setFormWeight(e.target.value)}
              />
              <span className="text-bambu-gray shrink-0">g</span>
              <button
                onClick={handleAdd}
                disabled={saving}
                className="px-3 py-2 bg-bambu-green text-white rounded-lg hover:bg-bambu-green/80 flex items-center gap-1 shrink-0"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
                {t('common.add')}
              </button>
              <button
                onClick={() => { setShowAddForm(false); setFormName(''); setFormWeight(''); }}
                className="p-2 rounded-lg text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        {/* Catalog list */}
        {loading ? (
          <div className="flex items-center justify-center py-8 text-bambu-gray">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            {t('common.loading')}
          </div>
        ) : (
          <div className="max-h-[600px] overflow-y-auto border border-bambu-dark-tertiary rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-bambu-dark sticky top-0">
                <tr>
                  <th className="px-4 py-2 text-left text-bambu-gray font-medium">{t('common.name')}</th>
                  <th className="px-4 py-2 text-right text-bambu-gray font-medium w-24">{t('settings.catalog.weight')}</th>
                  <th className="px-4 py-2 text-center text-bambu-gray font-medium w-20">{t('settings.catalog.type')}</th>
                  <th className="px-4 py-2 w-24"></th>
                </tr>
              </thead>
              <tbody>
                {filteredCatalog.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-4 py-8 text-center text-bambu-gray">
                      {search ? t('settings.catalog.noMatch') : t('settings.catalog.empty')}
                    </td>
                  </tr>
                ) : (
                  filteredCatalog.map(entry => (
                    <tr key={entry.id} className="border-t border-bambu-dark-tertiary hover:bg-bambu-dark">
                      {editingId === entry.id ? (
                        <>
                          <td className="px-4 py-2">
                            <input
                              type="text"
                              className="w-full px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white focus:border-bambu-green focus:outline-none"
                              value={formName}
                              onChange={(e) => setFormName(e.target.value)}
                            />
                          </td>
                          <td className="px-4 py-2">
                            <input
                              type="number"
                              className="w-full px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-right focus:border-bambu-green focus:outline-none"
                              value={formWeight}
                              onChange={(e) => setFormWeight(e.target.value)}
                            />
                          </td>
                          <td className="px-4 py-2 text-center">
                            <span className="text-xs text-bambu-gray">-</span>
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex justify-end gap-1">
                              <button
                                onClick={() => handleUpdate(entry.id)}
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
                          <td className="px-4 py-2 text-white">{entry.name}</td>
                          <td className="px-4 py-2 text-right font-mono text-white">{entry.weight}g</td>
                          <td className="px-4 py-2 text-center">
                            {entry.is_default ? (
                              <span className="text-xs px-2 py-0.5 rounded bg-bambu-dark-tertiary text-bambu-gray">
                                {t('settings.catalog.default')}
                              </span>
                            ) : (
                              <span className="text-xs px-2 py-0.5 rounded bg-bambu-green/20 text-bambu-green">
                                {t('settings.catalog.custom')}
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-2">
                            <div className="flex justify-end gap-1">
                              <button
                                onClick={() => startEdit(entry)}
                                className="p-1.5 rounded hover:bg-bambu-dark-tertiary text-bambu-gray hover:text-white"
                              >
                                <Pencil className="w-4 h-4" />
                              </button>
                              <button
                                onClick={() => setDeleteEntry(entry)}
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
      </CardContent>

      {/* Delete confirmation */}
      {deleteEntry && (
        <ConfirmModal
          title={t('settings.catalog.deleteEntry')}
          message={t('settings.catalog.deleteConfirm', { name: deleteEntry.name })}
          confirmText={t('common.delete')}
          variant="danger"
          onConfirm={handleDelete}
          onCancel={() => setDeleteEntry(null)}
        />
      )}

      {/* Reset confirmation */}
      {showResetConfirm && (
        <ConfirmModal
          title={t('settings.catalog.resetCatalog')}
          message={t('settings.catalog.resetConfirm')}
          confirmText={t('common.reset')}
          variant="danger"
          onConfirm={handleReset}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}
    </Card>
  );
}
