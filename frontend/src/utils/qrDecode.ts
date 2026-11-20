/**
 * QR decoding for the spool scanner (#1574): the platform BarcodeDetector where
 * there is one that reads QR codes, jsQR otherwise.
 *
 * Nothing here throws or rejects for an unreadable frame or photo -- that is a
 * frame or photo without a QR -- so a failure can't stop the live scan loop.
 */

import jsQR from 'jsqr';

// BarcodeDetector (platform QR decoder, e.g. Android Chrome) isn't in the TS DOM
// lib yet — minimal shape for the bit we use.
interface DetectedBarcode {
  rawValue: string;
}
export interface QrDetector {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
}
type BarcodeDetectorCtor = new (opts?: { formats?: string[] }) => QrDetector;

/**
 * The platform QR decoder, or null when there is none or it can't read QR
 * codes: its constructor throws for a format the platform doesn't support.
 */
export function createQrDetector(): QrDetector | null {
  const BD = (globalThis as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
  if (!BD) return null;
  try {
    return new BD({ formats: ['qr_code'] });
  } catch {
    return null;
  }
}

/**
 * Draw a frame/bitmap onto a canvas and decode a QR with jsQR. A canvas that
 * can't be drawn or read (jsdom without the canvas package, a video with no
 * frame yet) yields undefined. The caller owns the canvas so the live loop can
 * reuse one across frames.
 */
export function decodeCanvas(
  canvas: HTMLCanvasElement,
  source: CanvasImageSource,
  w: number,
  h: number,
  inversionAttempts: 'dontInvert' | 'attemptBoth',
): string | undefined {
  if (w <= 0 || h <= 0) return undefined;
  try {
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    if (!ctx) return undefined;
    ctx.drawImage(source, 0, 0, w, h);
    const img = ctx.getImageData(0, 0, w, h);
    return jsQR(img.data, w, h, { inversionAttempts })?.data ?? undefined;
  } catch {
    return undefined;
  }
}

/** jsQR's first, fast read of a photo: its long side scaled down to this. */
const PHOTO_FAST_SIDE = 1600;
/**
 * The second read, at the photo's own size up to this long side. iOS Safari
 * refuses a canvas over about 16.7 MP, which a 24 MP photo would be.
 */
const PHOTO_FULL_SIDE = 4096;

function scaledTo(bitmap: ImageBitmap, maxSide: number): [number, number] {
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  return [Math.max(1, Math.round(bitmap.width * scale)), Math.max(1, Math.round(bitmap.height * scale))];
}

/**
 * Decode a QR from a captured photo. The photo is taken with the phone's native
 * camera (file input + capture), which focuses far better than an in-page
 * getUserMedia preview. Prefer the platform decoder; fall back to jsQR, first
 * on the photo scaled down for speed, then, if that finds nothing, near its
 * own size: a small label in the photo has too few pixels per module left
 * after the downscale (#3616). Rejects only when the image itself can't be
 * opened.
 */
export async function decodeImageBlob(blob: Blob): Promise<string | undefined> {
  const bitmap = await createImageBitmap(blob, { imageOrientation: 'from-image' });
  try {
    const detector = createQrDetector();
    if (detector) {
      try {
        const codes = await detector.detect(bitmap);
        if (codes[0]?.rawValue) return codes[0].rawValue;
      } catch {
        /* the platform decoder failed on this image — jsQR gets a go */
      }
    }
    const canvas = document.createElement('canvas');
    const [w, h] = scaledTo(bitmap, PHOTO_FAST_SIDE);
    const fast = decodeCanvas(canvas, bitmap, w, h, 'attemptBoth');
    if (fast) return fast;
    const [fw, fh] = scaledTo(bitmap, PHOTO_FULL_SIDE);
    // A photo no larger than the first read has nothing more to give.
    if (fw === w && fh === h) return undefined;
    const full = decodeCanvas(canvas, bitmap, fw, fh, 'attemptBoth');
    // iOS Safari counts a canvas's memory until it is resized, not until it
    // is collected: give the 50 MB back now, for the next photo.
    canvas.width = 0;
    canvas.height = 0;
    return full;
  } finally {
    bitmap.close?.();
  }
}

/**
 * A decoder for the live preview's frames. It starts on the platform decoder
 * and drops it for jsQR the first time it throws or rejects. The returned
 * promise never rejects, so the loop waiting on it always gets to its next
 * frame.
 */
export function createFrameDecoder(): (video: HTMLVideoElement, canvas: HTMLCanvasElement) => Promise<string | undefined> {
  let detector = createQrDetector();
  return async (video, canvas) => {
    if (detector) {
      try {
        const codes = await detector.detect(video);
        return codes[0]?.rawValue;
      } catch {
        detector = null;
      }
    }
    // 'dontInvert' (cheapest) for the hot loop; the photo path uses 'attemptBoth'.
    return decodeCanvas(canvas, video, video.videoWidth, video.videoHeight, 'dontInvert');
  };
}
