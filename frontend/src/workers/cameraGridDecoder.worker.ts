/**
 * Camera Grid Decoder Worker
 *
 * Offloads JPEG → ImageBitmap decoding from the main thread.
 * Receives raw JPEG bytes, decodes via createImageBitmap, and transfers
 * the resulting ImageBitmap back to the main thread for drawing.
 *
 * Optimizations:
 *   - Skips decoding for off-screen cameras (IntersectionObserver visibility)
 *   - Drops stale frames via per-printer generation counter — if a newer
 *     frame arrives while decoding, the old result is discarded
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
const frameGen = new Map<number, number>(); // printerId → latest generation

ctx.onmessage = async (e: MessageEvent) => {
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

    case 'frame': {
      const printerId = msg.printerId as number;
      const jpeg = msg.jpeg as ArrayBuffer;

      if (!visibleSet.has(printerId)) break;

      // Bump generation — any in-flight decode for this printer becomes stale
      const gen = (frameGen.get(printerId) ?? 0) + 1;
      frameGen.set(printerId, gen);

      try {
        const blob = new Blob([jpeg], { type: 'image/jpeg' });
        const bitmap = await createImageBitmap(blob);

        // A newer frame arrived while we were decoding — discard
        if (frameGen.get(printerId) !== gen) {
          bitmap.close();
          break;
        }

        ctx.postMessage(
          { type: 'frame', printerId, bitmap },
          [bitmap],
        );
      } catch {
        // Invalid JPEG — skip
      }
      break;
    }
  }
};
