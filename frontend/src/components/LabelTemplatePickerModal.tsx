import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Printer, CheckSquare, Square, Search } from 'lucide-react';
import { api, type SpoolLabelTemplate, type InventorySpool } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

/** Subset of InventorySpool the modal needs for checkbox rendering. */
type SpoolForLabel = Pick<
  InventorySpool,
  'id' | 'material' | 'subtype' | 'brand' | 'color_name' | 'rgba'
>;

interface LabelTemplatePickerModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** All spools the modal can choose from. Typically the page's current
   *  filter result so the modal stays consistent with what the user sees. */
  availableSpools: SpoolForLabel[];
  /** IDs to pre-check when the modal opens. Per-card icon passes a single ID;
   *  the bulk header button passes every visible ID so the user lands in
   *  "all checked" and refines downward. */
  initialSelectedIds: number[];
  spoolmanMode: boolean;
}

interface TemplateOption {
  value: SpoolLabelTemplate;
  i18nKey: string;
  fallbackLabel: string;
  fallbackHint: string;
}

const TEMPLATE_OPTIONS: TemplateOption[] = [
  {
    value: 'ams_30x15',
    i18nKey: 'ams',
    fallbackLabel: 'AMS holder (30 × 15 mm)',
    fallbackHint: 'Single label per page; fits the popular AMS filament label holder.',
  },
  {
    value: 'box_62x29',
    i18nKey: 'box',
    fallbackLabel: 'Box label (62 × 29 mm)',
    fallbackHint: 'Single label per page; sized for Brother PT/QL and Dymo small labels.',
  },
  {
    value: 'avery_l7160',
    i18nKey: 'averyL7160',
    fallbackLabel: 'Avery L7160 — A4 sheet (38.1 × 63.5 mm × 21)',
    fallbackHint: 'EU sheet stock; 21 labels per A4 page.',
  },
  {
    value: 'avery_5160',
    i18nKey: 'avery5160',
    fallbackLabel: 'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
    fallbackHint: 'US sheet stock; 30 labels per Letter page.',
  },
];

function openBlobInNewTab(blob: Blob): void {
  const url = window.URL.createObjectURL(blob);
  const win = window.open(url, '_blank', 'noopener,noreferrer');
  if (!win) {
    const a = document.createElement('a');
    a.href = url;
    a.download = 'bambuddy-labels.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }
  setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
}

function swatchStyle(rgba: string | null | undefined): React.CSSProperties {
  if (!rgba) return { backgroundColor: '#808080' };
  const cleaned = rgba.replace(/^#/, '').slice(0, 6);
  return cleaned.length === 6 ? { backgroundColor: `#${cleaned}` } : { backgroundColor: '#808080' };
}

function spoolDisplayName(s: SpoolForLabel): string {
  const head = s.color_name ?? `${s.material}${s.subtype ? ` ${s.subtype}` : ''}`;
  const brand = s.brand ? ` · ${s.brand}` : '';
  return `${head}${brand}`;
}

/** Build a lowercased haystack that the search input matches against. */
function searchableText(s: SpoolForLabel): string {
  return [s.color_name, s.material, s.subtype, s.brand, `#${s.id}`]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

export function LabelTemplatePickerModal({
  isOpen,
  onClose,
  availableSpools,
  initialSelectedIds,
  spoolmanMode,
}: LabelTemplatePickerModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const [pending, setPending] = useState<SpoolLabelTemplate | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [materialFilter, setMaterialFilter] = useState<string>('');

  // Sync from caller and reset transient state on open. Intentionally not
  // reactive to props while open — once the user starts editing we don't want
  // a parent re-render to clobber their selection / filter / search.
  useEffect(() => {
    if (isOpen) {
      const allowed = new Set(availableSpools.map((s) => s.id));
      setSelectedIds(new Set(initialSelectedIds.filter((id) => allowed.has(id))));
      setSearch('');
      setMaterialFilter('');
      setPending(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const sortedSpools = useMemo(
    () => [...availableSpools].sort((a, b) => a.id - b.id),
    [availableSpools],
  );

  // Material chips are derived from the *full* available set so they stay
  // stable when search/material filter narrows the visible list.
  const materials = useMemo(() => {
    const set = new Set<string>();
    for (const s of sortedSpools) {
      if (s.material) set.add(s.material.toUpperCase());
    }
    return [...set].sort();
  }, [sortedSpools]);

  const visibleSpools = useMemo(() => {
    const q = search.trim().toLowerCase();
    return sortedSpools.filter((s) => {
      if (materialFilter && (s.material || '').toUpperCase() !== materialFilter) return false;
      if (q && !searchableText(s).includes(q)) return false;
      return true;
    });
  }, [sortedSpools, search, materialFilter]);

  const allVisibleChecked =
    visibleSpools.length > 0 && visibleSpools.every((s) => selectedIds.has(s.id));

  if (!isOpen) return null;

  const selectedCount = selectedIds.size;
  const noSelection = selectedCount === 0;

  function toggleOne(id: number) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAllVisible() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const s of visibleSpools) next.add(s.id);
      return next;
    });
  }

  function deselectVisible() {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      for (const s of visibleSpools) next.delete(s.id);
      return next;
    });
  }

  function clearAll() {
    setSelectedIds(new Set());
  }

  async function handlePick(template: SpoolLabelTemplate) {
    if (noSelection || pending) return;
    const ids = [...selectedIds].sort((a, b) => a - b);
    setPending(template);
    try {
      const blob = spoolmanMode
        ? await api.printSpoolmanSpoolLabels({ spool_ids: ids, template })
        : await api.printSpoolLabels({ spool_ids: ids, template });
      openBlobInNewTab(blob);
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(
        t('inventory.labels.error', 'Could not generate labels: {{msg}}', { msg }),
        'error',
      );
    } finally {
      setPending(null);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start sm:items-center justify-center p-4 overflow-y-auto">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-3xl bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] overflow-hidden flex flex-col my-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Printer className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">
              {t('inventory.labels.title', 'Print spool labels')}
            </h2>
            {selectedCount > 0 && (
              <span className="text-sm text-bambu-gray">
                ({t('inventory.labels.selectedCount', '{{count}} selected', { count: selectedCount })})
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
            aria-label={t('common.close', 'Close')}
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Search + material chips */}
        <div className="p-4 space-y-2 border-b border-bambu-dark-tertiary">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray pointer-events-none" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t('inventory.labels.searchPlaceholder', 'Search name, brand, or #ID')}
              className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>
          {materials.length > 1 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-xs text-bambu-gray mr-1">
                {t('inventory.labels.filterByMaterial', 'Material:')}
              </span>
              <button
                type="button"
                onClick={() => setMaterialFilter('')}
                className={`px-2 py-0.5 text-xs rounded-full border transition ${
                  materialFilter === ''
                    ? 'bg-bambu-green text-bambu-dark border-bambu-green'
                    : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:border-bambu-gray'
                }`}
              >
                {t('inventory.labels.allMaterials', 'All')}
              </button>
              {materials.map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMaterialFilter(m)}
                  className={`px-2 py-0.5 text-xs rounded-full border transition ${
                    materialFilter === m
                      ? 'bg-bambu-green text-bambu-dark border-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:border-bambu-gray'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Action bar */}
        <div className="px-4 pt-3 pb-2 flex items-center justify-between gap-3 flex-wrap">
          <span className="text-sm text-bambu-gray">
            {t('inventory.labels.pickSpools', 'Pick which spools to print labels for:')}
          </span>
          <div className="flex items-center gap-3 text-xs">
            <button
              type="button"
              onClick={allVisibleChecked ? deselectVisible : selectAllVisible}
              disabled={visibleSpools.length === 0}
              className="text-bambu-green hover:underline disabled:opacity-50 disabled:no-underline disabled:cursor-not-allowed"
            >
              {allVisibleChecked
                ? t('inventory.labels.deselectVisible', 'Deselect visible')
                : t('inventory.labels.selectVisible', 'Select all visible ({{count}})', {
                    count: visibleSpools.length,
                  })}
            </button>
            <button
              type="button"
              onClick={clearAll}
              disabled={selectedCount === 0}
              className="text-bambu-gray hover:text-white hover:underline disabled:opacity-50 disabled:no-underline disabled:cursor-not-allowed"
            >
              {t('inventory.labels.clearAll', 'Clear all')}
            </button>
          </div>
        </div>

        {/* Spool list */}
        <div className="flex-1 overflow-y-auto px-2 pb-2 min-h-0">
          {visibleSpools.length === 0 ? (
            <div className="text-center text-sm text-bambu-gray py-6">
              {sortedSpools.length === 0
                ? t('inventory.labels.noSpoolsToShow', 'No spools to show. Adjust your filter and try again.')
                : t('inventory.labels.noMatches', 'No spools match the current search or filter.')}
            </div>
          ) : (
            <ul className="space-y-0.5">
              {visibleSpools.map((s) => {
                const checked = selectedIds.has(s.id);
                return (
                  <li key={s.id}>
                    <label className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-bambu-dark-tertiary/50 cursor-pointer">
                      {checked ? (
                        <CheckSquare className="w-4 h-4 text-bambu-green shrink-0" />
                      ) : (
                        <Square className="w-4 h-4 text-bambu-gray shrink-0" />
                      )}
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleOne(s.id)}
                        className="sr-only"
                      />
                      <span
                        className="w-4 h-4 rounded border border-black/20 shrink-0"
                        style={swatchStyle(s.rgba)}
                      />
                      <span className="flex-1 min-w-0 truncate text-sm text-white">
                        {spoolDisplayName(s)}
                      </span>
                      <span className="text-xs font-mono text-bambu-gray shrink-0">
                        #{s.id}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Templates — 2x2 grid on >= sm so all 4 plus the Cancel footer fit
            inside max-h-[90vh] even when browser chrome eats into the viewport
            (#1230). Stacked single column on mobile widths. */}
        <div className="px-3 pt-2 pb-2 grid grid-cols-1 sm:grid-cols-2 gap-2 border-t border-bambu-dark-tertiary">
          {TEMPLATE_OPTIONS.map((opt) => {
            const isPending = pending === opt.value;
            const label = t(`inventory.labels.templates.${opt.i18nKey}.label`, opt.fallbackLabel);
            const hint = t(`inventory.labels.templates.${opt.i18nKey}.hint`, opt.fallbackHint);
            return (
              <button
                key={opt.value}
                disabled={noSelection || pending !== null}
                onClick={() => handlePick(opt.value)}
                title={`${label} — ${hint}`}
                className="w-full text-left p-2.5 rounded-lg border border-bambu-dark-tertiary bg-bambu-dark hover:border-bambu-green hover:bg-bambu-green/10 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-bambu-dark-tertiary disabled:hover:bg-bambu-dark transition flex items-center gap-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-white text-sm truncate">{label}</div>
                  <div className="text-xs text-bambu-gray mt-0.5 truncate">{hint}</div>
                </div>
                {isPending && <Loader2 className="w-4 h-4 animate-spin text-bambu-green shrink-0" />}
              </button>
            );
          })}
        </div>

        <div className="flex justify-end gap-2 px-5 py-2 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose} disabled={pending !== null}>
            {t('common.cancel', 'Cancel')}
          </Button>
        </div>
      </div>
    </div>
  );
}
