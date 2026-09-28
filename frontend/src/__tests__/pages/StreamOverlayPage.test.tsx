/**
 * Tests for the StreamOverlayPage component.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, waitFor, render as rtlRender } from '@testing-library/react';
import { StreamOverlayPage } from '../../pages/StreamOverlayPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { AuthProvider } from '../../contexts/AuthContext';

const mockPrinter = {
  id: 1,
  name: 'X1 Carbon',
  ip_address: '192.168.1.100',
  serial_number: '00M09A350100001',
  access_code: '12345678',
  model: 'X1C',
  enabled: true,
};

const mockStatusIdle = {
  id: 1,
  name: 'X1 Carbon',
  connected: true,
  state: 'IDLE',
  progress: 0,
  current_print: null,
  remaining_time: null,
  layer_num: null,
  total_layers: null,
  stg_cur_name: null,
};

const mockStatusPrinting = {
  id: 1,
  name: 'X1 Carbon',
  connected: true,
  state: 'RUNNING',
  progress: 45,
  current_print: 'Benchy.gcode.3mf',
  remaining_time: 82,
  layer_num: 150,
  total_layers: 300,
  stg_cur_name: null,
};

// Custom render for StreamOverlayPage
function renderOverlayPage(printerId: number, queryParams = '') {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });

  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/overlay/${printerId}${queryParams}`]}>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              <Routes>
                <Route path="/overlay/:printerId" element={<StreamOverlayPage />} />
              </Routes>
            </ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('StreamOverlayPage', () => {
  const originalTitle = document.title;

  beforeEach(() => {
    // Mock WebSocket. vitest 4 dropped support for arrow-function constructor
    // mocks (`new (() => ...)` throws "is not a constructor"); use a plain
    // function so `new WebSocket(...)` resolves correctly.
    vi.stubGlobal(
      'WebSocket',
      vi.fn().mockImplementation(function (this: { close: () => void; onmessage: null; onerror: null }) {
        this.close = vi.fn();
        this.onmessage = null;
        this.onerror = null;
      }),
    );

    server.use(
      http.get('/api/v1/printers/:id', () => {
        return HttpResponse.json(mockPrinter);
      }),
      http.get('/api/v1/printers/:id/status', () => {
        return HttpResponse.json(mockStatusIdle);
      })
    );
  });

  afterEach(() => {
    document.title = originalTitle;
    vi.unstubAllGlobals();
  });

  describe('rendering', () => {
    it('renders overlay page for printer', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('Printer is idle')).toBeInTheDocument();
      });
    });

    it('shows Bambuddy logo', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByAltText('Bambuddy')).toBeInTheDocument();
      });
    });

    it('logo links to GitHub', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        const logo = screen.getByAltText('Bambuddy');
        const link = logo.closest('a');
        expect(link).toHaveAttribute('href', 'https://github.com/maziggy/bambuddy');
      });
    });
  });

  describe('printer model', () => {
    it.each(['', '?show=printer', '?show='])('hides the model unless selected (%s)', async (query) => {
      renderOverlayPage(1, query);
      await screen.findByText('Printer is idle');
      expect(screen.queryByText(/X1C/)).not.toBeInTheDocument();
    });

    it.each([
      ['?show=model', 'X1C'],
      ['?show=printer,model', 'X1 Carbon · X1C'],
    ])('shows the selected printer identity (%s)', async (query, identity) => {
      renderOverlayPage(1, query);
      expect(await screen.findByText(identity)).toBeInTheDocument();
      if (query === '?show=model') expect(screen.queryByText(/X1 Carbon/)).not.toBeInTheDocument();
    });

    it.each([null, ''])('omits a missing model without adding a separator (%s)', async (model) => {
      server.use(http.get('/api/v1/printers/:id', () => HttpResponse.json({ ...mockPrinter, model })));
      renderOverlayPage(1, '?show=printer,model');
      expect(await screen.findByText('X1 Carbon')).toBeInTheDocument();
      expect(screen.queryByText(/·/)).not.toBeInTheDocument();
    });

    it.each([['H2D', 'Workshop · H2D'], [null, 'Workshop'], ['', 'Workshop']] as const)('reads the model (%s) from the OBS token feed without requesting printer details', async (model, identity) => {
      let printerHit = false;
      server.use(
        http.get('/api/v1/printers/:id/overlay-status', () => HttpResponse.json({
          ...mockStatusIdle, name: 'Workshop', model, camera_rotation: 0,
          gcode_file: null, temperatures: {}, time_format: 'system',
        })),
        http.get('/api/v1/printers/:id', () => {
          printerHit = true;
          return new HttpResponse(null, { status: 401 });
        }),
      );
      renderOverlayPage(1, '?token=obs-tok&show=printer,model');
      expect(await screen.findByText(identity)).toBeInTheDocument();
      expect(printerHit).toBe(false);
      if (!model) expect(screen.queryByText(/·/)).not.toBeInTheDocument();
    });
  });

  describe('printing state', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockStatusPrinting);
        })
      );
    });

    it('shows filename when printing', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('Benchy')).toBeInTheDocument();
      });
    });

    it('shows progress percentage', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('45%')).toBeInTheDocument();
      });
    });

    it('shows layer count', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('150')).toBeInTheDocument();
        expect(screen.getByText('300')).toBeInTheDocument();
      });
    });

    it('shows status text', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('Printing')).toBeInTheDocument();
      });
    });
  });

  describe('invalid printer', () => {
    it('shows invalid printer message for ID 0', async () => {
      renderOverlayPage(0);

      await waitFor(() => {
        expect(screen.getByText('Invalid printer ID')).toBeInTheDocument();
      });
    });
  });

  describe('query parameters', () => {
    it('respects size parameter', async () => {
      renderOverlayPage(1, '?size=large');

      await waitFor(() => {
        // Just verify it renders without error
        expect(screen.getByAltText('Bambuddy')).toBeInTheDocument();
      });
    });

    it('respects show parameter to hide elements', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockStatusPrinting);
        })
      );

      renderOverlayPage(1, '?show=progress');

      await waitFor(() => {
        // Progress should be visible
        expect(screen.getByText('45%')).toBeInTheDocument();
        // Status text should be hidden when not in show list
        expect(screen.queryByText('Printing')).not.toBeInTheDocument();
      });
    });
  });

  describe('FPS configuration', () => {
    it('uses default FPS of 15 when not specified', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=15');
      });
    });

    it('uses custom FPS when specified in query params', async () => {
      renderOverlayPage(1, '?fps=30');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=30');
      });
    });

    it('clamps FPS to maximum of 30', async () => {
      renderOverlayPage(1, '?fps=60');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=30');
      });
    });

    it('clamps FPS to minimum of 1', async () => {
      renderOverlayPage(1, '?fps=0');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=1');
      });
    });

    it('handles invalid FPS value gracefully', async () => {
      renderOverlayPage(1, '?fps=invalid');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        // Should fall back to default of 15
        expect(img.src).toContain('fps=15');
      });
    });
  });

  describe('camera toggle (status-only mode)', () => {
    it('shows camera by default', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByAltText('Camera stream')).toBeInTheDocument();
      });
    });

    it('hides camera when camera=false', async () => {
      renderOverlayPage(1, '?camera=false');

      await waitFor(() => {
        // Status should still be visible
        expect(screen.getByText('Printer is idle')).toBeInTheDocument();
      });

      // Camera should not be rendered
      expect(screen.queryByAltText('Camera stream')).not.toBeInTheDocument();
    });

    it('hides camera when camera=0', async () => {
      renderOverlayPage(1, '?camera=0');

      await waitFor(() => {
        expect(screen.getByText('Printer is idle')).toBeInTheDocument();
      });

      expect(screen.queryByAltText('Camera stream')).not.toBeInTheDocument();
    });

    it('shows camera when camera=true', async () => {
      renderOverlayPage(1, '?camera=true');

      await waitFor(() => {
        expect(screen.getByAltText('Camera stream')).toBeInTheDocument();
      });
    });

    it('shows camera when camera=1', async () => {
      renderOverlayPage(1, '?camera=1');

      await waitFor(() => {
        expect(screen.getByAltText('Camera stream')).toBeInTheDocument();
      });
    });
  });

  describe('combined parameters', () => {
    it('supports fps and camera together', async () => {
      renderOverlayPage(1, '?fps=25&camera=true');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=25');
      });
    });

    it('supports status-only with custom size', async () => {
      renderOverlayPage(1, '?camera=false&size=large');

      await waitFor(() => {
        expect(screen.getByText('Printer is idle')).toBeInTheDocument();
      });

      expect(screen.queryByAltText('Camera stream')).not.toBeInTheDocument();
    });

    it('supports show parameter with fps', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockStatusPrinting);
        })
      );

      renderOverlayPage(1, '?fps=20&show=progress');

      await waitFor(() => {
        const img = screen.getByAltText('Camera stream') as HTMLImageElement;
        expect(img.src).toContain('fps=20');
        expect(screen.getByText('45%')).toBeInTheDocument();
      });
    });
  });

  describe('offline state', () => {
    beforeEach(() => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({
            ...mockStatusIdle,
            connected: false,
          });
        })
      );
    });

    it('shows offline message when printer disconnected', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('Printer offline')).toBeInTheDocument();
      });
    });
  });

  describe('kiosk token mode (#2613)', () => {
    const mockOverlayPrinting = {
      id: 1,
      name: 'X1 Carbon',
      camera_rotation: 0,
      connected: true,
      state: 'RUNNING',
      current_print: 'KioskBenchy.gcode.3mf',
      gcode_file: 'plate_1.gcode',
      progress: 67,
      remaining_time: 40,
      layer_num: 10,
      total_layers: 20,
      stg_cur_name: null,
      time_format: 'system',
    };

    it('reads the token-authed overlay-status feed and carries the token to the camera', async () => {
      let overlayHit = false;
      server.use(
        http.get('/api/v1/printers/:id/overlay-status', () => {
          overlayHit = true;
          return HttpResponse.json(mockOverlayPrinting);
        })
      );

      renderOverlayPage(1, '?token=obs-tok');

      await waitFor(() => {
        expect(screen.getByText('KioskBenchy')).toBeInTheDocument();
      });
      expect(overlayHit).toBe(true);
      expect(screen.getByText('67%')).toBeInTheDocument();

      // The camera <img> must carry the same kiosk token — a fresh OBS browser
      // has no session to mint a camera stream token from.
      const img = screen.getByAltText('Camera stream') as HTMLImageElement;
      expect(img.src).toContain('token=obs-tok');
    });

    it('does not touch the JWT-only status endpoint or a WebSocket in kiosk mode', async () => {
      let statusHit = false;
      server.use(
        http.get('/api/v1/printers/:id/overlay-status', () => HttpResponse.json(mockOverlayPrinting)),
        http.get('/api/v1/printers/:id/status', () => {
          statusHit = true;
          return HttpResponse.json(mockStatusIdle);
        })
      );

      renderOverlayPage(1, '?token=obs-tok');

      await waitFor(() => {
        expect(screen.getByText('KioskBenchy')).toBeInTheDocument();
      });
      // The logged-in status query is disabled when a token is present, so an
      // unauthenticated OBS browser never fires a doomed 401 (or opens a socket).
      expect(statusHit).toBe(false);
      expect(WebSocket).not.toHaveBeenCalled();
    });
  });

  describe('temperatures (#1422)', () => {
    const withTemps = {
      ...mockStatusPrinting,
      temperatures: {
        nozzle: 219.6,
        nozzle_target: 220,
        bed: 60,
        bed_target: 60,
        chamber: 38.4,
      },
    };

    beforeEach(() => {
      server.use(http.get('/api/v1/printers/:id/status', () => HttpResponse.json(withTemps)));
    });

    it('draws no temperatures unless the URL asks for them', async () => {
      renderOverlayPage(1);

      await waitFor(() => {
        expect(screen.getByText('45%')).toBeInTheDocument();
      });
      // Default ?show= is unchanged by #1422, so overlays already running in an
      // OBS scene look identical after the upgrade.
      expect(screen.queryByText('Nozzle')).not.toBeInTheDocument();
      expect(screen.queryByText('Bed')).not.toBeInTheDocument();
    });

    it('draws only the readings named in ?show=', async () => {
      renderOverlayPage(1, '?show=progress,nozzle');

      await waitFor(() => {
        expect(screen.getByText('Nozzle')).toBeInTheDocument();
      });
      expect(screen.queryByText('Bed')).not.toBeInTheDocument();
      expect(screen.queryByText('Chamber')).not.toBeInTheDocument();
    });

    it('rounds the reading and hides a target it has already reached', async () => {
      renderOverlayPage(1, '?show=nozzle,bed');

      await waitFor(() => {
        expect(screen.getByText('220°C')).toBeInTheDocument();
      });
      // Nozzle is 219.6 against a target of 220: both round to 220, so the
      // "/ 220°C" half is dropped rather than reading "220 / 220°C" all print.
      expect(screen.queryByText('/')).not.toBeInTheDocument();
      expect(screen.getByText('60°C')).toBeInTheDocument();
    });

    it('shows the target while the heater is still climbing', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () =>
          HttpResponse.json({ ...withTemps, temperatures: { nozzle: 140, nozzle_target: 220 } }),
        ),
      );
      renderOverlayPage(1, '?show=nozzle');

      await waitFor(() => {
        expect(screen.getByText('140°C')).toBeInTheDocument();
      });
      expect(screen.getByText('220°C')).toBeInTheDocument();
    });

    it('skips a reading the printer does not report', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () =>
          // A P1S: the backend drops chamber for models without a real sensor,
          // so asking for it in ?show= must not produce an empty row.
          HttpResponse.json({ ...withTemps, temperatures: { nozzle: 200, bed: 55 } }),
        ),
      );
      renderOverlayPage(1, '?show=nozzle,bed,chamber');

      await waitFor(() => {
        expect(screen.getByText('Nozzle')).toBeInTheDocument();
      });
      expect(screen.queryByText('Chamber')).not.toBeInTheDocument();
    });

    it('draws both nozzles on a dual-nozzle printer', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () =>
          HttpResponse.json({
            ...withTemps,
            temperatures: { nozzle: 220, nozzle_2: 250, nozzle_2_target: 250 },
          }),
        ),
      );
      renderOverlayPage(1, '?show=nozzle');

      await waitFor(() => {
        expect(screen.getByText('Nozzle')).toBeInTheDocument();
      });
      expect(screen.getByText('Nozzle 2')).toBeInTheDocument();
      expect(screen.getByText('250°C')).toBeInTheDocument();
    });

    it('draws temperatures while the printer is idle', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () =>
          HttpResponse.json({ ...mockStatusIdle, temperatures: { bed: 45, bed_target: 60 } }),
        ),
      );
      renderOverlayPage(1, '?show=bed');

      await waitFor(() => {
        expect(screen.getByText('Printer is idle')).toBeInTheDocument();
      });
      // A preheating printer is exactly when the readings are worth watching,
      // so they are not gated behind a running print.
      expect(screen.getByText('45°C')).toBeInTheDocument();
      expect(screen.getByText('60°C')).toBeInTheDocument();
    });

    it('reads temperatures from the token-authed feed in kiosk mode', async () => {
      server.use(
        http.get('/api/v1/printers/:id/overlay-status', () =>
          HttpResponse.json({
            id: 1,
            name: 'X1 Carbon',
            camera_rotation: 0,
            connected: true,
            state: 'RUNNING',
            current_print: 'KioskBenchy.gcode.3mf',
            gcode_file: 'plate_1.gcode',
            progress: 67,
            remaining_time: 40,
            layer_num: 10,
            total_layers: 20,
            stg_cur_name: null,
            temperatures: { chamber: 38, chamber_target: 40 },
            time_format: 'system',
          }),
        ),
      );
      renderOverlayPage(1, '?token=obs-tok&show=chamber');

      await waitFor(() => {
        expect(screen.getByText('Chamber')).toBeInTheDocument();
      });
      expect(screen.getByText('38°C')).toBeInTheDocument();
    });
  });
});
