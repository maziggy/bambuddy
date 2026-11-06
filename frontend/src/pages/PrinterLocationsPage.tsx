import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';
import { ArrowLeft, Box, CheckSquare, ChevronDown, Loader2, Move, Pencil, Plus, Search, Square, Trash2, UserMinus, X } from 'lucide-react';
import { api } from '../api/client';
import type { Printer, PrinterLocation } from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { IconPicker, AVAILABLE_ICONS } from '../components/IconPicker';
import { useAuth } from '../contexts/AuthContext';
import { useToast } from '../contexts/ToastContext';

// Matches printers.location / printer_locations.name (VARCHAR(100)).
const LOCATION_NAME_MAX_LENGTH = 100;

const LOCATION_COLORS = [
  '#ef4444', // red
  '#f97316', // orange
  '#eab308', // yellow
  '#22c55e', // green
  '#14b8a6', // teal
  '#3b82f6', // blue
  '#8b5cf6', // violet
  '#ec4899', // pink
  '#6b7280', // gray
];

type SortMode = 'name-asc' | 'name-desc' | 'count-asc' | 'count-desc';
const SORT_MODES: SortMode[] = ['name-asc', 'name-desc', 'count-asc', 'count-desc'];

// Per-viewer conveniences only; the locations themselves live on the server.
const HIDE_EMPTY_KEY = 'printerLocations.hideEmpty';
const SORT_KEY = 'printerLocations.sort';

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return allowed.includes(value as T) ? (value as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode or storage disabled: the setting just is not remembered.
  }
}

const locationOf = (printer: Printer) => printer.location?.trim() || '';

function LocationIcon({ location }: { location: Pick<PrinterLocation, 'icon' | 'color'> }) {
  const Icon = AVAILABLE_ICONS.find((i) => i.name === location.icon)?.icon ?? Box;
  return (
    <div className="p-2 rounded-lg bg-bambu-dark relative overflow-hidden flex-shrink-0">
      <Icon className="w-[25px] h-[25px] text-bambu-gray" />
      {location.color && (
        <div className="absolute top-0 left-0 w-1 h-full" style={{ backgroundColor: location.color }} />
      )}
    </div>
  );
}

interface PrinterRowProps {
  printer: Printer;
  status?: { connected?: boolean; state?: string | null };
  selected: boolean;
  canEdit: boolean;
  busy: boolean;
  onToggle: () => void;
  onMove: () => void;
  onRemove?: () => void;
}

function PrinterRow({ printer, status, selected, canEdit, busy, onToggle, onMove, onRemove }: PrinterRowProps) {
  const { t } = useTranslation();
  const state = status?.state;
  const connected = status?.connected;
  const label = !connected
    ? t('printers.status.offline')
    : state === 'RUNNING'
      ? t('printers.status.printing')
      : state === 'PAUSE'
        ? t('printers.status.paused')
        : state === 'FINISH'
          ? t('printers.status.finished')
          : t('printers.status.idle');
  const badge = !connected
    ? 'bg-gray-500/20 text-gray-400'
    : state === 'RUNNING'
      ? 'bg-orange-500/20 text-orange-400'
      : state === 'PAUSE'
        ? 'bg-yellow-500/20 text-yellow-400'
        : state === 'FINISH'
          ? 'bg-bambu-green/20 text-bambu-green'
          : 'bg-bambu-dark text-bambu-gray';

  return (
    <div
      className={`flex items-center justify-between gap-2 py-2 px-3 rounded-lg transition-colors ${
        selected ? 'bg-bambu-green/10 border border-bambu-green/30' : 'bg-bambu-dark-secondary hover:bg-bambu-dark'
      }`}
    >
      <div className="flex items-center gap-3 min-w-0">
        {canEdit && (
          <button
            type="button"
            onClick={onToggle}
            className="text-bambu-gray hover:text-bambu-green transition-colors"
            title={t('printers.locations.selectPrinter')}
            aria-label={t('printers.locations.selectPrinter')}
            aria-pressed={selected}
          >
            {selected ? <CheckSquare className="w-4 h-4 text-bambu-green" /> : <Square className="w-4 h-4" />}
          </button>
        )}
        <div
          className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${
            connected ? (state === 'RUNNING' || state === 'PAUSE' ? 'bg-orange-500' : 'bg-bambu-green') : 'bg-gray-500'
          }`}
        />
        <div className="min-w-0">
          <p className="text-white text-sm font-medium truncate">{printer.name}</p>
          <p className="text-xs text-bambu-gray">{printer.model || t('printers.status.unknown')}</p>
        </div>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <span className={`text-xs px-2 py-1 rounded-full ${badge}`}>{label}</span>
        {canEdit && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onMove}
            disabled={busy}
            className="text-bambu-gray hover:text-bambu-green hover:bg-bambu-green/10"
            title={t('printers.locations.move')}
            aria-label={t('printers.locations.move')}
          >
            <Move className="w-3.5 h-3.5" />
          </Button>
        )}
        {canEdit && onRemove && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={busy}
            className="text-bambu-gray hover:text-red-400 hover:bg-red-500/10"
            title={t('printers.locations.removeFromLocation')}
            aria-label={t('printers.locations.removeFromLocation')}
          >
            <UserMinus className="w-3.5 h-3.5" />
          </Button>
        )}
      </div>
    </div>
  );
}

interface LocationDialogProps {
  initial?: PrinterLocation;
  saving: boolean;
  onSave: (value: { name: string; icon: string; color: string }) => void;
  onCancel: () => void;
}

function LocationDialog({ initial, saving, onSave, onCancel }: LocationDialogProps) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial?.name ?? '');
  const [icon, setIcon] = useState(initial?.icon ?? '');
  const [color, setColor] = useState(initial?.color ?? '');
  const canSave = name.trim().length > 0 && !saving;
  const save = () => canSave && onSave({ name: name.trim(), icon, color });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardContent className="space-y-4">
          <h2 className="text-lg font-semibold text-white">
            {initial ? t('printers.locations.editTitle') : t('printers.locations.createTitle')}
          </h2>
          <input
            type="text"
            value={name}
            maxLength={LOCATION_NAME_MAX_LENGTH}
            onChange={(e) => setName(e.target.value)}
            placeholder={t('printers.locations.namePlaceholder')}
            aria-label={t('printers.locations.namePlaceholder')}
            className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
            autoFocus
            onKeyDown={(e) => {
              if (e.key === 'Enter') save();
              if (e.key === 'Escape') onCancel();
            }}
          />
          <div>
            <p className="text-sm text-bambu-gray mb-2">{t('printers.locations.icon')}</p>
            <IconPicker value={icon} onChange={setIcon} />
          </div>
          <div>
            <p className="text-sm text-bambu-gray mb-2">{t('printers.locations.color')}</p>
            <div className="flex gap-2 flex-wrap">
              {LOCATION_COLORS.map((c) => (
                <button
                  key={c}
                  type="button"
                  onClick={() => setColor(color === c ? '' : c)}
                  className={`w-8 h-8 rounded-lg transition-all ${
                    color === c ? 'ring-2 ring-white ring-offset-2 ring-offset-bambu-dark-secondary scale-110' : 'hover:scale-105'
                  }`}
                  style={{ backgroundColor: c }}
                  title={c}
                  aria-label={c}
                  aria-pressed={color === c}
                />
              ))}
              {color && (
                <button
                  type="button"
                  onClick={() => setColor('')}
                  className="w-8 h-8 rounded-lg border-2 border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-gray hover:text-white hover:border-bambu-gray transition-all flex items-center justify-center"
                  title={t('printers.locations.noColor')}
                  aria-label={t('printers.locations.noColor')}
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onCancel} disabled={saving}>
              {t('common.cancel')}
            </Button>
            <Button onClick={save} disabled={!canSave}>
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : initial ? t('common.save') : t('printers.locations.create')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

interface MoveDialogProps {
  count: number;
  printerName?: string;
  locations: PrinterLocation[];
  saving: boolean;
  onMove: (location: string | null) => void;
  onCancel: () => void;
}

const NO_LOCATION = '__none__';

function MoveDialog({ count, printerName, locations, saving, onMove, onCancel }: MoveDialogProps) {
  const { t } = useTranslation();
  const [target, setTarget] = useState('');
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <Card className="w-full max-w-md">
        <CardContent className="space-y-4">
          <h2 className="text-lg font-semibold text-white">
            {count === 1 ? t('printers.locations.moveTitle') : t('printers.locations.moveTitleMany', { count })}
          </h2>
          {printerName && <p className="text-sm text-bambu-gray">{printerName}</p>}
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            aria-label={t('printers.locations.targetPlaceholder')}
            className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
            disabled={saving}
            autoFocus
          >
            <option value="" disabled>
              {t('printers.locations.targetPlaceholder')}
            </option>
            {locations.map((loc) => (
              <option key={loc.name} value={loc.name}>
                {loc.name}
              </option>
            ))}
            <option value={NO_LOCATION}>{t('printers.locations.noLocation')}</option>
          </select>
          <div className="flex gap-2 justify-end">
            <Button variant="secondary" onClick={onCancel} disabled={saving}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => onMove(target === NO_LOCATION ? null : target)} disabled={saving || !target}>
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : t('printers.locations.moveButton')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

export function PrinterLocationsPage() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { showToast } = useToast();
  const { hasPermission } = useAuth();
  const canEdit = hasPermission('printers:update');

  const [search, setSearch] = useState('');
  const [hideEmpty, setHideEmpty] = useState(() => readStored(HIDE_EMPTY_KEY, ['true', 'false'], 'false') === 'true');
  const [sortMode, setSortMode] = useState<SortMode>(() => readStored(SORT_KEY, SORT_MODES, 'name-asc'));
  const [showSortMenu, setShowSortMenu] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selectingLocations, setSelectingLocations] = useState(false);
  const [selectedLocations, setSelectedLocations] = useState<Set<string>>(new Set());
  const [selectedPrinters, setSelectedPrinters] = useState<Set<number>>(new Set());
  const [dialog, setDialog] = useState<{ mode: 'create' } | { mode: 'edit'; location: PrinterLocation } | null>(null);
  const [moving, setMoving] = useState<{ ids: number[]; printerName?: string } | null>(null);
  const [deleting, setDeleting] = useState<string[] | null>(null);

  useEffect(() => writeStored(HIDE_EMPTY_KEY, String(hideEmpty)), [hideEmpty]);
  useEffect(() => writeStored(SORT_KEY, sortMode), [sortMode]);
  useEffect(() => {
    if (selectingLocations) setExpanded(null);
  }, [selectingLocations]);

  const { data: printers, isLoading: printersLoading } = useQuery({ queryKey: ['printers'], queryFn: api.getPrinters });
  const { data: locations, isLoading: locationsLoading } = useQuery({
    queryKey: ['printer-locations'],
    queryFn: api.getPrinterLocations,
  });

  // Same query the printer cards use, so the cache and the WebSocket updates
  // are shared rather than polled twice.
  const statusQueries = useQueries({
    queries: (printers ?? []).map((p) => ({
      queryKey: ['printerStatus', p.id],
      queryFn: () => api.getPrinterStatus(p.id),
      refetchInterval: 30000,
    })),
  });
  const statusById = new Map((printers ?? []).map((p, i) => [p.id, statusQueries[i]?.data]));

  const printersByLocation = useMemo(() => {
    const map = new Map<string, Printer[]>();
    for (const p of printers ?? []) {
      const key = locationOf(p);
      map.set(key, [...(map.get(key) ?? []), p]);
    }
    return map;
  }, [printers]);
  const ungrouped = printersByLocation.get('') ?? [];
  const groupedCount = (printers?.length ?? 0) - ungrouped.length;

  const displayed = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = (locations ?? []).filter(
      (loc) => (!q || loc.name.toLowerCase().includes(q)) && (!hideEmpty || loc.printer_count > 0),
    );
    const byName = (a: PrinterLocation, b: PrinterLocation) =>
      a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
    return [...list].sort((a, b) => {
      switch (sortMode) {
        case 'name-desc':
          return byName(b, a);
        case 'count-asc':
          return a.printer_count - b.printer_count || byName(a, b);
        case 'count-desc':
          return b.printer_count - a.printer_count || byName(a, b);
        default:
          return byName(a, b);
      }
    });
  }, [locations, search, hideEmpty, sortMode]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['printer-locations'] });
    queryClient.invalidateQueries({ queryKey: ['printers'] });
  };
  const fail = (error: unknown) =>
    showToast(t('printers.locations.failed', { error: error instanceof Error ? error.message : String(error) }), 'error');

  const saveMutation = useMutation({
    mutationFn: ({ value, location }: { value: { name: string; icon: string; color: string }; location?: PrinterLocation }) =>
      location
        ? api.updatePrinterLocation({
            name: location.name,
            ...(value.name !== location.name ? { new_name: value.name } : {}),
            icon: value.icon || null,
            color: value.color || null,
          })
        : api.createPrinterLocation({ name: value.name, icon: value.icon || null, color: value.color || null }),
    onSuccess: (saved, { location }) => {
      refresh();
      if (location && expanded === location.name) setExpanded(saved.name);
      showToast(location ? t('printers.locations.saved') : t('printers.locations.created'));
      setDialog(null);
    },
    onError: fail,
  });

  const moveMutation = useMutation({
    mutationFn: ({ ids, location }: { ids: number[]; location: string | null }) => api.assignPrinterLocation(ids, location),
    onSuccess: ({ moved }) => {
      refresh();
      showToast(t('printers.locations.moved', { count: moved }));
      setSelectedPrinters(new Set());
      setMoving(null);
    },
    onError: fail,
  });

  const deleteMutation = useMutation({
    mutationFn: (names: string[]) => api.deletePrinterLocations(names),
    onSuccess: ({ deleted }) => {
      refresh();
      showToast(t('printers.locations.deleted', { count: deleted }));
      setSelectedLocations(new Set());
      setSelectingLocations(false);
      setDeleting(null);
    },
    onError: (error) => {
      fail(error);
      setDeleting(null);
    },
  });

  const busy = moveMutation.isPending || deleteMutation.isPending || saveMutation.isPending;

  const toggle = <T,>(set: Set<T>, value: T) => {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    return next;
  };

  if (printersLoading || locationsLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  const deletingPrinterCount = (deleting ?? []).reduce(
    (sum, name) => sum + (locations?.find((l) => l.name === name)?.printer_count ?? 0),
    0,
  );
  const sortLabel = (mode: SortMode) =>
    ({
      'name-asc': t('printers.locations.sortNameAsc'),
      'name-desc': t('printers.locations.sortNameDesc'),
      'count-asc': t('printers.locations.sortCountAsc'),
      'count-desc': t('printers.locations.sortCountDesc'),
    })[mode];

  const renderPrinter = (printer: Printer, removable: boolean) => (
    <PrinterRow
      key={printer.id}
      printer={printer}
      status={statusById.get(printer.id)}
      selected={selectedPrinters.has(printer.id)}
      canEdit={canEdit && !selectingLocations}
      busy={busy}
      onToggle={() => setSelectedPrinters(toggle(selectedPrinters, printer.id))}
      onMove={() => setMoving({ ids: [printer.id], printerName: printer.name })}
      onRemove={removable ? () => moveMutation.mutate({ ids: [printer.id], location: null }) : undefined}
    />
  );

  const allUngroupedSelected = ungrouped.length > 0 && ungrouped.every((p) => selectedPrinters.has(p.id));

  return (
    <div className="p-4 md:p-8 pb-28">
      <Link
        to="/"
        className="inline-flex items-center gap-1 mb-3 text-sm text-bambu-gray hover:text-white transition-colors"
      >
        <ArrowLeft className="w-4 h-4" /> {t('printers.locations.backToPrinters')}
      </Link>
      <div className="mb-4">
        <div className="flex items-center gap-3 mb-1">
          <Box className="w-[25px] h-[25px] text-bambu-green" />
          <h1 className="text-2xl font-bold text-white">{t('printers.locations.title')}</h1>
        </div>
        <p className="text-sm text-bambu-gray">
          {t('printers.locations.subtitle', { grouped: groupedCount, ungrouped: ungrouped.length })}
        </p>
      </div>

      <div className="flex flex-wrap gap-3 mb-6">
        <div className="relative flex-1 min-w-[12rem]">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('printers.locations.search')}
            aria-label={t('printers.locations.search')}
            className="w-full pl-9 pr-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
          />
        </div>
        <Button onClick={() => setHideEmpty(!hideEmpty)} variant={hideEmpty ? 'primary' : 'secondary'}>
          {hideEmpty ? t('printers.locations.showEmpty') : t('printers.locations.hideEmpty')}
        </Button>
        {canEdit && (
          <Button
            onClick={() => {
              setSelectedLocations(new Set());
              setSelectingLocations(!selectingLocations);
            }}
            variant={selectingLocations ? 'primary' : 'secondary'}
          >
            <CheckSquare className="w-4 h-4 mr-1" />
            {selectingLocations ? t('printers.locations.done') : t('printers.locations.select')}
          </Button>
        )}
        <div className="relative">
          <Button onClick={() => setShowSortMenu(!showSortMenu)} variant="secondary" aria-haspopup="menu">
            <ChevronDown className={`w-4 h-4 mr-1 transition-transform ${showSortMenu ? 'rotate-180' : ''}`} />
            {sortLabel(sortMode)}
          </Button>
          {showSortMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowSortMenu(false)} />
              <div role="menu" className="absolute right-0 top-full mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg p-1 min-w-[10rem]">
                {SORT_MODES.map((mode) => (
                  <button
                    key={mode}
                    role="menuitem"
                    onClick={() => {
                      setSortMode(mode);
                      setShowSortMenu(false);
                    }}
                    className={`w-full text-left px-3 py-2 text-sm rounded transition-colors ${
                      sortMode === mode ? 'bg-bambu-green text-white' : 'text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white'
                    }`}
                  >
                    {sortLabel(mode)}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
        {canEdit && (
          <Button onClick={() => setDialog({ mode: 'create' })}>
            <Plus className="w-4 h-4 mr-1" />
            {t('printers.locations.create')}
          </Button>
        )}
      </div>

      {displayed.length === 0 && ungrouped.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12 text-bambu-gray">
            {search ? t('printers.locations.noResults') : t('printers.locations.none')}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {displayed.map((loc) => {
            const isExpanded = expanded === loc.name;
            const isSelected = selectedLocations.has(loc.name);
            return (
              <Card key={loc.name}>
                <CardContent className="p-4">
                  <div className="flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        selectingLocations
                          ? setSelectedLocations(toggle(selectedLocations, loc.name))
                          : setExpanded(isExpanded ? null : loc.name)
                      }
                      className="flex items-center gap-3 flex-1 min-w-0 text-left"
                      aria-expanded={selectingLocations ? undefined : isExpanded}
                      aria-pressed={selectingLocations ? isSelected : undefined}
                    >
                      {selectingLocations ? (
                        isSelected ? (
                          <CheckSquare className="w-4 h-4 text-bambu-green flex-shrink-0" />
                        ) : (
                          <Square className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                        )
                      ) : (
                        <ChevronDown
                          className={`w-4 h-4 text-bambu-gray flex-shrink-0 transition-transform ${isExpanded ? 'rotate-180' : ''}`}
                        />
                      )}
                      <LocationIcon location={loc} />
                      <div className="min-w-0">
                        <p className="text-white font-medium truncate">{loc.name}</p>
                        <p className="text-sm text-bambu-gray">{t('printers.locations.printerCount', { count: loc.printer_count })}</p>
                      </div>
                    </button>
                    {canEdit && !selectingLocations && (
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDialog({ mode: 'edit', location: loc })}
                          disabled={busy}
                          className="text-bambu-gray hover:text-blue-400 hover:bg-blue-500/10"
                          title={t('common.edit')}
                          aria-label={t('common.edit')}
                        >
                          <Pencil className="w-4 h-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleting([loc.name])}
                          disabled={busy}
                          className="text-red-500 hover:text-red-400 hover:bg-red-500/10"
                          title={t('common.delete')}
                          aria-label={t('common.delete')}
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                  {isExpanded && (
                    <div className="border-t border-bambu-dark-tertiary pt-3 mt-3 space-y-2">
                      {(printersByLocation.get(loc.name) ?? []).length === 0 ? (
                        <p className="text-sm text-bambu-gray text-center py-4">{t('printers.locations.noPrinters')}</p>
                      ) : (
                        (printersByLocation.get(loc.name) ?? []).map((p) => renderPrinter(p, true))
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}

          {ungrouped.length > 0 && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-medium text-bambu-gray">
                  {t('printers.locations.ungroupedPrinters', { count: ungrouped.length })}
                </h3>
                {canEdit && !selectingLocations && (
                  <button
                    type="button"
                    onClick={() => {
                      const next = new Set(selectedPrinters);
                      ungrouped.forEach((p) => (allUngroupedSelected ? next.delete(p.id) : next.add(p.id)));
                      setSelectedPrinters(next);
                    }}
                    className="text-xs text-bambu-green hover:text-bambu-green-light transition-colors"
                  >
                    {allUngroupedSelected ? t('common.deselectAll') : t('common.selectAll')}
                  </button>
                )}
              </div>
              <div className="space-y-2">{ungrouped.map((p) => renderPrinter(p, false))}</div>
            </div>
          )}
        </div>
      )}

      {canEdit && !selectingLocations && selectedPrinters.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-bambu-dark border border-bambu-dark-tertiary rounded-xl shadow-2xl px-6 py-4 flex items-center gap-4 z-40">
          <span className="text-white text-sm">{t('printers.locations.selected', { count: selectedPrinters.size })}</span>
          <Button onClick={() => setMoving({ ids: Array.from(selectedPrinters) })} disabled={busy}>
            <Move className="w-4 h-4 mr-1" />
            {t('printers.locations.moveButton')}
          </Button>
          <Button variant="ghost" onClick={() => setSelectedPrinters(new Set())} disabled={busy}>
            {t('common.cancel')}
          </Button>
        </div>
      )}

      {selectingLocations && selectedLocations.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-bambu-dark border border-bambu-dark-tertiary rounded-xl shadow-2xl px-6 py-4 flex items-center gap-4 z-40">
          <span className="text-white text-sm">{t('printers.locations.selected', { count: selectedLocations.size })}</span>
          <Button variant="danger" onClick={() => setDeleting(Array.from(selectedLocations))} disabled={busy}>
            <Trash2 className="w-4 h-4 mr-1" />
            {t('printers.locations.deleteSelected')}
          </Button>
          <Button variant="ghost" onClick={() => setSelectedLocations(new Set())} disabled={busy}>
            {t('common.cancel')}
          </Button>
        </div>
      )}

      {dialog && (
        <LocationDialog
          initial={dialog.mode === 'edit' ? dialog.location : undefined}
          saving={saveMutation.isPending}
          onSave={(value) => saveMutation.mutate({ value, location: dialog.mode === 'edit' ? dialog.location : undefined })}
          onCancel={() => setDialog(null)}
        />
      )}

      {moving && (
        <MoveDialog
          count={moving.ids.length}
          printerName={moving.printerName}
          locations={locations ?? []}
          saving={moveMutation.isPending}
          onMove={(location) => moveMutation.mutate({ ids: moving.ids, location })}
          onCancel={() => setMoving(null)}
        />
      )}

      {deleting && (
        <ConfirmModal
          title={
            deleting.length === 1
              ? t('printers.locations.deleteTitle')
              : t('printers.locations.deleteTitleMany', { count: deleting.length })
          }
          message={t('printers.locations.deleteMessage', {
            count: deletingPrinterCount,
            names: deleting.join(', '),
          })}
          confirmText={t('common.delete')}
          variant="danger"
          isLoading={deleteMutation.isPending}
          onConfirm={() => deleteMutation.mutate(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
