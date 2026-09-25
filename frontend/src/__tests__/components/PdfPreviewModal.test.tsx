/**
 * Tests for PdfPreviewModal (#2976).
 *
 * pdf.js cannot rasterise inside jsdom (no real canvas), so the library is
 * mocked at the module boundary; the tests cover the modal's own logic —
 * loading, page navigation, error/size fallbacks, zoom input and fullscreen.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PdfPreviewModal } from '../../components/PdfPreviewModal';

const pdfjsMocks = vi.hoisted(() => {
  const render = vi.fn(() => ({ promise: Promise.resolve(), cancel: vi.fn() }));
  const defaultViewport = ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale });
  const getViewport = vi.fn(defaultViewport);
  const getPage = vi.fn(async () => ({ getViewport, render }));
  const getDocument = vi.fn(() => ({
    promise: Promise.resolve({ numPages: 3, getPage }),
    destroy: vi.fn(),
  }));
  return { render, defaultViewport, getViewport, getPage, getDocument };
});

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: { workerSrc: '' },
  getDocument: pdfjsMocks.getDocument,
}));

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: 'pdf.worker.min.mjs' }));

vi.mock('../../api/client', () => ({
  api: {
    getLibraryFileDownloadUrl: vi.fn((id: number) => `http://test/library/files/${id}/download`),
  },
  getAuthToken: () => null,
}));

const mockOnClose = vi.fn();

function renderModal(props: Partial<Parameters<typeof PdfPreviewModal>[0]> = {}) {
  return render(
    <PdfPreviewModal
      libraryFileId={7}
      filename="drawing.pdf"
      fileSize={1024}
      onClose={mockOnClose}
      {...props}
    />,
  );
}

// jsdom lays nothing out, so the fit scale bottoms out at the 0.1 floor and
// the 600pt page is shown 60px wide at zoom 1.
const CSS_WIDTH_AT_ZOOM_1 = 60;

async function renderLoadedModal(props: Partial<Parameters<typeof PdfPreviewModal>[0]> = {}) {
  const utils = renderModal(props);
  await screen.findByText('Page 1 of 3');
  await waitFor(() => expect(pdfjsMocks.render).toHaveBeenCalled());
  const page = screen.getByTestId('pdf-preview-page');
  const canvas = page.querySelector('canvas') as HTMLCanvasElement;
  await waitFor(() => expect(canvas.style.width).toBe(`${CSS_WIDTH_AT_ZOOM_1}px`));
  return { ...utils, page, canvas };
}

function lastRenderScale(): number {
  const calls = pdfjsMocks.getViewport.mock.calls;
  return calls[calls.length - 1][0].scale;
}

// A wheel event with ctrlKey, as Ctrl+wheel and a trackpad pinch both arrive.
function wheelWithCtrl(target: Element, deltaY: number) {
  return fireEvent.wheel(target, { deltaY, ctrlKey: true, clientX: 30, clientY: 40 });
}

describe('PdfPreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // clearAllMocks keeps implementations, but a test that swaps the page
    // geometry would otherwise leak it into every test after it.
    pdfjsMocks.getViewport.mockImplementation(pdfjsMocks.defaultViewport);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 })),
    );
    // The setup-file stub is wiped by unstubAllGlobals below; keep one here.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows the page indicator once the document loads', async () => {
    renderModal();
    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument();
    expect(pdfjsMocks.render).toHaveBeenCalled();
  });

  it('navigates between pages', async () => {
    const user = userEvent.setup();
    renderModal();
    await screen.findByText('Page 1 of 3');

    await user.click(screen.getByRole('button', { name: 'Next page' }));
    expect(await screen.findByText('Page 2 of 3')).toBeInTheDocument();
    expect(pdfjsMocks.getPage).toHaveBeenLastCalledWith(2);

    await user.click(screen.getByRole('button', { name: 'Previous page' }));
    expect(await screen.findByText('Page 1 of 3')).toBeInTheDocument();
  });

  it('shows an error message when the document cannot be parsed', async () => {
    pdfjsMocks.getDocument.mockReturnValueOnce({ promise: Promise.reject(new Error('bad pdf')), destroy: vi.fn() } as never);
    renderModal();
    expect(await screen.findByText('This file cannot be previewed.')).toBeInTheDocument();
  });

  it('points pdf.js at the resources it fetches at runtime', async () => {
    renderModal();
    await screen.findByText('Page 1 of 3');

    // Unset, CJK text has no CMaps, non-embedded fonts no font data, and the
    // JPEG2000/JBIG2/ICC decoders no wasm — all of which fail silently.
    expect(pdfjsMocks.getDocument).toHaveBeenCalledWith(
      expect.objectContaining({
        cMapUrl: '/assets/pdfjs/cmaps/',
        iccUrl: '/assets/pdfjs/iccs/',
        standardFontDataUrl: '/assets/pdfjs/standard_fonts/',
        wasmUrl: '/assets/pdfjs/wasm/',
      }),
    );
  });

  it('destroys a loading task that resolved after the modal closed', async () => {
    // Closing during the fetch/import window used to leave cleanup holding a
    // null task, and the pdf.js worker it later started ran on forever.
    let deliver: (response: Response) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => (deliver = resolve))),
    );
    const destroy = vi.fn();
    pdfjsMocks.getDocument.mockReturnValueOnce({ promise: new Promise(() => {}), destroy } as never);

    const { unmount } = renderModal();
    unmount();
    deliver(new Response(new Uint8Array([1, 2, 3]), { status: 200 }));

    await waitFor(() => expect(destroy).toHaveBeenCalledTimes(1));
  });

  it('refuses oversized files without fetching them', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    renderModal({ fileSize: 500 * 1024 * 1024 });

    expect(await screen.findByText(/too large to preview/)).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  describe('zoom', () => {
    it('zooms on Ctrl+wheel and keeps the browser from zooming the page', async () => {
      const { page, canvas } = await renderLoadedModal();
      const scaleBefore = lastRenderScale();

      const notPrevented = wheelWithCtrl(page, -100);

      expect(notPrevented).toBe(false);
      // Immediate feedback: the raster is scaled by CSS by exactly one step.
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 1.25, 5);
      // Then the page is re-rasterised at the new scale once the wheel settles.
      await waitFor(() => expect(lastRenderScale()).toBeCloseTo(scaleBefore * 1.25, 5));
      expect(pdfjsMocks.render).toHaveBeenCalledTimes(2);
    });

    it('zooms out on Ctrl+wheel down', async () => {
      const { page, canvas } = await renderLoadedModal();

      wheelWithCtrl(page, 100);

      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 / 1.25, 5);
    });

    it('turns a pinch (many small ctrlKey deltas) into a smooth ramp', async () => {
      const { page, canvas } = await renderLoadedModal();

      for (let i = 0; i < 10; i++) wheelWithCtrl(page, -3);

      const width = parseFloat(canvas.style.width);
      expect(width).toBeGreaterThan(CSS_WIDTH_AT_ZOOM_1);
      // Ten pixels of pinch is one wheel notch (30px clamp → one step), not ten.
      expect(width).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 1.25, 5);
    });

    it('clamps to the zoom range', async () => {
      const { page, canvas } = await renderLoadedModal();

      for (let i = 0; i < 40; i++) wheelWithCtrl(page, -100);
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 4, 5);

      for (let i = 0; i < 40; i++) wheelWithCtrl(page, 100);
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 0.4, 5);
    });

    it('zooms on a plain wheel while the page fits the viewport', async () => {
      const { page, canvas } = await renderLoadedModal();
      // jsdom reports every scroll metric as 0: nothing to scroll.
      const notPrevented = fireEvent.wheel(page, { deltaY: -100 });

      expect(notPrevented).toBe(false);
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 1.25, 5);
    });

    it('lets a plain wheel scroll once the page overflows the viewport', async () => {
      const { page, canvas } = await renderLoadedModal();
      Object.defineProperty(page, 'scrollHeight', { configurable: true, value: 2000 });
      Object.defineProperty(page, 'clientHeight', { configurable: true, value: 500 });

      const notPrevented = fireEvent.wheel(page, { deltaY: -100 });

      expect(notPrevented).toBe(true);
      expect(canvas.style.width).toBe(`${CSS_WIDTH_AT_ZOOM_1}px`);
    });

    it('zooms with the keyboard: + and - step, 0 resets', async () => {
      const { canvas } = await renderLoadedModal();

      fireEvent.keyDown(window, { key: '+' });
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 1.25, 5);

      fireEvent.keyDown(window, { key: '-' });
      fireEvent.keyDown(window, { key: '-' });
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 / 1.25, 5);

      fireEvent.keyDown(window, { key: '0' });
      expect(canvas.style.width).toBe(`${CSS_WIDTH_AT_ZOOM_1}px`);
    });

    it('leaves Ctrl+plus / Ctrl+0 to the browser', async () => {
      const { canvas } = await renderLoadedModal();

      fireEvent.keyDown(window, { key: '+', ctrlKey: true });
      fireEvent.keyDown(window, { key: '0', metaKey: true });

      expect(canvas.style.width).toBe(`${CSS_WIDTH_AT_ZOOM_1}px`);
    });

    it('pinch-zooms with two touch pointers', async () => {
      // jsdom has no PointerEvent, and testing-library then falls back to a
      // plain Event without pointerId / pointerType.
      class FakePointerEvent extends MouseEvent {
        pointerId: number;
        pointerType: string;
        constructor(type: string, init: PointerEventInit = {}) {
          super(type, init);
          this.pointerId = init.pointerId ?? 0;
          this.pointerType = init.pointerType ?? '';
        }
      }
      Object.defineProperty(window, 'PointerEvent', { configurable: true, value: FakePointerEvent });
      const { page, canvas } = await renderLoadedModal();

      fireEvent.pointerDown(page, { pointerId: 1, pointerType: 'touch', clientX: 100, clientY: 100 });
      fireEvent.pointerDown(page, { pointerId: 2, pointerType: 'touch', clientX: 200, clientY: 100 });
      // First move sets the reference distance, the second one spreads it.
      fireEvent.pointerMove(page, { pointerId: 2, pointerType: 'touch', clientX: 200, clientY: 100 });
      fireEvent.pointerMove(page, { pointerId: 2, pointerType: 'touch', clientX: 300, clientY: 100 });

      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 2, 5);

      fireEvent.pointerUp(page, { pointerId: 2, pointerType: 'touch' });
      fireEvent.pointerUp(page, { pointerId: 1, pointerType: 'touch' });
      delete (window as { PointerEvent?: unknown }).PointerEvent;
    });

    it('still offers the zoom buttons', async () => {
      const user = userEvent.setup();
      const { canvas } = await renderLoadedModal();

      await user.click(screen.getByRole('button', { name: 'Zoom in' }));
      expect(parseFloat(canvas.style.width)).toBeCloseTo(CSS_WIDTH_AT_ZOOM_1 * 1.25, 5);

      await user.click(screen.getByRole('button', { name: 'Zoom out' }));
      expect(canvas.style.width).toBe(`${CSS_WIDTH_AT_ZOOM_1}px`);
    });
  });

  describe('fullscreen', () => {
    const requestFullscreen = vi.fn();
    const exitFullscreen = vi.fn();

    beforeEach(() => {
      requestFullscreen.mockReset().mockResolvedValue(undefined);
      exitFullscreen.mockReset().mockResolvedValue(undefined);
      Object.defineProperty(document, 'fullscreenEnabled', { configurable: true, value: true });
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: null, writable: true });
      Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exitFullscreen });
      Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: requestFullscreen });
    });

    afterEach(() => {
      delete (document as { fullscreenEnabled?: boolean }).fullscreenEnabled;
      delete (document as { fullscreenElement?: Element | null }).fullscreenElement;
      delete (document as { exitFullscreen?: () => Promise<void> }).exitFullscreen;
      delete (HTMLElement.prototype as { requestFullscreen?: () => Promise<void> }).requestFullscreen;
    });

    // The browser flips fullscreenElement and fires fullscreenchange; the
    // mocks do neither, so the test plays the browser's part.
    function enterFullscreen(panel: Element) {
      (document as { fullscreenElement: Element | null }).fullscreenElement = panel;
      act(() => {
        document.dispatchEvent(new Event('fullscreenchange'));
      });
    }

    it('double-click on the page toggles fullscreen', async () => {
      const { page } = await renderLoadedModal();
      const panel = screen.getByText('drawing.pdf').closest('.flex-col') as HTMLElement;

      fireEvent.doubleClick(page);
      expect(requestFullscreen).toHaveBeenCalledTimes(1);
      expect(requestFullscreen.mock.instances[0]).toBe(panel);

      enterFullscreen(panel);
      expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();

      fireEvent.doubleClick(page);
      expect(exitFullscreen).toHaveBeenCalledTimes(1);
    });

    it('offers a fullscreen button in the header', async () => {
      const user = userEvent.setup();
      await renderLoadedModal();

      await user.click(screen.getByRole('button', { name: 'Fullscreen' }));
      expect(requestFullscreen).toHaveBeenCalledTimes(1);
    });

    it('re-syncs when the browser leaves fullscreen on Esc', async () => {
      const { page } = await renderLoadedModal();
      const panel = screen.getByText('drawing.pdf').closest('.flex-col') as HTMLElement;

      fireEvent.doubleClick(page);
      enterFullscreen(panel);
      expect(screen.getByRole('button', { name: 'Exit fullscreen' })).toBeInTheDocument();

      // Esc while fullscreen is the browser's: the modal must not close.
      fireEvent.keyDown(window, { key: 'Escape' });
      expect(mockOnClose).not.toHaveBeenCalled();

      enterFullscreen(null as unknown as Element);
      expect(screen.getByRole('button', { name: 'Fullscreen' })).toBeInTheDocument();

      fireEvent.keyDown(window, { key: 'Escape' });
      expect(mockOnClose).toHaveBeenCalledTimes(1);
    });
  });

  describe('grid thumbnail snapshot', () => {
    // jsdom has no 2D backend: the snapshot helper draws into an offscreen
    // canvas and hands the result to toBlob, so both have to be stood in for.
    function stubCanvas2d(blob: Blob | null = new Blob(['png'], { type: 'image/png' })) {
      const context = { fillStyle: '', fillRect: vi.fn(), drawImage: vi.fn() };
      vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
        context as unknown as CanvasRenderingContext2D,
      );
      vi.spyOn(HTMLCanvasElement.prototype, 'toBlob').mockImplementation((callback) => callback(blob));
      return context;
    }

    afterEach(() => {
      vi.restoreAllMocks();
    });

    it('hands the first page to onSnapshot as a PNG', async () => {
      stubCanvas2d();
      const onSnapshot = vi.fn();

      await renderLoadedModal({ onSnapshot });

      await waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(1));
      const blob = onSnapshot.mock.calls[0][0] as Blob;
      expect(blob.type).toBe('image/png');
    });

    it('sends it once, not on every re-render of page 1', async () => {
      stubCanvas2d();
      const user = userEvent.setup();
      const onSnapshot = vi.fn();
      await renderLoadedModal({ onSnapshot });
      await waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(1));

      await user.click(screen.getByRole('button', { name: 'Next page' }));
      await screen.findByText('Page 2 of 3');
      await user.click(screen.getByRole('button', { name: 'Previous page' }));
      await screen.findByText('Page 1 of 3');

      expect(onSnapshot).toHaveBeenCalledTimes(1);
    });

    it('stays quiet when the canvas yields no blob', async () => {
      stubCanvas2d(null);
      const onSnapshot = vi.fn();

      await renderLoadedModal({ onSnapshot });
      await waitFor(() => expect(pdfjsMocks.render).toHaveBeenCalled());

      expect(onSnapshot).not.toHaveBeenCalled();
    });
  });

  it('keeps the raster inside the canvas area iOS Safari will back', async () => {
    // Past roughly 16.7M pixels iOS Safari hands back a blank canvas instead
    // of failing, so resolution is what gives way, not the page.
    vi.stubGlobal('devicePixelRatio', 2);
    pdfjsMocks.getViewport.mockImplementation(({ scale }: { scale: number }) => ({
      width: 30000 * scale,
      height: 30000 * scale,
    }));

    renderModal();
    await screen.findByText('Page 1 of 3');
    await waitFor(() => expect(pdfjsMocks.render).toHaveBeenCalled());

    // Unclamped this would be 0.1 (fit floor) x 2 (dpr) = 6000 x 6000 px.
    const scale = lastRenderScale();
    expect(scale).toBeLessThan(0.2);
    expect(30000 * scale * (30000 * scale)).toBeLessThanOrEqual(16 * 1024 * 1024);
  });
});
