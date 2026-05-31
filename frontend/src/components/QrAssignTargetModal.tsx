import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, QrCode, Cpu, MapPin, ArrowLeft, CameraOff } from 'lucide-react';
import jsQR from 'jsqr';
import { api } from '../api/client';
import { Button } from './Button';
import { useToast } from '../contexts/ToastContext';
import { formatSlotLabel } from '../utils/amsHelpers';
import { parseSpoolIdFromQr, type AssignTarget } from '../utils/qrAssignTarget';

interface QrAssignTargetModalProps {
  isOpen: boolean;
  onClose: () => void;
  spoolmanMode?: boolean;
  /** Existing storage_location values, offered as autocomplete suggestions. */
  storageSuggestions?: string[];
}

type SlotOption = {
  amsId: number;
  trayId: number;
  isHt: boolean;
  isExternal: boolean;
  label: string;
};

type CameraReason = 'denied' | 'insecure' | 'nodevice';

const TABS = [
  { key: 'ams' as const, Icon: Cpu, labelKey: 'inventory.qrAssign.tabAms' },
  { key: 'storage' as const, Icon: MapPin, labelKey: 'inventory.qrAssign.tabStorage' },
];

function targetLocationLabel(target: AssignTarget): string {
  return target.kind === 'ams'
    ? target.printerName
      ? `${target.printerName} · ${target.label}`
      : target.label
    : target.storageLocation;
}

/**
 * Live camera QR scanner. Runs a requestAnimationFrame loop that decodes the
 * video frame with jsQR and emits the payload (throttled). Callbacks are read
 * through refs so the camera starts exactly once and isn't torn down when the
 * parent re-renders. `paused` halts decoding (e.g. while an assign is in flight)
 * without stopping the stream.
 */
function QrScannerView({
  onDecode,
  onError,
  paused,
}: {
  onDecode: (text: string) => void;
  onError: (reason: CameraReason) => void;
  paused: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onDecodeRef = useRef(onDecode);
  const onErrorRef = useRef(onError);
  const pausedRef = useRef(paused);
  onDecodeRef.current = onDecode;
  onErrorRef.current = onError;
  pausedRef.current = paused;

  useEffect(() => {
    let cancelled = false;
    let raf = 0;
    let stream: MediaStream | null = null;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    let lastScan = 0;
    // jsQR on a full frame is CPU-heavy; decoding ~7×/sec scans QRs fine and
    // spares mobile battery vs. running it on every animation frame (~60fps).
    const SCAN_INTERVAL_MS = 150;

    const tick = () => {
      if (cancelled) return;
      raf = requestAnimationFrame(tick);
      const video = videoRef.current;
      const now = Date.now();
      if (!video || pausedRef.current || !ctx || video.readyState < video.HAVE_ENOUGH_DATA) return;
      if (now - lastScan < SCAN_INTERVAL_MS) return;
      lastScan = now;
      const w = video.videoWidth;
      const h = video.videoHeight;
      if (!w || !h) return;
      canvas.width = w;
      canvas.height = h;
      ctx.drawImage(video, 0, 0, w, h);
      const img = ctx.getImageData(0, 0, w, h);
      const code = jsQR(img.data, img.width, img.height, { inversionAttempts: 'dontInvert' });
      if (code?.data) onDecodeRef.current(code.data);
    };

    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        onErrorRef.current('insecure');
        return;
      }
      try {
        // `ideal` (not exact) so a device with only a front camera falls back
        // instead of throwing OverconstrainedError.
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((tr) => tr.stop());
          return;
        }
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        // play() can reject on autoplay quirks even though the stream is live;
        // ignore it — the decode loop reads frames once readyState is high
        // enough — and don't misreport it as a permission denial.
        await video.play().catch(() => {});
        raf = requestAnimationFrame(tick);
      } catch (err) {
        const name = (err as DOMException)?.name;
        onErrorRef.current(name === 'NotFoundError' || name === 'DevicesNotFoundError' ? 'nodevice' : 'denied');
      }
    };

    start();
    return () => {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
      stream?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  return (
    <video
      ref={videoRef}
      muted
      playsInline
      className="w-full aspect-square rounded-lg bg-black object-cover"
    />
  );
}

export function QrAssignTargetModal({ isOpen, onClose, spoolmanMode = false, storageSuggestions = [] }: QrAssignTargetModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const queryClient = useQueryClient();

  const [step, setStep] = useState<'target' | 'scan'>('target');
  const [tab, setTab] = useState<'ams' | 'storage'>('ams');
  const [pickedPrinterId, setPickedPrinterId] = useState<number | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<SlotOption | null>(null);
  const [storage, setStorage] = useState('');
  const [target, setTarget] = useState<AssignTarget | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  // Synchronous in-flight guard: `mutation.isPending` is only observable on the
  // next render, so two frames decoding before that render could both fire.
  // This ref blocks the second one immediately (the 700ms scan throttle alone
  // shouldn't be the only thing preventing a double assignment).
  const inFlightRef = useRef(false);

  // Reset to a clean target-selection state each time the modal opens.
  useEffect(() => {
    if (isOpen) {
      setStep('target');
      setSelectedSlot(null);
      setStorage('');
      setTarget(null);
      setScanError(null);
      setCameraError(null);
    }
  }, [isOpen]);

  const { data: printers, isLoading: printersLoading } = useQuery({
    queryKey: ['printers'],
    queryFn: () => api.getPrinters(),
    enabled: isOpen,
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
        });
      }
    }
    // External (vt_tray) slots: backend keys them under ams_id 255 with
    // tray_id = id - 254 (mirrors PrintersPage). formatSlotLabel collapses every
    // external to "Ext", so label them like PrintersPage does — Ext-L/Ext-R when
    // a printer exposes two, "External" otherwise — using the shared i18n keys.
    const externals = status.vt_tray ?? [];
    for (const vt of externals) {
      const trayId = (vt.id ?? 254) - 254;
      const label = externals.length > 1 ? t(trayId === 0 ? 'printers.extL' : 'printers.extR') : t('printers.external');
      out.push({ amsId: 255, trayId, isHt: false, isExternal: true, label });
    }
    return out;
  }, [status, t]);

  const assignMutation = useMutation({
    mutationFn: async ({ spoolId, tg }: { spoolId: number; tg: AssignTarget }) => {
      if (tg.kind === 'ams') {
        // Move semantics: a spool lives in one slot at a time. The backend assign
        // route upserts per (printer, ams, tray) and does NOT clear the spool's
        // previous slot (AssignSpoolModal sidesteps this by hiding already-assigned
        // spools from its picker — a filter the QR flow can't apply). So we strip
        // the spool's other slot(s) ourselves.
        if (spoolmanMode) {
          // Spoolman unassign is keyed by spool id (clears whatever slot it's in),
          // so clear first, then assign to the new slot.
          await api.unassignSpoolmanSlot(spoolId).catch(() => {});
          await api.assignSpoolmanSlot({ spoolman_spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId });
        } else {
          await api.assignSpool({ spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId });
          // Remove this spool from any OTHER slot (local unassign is keyed by slot
          // coordinates, so we look up the stale rows first). Best-effort.
          const existing = await api.getAssignments();
          await Promise.all(
            existing
              .filter((a) => a.spool_id === spoolId && !(a.printer_id === tg.printerId && a.ams_id === tg.amsId && a.tray_id === tg.trayId))
              .map((a) => api.unassignSpool(a.printer_id, a.ams_id, a.tray_id).catch(() => {})),
          );
        }
      } else if (spoolmanMode) {
        await api.updateSpoolmanInventorySpool(spoolId, { storage_location: tg.storageLocation });
      } else {
        await api.updateSpool(spoolId, { storage_location: tg.storageLocation });
      }
    },
    onSuccess: (_data, { tg }) => {
      inFlightRef.current = false;
      showToast(
        t(tg.kind === 'ams' ? 'inventory.qrAssign.assignSuccess' : 'inventory.qrAssign.storageSuccess', { location: targetLocationLabel(tg) }),
        'success',
      );
      // All targets live above this modal (queryClient + toast context), so
      // closing here is safe even though the mutation outlived the open modal.
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      queryClient.invalidateQueries({ queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments-all'] });
      if (tg.kind === 'ams') {
        // Parity with AssignSpoolModal (#1414): nudge the printer to republish
        // its state so the printer card doesn't sit on stale tray data until the
        // next poll. Best-effort — the assignment itself already succeeded.
        api.refreshPrinterStatus(tg.printerId).catch(() => {});
        queryClient.invalidateQueries({ queryKey: ['printerStatus', tg.printerId] });
      }
      onClose();
    },
    onError: (error: Error, { tg }) => {
      // Keep the camera running so the user can rescan and retry.
      inFlightRef.current = false;
      showToast(`${t('inventory.qrAssign.assignFailed', { location: targetLocationLabel(tg) })}: ${error.message}`, 'error');
    },
  });

  const handleDecode = useCallback(
    (text: string) => {
      if (inFlightRef.current || assignMutation.isPending || !target) return;
      const id = parseSpoolIdFromQr(text);
      if (!id) {
        setScanError(t('inventory.qrAssign.invalidQr'));
        return;
      }
      setScanError(null);
      inFlightRef.current = true;
      assignMutation.mutate({ spoolId: id, tg: target });
    },
    [assignMutation, target, t],
  );

  const handleCameraError = useCallback(
    (reason: CameraReason) => {
      setCameraError(
        t(
          reason === 'insecure'
            ? 'inventory.qrAssign.cameraInsecure'
            : reason === 'nodevice'
              ? 'inventory.qrAssign.cameraNoDevice'
              : 'inventory.qrAssign.cameraDenied',
        ),
      );
    },
    [t],
  );

  if (!isOpen) return null;

  const printerName = printers?.find((p) => p.id === effectivePrinterId)?.name ?? '';
  const canStart = tab === 'ams' ? selectedSlot !== null && effectivePrinterId !== null : storage.trim().length > 0;

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
      };
    } else {
      const loc = storage.trim();
      if (!loc) return;
      tg = { kind: 'storage', storageLocation: loc };
    }
    setTarget(tg);
    setScanError(null);
    setCameraError(null);
    setStep('scan');
  };

  return (
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
                              className={`aspect-square rounded-lg border text-sm font-semibold transition-colors ${
                                isSel
                                  ? 'bg-bambu-green/20 border-bambu-green text-white'
                                  : 'bg-bambu-dark border-bambu-dark-tertiary text-bambu-gray hover:border-bambu-gray'
                              }`}
                            >
                              {slot.label}
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
                  <label htmlFor="qr-storage-input" className="block text-xs font-medium text-bambu-gray uppercase tracking-wide mb-1">
                    {t('inventory.qrAssign.storageLabel')}
                  </label>
                  <input
                    id="qr-storage-input"
                    type="text"
                    list="qr-storage-suggestions"
                    maxLength={255}
                    value={storage}
                    onChange={(e) => setStorage(e.target.value)}
                    placeholder={t('inventory.qrAssign.storagePlaceholder')}
                    className="w-full px-3 py-2 bg-bambu-dark border border-bambu-dark-tertiary rounded-lg text-white text-sm placeholder:text-bambu-gray/50 focus:outline-none focus:border-bambu-green"
                  />
                  <datalist id="qr-storage-suggestions">
                    {storageSuggestions.map((s) => (
                      <option key={s} value={s} />
                    ))}
                  </datalist>
                </div>
              )}
            </>
          ) : (
            <div className="space-y-3">
              {target && (
                <p className="text-sm text-bambu-gray">{t('inventory.qrAssign.scanningTarget', { location: targetLocationLabel(target) })}</p>
              )}

              {cameraError ? (
                <div className="flex flex-col items-center gap-3 py-10 text-center">
                  <CameraOff className="w-10 h-10 text-bambu-gray" />
                  <p className="text-sm text-red-400 max-w-xs">{cameraError}</p>
                </div>
              ) : (
                <div className="relative">
                  <QrScannerView onDecode={handleDecode} onError={handleCameraError} paused={assignMutation.isPending} />
                  {/* Framing guide */}
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <div className="w-2/3 aspect-square border-2 border-bambu-green/70 rounded-xl" />
                  </div>
                  {assignMutation.isPending && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 rounded-lg">
                      <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
                    </div>
                  )}
                </div>
              )}

              {!cameraError && <p className="text-xs text-bambu-gray text-center">{t('inventory.qrAssign.scanInstruction')}</p>}
              {scanError && <p className="text-xs text-red-400 text-center">{scanError}</p>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-between gap-2 p-4 border-t border-bambu-dark-tertiary">
          {step === 'scan' ? (
            <Button variant="secondary" onClick={() => setStep('target')} disabled={assignMutation.isPending}>
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
  );
}
