import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, QrCode, Cpu, MapPin, ArrowLeft } from 'lucide-react';
import { api, ApiError, type AMSTray } from '../api/client';
import { Button } from './Button';
import { MaterialMismatchConfirm } from './MaterialMismatchConfirm';
import { QrSpoolScanner } from './QrSpoolScanner';
import { useToast } from '../contexts/ToastContext';
import { useUiSettings } from '../hooks/useUiSettings';
import { formatSlotLabel, getEmptySlotKind } from '../utils/amsHelpers';
import { findMaterialMismatch, type MaterialMismatch } from '../utils/assignMismatch';
import { getSwatchStyle } from '../utils/colors';
import { inventoryLocationsQueryKey, invalidateInventoryLocations } from '../utils/inventoryQueries';
import { parseSpoolQr, scannedSpoolId, type AssignTarget } from '../utils/qrAssignTarget';

type AmsTarget = Extract<AssignTarget, { kind: 'ams' }>;

interface QrAssignTargetModalProps {
  isOpen: boolean;
  onClose: () => void;
  spoolmanMode?: boolean;
}

type SlotOption = {
  amsId: number;
  trayId: number;
  isHt: boolean;
  isExternal: boolean;
  label: string;
  /** What the printer reports in the slot, for its tile. */
  tray?: AMSTray;
};

/**
 * What a slot's tile says is in it, by the printer card's rules
 * (getEmptySlotKind): filament it reports, "?" for a spool it can't identify,
 * and Empty only where it says no spool is there. A missing tray_type alone is
 * not empty — a spool without RFID reads that way (#2527).
 */
function SlotContents({ tray }: { tray?: AMSTray }) {
  const { t } = useTranslation();
  const kind = getEmptySlotKind(tray);
  if (kind === 'physical') return <span className="text-[10px] font-normal text-bambu-gray">{t('ams.slotEmpty')}</span>;
  if (kind === 'reset' || !tray) return <span className="text-xs font-normal text-bambu-gray">{t('ams.slotUnconfigured')}</span>;
  return (
    <span className="flex flex-col items-center gap-0.5 max-w-full">
      <span className="flex items-center gap-1 max-w-full">
        {tray.tray_color && (
          <span className="w-2.5 h-2.5 shrink-0 rounded-full border border-bambu-gray/50" style={getSwatchStyle(tray.tray_color)} />
        )}
        <span className="text-[10px] font-normal text-white truncate">{tray.tray_type}</span>
      </span>
      {/* Spaces keep the button's accessible name readable ("A1 PLA 80%"). */}
      {tray.remain >= 0 && <> <span className="text-[10px] font-normal text-bambu-gray">{tray.remain}%</span></>}
    </span>
  );
}

const TABS = [
  { key: 'ams' as const, Icon: Cpu, labelKey: 'inventory.qrAssign.tabAms' },
  { key: 'storage' as const, Icon: MapPin, labelKey: 'inventory.qrAssign.tabStorage' },
];

function targetLocationLabel(target: AssignTarget): string {
  return target.kind === 'ams'
    ? target.printerName
      ? `${target.printerName} · ${target.label}`
      : target.label
    : target.locationName;
}

function externalLabel(t: (k: string) => string, vtCount: number, trayId: number): string {
  // Match PrintersPage: Ext-L / Ext-R when two external slots, "External" otherwise.
  if (vtCount > 1) return t(trayId === 0 ? 'printers.extL' : 'printers.extR');
  return t('printers.external');
}

export function QrAssignTargetModal({ isOpen, onClose, spoolmanMode = false }: QrAssignTargetModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [step, setStep] = useState<'target' | 'scan'>('target');
  const [tab, setTab] = useState<'ams' | 'storage'>('ams');
  const [pickedPrinterId, setPickedPrinterId] = useState<number | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<SlotOption | null>(null);
  // Name is kept next to the id so the target survives a locations refetch that
  // hasn't landed yet (notably right after creating one inline).
  const [pickedLocation, setPickedLocation] = useState<{ id: number; name: string } | null>(null);
  const [newLocationName, setNewLocationName] = useState('');
  const [creatingLocation, setCreatingLocation] = useState(false);
  const [target, setTarget] = useState<AssignTarget | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Looking the scanned spool up before an AMS assign, and a material mismatch
  // waiting on the confirm — as in the Assign Spool dialog.
  const [checking, setChecking] = useState(false);
  const [mismatch, setMismatch] = useState<{ spoolId: number; tg: AmsTarget; details: MaterialMismatch } | null>(null);
  // Synchronous in-flight guard: mutation.isPending only updates next render.
  const inFlightRef = useRef(false);
  // Counts the dialog's closings and openings: a spool lookup still running
  // when the dialog closed (Cancel, X, the backdrop) must not go on to assign
  // the spool.
  const openingRef = useRef(0);

  useEffect(() => {
    if (isOpen) {
      setStep('target');
      setSelectedSlot(null);
      setPickedLocation(null);
      setNewLocationName('');
      setCreatingLocation(false);
      setTarget(null);
      setError(null);
      setChecking(false);
      setMismatch(null);
      inFlightRef.current = false;
    }
    return () => {
      openingRef.current += 1;
    };
  }, [isOpen]);

  const { data: settings } = useUiSettings({ enabled: isOpen });

  const { data: printers, isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen,
  });

  // Structured Locations are the canonical storage model, so the storage tab is
  // a picker over the same list the inventory Locations filter and LocationsModal
  // use — a QR assignment lands in `location_id`, not the legacy free-text field.
  const { data: locations = [], isLoading: locationsLoading } = useQuery({
    queryKey: inventoryLocationsQueryKey,
    queryFn: api.getLocations,
    enabled: isOpen && step === 'target',
  });

  const effectivePrinterId = pickedPrinterId ?? printers?.[0]?.id ?? null;

  const { data: status, isLoading: statusLoading } = useQuery({
    queryKey: ['printerStatus', effectivePrinterId],
    queryFn: () => api.getPrinterStatus(effectivePrinterId!),
    enabled: isOpen && step === 'target' && tab === 'ams' && effectivePrinterId !== null,
  });

  const slots = useMemo<SlotOption[]>(() => {
    if (!status) return [];
    const out: SlotOption[] = [];
    for (const ams of status.ams ?? []) {
      for (const tray of ams.tray ?? []) {
        out.push({
          amsId: ams.id,
          trayId: tray.id,
          isHt: ams.is_ams_ht,
          isExternal: false,
          label: formatSlotLabel(ams.id, tray.id, ams.is_ams_ht, false),
          tray,
        });
      }
    }
    // External (vt_tray) slots: backend keys them under ams_id 255 with
    // tray_id = id - 254 (mirrors PrintersPage).
    const externals = status.vt_tray ?? [];
    for (const vt of externals) {
      const trayId = (vt.id ?? 254) - 254;
      out.push({ amsId: 255, trayId, isHt: false, isExternal: true, label: externalLabel(t, externals.length, trayId), tray: vt });
    }
    return out;
  }, [status, t]);

  const assignMutation = useMutation({
    mutationFn: async ({ spoolId, tg }: { spoolId: number; tg: AssignTarget }): Promise<{ pendingConfig: boolean }> => {
      if (tg.kind === 'ams') {
        // A spool lives in one slot at a time. `move` has the backend take it
        // out of its other slot(s) in the same transaction as the assign, so a
        // failure leaves it where it was rather than in no slot.
        if (spoolmanMode) {
          await api.assignSpoolmanSlot({ spoolman_spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId, move: true });
          return { pendingConfig: false };
        }
        const assignment = await api.assignSpool({ spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId, move: true });
        return { pendingConfig: !!assignment.pending_config };
      }
      if (spoolmanMode) {
        await api.updateSpoolmanInventorySpool(spoolId, { location_id: tg.locationId });
      } else {
        await api.updateSpool(spoolId, { location_id: tg.locationId });
      }
      return { pendingConfig: false };
    },
    onSuccess: ({ pendingConfig }, { tg }) => {
      // Intentionally do NOT clear inFlightRef here: the modal closes right
      // after, and leaving it set blocks any stale in-flight live-scan decode
      // from firing a second assignment in the brief window before unmount.
      // It's reset wholesale when the modal next opens (and in onError for retry).
      //
      // A slot that read empty at assign time is configured when the spool goes
      // in (#1680); "Assigned" would read as if the slot were set up already.
      showToast(
        pendingConfig
          ? t('inventory.assignPendingInsert')
          : t(tg.kind === 'ams' ? 'inventory.qrAssign.assignSuccess' : 'inventory.qrAssign.storageSuccess', { location: targetLocationLabel(tg) }),
        'success',
      );
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      queryClient.invalidateQueries({ queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'] });
      // Prefix match: the per-printer lists and the all-printers one.
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments'] });
      if (tg.kind === 'ams') {
        // #1414 parity: nudge the printer to republish so its card doesn't show
        // stale tray state until the next poll. Best-effort.
        api.refreshPrinterStatus(tg.printerId).catch(() => {});
        queryClient.invalidateQueries({ queryKey: ['printerStatus', tg.printerId] });
        // A Spoolman move clears the fallback tag linking the spool to its old slot.
        if (spoolmanMode) queryClient.invalidateQueries({ queryKey: ['linked-spools'] });
      } else {
        // The move changes per-location spool_count, which the Locations UI shows.
        invalidateInventoryLocations(queryClient);
      }
      onClose();
    },
    onError: (error: Error, { tg }) => {
      inFlightRef.current = false;
      // Said in the dialog, which stays open: a toast sits under its backdrop,
      // the SpoolBuddy kiosk shows none, and the scanner doesn't send a label
      // again while it stays in view.
      setError(`${t('inventory.qrAssign.assignFailed', { location: targetLocationLabel(tg) })}: ${error.message}`);
    },
  });

  if (!isOpen) return null;

  const printerName = printers?.find((p) => p.id === effectivePrinterId)?.name ?? '';
  const canStart = tab === 'ams' ? selectedSlot !== null && effectivePrinterId !== null : pickedLocation !== null;
  const isBusy = assignMutation.isPending || checking || mismatch !== null;

  // A freshly created location may not be in the fetched list yet, so make sure
  // the current pick is always offered.
  const locationOptions =
    pickedLocation && !locations.some((l) => l.id === pickedLocation.id)
      ? [...locations, pickedLocation]
      : locations;

  // Create-and-pick, so the user doesn't have to leave the scan flow to add a
  // shelf. Mirrors the spool form's inline location creation.
  const handleCreateLocation = async () => {
    const name = newLocationName.trim();
    if (!name || creatingLocation) return;
    setCreatingLocation(true);
    try {
      const created = await api.createLocation({ name });
      invalidateInventoryLocations(queryClient);
      setPickedLocation({ id: created.id, name: created.name });
      setNewLocationName('');
    } catch (err) {
      showToast(err instanceof Error ? err.message : t('locations.saveFailed'), 'error');
    } finally {
      setCreatingLocation(false);
    }
  };

  const handleStart = () => {
    let tg: AssignTarget;
    if (tab === 'ams') {
      if (!selectedSlot || effectivePrinterId === null) return;
      tg = {
        kind: 'ams',
        printerId: effectivePrinterId,
        printerName,
        amsId: selectedSlot.amsId,
        trayId: selectedSlot.trayId,
        isExternal: selectedSlot.isExternal,
        label: selectedSlot.label,
        filament: {
          type: selectedSlot.tray?.tray_type || '',
          profile: selectedSlot.tray?.tray_sub_brands || undefined,
        },
      };
    } else {
      if (!pickedLocation) return;
      tg = { kind: 'storage', locationId: pickedLocation.id, locationName: pickedLocation.name };
    }
    setTarget(tg);
    setError(null);
    setStep('scan');
  };

  // A built-in spool going into an AMS slot is looked up first, as the Assign
  // Spool dialog has it at hand: a missing or archived spool says so, and one
  // whose material doesn't fit the slot asks before it is assigned. Spoolman
  // spools go straight to the backend, which checks them, as in that dialog.
  const checkAndAssign = async (spoolId: number, tg: AmsTarget) => {
    const opening = openingRef.current;
    setChecking(true);
    try {
      const spool = await api.getSpool(spoolId);
      if (openingRef.current !== opening) return; // the dialog was closed meanwhile
      if (spool.archived_at) {
        setError(t('inventory.spoolIdArchived', { id: spoolId }));
        inFlightRef.current = false;
        return;
      }
      const details = settings?.disable_filament_warnings ? null : findMaterialMismatch(spool, tg.filament);
      if (details) {
        setMismatch({ spoolId, tg, details }); // still in flight until confirmed or cancelled
        return;
      }
      assignMutation.mutate({ spoolId, tg });
    } catch (err) {
      if (openingRef.current !== opening) return;
      setError(
        err instanceof ApiError && err.status === 404
          ? t('inventory.spoolIdNotFound', { id: spoolId })
          : err instanceof Error
            ? err.message
            : t('inventory.assignFailed'),
      );
      inFlightRef.current = false;
    } finally {
      setChecking(false);
    }
  };

  // Shared by the live scanner and the photo fallback: validate the decoded QR
  // and fire the assignment (guarded so one scan can't assign twice).
  const handleDecode = (text: string) => {
    if (inFlightRef.current || isBusy || !target) return;
    const scanned = parseSpoolQr(text);
    if (!scanned) {
      setError(t('inventory.qrAssign.invalidQr'));
      return;
    }
    const id = scannedSpoolId(scanned, spoolmanMode);
    if (id === null) {
      setError(t('inventory.qrAssign.spoolmanLabel'));
      return;
    }
    setError(null);
    inFlightRef.current = true;
    if (target.kind === 'ams' && !spoolmanMode) {
      void checkAndAssign(id, target);
      return;
    }
    assignMutation.mutate({ spoolId: id, tg: target });
  };

  const confirmMismatch = () => {
    if (!mismatch) return;
    setMismatch(null);
    assignMutation.mutate({ spoolId: mismatch.spoolId, tg: mismatch.tg });
  };

  // The scanner keeps this label from coming straight back; another label, a
  // photo or Retry starts over.
  const cancelMismatch = () => {
    setMismatch(null);
    inFlightRef.current = false;
  };

  return (
    <>
    <div className="fixed inset-0 z-[100] flex items-start sm:items-center justify-center p-4 overflow-y-auto">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      <div className="relative w-full max-w-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-xl shadow-2xl max-h-[90vh] overflow-hidden flex flex-col my-auto">
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-bambu-dark-tertiary">
          <div className="flex items-center gap-2">
            <QrCode className="w-5 h-5 text-bambu-green" />
            <h2 className="text-lg font-semibold text-white">
              {step === 'target' ? t('inventory.qrAssign.title') : t('inventory.qrAssign.scanTitle')}
            </h2>
          </div>
          <button onClick={onClose} className="p-1 text-bambu-gray hover:text-white rounded transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 space-y-4 overflow-y-auto">
          {step === 'target' ? (
            <>
              <p className="text-sm text-bambu-gray">{t('inventory.qrAssign.description')}</p>

              {/* Tabs */}
              <div className="flex gap-2">
                {TABS.map(({ key, Icon, labelKey }) => (
                  <button
                    key={key}
                    onClick={() => setTab(key)}
                    className={`flex-1 flex items-center justify-center gap-2 py-2 rounded-lg text-sm font-medium border transition-colors ${
                      tab === key
                        ? 'bg-bambu-green/20 border-bambu-green text-bambu-green'
                        : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-gray'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    {t(labelKey)}
                  </button>
                ))}
              </div>

              {tab === 'ams' ? (
                <div className="space-y-3">
                  <div>
                    <label htmlFor="qr-printer-select" className="block text-xs font-medium text-bambu-gray uppercase tracking-wide mb-1">
                      {t('inventory.qrAssign.printer')}
                    </label>
                    <select
                      id="qr-printer-select"
                      value={effectivePrinterId ?? ''}
                      onChange={(e) => {
                        setPickedPrinterId(Number(e.target.value));
                        setSelectedSlot(null);
                      }}
                      disabled={printersLoading || !printers?.length}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                    >
                      {printers?.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <span className="block text-xs font-medium text-bambu-gray uppercase tracking-wide mb-1">
                      {t('inventory.qrAssign.selectSlot')}
                    </span>
                    {statusLoading ? (
                      <div className="flex justify-center py-6">
                        <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
                      </div>
                    ) : slots.length > 0 ? (
                      <div className="grid grid-cols-4 gap-2">
                        {slots.map((slot) => {
                          const isSel = selectedSlot?.amsId === slot.amsId && selectedSlot?.trayId === slot.trayId;
                          return (
                            <button
                              key={`${slot.amsId}-${slot.trayId}`}
                              onClick={() => setSelectedSlot(slot)}
                              className={`aspect-square min-w-0 px-1 flex flex-col items-center justify-center gap-1 rounded-lg border text-sm font-semibold transition-colors ${
                                isSel
                                  ? 'bg-bambu-green/20 border-bambu-green text-white'
                                  : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-gray'
                              }`}
                            >
                              <span>{slot.label}</span> <SlotContents tray={slot.tray} />
                            </button>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-center py-6 text-sm text-bambu-gray">{t('inventory.qrAssign.noSlots')}</p>
                    )}
                  </div>
                </div>
              ) : (
                <div>
                  <label htmlFor="qr-storage-select" className="block text-xs font-medium text-bambu-gray uppercase tracking-wide mb-1">
                    {t('inventory.qrAssign.storageLabel')}
                  </label>
                  {locationsLoading ? (
                    <div className="flex justify-center py-6">
                      <Loader2 className="w-6 h-6 text-bambu-green animate-spin" />
                    </div>
                  ) : (
                    <select
                      id="qr-storage-select"
                      value={pickedLocation?.id ?? ''}
                      onChange={(e) => {
                        const id = e.target.value ? Number(e.target.value) : null;
                        const match = id === null ? null : locationOptions.find((l) => l.id === id);
                        setPickedLocation(match ? { id: match.id, name: match.name } : null);
                      }}
                      className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm focus:outline-none focus:border-bambu-green"
                    >
                      <option value="">{t('inventory.qrAssign.storagePick')}</option>
                      {locationOptions.map((loc) => (
                        <option key={loc.id} value={loc.id}>
                          {loc.name}
                        </option>
                      ))}
                    </select>
                  )}

                  {!locationsLoading && locationOptions.length === 0 && (
                    <p className="mt-2 text-xs text-bambu-gray">{t('locations.empty')}</p>
                  )}

                  <div className="mt-2 flex gap-2">
                    <input
                      type="text"
                      maxLength={255}
                      value={newLocationName}
                      onChange={(e) => setNewLocationName(e.target.value)}
                      placeholder={t('locations.createPlaceholder')}
                      className="flex-1 px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                    />
                    <button
                      type="button"
                      onClick={handleCreateLocation}
                      disabled={!newLocationName.trim() || creatingLocation}
                      className="px-3 py-2 text-sm rounded-lg bg-bambu-dark-tertiary text-white hover:bg-bambu-gray-dark disabled:opacity-50"
                    >
                      {creatingLocation ? <Loader2 className="w-4 h-4 animate-spin" /> : t('locations.addShort')}
                    </button>
                  </div>
                </div>
              )}
            </>
          ) : (
            <QrSpoolScanner
              onDecode={handleDecode}
              busy={isBusy}
              error={error}
              targetLabel={target ? targetLocationLabel(target) : undefined}
            />
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between gap-2 p-4 border-t border-bambu-dark-tertiary">
          {step === 'scan' ? (
            <Button variant="secondary" onClick={() => setStep('target')} disabled={isBusy}>
              <ArrowLeft className="w-4 h-4" />
              {t('inventory.qrAssign.back')}
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            {step === 'target' && (
              <Button onClick={handleStart} disabled={!canStart}>
                <QrCode className="w-4 h-4" />
                {t('inventory.qrAssign.startScan')}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>

    {mismatch && (
      <MaterialMismatchConfirm
        mismatch={mismatch.details}
        location={targetLocationLabel(mismatch.tg)}
        onConfirm={confirmMismatch}
        onCancel={cancelMismatch}
      />
    )}
    </>
  );
}
