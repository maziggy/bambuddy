import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Camera } from 'lucide-react';
import { createFrameDecoder, decodeImageBlob } from '../utils/qrDecode';

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
    const decodeFrame = createFrameDecoder();

    const tick = () => {
      if (cancelled) return;
      raf = requestAnimationFrame(tick);
      const video = videoRef.current;
      const now = Date.now();
      if (!video || pausedRef.current || busy || video.readyState < video.HAVE_ENOUGH_DATA) return;
      if (now - lastScan < INTERVAL_MS) return;
      lastScan = now;
      busy = true;
      void decodeFrame(video, canvas)
        .then((value) => {
          if (value && !cancelled) onDecodeRef.current(value);
        })
        // Whatever happened to this frame, the next one gets decoded.
        .catch(() => {})
        .finally(() => {
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

interface QrSpoolScannerProps {
  /** A decoded QR payload, from the live view or a photo. The caller reads it and assigns. */
  onDecode: (text: string) => void;
  /** The caller is acting on a scan: the live view pauses and both paths wait. */
  busy: boolean;
  /** The caller's message about the last scan, e.g. a QR that is no spool label. */
  error: string | null;
  /** Where the scanned spool will go, shown above the camera. */
  targetLabel?: string;
}

/**
 * Scan a spool's label QR (#1574): the live camera preview, and a native-camera
 * photo for small or dense QRs the preview can't focus on. Photos are decoded in
 * the browser and never uploaded. Shared by every dialog that assigns a scanned
 * spool, so they scan the same way.
 */
export function QrSpoolScanner({ onDecode, busy, error, targetLabel }: QrSpoolScannerProps) {
  const { t } = useTranslation();
  const [cameraFailed, setCameraFailed] = useState(false);
  const [decoding, setDecoding] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // A photo still decoding when the scanner closes (Back, Cancel) must not
  // reach a caller that has moved on.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const isBusy = busy || decoding;

  const handleDecode = (text: string) => {
    setPhotoError(null);
    onDecode(text);
  };

  const handlePhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file; the File is GC'd after decode
    if (!file || isBusy) return;
    setPhotoError(null);
    setDecoding(true);
    try {
      const text = await decodeImageBlob(file);
      if (!mountedRef.current) return;
      if (!text) setPhotoError(t('inventory.qrAssign.noQrInPhoto'));
      else handleDecode(text);
    } catch {
      if (mountedRef.current) setPhotoError(t('inventory.qrAssign.noQrInPhoto'));
    } finally {
      if (mountedRef.current) setDecoding(false);
    }
  };

  const message = error ?? photoError;

  return (
    <div className="space-y-3">
      {targetLabel && <p className="text-sm text-bambu-gray">{t('inventory.qrAssign.scanningTarget', { location: targetLabel })}</p>}

      {/* Primary: live camera scan. Hidden once it fails (no camera /
          permission denied) — the photo-capture fallback below covers it.
          Phone width at most, so the photo button stays in view in a wide
          dialog too. */}
      {!cameraFailed && (
        <div className="relative mx-auto w-full max-w-sm">
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
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={isBusy}
        className="w-full flex items-center justify-center gap-2 py-3 rounded-lg border border-bambu-dark-tertiary text-bambu-gray hover:text-white hover:border-bambu-gray transition-colors disabled:opacity-50"
      >
        {decoding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Camera className="w-4 h-4" />}
        <span className="text-sm font-medium">
          {decoding ? t('inventory.qrAssign.decoding') : t('inventory.qrAssign.takePhoto')}
        </span>
      </button>

      {message && <p className="text-xs text-red-400 text-center">{message}</p>}
    </div>
  );
}
