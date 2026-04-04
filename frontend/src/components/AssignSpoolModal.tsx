import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, Package, Search } from 'lucide-react';
import { api } from '../api/client';
import type { InventorySpool, SpoolAssignment } from '../api/client';
import { Button } from './Button';
import { ConfirmModal } from './ConfirmModal';
import { useToast } from '../contexts/ToastContext';

interface AssignSpoolModalProps {
  isOpen: boolean;
  onClose: () => void;
  printerId: number;
  amsId: number;
  trayId: number;
  trayInfo?: {
    type: string;
    material?: string;
    profile?: string;
    color: string;
    location: string;
  };
}

export function AssignSpoolModal({ isOpen, onClose, printerId, amsId, trayId, trayInfo }: AssignSpoolModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [disableFiltering, setDisableFiltering] = useState(false);
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [searchFilter, setSearchFilter] = useState('');
  const [pendingAssignId, setPendingAssignId] = useState<number | null>(null);
  const [showMismatchConfirm, setShowMismatchConfirm] = useState(false);
  const [mismatchDetails, setMismatchDetails] = useState<{
    type: 'material' | 'partial' | 'profile' | 'material_profile' | 'partial_profile';
    spoolMaterial: string;
    trayMaterial: string;
    spoolProfile?: string;
    trayProfile?: string;
  } | null>(null);

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

  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings(),
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
      setShowMismatchConfirm(false);
      setPendingAssignId(null);
      setMismatchDetails(null);
      onClose();
    },
    onError: (error: Error) => {
      showToast(`${t('inventory.assignFailed')}: ${error.message}`, 'error');
    },
  });

  // --- Material/profile mismatch logic ---
  const normalizeValue = (value: string | undefined | null) =>
    (value ?? '').trim().toUpperCase();

  const checkMaterialMatch = (
    spoolMaterial: string | undefined | null,
    trayMaterial: string | undefined | null
  ): 'exact' | 'partial' | 'none' => {
    const normalizedSpool = normalizeValue(spoolMaterial);
    const normalizedTray = normalizeValue(trayMaterial);

    if (!normalizedSpool || !normalizedTray) return 'none';
    if (normalizedSpool === normalizedTray) return 'exact';
    if (normalizedTray.includes(normalizedSpool) || normalizedSpool.includes(normalizedTray)) {
      return 'partial';
    }

    return 'none';
  };

  const checkProfileMatch = (
    spoolProfile: string | undefined | null,
    trayProfile: string | undefined | null
  ): boolean => {
    const normalizedSpoolProfile = normalizeValue(spoolProfile);
    const normalizedTrayProfile = normalizeValue(trayProfile);

    if (!normalizedSpoolProfile || !normalizedTrayProfile) return false;

    return normalizedSpoolProfile === normalizedTrayProfile;
  };

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

  // Filtering logic with toggle: search filter always applies, AMS tray profile filter is optional
  let filteredSpools = manualSpools;
  if (!disableFiltering) {
    if (trayInfo?.profile || trayInfo?.type) {
      const trayProfile = (trayInfo.profile || trayInfo.type || '').trim().toUpperCase();
      filteredSpools = filteredSpools?.filter((spool: InventorySpool) => {
        const spoolProfile = (spool.slicer_filament_name || spool.slicer_filament || '').trim().toUpperCase();
        return trayProfile && spoolProfile && spoolProfile === trayProfile;
      });
    }
  }
  if (searchFilter && filteredSpools) {
    const q = searchFilter.toLowerCase();
    filteredSpools = filteredSpools.filter((spool: InventorySpool) => {
      return (
        spool.material.toLowerCase().includes(q) ||
        (spool.brand?.toLowerCase().includes(q) ?? false) ||
        (spool.color_name?.toLowerCase().includes(q) ?? false) ||
        (spool.subtype?.toLowerCase().includes(q) ?? false)
      );
    });
  }

  const handleAssign = () => {
    if (!selectedSpoolId) return;
    const selectedSpool = spools?.find((spool: InventorySpool) => spool.id === selectedSpoolId);
    if (!selectedSpool) {
      showToast(t('inventory.assignFailed'), 'error');
      return;
    }

    if (!settings?.disable_filament_warnings && trayInfo) {
      const trayMaterial = trayInfo.material || trayInfo.type;
      const materialMatchResult = checkMaterialMatch(selectedSpool.material, trayMaterial);
      const spoolProfile = selectedSpool.slicer_filament_name || selectedSpool.slicer_filament;
      const trayProfile = trayInfo.profile || trayInfo.type;
      const profileMatches = checkProfileMatch(spoolProfile, trayProfile);

      // Always evaluate both checks; if both fail, show a combined warning.
      if (materialMatchResult !== 'exact' || !profileMatches) {
        let mismatchType: 'material' | 'partial' | 'profile' | 'material_profile' | 'partial_profile' = 'profile';

        if (materialMatchResult === 'none' && !profileMatches) {
          mismatchType = 'material_profile';
        } else if (materialMatchResult === 'partial' && !profileMatches) {
          mismatchType = 'partial_profile';
        } else if (materialMatchResult === 'none') {
          mismatchType = 'material';
        } else if (materialMatchResult === 'partial') {
          mismatchType = 'partial';
        }

        setPendingAssignId(selectedSpoolId);
        setMismatchDetails({
          type: mismatchType,
          spoolMaterial: selectedSpool.material || '',
          trayMaterial: trayMaterial || '',
          spoolProfile: spoolProfile || undefined,
          trayProfile: trayProfile || undefined,
        });
        setShowMismatchConfirm(true);
        return;
      }
    }
    assignMutation.mutate(selectedSpoolId);
  };

  const handleConfirmMismatch = () => {
    if (!pendingAssignId) return;
    assignMutation.mutate(pendingAssignId);
    setShowMismatchConfirm(false);
    setPendingAssignId(null);
  };

  return (
    <>
      <div className="fixed inset-0 z-50 flex items-start sm:items-center justify-center p-4 overflow-y-auto">
        <div
          className="absolute inset-0 bg-black/60 backdrop-blur-sm"
          onClick={onClose}
        />

      <div className="relative w-full max-w-2xl bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] overflow-hidden flex flex-col my-auto">
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
        <div className="p-4 space-y-4 overflow-y-auto">
          {/* Tray info */}
          {trayInfo && (
            <div className="p-3 bg-bambu-dark rounded-lg border border-bambu-dark-tertiary">
              <p className="text-xs text-bambu-gray mb-1">{t('inventory.selectSpool')}:</p>
              <div className="flex items-center gap-2">
                {trayInfo.color && (
                  <span
                    className="w-4 h-4 rounded-full border border-black/20"
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
              <div className="max-h-96 overflow-y-auto grid grid-cols-2 sm:grid-cols-3 gap-2">
                {filteredSpools.map((spool: InventorySpool) => (
                  <button
                    key={spool.id}
                    onClick={() => setSelectedSpoolId(spool.id)}
                    title={spool.note || undefined}
                    className={`p-2.5 rounded-lg border text-left transition-colors ${
                      selectedSpoolId === spool.id
                        ? 'bg-bambu-green/20 border-bambu-green'
                        : 'bg-bambu-dark border-bambu-dark-tertiary hover:border-bambu-gray'
                    }`}
                  >
                    <p className="text-white text-sm font-medium truncate">
                      {spool.brand ? `${spool.brand} ` : ''}{spool.material}{spool.subtype ? ` ${spool.subtype}` : ''}
                    </p>
                    <div className="flex items-center gap-1.5 mt-1">
                      {spool.rgba && (
                        <span
                          className="w-3 h-3 rounded-full border border-black/20 flex-shrink-0"
                          style={{ backgroundColor: `#${spool.rgba.substring(0, 6)}` }}
                        />
                      )}
                      <span className="text-xs text-bambu-gray truncate">{spool.color_name || ''}</span>
                    </div>
                    {spool.label_weight && (
                      <p className="text-xs text-bambu-gray mt-1">
                        {Math.max(0, Math.round(spool.label_weight - spool.weight_used))} / {spool.label_weight}g
                      </p>
                    )}
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

        {/* Footer with filtering toggle */}
        <div className="flex justify-between items-center p-4 border-t border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <input
              id="disable-filtering-toggle"
              type="checkbox"
              checked={disableFiltering}
              onChange={() => setDisableFiltering(v => !v)}
              className="accent-bambu-green w-4 h-4 rounded focus:ring-0 border-bambu-dark-tertiary"
            />
            <label htmlFor="disable-filtering-toggle" className="text-xs text-bambu-gray select-none cursor-pointer">
              {t('inventory.showAllSpools')}
            </label>
          </div>
          <div className="flex gap-2">
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
        </div>


        {assignMutation.isError && (
          <div className="mx-4 mb-4 p-2 bg-red-500/20 border border-red-500/50 rounded text-sm text-red-400">
            {(assignMutation.error as Error).message}
          </div>
        )}

      </div>
      </div>

      {showMismatchConfirm && trayInfo && selectedSpoolId && mismatchDetails && (() => {
        let message = '';

        if (mismatchDetails.type === 'material') {
          message = t('inventory.assignMismatchMessage', {
            spoolMaterial: mismatchDetails.spoolMaterial,
            trayMaterial: mismatchDetails.trayMaterial,
            location: trayInfo.location,
          });
        } else if (mismatchDetails.type === 'partial') {
          message = t('inventory.assignPartialMismatchMessage', {
            spoolMaterial: mismatchDetails.spoolMaterial,
            trayMaterial: mismatchDetails.trayMaterial,
            location: trayInfo.location,
          });
        } else if (mismatchDetails.type === 'material_profile') {
          message = `${t('inventory.assignMismatchMessage', {
            spoolMaterial: mismatchDetails.spoolMaterial,
            trayMaterial: mismatchDetails.trayMaterial,
            location: trayInfo.location,
          })}\n\n${t('inventory.assignProfileMismatchMessage', {
            spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
            trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
            location: trayInfo.location,
          })}`;
        } else if (mismatchDetails.type === 'partial_profile') {
          message = `${t('inventory.assignPartialMismatchMessage', {
            spoolMaterial: mismatchDetails.spoolMaterial,
            trayMaterial: mismatchDetails.trayMaterial,
            location: trayInfo.location,
          })}\n\n${t('inventory.assignProfileMismatchMessage', {
            spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
            trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
            location: trayInfo.location,
          })}`;
        } else if (mismatchDetails.type === 'profile') {
          message = t('inventory.assignProfileMismatchMessage', {
            spoolProfile: mismatchDetails.spoolProfile || t('common.unknown'),
            trayProfile: mismatchDetails.trayProfile || t('common.unknown'),
            location: trayInfo.location,
          });
        }

        return (
          <ConfirmModal
            title={t('inventory.assignMismatchTitle')}
            message={message}
            confirmText={t('inventory.assignMismatchConfirm')}
            variant="warning"
            isLoading={assignMutation.isPending}
            onConfirm={handleConfirmMismatch}
            onCancel={() => {
              if (!assignMutation.isPending) {
                setShowMismatchConfirm(false);
                setPendingAssignId(null);
                setMismatchDetails(null);
              }
            }}
          />
        );
      })()}
    </>
  );
}
