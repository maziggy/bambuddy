/**
 * The shared spool QR scanner (#1574): its live loop keeps scanning after a
 * frame or a callback fails, hands a label over once while it stays in view
 * (other codes read in between or not), says why when there's no camera,
 * releases the camera when it closes, a photo that comes back while the
 * caller is busy is handed over once it's free, and a photo still decoding
 * when it closes is not handed to a caller that moved on.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, frames, render, screen, waitFor } from '../utils';

const jsQR = vi.hoisted(() => vi.fn());
vi.mock('jsqr', () => ({ default: jsQR }));

import { QrSpoolScanner } from '../../components/QrSpoolScanner';

const PAYLOAD = 'https://bambuddy.local/inventory?spool=9';
const OTHER_PAYLOAD = 'https://bambuddy.local/inventory?spool=10';

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

      // Frame 1 can't be drawn, frame 2's callback throws, frame 3 brings the
      // same label again: the caller never had it.
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2), { timeout: 3000 });
      expect(onDecode.mock.calls).toEqual([[PAYLOAD], [PAYLOAD]]);
    });

    it('hands a label over once while it stays in view, a different one at once, the same again on Retry', async () => {
      vi.stubGlobal('BarcodeDetector', undefined);
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error="Spool #9 is archived." />);

      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(1), { timeout: 3000 });
      await frames(3);
      // The caller turned it down; the label staying in front of the camera is not sent again.
      expect(onDecode).toHaveBeenCalledTimes(1);

      await screen.findByRole('button', { name: /retry/i }).then((retry) => fireEvent.click(retry));
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2), { timeout: 3000 });

      jsQR.mockReturnValue({ data: OTHER_PAYLOAD });
      await waitFor(() => expect(onDecode).toHaveBeenLastCalledWith(OTHER_PAYLOAD), { timeout: 3000 });
      expect(onDecode).toHaveBeenCalledTimes(3);
    });

    it('hands a label over again once it was out of view a moment: showing it again is asking', async () => {
      vi.stubGlobal('BarcodeDetector', undefined);
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(1), { timeout: 3000 });

      jsQR.mockReturnValue(null); // taken away...
      await frames(9);
      jsQR.mockReturnValue({ data: PAYLOAD }); // ...and shown again

      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2), { timeout: 3000 });
    });

    it('does not hand a label over again while another code in view is read in between', async () => {
      // The label and, next to it, the maker's QR on the spool: frames read one, then the other.
      vi.stubGlobal('BarcodeDetector', undefined);
      let read = 0;
      jsQR.mockImplementation(() => ({ data: read++ % 2 === 0 ? PAYLOAD : OTHER_PAYLOAD }));
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2), { timeout: 3000 });
      await frames(4);

      expect(onDecode.mock.calls).toEqual([[PAYLOAD], [OTHER_PAYLOAD]]);
    });

    it('offers no Retry while the caller is busy with the label', async () => {
      vi.stubGlobal('BarcodeDetector', undefined);
      const onDecode = vi.fn();
      const { rerender } = render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);
      await waitFor(() => expect(onDecode).toHaveBeenCalled(), { timeout: 3000 });

      rerender(<QrSpoolScanner onDecode={onDecode} busy error={null} />);

      expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
    });

    it('says why there is no live view when the camera cannot be opened', async () => {
      vi.stubGlobal('navigator', {
        ...navigator,
        mediaDevices: { getUserMedia: vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError')) },
      });
      render(<QrSpoolScanner onDecode={vi.fn()} busy={false} error={null} />);

      expect(await screen.findByText(/No camera could be opened here/)).toBeInTheDocument();
      expect(document.querySelector('video')).toBeNull();
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

    it('hands over every photo, even of the label it just handed over: taking one is asking', async () => {
      vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
      jsQR.mockReturnValue({ data: PAYLOAD });
      const onDecode = vi.fn();
      render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      pickPhoto();
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(1));
      pickPhoto();

      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(2));
    });

    it('hands over a photo once the caller is free, when it turned busy while the photo was taken', async () => {
      vi.stubGlobal('createImageBitmap', vi.fn().mockResolvedValue({ width: 10, height: 10, close: vi.fn() }));
      jsQR.mockReturnValue({ data: PAYLOAD });
      const onDecode = vi.fn();
      const { rerender } = render(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);

      // The button was free when tapped; the caller turned busy (a list
      // refetching as the page got focus back) before the photo came back.
      rerender(<QrSpoolScanner onDecode={onDecode} busy error={null} />);
      pickPhoto();
      await new Promise((r) => setTimeout(r, 50));
      expect(onDecode).not.toHaveBeenCalled();

      rerender(<QrSpoolScanner onDecode={onDecode} busy={false} error={null} />);
      await waitFor(() => expect(onDecode).toHaveBeenCalledTimes(1));
      expect(onDecode).toHaveBeenCalledWith(PAYLOAD);
    });

    it('says the live camera needs HTTPS when the page is not a secure origin', async () => {
      vi.stubGlobal('isSecureContext', false);
      render(<QrSpoolScanner onDecode={vi.fn()} busy={false} error={null} />);

      expect(await screen.findByText(/needs Bambuddy opened over HTTPS/)).toBeInTheDocument();
      // The photo fallback is still there.
      expect(screen.getByRole('button', { name: /take photo/i })).toBeEnabled();
    });

    it('on a secure origin without camera access, says no camera could be opened, not to use HTTPS', async () => {
      // jsdom offers no camera at all, as an old or embedded browser on HTTPS might.
      vi.stubGlobal('isSecureContext', true);
      render(<QrSpoolScanner onDecode={vi.fn()} busy={false} error={null} />);

      expect(await screen.findByText(/No camera could be opened here/)).toBeInTheDocument();
      expect(screen.queryByText(/needs Bambuddy opened over HTTPS/)).not.toBeInTheDocument();
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
