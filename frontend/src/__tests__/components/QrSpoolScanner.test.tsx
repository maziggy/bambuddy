/**
 * The shared spool QR scanner (#1574): its live loop keeps scanning after a
 * frame or a callback fails, it releases the camera when it closes, and a photo
 * still decoding when it closes is not handed to a caller that moved on.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../utils';

const jsQR = vi.hoisted(() => vi.fn());
vi.mock('jsqr', () => ({ default: jsQR }));

import { QrSpoolScanner } from '../../components/QrSpoolScanner';

const PAYLOAD = 'https://bambuddy.local/inventory?spool=9';

function readableContext() {
  return {
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4) })),
  };
}

describe('QrSpoolScanner', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    jsQR.mockReset();
  });

  describe('live camera', () => {
    let track: { stop: ReturnType<typeof vi.fn>; applyConstraints: ReturnType<typeof vi.fn> };
    let ctx: ReturnType<typeof readableContext>;

    beforeEach(() => {
      track = { stop: vi.fn(), applyConstraints: vi.fn().mockResolvedValue(undefined) };
      const stream = { getTracks: () => [track], getVideoTracks: () => [track] };
      vi.stubGlobal('navigator', { ...navigator, mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } });
      vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
      vi.spyOn(HTMLMediaElement.prototype, 'readyState', 'get').mockReturnValue(4); // HAVE_ENOUGH_DATA
      vi.spyOn(HTMLVideoElement.prototype, 'videoWidth', 'get').mockReturnValue(640);
      vi.spyOn(HTMLVideoElement.prototype, 'videoHeight', 'get').mockReturnValue(480);
      ctx = readableContext();
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => ctx as unknown as RenderingContext);
      jsQR.mockReturnValue({ data: PAYLOAD });
    });

    it('keeps scanning after a frame that could not be read and a callback that threw', async () => {
      // No platform decoder: frames go through the canvas and jsQR.
      vi.stubGlobal('BarcodeDetector', undefined);
      ctx.drawImage.mockImplementationOnce(() => {
        throw new DOMException('no frame yet', 'InvalidStateError');
      });
      const onDecode = vi.fn().mockImplementationOnce(() => {
        throw new Error('the caller failed');
      });

      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      // Frame 1 can't be drawn, frame 2's callback throws, frame 3 still arrives.
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2), { timeout: 3000 });
      expect(onDecode).toHaveBeenLastCalledWith(PAYLOAD);
    });

    it('scans with jsQR where the platform decoder cannot be built for QR codes', async () => {
      vi.stubGlobal(
        'BarcodeDetector',
        class {
          constructor() {
            throw new TypeError('qr_code is not supported');
          }
        },
      );
      const onDecode = vi.fn();

      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      await waitFor(() => expect(onDecode).toHaveBeenCalledWith(PAYLOAD), { timeout: 3000 });
    });

    it('scans nothing while the caller is busy', async () => {
      vi.stubGlobal('BarcodeDetector', undefined);
      const onDecode = vi.fn();

      render(<QrSpoolScanner onDecode={onDecode} busy error={null} />);

      await new Promise((r) => setTimeout(r, 500));
      expect(onDecode).not.toHaveBeenCalled();
    });

    it('stops the camera when it closes', async () => {
      vi.stubGlobal('BarcodeDetector', undefined);
      const { unmount } = render(<QrSpoolScanner onDecode={vi.fn()} busy={false} error={null} />);
      await waitFor(() => expect(HTMLMediaElement.prototype.play).toHaveBeenCalled());

      unmount();

      expect(track.stop).toHaveBeenCalled();
    });
  });

  describe('photo fallback', () => {
    function pickPhoto() {
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      fireEvent.change(input, { target: { files: [new File(['qr'], 'qr.jpg', { type: 'image/jpeg' })] } });
    }

    beforeEach(() => {
      // No camera in jsdom: the live view hides itself and the photo path is all there is.
      vi.stubGlobal('BarcodeDetector', undefined);
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
        () => readableContext() as unknown as RenderingContext,
      );
    });

    it('hands a decoded photo to the caller', async () => {
      vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
      jsQR.mockReturnValue({ data: PAYLOAD });
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      pickPhoto();

      await waitFor(() => expect(onDecode).toHaveBeenCalledWith(PAYLOAD));
    });

    it('says so when the photo has no QR code', async () => {
      vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
      jsQR.mockReturnValue(null);
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      pickPhoto();

      expect(await screen.findByText(/No QR code found in the photo/)).toBeInTheDocument();
      expect(onDecode).not.toHaveBeenCalled();
    });

    it('drops a photo that finishes decoding after the scanner closed', async () => {
      let open: (bitmap: unknown) => void = () => {};
      vi.stubGlobal(
        'createImageBitmap',
        vi.fn(() => new Promise((resolve) => { open = resolve; })),
      );
      jsQR.mockReturnValue({ data: PAYLOAD });
      const onDecode = vi.fn();
      const { unmount } = render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      pickPhoto();
      unmount();
      open({ width: 10, height: 10, close: vi.fn() });
      await new Promise((r) => setTimeout(r, 50));

      expect(onDecode).not.toHaveBeenCalled();
    });

    it("shows the caller's message about the last scan", () => {
      render(<QrSpoolScanner onDecode={vi.fn()} busy={false} error="That QR code is not a spool label." />);
      expect(screen.getByText('That QR code is not a spool label.')).toBeInTheDocument();
    });
  });
});
