import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { X, Loader2, QrCode, Cpu, MapPin, ArrowLeft, Camera } from 'lucide-react';
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

// BarcodeDetector (platform QR decoder, e.g. Android Chrome) isn't in the TS DOM
// lib yet — minimal shape for the bit we use.
interface DetectedBarcode {
  rawValue: string;
}
interface BarcodeDetectorLike {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
}
type BarcodeDetectorCtor = new (opts?: { formats?: string[] }) => BarcodeDetectorLike;

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
 * Decode a QR from a captured photo. The photo is taken with the phone's native
 * camera (file input + capture) — which focuses far better than an in-page
 * getUserMedia preview — so a small/dense spool QR resolves cleanly. Prefer the
 * platform BarcodeDetector; fall back to jsQR (downscaling huge stills for speed
 * — a focused QR survives the downscale).
 */
/**
 * Draw a frame/bitmap onto a canvas and decode a QR with jsQR. getContext is
 * guarded because jsdom (test env) throws without the canvas package; the
 * caller owns the canvas so the live loop can reuse one across frames.
 */
function decodeCanvas(
  canvas: HTMLCanvasElement,
  source: CanvasImageSource,
  w: number,
  h: number,
  inversionAttempts: 'dontInvert' | 'attemptBoth',
): string | undefined {
  if (w <= 0 || h <= 0) return undefined;
  canvas.width = w;
  canvas.height = h;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext('2d', { willReadFrequently: true });
  } catch {
    ctx = null;
  }
  if (!ctx) return undefined;
  ctx.drawImage(source, 0, 0, w, h);
  const img = ctx.getImageData(0, 0, w, h);
  return jsQR(img.data, w, h, { inversionAttempts })?.data ?? undefined;
}

async function decodeImageBlob(blob: Blob): Promise<string | undefined> {
  const bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  try {
    const BD = (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
    if (BD) {
      try {
        const codes = await new BD({ formats: ['qr_code'] }).detect(bitmap);
        if (codes[0]?.rawValue) return codes[0].rawValue;
      } catch {
        /* BarcodeDetector present but unusable — fall through to jsQR */
      }
    }
    // Downscale huge stills so jsQR stays fast — a focused QR survives it.
    const maxSide = 1600;
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    return decodeCanvas(document.createElement('canvas'), bitmap, w, h, 'attemptBoth');
  } finally {
    bitmap.close?.();
  }
}

/**
 * Live in-page camera scanner (primary path). Continuously decodes the preview
 * frame with BarcodeDetector (→ jsQR fallback). Fast for QRs the user can frame
 * well; the photo-capture fallback covers small/hard ones this can't focus on.
 * Callbacks go through refs so the camera starts once and survives re-renders.
 */
function LiveScanner({
  onDecode,
  onError,
  paused,
}: {
  onDecode: (text: string) => void;
  onError: () => void;
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
    let busy = false;
    let lastScan = 0;
    const INTERVAL_MS = 200;
    const canvas = document.createElement('canvas'); // reused across frames
    const BD = (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
    let detector = BD ? new BD({ formats: ['qr_code'] }) : null;

    const decode = async (video: HTMLVideoElement): Promise<string | undefined> => {
      if (detector) {
        try {
          const codes = await detector.detect(video);
          return codes[0]?.rawValue;
        } catch {
          detector = null; // exists but unusable — fall back to jsQR
        }
      }
      // 'dontInvert' (cheapest) for the hot loop; the photo path uses 'attemptBoth'.
      return decodeCanvas(canvas, video, video.videoWidth, video.videoHeight, 'dontInvert');
    };

    const tick = () => {
      if (cancelled) return;
      raf = requestAnimationFrame(tick);
      const video = videoRef.current;
      const now = Date.now();
      if (!video || pausedRef.current || busy || video.readyState < video.HAVE_ENOUGH_DATA) return;
      if (now - lastScan < INTERVAL_MS) return;
      lastScan = now;
      busy = true;
      void decode(video).then((value) => {
        if (value && !cancelled) onDecodeRef.current(value);
        busy = false;
      });
    };

    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        onErrorRef.current();
        return;
      }
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
          audio: false,
        });
        if (cancelled) {
          stream.getTracks().forEach((tr) => tr.stop());
          return;
        }
        try {
          await stream.getVideoTracks()[0]?.applyConstraints({ advanced: [{ focusMode: 'continuous' }] as unknown as MediaTrackConstraintSet[] });
        } catch {
          /* unsupported */
        }
        const video = videoRef.current;
        if (!video) return;
        video.srcObject = stream;
        await video.play().catch(() => {});
        raf = requestAnimationFrame(tick);
      } catch {
        // Permission denied / no camera / insecure context — hide the live view;
        // the photo-capture fallback covers all of these.
        onErrorRef.current();
      }
    };

    void start();
    return () => {
      cancelled = true;
      if (raf) cancelAnimationFrame(raf);
      stream?.getTracks().forEach((tr) => tr.stop());
    };
  }, []);

  return <video ref={videoRef} muted playsInline className="w-full aspect-square rounded-lg bg-black object-cover" />;
}

function externalLabel(t: (k: string) => string, vtCount: number, trayId: number): string {
  // Match PrintersPage: Ext-L / Ext-R when two external slots, "External" otherwise.
  if (vtCount > 1) return t(trayId === 0 ? 'printers.extL' : 'printers.extR');
  return t('printers.external');
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
  const [error, setError] = useState<string | null>(null);
  const [decoding, setDecoding] = useState(false);
  const [cameraFailed, setCameraFailed] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Synchronous in-flight guard: mutation.isPending only updates next render.
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (isOpen) {
      setStep('target');
      setSelectedSlot(null);
      setStorage('');
      setTarget(null);
      setError(null);
      setDecoding(false);
      setCameraFailed(false);
      inFlightRef.current = false;
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
    // tray_id = id - 254 (mirrors PrintersPage).
    const externals = status.vt_tray ?? [];
    for (const vt of externals) {
      const trayId = (vt.id ?? 254) - 254;
      out.push({ amsId: 255, trayId, isHt: false, isExternal: true, label: externalLabel(t, externals.length, trayId) });
    }
    return out;
  }, [status, t]);

  const assignMutation = useMutation({
    mutationFn: async ({ spoolId, tg }: { spoolId: number; tg: AssignTarget }) => {
      if (tg.kind === 'ams') {
        // Move semantics: a spool lives in one slot at a time. The backend assign
        // route upserts per (printer, ams, tray) and does NOT clear the spool's
        // previous slot (AssignSpoolModal sidesteps this by hiding already-assigned
        // spools from its picker — a filter the QR flow can't apply), so we strip
        // the spool's other slot(s) ourselves.
        if (spoolmanMode) {
          // Spoolman unassign is keyed by spool id (clears whatever slot it's in),
          // so we must clear first, then assign to the new slot.
          await api.unassignSpoolmanSlot(spoolId).catch(() => {});
          await api.assignSpoolmanSlot({ spoolman_spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId });
        } else {
          await api.assignSpool({ spool_id: spoolId, printer_id: tg.printerId, ams_id: tg.amsId, tray_id: tg.trayId });
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
      // Intentionally do NOT clear inFlightRef here: the modal closes right
      // after, and leaving it set blocks any stale in-flight live-scan decode
      // from firing a second assignment in the brief window before unmount.
      // It's reset wholesale when the modal next opens (and in onError for retry).
      showToast(
        t(tg.kind === 'ams' ? 'inventory.qrAssign.assignSuccess' : 'inventory.qrAssign.storageSuccess', { location: targetLocationLabel(tg) }),
        'success',
      );
      queryClient.invalidateQueries({ queryKey: ['spool-assignments'] });
      queryClient.invalidateQueries({ queryKey: spoolmanMode ? ['spoolman-inventory-spools'] : ['inventory-spools'] });
      queryClient.invalidateQueries({ queryKey: ['spoolman-slot-assignments-all'] });
      if (tg.kind === 'ams') {
        // #1414 parity: nudge the printer to republish so its card doesn't show
        // stale tray state until the next poll. Best-effort.
        api.refreshPrinterStatus(tg.printerId).catch(() => {});
        queryClient.invalidateQueries({ queryKey: ['printerStatus', tg.printerId] });
      }
      onClose();
    },
    onError: (error: Error, { tg }) => {
      inFlightRef.current = false;
      showToast(`${t('inventory.qrAssign.assignFailed', { location: targetLocationLabel(tg) })}: ${error.message}`, 'error');
    },
  });

  if (!isOpen) return null;

  const printerName = printers?.find((p) => p.id === effectivePrinterId)?.name ?? '';
  const canStart = tab === 'ams' ? selectedSlot !== null && effectivePrinterId !== null : storage.trim().length > 0;
  const isBusy = decoding || assignMutation.isPending;

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
    setError(null);
    setCameraFailed(false);
    setStep('scan');
  };

  // Shared by the live scanner and the photo fallback: validate the decoded QR
  // and fire the assignment (guarded so one scan can't assign twice).
  const handleDecode = (text: string) => {
    if (inFlightRef.current || assignMutation.isPending || !target) return;
    const id = parseSpoolIdFromQr(text);
    if (!id) {
      setError(t('inventory.qrAssign.invalidQr'));
      return;
    }
    setError(null);
    inFlightRef.current = true;
    assignMutation.mutate({ spoolId: id, tg: target });
  };

  const handlePhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file; the File is GC'd after decode
    if (!file || !target || inFlightRef.current) return;
    setError(null);
    setDecoding(true);
    try {
      const text = await decodeImageBlob(file);
      if (!text) setError(t('inventory.qrAssign.noQrInPhoto'));
      else handleDecode(text);
    } catch {
      setError(t('inventory.qrAssign.noQrInPhoto'));
    } finally {
      setDecoding(false);
    }
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

              {/* Primary: live camera scan. Hidden once it fails (no camera /
                  permission denied) — the photo-capture fallback below covers it. */}
              {!cameraFailed && (
                <div className="relative">
                  <LiveScanner onDecode={handleDecode} onError={() => setCameraFailed(true)} paused={isBusy} />
                  <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                    <div className="w-2/3 aspect-square border-2 border-bambu-green/70 rounded-xl" />
                  </div>
                  {isBusy && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 rounded-lg">
                      <Loader2 className="w-8 h-8 text-bambu-green animate-spin" />
                    </div>
                  )}
                </div>
              )}

              <p className="text-xs text-bambu-gray text-center">{t('inventory.qrAssign.scanInstruction')}</p>

              {/* Fallback: capture a photo with the native camera (better focus). */}
              <input ref={inputRef} type="file" accept="image/*" capture="environment" className="hidden" onChange={handlePhoto} />
              <button
                onClick={() => inputRef.current?.click()}
                disabled={isBusy}
                className="w-full flex items-center justify-center gap-2 py-3 rounded-lg border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray transition-colors disabled:opacity-50"
              >
                {decoding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
                <span className="text-sm font-medium">
                  {decoding ? t('inventory.qrAssign.decoding') : t('inventory.qrAssign.takePhoto')}
                </span>
              </button>

              {error && <p className="text-xs text-red-400 text-center">{error}</p>}
            </div>
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
  );
}
