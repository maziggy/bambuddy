import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Printer, CheckSquare, Square, Search, Download } from 'lucide-react';
import {
  api,
  DEFAULT_SPOOL_LABEL_FIELDS,
  SPOOL_LABEL_FIELDS,
  type SpoolLabelField,
  type SpoolLabelTemplate,
  type InventorySpool,
} from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { getSwatchStyle, resolveSpoolColorName } from '../utils/colors';
import { useColorCatalogVersion } from '../hooks/useColorCatalogVersion';

/** Subset of InventorySpool the modal needs for checkbox rendering. */
type SpoolForLabel = Pick<
  InventorySpool,
  'id' | 'material' | 'subtype' | 'brand' | 'color_name' | 'color_name_is_synthesized' | 'rgba'
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
    value: 'ams_holder_74x33',
    i18nKey: 'amsHolderSmall',
    fallbackLabel: 'AMS holder — small (74 × 33 mm)',
    fallbackHint: 'Single label per page; matches the printable label from MakerWorld model 752566 (AMS Filament Label Holder).',
  },
  {
    value: 'ams_holder_75x55',
    i18nKey: 'amsHolderLarge',
    fallbackLabel: 'AMS holder — large (75 × 55 mm)',
    fallbackHint: 'Single label per page; fits the cardstock-insert variant of the AMS Filament Label Holder. Roomy enough for swatch, brand, material, ID, and QR code.',
  },
  {
    value: 'box_40x30',
    i18nKey: 'box40x30',
    fallbackLabel: 'Box label (40 × 30 mm)',
    fallbackHint: 'Single label per page; common DK/Brother roll size, good for filament-bag and storage-bin labels.',
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
    value: 'avery_3490',
    i18nKey: 'avery3490',
    fallbackLabel: 'Avery 3490 — A4 sheet (36 × 70 mm × 24)',
    fallbackHint: 'EU sheet stock; 24 labels per A4 page, edge to edge.',
  },
  {
    value: 'avery_5160',
    i18nKey: 'avery5160',
    fallbackLabel: 'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
    fallbackHint: 'US sheet stock; 30 labels per Letter page.',
  },
];

const SHEET_CAPACITIES: Partial<Record<SpoolLabelTemplate, number>> = {
  avery_l7160: 21,
  avery_3490: 24,
  avery_5160: 30,
};

type OutputFormat = 'pdf' | 'png';
type PngDpi = 203 | 300 | 600;
const PNG_DPIS: PngDpi[] = [203, 300, 600];

// What the checkboxes read, keyed by SpoolLabelField (#2981).
const FIELD_LABELS: Record<SpoolLabelField, { i18nKey: string; fallback: string }> = {
  brand: { i18nKey: 'brand', fallback: 'Brand' },
  material: { i18nKey: 'material', fallback: 'Material and subtype' },
  hex: { i18nKey: 'hex', fallback: 'Colour code' },
  name: { i18nKey: 'name', fallback: 'Colour or filament name' },
  location: { i18nKey: 'location', fallback: 'Storage location' },
  material_number: { i18nKey: 'materialNumber', fallback: 'Material number' },
  temps: { i18nKey: 'temps', fallback: 'Nozzle temperature' },
  weight: { i18nKey: 'weight', fallback: 'Net weight' },
  note: { i18nKey: 'note', fallback: 'Note' },
  added: { i18nKey: 'added', fallback: 'Date added' },
  qr: { i18nKey: 'qr', fallback: 'QR code' },
  spool_id: { i18nKey: 'spoolId', fallback: 'Spool ID' },
};

// Remembered per browser. Printing only needs inventory access, and a server
// setting would need settings access to save.
const PREFS_STORAGE_KEY = 'bambuddy-label-print-options';

interface LabelPrefs {
  template: SpoolLabelTemplate;
  /** Chosen lines per template: a 40 × 30 roll and an A4 sheet fit different amounts. */
  fields: Partial<Record<SpoolLabelTemplate, SpoolLabelField[]>>;
  monochrome: boolean;
  format: OutputFormat;
  dpi: PngDpi;
}

const DEFAULT_PREFS: LabelPrefs = {
  template: TEMPLATE_OPTIONS[0].value,
  fields: {},
  monochrome: false,
  format: 'pdf',
  dpi: 300,
};

function loadPrefs(): LabelPrefs {
  let raw: unknown;
  try {
    raw = JSON.parse(localStorage.getItem(PREFS_STORAGE_KEY) ?? 'null');
  } catch {
    return DEFAULT_PREFS;
  }
  if (!raw || typeof raw !== 'object') return DEFAULT_PREFS;
  const r = raw as Partial<Record<keyof LabelPrefs, unknown>>;
  const templates = TEMPLATE_OPTIONS.map((o) => o.value);
  const fields: LabelPrefs['fields'] = {};
  if (r.fields && typeof r.fields === 'object') {
    for (const [tpl, list] of Object.entries(r.fields as Record<string, unknown>)) {
      if (!templates.includes(tpl as SpoolLabelTemplate) || !Array.isArray(list)) continue;
      fields[tpl as SpoolLabelTemplate] = list.filter((f): f is SpoolLabelField =>
        (SPOOL_LABEL_FIELDS as readonly string[]).includes(f),
      );
    }
  }
  return {
    template: templates.includes(r.template as SpoolLabelTemplate)
      ? (r.template as SpoolLabelTemplate)
      : DEFAULT_PREFS.template,
    fields,
    monochrome: r.monochrome === true,
    format: r.format === 'png' ? 'png' : 'pdf',
    dpi: PNG_DPIS.includes(r.dpi as PngDpi) ? (r.dpi as PngDpi) : DEFAULT_PREFS.dpi,
  };
}

function savePrefs(prefs: LabelPrefs): void {
  try {
    localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Private mode or storage disabled: the options just aren't remembered.
  }
}

function fieldsFor(prefs: LabelPrefs, template: SpoolLabelTemplate): SpoolLabelField[] {
  return prefs.fields[template] ?? DEFAULT_SPOOL_LABEL_FIELDS;
}

function openBlobInNewTab(blob: Blob): void {
  const url = window.URL.createObjectURL(blob);
  // Do NOT pass `noopener,noreferrer`: per the WindowFeatures spec, `noopener`
  // forces window.open to return `null` even on success, which made the
  // `if (!win)` popup-block fallback below fire on EVERY click — so the blob
  // tab opened (downloading a random-named PDF on systems without an inline
  // viewer) AND the `<a download>` fallback fired (downloading a second copy
  // named bambuddy-labels.pdf). Two identical PDFs per click — issue #1628.
  // The blob is same-origin, the destination is a passive PDF tab with no
  // script context, and `noreferrer` is a no-op for blob URLs, so dropping
  // these flags has no security impact.
  const win = window.open(url, '_blank');
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

/** Save a PNG, or a ZIP of PNGs when there were several pages. */
function downloadBlob(blob: Blob, template: SpoolLabelTemplate): void {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `bambuddy-labels-${template}.${blob.type === 'application/zip' ? 'zip' : 'png'}`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
}

// Thin wrapper over `getSwatchStyle` from utils/colors so the modal's render
// sites keep their existing call shape. Transparent (alpha=00) spools now
// render as a checkerboard pattern instead of collapsing to solid black
// (#1545).
function swatchStyle(rgba: string | null | undefined): React.CSSProperties {
  return getSwatchStyle(rgba);
}

function labelColorName(s: SpoolForLabel): string | null {
  return resolveSpoolColorName(s.color_name, s.rgba, s.color_name_is_synthesized);
}

function spoolDisplayName(s: SpoolForLabel): string {
  // Resolved, not stored: most Bambu spools arrive with no colour name on the
  // tag, and picking a label template for "PLA Silk" tells you nothing about
  // which of six red spools you are looking at (#3090).
  const head = labelColorName(s) ?? `${s.material}${s.subtype ? ` ${s.subtype}` : ''}`;
  const brand = s.brand ? ` · ${s.brand}` : '';
  return `${head}${brand}`;
}

/** Build a lowercased haystack that the search input matches against. */
function searchableText(s: SpoolForLabel): string {
  return [labelColorName(s), s.color_name, s.material, s.subtype, s.brand, `#${s.id}`]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

type SortMode = 'id' | 'color';

/** Sort key for the "by colour" mode (#1410).
 *
 * Returns a 2-tuple so JS array compare does the right thing without us having
 * to spell out a comparator: ``[bucket, position]``. Chromatic colours
 * (saturation above the threshold) go in bucket 0 ordered by HSL hue, so the
 * sheet reads as a continuous rainbow. Achromatic colours (white / grey /
 * black, plus missing/invalid rgba) go in bucket 1 ordered by lightness so the
 * neutrals trail at the end of the rainbow going dark → light. Multi-colour
 * spools sort on their primary ``rgba``; their ``extra_colors`` stripe is
 * still rendered on the label itself but doesn't drive the sort.
 */
function colorSortKey(rgba: string | null | undefined): [number, number] {
  if (!rgba) return [1, 0]; // unknown colour — bucket with the neutrals at black
  const cleaned = rgba.replace(/^#/, '').slice(0, 6);
  if (cleaned.length !== 6) return [1, 0];
  const r = parseInt(cleaned.slice(0, 2), 16);
  const g = parseInt(cleaned.slice(2, 4), 16);
  const b = parseInt(cleaned.slice(4, 6), 16);
  if ([r, g, b].some(Number.isNaN)) return [1, 0];

  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  const delta = max - min;
  // Saturation in the HSL definition. Achromatic cutoff at 0.1 is generous —
  // matches what feels "grey enough" to a user picking colours, without
  // sending dark muted colours like deep navy into the neutrals bucket.
  const s = delta === 0 ? 0 : delta / (1 - Math.abs(2 * l - 1));
  if (s < 0.1) return [1, l]; // neutrals: ordered black → white

  let h = 0;
  if (max === rn) h = ((gn - bn) / delta) % 6;
  else if (max === gn) h = (bn - rn) / delta + 2;
  else h = (rn - gn) / delta + 4;
  h = h * 60;
  if (h < 0) h += 360;
  return [0, h]; // chromatic: ordered by hue 0..360
}

export function LabelTemplatePickerModal({
  isOpen,
  onClose,
  availableSpools,
  initialSelectedIds,
  spoolmanMode,
}: LabelTemplatePickerModalProps) {
  const { t } = useTranslation();
  // The spool filter below resolves colour names through the catalog; its
  // memo has to recompute when the catalog finishes loading (#3090).
  const colorCatalogVersion = useColorCatalogVersion();
  const { showToast } = useToast();
  const [pending, setPending] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [materialFilter, setMaterialFilter] = useState<string>('');
  const [sortMode, setSortMode] = useState<SortMode>('id');
  const [prefs, setPrefs] = useState<LabelPrefs>(DEFAULT_PREFS);
  const [startingPositionInput, setStartingPositionInput] = useState('1');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // The object URL on screen. Freed when its replacement arrives, not when the
  // options change, so the old label stays visible while the new one renders.
  const previewUrlRef = useRef<string | null>(null);

  // Sync from caller and reset transient state on open. Intentionally not
  // reactive to props while open — once the user starts editing we don't want
  // a parent re-render to clobber their selection / filter / search. The
  // label options come back as the user last left them.
  useEffect(() => {
    if (isOpen) {
      const allowed = new Set(availableSpools.map((s) => s.id));
      setSelectedIds(new Set(initialSelectedIds.filter((id) => allowed.has(id))));
      setSearch('');
      setMaterialFilter('');
      setSortMode('id');
      setPrefs(loadPrefs());
      setStartingPositionInput('1');
      setPending(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  const sortedSpools = useMemo(() => {
    const copy = [...availableSpools];
    if (sortMode === 'color') {
      copy.sort((a, b) => {
        const ka = colorSortKey(a.rgba);
        const kb = colorSortKey(b.rgba);
        if (ka[0] !== kb[0]) return ka[0] - kb[0];
        if (ka[1] !== kb[1]) return ka[1] - kb[1];
        // Stable tiebreaker on ID so identical colours print in a deterministic
        // order across renders.
        return a.id - b.id;
      });
      return copy;
    }
    copy.sort((a, b) => a.id - b.id);
    return copy;
  }, [availableSpools, sortMode]);

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
    // Named so this memo depends on it: searchableText resolves colour names
    // through the catalog, which resolveSpoolColorName reads from module state
    // the linter cannot follow. Without it a query typed before the catalog
    // loads keeps its empty result (#3090).
    void colorCatalogVersion;
    const q = search.trim().toLowerCase();
    return sortedSpools.filter((s) => {
      if (materialFilter && (s.material || '').toUpperCase() !== materialFilter) return false;
      if (q && !searchableText(s).includes(q)) return false;
      return true;
    });
  }, [sortedSpools, search, materialFilter, colorCatalogVersion]);

  const allVisibleChecked =
    visibleSpools.length > 0 && visibleSpools.every((s) => selectedIds.has(s.id));

  const template = prefs.template;
  const fields = fieldsFor(prefs, template);
  const fieldsKey = fields.join(',');
  // The preview shows the first spool that will print, in print order.
  const previewSpoolId = sortedSpools.find((s) => selectedIds.has(s.id))?.id ?? null;

  function showPreview(url: string | null) {
    if (previewUrlRef.current) window.URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = url;
    setPreviewUrl(url);
  }

  // Re-render the preview a moment after the last change, dropping any
  // request still in flight so a slow answer can't overwrite a newer one.
  useEffect(() => {
    if (!isOpen || previewSpoolId === null) {
      showPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    const timer = window.setTimeout(async () => {
      const request = {
        spool_id: previewSpoolId,
        template,
        monochrome: prefs.monochrome,
        fields: fieldsKey ? (fieldsKey.split(',') as SpoolLabelField[]) : [],
      };
      try {
        const blob = spoolmanMode
          ? await api.previewSpoolmanSpoolLabel(request, controller.signal)
          : await api.previewSpoolLabel(request, controller.signal);
        if (controller.signal.aborted) return;
        showPreview(window.URL.createObjectURL(blob));
        setPreviewError(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        showPreview(null);
        setPreviewError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!controller.signal.aborted) setPreviewLoading(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [isOpen, previewSpoolId, template, fieldsKey, prefs.monochrome, spoolmanMode]);

  // Free the last preview when the modal unmounts.
  useEffect(
    () => () => {
      if (previewUrlRef.current) window.URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  if (!isOpen) return null;

  const selectedCount = selectedIds.size;
  const noSelection = selectedCount === 0;
  const sheetCapacity = SHEET_CAPACITIES[template];
  const startingPosition = Number(startingPositionInput);
  const startingPositionIsValid =
    sheetCapacity === undefined ||
    (Number.isInteger(startingPosition) && startingPosition >= 1 && startingPosition <= sheetCapacity);
  const templateOption = TEMPLATE_OPTIONS.find((o) => o.value === template) ?? TEMPLATE_OPTIONS[0];

  function updatePrefs(change: Partial<LabelPrefs>) {
    setPrefs((prev) => {
      const next = { ...prev, ...change };
      savePrefs(next);
      return next;
    });
  }

  function setFields(next: SpoolLabelField[]) {
    // Stored in print order, so the saved list reads like the label.
    const ordered = SPOOL_LABEL_FIELDS.filter((f) => next.includes(f));
    updatePrefs({ fields: { ...prefs.fields, [template]: ordered } });
  }

  function toggleField(field: SpoolLabelField) {
    setFields(fields.includes(field) ? fields.filter((f) => f !== field) : [...fields, field]);
  }

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

  async function handlePrint() {
    if (noSelection || pending || !startingPositionIsValid) return;
    // Order matters: the backend (labels.py) prints labels in the same order
    // we send IDs. Use the sorted list so a "by colour" sort flows through to
    // the output instead of being clobbered by an ascending-ID re-sort.
    const ids = sortedSpools.filter((s) => selectedIds.has(s.id)).map((s) => s.id);
    const request = {
      spool_ids: ids,
      template,
      monochrome: prefs.monochrome,
      starting_position: sheetCapacity === undefined ? 1 : startingPosition,
      fields,
      format: prefs.format,
      dpi: prefs.dpi,
    };
    setPending(true);
    try {
      const blob = spoolmanMode
        ? await api.printSpoolmanSpoolLabels(request)
        : await api.printSpoolLabels(request);
      if (prefs.format === 'pdf') openBlobInNewTab(blob);
      else downloadBlob(blob, template);
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(
        t('inventory.labels.error', 'Could not generate labels: {{msg}}', { msg }),
        'error',
      );
    } finally {
      setPending(false);
    }
  }

  const chipClass = (active: boolean) =>
    `px-2 py-0.5 text-xs rounded-full border transition ${
      active
        ? 'bg-bambu-green text-bambu-dark border-bambu-green'
        : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:border-bambu-gray'
    }`;

  return (
    <div className="fixed inset-0 z-50 flex items-start sm:items-center justify-center p-4 overflow-y-auto">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div
        data-testid="label-template-picker-panel"
        className="relative w-full max-w-5xl bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] overflow-clip flex flex-col my-auto"
      >
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

        {/* Two columns from md up: which spools on the left, what the label
            looks like on the right. Stacked and scrolled as one on phones. */}
        <div className="flex-1 min-h-0 overflow-y-auto md:overflow-hidden grid grid-cols-1 md:grid-cols-2">
          <div className="flex flex-col min-h-0 md:border-r border-bambu-dark-tertiary">
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
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs text-bambu-gray mr-1">
                  {t('inventory.labels.sortBy.label')}
                </span>
                <button
                  type="button"
                  onClick={() => setSortMode('id')}
                  className={`px-2 py-0.5 text-xs rounded-full border transition ${
                    sortMode === 'id'
                      ? 'bg-bambu-green text-bambu-dark border-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:border-bambu-gray'
                  }`}
                >
                  {t('inventory.labels.sortBy.id')}
                </button>
                <button
                  type="button"
                  onClick={() => setSortMode('color')}
                  className={`px-2 py-0.5 text-xs rounded-full border transition ${
                    sortMode === 'color'
                      ? 'bg-bambu-green text-bambu-dark border-bambu-green'
                      : 'bg-bambu-dark text-bambu-gray border-bambu-dark-tertiary hover:border-bambu-gray'
                  }`}
                >
                  {t('inventory.labels.sortBy.color')}
                </button>
              </div>
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
            <div className="flex-1 overflow-y-auto px-2 pb-2 min-h-0 max-h-72 md:max-h-none">
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

          </div>

          <div className="md:overflow-y-auto p-4 space-y-4 border-t md:border-t-0 border-bambu-dark-tertiary">
            {/* Label size */}
            <div className="space-y-1">
              <label htmlFor="label-template" className="text-sm font-medium text-white">
                {t('inventory.labels.template', 'Label size')}
              </label>
              <select
                id="label-template"
                data-testid="label-template-select"
                value={template}
                onChange={(e) => updatePrefs({ template: e.target.value as SpoolLabelTemplate })}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
              >
                {TEMPLATE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {t(`inventory.labels.templates.${opt.i18nKey}.label`, opt.fallbackLabel)}
                  </option>
                ))}
              </select>
              <p className="text-xs text-bambu-gray">
                {t(`inventory.labels.templates.${templateOption.i18nKey}.hint`, templateOption.fallbackHint)}
              </p>
            </div>

            {/* Preview */}
            <div className="space-y-1">
              <div className="text-sm font-medium text-white">
                {previewSpoolId === null
                  ? t('inventory.labels.preview', 'Preview')
                  : t('inventory.labels.previewOf', 'Preview of #{{id}}', { id: previewSpoolId })}
              </div>
              <div
                data-testid="label-preview"
                className="relative flex items-center justify-center min-h-40 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary"
              >
                {previewSpoolId === null ? (
                  <span className="text-sm text-bambu-gray text-center">
                    {t('inventory.labels.previewNoSelection', 'Select a spool to see its label.')}
                  </span>
                ) : previewError ? (
                  <span className="text-sm text-red-400 text-center">
                    {t('inventory.labels.previewError', 'Could not render the preview: {{msg}}', { msg: previewError })}
                  </span>
                ) : previewUrl ? (
                  <img
                    src={previewUrl}
                    alt={t('inventory.labels.previewAlt', 'Label preview')}
                    className="max-h-56 max-w-full object-contain bg-white shadow"
                  />
                ) : null}
                {previewLoading && (
                  <Loader2 className="absolute top-2 right-2 w-4 h-4 animate-spin text-bambu-green" />
                )}
              </div>
              <p className="text-xs text-bambu-gray">
                {t('inventory.labels.previewHint', 'Lines that don\'t fit on this label size are left out.')}
              </p>
            </div>

            {/* Label content */}
            <fieldset className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <legend className="text-sm font-medium text-white">
                  {t('inventory.labels.content', 'Print on the label')}
                </legend>
                <button
                  type="button"
                  onClick={() => setFields(DEFAULT_SPOOL_LABEL_FIELDS)}
                  className="text-xs text-bambu-green hover:underline"
                >
                  {t('inventory.labels.resetFields', 'Reset to default')}
                </button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1">
                {SPOOL_LABEL_FIELDS.map((field) => {
                  const checked = fields.includes(field);
                  const { i18nKey, fallback } = FIELD_LABELS[field];
                  return (
                    <label
                      key={field}
                      className="flex items-center gap-2 py-0.5 cursor-pointer select-none"
                    >
                      {checked ? (
                        <CheckSquare className="w-4 h-4 text-bambu-green shrink-0" />
                      ) : (
                        <Square className="w-4 h-4 text-bambu-gray shrink-0" />
                      )}
                      <input
                        type="checkbox"
                        data-testid={`label-field-${field}`}
                        checked={checked}
                        onChange={() => toggleField(field)}
                        className="sr-only"
                      />
                      <span className="text-sm text-white">
                        {t(`inventory.labels.fields.${i18nKey}`, fallback)}
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            {/* Print options */}
            <div className="space-y-3">
              <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                {prefs.monochrome ? (
                  <CheckSquare className="w-4 h-4 text-bambu-green shrink-0" />
                ) : (
                  <Square className="w-4 h-4 text-bambu-gray shrink-0" />
                )}
                <input
                  type="checkbox"
                  checked={prefs.monochrome}
                  onChange={(e) => updatePrefs({ monochrome: e.target.checked })}
                  className="sr-only"
                />
                <span className="text-sm text-white">
                  {t('inventory.labels.monochrome', 'Monochrome (black & white printer)')}
                </span>
                <span className="text-xs text-bambu-gray">
                  {t('inventory.labels.monochromeHint', 'Drops the colour swatch and widens the text')}
                </span>
              </label>

              {sheetCapacity !== undefined && (
                <div className="flex items-start gap-3">
                  <label
                    htmlFor="label-starting-position"
                    className="text-sm text-white whitespace-nowrap pt-1.5"
                  >
                    {t('inventory.labels.startingPosition', 'Starting label position')}
                  </label>
                  <input
                    id="label-starting-position"
                    data-testid="label-starting-position"
                    type="number"
                    min={1}
                    max={sheetCapacity}
                    step={1}
                    value={startingPositionInput}
                    onChange={(event) => setStartingPositionInput(event.target.value)}
                    aria-describedby="label-starting-position-help"
                    className="w-20 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                  <div
                    id="label-starting-position-help"
                    data-testid="label-starting-position-status"
                    className={`text-xs pt-1.5 ${startingPositionIsValid ? 'text-bambu-gray' : 'text-red-400'}`}
                  >
                    {!startingPositionIsValid
                      ? t(
                          'inventory.labels.startingPositionInvalid',
                          'Enter a whole number from 1 to {{capacity}}.',
                          { capacity: sheetCapacity },
                        )
                      : startingPosition === 1
                        ? t('inventory.labels.startingPositionFirst', 'Printing starts at position 1.')
                        : t(
                            'inventory.labels.startingPositionSkipped',
                            'Positions 1 through {{lastPosition}} will be left blank on the first sheet.',
                            { lastPosition: startingPosition - 1 },
                          )}
                  </div>
                </div>
              )}

              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-sm text-white mr-1">
                  {t('inventory.labels.format', 'Output:')}
                </span>
                <button
                  type="button"
                  data-testid="label-format-pdf"
                  aria-pressed={prefs.format === 'pdf'}
                  onClick={() => updatePrefs({ format: 'pdf' })}
                  className={chipClass(prefs.format === 'pdf')}
                >
                  PDF
                </button>
                <button
                  type="button"
                  data-testid="label-format-png"
                  aria-pressed={prefs.format === 'png'}
                  onClick={() => updatePrefs({ format: 'png' })}
                  className={chipClass(prefs.format === 'png')}
                >
                  PNG
                </button>
                {prefs.format === 'png' && (
                  <select
                    data-testid="label-dpi-select"
                    aria-label={t('inventory.labels.dpi', 'Resolution')}
                    value={prefs.dpi}
                    onChange={(e) => updatePrefs({ dpi: Number(e.target.value) as PngDpi })}
                    className="ml-2 px-2 py-0.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-xs focus:outline-none focus:border-bambu-green"
                  >
                    {PNG_DPIS.map((dpi) => (
                      <option key={dpi} value={dpi}>
                        {dpi} dpi
                      </option>
                    ))}
                  </select>
                )}
              </div>
              {prefs.format === 'png' && (
                <p className="text-xs text-bambu-gray">
                  {t(
                    'inventory.labels.pngHint',
                    'For label printer software that takes images. Match your printer\'s resolution; several labels come as a ZIP.',
                  )}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 px-5 py-3 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose} disabled={pending}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button
            data-testid="label-print-button"
            onClick={handlePrint}
            disabled={noSelection || pending || !startingPositionIsValid}
          >
            {pending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : prefs.format === 'pdf' ? (
              <Printer className="w-4 h-4" />
            ) : (
              <Download className="w-4 h-4" />
            )}
            {prefs.format === 'pdf'
              ? t('inventory.labels.createPdf', 'Create PDF')
              : t('inventory.labels.downloadPng', 'Download PNG')}
          </Button>
        </div>
      </div>
    </div>
  );
}
