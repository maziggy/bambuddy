import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Loader2, Plus, Store, X } from 'lucide-react';
import { api } from '../../api/client';
import type { Supplier } from '../../api/client';
import { useToast } from '../../contexts/ToastContext';

// One editable supplier assignment as the form holds it (#2988). Mirrors
// SpoolSupplierLinkInput but keeps the name so chips render without lookups.
export interface SupplierLinkDraft {
  supplier_id: number;
  supplier_name: string;
  supplier_article_number: string;
  /** Quoted price for comparison — never the cost basis (spool.cost_per_kg). */
  quoted_price_per_kg: number | null;
  is_purchase_source: boolean;
}

interface SupplierSectionProps {
  links: SupplierLinkDraft[];
  onChange: (links: SupplierLinkDraft[]) => void;
  currencySymbol: string;
}

// Multi-select with chips for the spool dialog (#2988): a material can come
// from several suppliers. Each chip carries the per-assignment fields
// (supplier's article number, price there, bought-here marker) and "+ new
// supplier" creates a master-list entry without leaving the dialog.
export function SupplierSection({ links, onChange, currencySymbol }: SupplierSectionProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [creating, setCreating] = useState(false);
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .getSuppliers()
      .then((list) => {
        if (!cancelled) setSuppliers(list);
      })
      .catch((err) => console.error('SupplierSection.getSuppliers failed:', err))
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const linkedIds = useMemo(() => new Set(links.map((l) => l.supplier_id)), [links]);
  const candidates = useMemo(
    () =>
      suppliers
        .filter((s) => !linkedIds.has(s.id))
        .filter((s) => s.name.toLowerCase().includes(search.trim().toLowerCase())),
    [suppliers, linkedIds, search]
  );
  const exactMatch = suppliers.some((s) => s.name.toLowerCase() === search.trim().toLowerCase());

  const addLink = (supplier: Supplier) => {
    onChange([
      ...links,
      {
        supplier_id: supplier.id,
        supplier_name: supplier.name,
        supplier_article_number: '',
        quoted_price_per_kg: null,
        is_purchase_source: false,
      },
    ]);
    setOpen(false);
    setSearch('');
  };

  const createAndAdd = async () => {
    const name = search.trim();
    if (!name) return;
    setCreating(true);
    try {
      const supplier = await api.createSupplier({ name });
      setSuppliers((prev) => [...prev, supplier].sort((a, b) => a.name.localeCompare(b.name)));
      addLink(supplier);
    } catch (err) {
      console.error('SupplierSection.createAndAdd failed:', err);
      showToast(t('inventory.suppliers.createFailed'), 'error');
    } finally {
      setCreating(false);
    }
  };

  const updateLink = (supplierId: number, patch: Partial<SupplierLinkDraft>) => {
    onChange(
      links.map((link) => {
        if (link.supplier_id !== supplierId) {
          // Only one assignment can be the purchase source.
          return patch.is_purchase_source ? { ...link, is_purchase_source: false } : link;
        }
        return { ...link, ...patch };
      })
    );
  };

  const removeLink = (supplierId: number) => {
    onChange(links.filter((link) => link.supplier_id !== supplierId));
  };

  return (
    <div>
      <label className="block text-sm font-medium text-bambu-gray mb-1">{t('inventory.suppliers.label')}</label>

      {links.length > 0 && (
        <div className="space-y-2 mb-2">
          {links.map((link) => (
            <div key={link.supplier_id} className="p-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-bambu-green/10 text-bambu-green">
                  <Store className="w-3 h-3" />
                  {link.supplier_name}
                </span>
                <label
                  className="ml-auto flex items-center gap-1.5 text-xs text-bambu-gray cursor-pointer"
                  title={t('inventory.suppliers.purchaseSourceHelp')}
                >
                  <input
                    type="checkbox"
                    className="w-3.5 h-3.5 accent-bambu-green"
                    checked={link.is_purchase_source}
                    onChange={(e) => updateLink(link.supplier_id, { is_purchase_source: e.target.checked })}
                  />
                  {t('inventory.suppliers.purchaseSource')}
                </label>
                <button
                  type="button"
                  onClick={() => removeLink(link.supplier_id)}
                  className="p-1 rounded text-bambu-gray hover:text-white hover:bg-bambu-dark-tertiary"
                  aria-label={t('common.remove')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="flex gap-2 mt-2">
                <input
                  type="text"
                  className="flex-1 min-w-0 px-2 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-xs placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                  placeholder={t('inventory.suppliers.articleNumberPlaceholder')}
                  value={link.supplier_article_number}
                  maxLength={100}
                  onChange={(e) => updateLink(link.supplier_id, { supplier_article_number: e.target.value })}
                />
                <div className="relative w-28">
                  <span className="absolute left-2 top-1/2 -translate-y-1/2 text-xs text-bambu-gray pointer-events-none">
                    {currencySymbol}
                  </span>
                  <input
                    type="number"
                    className="w-full pl-6 pr-2 py-1.5 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-xs text-right placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                    placeholder={t('inventory.suppliers.pricePlaceholder')}
                    title={t('inventory.suppliers.priceHelp')}
                    min={0}
                    step={0.01}
                    value={link.quoted_price_per_kg ?? ''}
                    onChange={(e) =>
                      updateLink(link.supplier_id, {
                        quoted_price_per_kg: e.target.value === '' ? null : parseFloat(e.target.value),
                      })
                    }
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="relative" ref={containerRef}>
        <button
          type="button"
          onClick={() => setOpen((prev) => !prev)}
          className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-sm text-bambu-gray hover:text-white flex items-center justify-between transition-colors focus:outline-none focus:border-bambu-green"
        >
          <span>{t('inventory.suppliers.addSupplier')}</span>
          <ChevronDown className={`w-4 h-4 transition-transform ${open ? 'rotate-180' : ''}`} />
        </button>
        {open && (
          <div className="absolute z-50 w-full mt-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg">
            <div className="p-2 border-b border-bambu-dark-tertiary">
              <input
                type="text"
                autoFocus
                className="w-full px-2 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                placeholder={t('inventory.suppliers.searchPlaceholder')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <div className="max-h-48 overflow-y-auto py-1">
              {!loaded ? (
                <div className="px-3 py-2 text-sm text-bambu-gray flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('common.loading')}
                </div>
              ) : (
                <>
                  {candidates.map((supplier) => (
                    <button
                      key={supplier.id}
                      type="button"
                      onClick={() => addLink(supplier)}
                      className="w-full px-3 py-2 text-left text-sm text-white hover:bg-bambu-dark-tertiary"
                    >
                      {supplier.name}
                    </button>
                  ))}
                  {candidates.length === 0 && !search.trim() && (
                    <div className="px-3 py-2 text-sm text-bambu-gray">{t('inventory.suppliers.noneLeft')}</div>
                  )}
                  {search.trim() && !exactMatch && (
                    <button
                      type="button"
                      onClick={createAndAdd}
                      disabled={creating}
                      className="w-full px-3 py-2 text-left text-sm text-bambu-green hover:bg-bambu-dark-tertiary flex items-center gap-1.5"
                    >
                      {creating ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                      {t('inventory.suppliers.createNew', { name: search.trim() })}
                    </button>
                  )}
                </>
              )}
            </div>
          </div>
        )}
      </div>
      <p className="text-xs text-bambu-gray mt-1">{t('inventory.suppliers.help')}</p>
    </div>
  );
}
