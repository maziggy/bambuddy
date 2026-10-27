import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Camera, RotateCcw } from 'lucide-react';
import { createFrameDecoder, decodeImageBlob } from '../utils/qrDecode';

/** Why the live view is off: the page isn't a secure origin, or no camera could be opened. */
type CameraIssue = 'insecure' | 'unavailable';

/**
 * Live frames in a row a label must go unread before it counts as taken out of
 * view: frames are at least 200 ms apart, so over a second. A label in front of
 * the camera is not read in every frame, and a missed frame or two mustn't send
 * it again.
 */
const OUT_OF_VIEW_FRAMES = 6;

/**
 * Live in-page camera scanner (primary path). Continuously decodes the preview
 * frame with BarcodeDetector (→ jsQR fallback) and reports each frame it read:
 * the QR payload, or null for none. Fast for QRs the user can frame well; the
 * photo-capture fallback covers small/hard ones this can't focus on. Callbacks
 * go through refs so the camera starts once and survives re-renders.
 */
function LiveScanner({
  onFrame,
  onError,
  paused,
}: {
  onFrame: (text: string | null) => void;
  onError: (issue: CameraIssue) => void;
  paused: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const onFrameRef = useRef(onFrame);
  const onErrorRef = useRef(onError);
  const pausedRef = useRef(paused);
  onFrameRef.current = onFrame;
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
          if (!cancelled) onFrameRef.current(value || null);
        })
        // Whatever happened to this frame, the next one gets decoded.
        .catch(() => {})
        .finally(() => {
          busy = false;
        });
    };

    const start = async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        // Browsers only offer the camera to a secure origin (HTTPS or localhost).
        onErrorRef.current(window.isSecureContext ? 'unavailable' : 'insecure');
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
        // Permission denied / no camera — the live view gives way to a hint;
        // the photo-capture fallback covers both.
        onErrorRef.current('unavailable');
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
  /** The caller is acting on a scan, or isn't ready for one: the live view pauses and both paths wait. */
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
  const [cameraIssue, setCameraIssue] = useState<CameraIssue | null>(null);
  const [decoding, setDecoding] = useState(false);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // The payloads handed over, each with the live frame it was last read in.
  // The live view goes on reading a label in front of it, and the caller has
  // had its say on that one: a scan it refused, or a confirm the user
  // cancelled, isn't sent again frame after frame -- nor each time another
  // code in view (the maker's QR, the label next to it) is read in between.
  // Taken out of view a moment and shown again, photographed, or Retry, it is
  // sent again. Frames only count while the live view runs, so the time the
  // caller spends on a scan doesn't take a label out of view.
  const handedOverRef = useRef(new Map<string, number>());
  const frameRef = useRef(0);
  const [holdingPayload, setHoldingPayload] = useState(false);
  // A photo still decoding when the scanner closes (Back, Cancel) must not
  // reach a caller that has moved on.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // A photo taken while the caller was busy, waiting for it to be free. The
  // native camera app is open a while, and the caller can turn busy meanwhile
  // (a spool list refetching as the page gets focus back): the photo is
  // handed over once it's free, not dropped.
  const [waitingPhoto, setWaitingPhoto] = useState<string | null>(null);
  const busyRef = useRef(busy);
  busyRef.current = busy;

  const isBusy = busy || decoding || waitingPhoto !== null;

  const handOver = (text: string) => {
    setPhotoError(null);
    onDecode(text);
    // Held only once the caller has it: had it thrown, the next frame offers
    // the label again.
    handedOverRef.current.set(text, frameRef.current);
    setHoldingPayload(true);
  };

  const handleLiveFrame = (text: string | null) => {
    const frame = ++frameRef.current;
    if (!text) return;
    const lastRead = handedOverRef.current.get(text);
    if (lastRead !== undefined && frame - lastRead <= OUT_OF_VIEW_FRAMES) {
      handedOverRef.current.set(text, frame); // still in front of the camera
      return;
    }
    handOver(text);
  };

  const retry = () => {
    handedOverRef.current.clear();
    setHoldingPayload(false);
  };

  useEffect(() => {
    if (waitingPhoto === null || busy) return;
    setWaitingPhoto(null);
    handOver(waitingPhoto);
    // handOver is recreated each render; only the caller turning free matters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, waitingPhoto]);

  const handlePhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // allow re-picking the same file; the File is GC'd after decode
    // Only one photo at a time. The caller being busy now is no reason to
    // drop one: the button was enabled when it was tapped.
    if (!file || decoding || waitingPhoto !== null) return;
    setPhotoError(null);
    setDecoding(true);
    try {
      const text = await decodeImageBlob(file);
      if (!mountedRef.current) return;
      if (!text) setPhotoError(t('inventory.qrAssign.noQrInPhoto'));
      else if (busyRef.current) setWaitingPhoto(text);
      else handOver(text);
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

      {/* Primary: live camera scan. When there is no camera to use, a hint
          says why — the photo-capture fallback below covers every case.
          Phone width at most, so the photo button stays in view in a wide
          dialog too. */}
      {cameraIssue ? (
        <p className="text-xs text-amber-400 text-center">
          {t(cameraIssue === 'insecure' ? 'inventory.qrAssign.cameraNeedsHttps' : 'inventory.qrAssign.cameraUnavailable')}
        </p>
      ) : (
        <div className="relative mx-auto w-full max-w-sm">
          <LiveScanner onFrame={handleLiveFrame} onError={setCameraIssue} paused={isBusy} />
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

      {/* A label still in view was handed over already; scan it again on request. */}
      {!cameraIssue && holdingPayload && !isBusy && (
        <button
          type="button"
          onClick={retry}
          className="mx-auto flex items-center gap-1.5 text-xs text-bambu-gray hover:text-white transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          {t('common.retry')}
        </button>
      )}
    </div>
  );
}
