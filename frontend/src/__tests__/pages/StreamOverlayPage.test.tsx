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
        <ThemeProvider>
          <ToastProvider>
            <Routes>
              <Route path="/overlay/:printerId" element={<StreamOverlayPage />} />
            </Routes>
          </ToastProvider>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe('StreamOverlayPage', () => {
  const originalTitle = document.title;

  beforeEach(() => {
    // Mock WebSocket
    vi.stubGlobal('WebSocket', vi.fn().mockImplementation(() => ({
      close: vi.fn(),
      onmessage: null,
      onerror: null,
    })));

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
});
