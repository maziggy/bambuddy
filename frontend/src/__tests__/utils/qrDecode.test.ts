/**
 * The scanner's decoding never throws for a frame or photo it can't read, and
 * falls back to jsQR when the platform decoder is missing, can't be built or
 * fails (#1574 review).
 */

import { describe, it, expect, vi, afterEach } from 'vitest';

const jsQR = vi.hoisted(() => vi.fn());
vi.mock('jsqr', () => ({ default: jsQR }));

import { createFrameDecoder, createQrDetector, decodeCanvas, decodeImageBlob } from '../../utils/qrDecode';

function readableContext() {
  return {
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4) })),
  };
}

function canvasWith(getContext: () => unknown): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.getContext = vi.fn(getContext) as unknown as HTMLCanvasElement['getContext'];
  return canvas;
}

const video = { videoWidth: 640, videoHeight: 480 } as unknown as HTMLVideoElement;

function stubDetector(detect: () => Promise<{ rawValue: string }[]>) {
  const detectSpy = vi.fn(detect);
  vi.stubGlobal(
    'BarcodeDetector',
    class {
      detect = detectSpy;
    },
  );
  return detectSpy;
}

function stubUnbuildableDetector() {
  vi.stubGlobal(
    'BarcodeDetector',
    class {
      constructor() {
        throw new TypeError("Failed to construct 'BarcodeDetector': qr_code is not supported");
      }
    },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  jsQR.mockReset();
});

describe('createQrDetector', () => {
  it('is null where the platform has no BarcodeDetector', () => {
    vi.stubGlobal('BarcodeDetector', undefined);
    expect(createQrDetector()).toBeNull();
  });

  it('is null where the platform decoder cannot read QR codes', () => {
    stubUnbuildableDetector();
    expect(createQrDetector()).toBeNull();
  });

  it('asks the platform decoder for QR codes', () => {
    const made: unknown[] = [];
    vi.stubGlobal(
      'BarcodeDetector',
      class {
        constructor(opts: unknown) {
          made.push(opts);
        }
      },
    );
    expect(createQrDetector()).not.toBeNull();
    expect(made).toEqual([{ formats: ['qr_code'] }]);
  });
});

describe('decodeCanvas', () => {
  it("returns jsQR's reading of the frame", () => {
    jsQR.mockReturnValue({ data: 'spool=3' });
    expect(decodeCanvas(canvasWith(readableContext), video, 4, 4, 'dontInvert')).toBe('spool=3');
  });

  it('is undefined, not a throw, when the canvas cannot be read', () => {
    expect(decodeCanvas(canvasWith(() => { throw new Error('not implemented'); }), video, 4, 4, 'dontInvert')).toBeUndefined();
    expect(decodeCanvas(canvasWith(() => null), video, 4, 4, 'dontInvert')).toBeUndefined();
    const ctx = readableContext();
    ctx.drawImage.mockImplementation(() => {
      throw new DOMException('no frame yet', 'InvalidStateError');
    });
    expect(decodeCanvas(canvasWith(() => ctx), video, 4, 4, 'dontInvert')).toBeUndefined();
  });

  it('skips a frame with no size', () => {
    const canvas = canvasWith(readableContext);
    expect(decodeCanvas(canvas, video, 0, 0, 'dontInvert')).toBeUndefined();
    expect(canvas.getContext).not.toHaveBeenCalled();
  });
});

describe('createFrameDecoder', () => {
  it("uses the platform decoder's reading", async () => {
    stubDetector(async () => [{ rawValue: 'spool=1' }]);
    const decode = createFrameDecoder();
    expect(await decode(video, canvasWith(readableContext))).toBe('spool=1');
    expect(jsQR).not.toHaveBeenCalled();
  });

  it('drops a platform decoder that rejects, for jsQR on that frame and the next', async () => {
    const detect = stubDetector(async () => {
      throw new DOMException('decode failed', 'OperationError');
    });
    jsQR.mockReturnValue({ data: 'spool=2' });
    const decode = createFrameDecoder();
    const canvas = canvasWith(readableContext);

    expect(await decode(video, canvas)).toBe('spool=2');
    expect(await decode(video, canvas)).toBe('spool=2');
    expect(detect).toHaveBeenCalledTimes(1);
  });

  it('uses jsQR when the platform decoder cannot be built', async () => {
    stubUnbuildableDetector();
    jsQR.mockReturnValue({ data: 'spool=4' });
    const decode = createFrameDecoder();
    expect(await decode(video, canvasWith(readableContext))).toBe('spool=4');
  });

  it('resolves rather than rejects when nothing can read the frame', async () => {
    stubDetector(async () => {
      throw new Error('boom');
    });
    const decode = createFrameDecoder();
    await expect(decode(video, canvasWith(() => { throw new Error('no canvas'); }))).resolves.toBeUndefined();
  });
});

describe('decodeImageBlob', () => {
  const photo = new Blob(['qr'], { type: 'image/jpeg' });

  function stubBitmap() {
    const bitmap = { width: 3000, height: 2000, close: vi.fn() };
    vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue(bitmap));
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () => readableContext() as unknown as RenderingContext,
    );
    return bitmap;
  }

  it('falls back to jsQR when the platform decoder cannot be built', async () => {
    const bitmap = stubBitmap();
    stubUnbuildableDetector();
    jsQR.mockReturnValue({ data: 'spool=5' });

    expect(await decodeImageBlob(photo)).toBe('spool=5');
    // Downscaled to 1600px on the long side before jsQR reads it.
    expect(jsQR).toHaveBeenCalledWith(expect.any(Uint8ClampedArray), 1600, 1067, { inversionAttempts: 'attemptBoth' });
    expect(bitmap.close).toHaveBeenCalled();
  });

  it('falls back to jsQR when the platform decoder rejects the photo', async () => {
    stubBitmap();
    stubDetector(async () => {
      throw new DOMException('decode failed', 'OperationError');
    });
    jsQR.mockReturnValue({ data: 'spool=6' });

    expect(await decodeImageBlob(photo)).toBe('spool=6');
  });
});
