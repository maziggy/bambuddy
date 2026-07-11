/**
 * Standalone Cam Wall page (#2531).
 *
 * The assertions that carry weight are the kiosk ones: a wall on a TV must not
 * offer controls it cannot honour, must not name the file on the bed, and must
 * carry its token into the <img> URLs — an MJPEG tag has no Authorization
 * header, so a missing token means a wall of broken images.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render as rtlRender } from '@testing-library/react';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { AuthProvider } from '../../contexts/AuthContext';
import { CamWallPage } from '../../pages/CamWallPage';
import { api, getStreamToken } from '../../api/client';

const KIOSK_TOKEN = 'bblt_abcdefgh_secretsecretsecret';

const FEED = [
  {
    id: 7,
    name: 'X1C-Lab',
    camera_rotation: 0,
    connected: true,
    state: 'RUNNING',
    progress: 42,
    remaining_time: 33,
    layer_num: 120,
    total_layers: 300,
    hms_errors: [],
  },
];

// The page renders outside the app layout, so it supplies its own route context.
// The shared render() util hard-codes BrowserRouter with no way to seed a query
// string, and the query string is the whole point here.
function renderAt(search: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return rtlRender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/camwall${search}`]}>
        <AuthProvider>
          <ThemeProvider>
            <ToastProvider>
              <CamWallPage />
            </ToastProvider>
          </ThemeProvider>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('CamWallPage — kiosk mode', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getCamWallPrinters').mockResolvedValue(FEED);
    // AuthProvider probes /auth/me on mount; a kiosk browser has no session.
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(null, { status: 401 }));
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches the wall with the URL token and renders a tile per printer', async () => {
    renderAt(`?token=${KIOSK_TOKEN}`);

    await waitFor(() => expect(screen.getByText('X1C-Lab')).toBeInTheDocument());
    expect(api.getCamWallPrinters).toHaveBeenCalledWith(KIOSK_TOKEN);
  });

  it('carries the token into the stream URL', async () => {
    renderAt(`?token=${KIOSK_TOKEN}`);

    await waitFor(() => expect(screen.getByText('X1C-Lab')).toBeInTheDocument());
    // Tiles start paused (jsdom's IntersectionObserver never reports a tile as
    // on-screen), and a paused tile renders no <img> at all. So assert on the
    // module-level token those URLs are built from, which is what CameraTile
    // reads through withStreamToken().
    expect(getStreamToken()).toBe(KIOSK_TOKEN);
  });

  it('offers no settings popover — a TV has nobody standing at it', async () => {
    renderAt(`?token=${KIOSK_TOKEN}`);

    await waitFor(() => expect(screen.getByText('X1C-Lab')).toBeInTheDocument());
    expect(screen.queryByTitle('Cam wall settings')).not.toBeInTheDocument();
  });

  it('renders tiles inert — click-through would need a session the token has not got', async () => {
    renderAt(`?token=${KIOSK_TOKEN}`);

    await waitFor(() => expect(screen.getByText('X1C-Lab')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /X1C-Lab/ })).not.toBeInTheDocument();
  });

  it('refuses to honour ?status=full — a kiosk wall never names the part on the bed', async () => {
    renderAt(`?token=${KIOSK_TOKEN}&status=full`);

    await waitFor(() => expect(screen.getByText('X1C-Lab')).toBeInTheDocument());
    // 'full' is what adds the progress/layer strip. Capped to 'compact', so the
    // strip must be absent even though the feed says the printer is at 42%.
    expect(screen.queryByText('42%')).not.toBeInTheDocument();
    expect(screen.queryByText(/Layer 120/)).not.toBeInTheDocument();
  });

  it('says so plainly when the token has expired or been revoked', async () => {
    vi.spyOn(api, 'getCamWallPrinters').mockRejectedValue(new Error('401'));
    renderAt(`?token=${KIOSK_TOKEN}`);

    await waitFor(() =>
      expect(screen.getByText(/no longer valid/i)).toBeInTheDocument(),
    );
  });
});

describe('CamWallPage — signed out, no token', () => {
  beforeEach(() => {
    vi.spyOn(api, 'getCamWallPrinters').mockResolvedValue(FEED);
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('does not reach for the kiosk feed without a token', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue(new Response(null, { status: 401 }));
    renderAt('');
    await act(async () => {
      await Promise.resolve();
    });

    // No token means this is an ordinary app page; it must fall back to the
    // session-authenticated printers API (or bounce to /login), never to the
    // kiosk endpoint.
    expect(api.getCamWallPrinters).not.toHaveBeenCalled();
  });
});
