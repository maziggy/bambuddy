import { StrictMode } from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { EmbeddedCameraViewer } from '../../components/EmbeddedCameraViewer';

// A detached img keeps its MJPEG connection open (measured in Chromium): the
// backend stream, the chamber light it holds and the /camera/stop it blocks
// stay until the browser closes. Every img the viewer drops has its src removed.

async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

function viewer() {
  return <EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />;
}

describe('EmbeddedCameraViewer stream lifetime', () => {
  beforeEach(() => {
    localStorage.clear();
    server.use(
      http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
      http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('closes the stream when the viewer is minimized', async () => {
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');
    expect(img.getAttribute('src')).toContain('/api/v1/printers/7/camera/stream');

    fireEvent.click(screen.getByTitle('Minimize'));
    await settle();

    expect(img.getAttribute('src')).toBeNull();
  });

  it('closes the current stream on close after the stream was restarted', async () => {
    const { unmount } = render(viewer());
    await settle();
    const first = screen.getByAltText('Camera stream');
    fireEvent.load(first);
    await waitFor(() => expect(screen.getByTitle('Refresh stream')).not.toBeDisabled());

    fireEvent.click(screen.getByTitle('Refresh stream'));
    await waitFor(() => expect(screen.getByAltText('Camera stream')).not.toBe(first));
    const current = screen.getByAltText('Camera stream');
    expect(current.getAttribute('src')).toContain('/camera/stream');

    unmount();
    await settle();

    expect(current.getAttribute('src')).toBeNull();
  });

  it('takes the error of a closed img for no stream failure', async () => {
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');

    await act(async () => {
      img.setAttribute('src', ''); // what the reconnect and restart code does
      img.dispatchEvent(new Event('error'));
    });

    expect(screen.queryByText(/Reconnecting in/)).toBeNull();
  });

  it('stops its own stream by the viewer id it opened it with', async () => {
    const fetchMock = vi.spyOn(global, 'fetch');
    const { unmount } = render(viewer());
    await settle();
    const id = new URL((screen.getByAltText('Camera stream') as HTMLImageElement).src).searchParams.get('viewer');
    expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);

    unmount();

    const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
    expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
  });

  it('names itself on the stop its Refresh sends', async () => {
    const fetchMock = vi.spyOn(global, 'fetch');
    render(viewer());
    await settle();
    const img = screen.getByAltText('Camera stream');
    const id = new URL((img as HTMLImageElement).src).searchParams.get('viewer');
    fireEvent.load(img);
    await waitFor(() => expect(screen.getByTitle('Refresh stream')).not.toBeDisabled());

    fireEvent.click(screen.getByTitle('Refresh stream'));

    const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
    expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
    // Refresh reopens the stream 100 ms later: let that happen inside the test,
    // not after its environment is gone.
    await waitFor(() => expect(screen.getByAltText('Camera stream')).not.toBe(img));
  });

  it('keeps its stream under StrictMode', async () => {
    render(<StrictMode>{viewer()}</StrictMode>);
    await settle();

    expect(screen.getByAltText('Camera stream').getAttribute('src')).toContain('/api/v1/printers/7/camera/stream');
  });
});

// A sideways camera was laid out at the window's unswapped size and then
// turned: cut off in a wide window, boxed in by black bars in a tall one (#3362).
describe('EmbeddedCameraViewer with a camera turned sideways', () => {
  function sizeVideoArea(width: number, height: number) {
    const isVideoArea = (el: HTMLElement) => el.querySelector(':scope > img[alt="Camera stream"]') !== null;
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
      return isVideoArea(this) ? width : 0;
    });
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
      return isVideoArea(this) ? height : 0;
    });
  }

  beforeEach(() => {
    localStorage.clear();
    server.use(http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })));
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    [270, 400, 260, { maxWidth: '260px', maxHeight: '400px' }],
    [90, 260, 400, { maxWidth: '400px', maxHeight: '260px' }],
  ])('fits a camera turned %i degrees into a %ix%i window', async (rotation, width, height, expected) => {
    sizeVideoArea(width, height);
    server.use(http.get('/api/v1/printers/:id', () => HttpResponse.json({ id: 7, name: 'A1 Mini', camera_rotation: rotation })));

    render(viewer());

    const img = await screen.findByAltText('Camera stream');
    await waitFor(() => expect(img.style.transform).toContain(`rotate(${rotation}deg)`));
    await waitFor(() => expect(img).toHaveStyle(expected));
  });

  it('leaves an upright camera alone', async () => {
    sizeVideoArea(400, 260);
    server.use(http.get('/api/v1/printers/:id', () => HttpResponse.json({ id: 7, name: 'A1 Mini', camera_rotation: 180 })));

    render(viewer());

    const img = await screen.findByAltText('Camera stream');
    await waitFor(() => expect(img.style.transform).toContain('rotate(180deg)'));
    expect(img.style.maxWidth).toBe('');
    expect(img.style.maxHeight).toBe('');
  });
});

describe('EmbeddedCameraViewer pop-out (#2737)', () => {
  beforeEach(() => {
    server.use(
      http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
      http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('opens the camera window and closes the overlay', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue({} as Window);
    const onClose = vi.fn();
    render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={onClose} />);
    await settle();

    fireEvent.click(screen.getByRole('button', { name: 'Open in its own window' }));

    expect(open).toHaveBeenCalledWith('/camera/7', 'camera-7', expect.any(String));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('offers no window to pop out to in a home-screen web app (#3419)', async () => {
    Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });
    try {
      render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={vi.fn()} />);
      await settle();

      expect(screen.queryByRole('button', { name: 'Open in its own window' })).toBeNull();
    } finally {
      delete (navigator as Navigator & { standalone?: boolean }).standalone;
    }
  });

  it('keeps the overlay when a popup blocker stopped the window', async () => {
    vi.spyOn(window, 'open').mockReturnValue(null);
    const onClose = vi.fn();
    render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={onClose} />);
    await settle();

    fireEvent.click(screen.getByRole('button', { name: 'Open in its own window' }));

    expect(onClose).not.toHaveBeenCalled();
  });
});

describe('EmbeddedCameraViewer first position', () => {
  const originalWidth = window.innerWidth;

  afterEach(() => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: originalWidth });
    vi.mocked(localStorage.getItem).mockReset();
  });

  it('moves below the header when the window narrows into the compact layout', async () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1440 });
    server.use(http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })));
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === 'embeddedCameraState_7' ? JSON.stringify({ x: 900, y: 20, width: 400, height: 300 }) : null,
    );
    const listeners: Array<(e: { matches: boolean }) => void> = [];
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      ...originalMatchMedia(query),
      matches: false,
      addEventListener: (_: string, cb: (e: { matches: boolean }) => void) => listeners.push(cb),
      removeEventListener: () => {},
    })) as typeof window.matchMedia;
    try {
      const { container } = render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />);
      await settle();
      const box = container.querySelector('.fixed') as HTMLElement;
      expect(box.style.top).toBe('20px');

      await act(async () => listeners.forEach(cb => cb({ matches: true })));

      expect(parseInt(box.style.top, 10)).toBeGreaterThanOrEqual(56);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('cannot be dragged over the compact header', async () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
    server.use(http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })));
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) => ({ ...originalMatchMedia(query), matches: /max-width/.test(query) })) as typeof window.matchMedia;
    try {
      const { container } = render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />);
      await settle();
      const box = container.querySelector('.fixed') as HTMLElement;

      fireEvent.mouseDown(screen.getByText('P1S'), { clientX: 100, clientY: 80 });
      fireEvent.mouseMove(document, { clientX: 100, clientY: 0 });
      fireEvent.mouseUp(document);
      expect(parseInt(box.style.top, 10)).toBeGreaterThanOrEqual(56);

      // And by touch, as on a phone.
      fireEvent.touchStart(screen.getByText('P1S'), { touches: [{ clientX: 100, clientY: 80 }] });
      fireEvent.touchMove(document, { touches: [{ clientX: 100, clientY: 0 }] });
      fireEvent.touchEnd(document);
      expect(parseInt(box.style.top, 10)).toBeGreaterThanOrEqual(56);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('keeps a position saved before the update below the compact header', async () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
    server.use(http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })));
    // The old default, saved by every viewer half a second after it opened.
    vi.mocked(localStorage.getItem).mockImplementation((key: string) =>
      key === 'embeddedCameraState_7' ? JSON.stringify({ x: -30, y: 20, width: 400, height: 300 }) : null,
    );

    const { container } = render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />);
    await settle();

    const box = container.querySelector('.fixed') as HTMLElement;
    expect(parseInt(box.style.top, 10)).toBeGreaterThanOrEqual(56);
  });

  it('opens below the compact header and inside a phone-sized window (#2737)', async () => {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
    server.use(http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })));

    const { container } = render(<EmbeddedCameraViewer printerId={7} printerName="P1S" onClose={() => {}} />);
    await settle();

    const box = container.querySelector('.fixed') as HTMLElement;
    // The 56px header holds the camera launcher and the bug report.
    expect(parseInt(box.style.top, 10)).toBeGreaterThanOrEqual(56);
    expect(parseInt(box.style.left, 10)).toBeGreaterThanOrEqual(0);
    expect(parseInt(box.style.left, 10) + parseInt(box.style.width, 10)).toBeLessThanOrEqual(390);
  });
});
