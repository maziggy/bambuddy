import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, ScanBarcode, Image as ImageIcon, Keyboard, Loader2 } from 'lucide-react';
import type { BrowserMultiFormatReader as BrowserMultiFormatReaderType, IScannerControls } from '@zxing/browser';
import { Button } from './Button';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';
import {
  expandUpcEToUpcA,
  extractGtinFromManualEntry,
  extractGtinFromScan,
  isPlausibleSku,
  isValidUpcEanBarcode,
} from '../utils/barcode';
import type { LinkedCode } from '../api/client';

type ScanTab = 'scan' | 'photo' | 'manual';

export interface ScannedFilamentResult {
  barcode: string | null;
  matched: boolean;
  source: 'inventory' | 'ofd' | 'spoolmandb-community' | 'parsed' | null;
  material: string | null;
  brand: string | null;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  label_weight: number | null;
  linked_codes: LinkedCode[];
}

interface BarcodeScannerModalProps {
  onClose: () => void;
  /** Called once a barcode/photo resolves (matched or not) — the caller opens
   *  the add-spool form prefilled with the result and closes this modal. */
  onResolved: (result: ScannedFilamentResult) => void;
}

// getUserMedia is unavailable outside a secure context (https:// or
// localhost). Most self-hosted Bambuddy instances are reached over plain
// http://<lan-ip>, where the camera tab simply can't work.
function hasCameraSupport(): boolean {
  return typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia;
}

// Best-effort guess for whether this device has a camera, purely to decide
// the Photo tab's button label ("Take Photograph" vs "Choose Photograph").
// The <input capture> attribute that triggers the OS camera app has no JS
// API to ask "will this actually open a camera" ahead of time, so this is a
// heuristic, not a hard guarantee — the file picker still works either way.
function guessDeviceHasCamera(): boolean {
  // Touch-primary devices (phones/tablets) almost always have a camera, and
  // are exactly the class of device where browsers honor `capture`. Desktop
  // browsers generally ignore `capture` and show a plain file picker even if
  // a webcam is attached, so a coarse-pointer check is a better proxy here
  // than device enumeration would be.
  if (typeof window !== 'undefined' && window.matchMedia?.('(pointer: coarse)').matches) return true;
  return typeof navigator !== 'undefined' && navigator.maxTouchPoints > 0;
}

function loadImageFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Failed to load image'));
    img.src = URL.createObjectURL(file);
  });
}

// Draws the image rotated by `deg`, downscaled to maxDim for speed.
function rotatedCanvas(img: HTMLImageElement, deg: number, maxDim: number): HTMLCanvasElement {
  const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
  const w = Math.round(img.width * scale);
  const h = Math.round(img.height * scale);
  const swap = deg === 90 || deg === 270;
  const canvas = document.createElement('canvas');
  canvas.width = swap ? h : w;
  canvas.height = swap ? w : h;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.translate(canvas.width / 2, canvas.height / 2);
    ctx.rotate((deg * Math.PI) / 180);
    ctx.drawImage(img, -w / 2, -h / 2, w, h);
  }
  return canvas;
}

const FILAMENT_CUE_RE =
  /\b(PLA|PETG|PCTG|ABS|ASA|TPU|nylon|temp|filament|spool|silk|matte|dual|sunlu|esun|polymaker|panchroma|hatchbox|overture)\b/i;

// Score recognized text: more letters is better; big bonus for filament cues.
function scoreOcrText(text: string): number {
  const letters = (text.match(/[A-Za-z]/g) || []).length;
  const cue = FILAMENT_CUE_RE.test(text) || /\d{2,3}\s*[-~]\s*\d{2,3}/.test(text);
  return letters + (cue ? 80 : 0);
}

// OCRs the photo trying each orientation, keeping the best-scoring result —
// handles a sideways/upside-down phone photo of a spool label.
async function ocrBestOrientation(file: File, onProgress: (message: string) => void): Promise<string> {
  const Tesseract = await import('tesseract.js');
  const img = await loadImageFile(file);
  let best = { text: '', score: -1 };
  for (const deg of [0, 90, 270, 180]) {
    const canvas = rotatedCanvas(img, deg, 1600);
    const { data } = await Tesseract.recognize(canvas, 'eng', {
      logger: (m) => {
        if (m.status === 'recognizing text') {
          const suffix = deg ? ` (${deg}°)` : '';
          onProgress(`${Math.round(m.progress * 100)}%${suffix}`);
        }
      },
    });
    const text = (data.text || '').replace(/\s+/g, ' ').trim();
    const score = scoreOcrText(text);
    if (score > best.score) best = { text, score };
    if (score >= 120) break; // strong result — no need to try more rotations
  }
  return best.text;
}

function getSourceLabel(
  source: ScannedFilamentResult['source'],
  t: (key: string, fallback: string) => string,
): string {
  if (source === 'inventory') return t('inventory.barcodeScan.sourceInventory', 'your inventory');
  if (source === 'spoolmandb-community') return t('inventory.barcodeScan.sourceSpoolmanDbCommunity', 'SpoolmanDB-Community');
  return t('inventory.barcodeScan.sourceOfd', 'the Open Filament Database');
}

export function BarcodeScannerModal({ onClose, onResolved }: BarcodeScannerModalProps) {
  const { t } = useTranslation();
  const { showToast } = useToast();
  const cameraSupported = hasCameraSupport();

  const [activeTab, setActiveTab] = useState<ScanTab>(cameraSupported ? 'scan' : 'photo');
  const [loadingMessage, setLoadingMessage] = useState<string | null>(null);
  // 'https-required' covers browsers (Firefox in particular) that still expose
  // navigator.mediaDevices.getUserMedia as a function on an insecure origin —
  // hasCameraSupport() lets the tab through, and the call only fails once we
  // actually try it. Kept distinct from 'other' so we never show the raw
  // browser error (e.g. "NotAllowedError...") for what is really just a
  // missing-HTTPS setup.
  const [cameraError, setCameraError] = useState<{ type: 'https-required' } | { type: 'other'; message: string } | null>(null);
  const [manualBarcode, setManualBarcode] = useState('');
  const [manualError, setManualError] = useState<string | null>(null);
  const [ocrHint, setOcrHint] = useState<string | null>(null);
  // Drives the Photo tab's button label ("Take" vs "Choose Photograph").
  // Seeded with the touch-device heuristic, then refined below with a real
  // device list when the browser allows enumeration.
  const [hasCameraDevice, setHasCameraDevice] = useState<boolean>(guessDeviceHasCamera);
  // Bumped on a failed lookup to restart the camera decode loop — the reader
  // is already stopped by the time resolveBarcode is called (see the scan
  // callback below), and activeTab/cameraSupported don't change on a lookup
  // failure, so without this the video feed would freeze until the user
  // left and re-entered the Scan tab.
  const [scanRetryToken, setScanRetryToken] = useState(0);

  const videoRef = useRef<HTMLVideoElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const controlsRef = useRef<IScannerControls | null>(null);
  const resolvingRef = useRef(false);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  useEffect(() => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    let cancelled = false;
    navigator.mediaDevices
      .enumerateDevices()
      .then((devices) => {
        if (!cancelled) setHasCameraDevice(devices.some((d) => d.kind === 'videoinput'));
      })
      .catch(() => {
        // enumerateDevices can reject in some insecure-context browsers —
        // keep the touch-device heuristic from initial state.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const resolveBarcode = async (barcode: string) => {
    if (resolvingRef.current) return;
    resolvingRef.current = true;
    setLoadingMessage(t('inventory.barcodeScan.lookingUp', 'Looking up barcode…'));
    try {
      const result = await api.lookupFilamentBarcode(barcode);
      if (result.matched) {
        const sourceLabel = getSourceLabel(result.source, t);
        showToast(t('inventory.barcodeScan.matchedToast', 'Matched from {{source}}', { source: sourceLabel }), 'success');
      } else {
        showToast(t('inventory.barcodeScan.noMatchToast', 'No match found — fill in the details manually'), 'info');
      }
      onResolved({
        barcode: result.barcode,
        matched: result.matched,
        source: result.source,
        material: result.material,
        brand: result.brand,
        subtype: result.subtype,
        color_name: result.color_name,
        rgba: result.rgba,
        label_weight: result.label_weight,
        linked_codes: result.linked_codes,
      });
    } catch {
      showToast(t('inventory.barcodeScan.lookupFailed', 'Barcode lookup failed'), 'error');
      resolvingRef.current = false;
      setLoadingMessage(null);
      // The scan loop was already stopped before this call (see the decode
      // callback below) — bump the retry token so the camera effect restarts
      // it instead of leaving the feed frozen.
      setScanRetryToken((n) => n + 1);
    }
  };

  // Camera scanning — only runs while the Scan tab is active.
  useEffect(() => {
    if (activeTab !== 'scan' || !cameraSupported) return;
    let cancelled = false;
    setCameraError(null);

    // Short-circuit before even requesting the camera: some browsers (Firefox)
    // keep getUserMedia callable on an insecure origin and only reject once
    // invoked, which would otherwise flash the video element and a raw
    // permission error before landing on the same conclusion.
    if (!window.isSecureContext) {
      setCameraError({ type: 'https-required' });
      return;
    }

    Promise.all([import('@zxing/browser'), import('@zxing/library')]).then(
      ([{ BrowserMultiFormatReader, BarcodeFormat }, { DecodeHintType }]) => {
      if (cancelled || !videoRef.current) return;
      // Retail filament spools carry a linear UPC/EAN barcode, a GS1 Digital
      // Link QR code encoding the same GTIN in its URL path, or — for some
      // manufacturers (e.g. a Polymaker box with no UPC/EAN at all) — a Code
      // 128 "inventory barcode" encoding a SKU/article number instead of a
      // GTIN. Restricting decode to just these formats (instead of every
      // symbology ZXing supports) cuts down on false-positive reads from
      // webcam noise, which is where the single-digit "barcode" misreads
      // were coming from.
      const hints = new Map<unknown, unknown>();
      hints.set(DecodeHintType.POSSIBLE_FORMATS, [
        BarcodeFormat.UPC_A,
        BarcodeFormat.UPC_E,
        BarcodeFormat.EAN_13,
        BarcodeFormat.EAN_8,
        BarcodeFormat.QR_CODE,
        BarcodeFormat.CODE_128,
      ]);
      // TRY_HARDER trades a bit of per-frame CPU for meaningfully better
      // detection of dense/small 1D barcodes — e.g. some third-party spool
      // boxes carry an Amazon FNSKU-style Code 128 label instead of a retail
      // GTIN, and those are dense enough that ZXing's default (fast) mode
      // misses them far more often than a phone's native camera scanner would.
      hints.set(DecodeHintType.TRY_HARDER, true);
      const reader: BrowserMultiFormatReaderType = new BrowserMultiFormatReader(hints);
      reader
        .decodeFromVideoDevice(undefined, videoRef.current, (result, _err, controls) => {
          // Guard against the window between this effect's cleanup running
          // (component unmount / tab switch) and a frame callback that was
          // already in flight: without this, a stale ref write here would
          // leave the camera stream live with nothing left to stop it (the
          // camera light stays on) since cleanup has already fired and won't
          // run again.
          if (cancelled) {
            controls.stop();
            return;
          }
          controlsRef.current = controls;
          if (!result || resolvingRef.current) return;
          if (result.getBarcodeFormat() === BarcodeFormat.CODE_128) {
            // Code 128 has its own symbology-level checksum and start/stop
            // pattern, so a successful decode is already trustworthy — unlike
            // a bare guessed digit string, it doesn't need a GTIN-style
            // checksum (it's typically not even a GTIN, but a manufacturer
            // SKU/article number). Only a light sanity check is applied.
            const text = result.getText().trim();
            if (!isPlausibleSku(text)) return;
            controls.stop();
            void resolveBarcode(text);
            return;
          }
          if (result.getBarcodeFormat() === BarcodeFormat.UPC_E) {
            // ZXing decodes UPC-E to its own compressed 8-digit form, which
            // does not validate against the standard EAN-8 checksum — expand
            // to the equivalent UPC-A form first (see expandUpcEToUpcA).
            const expanded = expandUpcEToUpcA(result.getText());
            if (!expanded || !isValidUpcEanBarcode(expanded)) return;
            controls.stop();
            void resolveBarcode(expanded);
            return;
          }
          // A UPC/EAN decode is already a bare digit string; a QR decode is
          // typically a GS1 Digital Link URL with the GTIN embedded in its
          // path — extractGtinFromScan pulls the candidate out of either shape.
          const candidate = extractGtinFromScan(result.getText());
          // Defense-in-depth: even within the restricted formats above, a
          // corrupted read can still surface a bad check digit, and a QR
          // code might not be a GS1 Digital Link at all — validate before
          // treating it as a real barcode. An invalid read is silently
          // ignored so the scan loop just keeps looking.
          if (!candidate || !isValidUpcEanBarcode(candidate)) return;
          controls.stop();
          void resolveBarcode(candidate);
        })
        .then((controls) => {
          // Same race as above: decodeFromVideoDevice's setup (getUserMedia
          // through the first frame) is async, so cleanup can already have
          // run by the time this resolves. If so, stop the stream directly
          // instead of stashing it in a ref nothing will use again — that's
          // what left the camera light on.
          if (cancelled) {
            controls.stop();
            return;
          }
          controlsRef.current = controls;
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (!window.isSecureContext) {
            setCameraError({ type: 'https-required' });
          } else {
            setCameraError({ type: 'other', message: err instanceof Error ? err.message : String(err) });
          }
        });
    });

    return () => {
      cancelled = true;
      controlsRef.current?.stop();
      controlsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, cameraSupported, scanRetryToken]);

  const handlePhotoSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;

    setOcrHint(t('inventory.barcodeScan.ocrReading', 'Reading label…'));
    try {
      const text = await ocrBestOrientation(file, (progress) =>
        setOcrHint(t('inventory.barcodeScan.ocrProgress', 'Reading label… {{progress}}', { progress })),
      );
      if (!text) {
        setOcrHint(t('inventory.barcodeScan.ocrNoText', 'No text found — try a clearer, closer photo.'));
        return;
      }
      setOcrHint(null);
      setLoadingMessage(t('inventory.barcodeScan.parsingLabel', 'Parsing label…'));
      const result = await api.parseFilamentLabel(text);
      if (result.matched) {
        const sourceLabel = getSourceLabel(result.source, t);
        showToast(t('inventory.barcodeScan.matchedToast', 'Matched from {{source}}', { source: sourceLabel }), 'success');
      } else if (result.material || result.brand) {
        showToast(t('inventory.barcodeScan.guessedToast', 'Guessed details from the label — please review'), 'info');
      } else {
        showToast(t('inventory.barcodeScan.noMatchToast', 'No match found — fill in the details manually'), 'info');
      }
      onResolved({
        barcode: result.barcode,
        matched: result.matched,
        source: result.source,
        material: result.material,
        brand: result.brand,
        subtype: result.subtype,
        color_name: result.color_name,
        rgba: result.rgba,
        label_weight: result.label_weight,
        linked_codes: result.linked_codes,
      });
    } catch {
      setOcrHint(null);
      setLoadingMessage(null);
      showToast(t('inventory.barcodeScan.ocrFailed', 'Reading the label failed'), 'error');
    }
  };

  const handleManualSubmit = () => {
    const trimmed = manualBarcode.trim();
    if (!trimmed) return;
    // Tolerates spaces/dashes for readability and a pasted GS1 Digital Link
    // URL (e.g. copied from a phone's native QR scanner), not just bare digits.
    const candidate = extractGtinFromManualEntry(trimmed);
    if (candidate && isValidUpcEanBarcode(candidate)) {
      setManualError(null);
      void resolveBarcode(candidate);
      return;
    }
    // Not a valid GTIN shape — fall back to treating the raw input as a
    // manufacturer SKU/article number (e.g. a Polymaker inventory barcode
    // typed by hand instead of scanned) rather than hard-rejecting it. The
    // backend's OFD/SpoolmanDB-Community lookups decide if it resolves.
    if (isPlausibleSku(trimmed)) {
      setManualError(null);
      void resolveBarcode(trimmed.toUpperCase());
      return;
    }
    setManualError(
      t(
        'inventory.barcodeScan.manualInvalid',
        'Enter a valid UPC-A/EAN-8/EAN-13 barcode, or a manufacturer SKU/article number (3+ characters)',
      ),
    );
  };

  const busy = loadingMessage !== null;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div
        className="bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary w-full max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">
            {t('inventory.barcodeScan.title', 'Scan Barcode')}
          </h2>
          <button onClick={onClose} className="text-bambu-gray hover:text-white transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-bambu-dark-tertiary">
          {cameraSupported && (
            <button
              onClick={() => setActiveTab('scan')}
              className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-sm font-medium transition-colors ${
                activeTab === 'scan' ? 'text-bambu-green border-b-2 border-bambu-green' : 'text-bambu-gray hover:text-white'
              }`}
            >
              <ScanBarcode className="w-4 h-4" />
              {t('inventory.barcodeScan.tabScan', 'Scan')}
            </button>
          )}
          <button
            onClick={() => setActiveTab('photo')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-sm font-medium transition-colors ${
              activeTab === 'photo' ? 'text-bambu-green border-b-2 border-bambu-green' : 'text-bambu-gray hover:text-white'
            }`}
          >
            <ImageIcon className="w-4 h-4" />
            {t('inventory.barcodeScan.tabPhoto', 'Photo of Label')}
          </button>
          <button
            onClick={() => setActiveTab('manual')}
            className={`flex-1 flex items-center justify-center gap-1.5 py-3 text-sm font-medium transition-colors ${
              activeTab === 'manual' ? 'text-bambu-green border-b-2 border-bambu-green' : 'text-bambu-gray hover:text-white'
            }`}
          >
            <Keyboard className="w-4 h-4" />
            {t('inventory.barcodeScan.tabManual', 'Manual Entry')}
          </button>
        </div>

        {/* Content */}
        <div className="p-6">
          {busy ? (
            <div className="flex flex-col items-center justify-center gap-3 py-10 text-bambu-gray">
              <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
              <p className="text-sm">{loadingMessage}</p>
            </div>
          ) : (
            <>
              {activeTab === 'scan' && cameraSupported && (
                <div className="space-y-3">
                  {cameraError?.type === 'https-required' ? (
                    <p className="text-sm text-red-400 text-center py-10">
                      {t(
                        'inventory.barcodeScan.cameraNeedsHttps',
                        'Camera not available — HTTPS (or localhost) is required. Use Photo of Label or Manual Entry instead.',
                      )}
                    </p>
                  ) : (
                    <>
                      <div className="relative aspect-video bg-black rounded-lg overflow-hidden">
                        <video ref={videoRef} muted playsInline autoPlay className="w-full h-full object-cover" />
                        {!cameraError && (
                          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
                            <div className="relative w-[82%] max-w-xs h-[38%]">
                              {/* Dim everything outside the guide box so the target area stands out */}
                              <div
                                className="absolute inset-0 rounded-lg"
                                style={{ boxShadow: '0 0 0 9999px rgba(0,0,0,0.45)' }}
                              />
                              {/* Corner brackets marking the guide box */}
                              <div className="absolute -top-0.5 -left-0.5 w-6 h-6 border-t-2 border-l-2 border-bambu-green rounded-tl-md" />
                              <div className="absolute -top-0.5 -right-0.5 w-6 h-6 border-t-2 border-r-2 border-bambu-green rounded-tr-md" />
                              <div className="absolute -bottom-0.5 -left-0.5 w-6 h-6 border-b-2 border-l-2 border-bambu-green rounded-bl-md" />
                              <div className="absolute -bottom-0.5 -right-0.5 w-6 h-6 border-b-2 border-r-2 border-bambu-green rounded-br-md" />
                              {/* Sweeping scan line */}
                              <div className="barcode-scan-line absolute left-0 right-0 h-0.5 -translate-y-1/2 bg-bambu-green shadow-[0_0_6px_2px_rgba(0,174,66,0.6)]" />
                            </div>
                          </div>
                        )}
                      </div>
                      {cameraError ? (
                        <p className="text-sm text-red-400 text-center">
                          {t(
                            'inventory.barcodeScan.cameraError',
                            'Camera unavailable ({{error}}). If Bambuddy isn\'t served over HTTPS, this is expected — use Photo of Label or Manual Entry instead.',
                            { error: cameraError.message },
                          )}
                        </p>
                      ) : (
                        <p className="text-sm text-bambu-gray text-center">
                          {t('inventory.barcodeScan.scanHint', 'Align the barcode within the frame')}
                        </p>
                      )}
                    </>
                  )}
                </div>
              )}

              {activeTab === 'photo' && (
                <div className="flex flex-col items-center gap-4 py-4">
                  <ImageIcon className="w-12 h-12 text-bambu-gray" strokeWidth={1.5} />
                  <p className="text-sm text-bambu-gray text-center max-w-xs">
                    {t(
                      'inventory.barcodeScan.photoHint',
                      'Take or choose a photo of the spool label — this works even without camera-scanning support.',
                    )}
                  </p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    capture="environment"
                    className="hidden"
                    onChange={handlePhotoSelected}
                  />
                  <Button onClick={() => fileInputRef.current?.click()}>
                    <ImageIcon className="w-4 h-4" />
                    {hasCameraDevice
                      ? t('inventory.barcodeScan.takePhoto', 'Take Photo')
                      : t('inventory.barcodeScan.choosePhoto', 'Choose Photo')}
                  </Button>
                  {ocrHint && <p className="text-xs text-bambu-gray text-center">{ocrHint}</p>}
                </div>
              )}

              {activeTab === 'manual' && (
                <div className="space-y-4 py-2">
                  <p className="text-sm text-bambu-gray">
                    {t(
                      'inventory.barcodeScan.manualHint',
                      'Type the barcode or SKU/article number printed on the spool box',
                    )}
                  </p>
                  <input
                    type="text"
                    value={manualBarcode}
                    onChange={(e) => {
                      setManualBarcode(e.target.value);
                      setManualError(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleManualSubmit();
                    }}
                    placeholder={t('inventory.barcodeScan.manualPlaceholder', 'e.g. 6938936716785')}
                    className={`w-full bg-bambu-dark-tertiary border rounded-lg px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:ring-2 ${
                      manualError ? 'border-red-500 focus:ring-red-500' : 'border-bambu-dark-tertiary focus:ring-bambu-green'
                    }`}
                    autoFocus
                  />
                  {manualError && <p className="text-xs text-red-400">{manualError}</p>}
                  <Button onClick={handleManualSubmit} disabled={!manualBarcode.trim()} className="w-full">
                    {t('inventory.barcodeScan.lookUp', 'Look Up')}
                  </Button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
