/**
 * A Camera Wall tile in a home-screen web app (#3419).
 *
 * A tile opens the camera with window.open, and iOS loads that URL into the
 * web app itself, full screen. The camera then opens in place instead, where
 * its back button returns to the wall.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render as rtlRender, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';

vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({
      authEnabled: false,
      loading: false,
      user: null,
      authUnconfirmed: false,
      hasPermission: () => true,
    }),
  };
});

import { CamWallPage } from '../../pages/CamWallPage';
import { api } from '../../api/client';

function renderWall() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/camwall']}>
        <ThemeProvider>
          <ToastProvider>
            <Routes>
              <Route path="/camwall" element={<CamWallPage />} />
              <Route path="/camera/:printerId" element={<p>camera page</p>} />
            </Routes>
          </ToastProvider>
        </ThemeProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('CamWallPage — tile click', () => {
  let open: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.spyOn(api, 'getPrinters').mockResolvedValue([{ id: 3, name: 'X1C-Lab' }] as never);
    vi.spyOn(api, 'getPrinterStatus').mockResolvedValue({ connected: true, state: 'IDLE' } as never);
    open = vi.spyOn(window, 'open').mockReturnValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    delete (navigator as Navigator & { standalone?: boolean }).standalone;
  });

  it('opens the camera in its own window in a browser', async () => {
    renderWall();
    fireEvent.click(await screen.findByTitle('X1C-Lab'));

    expect(open).toHaveBeenCalledWith('/camera/3', 'camera-3');
  });

  it('opens the camera in place in a home-screen web app', async () => {
    Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });
    renderWall();
    fireEvent.click(await screen.findByTitle('X1C-Lab'));

    expect(await screen.findByText('camera page')).toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
  });
});
