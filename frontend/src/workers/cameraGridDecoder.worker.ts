/// <reference lib="webworker" />
declare const self: DedicatedWorkerGlobalScope;

/**
 * Camera Grid Decoder Worker
 *
 * Offloads JPEG → ImageBitmap decoding from the main thread.
 * Receives raw JPEG bytes, decodes via createImageBitmap, and transfers
 * the resulting ImageBitmap back to the main thread for drawing.
 *
 * Optimizations:
 *   - Skips decoding for off-screen cameras (IntersectionObserver visibility)
 *   - Serialized decode pipeline per printer — max 1 concurrent
 *     createImageBitmap per printer. New frames during decode replace the
 *     pending buffer (zero wasted CPU).
 *
 * Protocol (main → worker):
 *   { type: 'frame', printerId, jpeg }         — decode a JPEG frame
 *   { type: 'visibility', printerId, visible } — update visibility (IntersectionObserver)
 *
 * Protocol (worker → main):
 *   { type: 'frame', printerId, bitmap }       — decoded ImageBitmap (transferred)
 */

const visibleSet = new Set<number>();
const pendingFrame = new Map<number, ArrayBuffer>(); // latest JPEG waiting to decode
const decoding = new Map<number, boolean>();          // whether decode is in-flight

let totalDecodeErrors = 0;
let totalDecodeSuccess = 0;
let lastErrorReportTime = 0;

function tryDecode(printerId: number): void {
  if (decoding.get(printerId)) return;

  const jpeg = pendingFrame.get(printerId);
  if (!jpeg) return;
  pendingFrame.delete(printerId);

  decoding.set(printerId, true);
  const blob = new Blob([jpeg], { type: 'image/jpeg' });
  createImageBitmap(blob).then(
    (bitmap) => {
      totalDecodeSuccess++;
      self.postMessage(
        { type: 'frame', printerId, bitmap },
        [bitmap],
      );
      decoding.set(printerId, false);
      tryDecode(printerId);
    },
    () => {
      totalDecodeErrors++;
      // Report decode errors back to main thread, throttled to once per 5s
      const now = Date.now();
      if (now - lastErrorReportTime > 5000) {
        lastErrorReportTime = now;
        self.postMessage({
          type: 'decodeError',
          printerId,
          totalErrors: totalDecodeErrors,
          totalSuccess: totalDecodeSuccess,
          visibleCount: visibleSet.size,
        });
      }
      decoding.set(printerId, false);
      tryDecode(printerId);
    },
  );
}

self.onmessage = (e: MessageEvent) => {
  try {
    const msg = e.data;

    switch (msg.type) {
      case 'visibility': {
        if (msg.visible) {
          visibleSet.add(msg.printerId);
        } else {
          visibleSet.delete(msg.printerId);
        }
        break;
      }

      case 'clear': {
        visibleSet.clear();
        pendingFrame.clear();
        decoding.clear();
        totalDecodeErrors = 0;
        totalDecodeSuccess = 0;
        break;
      }

      case 'ping': {
        self.postMessage({
          type: 'pong',
          visibleCount: visibleSet.size,
          pendingCount: pendingFrame.size,
          decodingCount: [...decoding.values()].filter(Boolean).length,
          totalDecodeErrors,
          totalDecodeSuccess,
        });
        break;
      }

      case 'frame': {
        const printerId = msg.printerId as number;
        const jpeg = msg.jpeg as ArrayBuffer;

        if (!visibleSet.has(printerId)) break;

        // Store latest frame (replaces any previous pending frame)
        pendingFrame.set(printerId, jpeg);
        tryDecode(printerId);
        break;
      }
    }
  } catch (err) {
    self.postMessage({ type: 'error', error: String(err) }, []);
  }
};
