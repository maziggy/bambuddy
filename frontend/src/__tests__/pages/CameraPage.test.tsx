/**
 * Tests for the CameraPage component.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, render as rtlRender } from '@testing-library/react';
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

// Custom render for CameraPage which needs specific route params
function renderCameraPage(printerId: number, search = '') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <MemoryRouter initialEntries={[`/cameras/${printerId}${search}`]}>
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>
                <Routes>
                  <Route path="/cameras/:printerId" element={<CameraPage />} />
                </Routes>
              </ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>
  );
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
