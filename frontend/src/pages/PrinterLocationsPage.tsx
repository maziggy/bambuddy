import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { Box, Loader2, Plus, Search, Trash2 } from 'lucide-react';
import { api } from '../api/client';
import type { Printer as PrinterType } from '../api/client';
import { Button } from '../components/Button';
import { Card, CardContent } from '../components/Card';
import { ConfirmModal } from '../components/ConfirmModal';
import { useToast } from '../contexts/ToastContext';

export function PrinterLocationsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { showToast } = useToast();

  const [search, setSearch] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState<{
    name: string;
    count: number;
  } | null>(null);
  const [createLocation, setCreateLocation] = useState(false);
  const [newLocationName, setNewLocationName] = useState('');

  // Fetch all printers to derive locations
  const { data: printers, isLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: api.getPrinters,
  });

  // Derive unique locations from printers
  const locations = useMemo(() => {
    if (!printers) return [];
    const locationMap = new Map<string, number>();
    printers.forEach((p: PrinterType) => {
      const loc = p.location || '';
      locationMap.set(loc, (locationMap.get(loc) || 0) + 1);
    });
    // Sort: empty name ("Ungrouped") first, then alphabetically
    return Array.from(locationMap.entries())
      .sort(([a], [b]) => {
        if (!a) return -1;
        if (!b) return 1;
        return a.localeCompare(b);
      });
  }, [printers]);

  // Filter locations by search
  const filteredLocations = useMemo(() => {
    if (!search.trim()) return locations;
    const q = search.toLowerCase();
    return locations.filter(([name]) => name.toLowerCase().includes(q));
  }, [locations, search]);

  // Delete location mutation
  const deleteLocationMutation = useMutation({
    mutationFn: (locationName: string) => {
      // Clear location from all printers in this location
      return Promise.all(
        (printers || [])
          .filter((p: PrinterType) => (p.location || '') === locationName)
          .map((p: PrinterType) =>
            api.updatePrinter(p.id, { location: undefined })
          )
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.deleted', 'Location deleted'));
      setDeleteConfirm(null);
    },
    onError: (error: Error) => {
      showToast(error.message || t('printers.locations.deleteError', 'Failed to delete location'), 'error');
      setDeleteConfirm(null);
    },
  });

  // Create location mutation
  const createLocationMutation = useMutation({
    mutationFn: (locationName: string) => {
      // Assign all ungrouped printers to new location
      return Promise.all(
        (printers || [])
          .filter((p: PrinterType) => !p.location)
          .map((p: PrinterType) =>
            api.updatePrinter(p.id, { location: locationName })
          )
      );
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['printers'] });
      showToast(t('printers.locations.created', 'Location created'));
      setCreateLocation(false);
      setNewLocationName('');
    },
    onError: (error: Error) => {
      showToast(error.message || t('printers.locations.createError', 'Failed to create location'), 'error');
    },
  });

  const handleCreateLocation = () => {
    if (!newLocationName.trim()) {
      showToast(t('printers.locations.nameRequired', 'Location name is required'), 'error');
      return;
    }
    // Check if location already exists
    if (locations.some(([name]) => name === newLocationName.trim())) {
      showToast(t('printers.locations.exists', 'Location already exists'), 'error');
      return;
    }
    createLocationMutation.mutate(newLocationName.trim());
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
      {filteredLocations.length === 0 ? (
        <Card>
          <CardContent className="text-center py-12 text-bambu-gray">
            {search
              ? t('printers.locations.noResults', 'No locations match your search')
              : t('printers.locations.none', 'No printer locations yet')}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {filteredLocations.map(([name, count]) => (
            <Card key={name || 'ungrouped'}>
              <CardContent className="flex items-center justify-between py-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-bambu-dark">
                    <Box className="w-[25px] h-[25px] text-bambu-gray" />
                  </div>
                  <div>
                    <p className="text-white font-medium">
                      {name || t('printers.locations.ungrouped', 'Ungrouped')}
                    </p>
                    <p className="text-sm text-bambu-gray">
                      {count} {count === 1 ? t('printers.locations.printer', 'printer') : t('printers.locations.printers', 'printers')}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setDeleteConfirm({ name, count })}
                  className="text-red-500 hover:text-red-400 hover:bg-red-500/10"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create Location Modal */}
      {createLocation && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <Card className="w-full max-w-md">
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
          isOpen={true}
          onClose={() => setDeleteConfirm(null)}
          onConfirm={() => deleteLocationMutation.mutate(deleteConfirm.name)}
          title={t('printers.locations.deleteTitle', 'Delete Location')}
          description={t('printers.locations.deleteDescription', 'Are you sure? This will remove this location from {{count}} printer(s).', {
            count: deleteConfirm.count,
          })}
          confirmText={t('printers.locations.deleteConfirm', 'Delete')}
          isDestructive={true}
          isLoading={deleteLocationMutation.isPending}
        />
      )}
    </div>
  );
}
