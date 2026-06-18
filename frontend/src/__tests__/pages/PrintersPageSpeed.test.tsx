/**
 * Tests for the print speed control feature on the PrintersPage.
 *
 * The printer-card refactor in #1661 replaced the visible "100%" / "50%"
 * speed badge with an icon-only Gauge button. These tests now target the
 * button via data-testid="speed-control" instead of the percentage text.
 * The dropdown still shows the same translated labels (Silent (50%),
 * Standard (100%), Sport (124%), Ludicrous (166%)).
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { PrintersPage } from '../../pages/PrintersPage';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';

const mockPrinters = [
  {
    id: 1,
    name: 'X1 Carbon',
    ip_address: '192.168.1.100',
    serial_number: '00M09A350100001',
    access_code: '12345678',
    model: 'X1C',
    enabled: true,
    nozzle_diameter: 0.4,
    nozzle_type: 'hardened_steel',
    location: 'Workshop',
    auto_archive: true,
    created_at: '2024-01-01T00:00:00Z',
    updated_at: '2024-01-01T00:00:00Z',
  },
];

const mockPrintingStatus = {
  connected: true,
  state: 'RUNNING',
  progress: 42,
  layer_num: 10,
  total_layers: 100,
  temperatures: {
    nozzle: 220,
    bed: 60,
    chamber: 35,
  },
  remaining_time: 3600,
  filename: 'test_print.3mf',
  wifi_signal: -50,
  vt_tray: [],
  speed_level: 2,
};

const mockIdleStatus = {
  connected: true,
  state: 'IDLE',
  progress: 0,
  layer_num: 0,
  total_layers: 0,
  temperatures: {
    nozzle: 25,
    bed: 25,
    chamber: 25,
  },
  remaining_time: 0,
  filename: null,
  wifi_signal: -50,
  vt_tray: [],
  speed_level: 2,
};

describe('PrintersPage - Print Speed Control', () => {
  beforeEach(() => {
    server.use(
      http.get('/api/v1/printers/', () => {
        return HttpResponse.json(mockPrinters);
      }),
      http.get('/api/v1/queue/', () => {
        return HttpResponse.json([]);
      })
    );
  });

  describe('speed control button', () => {
    it('renders and is enabled when printer is printing', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockPrintingStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        const button = screen.getByTestId('speed-control');
        expect(button).toBeInTheDocument();
        expect(button).toBeEnabled();
      });
    });

    it('is disabled when printer is idle', async () => {
      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockIdleStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        const button = screen.getByTestId('speed-control');
        expect(button).toBeDisabled();
      });
    });
  });

  describe('speed dropdown menu', () => {
    it('opens speed menu on click when printing', async () => {
      const user = userEvent.setup();

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockPrintingStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByTestId('speed-control')).toBeEnabled();
      });

      await user.click(screen.getByTestId('speed-control'));

      await waitFor(() => {
        expect(screen.getByText('Silent (50%)')).toBeInTheDocument();
        expect(screen.getByText('Standard (100%)')).toBeInTheDocument();
        expect(screen.getByText('Sport (124%)')).toBeInTheDocument();
        expect(screen.getByText('Ludicrous (166%)')).toBeInTheDocument();
      });
    });

    it('displays all four speed options in the dropdown', async () => {
      const user = userEvent.setup();

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockPrintingStatus);
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByTestId('speed-control')).toBeEnabled();
      });

      await user.click(screen.getByTestId('speed-control'));

      await waitFor(() => {
        const options = [
          screen.getByText('Silent (50%)'),
          screen.getByText('Standard (100%)'),
          screen.getByText('Sport (124%)'),
          screen.getByText('Ludicrous (166%)'),
        ];
        expect(options).toHaveLength(4);
        options.forEach((opt) => expect(opt).toBeInTheDocument());
      });
    });

    it('calls the API with the correct mode when a speed option is selected', async () => {
      const user = userEvent.setup();
      let capturedMode: number | null = null;

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockPrintingStatus);
        }),
        http.post('/api/v1/printers/:id/print-speed', async ({ request }) => {
          const url = new URL(request.url);
          capturedMode = Number(url.searchParams.get('mode'));
          return HttpResponse.json({ success: true, message: 'Speed set' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByTestId('speed-control')).toBeEnabled();
      });

      await user.click(screen.getByTestId('speed-control'));

      await waitFor(() => {
        expect(screen.getByText('Sport (124%)')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Sport (124%)'));

      await waitFor(() => {
        expect(capturedMode).toBe(3);
      });
    });

    it('closes the dropdown after selecting a speed option', async () => {
      const user = userEvent.setup();

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json(mockPrintingStatus);
        }),
        http.post('/api/v1/printers/:id/print-speed', () => {
          return HttpResponse.json({ success: true, message: 'Speed set' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByTestId('speed-control')).toBeEnabled();
      });

      await user.click(screen.getByTestId('speed-control'));

      await waitFor(() => {
        expect(screen.getByText('Silent (50%)')).toBeInTheDocument();
      });

      await user.click(screen.getByText('Silent (50%)'));

      await waitFor(() => {
        expect(screen.queryByText('Silent (50%)')).not.toBeInTheDocument();
      });
    });

    it.each([
      { mode: 1, label: 'Silent (50%)' },
      { mode: 2, label: 'Standard (100%)' },
      { mode: 3, label: 'Sport (124%)' },
      { mode: 4, label: 'Ludicrous (166%)' },
    ])('selecting $label sends mode=$mode', async ({ mode, label }) => {
      const user = userEvent.setup();
      let capturedMode: number | null = null;

      server.use(
        http.get('/api/v1/printers/:id/status', () => {
          return HttpResponse.json({ ...mockPrintingStatus, speed_level: 2 });
        }),
        http.post('/api/v1/printers/:id/print-speed', async ({ request }) => {
          const url = new URL(request.url);
          capturedMode = Number(url.searchParams.get('mode'));
          return HttpResponse.json({ success: true, message: 'Speed set' });
        })
      );

      render(<PrintersPage />);

      await waitFor(() => {
        expect(screen.getByTestId('speed-control')).toBeEnabled();
      });

      await user.click(screen.getByTestId('speed-control'));

      await waitFor(() => {
        expect(screen.getByText(label)).toBeInTheDocument();
      });

      await user.click(screen.getByText(label));

      await waitFor(() => {
        expect(capturedMode).toBe(mode);
      });
    });
  });
});
