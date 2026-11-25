import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { X, Loader2, Printer, CheckSquare, Square, Search, Download, Tag } from 'lucide-react';
import {
  api,
  ApiError,
  DEFAULT_PROJECT_LABEL_FIELDS,
  DEFAULT_ROLL_LENGTH_MM,
  DEFAULT_SPOOL_LABEL_FIELDS,
  LABEL_PRINTER_TAPES,
  ROLL_MAX_LENGTH_MM,
  MAX_PRINT_LABELS_PER_REQUEST,
  MAX_PRINT_TAPE_MM,
  PROJECT_LABEL_FIELDS,
  ROLL_MIN_LENGTH_MM,
  SPOOL_LABEL_FIELDS,
  type LabelRollSpec,
  type LabelSheetLayout,
  type ProjectLabelField,
  type SavedLabelTemplate,
  type SpoolLabelField,
  type SpoolLabelRequestTemplate,
  type SpoolLabelTemplate,
  type InventorySpool,
} from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { LabelSheetDiagram, LabelSheetLayoutEditor } from './LabelSheetLayoutEditor';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';
import { isLabelPrinterConfigured, useUiSettings } from '../hooks/useUiSettings';
import { printCapsProblem, printLabelsWithToasts } from '../utils/labelPrintJobs';
import { DEFAULT_ROLL_TAPE, LABEL_PREFS_STORAGE_KEY, clampRollLength } from '../utils/labelPrint';
import {
  DEFAULT_LAYOUT,
  MAX_PNG_SHEET_PAGES_X_DPI,
  draftFromLayout,
  formatMm,
  isLayoutDraft,
  layoutProblem,
  parseDraft,
  sheetPageCount,
  type LayoutDraft,
} from '../utils/labelSheetLayout';
import { getSwatchStyle, resolveSpoolColorName } from '../utils/colors';
import { useColorCatalogVersion } from '../hooks/useColorCatalogVersion';

/** Subset of InventorySpool the modal needs for checkbox rendering. */
type SpoolForLabel = Pick<
  InventorySpool,
  'id' | 'material' | 'subtype' | 'brand' | 'color_name' | 'color_name_is_synthesized' | 'rgba'
>;

/** The project a bag label is printed for (#3655). */
export interface ProjectForLabel {
  id: number;
  name: string;
  color: string | null;
}

interface SpoolLabelProps {
  /** All spools the modal can choose from. Typically the page's current
   *  filter result so the modal stays consistent with what the user sees. */
  availableSpools: SpoolForLabel[];
  /** IDs to pre-check when the modal opens. Per-card icon passes a single ID;
   *  the bulk header button passes every visible ID so the user lands in
   *  "all checked" and refines downward. */
  initialSelectedIds: number[];
  spoolmanMode: boolean;
  project?: undefined;
}

interface ProjectLabelProps {
  /** Print labels for this project's finished parts instead of spools: the
   *  spool list gives way to how many are in the bag, a note and the number
   *  of labels; the label sizes, sheets and output are the same (#3655). */
  project: ProjectForLabel;
  availableSpools?: undefined;
  initialSelectedIds?: undefined;
  spoolmanMode?: undefined;
}

type LabelTemplatePickerModalProps = {
  isOpen: boolean;
  onClose: () => void;
} & (SpoolLabelProps | ProjectLabelProps);

const NO_SPOOLS: SpoolForLabel[] = [];
const NO_IDS: number[] = [];

// Bounds of the project label inputs, as on the server (project_labels.py).
const MAX_PROJECT_QUANTITY = 100_000;
const MAX_PROJECT_NOTE_LENGTH = 200;
const MAX_PROJECT_COPIES = 100;

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
    value: 'avery_5160',
    i18nKey: 'avery5160',
    fallbackLabel: 'Avery 5160 — US Letter sheet (25.4 × 66.7 mm × 30)',
    fallbackHint: 'US sheet stock; 30 labels per Letter page.',
  },
];

const SHEET_CAPACITIES: Partial<Record<SpoolLabelTemplate, number>> = {
  avery_l7160: 21,
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

// The project label's lines (#3655), read from projectDetail.labels.fields.
const PROJECT_FIELD_LABELS: Record<ProjectLabelField, { i18nKey: string; fallback: string }> = {
  parent: { i18nKey: 'parent', fallback: 'Parent project' },
  name: { i18nKey: 'name', fallback: 'Project name' },
  note: { i18nKey: 'note', fallback: 'Note' },
  parts: { i18nKey: 'parts', fallback: 'Parts printed' },
  due_date: { i18nKey: 'dueDate', fallback: 'Due date' },
  printed: { i18nKey: 'printed', fallback: 'Last printed' },
  color: { i18nKey: 'color', fallback: 'Project colour' },
  qr: { i18nKey: 'qr', fallback: 'QR code' },
  quantity: { i18nKey: 'quantity', fallback: 'Quantity' },
  project_id: { i18nKey: 'projectId', fallback: 'Project ID' },
};

// What the label size dropdown holds: a built-in template, a one-off custom
// sheet, or a saved template by id (#3296).
type TemplateChoice = SpoolLabelTemplate | 'custom' | 'roll' | `saved:${number}`;

const SAVED_TEMPLATES_KEY = ['labelTemplates'];

function savedChoice(id: number): TemplateChoice {
  return `saved:${id}`;
}

function savedIdOf(choice: TemplateChoice): number | null {
  return choice.startsWith('saved:') ? Number(choice.slice('saved:'.length)) : null;
}

function isTemplateChoice(value: unknown): value is TemplateChoice {
  if (typeof value !== 'string') return false;
  if (value === 'custom' || value === 'roll' || /^saved:[1-9]\d*$/.test(value)) return true;
  return TEMPLATE_OPTIONS.some((o) => o.value === value);
}

interface LabelPrefs {
  template: TemplateChoice;
  /** Chosen lines per template: a 40 × 30 roll and an A4 sheet fit different amounts. */
  fields: Partial<Record<TemplateChoice, SpoolLabelField[]>>;
  /** The same for project labels (#3655), which have lines of their own. */
  projectFields: Partial<Record<TemplateChoice, ProjectLabelField[]>>;
  /** The one-off custom sheet, as last typed. */
  customDraft: LayoutDraft;
  monochrome: boolean;
  format: OutputFormat;
  dpi: PngDpi;
  /** Roll format (#3612): tape width in mm, '' = the label printer's tape. */
  rollTape: string;
  rollLength: number;
}

const DEFAULT_PREFS: LabelPrefs = {
  template: TEMPLATE_OPTIONS[0].value,
  fields: {},
  projectFields: {},
  customDraft: draftFromLayout(DEFAULT_LAYOUT),
  monochrome: false,
  format: 'pdf',
  dpi: 300,
  rollTape: '',
  rollLength: DEFAULT_ROLL_LENGTH_MM,
};

function loadPrefs(): LabelPrefs {
  let raw: unknown;
  try {
    raw = JSON.parse(localStorage.getItem(LABEL_PREFS_STORAGE_KEY) ?? 'null');
  } catch {
    return DEFAULT_PREFS;
  }
  if (!raw || typeof raw !== 'object') return DEFAULT_PREFS;
  const r = raw as Partial<Record<keyof LabelPrefs, unknown>>;
  const fields: LabelPrefs['fields'] = {};
  if (r.fields && typeof r.fields === 'object') {
    for (const [tpl, list] of Object.entries(r.fields as Record<string, unknown>)) {
      if (!isTemplateChoice(tpl) || !Array.isArray(list)) continue;
      fields[tpl] = list.filter((f): f is SpoolLabelField =>
        (SPOOL_LABEL_FIELDS as readonly string[]).includes(f),
      );
    }
  }
  const projectFields: LabelPrefs['projectFields'] = {};
  if (r.projectFields && typeof r.projectFields === 'object') {
    for (const [tpl, list] of Object.entries(r.projectFields as Record<string, unknown>)) {
      if (!isTemplateChoice(tpl) || !Array.isArray(list)) continue;
      projectFields[tpl] = list.filter((f): f is ProjectLabelField =>
        (PROJECT_LABEL_FIELDS as readonly string[]).includes(f),
      );
    }
  }
  return {
    template: isTemplateChoice(r.template) ? r.template : DEFAULT_PREFS.template,
    fields,
    projectFields,
    customDraft: isLayoutDraft(r.customDraft) ? r.customDraft : DEFAULT_PREFS.customDraft,
    monochrome: r.monochrome === true,
    format: r.format === 'png' ? 'png' : 'pdf',
    dpi: PNG_DPIS.includes(r.dpi as PngDpi) ? (r.dpi as PngDpi) : DEFAULT_PREFS.dpi,
    rollTape: (LABEL_PRINTER_TAPES as readonly unknown[]).includes(r.rollTape) ? (r.rollTape as string) : '',
    rollLength: typeof r.rollLength === 'number' ? clampRollLength(r.rollLength) : DEFAULT_ROLL_LENGTH_MM,
  };
}

function savePrefs(prefs: LabelPrefs): void {
  try {
    localStorage.setItem(LABEL_PREFS_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Private mode or storage disabled: the options just aren't remembered.
  }
}

function fieldsFor(prefs: LabelPrefs, template: TemplateChoice): SpoolLabelField[] {
  return prefs.fields[template] ?? DEFAULT_SPOOL_LABEL_FIELDS;
}

function projectFieldsFor(prefs: LabelPrefs, template: TemplateChoice): ProjectLabelField[] {
  return prefs.projectFields[template] ?? DEFAULT_PROJECT_LABEL_FIELDS;
}

/** A whole number from ``min`` to ``max``, or null. */
function wholeNumber(text: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(text)) return null;
  const n = Number(text);
  return n >= min && n <= max ? n : null;
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
function downloadBlob(blob: Blob, template: SpoolLabelRequestTemplate): void {
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
  availableSpools = NO_SPOOLS,
  initialSelectedIds = NO_IDS,
  spoolmanMode = false,
  project,
}: LabelTemplatePickerModalProps) {
  const { t, i18n } = useTranslation();
  // The spool filter below resolves colour names through the catalog; its
  // memo has to recompute when the catalog finishes loading (#3090).
  const colorCatalogVersion = useColorCatalogVersion();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  // Saving, editing and deleting templates is inventory:update, like storage
  // locations; anyone who can download labels can use them (#3296).
  const canManageTemplates = hasPermission('inventory:update');
  const queryClient = useQueryClient();
  const [pending, setPending] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState('');
  const [materialFilter, setMaterialFilter] = useState<string>('');
  const [sortMode, setSortMode] = useState<SortMode>('id');
  const [prefs, setPrefs] = useState<LabelPrefs>(DEFAULT_PREFS);
  const [startingPositionInput, setStartingPositionInput] = useState('1');
  const [rollLengthInput, setRollLengthInput] = useState(String(DEFAULT_ROLL_LENGTH_MM));
  const [directPending, setDirectPending] = useState(false);
  // Project labels (#3655): typed per print, not remembered.
  const [quantityInput, setQuantityInput] = useState('');
  const [noteInput, setNoteInput] = useState('');
  const [copiesInput, setCopiesInput] = useState('1');
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  // The object URL on screen. Freed when its replacement arrives, not when the
  // options change, so the old label stays visible while the new one renders.
  const previewUrlRef = useRef<string | null>(null);
  // A saved template being edited: its name and layout as typed, not yet saved.
  const [editing, setEditing] = useState<{ name: string; draft: LayoutDraft } | null>(null);
  // The name typed for "Save as template"; null while that form is closed.
  const [newTemplateName, setNewTemplateName] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // When the dialog last opened: a remembered template is only given up on
  // when a list fetched since then lacks it, not a cached one (#3296 review).
  const openedAtRef = useRef(0);

  const savedQuery = useQuery({
    queryKey: SAVED_TEMPLATES_KEY,
    queryFn: api.getLabelTemplates,
    enabled: isOpen,
    // Someone else may have saved or deleted one since the last open.
    staleTime: 0,
  });
  const savedTemplates = useMemo(() => savedQuery.data ?? [], [savedQuery.data]);
  // Direct printing is offered only with a label printer set up (#3612), and
  // only with inventory:update: a print uses up tape, a download does not.
  // Spool labels only: a project label (#3655) is downloaded as before.
  const { data: appSettings } = useUiSettings({ enabled: isOpen && !project });
  const printerConfigured =
    !project && hasPermission('inventory:update') && isLabelPrinterConfigured(appSettings);
  // ui-flags sends the tape to every user; an older backend omits it, and the
  // server then checks the tape itself.
  const printerTape = printerConfigured ? appSettings?.label_printer_tape || null : null;

  const saveMutation = useMutation({
    mutationFn: ({ id, name, layout }: { id?: number; name: string; layout: LabelSheetLayout }) =>
      id === undefined ? api.createLabelTemplate({ name, layout }) : api.updateLabelTemplate(id, { name, layout }),
    onSuccess: (saved) => {
      // In the list before it is selected, so the selection doesn't fall back.
      queryClient.setQueryData<SavedLabelTemplate[]>(SAVED_TEMPLATES_KEY, (old = []) => [
        ...old.filter((tpl) => tpl.id !== saved.id),
        saved,
      ]);
      void queryClient.invalidateQueries({ queryKey: SAVED_TEMPLATES_KEY });
    },
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) {
        showToast(t('inventory.labels.custom.nameTaken', 'A label template with this name already exists.'), 'error');
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      showToast(t('inventory.labels.custom.saveFailed', 'Could not save the template: {{msg}}', { msg }), 'error');
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteLabelTemplate(id),
    onSuccess: (_, id) => {
      queryClient.setQueryData<SavedLabelTemplate[]>(SAVED_TEMPLATES_KEY, (old = []) =>
        old.filter((tpl) => tpl.id !== id),
      );
      void queryClient.invalidateQueries({ queryKey: SAVED_TEMPLATES_KEY });
    },
    onError: (err) => {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(t('inventory.labels.custom.deleteFailed', 'Could not delete the template: {{msg}}', { msg }), 'error');
    },
  });

  // Sync from caller and reset transient state on open. Intentionally not
  // reactive to props while open — once the user starts editing we don't want
  // a parent re-render to clobber their selection / filter / search. The
  // label options come back as the user last left them.
  useEffect(() => {
    if (isOpen) {
      openedAtRef.current = Date.now();
      const allowed = new Set(availableSpools.map((s) => s.id));
      setSelectedIds(new Set(initialSelectedIds.filter((id) => allowed.has(id))));
      setSearch('');
      setMaterialFilter('');
      setSortMode('id');
      const loaded = loadPrefs();
      setPrefs(loaded);
      setRollLengthInput(String(loaded.rollLength));
      setStartingPositionInput('1');
      setQuantityInput('');
      setNoteInput('');
      setCopiesInput('1');
      setPending(false);
      setEditing(null);
      setNewTemplateName(null);
      setConfirmDelete(false);
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

  // A failed refresh can still use a cached layout. With no usable layout,
  // show the default stock this time and keep the saved choice for a retry.
  const preferredId = savedIdOf(prefs.template);
  const missingOnError = savedQuery.isError && preferredId !== null && !editing &&
    !savedTemplates.some((tpl) => tpl.id === preferredId);
  // The roll format and direct printing are for spool labels (#3612): a project
  // label shows the default stock when the roll is the remembered choice.
  const rollForProject = project !== undefined && prefs.template === 'roll';
  const template = missingOnError || rollForProject ? DEFAULT_PREFS.template : prefs.template;
  // Spool labels and project labels (#3655) have their own lines to choose from.
  const fieldOptions: readonly string[] = project ? PROJECT_LABEL_FIELDS : SPOOL_LABEL_FIELDS;
  const defaultFields: readonly string[] = project ? DEFAULT_PROJECT_LABEL_FIELDS : DEFAULT_SPOOL_LABEL_FIELDS;
  const fields: readonly string[] = project ? projectFieldsFor(prefs, template) : fieldsFor(prefs, template);
  const fieldsKey = fields.join(',');

  // What a project label's bag holds, and how many labels to print.
  const quantity = wholeNumber(quantityInput, 1, MAX_PROJECT_QUANTITY);
  const quantityIsValid = quantityInput === '' || quantity !== null;
  const note = noteInput.trim();
  const copies = wholeNumber(copiesInput, 1, MAX_PROJECT_COPIES);

  // A saved template or a one-off custom sheet prints as "custom" with its
  // layout. The layout on screen is what prints, so an edit can be tried
  // before it is saved.
  const savedId = savedIdOf(template);
  const savedTemplate = savedId === null ? undefined : savedTemplates.find((tpl) => tpl.id === savedId);
  const isCustomSheet = template === 'custom' || savedId !== null;
  const activeDraft: LayoutDraft | null =
    template === 'custom'
      ? prefs.customDraft
      : editing
        ? editing.draft
        : savedTemplate
          ? draftFromLayout(savedTemplate.layout)
          : null;
  const problem = activeDraft ? layoutProblem(activeDraft) : null;
  const activeLayout = activeDraft && !problem ? parseDraft(activeDraft) : null;
  const isRoll = template === 'roll';
  const rollTape = prefs.rollTape || printerTape || (printerConfigured ? '50' : DEFAULT_ROLL_TAPE);
  const tapeFits = printerTape === null || rollTape === printerTape;
  // Preview = print: this format goes to the printer in black and white, so the
  // preview shows that and the option is locked on; downloads keep the choice.
  const printLocked = printerConfigured && isRoll && tapeFits;
  const monochrome = prefs.monochrome || printLocked;
  const rollLengthValue = Number(rollLengthInput);
  const rollLengthValid =
    rollLengthInput.trim() !== '' &&
    Number.isFinite(rollLengthValue) &&
    rollLengthValue >= ROLL_MIN_LENGTH_MM &&
    rollLengthValue <= ROLL_MAX_LENGTH_MM;
  const roll: LabelRollSpec | null = isRoll && rollLengthValid ? { tape: rollTape, length_mm: rollLengthValue } : null;
  const rollKey = roll ? JSON.stringify(roll) : '';
  // Waiting for the saved list, or a layout that can't print: nothing to send.
  const sheetReady = (!isCustomSheet || activeLayout !== null) && (!isRoll || rollLengthValid);
  const requestTemplate: SpoolLabelRequestTemplate = isCustomSheet
    ? 'custom'
    : isRoll
      ? 'roll'
      : (template as SpoolLabelTemplate);
  const layoutKey = activeLayout ? JSON.stringify(activeLayout) : '';

  // A remembered saved template that has since been deleted: back to the
  // default, and out of any edit of it, which would otherwise still send its
  // layout with the built-in template (a 422). Only once a list fetched since
  // the dialog opened says so.
  const savedListIsFresh =
    savedQuery.isSuccess && !savedQuery.isFetching && savedQuery.dataUpdatedAt >= openedAtRef.current;
  useEffect(() => {
    if (!isOpen || !savedListIsFresh || savedId === null) return;
    if (savedTemplates.some((tpl) => tpl.id === savedId)) return;
    setEditing(null);
    setNewTemplateName(null);
    setConfirmDelete(false);
    setPrefs((prev) => {
      const next = { ...prev, template: DEFAULT_PREFS.template };
      savePrefs(next);
      return next;
    });
  }, [isOpen, savedListIsFresh, savedTemplates, savedId]);
  // The preview shows the first spool that will print, in print order, or
  // the project's label once what was typed for it is valid.
  const previewSpoolId = sortedSpools.find((s) => selectedIds.has(s.id))?.id ?? null;
  const projectId = project?.id;
  const previewTarget = projectId !== undefined ? (quantityIsValid ? projectId : null) : previewSpoolId;

  function showPreview(url: string | null) {
    if (previewUrlRef.current) window.URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = url;
    setPreviewUrl(url);
  }

  // Re-render the preview a moment after the last change, dropping any
  // request still in flight so a slow answer can't overwrite a newer one.
  useEffect(() => {
    if (!isOpen || previewTarget === null || !sheetReady) {
      showPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    const timer = window.setTimeout(async () => {
      const options = {
        template: requestTemplate,
        ...(layoutKey ? { layout: JSON.parse(layoutKey) as LabelSheetLayout } : {}),
        ...(rollKey ? { roll: JSON.parse(rollKey) as LabelRollSpec } : {}),
        monochrome,
      };
      const chosen = fieldsKey ? fieldsKey.split(',') : [];
      try {
        const blob = projectId !== undefined
          ? await api.previewProjectLabel(
              { ...options, project_id: previewTarget, quantity, note: note || null, fields: chosen as ProjectLabelField[] },
              controller.signal,
            )
          : spoolmanMode
            ? await api.previewSpoolmanSpoolLabel(
                { ...options, spool_id: previewTarget, fields: chosen as SpoolLabelField[] },
                controller.signal,
              )
            : await api.previewSpoolLabel(
                { ...options, spool_id: previewTarget, fields: chosen as SpoolLabelField[] },
                controller.signal,
              );
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
  }, [
    isOpen,
    previewTarget,
    projectId,
    sheetReady,
    requestTemplate,
    layoutKey,
    rollKey,
    fieldsKey,
    monochrome,
    spoolmanMode,
    quantity,
    note,
  ]);

  // Free the last preview when the modal unmounts.
  useEffect(
    () => () => {
      if (previewUrlRef.current) window.URL.revokeObjectURL(previewUrlRef.current);
    },
    [],
  );

  if (!isOpen) return null;

  const selectedCount = selectedIds.size;
  // Labels this print makes: the ticked spools, or the project label's copies.
  const labelCount = project ? (copies ?? 0) : selectedCount;
  const noSelection = project ? !quantityIsValid || copies === null : selectedCount === 0;
  const sheetCapacity = isCustomSheet
    ? activeLayout
      ? activeLayout.cols * activeLayout.rows
      : undefined
    : SHEET_CAPACITIES[template as SpoolLabelTemplate];
  const startingPosition = Number(startingPositionInput);
  const startingPositionIsValid =
    sheetCapacity === undefined ||
    (Number.isInteger(startingPosition) && startingPosition >= 1 && startingPosition <= sheetCapacity);
  const templateOption = TEMPLATE_OPTIONS.find((o) => o.value === template) ?? TEMPLATE_OPTIONS[0];
  const directHint = printerTape
    ? t('inventory.labels.direct.hint', 'The label printer takes the roll format with {{tape}} mm tape.', { tape: printerTape })
    : t('inventory.labels.direct.hintAnyTape', 'The label printer takes the roll format.');
  let templateHint;
  if (template === 'custom') {
    templateHint = t('inventory.labels.custom.hint', 'Describe your own label sheet below.');
  } else if (isRoll) {
    templateHint = t('inventory.labels.roll.hint', 'Continuous tape cut to the length you choose, for label printers.');
  } else if (savedId !== null) {
    templateHint = savedTemplate ? layoutSummary(savedTemplate.layout) : null;
  } else if (project && sheetCapacity === undefined) {
    templateHint = t('projectDetail.labels.singleLabelPage', 'One label per page.');
  } else {
    templateHint = t(`inventory.labels.templates.${templateOption.i18nKey}.hint`, templateOption.fallbackHint);
  }
  // PNG of whole sheet pages is capped at pages × dpi, as on the server.
  const pngSheetPages =
    prefs.format === 'png' && sheetCapacity !== undefined && startingPositionIsValid
      ? sheetPageCount(sheetCapacity, labelCount, startingPosition)
      : 0;
  const pngTooLarge = pngSheetPages * prefs.dpi > MAX_PNG_SHEET_PAGES_X_DPI;
  const pngLimit = {
    maxPages: Math.floor(MAX_PNG_SHEET_PAGES_X_DPI / prefs.dpi),
    dpi: prefs.dpi,
    pages: pngSheetPages,
  };
  const canPrint = !noSelection && !pending && !directPending && startingPositionIsValid && sheetReady && !pngTooLarge;
  // Only the roll format on the printer's own tape goes to the label printer.
  // One print request is capped in labels and in metres of tape (#3612).
  const capsProblem = roll ? printCapsProblem(selectedCount, roll.length_mm) : null;
  const capsHint =
    capsProblem === 'tooMany'
      ? t('inventory.labels.direct.tooMany', 'At most {{max}} labels can be printed at once. Select fewer spools.', {
          max: MAX_PRINT_LABELS_PER_REQUEST,
        })
      : capsProblem === 'tooLong' && roll
        ? t(
            'inventory.labels.direct.tooLong',
            'These labels need {{total}} mm of tape; at most {{max}} mm fit in one print. Select fewer spools or use a shorter label.',
            { total: selectedCount * roll.length_mm, max: MAX_PRINT_TAPE_MM },
          )
        : null;
  const canDirectPrint =
    printerConfigured &&
    !noSelection &&
    !pending &&
    !directPending &&
    isRoll &&
    roll !== null &&
    tapeFits &&
    capsProblem === null;

  function updatePrefs(change: Partial<LabelPrefs>) {
    setPrefs((prev) => {
      const next = { ...prev, ...change };
      savePrefs(next);
      return next;
    });
  }

  function chooseTemplate(choice: TemplateChoice) {
    setEditing(null);
    setNewTemplateName(null);
    updatePrefs({ template: choice });
  }

  function layoutSummary(layout: LabelSheetLayout): string {
    return t(
      'inventory.labels.custom.summary',
      '{{page}} · {{width}} × {{height}} mm · {{capacity}} labels ({{cols}} × {{rows}})',
      {
        page:
          layout.page === 'a4'
            ? t('inventory.labels.custom.pageShortA4', 'A4')
            : t('inventory.labels.custom.pageShortLetter', 'Letter'),
        width: formatMm(layout.label_w_mm, i18n.language),
        height: formatMm(layout.label_h_mm, i18n.language),
        capacity: layout.cols * layout.rows,
        cols: layout.cols,
        rows: layout.rows,
      },
    );
  }

  function saveNewTemplate(name: string, layout: LabelSheetLayout) {
    // The new template keeps the lines chosen for the sheet it was made from,
    // as they are when the save completes: they can be ticked meanwhile.
    const source = template;
    saveMutation.mutate(
      { name: name.trim(), layout },
      {
        onSuccess: (saved) => {
          setEditing(null);
          setNewTemplateName(null);
          const choice = savedChoice(saved.id);
          setPrefs((prev) => {
            const next = {
              ...prev,
              template: choice,
              fields: { ...prev.fields, [choice]: fieldsFor(prev, source) },
              projectFields: { ...prev.projectFields, [choice]: projectFieldsFor(prev, source) },
            };
            savePrefs(next);
            return next;
          });
          showToast(t('inventory.labels.custom.saved', 'Template "{{name}}" saved', { name: saved.name }), 'success');
        },
      },
    );
  }

  function saveEditedTemplate(id: number, name: string, layout: LabelSheetLayout) {
    saveMutation.mutate(
      { id, name: name.trim(), layout },
      {
        onSuccess: (saved) => {
          setEditing(null);
          showToast(t('inventory.labels.custom.saved', 'Template "{{name}}" saved', { name: saved.name }), 'success');
        },
      },
    );
  }

  function deleteSavedTemplate(tpl: SavedLabelTemplate) {
    deleteMutation.mutate(tpl.id, {
      onSuccess: () => {
        setConfirmDelete(false);
        setEditing(null);
        updatePrefs({ template: DEFAULT_PREFS.template });
        showToast(t('inventory.labels.custom.deleted', 'Template "{{name}}" deleted', { name: tpl.name }), 'success');
      },
    });
  }

  function setFields(next: readonly string[]) {
    // Stored in print order, so the saved list reads like the label.
    const ordered = fieldOptions.filter((f) => next.includes(f));
    if (project) {
      updatePrefs({ projectFields: { ...prefs.projectFields, [template]: ordered as ProjectLabelField[] } });
    } else {
      updatePrefs({ fields: { ...prefs.fields, [template]: ordered as SpoolLabelField[] } });
    }
  }

  function fieldText(field: string): string {
    if (project) {
      const { i18nKey, fallback } = PROJECT_FIELD_LABELS[field as ProjectLabelField];
      return t(`projectDetail.labels.fields.${i18nKey}`, fallback);
    }
    const { i18nKey, fallback } = FIELD_LABELS[field as SpoolLabelField];
    return t(`inventory.labels.fields.${i18nKey}`, fallback);
  }

  function toggleField(field: string) {
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

  async function handleDirectPrint() {
    if (!canDirectPrint || !roll) return;
    const ids = sortedSpools.filter((s) => selectedIds.has(s.id)).map((s) => s.id);
    setDirectPending(true);
    try {
      // Always monochrome: the printer prints black on white (#3612).
      // The server takes the job at once; the toasts follow it from here, so
      // the dialog can close.
      await printLabelsWithToasts(
        spoolmanMode ? 'spoolman' : 'inventory',
        { spool_ids: ids, template: 'roll', roll, monochrome: true, fields: fields as SpoolLabelField[] },
        { t, showToast },
      );
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      showToast(t('inventory.labels.direct.error', 'Could not print: {{msg}}', { msg }), 'error');
    } finally {
      setDirectPending(false);
    }
  }

  async function handlePrint() {
    if (!canPrint) return;
    // Order matters: the backend (labels.py) prints labels in the same order
    // we send IDs. Use the sorted list so a "by colour" sort flows through to
    // the output instead of being clobbered by an ascending-ID re-sort.
    const ids = sortedSpools.filter((s) => selectedIds.has(s.id)).map((s) => s.id);
    const options = {
      template: requestTemplate,
      ...(activeLayout ? { layout: activeLayout } : {}),
      ...(roll ? { roll } : {}),
      monochrome: prefs.monochrome,
      starting_position: sheetCapacity === undefined ? 1 : startingPosition,
      format: prefs.format,
      dpi: prefs.dpi,
    };
    setPending(true);
    try {
      let blob: Blob;
      if (project) {
        // One item per label: the same bag, `copies` times.
        const item = { project_id: project.id, quantity, note: note || null };
        blob = await api.printProjectLabels({
          ...options,
          items: Array.from({ length: copies ?? 1 }, () => item),
          fields: fields as ProjectLabelField[],
        });
      } else {
        const request = { ...options, spool_ids: ids, fields: fields as SpoolLabelField[] };
        blob = spoolmanMode
          ? await api.printSpoolmanSpoolLabels(request)
          : await api.printSpoolLabels(request);
      }
      if (prefs.format === 'pdf') openBlobInNewTab(blob);
      else downloadBlob(blob, requestTemplate);
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
              {project
                ? t('projectDetail.labels.title', 'Print project label')
                : t('inventory.labels.title', 'Print spool labels')}
            </h2>
            {!project && selectedCount > 0 && (
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
          {project ? (
            <ProjectLabelInputs
              project={project}
              quantity={quantityInput}
              onQuantityChange={setQuantityInput}
              quantityIsValid={quantityIsValid}
              note={noteInput}
              onNoteChange={setNoteInput}
              copies={copiesInput}
              onCopiesChange={setCopiesInput}
              copiesAreValid={copies !== null}
            />
          ) : (
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
          )}

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
                onChange={(e) => chooseTemplate(e.target.value as TemplateChoice)}
                className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
              >
                {TEMPLATE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {t(`inventory.labels.templates.${opt.i18nKey}.label`, opt.fallbackLabel)}
                  </option>
                ))}
                {!project && <option value="roll">{t('inventory.labels.roll.option', 'Roll (label printer tape)')}</option>}
                {savedTemplates.length > 0 && (
                  <optgroup label={t('inventory.labels.custom.savedGroup', 'Saved templates')}>
                    {savedTemplates.map((tpl) => (
                      <option key={tpl.id} value={savedChoice(tpl.id)}>
                        {tpl.name}
                      </option>
                    ))}
                  </optgroup>
                )}
                <option value="custom">{t('inventory.labels.custom.option', 'Custom sheet…')}</option>
              </select>
              {missingOnError && (
                <p role="status" className="text-xs text-amber-500">
                  {t('inventory.labels.custom.loadFailed', 'Could not load the saved template. Check the selected label size before printing.')}
                </p>
              )}
              <p data-testid="label-template-hint" className="text-xs text-bambu-gray">
                {templateHint}
              </p>
            </div>

            {/* Roll: tape width and length (#3612). */}
            {isRoll && (
              <div className="flex flex-wrap items-end gap-3">
                <label className="space-y-0.5 block">
                  <span className="text-xs text-bambu-gray">{t('inventory.labels.roll.tape', 'Tape width')}</span>
                  <select
                    data-testid="label-roll-tape"
                    value={rollTape}
                    onChange={(e) => updatePrefs({ rollTape: e.target.value })}
                    className="block px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
                  >
                    {LABEL_PRINTER_TAPES.map((tape) => (
                      <option key={tape} value={tape}>
                        {tape} mm
                      </option>
                    ))}
                  </select>
                </label>
                <label className="space-y-0.5 block">
                  <span className="text-xs text-bambu-gray">{t('inventory.labels.roll.length', 'Length (mm)')}</span>
                  <input
                    data-testid="label-roll-length"
                    type="number"
                    min={ROLL_MIN_LENGTH_MM}
                    max={ROLL_MAX_LENGTH_MM}
                    step={1}
                    value={rollLengthInput}
                    onChange={(e) => {
                      setRollLengthInput(e.target.value);
                      const n = Number(e.target.value);
                      if (e.target.value.trim() !== '' && n >= ROLL_MIN_LENGTH_MM && n <= ROLL_MAX_LENGTH_MM) {
                        updatePrefs({ rollLength: n });
                      }
                    }}
                    className="w-24 px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
                  />
                </label>
                {!rollLengthValid && (
                  <p data-testid="label-roll-length-invalid" className="text-xs text-red-400 pb-1.5">
                    {t('inventory.labels.roll.lengthInvalid', 'Enter a length from {{min}} to {{max}} mm.', {
                      min: ROLL_MIN_LENGTH_MM,
                      max: ROLL_MAX_LENGTH_MM,
                    })}
                  </p>
                )}
              </div>
            )}

            {/* A saved template: its sheet, and Edit / Delete for those who may (#3296). */}
            {savedTemplate && !editing && (
              <div className="space-y-2">
                <LabelSheetDiagram layout={savedTemplate.layout} startingPosition={startingPositionIsValid ? startingPosition : 1} />
                {canManageTemplates && (
                  <div className="flex justify-center gap-4 text-xs">
                    <button
                      type="button"
                      data-testid="label-template-edit"
                      onClick={() =>
                        setEditing({ name: savedTemplate.name, draft: draftFromLayout(savedTemplate.layout) })
                      }
                      className="text-bambu-green hover:underline"
                    >
                      {t('inventory.labels.custom.edit', 'Edit')}
                    </button>
                    <button
                      type="button"
                      data-testid="label-template-delete"
                      onClick={() => setConfirmDelete(true)}
                      className="text-red-400 hover:underline"
                    >
                      {t('inventory.labels.custom.delete', 'Delete')}
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Editing a saved template: the name and the whole layout. */}
            {savedTemplate && editing && (
              <div className="space-y-3">
                <TemplateNameInput
                  value={editing.name}
                  onChange={(name) => setEditing({ ...editing, name })}
                />
                <LabelSheetLayoutEditor
                  draft={editing.draft}
                  onChange={(draft) => setEditing({ ...editing, draft })}
                  problem={problem}
                  startingPosition={startingPositionIsValid ? startingPosition : 1}
                />
                <div className="flex flex-wrap justify-end gap-2">
                  <Button variant="secondary" onClick={() => setEditing(null)} disabled={saveMutation.isPending}>
                    {t('common.cancel', 'Cancel')}
                  </Button>
                  <Button
                    variant="secondary"
                    data-testid="label-template-save-as-new"
                    disabled={!activeLayout || !editing.name.trim() || saveMutation.isPending}
                    onClick={() => activeLayout && saveNewTemplate(editing.name, activeLayout)}
                  >
                    {t('inventory.labels.custom.saveAsNew', 'Save as new')}
                  </Button>
                  <Button
                    data-testid="label-template-save-changes"
                    disabled={!activeLayout || !editing.name.trim() || saveMutation.isPending}
                    onClick={() => activeLayout && saveEditedTemplate(savedTemplate.id, editing.name, activeLayout)}
                  >
                    {t('inventory.labels.custom.saveChanges', 'Save changes')}
                  </Button>
                </div>
              </div>
            )}

            {/* A one-off custom sheet, and saving it under a name. */}
            {template === 'custom' && (
              <div className="space-y-3">
                <LabelSheetLayoutEditor
                  draft={prefs.customDraft}
                  onChange={(customDraft) => updatePrefs({ customDraft })}
                  problem={problem}
                  startingPosition={startingPositionIsValid ? startingPosition : 1}
                />
                {canManageTemplates &&
                  (newTemplateName === null ? (
                    <button
                      type="button"
                      data-testid="label-template-save-as"
                      disabled={!activeLayout}
                      onClick={() => setNewTemplateName('')}
                      className="text-xs text-bambu-green hover:underline disabled:opacity-50 disabled:no-underline disabled:cursor-not-allowed"
                    >
                      {t('inventory.labels.custom.saveAs', 'Save as template…')}
                    </button>
                  ) : (
                    <div className="flex flex-wrap items-end gap-2">
                      <div className="flex-1 min-w-40">
                        <TemplateNameInput value={newTemplateName} onChange={setNewTemplateName} autoFocus />
                      </div>
                      <Button variant="secondary" onClick={() => setNewTemplateName(null)} disabled={saveMutation.isPending}>
                        {t('common.cancel', 'Cancel')}
                      </Button>
                      <Button
                        data-testid="label-template-save"
                        disabled={!activeLayout || !newTemplateName.trim() || saveMutation.isPending}
                        onClick={() => activeLayout && saveNewTemplate(newTemplateName, activeLayout)}
                      >
                        {t('inventory.labels.custom.save', 'Save')}
                      </Button>
                    </div>
                  ))}
              </div>
            )}

            {/* Preview */}
            <div className="space-y-1">
              <div className="text-sm font-medium text-white">
                {project || previewSpoolId === null
                  ? t('inventory.labels.preview', 'Preview')
                  : t('inventory.labels.previewOf', 'Preview of #{{id}}', { id: previewSpoolId })}
              </div>
              <div
                data-testid="label-preview"
                className="relative flex items-center justify-center min-h-40 p-3 rounded-lg bg-bambu-dark border border-bambu-dark-tertiary"
              >
                {previewTarget === null ? (
                  <span className="text-sm text-bambu-gray text-center">
                    {project
                      ? t('projectDetail.labels.previewInvalid', 'Enter a valid quantity to see the label.')
                      : t('inventory.labels.previewNoSelection', 'Select a spool to see its label.')}
                  </span>
                ) : !sheetReady ? (
                  <span className="text-sm text-bambu-gray text-center">
                    {t('inventory.labels.custom.previewWaiting', 'The preview appears once the sheet layout is complete.')}
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
              {!project && (
                <p className="text-xs text-bambu-gray">
                  {t('inventory.labels.previewHint', 'Lines that don\'t fit on this label size are left out.')}
                </p>
              )}
            </div>

            {/* Label content */}
            <fieldset className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <legend className="text-sm font-medium text-white">
                  {t('inventory.labels.content', 'Print on the label')}
                </legend>
                <button
                  type="button"
                  onClick={() => setFields(defaultFields)}
                  className="text-xs text-bambu-green hover:underline"
                >
                  {t('inventory.labels.resetFields', 'Reset to default')}
                </button>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-3 gap-y-1">
                {fieldOptions.map((field) => {
                  const checked = fields.includes(field);
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
                      <span className="text-sm text-white">{fieldText(field)}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            {/* Print options */}
            <div className="space-y-3">
              <label
                className={`inline-flex items-center gap-2 select-none ${printLocked ? 'opacity-70' : 'cursor-pointer'}`}
              >
                {monochrome ? (
                  <CheckSquare className="w-4 h-4 text-bambu-green shrink-0" />
                ) : (
                  <Square className="w-4 h-4 text-bambu-gray shrink-0" />
                )}
                <input
                  data-testid="label-monochrome"
                  type="checkbox"
                  checked={monochrome}
                  disabled={printLocked}
                  onChange={(e) => updatePrefs({ monochrome: e.target.checked })}
                  className="sr-only"
                />
                <span className="text-sm text-white">
                  {t('inventory.labels.monochrome', 'Monochrome (black & white printer)')}
                </span>
                <span className="text-xs text-bambu-gray">
                  {printLocked
                    ? t('inventory.labels.direct.monochromeForced', 'Always black and white when printing on the label printer.')
                    : t('inventory.labels.monochromeHint', 'Drops the colour swatch and widens the text')}
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

              {sheetCapacity !== undefined && (
                <p data-testid="label-scale-hint" className="text-xs text-bambu-gray">
                  {t(
                    'inventory.labels.scaleHint',
                    'Print sheets at 100% (actual size), not "Fit to page": a shrunk page drifts off the labels.',
                  )}
                </p>
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
              {pngTooLarge && (
                <p data-testid="label-png-too-large" className="text-xs text-red-400">
                  {project
                    ? t('projectDetail.labels.pngTooLarge', pngLimit)
                    : t(
                        'inventory.labels.pngTooLarge',
                        'PNG export is limited to {{maxPages}} sheet pages at {{dpi}} dpi, and this is {{pages}}. Use PDF, a lower resolution or fewer spools.',
                        pngLimit,
                      )}
                </p>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2 px-5 py-3 border-t border-bambu-dark-tertiary">
          {printerConfigured && (
            <p data-testid="label-direct-hint" className="flex-1 min-w-48 text-xs text-bambu-gray">
              {capsHint ??
                (isRoll && tapeFits
                ? t('inventory.labels.direct.note', 'Printed in black and white on the label printer.')
                : directHint)}
            </p>
          )}
          <Button variant="secondary" onClick={onClose} disabled={pending || directPending}>
            {t('common.cancel', 'Cancel')}
          </Button>
          {printerConfigured && (
            <Button
              data-testid="label-direct-print-button"
              variant="secondary"
              onClick={handleDirectPrint}
              disabled={!canDirectPrint}
            >
              {directPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Tag className="w-4 h-4" />}
              {t('inventory.labels.direct.print', 'Print on label printer')}
            </Button>
          )}
          <Button
            data-testid="label-print-button"
            onClick={handlePrint}
            disabled={!canPrint}
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

      {confirmDelete && savedTemplate && (
        <ConfirmModal
          title={t('inventory.labels.custom.deleteTitle', 'Delete label template')}
          message={t(
            'inventory.labels.custom.deleteMessage',
            'Delete "{{name}}" for everyone? Labels already printed are not affected.',
            { name: savedTemplate.name },
          )}
          confirmText={t('inventory.labels.custom.delete', 'Delete')}
          variant="danger"
          overlayZIndex="z-[60]"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteSavedTemplate(savedTemplate)}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}

/** The left column of a project label (#3655): what is in this bag, and how many labels. */
function ProjectLabelInputs({
  project,
  quantity,
  onQuantityChange,
  quantityIsValid,
  note,
  onNoteChange,
  copies,
  onCopiesChange,
  copiesAreValid,
}: {
  project: ProjectForLabel;
  quantity: string;
  onQuantityChange: (value: string) => void;
  quantityIsValid: boolean;
  note: string;
  onNoteChange: (value: string) => void;
  copies: string;
  onCopiesChange: (value: string) => void;
  copiesAreValid: boolean;
}) {
  const { t } = useTranslation();
  const inputClass =
    'w-full px-3 py-2 bg-bambu-dark border rounded-lg text-white text-sm placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green';
  return (
    <div data-testid="project-label-inputs" className="p-4 space-y-4 md:border-r border-bambu-dark-tertiary">
      <div className="flex items-center gap-2">
        <span
          className="w-3 h-3 rounded-full shrink-0"
          style={{ backgroundColor: project.color || '#6b7280' }}
        />
        <span className="text-sm font-medium text-white truncate">{project.name}</span>
      </div>
      <p className="text-xs text-bambu-gray">
        {t(
          'projectDetail.labels.intro',
          'A label for the bag or box this project\'s finished parts go in. Its QR code opens this project.',
        )}
      </p>

      <div className="space-y-1">
        <label htmlFor="project-label-quantity" className="block text-sm text-white">{t('projectDetail.labels.quantity', 'Quantity in the bag')}</label>
        <input
          id="project-label-quantity"
          data-testid="project-label-quantity"
          aria-invalid={!quantityIsValid}
          aria-describedby="project-label-quantity-help"
          type="text"
          inputMode="numeric"
          value={quantity}
          onChange={(e) => onQuantityChange(e.target.value)}
          className={`${inputClass} ${quantityIsValid ? 'border-bambu-dark-tertiary' : 'border-red-400'}`}
        />
        <span id="project-label-quantity-help" className={`block text-xs ${quantityIsValid ? 'text-bambu-gray' : 'text-red-400'}`}>
          {quantityIsValid
            ? t('projectDetail.labels.quantityHint', 'Printed big at the bottom. Leave empty for none.')
            : t('projectDetail.labels.quantityInvalid', 'Enter a whole number from 1 to {{max}}.', {
                max: MAX_PROJECT_QUANTITY,
              })}
        </span>
      </div>

      <label className="block space-y-1">
        <span className="text-sm text-white">{t('projectDetail.labels.note', 'Note')}</span>
        <input
          data-testid="project-label-note"
          type="text"
          value={note}
          placeholder={t('projectDetail.labels.notePlaceholder', 'e.g. Order #1042 or Bag 2 of 3')}
          onChange={(e) => onNoteChange(Array.from(e.target.value).slice(0, MAX_PROJECT_NOTE_LENGTH).join(''))}
          className={`${inputClass} border-bambu-dark-tertiary`}
        />
      </label>

      <div className="space-y-1">
        <label htmlFor="project-label-copies" className="block text-sm text-white">{t('projectDetail.labels.copies', 'Number of labels')}</label>
        <input
          id="project-label-copies"
          data-testid="project-label-copies"
          aria-invalid={!copiesAreValid}
          aria-describedby={copiesAreValid ? undefined : 'project-label-copies-error'}
          type="text"
          inputMode="numeric"
          value={copies}
          onChange={(e) => onCopiesChange(e.target.value)}
          className={`w-24 px-3 py-2 bg-bambu-dark border rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green ${
            copiesAreValid ? 'border-bambu-dark-tertiary' : 'border-red-400'
          }`}
        />
        {!copiesAreValid && (
          <span id="project-label-copies-error" className="block text-xs text-red-400">
            {t('projectDetail.labels.copiesInvalid', 'Enter a whole number from 1 to {{max}}.', {
              max: MAX_PROJECT_COPIES,
            })}
          </span>
        )}
      </div>
    </div>
  );
}

function TemplateNameInput({
  value,
  onChange,
  autoFocus = false,
}: {
  value: string;
  onChange: (name: string) => void;
  autoFocus?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <label className="block space-y-0.5">
      <span className="text-xs text-bambu-gray">{t('inventory.labels.custom.name', 'Template name')}</span>
      <input
        data-testid="label-template-name"
        type="text"
        maxLength={100}
        autoFocus={autoFocus}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-2 py-1 bg-bambu-dark border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
      />
    </label>
  );
}
