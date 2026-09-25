/**
 * Tests for ImagePreviewModal (#2976).
 *
 * jsdom decodes nothing and lays nothing out, so the tests cover the modal's
 * own logic — the authenticated fetch, the size guard, zoom input and the
 * fullscreen wiring — rather than what the image looks like.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ImagePreviewModal, IMAGE_PREVIEW_MAX_BYTES } from '../../components/ImagePreviewModal';

vi.mock('../../api/client', () => ({
  api: {
    getLibraryFileDownloadUrl: vi.fn((id: number) => `http://test/library/files/${id}/download`),
  },
  getAuthToken: () => 'token-123',
}));

const mockOnClose = vi.fn();

function renderModal(props: Partial<Parameters<typeof ImagePreviewModal>[0]> = {}) {
  return render(
    <ImagePreviewModal
      libraryFileId={11}
      filename="plate.png"
      fileSize={2048}
      onClose={mockOnClose}
      {...props}
    />,
  );
}

async function renderLoadedModal(props: Partial<Parameters<typeof ImagePreviewModal>[0]> = {}) {
  const utils = renderModal(props);
  const image = (await screen.findByAltText('plate.png')) as HTMLImageElement;
  fireEvent.load(image);
  return { ...utils, image, content: screen.getByTestId('image-preview-content') };
}

function scaleOf(image: HTMLImageElement): number {
  const match = /scale\(([\d.]+)\)/.exec(image.style.transform);
  return match ? Number(match[1]) : NaN;
}

describe('ImagePreviewModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(new Uint8Array([137, 80, 78, 71]), { status: 200 })),
    );
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:image-preview'),
      revokeObjectURL: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads the file through the authenticated download URL', async () => {
    await renderLoadedModal();

    expect(fetch).toHaveBeenCalledWith('http://test/library/files/11/download', {
      headers: { Authorization: 'Bearer token-123' },
    });
    expect(screen.getByAltText('plate.png')).toHaveAttribute('src', 'blob:image-preview');
    expect(screen.getByText('plate.png')).toBeInTheDocument();
  });

  it('refuses a file too large to hold in memory', async () => {
    renderModal({ fileSize: IMAGE_PREVIEW_MAX_BYTES + 1 });

    expect(await screen.findByText(/too large to preview/i)).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('shows a message when the download fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 404 })));
    vi.spyOn(console, 'error').mockImplementation(() => {});

    renderModal();

    expect(await screen.findByText('This file cannot be previewed.')).toBeInTheDocument();
  });

  it('starts fitted to the window and zooms from the buttons', async () => {
    const user = userEvent.setup();
    const { image } = await renderLoadedModal();

    expect(scaleOf(image)).toBe(1);

    await user.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(scaleOf(image)).toBeCloseTo(1.25, 5);

    await user.click(screen.getByRole('button', { name: 'Zoom out' }));
    expect(scaleOf(image)).toBeCloseTo(1, 5);
  });

  it('zooms with the wheel around the pointer', async () => {
    const { image, content } = await renderLoadedModal();

    fireEvent.wheel(content, { deltaY: -100, clientX: 40, clientY: 30 });
    await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1.25, 5));

    fireEvent.wheel(content, { deltaY: 100, clientX: 40, clientY: 30 });
    await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1, 5));
  });

  it('zooms from the keyboard and resets with 0', async () => {
    const { image } = await renderLoadedModal();

    fireEvent.keyDown(window, { key: '+' });
    fireEvent.keyDown(window, { key: '+' });
    await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1.5625, 4));

    fireEvent.keyDown(window, { key: '0' });
    await waitFor(() => expect(scaleOf(image)).toBe(1));
  });

  it('stops at the zoom limits', async () => {
    const { image } = await renderLoadedModal();

    for (let i = 0; i < 20; i++) fireEvent.keyDown(window, { key: '+' });
    await waitFor(() => expect(scaleOf(image)).toBe(8));

    for (let i = 0; i < 30; i++) fireEvent.keyDown(window, { key: '-' });
    await waitFor(() => expect(scaleOf(image)).toBe(0.4));
  });

  it('offers grab-to-pan only once the image is bigger than the window', async () => {
    const { content } = await renderLoadedModal();

    expect(content.className).not.toContain('cursor-grab');

    fireEvent.keyDown(window, { key: '+' });
    await waitFor(() => expect(content.className).toContain('cursor-grab'));
  });

  describe('a pointer that never reports its release', () => {
    // jsdom has no PointerEvent, and testing-library then falls back to a plain
    // Event without pointerId / pointerType / buttons.
    class FakePointerEvent extends MouseEvent {
      pointerId: number;
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
        this.pointerType = init.pointerType ?? '';
      }
    }

    /** jsdom lays nothing out, so the pan clamp needs real box sizes. */
    function giveBoxes(content: HTMLElement, image: HTMLImageElement) {
      Object.defineProperty(content, 'clientWidth', { configurable: true, value: 200 });
      Object.defineProperty(content, 'clientHeight', { configurable: true, value: 200 });
      Object.defineProperty(image, 'offsetWidth', { configurable: true, value: 400 });
      Object.defineProperty(image, 'offsetHeight', { configurable: true, value: 400 });
      content.setPointerCapture = vi.fn();
    }

    function translateOf(image: HTMLImageElement): string {
      return /translate\(([^)]*)\)/.exec(image.style.transform)?.[1] ?? '';
    }

    beforeEach(() => {
      Object.defineProperty(window, 'PointerEvent', { configurable: true, value: FakePointerEvent });
    });

    afterEach(() => {
      delete (window as { PointerEvent?: unknown }).PointerEvent;
    });

    it('drops it when it leaves the image area, instead of panning on hover', async () => {
      const { image, content } = await renderLoadedModal();
      giveBoxes(content, image);

      // Press at zoom 1 — no pointer capture is taken — then release somewhere
      // else, so only pointerleave arrives.
      fireEvent.pointerDown(content, { pointerId: 1, pointerType: 'mouse', buttons: 1, clientX: 100, clientY: 100 });
      fireEvent.pointerLeave(content, { pointerId: 1, pointerType: 'mouse' });

      fireEvent.keyDown(window, { key: '+' });
      await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1.25, 5));

      fireEvent.pointerMove(content, { pointerId: 1, pointerType: 'mouse', buttons: 0, clientX: 160, clientY: 140 });

      expect(translateOf(image)).toBe('0px, 0px');
    });

    it('drops it on the first buttonless move, so a later touch is not read as a pinch', async () => {
      const { image, content } = await renderLoadedModal();
      giveBoxes(content, image);

      fireEvent.pointerDown(content, { pointerId: 1, pointerType: 'mouse', buttons: 1, clientX: 100, clientY: 100 });
      fireEvent.keyDown(window, { key: '+' });
      await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1.25, 5));

      // The mouse moves back over the image with nothing held down.
      fireEvent.pointerMove(content, { pointerId: 1, pointerType: 'mouse', buttons: 0, clientX: 160, clientY: 140 });
      expect(translateOf(image)).toBe('0px, 0px');

      // One finger now drags alone: it pans, it does not pinch-zoom.
      fireEvent.pointerDown(content, { pointerId: 2, pointerType: 'touch', clientX: 100, clientY: 100 });
      fireEvent.pointerMove(content, { pointerId: 2, pointerType: 'touch', clientX: 130, clientY: 100 });

      expect(scaleOf(image)).toBeCloseTo(1.25, 5);
      expect(translateOf(image)).toBe('30px, 0px');
    });

    it('still pans while the button is held', async () => {
      const { image, content } = await renderLoadedModal();
      giveBoxes(content, image);

      fireEvent.keyDown(window, { key: '+' });
      await waitFor(() => expect(scaleOf(image)).toBeCloseTo(1.25, 5));

      fireEvent.pointerDown(content, { pointerId: 1, pointerType: 'mouse', buttons: 1, clientX: 100, clientY: 100 });
      fireEvent.pointerMove(content, { pointerId: 1, pointerType: 'mouse', buttons: 1, clientX: 120, clientY: 110 });

      expect(translateOf(image)).toBe('20px, 10px');
    });
  });

  it('closes on Escape', async () => {
    await renderLoadedModal();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(mockOnClose).toHaveBeenCalledTimes(1);
  });
});
