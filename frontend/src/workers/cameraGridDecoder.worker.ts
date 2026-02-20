/**
 * Camera Grid Decoder Worker
 *
 * Offloads JPEG → ImageBitmap decoding from the main thread.
 * Receives raw JPEG bytes, decodes via createImageBitmap, and transfers
 * the resulting ImageBitmap back to the main thread for drawing.
 *
 * Protocol (main → worker):
 *   { type: 'frame', printerId, jpeg }         — decode a JPEG frame
 *   { type: 'visibility', printerId, visible } — update visibility (IntersectionObserver)
 *
 * Protocol (worker → main):
 *   { type: 'frame', printerId, bitmap }       — decoded ImageBitmap (transferred)
 */

const ctx = self as unknown as { postMessage(msg: unknown, transfer: Transferable[]): void; onmessage: ((e: MessageEvent) => void) | null };

const visibleSet = new Set<number>(); // printers currently visible in viewport

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

      // Skip decoding for off-screen cameras
      if (!visibleSet.has(printerId)) break;

      try {
        const blob = new Blob([jpeg], { type: 'image/jpeg' });
        const bitmap = await createImageBitmap(blob);
        // Transfer bitmap back to main thread (zero-copy)
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
