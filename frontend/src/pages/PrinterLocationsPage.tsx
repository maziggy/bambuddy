import { useState, useMemo, useCallback, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Box, ChevronDown, ChevronUp, Loader2, Plus, Search, Trash2, X, Move, UserMinus } from 'lucide-react';
import { api } from '../api/client';
import type { Printer as PrinterType } from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';
import {
  getCachedPrinterLocations,
  addCachedPrinterLocation,
  removeCachedPrinterLocation,
} from '../utils/printerLocationsCache';

/** Debounce a value with the given delay (ms). */
function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function PrinterLocationsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { showToast } = useToast();

  const [search, setSearch] = useState('');
  const debouncedSearch = useDebounce(search, 200);
  const [deleteConfirm, setDeleteConfirm] = useState<{
    name: string;
    count: number;
  } | null>(null);
  const [createLocation, setCreateLocation] = useState(false);
  const [newLocationName, setNewLocationName] = useState('');
  const [expandedLocation, setExpandedLocation] = useState<string | null>(null);

  // Per-printer move: which printer + which target location
  const [movePrinter, setMovePrinter] = useState<{ id: number; name: string } | null>(null);
  const [moveTarget, setMoveTarget] = useState<string>('');

  // Filter cached locations for autocomplete (used in Move modal, not Create)

  // Fetch all printers to derive locations
  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Derive unique locations from printers + cached names
  // Computed on every render — no useMemo needed, array is tiny.
  const locations: [string, number][] = (() => {
    if (!printers) return [];

    const locationMap = new Map<string, number>();
    printers.forEach((p: PrinterType) => {
      const loc = p.location || '';
      locationMap.set(loc, (locationMap.get(loc) || 0) + 1);
    });

    // Merge with cached (possibly empty) locations so they show up even without printers
    const cached = getCachedPrinterLocations();
    const ungroupedKey = '';
    for (const name of cached) {
      if (name === ungroupedKey) continue;
      if (!locationMap.has(name)) {
        locationMap.set(name, 0); // 0 printers — user hasn't assigned any yet
      }
    }

    // Sort: empty name ("Ungrouped") first, then alphabetically
    return Array.from(locationMap.entries())
      .sort(([a], [b]) => {
        if (!a) return -1;
        if (!b) return 1;
        return a.localeCompare(b);
      });
  })();

  // Initial sync: populate cache with any real locations that aren't cached yet
  useEffect(() => {
    const printerLocs = printers?.map((p: PrinterType) => p.location || '') || [];
    const cache = new Set(getCachedPrinterLocations());
    const newNames = printerLocs.filter((n) => n !== '' && !cache.has(n));
    if (newNames.length > 0) {
      const updated = [...getCachedPrinterLocations(), ...newNames];
      localStorage.setItem('printerLocationsCache', JSON.stringify(updated));
    }
  }, []); // run once on mount

  // Filter locations by debounced search
  const filteredLocations = useMemo(() => {
    if (!debouncedSearch.trim()) return locations;
    const q = debouncedSearch.toLowerCase();
    return locations.filter(([name]) => name.toLowerCase().includes(q));
  }, [locations, debouncedSearch]);

  // Get printers for a location
  const getPrintersInLocation = (locationName: string) => {
    return (printers || []).filter(p => (p.location || '') === locationName);
  };

  // Get printer status from query cache (stable across renders)
  const getPrinterStatus = useCallback(
    (printerId: number) => {
      return queryClient.getQueryData<{ connected: boolean; state: string | null }>([
        'printerStatus',
        printerId,
      ]);
    },
    [queryClient],
  );

  // Delete location mutation — reads fresh data from cache instead of stale closure
  const deleteLocationMutation = useMutation({
    mutationFn: async (locationName: string) => {
      const currentPrinters = queryClient.getQueryData<PrinterType[]>(['printers']) || [];

      const printersInLocation = currentPrinters.filter(
        (p) => (p.location || '') === locationName,
      );

      if (printersInLocation.length === 0) return;

      // Use empty string '' to clear location — `undefined` may be dropped by JSON.stringify
      await Promise.all(
        printersInLocation.map((p) => api.updatePrinter(p.id, { location: '' })),
      );
    },
    onSuccess: (_, locationName) => {
      removeCachedPrinterLocation(locationName);
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.deleted', 'Location deleted'));
      setDeleteConfirm(null);
    },

    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.deleteError', 'Failed to delete location');
      showToast(message, 'error');
      setDeleteConfirm(null);
    },
  });

  // Remove printer from group mutation
  const removeFromGroupMutation = useMutation({
    mutationFn: ({ printerId }: { printerId: number }) =>
      api.updatePrinter(printerId, { location: '' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.removedFromGroup', 'Printer removed from group'));
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.removeFromGroupError', 'Failed to remove printer from group');
      showToast(message, 'error');
    },
  });

  // Move single printer mutation
  const movePrinterMutation = useMutation({
    mutationFn: ({
      printerId,
      location,
    }: {
      printerId: number;
      location: string;
    }) => api.updatePrinter(printerId, { location }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.moved', 'Printer moved'));
      setMovePrinter(null);
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.moveError', 'Failed to move printer');
      showToast(message, 'error');
    },
  });

  // Create location mutation — creates an empty location
  const createLocationMutation = useMutation({
    mutationFn: async (locationName: string) => {
      // No printers are moved — location is created empty.
      // User will manually assign printers via the Move button.
      await Promise.resolve();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.created', 'Location created'));
      setCreateLocation(false);
      setNewLocationName('');
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.createError', 'Failed to create location');
      showToast(message, 'error');
    },
  });

  const handleCreateLocation = () => {
    if (!newLocationName.trim()) {
      showToast(t('printers.locations.nameRequired', 'Location name is required'), 'error');
      return;
    }
    // Check if location already exists in DB
    if (locations.some(([name]) => name === newLocationName.trim())) {
      showToast(t('printers.locations.exists', 'Location already exists'), 'error');
      return;
    }
    // Cache the new location for future autocomplete
    addCachedPrinterLocation(newLocationName);
    createLocationMutation.mutate(newLocationName.trim());
  };

  const handleSelectSuggestion = (name: string) => {
    setNewLocationName(name);
    setShowSuggestions(false);
  };

  const handleCancelMove = () => {
    setMovePrinter(null);
    setMoveTarget('');
  };

  const handleConfirmMove = () => {
    if (!movePrinter || !moveTarget) return;
    if (moveTarget === '__ungrouped__') {
      movePrinterMutation.mutate({ printerId: movePrinter.id, location: '' });
    } else {
      movePrinterMutation.mutate({ printerId: movePrinter.id, location: moveTarget });
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
      </div>
    );
  }

  const ungroupedCount = locations.find(([name]) => !name)?.[1] || 0;
  const groupedCount = locations.reduce((sum, [, count]) => sum + count, 0) - ungroupedCount;

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="mb-2">
        <div className="flex items-center gap-3 mb-1">
          <Box className="w-[25px] h-[25px] text-bambu-green" />
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            {t('printers.locations.title', 'Printer Locations')}
          </h1>
        </div>
        <p className="text-sm text-bambu-gray">
          {t('printers.locations.subtitle', '{{grouped}} grouped, {{ungrouped}} ungrouped', {
            grouped: groupedCount,
            ungrouped: ungroupedCount,
          })}
        </p>
      </div>

      {/* Search & Add row */}
      <div className="flex gap-3 mb-6">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-bambu-gray" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('printers.locations.search', 'Search locations...')}
            className="w-full pl-9 pr-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
          />
        </div>
        <Button
          onClick={() => setCreateLocation(true)}
          disabled={createLocationMutation.isPending}
        >
          <Plus className="w-4 h-4 mr-1" />
          {t('printers.locations.create', 'New Location')}
        </Button>
      </div>

      {/* Locations list */}
      {filteredLocations.length === 0 && ungroupedCount === 0 ? (
        <Card>
          <CardContent className="text-center py-12 text-bambu-gray">
            {search
              ? t('printers.locations.noResults', 'No locations match your search')
              : t('printers.locations.none', 'No printer locations yet')}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {/* Named locations */}
          {filteredLocations
            .filter(([name]) => name) // skip ungrouped
            .map(([name, count]) => {
              const printersInGroup = getPrintersInLocation(name);
              const isExpanded = expandedLocation === name;

              return (
                <Card key={name}>
                  <CardContent className="p-4">
                    {/* Group header */}
                    <div className="flex items-center justify-between mb-3">
                      <button
                        onClick={() =>
                          setExpandedLocation(isExpanded ? null : name)
                        }
                        className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer group"
                      >
                        <ChevronDown className={`w-4 h-4 text-bambu-gray transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} />
                        <div className="p-2 rounded-lg bg-bambu-dark group-hover:bg-bambu-dark-tertiary transition-colors">
                          <Box className="w-[25px] h-[25px] text-bambu-gray" />
                        </div>
                        <div className="flex-1 text-left">
                          <p className="text-white font-medium">{name}</p>
                          <p className="text-sm text-bambu-gray">
                            {count} {count === 1 ? t('printers.locations.printer', 'printer') : t('printers.locations.printers', 'printers')}
                          </p>
                        </div>
                      </button>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteConfirm({ name, count })}
                          className="text-red-500 hover:text-red-400 hover:bg-red-500/10"
                        >
                          <Trash2 className="w-4 h-4" />
                        </Button>
                      </div>
                    </div>

                    {/* Expanded printers list */}
                    {isExpanded && (
                      <div className="border-t border-bambu-dark-tertiary pt-3 mt-3">
                        {printersInGroup.length === 0 ? (
                          <p className="text-sm text-bambu-gray text-center py-4">
                            {t('printers.locations.noPrinters', 'No printers in this location')}
                          </p>
                        ) : (
                          <div className="space-y-2">
                            {printersInGroup.map((printer) => {
                              const status = getPrinterStatus(printer.id);
                              const isConnected = status?.connected;
                              const state = status?.state;

                              return (
                                <div
                                  key={printer.id}
                                  className="flex items-center justify-between py-2 px-3 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark transition-colors"
                                >
                                  <div className="flex items-center gap-3">
                                    {/* Status indicator */}
                                    <div
                                      className={`w-2.5 h-2.5 rounded-full ${
                                        isConnected
                                          ? state === 'RUNNING' || state === 'PAUSE'
                                            ? 'bg-orange-500'
                                            : 'bg-bambu-green'
                                          : 'bg-gray-500'
                                      }`}
                                    />
                                    <div>
                                      <p className="text-white text-sm font-medium">{printer.name}</p>
                                      <p className="text-xs text-bambu-gray">
                                        {printer.model || 'Unknown model'}
                                      </p>
                                    </div>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    {/* Status badge */}
                                    {isConnected ? (
                                      <span
                                        className={`text-xs px-2 py-1 rounded-full ${
                                          state === 'RUNNING'
                                            ? 'bg-orange-500/20 text-orange-400'
                                            : state === 'PAUSE'
                                            ? 'bg-yellow-500/20 text-yellow-400'
                                            : state === 'FINISH'
                                            ? 'bg-bambu-green/20 text-bambu-green'
                                            : 'bg-bambu-dark text-bambu-gray'
                                        }`}
                                      >
                                        {state === 'RUNNING'
                                          ? 'Printing'
                                          : state === 'PAUSE'
                                          ? 'Paused'
                                          : state === 'FINISH'
                                          ? 'Finished'
                                          : 'Idle'}
                                      </span>
                                    ) : (
                                      <span className="text-xs px-2 py-1 rounded-full bg-gray-500/20 text-gray-400">
                                        Offline
                                      </span>
                                    )}
                                    {/* Move button */}
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      onClick={() => setMovePrinter({ id: printer.id, name: printer.name })}
                                      disabled={movePrinterMutation.isPending || removeFromGroupMutation.isPending}
                                      className="text-bambu-gray hover:text-bambu-green hover:bg-bambu-green/10"
                                      title={t('printers.locations.move', 'Move to another location')}
                                    >
                                      <Move className="w-3.5 h-3.5" />
                                    </Button>
                                    {/* Remove from group button */}
                                    <Button
                                      variant="ghost"
                                      size="sm"
                                      onClick={() => removeFromGroupMutation.mutate({ printerId: printer.id })}
                                      disabled={removeFromGroupMutation.isPending || movePrinterMutation.isPending}
                                      className="text-bambu-gray hover:text-red-400 hover:bg-red-500/10"
                                      title={t('printers.locations.removeFromGroup', 'Remove from group')}
                                    >
                                      <UserMinus className="w-3.5 h-3.5" />
                                    </Button>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}

          {/* Ungrouped printers list */}
          {ungroupedCount > 0 && (
            <div className="pt-4 border-t border-bambu-dark-tertiary">
              <h3 className="text-sm font-medium text-bambu-gray mb-3">
                {t('printers.locations.ungroupedPrinters', 'Ungrouped Printers')}, {ungroupedCount}
              </h3>
              <div className="space-y-2">
                {getPrintersInLocation('').map((printer) => {
                  const status = getPrinterStatus(printer.id);
                  const isConnected = status?.connected;
                  const state = status?.state;

                  return (
                    <div
                      key={printer.id}
                      className="flex items-center justify-between py-2 px-3 rounded-lg bg-bambu-dark-secondary hover:bg-bambu-dark transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        {/* Status indicator */}
                        <div
                          className={`w-2.5 h-2.5 rounded-full ${
                            isConnected
                              ? state === 'RUNNING' || state === 'PAUSE'
                                ? 'bg-orange-500'
                                : 'bg-bambu-green'
                              : 'bg-gray-500'
                          }`}
                        />
                        <div>
                          <p className="text-white text-sm font-medium">{printer.name}</p>
                          <p className="text-xs text-bambu-gray">
                            {printer.model || 'Unknown model'}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {/* Status badge */}
                        {isConnected ? (
                          <span
                            className={`text-xs px-2 py-1 rounded-full ${
                              state === 'RUNNING'
                                ? 'bg-orange-500/20 text-orange-400'
                                : state === 'PAUSE'
                                ? 'bg-yellow-500/20 text-yellow-400'
                                : state === 'FINISH'
                                ? 'bg-bambu-green/20 text-bambu-green'
                                : 'bg-bambu-dark text-bambu-gray'
                            }`}
                          >
                            {state === 'RUNNING'
                              ? 'Printing'
                              : state === 'PAUSE'
                              ? 'Paused'
                              : state === 'FINISH'
                              ? 'Finished'
                              : 'Idle'}
                          </span>
                        ) : (
                          <span className="text-xs px-2 py-1 rounded-full bg-gray-500/20 text-gray-400">
                            Offline
                          </span>
                        )}
                        {/* Move button */}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setMovePrinter({ id: printer.id, name: printer.name })}
                          disabled={movePrinterMutation.isPending}
                          className="text-bambu-gray hover:text-bambu-green hover:bg-bambu-green/10"
                          title={t('printers.locations.move', 'Move to another location')}
                        >
                          <Move className="w-3.5 h-3.5" />
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Create Location Modal */}
      {createLocation && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md relative">
            <CardContent className="space-y-4">
              <h2 className="text-lg font-semibold text-white">
                {t('printers.locations.createTitle', 'Create Location')}
              </h2>
              <input
                type="text"
                value={newLocationName}
                onChange={(e) => setNewLocationName(e.target.value)}
                placeholder={t('printers.locations.namePlaceholder', 'Location name')}
                className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCreateLocation();
                  if (e.key === 'Escape') setCreateLocation(false);
                }}
              />
              <div className="flex gap-2 justify-end">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setCreateLocation(false);
                    setNewLocationName('');
                  }}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleCreateLocation}
                  disabled={createLocationMutation.isPending || !newLocationName.trim()}
                >
                  {createLocationMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('common.saving')}
                    </>
                  ) : (
                    t('common.create')
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteConfirm && (
        <ConfirmModal
          onConfirm={() => deleteLocationMutation.mutate(deleteConfirm.name)}
          onCancel={() => setDeleteConfirm(null)}
          title={t('printers.locations.deleteTitle', 'Delete Location')}
          message={t('printers.locations.deleteDescription', 'Are you sure? This will remove this location from {{count}} printer(s).', {
            count: deleteConfirm.count,
          })}
          confirmText={t('printers.locations.deleteConfirm', 'Delete')}
          variant="danger"
          isLoading={deleteLocationMutation.isPending}
        />
      )}

      {/* Move Printer Modal */}
      {movePrinter && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardContent className="space-y-4">
              <h2 className="text-lg font-semibold text-white">
                {t('printers.locations.moveTo', 'Move Printer')}
              </h2>
              <p className="text-sm text-bambu-gray">
                {t('printers.locations.movePrinterName', '{{printer}}', { printer: movePrinter.name })}
              </p>

              {/* Target location selector */}
              <select
                value={moveTarget}
                onChange={(e) => setMoveTarget(e.target.value)}
                className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                disabled={movePrinterMutation.isPending}
              >
                <option value="" disabled className="text-bambu-gray">
                  {t('printers.locations.selectLocation', 'Select a location...')}
                </option>
                {/* Existing named locations */}
                {locations
                  .filter(([name]) => name) // skip ungrouped
                  .map(([name]) => (
                    <option key={name} value={name} className="text-white">
                      {name}
                    </option>
                  ))}
                {/* Ungrouped option */}
                <option value="__ungrouped__" className="text-white">
                  {t('printers.locations.ungrouped', 'Ungrouped')}
                </option>
              </select>

              <div className="flex gap-2 justify-end">
                <Button
                  variant="secondary"
                  onClick={handleCancelMove}
                  disabled={movePrinterMutation.isPending}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleConfirmMove}
                  disabled={movePrinterMutation.isPending || !moveTarget}
                >
                  {t('common.move')}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
