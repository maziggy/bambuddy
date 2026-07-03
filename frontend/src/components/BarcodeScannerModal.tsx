import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, ScanBarcode, Image as ImageIcon, Keyboard, Loader2 } from 'lucide-react';
import type { BrowserMultiFormatReader as BrowserMultiFormatReaderType, IScannerControls } from '@zxing/browser';
import { Button } from './Button';
import { api } from '../api/client';
import { useToast } from '../contexts/ToastContext';

type ScanTab = 'scan' | 'photo' | 'manual';

export interface ScannedFilamentResult {
  barcode: string | null;
  matched: boolean;
  source: 'inventory' | 'ofd' | 'parsed' | null;
  material: string | null;
  brand: string | null;
  subtype: string | null;
  color_name: string | null;
  rgba: string | null;
  label_weight: number | null;
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
  const [ocrHint, setOcrHint] = useState<string | null>(null);

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

  const resolveBarcode = async (barcode: string) => {
    if (resolvingRef.current) return;
    resolvingRef.current = true;
    setLoadingMessage(t('inventory.barcodeScan.lookingUp', 'Looking up barcode…'));
    try {
      const result = await api.lookupFilamentBarcode(barcode);
      if (result.matched) {
        const sourceLabel =
          result.source === 'inventory'
            ? t('inventory.barcodeScan.sourceInventory', 'your inventory')
            : t('inventory.barcodeScan.sourceOfd', 'the Open Filament Database');
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
      });
    } catch {
      showToast(t('inventory.barcodeScan.lookupFailed', 'Barcode lookup failed'), 'error');
      resolvingRef.current = false;
      setLoadingMessage(null);
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

    import('@zxing/browser').then(({ BrowserMultiFormatReader }) => {
      if (cancelled || !videoRef.current) return;
      const reader: BrowserMultiFormatReaderType = new BrowserMultiFormatReader();
      reader
        .decodeFromVideoDevice(undefined, videoRef.current, (result, _err, controls) => {
          controlsRef.current = controls;
          if (result && !resolvingRef.current) {
            controls.stop();
            void resolveBarcode(result.getText());
          }
        })
        .then((controls) => {
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
  }, [activeTab, cameraSupported]);

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
        const sourceLabel =
          result.source === 'inventory'
            ? t('inventory.barcodeScan.sourceInventory', 'your inventory')
            : t('inventory.barcodeScan.sourceOfd', 'the Open Filament Database');
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
    void resolveBarcode(trimmed);
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
                          {t('inventory.barcodeScan.scanHint', 'Point the camera at the barcode on the spool box')}
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
                    {t('inventory.barcodeScan.choosePhoto', 'Choose Photo')}
                  </Button>
                  {ocrHint && <p className="text-xs text-bambu-gray text-center">{ocrHint}</p>}
                </div>
              )}

              {activeTab === 'manual' && (
                <div className="space-y-4 py-2">
                  <p className="text-sm text-bambu-gray">
                    {t('inventory.barcodeScan.manualHint', 'Type the barcode printed on the spool box')}
                  </p>
                  <input
                    type="text"
                    inputMode="numeric"
                    value={manualBarcode}
                    onChange={(e) => setManualBarcode(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleManualSubmit();
                    }}
                    placeholder={t('inventory.barcodeScan.manualPlaceholder', 'e.g. 6938936716785')}
                    className="w-full bg-bambu-dark-tertiary border border-bambu-dark-tertiary rounded-lg px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:ring-2 focus:ring-bambu-green"
                    autoFocus
                  />
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
