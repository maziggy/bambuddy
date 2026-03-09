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

const ctx = self as unknown as { postMessage(msg: unknown, transfer: Transferable[]): void; onmessage: ((e: MessageEvent) => void) | null };

const visibleSet = new Set<number>();
const pendingFrame = new Map<number, ArrayBuffer>(); // latest JPEG waiting to decode
const decoding = new Map<number, boolean>();          // whether decode is in-flight

function tryDecode(printerId: number): void {
  if (decoding.get(printerId)) return;

  const jpeg = pendingFrame.get(printerId);
  if (!jpeg) return;
  pendingFrame.delete(printerId);

  decoding.set(printerId, true);
  const blob = new Blob([jpeg], { type: 'image/jpeg' });
  createImageBitmap(blob).then(
    (bitmap) => {
      ctx.postMessage(
        { type: 'frame', printerId, bitmap },
        [bitmap],
      );
      decoding.set(printerId, false);
      tryDecode(printerId);
    },
    () => {
      // Invalid JPEG — skip
      decoding.set(printerId, false);
      tryDecode(printerId);
    },
  );
}

ctx.onmessage = (e: MessageEvent) => {
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
    ctx.postMessage({ type: 'error', error: String(err) }, []);
  }
};
