import { useState, useMemo, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Box, ChevronDown, Loader2, Plus, Search, Trash2, Move, UserMinus, Pencil, CheckSquare, Square, ChevronUp } from 'lucide-react';
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
import { getLocationIcon, setLocationIcon, removeLocationIcon } from '../utils/printerLocationIcons';
import { getLocationColor, setLocationColor, removeLocationColor, getPresetColors } from '../utils/printerLocationColors';
import { IconPicker, getIconByName } from '../components/IconPicker';

export function PrinterLocationsPage() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { showToast } = useToast();

  const [search, setSearch] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<{
    name: string;
    count: number;
    isBulkDelete?: boolean;
    names?: string[];
  } | null>(null);
  const [createLocation, setCreateLocation] = useState(false);
  const [newLocationName, setNewLocationName] = useState('');
  const [createLocationIcon, setCreateLocationIcon] = useState('');
  const [createLocationColor, setCreateLocationColor] = useState('');
  const [expandedLocation, setExpandedLocation] = useState<string | null>(null);

  // Per-printer move: which printer + which target location
  const [movePrinter, setMovePrinter] = useState<{ id: number; name: string } | null>(null);
  const [moveTarget, setMoveTarget] = useState<string>('');

  // Edit location (name + icon + color)
  const [editLocation, setEditLocation] = useState<{ name: string } | null>(null);
  const [editLocationName, setEditLocationName] = useState('');
  const [editLocationIcon, setEditLocationIcon] = useState('');
  const [editLocationColor, setEditLocationColor] = useState('');

  // Hide empty groups toggle — read from localStorage on mount
  const [hideEmptyGroups, setHideEmptyGroups] = useState(() => {
    try {
      const saved = localStorage.getItem('hideEmptyGroups');
      return saved === 'true';
    } catch {
      return false;
    }
  });

  // Persist hideEmptyGroups to localStorage
  useEffect(() => {
    try {
      localStorage.setItem('hideEmptyGroups', String(hideEmptyGroups));
    } catch {
      // localStorage unavailable
    }
  }, [hideEmptyGroups]);

  // Bulk selection
  const [selectedPrinterIds, setSelectedPrinterIds] = useState<Set<number>>(new Set());
  const [bulkMoveTarget, setBulkMoveTarget] = useState<string>('');
  const [showBulkMove, setShowBulkMove] = useState(false);

  // Group selection mode
  const [groupSelectionMode, setGroupSelectionMode] = useState(false);
  const [selectedGroupNames, setSelectedGroupNames] = useState<Set<string>>(new Set());

  // Group sort mode — persisted in localStorage
  const [sortMode, setSortMode] = useState<'name-asc' | 'name-desc' | 'count-asc' | 'count-desc'>(() => {
    try {
      const saved = localStorage.getItem('locationSortMode');
      if (saved === 'name-desc' || saved === 'count-asc' || saved === 'count-desc') return saved;
    } catch { /* ignore */ }
    return 'name-asc';
  });

  // Persist sort mode
  useEffect(() => {
    try {
      localStorage.setItem('locationSortMode', sortMode);
    } catch { /* ignore */ }
  }, [sortMode]);

  // Sort dropdown
  const [showSortMenu, setShowSortMenu] = useState(false);

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

    return Array.from(locationMap.entries());
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

  // Filter locations by search
  const filteredLocations = useMemo(() => {
    if (!search.trim()) return locations;
    const q = search.toLowerCase();
    return locations.filter(([name]) => name.toLowerCase().includes(q));
  }, [locations, search]);

  // Filter out empty groups if toggle is on
  const displayedLocations = useMemo(() => {
    let result = hideEmptyGroups
      ? filteredLocations.filter(([, count]) => count > 0)
      : filteredLocations;

    // Ungrouped (empty name) always first
    const ungrouped = result.find(([name]) => !name);
    const named = result.filter(([name]) => name);

    // Sort named groups
    const sorted = [...named].sort(([a, countA], [b, countB]) => {
      switch (sortMode) {
        case 'name-asc':
          return a.localeCompare(b);
        case 'name-desc':
          return b.localeCompare(a);
        case 'count-asc':
          return countA - countB || a.localeCompare(b);
        case 'count-desc':
          return countB - countA || a.localeCompare(b);
      }
    });

    return ungrouped ? [ungrouped, ...sorted] : sorted;
  }, [filteredLocations, hideEmptyGroups, sortMode]);

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
      removeLocationIcon(locationName);
      removeLocationColor(locationName);
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

  // Edit location mutation (name + icon + color)
  const editLocationMutation = useMutation({
    mutationFn: async ({
      oldName,
      newName,
      newIcon,
      newColor,
    }: {
      oldName: string;
      newName: string;
      newIcon: string;
      newColor: string;
    }) => {
      const currentPrinters = queryClient.getQueryData<PrinterType[]>(['printers']) || [];
      const printersInLocation = currentPrinters.filter(
        (p) => (p.location || '') === oldName,
      );
      if (printersInLocation.length === 0) return;
      await Promise.all(
        printersInLocation.map((p) => api.updatePrinter(p.id, { location: newName })),
      );
      return { oldName, newName, newIcon };
    },
    onSuccess: (_, { oldName, newName, newIcon, newColor }) => {
      removeCachedPrinterLocation(oldName);
      if (oldName !== newName) {
        removeLocationIcon(oldName);
        removeLocationColor(oldName);
      }
      addCachedPrinterLocation(newName);
      setLocationIcon(newName, newIcon);
      setLocationColor(newName, newColor);
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.editSaved', 'Location updated'));
      setEditLocation(null);
      setEditLocationName('');
      setEditLocationIcon('');
      setEditLocationColor('');
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.editError', 'Failed to update location');
      showToast(message, 'error');
      setEditLocation(null);
      setEditLocationName('');
      setEditLocationIcon('');
      setEditLocationColor('');
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

  // Bulk move mutation
  const bulkMoveMutation = useMutation({
    mutationFn: async ({
      printerIds,
      location,
    }: {
      printerIds: number[];
      location: string;
    }) => {
      await Promise.all(
        printerIds.map((id) => api.updatePrinter(id, { location })),
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.bulkMoved', '{{count}} printers moved', {
        count: selectedPrinterIds.size,
      }));
      clearSelection();
      setShowBulkMove(false);
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.bulkMoveError', 'Failed to move printers');
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
      setLocationIcon(newLocationName, createLocationIcon);
      setLocationColor(newLocationName, createLocationColor);
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.created', 'Location created'));
      setCreateLocation(false);
      setNewLocationName('');
      setCreateLocationIcon('');
      setCreateLocationColor('');
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.createError', 'Failed to create location');
      showToast(message, 'error');
    },
  });

  // Bulk delete groups mutation
  const bulkDeleteGroupsMutation = useMutation({
    mutationFn: async (groupNames: string[]) => {
      const currentPrinters = queryClient.getQueryData<PrinterType[]>(['printers']) || [];

      const printersToRemove = currentPrinters.filter((p) =>
        groupNames.includes((p.location || '')),
      );

      // Use empty string '' to clear location
      await Promise.all(
        printersToRemove.map((p) => api.updatePrinter(p.id, { location: '' })),
      );

      return groupNames;
    },
    onSuccess: (_, groupNames) => {
      groupNames.forEach((name) => {
        removeCachedPrinterLocation(name);
        removeLocationIcon(name);
        removeLocationColor(name);
      });
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.groupsDeleted', '{{count}} group(s) deleted', {
        count: groupNames.length,
      }));
      clearGroupSelection();
      setGroupSelectionMode(false);
      setDeleteConfirm(null);
    },
    onError: (error: unknown) => {
      const message =
        error instanceof Error && error.message
          ? error.message
          : t('printers.locations.deleteGroupsError', 'Failed to delete groups');
      showToast(message, 'error');
      setGroupSelectionMode(false);
      setDeleteConfirm(null);
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

  const handleCancelCreate = () => {
    setCreateLocation(false);
    setNewLocationName('');
    setCreateLocationIcon('');
    setCreateLocationColor('');
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

  const handleEditLocation = () => {
    if (!editLocation || !editLocationName.trim()) return;
    // Check if location already exists (with different name)
    if (editLocationName.trim() !== editLocation.name &&
        locations.some(([name]) => name === editLocationName.trim())) {
      showToast(t('printers.locations.exists', 'Location already exists'), 'error');
      return;
    }
    editLocationMutation.mutate({
      oldName: editLocation.name,
      newName: editLocationName.trim(),
      newIcon: editLocationIcon,
      newColor: editLocationColor,
    });
  };

  const handleCancelEdit = () => {
    setEditLocation(null);
    setEditLocationName('');
    setEditLocationIcon('');
    setEditLocationColor('');
  };

  // Bulk selection helpers
  const togglePrinterSelection = (printerId: number) => {
    const next = new Set(selectedPrinterIds);
    if (next.has(printerId)) {
      next.delete(printerId);
    } else {
      next.add(printerId);
    }
    setSelectedPrinterIds(next);
  };

  const selectAllPrinters = (printers: PrinterType[]) => {
    setSelectedPrinterIds(new Set(printers.map((p) => p.id)));
  };

  const clearSelection = () => {
    setSelectedPrinterIds(new Set());
    setBulkMoveTarget('');
  };

  const handleBulkMove = () => {
    if (selectedPrinterIds.size === 0 || !bulkMoveTarget) return;
    bulkMoveMutation.mutate({
      printerIds: Array.from(selectedPrinterIds),
      location: bulkMoveTarget === '__ungrouped__' ? '' : bulkMoveTarget,
    });
  };

  // Group selection helpers
  const toggleGroupSelection = (groupName: string) => {
    const next = new Set(selectedGroupNames);
    if (next.has(groupName)) {
      next.delete(groupName);
    } else {
      next.add(groupName);
    }
    setSelectedGroupNames(next);
  };

  const clearGroupSelection = () => {
    setSelectedGroupNames(new Set());
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
          onClick={() => setHideEmptyGroups(!hideEmptyGroups)}
          variant={hideEmptyGroups ? 'default' : 'secondary'}
          className="h-10"
        >
          <Box className="w-4 h-4 mr-1" />
          {hideEmptyGroups
            ? t('printers.locations.showEmpty', 'Show empty')
            : t('printers.locations.hideEmpty', 'Hide empty')}
        </Button>
        <Button
          onClick={() => {
            if (groupSelectionMode) {
              clearGroupSelection();
              setGroupSelectionMode(false);
            } else {
              setGroupSelectionMode(true);
            }
          }}
          variant={groupSelectionMode ? 'default' : 'secondary'}
          className="h-10"
        >
          <CheckSquare className="w-4 h-4 mr-1" />
          {groupSelectionMode
            ? t('printers.locations.selectGroupsActive', 'Done')
            : t('printers.locations.selectGroups', 'Select')}
        </Button>
        <div className="relative">
          <Button
            onClick={() => setShowSortMenu(!showSortMenu)}
            variant="secondary"
            className="h-10"
          >
            <ChevronDown className={`w-4 h-4 mr-1 transition-transform duration-200 ${showSortMenu ? 'rotate-180' : ''}`} />
            {sortMode === 'name-asc' ? t('printers.locations.sortNameAsc', 'Name A→Z') :
             sortMode === 'name-desc' ? t('printers.locations.sortNameDesc', 'Name Z→A') :
             sortMode === 'count-asc' ? t('printers.locations.sortCountAsc', 'Count ↑') :
             t('printers.locations.sortCountDesc', 'Count ↓')}
          </Button>
          {showSortMenu && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setShowSortMenu(false)} />
              <div className="absolute right-0 top-full mt-1 z-50 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-lg p-1">
                {([
                  ['name-asc', t('printers.locations.sortNameAsc', 'Name A→Z')],
                  ['name-desc', t('printers.locations.sortNameDesc', 'Name Z→A')],
                  ['count-asc', t('printers.locations.sortCountAsc', 'Count ↑')],
                  ['count-desc', t('printers.locations.sortCountDesc', 'Count ↓')],
                ] as const).map(([mode, label]) => (
                  <button
                    key={mode}
                    onClick={() => {
                      setSortMode(mode);
                      setShowSortMenu(false);
                    }}
                    className={`w-full text-left px-3 py-2 text-sm rounded transition-colors ${
                      sortMode === mode
                        ? 'bg-bambu-green text-white'
                        : 'text-bambu-gray hover:bg-bambu-dark-tertiary hover:text-white'
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </>
          )}
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
      {displayedLocations.length === 0 && ungroupedCount === 0 ? (
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
          {displayedLocations
            .filter(([name]) => name) // skip ungrouped
            .map(([name, count]) => {
              const printersInGroup = getPrintersInLocation(name);
              const isExpanded = expandedLocation === name;

              return (
                <Card key={name}>
                  <CardContent className="p-4">
                    {/* Group header */}
                    <div className="flex items-center justify-between relative">
                      <div
                        onClick={groupSelectionMode ? undefined : () =>
                          setExpandedLocation(isExpanded ? null : name)
                        }
                        className="flex items-center gap-3 flex-1 min-w-0 cursor-pointer group"
                        role="button"
                        tabIndex={groupSelectionMode ? -1 : 0}
                      >
                        {groupSelectionMode ? (
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleGroupSelection(name);
                            }}
                            className="text-bambu-gray hover:text-bambu-green transition-colors flex-shrink-0"
                            title={t('printers.locations.selectGroup', 'Select group')}
                          >
                            {selectedGroupNames.has(name) ? (
                              <CheckSquare className="w-4 h-4 text-bambu-green" />
                            ) : (
                              <Square className="w-4 h-4" />
                            )}
                          </button>
                        ) : (
                          <ChevronDown className={`w-4 h-4 text-bambu-gray transition-transform duration-200 ${isExpanded ? 'rotate-180' : ''}`} />
                        )}
                        <div className="p-2 rounded-lg bg-bambu-dark group-hover:bg-bambu-dark-tertiary transition-colors relative">
                          {(() => {
                            const iconName = getLocationIcon(name);
                            const color = getLocationColor(name);
                            if (iconName) {
                              const Icon = getIconByName(iconName);
                              return <Icon className="w-[25px] h-[25px] text-bambu-gray" />;
                            }
                            return <Box className="w-[25px] h-[25px] text-bambu-gray" />;
                          })()}
                          {(() => {
                            const color = getLocationColor(name);
                            if (!color) return null;
                            return (
                              <div
                                className="absolute top-0 left-0 w-1 h-full rounded-l-lg"
                                style={{ backgroundColor: color }}
                              />
                            );
                          })()}
                        </div>
                        <div className="flex-1 text-left">
                          <p className="text-white font-medium">{name}</p>
                          <p className="text-sm text-bambu-gray">
                            {count} {count === 1 ? t('printers.locations.printer') : t('printers.locations.printers')}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            const icon = getLocationIcon(name);
                            const color = getLocationColor(name);
                            setEditLocation({ name });
                            setEditLocationName(name);
                            setEditLocationIcon(icon || '');
                            setEditLocationColor(color || '');
                          }}
                          disabled={editLocationMutation.isPending}
                          className="text-bambu-gray hover:text-blue-400 hover:bg-blue-500/10"
                          title={t('printers.locations.edit', 'Edit')}
                        >
                          <Pencil className="w-4 h-4" />
                        </Button>
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
                              const isSelected = selectedPrinterIds.has(printer.id);

                              return (
                                <div
                                  key={printer.id}
                                  className={`flex items-center justify-between py-2 px-3 rounded-lg transition-colors ${
                                    isSelected
                                      ? 'bg-bambu-green/10 border border-bambu-green/30'
                                      : 'bg-bambu-dark-secondary hover:bg-bambu-dark'
                                  }`}
                                >
                                  <div className="flex items-center gap-3">
                                    {/* Checkbox */}
                                    <button
                                      onClick={() => togglePrinterSelection(printer.id)}
                                      className="text-bambu-gray hover:text-bambu-green transition-colors"
                                      title={t('printers.locations.selectPrinter', 'Select printer')}
                                    >
                                      {isSelected ? (
                                        <CheckSquare className="w-4 h-4 text-bambu-green" />
                                      ) : (
                                        <Square className="w-4 h-4" />
                                      )}
                                    </button>
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
                {t('printers.locations.ungroupedPrinters', 'Ungrouped Printers')}, {t('printers.locations.printer', { count: ungroupedCount, smartCount: true })}
              </h3>
              <div className="space-y-2">
                {getPrintersInLocation('').map((printer) => {
                  const status = getPrinterStatus(printer.id);
                  const isConnected = status?.connected;
                  const state = status?.state;
                  const isSelected = selectedPrinterIds.has(printer.id);

                  return (
                    <div
                      key={printer.id}
                      className={`flex items-center justify-between py-2 px-3 rounded-lg transition-colors ${
                        isSelected
                          ? 'bg-bambu-green/10 border border-bambu-green/30'
                          : 'bg-bambu-dark-secondary hover:bg-bambu-dark'
                      }`}
                    >
                      <div className="flex items-center gap-3">
                        {/* Checkbox */}
                        <button
                          onClick={() => togglePrinterSelection(printer.id)}
                          className="text-bambu-gray hover:text-bambu-green transition-colors"
                          title={t('printers.locations.selectPrinter', 'Select printer')}
                        >
                          {isSelected ? (
                            <CheckSquare className="w-4 h-4 text-bambu-green" />
                          ) : (
                            <Square className="w-4 h-4" />
                          )}
                        </button>
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

      {/* Bulk move bar */}
      {selectedPrinterIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-bambu-dark border border-bambu-dark-tertiary rounded-xl shadow-2xl px-6 py-4 flex items-center gap-4 z-40">
          <div className="text-white text-sm">
            {t('printers.locations.selected', '{{count}} selected', { count: selectedPrinterIds.size })}
          </div>
          <Button
            onClick={() => setShowBulkMove(true)}
            disabled={bulkMoveMutation.isPending}
            className="h-9"
          >
            <Move className="w-4 h-4 mr-1" />
            {t('printers.locations.moveSelected', 'Move')}
          </Button>
          <Button
            variant="ghost"
            onClick={clearSelection}
            disabled={bulkMoveMutation.isPending}
            className="h-9 text-bambu-gray hover:text-white"
          >
            {t('common.cancel')}
          </Button>
        </div>
      )}

      {/* Bulk delete groups bar */}
      {selectedGroupNames.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 bg-bambu-dark border border-bambu-dark-tertiary rounded-xl shadow-2xl px-6 py-4 flex items-center gap-4 z-40">
          <div className="text-white text-sm">
            {t('printers.locations.selected', '{{count}} selected', { count: selectedGroupNames.size })}
          </div>
          <Button
            onClick={() => {
              const names = Array.from(selectedGroupNames);
              const printerCount = (printers || []).filter((p) =>
                names.includes((p.location || ''))
              ).length;
              setDeleteConfirm({
                name: names.join(', '),
                count: printerCount,
                isBulkDelete: true,
                names,
              });
            }}
            disabled={bulkDeleteGroupsMutation.isPending}
            className="h-9 bg-red-500/20 border border-red-500/50 text-red-400 hover:bg-red-500/30"
          >
            <Trash2 className="w-4 h-4 mr-1" />
            {t('printers.locations.deleteSelectedGroups', 'Delete selected')}
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              clearGroupSelection();
              setGroupSelectionMode(false);
            }}
            disabled={bulkDeleteGroupsMutation.isPending}
            className="h-9 text-bambu-gray hover:text-white"
          >
            {t('common.cancel')}
          </Button>
        </div>
      )}

      {/* Bulk Move Modal */}
      {showBulkMove && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardContent className="space-y-4">
              <h2 className="text-lg font-semibold text-white">
                {t('printers.locations.moveSelectedTitle', 'Move Printers')}
              </h2>
              <p className="text-sm text-bambu-gray">
                {t('printers.locations.moveSelectedCount', '{{count}} printers', { count: selectedPrinterIds.size })}
              </p>
              <select
                value={bulkMoveTarget}
                onChange={(e) => setBulkMoveTarget(e.target.value)}
                className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                disabled={bulkMoveMutation.isPending}
                autoFocus
              >
                <option value="" disabled className="text-bambu-gray">
                  {t('printers.locations.selectLocation', 'Select a location...')}
                </option>
                {locations
                  .filter(([name]) => name)
                  .map(([name]) => (
                    <option key={name} value={name} className="text-white">
                      {name}
                    </option>
                  ))}
                <option value="__ungrouped__" className="text-white">
                  {t('printers.locations.ungrouped', 'Ungrouped')}
                </option>
              </select>
              <div className="flex gap-2 justify-end">
                <Button
                  variant="secondary"
                  onClick={() => setShowBulkMove(false)}
                  disabled={bulkMoveMutation.isPending}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleBulkMove}
                  disabled={bulkMoveMutation.isPending || !bulkMoveTarget}
                >
                  {bulkMoveMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('common.saving')}
                    </>
                  ) : (
                    t('printers.locations.moveSelected', 'Move')
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
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
                  if (e.key === 'Escape') handleCancelCreate();
                }}
              />
              <div>
                <p className="text-sm text-bambu-gray mb-2">
                  {t('printers.locations.editIcon', 'Icon')}
                </p>
                <IconPicker
                  value={createLocationIcon}
                  onChange={setCreateLocationIcon}
                />
              </div>
              <div>
                <p className="text-sm text-bambu-gray mb-2">
                  {t('printers.locations.editColor', 'Color')}
                </p>
                <div className="flex gap-2 flex-wrap">
                  {getPresetColors().map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setCreateLocationColor(createLocationColor === color ? '' : color)}
                      className={`w-8 h-8 rounded-lg transition-all ${
                        createLocationColor === color
                          ? 'ring-2 ring-white ring-offset-2 ring-offset-bambu-dark-secondary scale-110'
                          : 'hover:scale-105'
                      }`}
                      style={{ backgroundColor: color }}
                      title={color}
                    />
                  ))}
                  {createLocationColor && (
                    <button
                      type="button"
                      onClick={() => setCreateLocationColor('')}
                      className="w-8 h-8 rounded-lg border-2 border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-gray hover:text-white hover:border-bambu-gray transition-all flex items-center justify-center"
                      title="No color"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                    </button>
                  )}
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button
                  variant="secondary"
                  onClick={handleCancelCreate}
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
          onConfirm={() => {
            if (deleteConfirm.isBulkDelete && deleteConfirm.names) {
              bulkDeleteGroupsMutation.mutate(deleteConfirm.names);
            } else {
              deleteLocationMutation.mutate(deleteConfirm.name);
            }
          }}
          onCancel={() => setDeleteConfirm(null)}
          title={deleteConfirm.isBulkDelete
            ? t('printers.locations.deleteSelectedGroupsTitle', 'Delete Selected Groups')
            : t('printers.locations.deleteTitle', 'Delete Location')}
          message={deleteConfirm.isBulkDelete
            ? t('printers.locations.deleteSelectedGroupsDescription', 'Are you sure? This will remove {{count}} group(s) and ungroup all printers in them.', {
                count: deleteConfirm.count,
              })
            : t('printers.locations.deleteDescription', 'Are you sure? This will remove this location from {{count}} printer(s).', {
                count: deleteConfirm.count,
              })}
          confirmText={deleteConfirm.isBulkDelete
            ? t('printers.locations.deleteConfirm', 'Delete')
            : t('printers.locations.deleteConfirm', 'Delete')}
          variant="danger"
          isLoading={bulkDeleteGroupsMutation.isPending || deleteLocationMutation.isPending}
        />
      )}

      {/* Edit Location Modal */}
      {editLocation && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
            <CardContent className="space-y-4">
              <h2 className="text-lg font-semibold text-white">
                {t('printers.locations.editTitle', 'Edit Location')}
              </h2>
              <input
                type="text"
                value={editLocationName}
                onChange={(e) => setEditLocationName(e.target.value)}
                placeholder={t('printers.locations.editNamePlaceholder', 'Location name')}
                className="w-full px-4 py-2 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green/50 focus:border-bambu-green transition-colors"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleEditLocation();
                  if (e.key === 'Escape') handleCancelEdit();
                }}
              />
              <div>
                <p className="text-sm text-bambu-gray mb-2">
                  {t('printers.locations.editIcon', 'Icon')}
                </p>
                <IconPicker
                  value={editLocationIcon}
                  onChange={setEditLocationIcon}
                />
              </div>
              <div>
                <p className="text-sm text-bambu-gray mb-2">
                  {t('printers.locations.editColor', 'Color')}
                </p>
                <div className="flex gap-2 flex-wrap">
                  {getPresetColors().map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setEditLocationColor(editLocationColor === color ? '' : color)}
                      className={`w-8 h-8 rounded-lg transition-all ${
                        editLocationColor === color
                          ? 'ring-2 ring-white ring-offset-2 ring-offset-bambu-dark-secondary scale-110'
                          : 'hover:scale-105'
                      }`}
                      style={{ backgroundColor: color }}
                      title={color}
                    />
                  ))}
                  {editLocationColor && (
                    <button
                      type="button"
                      onClick={() => setEditLocationColor('')}
                      className="w-8 h-8 rounded-lg border-2 border-bambu-dark-tertiary bg-bambu-dark-secondary text-bambu-gray hover:text-white hover:border-bambu-gray transition-all flex items-center justify-center"
                      title="No color"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
                    </button>
                  )}
                </div>
              </div>
              <div className="flex gap-2 justify-end">
                <Button
                  variant="secondary"
                  onClick={handleCancelEdit}
                  disabled={editLocationMutation.isPending}
                >
                  {t('common.cancel')}
                </Button>
                <Button
                  onClick={handleEditLocation}
                  disabled={editLocationMutation.isPending || !editLocationName.trim()}
                >
                  {editLocationMutation.isPending ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      {t('common.saving')}
                    </>
                  ) : (
                    t('printers.locations.editSave', 'Save')
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
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
