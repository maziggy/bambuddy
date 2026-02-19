import { useState, useMemo, type ReactNode } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  Plus, Loader2, Trash2, Archive, RotateCcw, Edit2, Package,
  Search, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight,
  TrendingDown, Layers, Printer, AlertTriangle, X, Clock, LayoutGrid, TableProperties, Columns,
  ArrowUp, ArrowDown, ArrowUpDown,
} from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool, SpoolAssignment } from '../api/client';
import { Button } from '../components/Button';
import { SpoolFormModal } from '../components/SpoolFormModal';
import { ConfirmModal } from '../components/ConfirmModal';
import { ColumnConfigModal, type ColumnConfig } from '../components/ColumnConfigModal';
import { useToast } from '../contexts/ToastContext';
import { resolveSpoolColorName } from '../utils/colors';
import { getCurrencySymbol } from '../utils/currency';

type ArchiveFilter = 'active' | 'archived';
type UsageFilter = 'all' | 'used' | 'new';
type ViewMode = 'table' | 'cards';
type SortDirection = 'asc' | 'desc';
type SortState = { column: string; direction: SortDirection } | null;

// Column definitions for the inventory table
const COLUMN_CONFIG_KEY = 'bambuddy-inventory-columns';

const DEFAULT_COLUMNS: ColumnConfig[] = [
  { id: 'id', label: '#', visible: true },
  { id: 'added_time', label: 'Added', visible: true },
  { id: 'encode_time', label: 'Encoded', visible: false },
  { id: 'last_used_time', label: 'Last Used', visible: false },
  { id: 'rgba', label: 'Color', visible: true },
  { id: 'material', label: 'Material', visible: true },
  { id: 'subtype', label: 'Subtype', visible: true },
  { id: 'color_name', label: 'Color Name', visible: false },
  { id: 'brand', label: 'Brand', visible: true },
  { id: 'slicer_filament', label: 'Slicer Filament', visible: false },
  { id: 'location', label: 'Location', visible: true },
  { id: 'label_weight', label: 'Label', visible: true },
  { id: 'net', label: 'Net', visible: true },
  { id: 'gross', label: 'Gross', visible: false },
  { id: 'added_full', label: 'Full', visible: false },
  { id: 'used', label: 'Used', visible: false },
  { id: 'printed_total', label: 'Printed Total', visible: false },
  { id: 'printed_since_weight', label: 'Printed Since Weight', visible: false },
  { id: 'note', label: 'Note', visible: false },
  { id: 'pa_k', label: 'PA(K)', visible: true },
  { id: 'tag_id', label: 'Tag ID', visible: false },
  { id: 'data_origin', label: 'Data Origin', visible: false },
  { id: 'tag_type', label: 'Linked Tag Type', visible: false },
  { id: 'remaining', label: 'Remaining', visible: true },
  { id: 'cost_per_kg', label: 'Cost/kg', visible: false },
];

function loadColumnConfig(): ColumnConfig[] {
  try {
    const stored = localStorage.getItem(COLUMN_CONFIG_KEY);
    if (stored) {
      const parsed = JSON.parse(stored) as ColumnConfig[];
      const defaultIds = new Set(DEFAULT_COLUMNS.map((c) => c.id));
      const storedIds = new Set(parsed.map((c) => c.id));
      // Keep stored columns that still exist in defaults
      const validStored = parsed.filter((c) => defaultIds.has(c.id));
      // Add any new default columns not in stored config
      const newColumns = DEFAULT_COLUMNS.filter((c) => !storedIds.has(c.id));
      return [...validStored, ...newColumns];
    }
  } catch {
    // Ignore errors
  }
  return DEFAULT_COLUMNS.map((c) => ({ ...c }));
}

function saveColumnConfig(config: ColumnConfig[]) {
  try {
    localStorage.setItem(COLUMN_CONFIG_KEY, JSON.stringify(config));
  } catch {
    // Ignore errors
  }
}

function formatWeight(g: number, useKg = false): string {
  if (useKg && g >= 1000) return `${(g / 1000).toFixed(1)}kg`;
  return `${Math.round(g)}g`;
}

// Material color mapping for pills
const MATERIAL_COLORS: Record<string, string> = {
  PLA: 'bg-green-500/20 text-green-400',
  ABS: 'bg-red-500/20 text-red-400',
  PETG: 'bg-blue-500/20 text-blue-400',
  TPU: 'bg-purple-500/20 text-purple-400',
  ASA: 'bg-orange-500/20 text-orange-400',
  PA: 'bg-yellow-500/20 text-yellow-400',
  PC: 'bg-cyan-500/20 text-cyan-400',
  PET: 'bg-sky-500/20 text-sky-400',
};

type TFn = (key: string) => string;

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: '2-digit' });
}

type CellCtx = {
  spool: InventorySpool;
  remaining: number;
  pct: number;
  assignmentMap: Record<number, SpoolAssignment>;
  currencySymbol: string;
};

// Column header labels (25 columns — matching SpoolBuddy exactly)
const columnHeaders: Record<string, (t: TFn) => string> = {
  id: () => '#',
  added_time: () => 'Added',
  encode_time: () => 'Encoded',
  last_used_time: () => 'Last Used',
  rgba: (t) => t('inventory.color'),
  material: (t) => t('inventory.material'),
  subtype: (t) => t('inventory.subtype'),
  color_name: (t) => t('inventory.colorName'),
  brand: (t) => t('inventory.brand'),
  slicer_filament: (t) => t('inventory.slicerFilament'),
  location: () => 'Location',
  label_weight: (t) => t('inventory.labelWeight'),
  net: (t) => t('inventory.net'),
  gross: () => 'Gross',
  added_full: () => 'Full',
  used: (t) => t('inventory.weightUsed'),
  printed_total: () => 'Printed Total',
  printed_since_weight: () => 'Printed Since Weight',
  note: (t) => t('inventory.note'),
  pa_k: () => 'PA(K)',
  tag_id: () => 'Tag ID',
  data_origin: () => 'Data Origin',
  tag_type: () => 'Linked Tag Type',
  remaining: (t) => t('inventory.remaining'),
  cost_per_kg: (t) => t('inventory.costPerKg'),
};

// Column cell renderers (25 columns — matching SpoolBuddy exactly)
const columnCells: Record<string, (ctx: CellCtx) => ReactNode> = {
  id: ({ spool }) => (
    <span className="text-sm font-medium text-white">{spool.id}</span>
  ),
  added_time: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{formatDate(spool.created_at)}</span>
  ),
  encode_time: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{formatDate(spool.encode_time)}</span>
  ),
  last_used_time: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.last_used ? formatDate(spool.last_used) : 'Never'}</span>
  ),
  rgba: ({ spool }) => (
    <div className="flex items-center justify-center">
      <span
        className="w-5 h-5 rounded-full border border-white/20 flex-shrink-0"
        style={{ backgroundColor: spool.rgba ? `#${spool.rgba.substring(0, 6)}` : '#808080' }}
        title={spool.rgba ? `#${spool.rgba.substring(0, 6)}` : undefined}
      />
    </div>
  ),
  material: ({ spool }) => (
    <span className="text-sm text-white">{spool.material}</span>
  ),
  subtype: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.subtype || '-'}</span>
  ),
  color_name: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{resolveSpoolColorName(spool.color_name, spool.rgba) || '-'}</span>
  ),
  brand: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.brand || '-'}</span>
  ),
  slicer_filament: ({ spool }) => (
    <span className="text-sm text-bambu-gray" title={spool.slicer_filament || undefined}>
      {spool.slicer_filament_name || spool.slicer_filament || '-'}
    </span>
  ),
  location: ({ spool, assignmentMap }) => {
    const assignment = assignmentMap[spool.id];
    if (!assignment) return <span className="text-sm text-bambu-gray">-</span>;
    const printerLabel = assignment.printer_name || `Printer ${assignment.printer_id}`;
    // Bambu slot notation: AMS 0=A, 1=B, 2=C, 3=D; tray 0-based → 1-based
    const slotLetter = String.fromCharCode(65 + assignment.ams_id);
    const slotNumber = assignment.tray_id + 1;
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-purple-500/20 text-purple-400">
        {printerLabel} {slotLetter}{slotNumber}
      </span>
    );
  },
  label_weight: ({ spool }) => (
    <span className="text-sm text-white">{formatWeight(spool.label_weight)}</span>
  ),
  net: ({ remaining }) => (
    <span className="text-sm text-white">{formatWeight(remaining)}</span>
  ),
  gross: ({ spool, remaining }) => (
    <span className="text-sm text-bambu-gray">{formatWeight(remaining + spool.core_weight)}</span>
  ),
  added_full: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.added_full == null ? '-' : spool.added_full ? 'Yes' : 'No'}</span>
  ),
  used: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.weight_used > 0 ? formatWeight(spool.weight_used) : '-'}</span>
  ),
  printed_total: () => (
    <span className="text-sm text-bambu-gray/50">-</span>
  ),
  printed_since_weight: () => (
    <span className="text-sm text-bambu-gray/50">-</span>
  ),
  note: ({ spool }) => (
    <span className="text-sm text-bambu-gray max-w-[150px] truncate block" title={spool.note || undefined}>{spool.note || '-'}</span>
  ),
  pa_k: ({ spool }) => {
    const count = spool.k_profiles?.length ?? 0;
    if (count === 0) return <span className="text-sm text-bambu-gray">-</span>;
    return (
      <span className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium bg-bambu-green/20 text-bambu-green">
        K
      </span>
    );
  },
  tag_id: ({ spool }) => {
    const tag = spool.tag_uid || spool.tray_uuid;
    if (!tag) return <span className="text-sm text-bambu-gray/50">-</span>;
    return (
      <span className="text-sm text-bambu-gray font-mono" title={tag}>
        {tag.length > 12 ? `${tag.slice(0, 6)}...${tag.slice(-4)}` : tag}
      </span>
    );
  },
  data_origin: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.data_origin || '-'}</span>
  ),
  tag_type: ({ spool }) => (
    <span className="text-sm text-bambu-gray">{spool.tag_type || '-'}</span>
  ),
  remaining: ({ remaining, pct }) => (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 bg-bambu-dark-tertiary rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className="text-xs text-bambu-gray min-w-[40px] text-right">{Math.round(remaining)}g</span>
    </div>
  ),
  cost_per_kg: ({ spool, currencySymbol }) => (
    <span className="text-sm text-bambu-gray">
      {spool.cost_per_kg != null ? `${currencySymbol}${spool.cost_per_kg.toFixed(2)}` : '-'}
    </span>
  ),
};

// Sort value extractors — return a comparable value for each sortable column
const columnSortValues: Record<string, (spool: InventorySpool, assignmentMap: Record<number, SpoolAssignment>) => string | number> = {
  id: (s) => s.id,
  added_time: (s) => s.created_at || '',
  encode_time: (s) => s.encode_time || '',
  last_used_time: (s) => s.last_used || '',
  material: (s) => (s.material || '').toLowerCase(),
  subtype: (s) => (s.subtype || '').toLowerCase(),
  color_name: (s) => (s.color_name || '').toLowerCase(),
  brand: (s) => (s.brand || '').toLowerCase(),
  slicer_filament: (s) => (s.slicer_filament_name || s.slicer_filament || '').toLowerCase(),
  location: (s, am) => {
    const a = am[s.id];
    if (!a) return '';
    return `${a.printer_name || ''} ${String.fromCharCode(65 + a.ams_id)}${a.tray_id + 1}`;
  },
  label_weight: (s) => s.label_weight,
  net: (s) => Math.max(0, s.label_weight - s.weight_used),
  gross: (s) => Math.max(0, s.label_weight - s.weight_used) + s.core_weight,
  used: (s) => s.weight_used,
  remaining: (s) => s.label_weight > 0 ? Math.max(0, s.label_weight - s.weight_used) / s.label_weight : 0,
  note: (s) => (s.note || '').toLowerCase(),
  data_origin: (s) => (s.data_origin || '').toLowerCase(),
  tag_type: (s) => (s.tag_type || '').toLowerCase(),
  cost_per_kg: (s) => s.cost_per_kg ?? 0,
};

const SORT_STATE_KEY = 'bambuddy-inventory-sort';

function loadSortState(): SortState {
  try {
    const stored = localStorage.getItem(SORT_STATE_KEY);
    if (stored) return JSON.parse(stored);
  } catch { /* ignore */ }
  return null;
}

function saveSortState(state: SortState) {
  try {
    if (state) {
      localStorage.setItem(SORT_STATE_KEY, JSON.stringify(state));
    } else {
      localStorage.removeItem(SORT_STATE_KEY);
    }
  } catch { /* ignore */ }
}

export default function InventoryPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [formModal, setFormModal] = useState<{ spool?: InventorySpool | null } | null>(null);
  const [confirmAction, setConfirmAction] = useState<{ type: 'delete' | 'archive'; spoolId: number } | null>(null);

  // Filter state
  const [archiveFilter, setArchiveFilter] = useState<ArchiveFilter>('active');
  const [usageFilter, setUsageFilter] = useState<UsageFilter>('all');
  const [materialFilter, setMaterialFilter] = useState('');
  const [brandFilter, setBrandFilter] = useState('');
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('table');
  const [sortState, setSortState] = useState<SortState>(loadSortState);
  const [columnConfig, setColumnConfig] = useState<ColumnConfig[]>(loadColumnConfig);
  const [showColumnModal, setShowColumnModal] = useState(false);

  // Pagination state (pageSize persisted to localStorage)
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(() => {
    try {
      const stored = localStorage.getItem('bambuddy-inventory-pageSize');
      if (stored) {
        const n = Number(stored);
        if ([15, 30, 50, 100, -1].includes(n)) return n;
      }
    } catch { /* ignore */ }
    return 15;
  });

  const { data: spools, isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(true), // Always fetch all, filter client-side
    refetchInterval: 30000,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    refetchInterval: 30000,
  });

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: api.getSettings,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSpool(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      showToast(t('inventory.spoolDeleted'), 'success');
    },
  });

  const archiveMutation = useMutation({
    mutationFn: (id: number) => api.archiveSpool(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      showToast(t('inventory.spoolArchived'), 'success');
    },
  });

  const restoreMutation = useMutation({
    mutationFn: (id: number) => api.restoreSpool(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['inventory-spools'] });
      showToast(t('inventory.spoolRestored'), 'success');
    },
  });

  // Stats calculation (active spools only)
  const stats = useMemo(() => {
    if (!spools) return null;
    let totalWeight = 0;
    let totalConsumed = 0;
    let lowStock = 0;
    let activeCount = 0;
    const byMaterial: Record<string, { count: number; weight: number }> = {};
    for (const s of spools) {
      if (s.archived_at) continue;
      activeCount++;
      const remaining = Math.max(0, s.label_weight - s.weight_used);
      totalWeight += remaining;
      totalConsumed += s.weight_used;
      const pct = s.label_weight > 0 ? (remaining / s.label_weight) * 100 : 0;
      if (pct < 20) lowStock++;
      const mat = s.material || 'Unknown';
      if (!byMaterial[mat]) byMaterial[mat] = { count: 0, weight: 0 };
      byMaterial[mat].count++;
      byMaterial[mat].weight += remaining;
    }
    return { totalWeight, totalConsumed, lowStock, byMaterial, totalSpools: activeCount };
  }, [spools]);

  const inPrinterCount = assignments?.length ?? 0;

  const currencySymbol = getCurrencySymbol(settings?.currency || 'USD');

  // Map spool_id -> assignment for location column
  const assignmentMap = useMemo(() => {
    const map: Record<number, SpoolAssignment> = {};
    for (const a of assignments || []) {
      map[a.spool_id] = a;
    }
    return map;
  }, [assignments]);

  // Top materials by weight for stat card pills
  const topMaterials = useMemo(() => {
    if (!stats) return [];
    return Object.entries(stats.byMaterial)
      .sort((a, b) => b[1].weight - a[1].weight)
      .slice(0, 4);
  }, [stats]);

  // Filtering pipeline
  const filteredSpools = useMemo(() => {
    let filtered = spools || [];

    // Archive filter
    if (archiveFilter === 'active') {
      filtered = filtered.filter((s) => !s.archived_at);
    } else {
      filtered = filtered.filter((s) => !!s.archived_at);
    }

    // Usage filter
    if (usageFilter === 'used') {
      filtered = filtered.filter((s) => s.weight_used > 0);
    } else if (usageFilter === 'new') {
      filtered = filtered.filter((s) => s.weight_used === 0);
    }

    // Material dropdown
    if (materialFilter) {
      filtered = filtered.filter((s) => s.material === materialFilter);
    }

    // Brand dropdown
    if (brandFilter) {
      filtered = filtered.filter((s) => s.brand === brandFilter);
    }

    // Global search
    if (search) {
      const q = search.toLowerCase();
      filtered = filtered.filter((s) =>
        s.brand?.toLowerCase().includes(q) ||
        s.material.toLowerCase().includes(q) ||
        s.color_name?.toLowerCase().includes(q) ||
        s.subtype?.toLowerCase().includes(q) ||
        s.note?.toLowerCase().includes(q) ||
        s.slicer_filament_name?.toLowerCase().includes(q)
      );
    }

    return filtered;
  }, [spools, archiveFilter, usageFilter, materialFilter, brandFilter, search]);

  // Reset page on filter changes
  const resetPage = () => setPageIndex(0);

  // Unique values for filter dropdowns
  const uniqueMaterials = [...new Set(spools?.map((s) => s.material) || [])].sort();
  const uniqueBrands = [...new Set(spools?.map((s) => s.brand).filter(Boolean) || [])].sort() as string[];

  // Check if any filters are non-default
  const hasActiveFilters = archiveFilter !== 'active' || usageFilter !== 'all' || !!materialFilter || !!brandFilter || !!search;

  const handleColumnConfigSave = (config: ColumnConfig[]) => {
    setColumnConfig(config);
    saveColumnConfig(config);
  };

  // Visible column IDs in order
  const visibleColumns = useMemo(
    () => columnConfig.filter((c) => c.visible).map((c) => c.id),
    [columnConfig]
  );

  const handleSort = (colId: string) => {
    if (!columnSortValues[colId]) return; // Not sortable
    setSortState((prev) => {
      let next: SortState;
      if (prev?.column === colId) {
        // Toggle direction, or clear on third click
        next = prev.direction === 'asc' ? { column: colId, direction: 'desc' } : null;
      } else {
        next = { column: colId, direction: 'asc' };
      }
      saveSortState(next);
      return next;
    });
    resetPage();
  };

  // Sort filtered spools
  const sortedSpools = useMemo(() => {
    if (!sortState) return filteredSpools;
    const extractor = columnSortValues[sortState.column];
    if (!extractor) return filteredSpools;
    const sorted = [...filteredSpools].sort((a, b) => {
      const va = extractor(a, assignmentMap);
      const vb = extractor(b, assignmentMap);
      if (va < vb) return sortState.direction === 'asc' ? -1 : 1;
      if (va > vb) return sortState.direction === 'asc' ? 1 : -1;
      return 0;
    });
    return sorted;
  }, [filteredSpools, sortState, assignmentMap]);

  // Pagination (after sorting) — pageSize -1 means "All"
  const showAll = pageSize === -1;
  const effectivePageSize = showAll ? sortedSpools.length || 1 : pageSize;
  const totalPages = Math.max(1, Math.ceil(sortedSpools.length / effectivePageSize));
  const safePageIndex = showAll ? 0 : Math.min(pageIndex, totalPages - 1);
  const pagedSpools = showAll ? sortedSpools : sortedSpools.slice(safePageIndex * effectivePageSize, (safePageIndex + 1) * effectivePageSize);

  const handlePageSizeChange = (size: number) => {
    setPageSize(size);
    setPageIndex(0);
    try { localStorage.setItem('bambuddy-inventory-pageSize', String(size)); } catch { /* ignore */ }
  };

  const clearAllFilters = () => {
    setArchiveFilter('active');
    setUsageFilter('all');
    setMaterialFilter('');
    setBrandFilter('');
    setSearch('');
    resetPage();
  };

  return (
    <div className="p-4 md:p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <Package className="w-6 h-6 text-bambu-green" />
            <h1 className="text-2xl font-bold text-white">{t('inventory.title')}</h1>
          </div>
          <p className="text-sm text-bambu-gray mt-1 ml-9">{t('inventory.noSpools').split('.')[0] ? '' : ''}</p>
        </div>
        <Button onClick={() => setFormModal({ spool: null })}>
          <Plus className="w-4 h-4" />
          {t('inventory.addSpool')}
        </Button>
      </div>

      {/* Stats Bar */}
      {stats && !isLoading && (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
          {/* Total Inventory */}
          <div className="bg-bambu-dark-secondary rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1">
              <Package className="w-4 h-4 text-bambu-green" />
              <span className="text-xs text-bambu-gray font-medium uppercase tracking-wide">{t('inventory.totalInventory')}</span>
            </div>
            <div className="text-xl font-bold text-white">{formatWeight(stats.totalWeight, true)}</div>
            <div className="text-xs text-bambu-gray mt-1">{stats.totalSpools} {stats.totalSpools !== 1 ? t('inventory.spools') : t('inventory.spool')}</div>
          </div>

          {/* Total Consumed */}
          <div className="bg-bambu-dark-secondary rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1">
              <TrendingDown className="w-4 h-4 text-blue-400" />
              <span className="text-xs text-bambu-gray font-medium uppercase tracking-wide">{t('inventory.totalConsumed')}</span>
            </div>
            <div className="text-xl font-bold text-white">{formatWeight(stats.totalConsumed, true)}</div>
            <div className="text-xs text-bambu-gray mt-1">{t('inventory.sinceTracking')}</div>
          </div>

          {/* By Material */}
          <div className="bg-bambu-dark-secondary rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1">
              <Layers className="w-4 h-4 text-green-400" />
              <span className="text-xs text-bambu-gray font-medium uppercase tracking-wide">{t('inventory.byMaterial')}</span>
            </div>
            <div className="flex flex-wrap gap-1.5 mt-1">
              {topMaterials.map(([mat, data]) => (
                <span
                  key={mat}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${MATERIAL_COLORS[mat] || 'bg-bambu-dark-tertiary text-bambu-gray'}`}
                >
                  {mat} <span className="opacity-70">{formatWeight(data.weight, true)}</span>
                </span>
              ))}
            </div>
          </div>

          {/* In Printer */}
          <div className="bg-bambu-dark-secondary rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1">
              <Printer className="w-4 h-4 text-purple-400" />
              <span className="text-xs text-bambu-gray font-medium uppercase tracking-wide">{t('inventory.inPrinter')}</span>
            </div>
            <div className="text-xl font-bold text-white">{inPrinterCount}</div>
            <div className="text-xs text-bambu-gray mt-1">{t('inventory.loadedInAms')}</div>
          </div>

          {/* Low Stock */}
          <div className="bg-bambu-dark-secondary rounded-lg p-4">
            <div className="flex items-center gap-2 mb-1">
              <AlertTriangle className="w-4 h-4 text-yellow-400" />
              <span className="text-xs text-bambu-gray font-medium uppercase tracking-wide">{t('inventory.lowStock')}</span>
            </div>
            <div className={`text-xl font-bold ${stats.lowStock > 0 ? 'text-yellow-400' : 'text-white'}`}>{stats.lowStock}</div>
            <div className="text-xs text-bambu-gray mt-1">{t('inventory.lowStockThreshold')}</div>
          </div>
        </div>
      )}

      {/* Toolbar: Search + View toggle */}
      <div className="flex flex-col sm:flex-row gap-3 items-start sm:items-center justify-between">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray/50" />
          <input
            type="text"
            value={search}
            onChange={(e) => { setSearch(e.target.value); resetPage(); }}
            placeholder={t('inventory.search')}
            className="w-full pl-10 pr-8 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
          />
          {search && (
            <button
              onClick={() => { setSearch(''); resetPage(); }}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {/* Columns button (table view only) */}
          {viewMode === 'table' && (
            <button
              onClick={() => setShowColumnModal(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-bambu-gray border border-bambu-dark-tertiary rounded-lg hover:bg-bambu-dark-tertiary transition-colors"
              title={t('inventory.configureColumns')}
            >
              <Columns className="w-4 h-4" />
              <span className="hidden sm:inline">{t('inventory.columns')}</span>
            </button>
          )}
          {/* Table / Cards toggle */}
          <div className="flex bg-bambu-dark-primary border border-bambu-dark-tertiary rounded-lg overflow-hidden">
            <button
              onClick={() => setViewMode('table')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors ${
                viewMode === 'table'
                  ? 'bg-bambu-green text-white'
                  : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
              }`}
            >
              <TableProperties className="w-4 h-4" />
              <span className="hidden sm:inline">{t('inventory.table')}</span>
            </button>
            <button
              onClick={() => setViewMode('cards')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition-colors ${
                viewMode === 'cards'
                  ? 'bg-bambu-green text-white'
                  : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
              }`}
            >
              <LayoutGrid className="w-4 h-4" />
              <span className="hidden sm:inline">{t('inventory.cards')}</span>
            </button>
          </div>
        </div>
      </div>

      {/* Filter chips row */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Active / Archived chips */}
        <div className="flex items-center rounded-lg border border-bambu-dark-tertiary overflow-hidden">
          <button
            onClick={() => { setArchiveFilter('active'); resetPage(); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              archiveFilter === 'active'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            <Package className="w-3.5 h-3.5" />
            {t('inventory.active')}
          </button>
          <button
            onClick={() => { setArchiveFilter('archived'); resetPage(); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              archiveFilter === 'archived'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            <Archive className="w-3.5 h-3.5" />
            {t('inventory.archived')}
          </button>
        </div>

        <div className="w-px h-5 bg-bambu-dark-tertiary" />

        {/* All / Used / New chips */}
        <div className="flex items-center rounded-lg border border-bambu-dark-tertiary overflow-hidden">
          <button
            onClick={() => { setUsageFilter('all'); resetPage(); }}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              usageFilter === 'all'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            {t('inventory.all')}
          </button>
          <button
            onClick={() => { setUsageFilter('used'); resetPage(); }}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
              usageFilter === 'used'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            <Clock className="w-3.5 h-3.5" />
            {t('inventory.used')}
          </button>
          <button
            onClick={() => { setUsageFilter('new'); resetPage(); }}
            className={`px-3 py-1.5 text-xs font-medium transition-colors ${
              usageFilter === 'new'
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'text-bambu-gray hover:bg-bambu-dark-tertiary'
            }`}
          >
            {t('inventory.new')}
          </button>
        </div>

        <div className="w-px h-5 bg-bambu-dark-tertiary" />

        {/* Material dropdown chip */}
        <select
          value={materialFilter}
          onChange={(e) => { setMaterialFilter(e.target.value); resetPage(); }}
          className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors cursor-pointer focus:outline-none ${
            materialFilter
              ? 'bg-bambu-green/20 text-bambu-green border-bambu-green/30'
              : 'bg-transparent text-bambu-gray border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary'
          }`}
        >
          <option value="">{t('inventory.material')}</option>
          {uniqueMaterials.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>

        {/* Brand dropdown chip */}
        <select
          value={brandFilter}
          onChange={(e) => { setBrandFilter(e.target.value); resetPage(); }}
          className={`px-3 py-1.5 rounded-lg border text-xs font-medium transition-colors cursor-pointer focus:outline-none ${
            brandFilter
              ? 'bg-bambu-green/20 text-bambu-green border-bambu-green/30'
              : 'bg-transparent text-bambu-gray border-bambu-dark-tertiary hover:bg-bambu-dark-tertiary'
          }`}
        >
          <option value="">{t('inventory.brand')}</option>
          {uniqueBrands.map((b) => (
            <option key={b} value={b}>{b}</option>
          ))}
        </select>

        {/* Clear filters */}
        {hasActiveFilters && (
          <>
            <div className="w-px h-5 bg-bambu-dark-tertiary" />
            <button
              onClick={clearAllFilters}
              className="flex items-center gap-1 text-xs text-bambu-gray hover:text-bambu-green transition-colors"
            >
              <X className="w-3.5 h-3.5" />
              {t('inventory.clearFilters')}
            </button>
          </>
        )}

        {/* Results count */}
        <span className="ml-auto text-xs text-bambu-gray">
          {sortedSpools.length} {sortedSpools.length !== 1 ? t('inventory.spools') : t('inventory.spool')}
        </span>
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
        </div>
      ) : viewMode === 'cards' ? (
        /* Cards view */
        pagedSpools.length > 0 ? (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {pagedSpools.map((spool) => {
                const remaining = Math.max(0, spool.label_weight - spool.weight_used);
                const pct = spool.label_weight > 0 ? (remaining / spool.label_weight) * 100 : 0;
                const colorStyle = spool.rgba ? `#${spool.rgba.substring(0, 6)}` : '#808080';
                return (
                  <div
                    key={spool.id}
                    className={`bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary hover:border-bambu-green transition-colors cursor-pointer ${spool.archived_at ? 'opacity-50' : ''}`}
                    onClick={() => setFormModal({ spool })}
                  >
                    {/* Color header */}
                    <div className="h-14 flex items-center justify-center" style={{ backgroundColor: colorStyle }}>
                      <span className="bg-white/90 text-gray-800 px-3 py-0.5 rounded-full text-sm font-medium">
                        {resolveSpoolColorName(spool.color_name, spool.rgba) || '-'}
                      </span>
                    </div>
                    {/* Content */}
                    <div className="p-4 space-y-3">
                      <div className="flex items-start justify-between gap-2">
                        <div>
                          <h3 className="font-semibold text-white">{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}</h3>
                          <p className="text-sm text-bambu-gray">{spool.brand || '-'}</p>
                        </div>
                        <span className="text-xs font-mono text-bambu-gray bg-bambu-dark-tertiary px-2 py-1 rounded">#{spool.id}</span>
                      </div>
                      {/* Progress */}
                      <div>
                        <div className="flex justify-between text-xs text-bambu-gray mb-1">
                          <span>{t('inventory.remaining')}</span>
                          <span>{Math.round(pct)}%</span>
                        </div>
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-2 bg-bambu-dark-tertiary rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${pct > 50 ? 'bg-bambu-green' : pct > 20 ? 'bg-yellow-500' : 'bg-red-500'}`}
                              style={{ width: `${Math.min(pct, 100)}%` }}
                            />
                          </div>
                          <span className="text-xs text-bambu-gray min-w-[40px] text-right">{Math.round(remaining)}g</span>
                        </div>
                      </div>
                      {/* Weight info */}
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div>
                          <span className="text-bambu-gray/60">{t('inventory.labelWeight')}: </span>
                          <span className="text-bambu-gray">{formatWeight(spool.label_weight)}</span>
                        </div>
                        <div>
                          <span className="text-bambu-gray/60">{t('inventory.weightUsed')}: </span>
                          <span className="text-bambu-gray">{spool.weight_used > 0 ? formatWeight(spool.weight_used) : '-'}</span>
                        </div>
                      </div>
                      {/* Note */}
                      {spool.note && (
                        <div className="text-xs text-bambu-gray/60 pt-2 border-t border-bambu-dark-tertiary truncate" title={spool.note}>
                          {spool.note}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {/* Pagination for cards */}
            <PaginationBar
              pageIndex={safePageIndex}
              pageSize={pageSize}
              totalRows={sortedSpools.length}
              totalPages={totalPages}
              onPageChange={setPageIndex}
              onPageSizeChange={handlePageSizeChange}
              t={t}
            />
          </>
        ) : (
          <EmptyFilterState
            hasFilters={hasActiveFilters}
            onAddSpool={() => setFormModal({ spool: null })}
            t={t}
          />
        )
      ) : (
        /* Table view */
        pagedSpools.length > 0 ? (
          <div className="bg-bambu-dark-secondary rounded-lg overflow-hidden border border-bambu-dark-tertiary">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-bambu-dark-tertiary bg-bambu-dark-tertiary/30">
                    {visibleColumns.map((colId) => {
                      const sortable = !!columnSortValues[colId];
                      const isActive = sortState?.column === colId;
                      return (
                        <th
                          key={colId}
                          className={`text-left py-3 px-4 text-xs font-medium uppercase tracking-wide select-none ${colId === 'remaining' ? 'min-w-[150px]' : ''} ${
                            sortable ? 'cursor-pointer hover:text-bambu-green transition-colors' : ''
                          } ${isActive ? 'text-bambu-green' : 'text-bambu-gray'}`}
                          onClick={sortable ? () => handleSort(colId) : undefined}
                        >
                          <span className="inline-flex items-center gap-1">
                            {columnHeaders[colId]?.(t) ?? colId}
                            {sortable && (
                              isActive
                                ? sortState.direction === 'asc'
                                  ? <ArrowUp className="w-3 h-3" />
                                  : <ArrowDown className="w-3 h-3" />
                                : <ArrowUpDown className="w-3 h-3 opacity-30" />
                            )}
                          </span>
                        </th>
                      );
                    })}
                    <th className="text-right py-3 px-4 text-xs font-medium text-bambu-gray uppercase tracking-wide">{t('common.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {pagedSpools.map((spool) => {
                    const remaining = Math.max(0, spool.label_weight - spool.weight_used);
                    const pct = spool.label_weight > 0 ? (remaining / spool.label_weight) * 100 : 0;
                    return (
                      <tr
                        key={spool.id}
                        className={`border-b border-bambu-dark-tertiary/50 hover:bg-bambu-dark-tertiary/30 transition-colors cursor-pointer ${
                          spool.archived_at ? 'opacity-50' : ''
                        }`}
                        onClick={() => setFormModal({ spool })}
                      >
                        {visibleColumns.map((colId) => (
                          <td key={colId} className="py-3 px-4">
                            {columnCells[colId]?.({ spool, remaining, pct, assignmentMap, currencySymbol })}
                          </td>
                        ))}
                        <td className="py-3 px-4">
                          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                            <button
                              onClick={() => setFormModal({ spool })}
                              className="p-1.5 text-bambu-gray hover:text-white rounded transition-colors"
                              title={t('inventory.editSpool')}
                            >
                              <Edit2 className="w-4 h-4" />
                            </button>
                            {spool.archived_at ? (
                              <button
                                onClick={() => restoreMutation.mutate(spool.id)}
                                className="p-1.5 text-bambu-gray hover:text-bambu-green rounded transition-colors"
                                title={t('inventory.restore')}
                              >
                                <RotateCcw className="w-4 h-4" />
                              </button>
                            ) : (
                              <button
                                onClick={() => setConfirmAction({ type: 'archive', spoolId: spool.id })}
                                className="p-1.5 text-bambu-gray hover:text-yellow-400 rounded transition-colors"
                                title={t('inventory.archive')}
                              >
                                <Archive className="w-4 h-4" />
                              </button>
                            )}
                            <button
                              onClick={() => setConfirmAction({ type: 'delete', spoolId: spool.id })}
                              className="p-1.5 text-bambu-gray hover:text-red-400 rounded transition-colors"
                              title={t('common.delete')}
                            >
                              <Trash2 className="w-4 h-4" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Pagination inside card footer */}
            <div className="flex items-center justify-between px-4 py-3 bg-bambu-dark-tertiary/50 border-t border-bambu-dark-tertiary text-sm">
              <span className="text-bambu-gray">
                {showAll
                  ? `${sortedSpools.length} ${sortedSpools.length !== 1 ? t('inventory.spools') : t('inventory.spool')}`
                  : <>{t('inventory.showing')} {safePageIndex * effectivePageSize + 1} {t('inventory.to')}{' '}
                    {Math.min((safePageIndex + 1) * effectivePageSize, sortedSpools.length)}{' '}
                    {t('inventory.of')} {sortedSpools.length} {t('inventory.spools')}</>
                }
              </span>

              <div className="flex items-center gap-2">
                <span className="text-bambu-gray">{t('inventory.show')}</span>
                <select
                  value={pageSize}
                  onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                  className="px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
                >
                  {[15, 30, 50, 100].map((n) => (
                    <option key={n} value={n}>{n}</option>
                  ))}
                  <option value={-1}>{t('inventory.all')}</option>
                </select>

                {!showAll && (
                  <>
                    <button
                      onClick={() => setPageIndex(0)}
                      disabled={safePageIndex === 0}
                      className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      title="First page"
                    >
                      <ChevronsLeft className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => setPageIndex((p) => Math.max(0, p - 1))}
                      disabled={safePageIndex === 0}
                      className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      <ChevronLeft className="w-4 h-4" />
                    </button>
                    <span className="text-bambu-gray px-2 whitespace-nowrap">
                      {t('inventory.page')} {safePageIndex + 1} {t('inventory.of')} {totalPages}
                    </span>
                    <button
                      onClick={() => setPageIndex((p) => Math.min(totalPages - 1, p + 1))}
                      disabled={safePageIndex >= totalPages - 1}
                      className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                    >
                      <ChevronRight className="w-4 h-4" />
                    </button>
                    <button
                      onClick={() => setPageIndex(totalPages - 1)}
                      disabled={safePageIndex >= totalPages - 1}
                      className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
                      title="Last page"
                    >
                      <ChevronsRight className="w-4 h-4" />
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        ) : (
          <EmptyFilterState
            hasFilters={hasActiveFilters}
            onAddSpool={() => setFormModal({ spool: null })}
            t={t}
          />
        )
      )}

      {/* Spool Form Modal */}
      {formModal !== null && (
        <SpoolFormModal
          isOpen={true}
          onClose={() => setFormModal(null)}
          spool={formModal.spool}
        />
      )}

      {/* Confirm Modal (delete / archive) */}
      {confirmAction && (
        <ConfirmModal
          title={confirmAction.type === 'delete' ? t('common.delete') : t('inventory.archive')}
          message={confirmAction.type === 'delete' ? t('inventory.deleteConfirm') : t('inventory.archiveConfirm')}
          confirmText={confirmAction.type === 'delete' ? t('common.delete') : t('inventory.archive')}
          variant={confirmAction.type === 'delete' ? 'danger' : 'warning'}
          onConfirm={() => {
            if (confirmAction.type === 'delete') {
              deleteMutation.mutate(confirmAction.spoolId);
            } else {
              archiveMutation.mutate(confirmAction.spoolId);
            }
            setConfirmAction(null);
          }}
          onCancel={() => setConfirmAction(null)}
        />
      )}

      {/* Column Config Modal */}
      <ColumnConfigModal
        isOpen={showColumnModal}
        onClose={() => setShowColumnModal(false)}
        columns={columnConfig}
        defaultColumns={DEFAULT_COLUMNS}
        onSave={handleColumnConfigSave}
      />
    </div>
  );
}

/* Pagination bar (reused for cards view) */
function PaginationBar({
  pageIndex, pageSize, totalRows, totalPages, onPageChange, onPageSizeChange, t,
}: {
  pageIndex: number;
  pageSize: number;
  totalRows: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
  t: (key: string) => string;
}) {
  const isShowAll = pageSize === -1;
  if (totalPages <= 1 && !isShowAll) return null;
  const effectiveSize = isShowAll ? totalRows || 1 : pageSize;
  return (
    <div className="flex items-center justify-between pt-2 text-sm">
      <span className="text-bambu-gray">
        {isShowAll
          ? `${totalRows} ${totalRows !== 1 ? t('inventory.spools') : t('inventory.spool')}`
          : <>{t('inventory.showing')} {pageIndex * effectiveSize + 1} {t('inventory.to')}{' '}
              {Math.min((pageIndex + 1) * effectiveSize, totalRows)}{' '}
              {t('inventory.of')} {totalRows} {t('inventory.spools')}</>
        }
      </span>
      <div className="flex items-center gap-2">
        <span className="text-bambu-gray">{t('inventory.show')}</span>
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="px-2 py-1 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white text-sm focus:outline-none focus:border-bambu-green"
        >
          {[15, 30, 50, 100].map((n) => (
            <option key={n} value={n}>{n}</option>
          ))}
          <option value={-1}>{t('inventory.all')}</option>
        </select>
        {!isShowAll && (
          <>
            <button
              onClick={() => onPageChange(0)}
              disabled={pageIndex === 0}
              className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronsLeft className="w-4 h-4" />
            </button>
            <button
              onClick={() => onPageChange(Math.max(0, pageIndex - 1))}
              disabled={pageIndex === 0}
              className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="text-bambu-gray px-2 whitespace-nowrap">
              {t('inventory.page')} {pageIndex + 1} {t('inventory.of')} {totalPages}
            </span>
            <button
              onClick={() => onPageChange(Math.min(totalPages - 1, pageIndex + 1))}
              disabled={pageIndex >= totalPages - 1}
              className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
            <button
              onClick={() => onPageChange(totalPages - 1)}
              disabled={pageIndex >= totalPages - 1}
              className="p-1.5 rounded text-bambu-gray hover:text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronsRight className="w-4 h-4" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/* Empty state matching SpoolBuddy's design */
function EmptyFilterState({
  hasFilters,
  onAddSpool,
  t,
}: {
  hasFilters: boolean;
  onAddSpool: () => void;
  t: (key: string) => string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <div className="relative mb-6">
        <div className="absolute inset-0 -m-4 bg-bambu-green/5 rounded-full blur-2xl" />
        <div className="relative flex items-center justify-center w-24 h-24 rounded-2xl bg-gradient-to-br from-bambu-dark-secondary to-bambu-dark-tertiary border border-bambu-dark-tertiary shadow-lg">
          <div className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-bambu-green/30" />
          <div className="absolute -bottom-2 -left-2 w-2 h-2 rounded-full bg-bambu-green/20" />
          {hasFilters ? (
            <Search className="w-10 h-10 text-bambu-gray/40" strokeWidth={1.5} />
          ) : (
            <div className="relative">
              <div className="w-14 h-14 rounded-full border-4 border-bambu-gray/20 flex items-center justify-center">
                <div className="w-6 h-6 rounded-full bg-bambu-gray/10 border-2 border-bambu-gray/20" />
              </div>
              <div className="absolute -bottom-1 -right-1 w-6 h-6 rounded-full bg-bambu-green flex items-center justify-center shadow-md">
                <span className="text-white text-lg font-bold leading-none">+</span>
              </div>
            </div>
          )}
        </div>
      </div>
      <h3 className="text-lg font-semibold text-white mb-2 text-center">
        {hasFilters ? t('inventory.noSpoolsMatch') : t('inventory.noSpools').split('.')[0]}
      </h3>
      <p className="text-sm text-bambu-gray text-center max-w-sm mb-6">
        {hasFilters
          ? t('inventory.noSpoolsMatchDesc')
          : t('inventory.noSpools')
        }
      </p>
      {!hasFilters && (
        <Button onClick={onAddSpool}>
          <Package className="w-4 h-4" />
          {t('inventory.addSpool')}
        </Button>
      )}
    </div>
  );
}
