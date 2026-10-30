/**
 * Tests for the CameraPage component.
 */

import { StrictMode } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, screen, waitFor, render as rtlRender } from '@testing-library/react';
import { CameraPage } from '../../pages/CameraPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { AuthProvider } from '../../contexts/AuthContext';
import { I18nextProvider } from 'react-i18next';
import i18n from '../../i18n';
import { listenForCameraPopIn } from '../../utils/camera';

// Mock navigator.sendBeacon which isn't available in jsdom
vi.stubGlobal('navigator', {
  ...navigator,
  sendBeacon: vi.fn().mockReturnValue(true),
});

const mockPrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
};

/** As a window.open popup: something opened it, so it can close itself. */
function asPopup() {
  Object.defineProperty(window, 'opener', { value: {}, configurable: true, writable: true });
}

/** As a web app started from the iOS home screen. */
function asWebApp(): () => void {
  Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });
  return () => {
    delete (navigator as Navigator & { standalone?: boolean }).standalone;
  };
}

// Custom render for CameraPage which needs specific route params
function renderCameraPage(printerId: number, search = '', strict = false, entries?: string[]) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  const tree = (
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={entries ?? [`/cameras/${printerId}${search}`]}>
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>
                <Routes>
                  <Route path="/cameras/:printerId" element={<CameraPage />} />
                  <Route path="/" element={<p>Bambuddy home</p>} />
                  <Route path="/printers" element={<p>printers page</p>} />
                </Routes>
              </ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>
  );
  return rtlRender(strict ? <StrictMode>{tree}</StrictMode> : tree);
}

describe('CameraPage', () => {
  const originalTitle = document.title;

  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/:id', () => {
        return HttpResponse.json(mockPrinter);
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json({
          connected: true,
          state: 'IDLE',
          progress: 0,
        });
      }),
      http.post('/api/v1/printers/:id/camera/stop', () => {
        return HttpResponse.json({ success: true });
      }),
      http.get('/api/v1/printers/:id/camera/status', () => {
        return HttpResponse.json({ active: true, stalled: false });
      })
    );
  });

  afterEach(() => {
    document.title = originalTitle;
    Object.defineProperty(window, 'opener', { value: null, configurable: true, writable: true });
  });

  describe('rendering', () => {
    it('renders camera page for printer', async () => {
      renderCameraPage(1);

      // Camera page should load - look for the header with camera icon
      await waitFor(() => {
        expect(screen.getByRole('heading')).toBeInTheDocument();
      });
    });

    it('shows live and snapshot mode buttons', async () => {
      renderCameraPage(1);

      await waitFor(() => {
        // Check for translation key or translated text
        expect(screen.getByText(/Live|camera\.live/)).toBeInTheDocument();
        expect(screen.getByText(/Snapshot|camera\.snapshot/)).toBeInTheDocument();
      });
    });

    it('shows printer name in header', async () => {
      renderCameraPage(1);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
    });
  });

  // The viewport was the box a sideways camera was swapped against, but the
  // header and padding leave the video area smaller, so a wide window cut the
  // picture off at the top and bottom (#3362).
  describe('a camera turned sideways', () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it.each([
      [90, { maxWidth: '1000px', maxHeight: '1880px' }],
      [270, { maxWidth: '1000px', maxHeight: '1880px' }],
    ])('fits a camera turned %i degrees into the video area', async (rotation, expected) => {
      const isVideoArea = (el: HTMLElement) => el.querySelector(':scope > img') !== null && el.classList.contains('h-full');
      vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockImplementation(function (this: HTMLElement) {
        return isVideoArea(this) ? 1880 : 0;
      });
      vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function (this: HTMLElement) {
        return isVideoArea(this) ? 1000 : 0;
      });
      server.use(http.get('/api/v1/printers/:id', () => HttpResponse.json({ ...mockPrinter, camera_rotation: rotation })));

      renderCameraPage(1);

      await waitFor(() => {
        const img = document.querySelector('img') as HTMLImageElement;
        expect(img.style.transform).toContain(`rotate(${rotation}deg)`);
        expect(img).toHaveStyle(expected);
      });
    });
  });

  describe('camera controls', () => {
    it('renders without crashing', async () => {
      renderCameraPage(1);

      // Just verify no crash during render
      await waitFor(() => {
        expect(document.body).toBeInTheDocument();
      });
    });

    it('shows the camera diagnostic (stethoscope) button in the control bar (#1395)', async () => {
      // The diagnostic shipped wired into the embedded viewer only; window mode
      // (this page) was missing it. The control-bar button must be present here.
      renderCameraPage(1);

      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
      expect(screen.getByTitle('Diagnose')).toBeInTheDocument();
    });
  });

  // A detached img keeps its MJPEG connection open (measured in Chromium): every
  // img the page drops has its src removed, not only the one it first showed.
  describe('stream lifetime', () => {
    async function streamImg() {
      await waitFor(() => {
        expect((document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '').toContain('/camera/stream');
      });
      return document.querySelector('img') as HTMLImageElement;
    }

    async function snapshotImg() {
      const stream = await streamImg();
      // The mode buttons stay disabled until the stream shows a frame.
      await act(async () => {
        stream.dispatchEvent(new Event('load'));
      });
      fireEvent.click(screen.getByText('Snapshot'));
      await waitFor(() => {
        expect(document.querySelector('img')?.getAttribute('src') || '').toContain('/camera/snapshot');
      });
      return document.querySelector('img') as HTMLImageElement;
    }

    it('retries a failed snapshot once, a moment later', async () => {
      // The server answers 503 while the live view it is switching away from
      // still holds the camera.
      renderCameraPage(1);
      const img = await snapshotImg();
      const first = img.getAttribute('src');

      await act(async () => {
        img.dispatchEvent(new Event('error'));
      });
      expect(screen.queryByText('Camera unavailable')).toBeNull();

      await waitFor(
        () => {
          const src = document.querySelector('img')?.getAttribute('src') || '';
          expect(src).toContain('/camera/snapshot');
          expect(src).not.toBe(first);
        },
        { timeout: 3500 },
      );
    });

    it('shows the camera unavailable when the retry fails too', async () => {
      renderCameraPage(1);
      const img = await snapshotImg();
      const first = img.getAttribute('src');
      await act(async () => {
        img.dispatchEvent(new Event('error'));
      });
      await waitFor(() => expect(document.querySelector('img')?.getAttribute('src')).not.toBe(first), { timeout: 3500 });

      await act(async () => {
        (document.querySelector('img') as HTMLImageElement).dispatchEvent(new Event('error'));
      });

      expect(screen.getByText('Camera unavailable')).toBeInTheDocument();
    });

    it('closes the current stream on leaving after the stream was restarted', async () => {
      const { unmount } = renderCameraPage(1);
      const first = await streamImg();
      fireEvent.load(first);

      await waitFor(() => expect(screen.getByTitle('Restart stream')).not.toBeDisabled());
      fireEvent.click(screen.getByTitle('Restart stream'));
      await waitFor(() => expect(document.querySelector('img')).not.toBe(first));
      const current = await streamImg();

      unmount();
      await act(async () => {
        await Promise.resolve();
      });

      expect(current.getAttribute('src')).toBeNull();
    });

    it('takes the error of a closed img for no stream failure', async () => {
      renderCameraPage(1);
      const img = await streamImg();

      await act(async () => {
        img.setAttribute('src', ''); // what the reconnect and restart code does
        img.dispatchEvent(new Event('error'));
      });

      expect(screen.queryByText(/Reconnecting in/)).toBeNull();
    });

    it('stops its own stream by the viewer id it opened it with', async () => {
      const fetchMock = vi.spyOn(global, 'fetch');
      const { unmount } = renderCameraPage(1);
      const img = await streamImg();
      const id = new URL(img.src).searchParams.get('viewer');
      expect(id).toMatch(/^[A-Za-z0-9_-]{1,64}$/);

      unmount();

      const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
      expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
      fetchMock.mockRestore();
    });

    it('names itself on the stop its Restart sends', async () => {
      const fetchMock = vi.spyOn(global, 'fetch');
      renderCameraPage(1);
      const img = await streamImg();
      const id = new URL(img.src).searchParams.get('viewer');
      fireEvent.load(img);
      await waitFor(() => expect(screen.getByTitle('Restart stream')).not.toBeDisabled());

      fireEvent.click(screen.getByTitle('Restart stream'));

      const stop = fetchMock.mock.calls.map(([url]) => String(url)).find((url) => url.includes('/camera/stop'));
      expect(new URL(stop!, 'http://x').searchParams.get('viewer')).toBe(id);
      // Restart reopens the stream 100 ms later: let that happen inside the
      // test, not after its environment is gone.
      await waitFor(() => expect(document.querySelector('img')).not.toBe(img));
      fetchMock.mockRestore();
    });

    it('keeps its stream under StrictMode', async () => {
      renderCameraPage(1, '', true);

      const img = await streamImg();
      await act(async () => {
        await Promise.resolve();
      });

      expect(img.getAttribute('src')).toContain('/camera/stream');
    });
  });

  describe('stream token handling (#979)', () => {
    it('does not render image src until stream token arrives when auth is enabled', async () => {
      let resolveToken!: (value: unknown) => void;
      const tokenPromise = new Promise((resolve) => {
        resolveToken = resolve;
      });

      server.use(
        http.get('*/api/v1/auth/status', () =>
          HttpResponse.json({ auth_enabled: true, requires_setup: false })
        ),
        http.post('*/api/v1/printers/camera/stream-token', async () => {
          await tokenPromise;
          return HttpResponse.json({ token: 'tok-abc' });
        })
      );

      renderCameraPage(1);

      // Before the token resolves the <img> should not have a src pointing at
      // the stream endpoint — otherwise the backend would 401 with the
      // "Valid camera stream token required" error from #979.
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });
      const img = document.querySelector('img') as HTMLImageElement | null;
      expect(img).not.toBeNull();
      expect(img?.getAttribute('src') || '').not.toContain('/camera/stream');

      resolveToken(undefined);

      // After the token resolves the image src picks it up as ?token=...
      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('/camera/stream');
        expect(src).toContain('token=tok-abc');
      });
    });

    it('does not fire a tokenless stream request when auth is disabled (#2521)', async () => {
      // The stream-token query runs whether or not auth is enabled, and this page
      // subscribes to it. So the src used to be rendered on the first pass with no
      // token, and swapped once the token landed. Changing img.src makes the
      // browser abort the in-flight request and issue a second one — and with auth
      // disabled no token is required, so BOTH reached the backend and attached to
      // the camera fan-out. Every page load put two viewers on a printer that
      // allows one socket, then abandoned one of them.
      let resolveToken!: () => void;
      const gate = new Promise<void>((resolve) => {
        resolveToken = resolve;
      });

      server.use(
        http.post('*/api/v1/printers/camera/stream-token', async () => {
          await gate;
          return HttpResponse.json({ token: 'tok-xyz' });
        })
      );

      renderCameraPage(1);
      await waitFor(() => {
        expect(screen.getByText('X1 Carbon')).toBeInTheDocument();
      });

      // Token still in flight: the <img> must not already be pulling the stream.
      const early = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
      expect(early).not.toContain('/camera/stream');

      resolveToken();

      // The first — and only — stream URL the browser ever sees is the tokened one.
      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('/api/v1/printers/1/camera/stream');
        expect(src).toContain('token=tok-xyz');
      });
    });

    it('still streams when auth is disabled and the token endpoint fails', async () => {
      // Waiting for the token must not become a way to never render at all: an
      // auth-disabled backend doesn't need one. Once the query settles — even
      // unsuccessfully — the stream loads.
      server.use(
        http.post('*/api/v1/printers/camera/stream-token', () => new HttpResponse(null, { status: 500 }))
      );

      renderCameraPage(1);

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('/api/v1/printers/1/camera/stream');
      });
    });
  });

  describe('fps URL parameter (#1131)', () => {
    it('defaults to fps=15 when no query parameter is provided', async () => {
      renderCameraPage(1);

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('fps=15');
      });
    });

    it('honors fps query parameter from URL', async () => {
      renderCameraPage(1, '?fps=5');

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('fps=5');
      });
    });

    it('clamps fps above 30 to 30', async () => {
      renderCameraPage(1, '?fps=60');

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('fps=30');
      });
    });

    it('clamps fps below 1 to 1', async () => {
      renderCameraPage(1, '?fps=0');

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('fps=1');
      });
    });

    it('falls back to 15 for non-numeric fps', async () => {
      renderCameraPage(1, '?fps=invalid');

      await waitFor(() => {
        const src = (document.querySelector('img') as HTMLImageElement | null)?.getAttribute('src') || '';
        expect(src).toContain('fps=15');
      });
    });
  });

  describe('back into a Bambuddy tab (#2737)', () => {
    afterEach(() => {
      vi.restoreAllMocks();
      sessionStorage.clear();
    });

    it('moves into the open tab as an overlay and closes the window', async () => {
      asPopup();
      const open = vi.fn();
      const stop = listenForCameraPopIn('t1', open);
      const close = vi.spyOn(window, 'close').mockImplementation(() => {});
      const fetchSpy = vi.spyOn(global, 'fetch');
      try {
        renderCameraPage(1);
        await screen.findByText('X1 Carbon');

        fireEvent.click(screen.getByRole('button', { name: 'Show as an overlay in Bambuddy' }));

        await waitFor(() => expect(close).toHaveBeenCalled());
        expect(open).toHaveBeenCalledExactlyOnceWith(1, 'X1 Carbon');
        // A tab the script cannot close says where the camera went, and its
        // stream is gone with the img.
        expect(await screen.findByText(/This camera is now shown in Bambuddy/)).toBeInTheDocument();
        expect(document.querySelector('img')).toBeNull();
        // The view is gone with its timers, and the backend is told this
        // viewer left even though the tab itself stays.
        await waitFor(() =>
          expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/printers/1/camera/stop'))).toBe(true),
        );
      } finally {
        stop();
      }
    });

    it('stays open and says so when no Bambuddy tab is open', async () => {
      const close = vi.spyOn(window, 'close').mockImplementation(() => {});
      renderCameraPage(1);
      await screen.findByText('X1 Carbon');

      fireEvent.click(screen.getByRole('button', { name: 'Show as an overlay in Bambuddy' }));

      expect(await screen.findByText('No Bambuddy tab is open to show the camera in.')).toBeInTheDocument();
      expect(close).not.toHaveBeenCalled();
      expect(document.querySelector('img')).not.toBeNull();
    });

    it('goes back to the tab it was opened from', async () => {
      // The popup's sessionStorage is a copy of its opener's.
      sessionStorage.setItem('bambuddyTabId', 'opener');
      const other = vi.fn();
      const opener = vi.fn();
      const stops = [listenForCameraPopIn('other', other), listenForCameraPopIn('opener', opener)];
      vi.spyOn(window, 'close').mockImplementation(() => {});
      try {
        renderCameraPage(1);
        await screen.findByText('X1 Carbon');

        fireEvent.click(screen.getByRole('button', { name: 'Show as an overlay in Bambuddy' }));

        await waitFor(() => expect(opener).toHaveBeenCalledExactlyOnceWith(1, 'X1 Carbon'));
        expect(other).not.toHaveBeenCalled();
      } finally {
        stops.forEach(s => s());
      }
    });
  });

  describe('a page no window opened (#3419)', () => {
    afterEach(() => {
      sessionStorage.clear();
    });

    it('offers a way back to Bambuddy', async () => {
      renderCameraPage(1);
      await screen.findByText('X1 Carbon');

      fireEvent.click(screen.getByRole('button', { name: 'Back' }));

      expect(await screen.findByText('Bambuddy home')).toBeInTheDocument();
    });

    it('goes back instead of closing itself once the camera moved to a tab', async () => {
      const open = vi.fn();
      const stop = listenForCameraPopIn('t1', open);
      const close = vi.spyOn(window, 'close').mockImplementation(() => {});
      try {
        renderCameraPage(1);
        await screen.findByText('X1 Carbon');

        fireEvent.click(screen.getByRole('button', { name: 'Show as an overlay in Bambuddy' }));

        expect(await screen.findByText('Bambuddy home')).toBeInTheDocument();
        expect(open).toHaveBeenCalledExactlyOnceWith(1, 'X1 Carbon');
        expect(close).not.toHaveBeenCalled();
      } finally {
        stop();
      }
    });

    it('goes back to the Bambuddy page it came from', async () => {
      renderCameraPage(1, '', false, ['/printers', '/cameras/1']);
      await screen.findByText('X1 Carbon');

      fireEvent.click(screen.getByRole('button', { name: 'Back' }));

      expect(await screen.findByText('printers page')).toBeInTheDocument();
    });

    it('a popup whose tab has gone still closes rather than navigating', async () => {
      window.name = 'camera-1';
      try {
        renderCameraPage(1);
        await screen.findByText('X1 Carbon');

        expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
      } finally {
        window.name = '';
      }
    });

    it('a popup has no back button: it closes', async () => {
      asPopup();
      renderCameraPage(1);
      await screen.findByText('X1 Carbon');

      expect(screen.queryByRole('button', { name: 'Back' })).toBeNull();
    });

    it('in a home-screen web app, has a way back and no tab to pop into', async () => {
      // iOS may report an opener for the page it loaded into the app itself.
      asPopup();
      const restore = asWebApp();
      try {
        renderCameraPage(1);
        await screen.findByText('X1 Carbon');

        expect(screen.getByRole('button', { name: 'Back' })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Show as an overlay in Bambuddy' })).toBeNull();
      } finally {
        restore();
      }
    });
  });

  describe('invalid printer', () => {
    it('shows invalid printer message for ID 0', async () => {
      renderCameraPage(0);

      await waitFor(() => {
        // Check for translation key or translated text
        expect(screen.getByText(/Invalid printer ID|camera\.invalidPrinterId/)).toBeInTheDocument();
      });
    });
  });
});
