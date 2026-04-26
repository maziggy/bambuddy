import { useEffect, useState } from 'react';
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
  spoolmanEnabled?: boolean;
}

export function AssignSpoolModal({ isOpen, onClose, printerId, amsId, trayId, trayInfo, spoolmanEnabled }: AssignSpoolModalProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [disableFiltering, setDisableFiltering] = useState(false);
  const [selectedSpoolId, setSelectedSpoolId] = useState<number | null>(null);
  const [selectedSpoolmanSpoolId, setSelectedSpoolmanSpoolId] = useState<number | null>(null);
  useEffect(() => {
    setSelectedSpoolId(null);
    setSelectedSpoolmanSpoolId(null);
  }, [disableFiltering]);
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

  useEffect(() => {
    if (isOpen) {
      setDisableFiltering(false);
    }
  }, [isOpen]);

  // Unique cache key — different consumers of `['inventory-spools']` call
  // `getSpools()` with different `includeArchived` arguments (InventoryPage:
  // true, SpoolBuddyDashboard / SpoolBuddyInventoryPage: false), but they
  // all share the same key. React Query treats them as one query and
  // serves whichever response landed first, so a SpoolBuddy component
  // priming the cache with the archived-excluded payload makes the picker
  // miss spools that *are* archived OR (more subtly) miss any spool that
  // wasn't yet present when SpoolBuddy ran its initial fetch. The picker
  // gets its own key + a fetch-everything call so this consumer is never
  // at the mercy of someone else's cache state. Archived spools are then
  // explicitly excluded client-side because the backend rejects archived
  // assignments with HTTP 400 anyway, so listing them would only let the
  // user click a button that fails.
  const { data: spools, isLoading } = useQuery({
    queryKey: ['inventory-spools', 'assign-modal'],
    queryFn: () => api.getSpools(true),
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

  const { data: spoolmanSpools, isLoading: spoolmanLoading } = useQuery({
    queryKey: ['spoolman-inventory-spools', 'assign-modal'],
    queryFn: () => api.getSpoolmanInventorySpools(false),
    enabled: isOpen && !!spoolmanEnabled,
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

  const assignSpoolmanMutation = useMutation({
    mutationFn: (spoolmanSpoolId: number) =>
      api.assignSpoolmanSlot({
        spoolman_spool_id: spoolmanSpoolId,
        printer_id: printerId,
        ams_id: amsId,
        tray_id: trayId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['spoolman-inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      showToast(t('inventory.assignSuccess'), 'success');
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

  // Bambu Studio / OrcaSlicer profile names carry a printer/nozzle/variant qualifier after
  // `@` (e.g. "Devil Design PLA Basic @Bambu Lab H2D 0.4 nozzle (Custom)"), while the tray's
  // profile is typically the bare base name. Strip the qualifier before comparing so identical
  // base profiles don't trigger a mismatch warning (#1047).
  const stripProfileQualifier = (value: string) => value.split('@')[0].trim();

  const checkProfileMatch = (
    spoolProfile: string | undefined | null,
    trayProfile: string | undefined | null
  ): boolean => {
    const normalizedSpoolProfile = stripProfileQualifier(normalizeValue(spoolProfile));
    const normalizedTrayProfile = stripProfileQualifier(normalizeValue(trayProfile));

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
  // Show every spool that isn't already taken by another slot — including
  // RFID-tagged Bambu Lab spools (#1133). The earlier "manual spools only"
  // gate (tag_uid && tray_uuid both null) blocked the workflow where a
  // user has a Bambu Lab spool in inventory but doesn't want to scan it
  // via SpoolBuddy NFC every time and just wants to pick it from the list.
  // External slots (amsId 254/255) have always been allowed to pick from
  // any spool because the slot itself has no RFID reader; that
  // distinction collapses now that AMS slots also accept any spool.
  //
  // The "Show all spools" toggle (disableFiltering) bypasses BOTH this
  // gate and the material/profile filter below, making it a real escape
  // hatch for cases where MQTT has auto-reassigned a spool to another
  // slot a fraction of a second after a manual unassign — without this,
  // the toggle's label is a lie ("Show all" but actually filters by
  // assignment). The backend's assign_spool route is upsert-per-
  // (printer, ams, tray), so picking a spool that's currently taken by
  // a different slot creates a second assignment row; that's a foot-gun
  // for normal flows but exactly the recovery path the toggle is for.
  const availableSpools = spools?.filter((spool: InventorySpool) =>
    !spool.archived_at &&
    (disableFiltering || !assignedSpoolIds.has(spool.id))
  );

  // Filtering logic with toggle: search filter always applies, AMS tray profile filter is optional.
  // Show a spool if EITHER the slicer profile matches exactly OR the material overlaps with the
  // tray's material (partial-match both directions — "PLA" spool accepts a "PLA Basic" slot and
  // vice versa). Manually-added inventory spools typically have no slicer_filament_name; gating
  // on strict profile equality alone hid them even when the material matched (#1047).
  let filteredSpools = availableSpools;
  if (!disableFiltering) {
    const trayProfile = stripProfileQualifier(normalizeValue(trayInfo?.profile));
    const trayMaterial = normalizeValue(trayInfo?.material || trayInfo?.type);
    if (trayProfile || trayMaterial) {
      filteredSpools = filteredSpools?.filter((spool: InventorySpool) => {
        const spoolProfile = stripProfileQualifier(normalizeValue(spool.slicer_filament_name || spool.slicer_filament));
        const spoolMaterial = normalizeValue(spool.material);
        if (trayProfile && spoolProfile && spoolProfile === trayProfile) return true;
        if (trayMaterial && spoolMaterial) {
          return (
            spoolMaterial === trayMaterial ||
            trayMaterial.includes(spoolMaterial) ||
            spoolMaterial.includes(trayMaterial)
          );
        }
        // Neither side has filterable info on whatever dimension remains — show it.
        return !spoolProfile && !spoolMaterial;
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
    if (selectedSpoolmanSpoolId !== null) {
      assignSpoolmanMutation.mutate(selectedSpoolmanSpoolId);
      return;
    }
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
          <div className="space-y-3">
            {isLoading ? (
              <div className="flex justify-center py-8">
                <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
              </div>
            ) : filteredSpools && filteredSpools.length > 0 ? (
              <div className="max-h-96 overflow-y-auto grid grid-cols-2 sm:grid-cols-3 gap-2">
                {filteredSpools.map((spool: InventorySpool) => (
                  <button
                    key={spool.id}
                    onClick={() => { setSelectedSpoolId(spool.id); setSelectedSpoolmanSpoolId(null); }}
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
            ) : availableSpools && availableSpools.length === 0 ? (
              <div className="text-center py-8 text-bambu-gray">
                <p>{t('inventory.noAvailableSpools')}</p>
                {/* Diagnostic counter — when the picker is empty, having
                    the raw fetch / filter counts visible makes a
                    "spool I expected to see is missing" report
                    immediately answerable: if `total fetched` is 0 the
                    backend / cache returned nothing; if it's > 0 then
                    the archived / assigned-elsewhere filter ate the
                    spool and the toggle is the right escape hatch. */}
                {spools && (
                  <p className="text-[10px] mt-2 opacity-60">
                    {spools.length} fetched · {spools.filter(s => s.archived_at).length} archived ·{' '}
                    {spools.filter(s => assignedSpoolIds.has(s.id)).length} assigned to other slots
                  </p>
                )}
              </div>
            ) : (
              <div className="text-center py-8 text-bambu-gray">
                <p>{t('inventory.noSpoolsMatch')}</p>
                {availableSpools && (
                  <p className="text-[10px] mt-2 opacity-60">
                    {availableSpools.length} unassigned spools — {(availableSpools.length) - (filteredSpools?.length ?? 0)} filtered by tray match. Try "Show all spools".
                  </p>
                )}
              </div>
            )}

            {spoolmanEnabled && (
              <>
                {spoolmanLoading ? (
                  <div className="flex justify-center py-4">
                    <Loader2 className="w-5 h-5 text-bambu-green animate-spin" />
                  </div>
                ) : spoolmanSpools && spoolmanSpools.filter(s => !s.archived_at).length > 0 ? (
                  <>
                    <p className="text-xs font-medium text-bambu-gray uppercase tracking-wide pt-1">
                      {t('inventory.spoolmanSpools')}
                    </p>
                    <div className="max-h-64 overflow-y-auto grid grid-cols-2 sm:grid-cols-3 gap-2">
                      {spoolmanSpools
                        .filter(s => !s.archived_at)
                        .filter(s => {
                          if (!searchFilter) return true;
                          const q = searchFilter.toLowerCase();
                          return (
                            s.material.toLowerCase().includes(q) ||
                            (s.brand?.toLowerCase().includes(q) ?? false) ||
                            (s.color_name?.toLowerCase().includes(q) ?? false) ||
                            (s.subtype?.toLowerCase().includes(q) ?? false)
                          );
                        })
                        .map((spool: InventorySpool) => (
                          <button
                            key={`spoolman-${spool.id}`}
                            onClick={() => {
                              setSelectedSpoolmanSpoolId(spool.id);
                              setSelectedSpoolId(null);
                            }}
                            title={spool.note || undefined}
                            className={`p-2.5 rounded-lg border text-left transition-colors ${
                              selectedSpoolmanSpoolId === spool.id
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
                  </>
                ) : null}
              </>
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
              disabled={(!selectedSpoolId && selectedSpoolmanSpoolId === null) || assignMutation.isPending || assignSpoolmanMutation.isPending}
            >
              {(assignMutation.isPending || assignSpoolmanMutation.isPending) ? (
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
