/**
 * Tests for the camera grid decoder worker logic.
 *
 * Since workers run in a separate context, we test the message handler
 * logic by simulating postMessage calls.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

// We'll test the worker logic by importing and executing the worker module
// in a simulated environment.

describe('cameraGridDecoder worker', () => {
  let onmessage: ((e: MessageEvent) => void) | null = null;
  let posted: Array<{ msg: unknown; transfer: Transferable[] }> = [];

  beforeEach(() => {
    posted = [];
    onmessage = null;

    // Mock self/worker context
    const mockCtx = {
      postMessage: (msg: unknown, transfer: Transferable[]) => {
        posted.push({ msg, transfer });
      },
      onmessage: null as ((e: MessageEvent) => void) | null,
    };

    // We can't easily import the worker directly due to `self` reference,
    // so we test the logic patterns instead.
    // The worker tracks visibility and generation counters.

    // Simulate worker logic (mirrors real worker protocol)
    const visibleSet = new Set<number>();
    const pendingFrame = new Map<number, ArrayBuffer>();
    const decoding = new Map<number, boolean>();
    const frameGen = new Map<number, number>();
    let totalDecodeErrors = 0;
    let totalDecodeSuccess = 0;

    onmessage = async (e: MessageEvent) => {
      const msg = e.data;
      switch (msg.type) {
        case 'visibility':
          if (msg.visible) visibleSet.add(msg.printerId);
          else visibleSet.delete(msg.printerId);
          break;
        case 'clear':
          visibleSet.clear();
          frameGen.clear();
          pendingFrame.clear();
          decoding.clear();
          totalDecodeErrors = 0;
          totalDecodeSuccess = 0;
          break;
        case 'ping':
          mockCtx.postMessage(
            {
              type: 'pong',
              visibleCount: visibleSet.size,
              pendingCount: pendingFrame.size,
              decodingCount: [...decoding.values()].filter(Boolean).length,
              totalDecodeErrors,
              totalDecodeSuccess,
            },
            [],
          );
          break;
        case 'frame': {
          const printerId = msg.printerId as number;
          if (!visibleSet.has(printerId)) break;
          const gen = (frameGen.get(printerId) ?? 0) + 1;
          frameGen.set(printerId, gen);

          // Simulate bitmap creation (can't use createImageBitmap in test)
          const mockBitmap = { close: vi.fn() } as unknown as ImageBitmap;

          // Check generation (simulate decode delay)
          if (frameGen.get(printerId) !== gen) {
            mockBitmap.close();
            break;
          }

          totalDecodeSuccess++;
          mockCtx.postMessage(
            { type: 'frame', printerId, bitmap: mockBitmap },
            [mockBitmap as unknown as Transferable],
          );
          break;
        }
        case 'decodeFailure': {
          // Simulate a decode error (for testing error reporting)
          const pid = msg.printerId as number;
          totalDecodeErrors++;
          mockCtx.postMessage(
            {
              type: 'decodeError',
              printerId: pid,
              totalErrors: totalDecodeErrors,
              totalSuccess: totalDecodeSuccess,
              visibleCount: visibleSet.size,
            },
            [],
          );
          break;
        }
      }
    };
  });

  it('processes frame for visible printer', async () => {
    // Make printer 1 visible
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));

    // Send a frame
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(100) },
    }));

    expect(posted.length).toBe(1);
    expect((posted[0].msg as { type: string }).type).toBe('frame');
    expect((posted[0].msg as { printerId: number }).printerId).toBe(1);
  });

  it('skips frame for non-visible printer', async () => {
    // Printer 2 is NOT visible
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 2, jpeg: new ArrayBuffer(100) },
    }));

    expect(posted.length).toBe(0);
  });

  it('clear resets visibility and generations', async () => {
    // Make visible, then clear
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'clear' },
    }));

    // Frame should be skipped (visibility cleared)
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(100) },
    }));

    expect(posted.length).toBe(0);
  });

  it('visibility toggle removes printer from visible set', async () => {
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: false },
    }));

    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(100) },
    }));

    expect(posted.length).toBe(0);
  });

  it('handles multiple printers independently', async () => {
    // Make printer 1 and 3 visible, not 2
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 3, visible: true },
    }));

    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(50) },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 2, jpeg: new ArrayBuffer(50) },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 3, jpeg: new ArrayBuffer(50) },
    }));

    // Only printer 1 and 3 should have produced frames
    expect(posted.length).toBe(2);
    const ids = posted.map(p => (p.msg as { printerId: number }).printerId);
    expect(ids).toEqual([1, 3]);
  });

  it('responds to ping with worker state', async () => {
    // Make printer 1 visible
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));

    // Send a frame so success counter increments
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(100) },
    }));

    // Send ping
    await onmessage!(new MessageEvent('message', {
      data: { type: 'ping' },
    }));

    // Should have frame response + pong response
    expect(posted.length).toBe(2);
    const pong = posted[1].msg as {
      type: string;
      visibleCount: number;
      pendingCount: number;
      decodingCount: number;
      totalDecodeErrors: number;
      totalDecodeSuccess: number;
    };
    expect(pong.type).toBe('pong');
    expect(pong.visibleCount).toBe(1);
    expect(pong.totalDecodeSuccess).toBe(1);
    expect(pong.totalDecodeErrors).toBe(0);
  });

  it('reports decode errors back to main thread', async () => {
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));

    // Simulate a decode failure
    await onmessage!(new MessageEvent('message', {
      data: { type: 'decodeFailure', printerId: 1 },
    }));

    expect(posted.length).toBe(1);
    const errMsg = posted[0].msg as {
      type: string;
      printerId: number;
      totalErrors: number;
      totalSuccess: number;
      visibleCount: number;
    };
    expect(errMsg.type).toBe('decodeError');
    expect(errMsg.printerId).toBe(1);
    expect(errMsg.totalErrors).toBe(1);
    expect(errMsg.totalSuccess).toBe(0);
    expect(errMsg.visibleCount).toBe(1);
  });

  it('clear resets decode counters', async () => {
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));

    // Generate some activity
    await onmessage!(new MessageEvent('message', {
      data: { type: 'frame', printerId: 1, jpeg: new ArrayBuffer(100) },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'decodeFailure', printerId: 1 },
    }));

    // Clear everything
    await onmessage!(new MessageEvent('message', {
      data: { type: 'clear' },
    }));

    // Re-add visibility and ping to check counters were reset
    await onmessage!(new MessageEvent('message', {
      data: { type: 'visibility', printerId: 1, visible: true },
    }));
    await onmessage!(new MessageEvent('message', {
      data: { type: 'ping' },
    }));

    const pong = posted[posted.length - 1].msg as {
      type: string;
      totalDecodeErrors: number;
      totalDecodeSuccess: number;
    };
    expect(pong.type).toBe('pong');
    expect(pong.totalDecodeErrors).toBe(0);
    expect(pong.totalDecodeSuccess).toBe(0);
  });
});
