import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Package, Check, Search } from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool, SpoolAssignment } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';

interface AssignSpoolModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  amsId: number;
  trayId: number;
  trayInfo?: {
    type: string;
    color: string;
    location: string;
  };
}

export function AssignSpoolModal({ isOpen, onClose, printerId, amsId, trayId, trayInfo }: AssignSpoolModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [searchFilter, setSearchFilter] = useState('');

  const { data: spools, isLoading } = useQuery({
    queryKey: ['inventory-spools'],
    queryFn: () => api.getSpools(),
    enabled: isOpen,
  });

  const { data: assignments } = useQuery({
    queryKey: ['spool-assignments'],
    queryFn: () => api.getAssignments(),
    enabled: isOpen,
  });

  const assignMutation = useMutation({
    mutationFn: (spoolId: number) =>
      api.assignSpool({ spool_id: spoolId, printer_id: printerId, ams_id: amsId, tray_id: trayId }),
    onSuccess: (newAssignment) => {
      // Immediately update cache so UI reflects the new assignment without waiting for refetch
      queryClient.setQueryData<SpoolAssignment[]>(['spool-assignments'], (old) => {
        const filtered = (old || []).filter(a =>
          !(a.printer_id === printerId && a.ams_id === amsId && a.tray_id === trayId)
        );
        filtered.push(newAssignment);
        return filtered;
      });
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      showToast(t('inventory.assignSuccess'), 'success');
      onClose();
    },
    onError: (error: Error) => {
      showToast(`${t('inventory.assignFailed')}: ${error.message}`, 'error');
    },
  });

  if (!isOpen) return null;

  // Filter out spools already assigned to other slots
  const assignedSpoolIds = new Set(
    (assignments || [])
      .filter(a => !(a.printer_id === printerId && a.ams_id === amsId && a.tray_id === trayId))
      .map(a => a.spool_id)
  );
  // External slots (amsId 254 or 255) have no RFID reader, so show all spools.
  // AMS slots only show manual spools (no tag_uid or tray_uuid).
  const isExternalSlot = amsId === 254 || amsId === 255;
  const manualSpools = spools?.filter((spool: InventorySpool) =>
    !assignedSpoolIds.has(spool.id) && (isExternalSlot || (!spool.tag_uid && !spool.tray_uuid))
  );

  const filteredSpools = manualSpools?.filter((spool: InventorySpool) => {
    if (!searchFilter) return true;
    const q = searchFilter.toLowerCase();
    return (
      spool.material.toLowerCase().includes(q) ||
      (spool.brand?.toLowerCase().includes(q) ?? false) ||
      (spool.color_name?.toLowerCase().includes(q) ?? false) ||
      (spool.subtype?.toLowerCase().includes(q) ?? false)
    );
  });

  const handleAssign = () => {
    if (selectedSpoolId) {
      assignMutation.mutate(selectedSpoolId);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />

      <div className="relative w-full max-w-md mx-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <Package className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">{t('inventory.assignSpool')}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1 text-bambu-gray hover:text-white rounded transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4">
          {/* Tray info */}
          {trayInfo && (
            <div className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
              <p className="text-xs text-bambu-gray mb-1">{t('inventory.selectSpool')}:</p>
              <div className="flex items-center gap-2">
                {trayInfo.color && (
                  <span
                    className="w-4 h-4 rounded-full border border-white/20"
                    style={{ backgroundColor: `#${trayInfo.color}` }}
                  />
                )}
                <span className="text-white font-medium">{trayInfo.type || t('ams.emptySlot')}</span>
                <span className="text-bambu-gray">({trayInfo.location})</span>
              </div>
            </div>
          )}

          {/* Search filter */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
            <input
              type="text"
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              placeholder={t('inventory.searchSpools')}
              className="w-full pl-9 pr-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray focus:outline-none focus:border-bambu-green"
            />
          </div>

          {/* Spool list */}
          <div>
            {isLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : filteredSpools && filteredSpools.length > 0 ? (
              <div className="max-h-64 overflow-y-auto space-y-2">
                {filteredSpools.map((spool: InventorySpool) => (
                  <button
                    key={spool.id}
                    onClick={() => setSelectedSpoolId(spool.id)}
                    className={`w-full p-3 rounded-lg border text-left transition-colors ${
                      selectedSpoolId === spool.id
                        ? 'bg-bambu-green/20 border-bambu-green'
                        : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      {spool.rgba && (
                        <span
                          className="w-4 h-4 rounded-full border border-white/20 flex-shrink-0"
                          style={{ backgroundColor: `#${spool.rgba.substring(0, 6)}` }}
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <p className="text-white font-medium truncate">
                          {spool.brand ? `${spool.brand} ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
                        </p>
                        <p className="text-xs text-bambu-gray">
                          {spool.color_name || ''}
                          {spool.label_weight ? ` - ${spool.label_weight}g` : ''}
                          {spool.label_weight ? ` (${Math.max(0, Math.round(spool.label_weight - spool.weight_used))}g ${t('ams.remainingUnit')})` : ''}
                        </p>
                      </div>
                      {selectedSpoolId === spool.id && (
                        <Check className="w-4 h-4 text-bambu-green flex-shrink-0" />
                      )}
                    </div>
                  </button>
                ))}
              </div>
            ) : manualSpools && manualSpools.length === 0 ? (
              <div className="text-center py-8 text-bambu-gray">
                <p>{t('inventory.noManualSpools')}</p>
              </div>
            ) : (
              <div className="text-center py-8 text-bambu-gray">
                <p>{t('inventory.noSpoolsMatch')}</p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 p-4 border-t border-bambu-dark-tertiary">
          <Button variant="secondary" onClick={onClose}>
            {t('common.cancel')}
          </Button>
          <Button
            onClick={handleAssign}
            disabled={!selectedSpoolId || assignMutation.isPending}
          >
            {assignMutation.isPending ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('inventory.assigning')}
              </>
            ) : (
              <>
                <Package className="w-4 h-4" />
                {t('inventory.assignSpool')}
              </>
            )}
          </Button>
        </div>

        {assignMutation.isError && (
          <div className="mx-4 mb-4 p-2 bg-red-500/20 border border-red-500/50 rounded text-sm text-red-400">
            {(assignMutation.error as Error).message}
          </div>
        )}
      </div>
    </div>
  );
}
