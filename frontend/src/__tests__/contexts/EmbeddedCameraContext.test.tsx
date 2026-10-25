/**
 * Camera overlays from any page (#2737). They used to live in the Printers
 * page, so they disappeared on every other page.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render as rtlRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { AuthProvider } from '../../contexts/AuthContext';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { EmbeddedCameraProvider } from '../../contexts/EmbeddedCameraContext';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render } from '../utils';
import { CameraLauncher } from '../../components/CameraLauncher';
import { setAuthToken } from '../../api/client';
import { requestCameraPopIn } from '../../utils/camera';

const PRINTERS = [
  { id: 1, name: 'Workshop X1C' },
  { id: 2, name: 'Office P1S' },
];

// The setup file stubs localStorage out; this backs it so what the host keeps
// there can be read back.
const storage = new Map<string, string>();
function backStorage() {
  storage.clear();
  vi.mocked(localStorage.getItem).mockImplementation((key: string) => storage.get(key) ?? null);
  vi.mocked(localStorage.setItem).mockImplementation((key: string, value: string) => {
    storage.set(key, value);
  });
  vi.mocked(localStorage.removeItem).mockImplementation((key: string) => {
    storage.delete(key);
  });
}

function SomeOtherPage() {
  return <h1>Archives</h1>;
}

function overlayCount() {
  // "Refresh stream" is the overlay's own control.
  return screen.queryAllByTitle('Refresh stream').length;
}

// The host loads the viewer on first use; a cold import under a busy test run
// can outlast waitFor's second.
beforeAll(async () => {
  await import('../../components/EmbeddedCameraViewer');
});

describe('EmbeddedCameraProvider', () => {
  beforeEach(() => {
    backStorage();
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(PRINTERS)),
      http.get('/api/v1/printers/:id', ({ params }) =>
        HttpResponse.json(PRINTERS.find(p => p.id === Number(params.id)) ?? null, {
          status: PRINTERS.some(p => p.id === Number(params.id)) ? 200 : 404,
        }),
      ),
      http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
      http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it('shows an overlay left open on any page, not only on Printers', async () => {
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));

    render(<SomeOtherPage />);

    await waitFor(() => expect(overlayCount()).toBe(1));
    expect(screen.getByText('Archives')).toBeInTheDocument();
  });

  it('drops an overlay for a printer the user cannot see', async () => {
    localStorage.setItem(
      'openEmbeddedCameras',
      JSON.stringify([{ id: 2, name: 'Office P1S' }, { id: 99, name: 'Deleted' }]),
    );

    render(<SomeOtherPage />);

    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('openEmbeddedCameras')!)).toEqual([{ id: 2, name: 'Office P1S' }]),
    );
    await waitFor(() => expect(overlayCount()).toBe(1));
  });

  it('starts with none open from a corrupt entry', async () => {
    localStorage.setItem('openEmbeddedCameras', '{not json');

    render(<SomeOtherPage />);

    expect(await screen.findByText('Archives')).toBeInTheDocument();
    expect(overlayCount()).toBe(0);
  });

  it('skips a stored entry without a valid printer id', async () => {
    // The printer list is not available, so nothing is pruned against it.
    server.use(http.get('/api/v1/printers/', () => new HttpResponse(null, { status: 500 })));
    localStorage.setItem(
      'openEmbeddedCameras',
      JSON.stringify([{ name: 'no id' }, { id: '2', name: 'string id' }, { id: 0 }, null, { id: 2, name: 'Office P1S' }]),
    );

    render(<SomeOtherPage />);

    await waitFor(() => expect(overlayCount()).toBe(1));
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(overlayCount()).toBe(1);
  });

  it('opens an overlay a camera window pops back in', async () => {
    render(<SomeOtherPage />);
    await screen.findByText('Archives');

    await waitFor(async () =>
      expect(await requestCameraPopIn(1, 'Workshop X1C', null, { answerMs: 50 })).toBe(true),
    );

    await waitFor(() => expect(overlayCount()).toBe(1));
  });

  it('shows a stored overlay only once the printer list is in', async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    server.use(http.get('/api/v1/printers/', async () => { await gate; return HttpResponse.json(PRINTERS); }));
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));

    render(<SomeOtherPage />);
    await screen.findByText('Archives');
    await new Promise(resolve => setTimeout(resolve, 50));
    // The cache may still hold the last user's list; nothing streams before
    // this user's has been checked.
    expect(overlayCount()).toBe(0);

    release();
    await waitFor(() => expect(overlayCount()).toBe(1));
  });

  it('refuses a pop-in for a printer the user cannot see, so the window keeps it', async () => {
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));
    render(<SomeOtherPage />);
    await waitFor(() => expect(overlayCount()).toBe(1));

    await expect(requestCameraPopIn(99, 'Not yours', null, { answerMs: 50 })).resolves.toBe(false);

    expect(JSON.parse(localStorage.getItem('openEmbeddedCameras')!)).toEqual([{ id: 2, name: 'Office P1S' }]);
    expect(overlayCount()).toBe(1);
  });

  it('checks a pop-in against the list as it is now, not the cached one', async () => {
    // An open overlay has the list fetched and cached.
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));
    render(<SomeOtherPage />);
    await waitFor(() => expect(overlayCount()).toBe(1));
    // Printer 3 was added in another tab since this one last fetched.
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json([...PRINTERS, { id: 3, name: 'New A1' }])),
      http.get('/api/v1/printers/:id', ({ params }) => HttpResponse.json({ id: Number(params.id), name: 'New A1' })),
    );

    await expect(requestCameraPopIn(3, 'New A1', null, { answerMs: 50 })).resolves.toBe(true);

    await waitFor(() => expect(overlayCount()).toBe(2));
  });

  describe('remounted with the printer list still cached', () => {
    // Back from /camwall, or signed in again without a reload: the provider
    // mounts anew while the cache holds a list under a minute old.
    function renderWithCache(cached: Array<{ id: number; name: string }>) {
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 60_000 } } });
      queryClient.setQueryData(['printers'], cached);
      return rtlRender(
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <AuthProvider>
              <ThemeProvider>
                <ToastProvider>
                  <EmbeddedCameraProvider>
                    <SomeOtherPage />
                  </EmbeddedCameraProvider>
                </ToastProvider>
              </ThemeProvider>
            </AuthProvider>
          </BrowserRouter>
        </QueryClientProvider>,
      );
    }

    it('still shows the stored overlay', async () => {
      localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));

      renderWithCache(PRINTERS);

      await waitFor(() => expect(overlayCount()).toBe(1));
    });

    it('checks a pop-in against the server, not the minute-old list', async () => {
      localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));
      renderWithCache(PRINTERS);
      await waitFor(() => expect(overlayCount()).toBe(1));
      server.use(
        http.get('/api/v1/printers/', () => HttpResponse.json([...PRINTERS, { id: 3, name: 'New A1' }])),
        http.get('/api/v1/printers/:id', ({ params }) => HttpResponse.json({ id: Number(params.id), name: 'New A1' })),
      );

      await expect(requestCameraPopIn(3, 'New A1', null, { answerMs: 50 })).resolves.toBe(true);
    });

    it('goes by the list fetched now, not the last user\'s cached one', async () => {
      localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));
      let release!: () => void;
      const gate = new Promise<void>(resolve => { release = resolve; });
      server.use(http.get('/api/v1/printers/', async () => { await gate; return HttpResponse.json([PRINTERS[0]]); }));

      renderWithCache(PRINTERS);
      await screen.findByText('Archives');
      await new Promise(resolve => setTimeout(resolve, 50));
      // The cached list has printer 2; this user's, still on its way, has not.
      expect(overlayCount()).toBe(0);

      release();
      await waitFor(() => expect(localStorage.getItem('openEmbeddedCameras')).toBeNull());
      expect(overlayCount()).toBe(0);
    });
  });

  it('keeps what other tabs stored when this one opens and closes a camera', async () => {
    render(
      <>
        <SomeOtherPage />
        <CameraLauncher placement="side" />
      </>,
    );
    await screen.findByText('Archives');
    // Another tab opened camera 1 after this one loaded.
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 1, name: 'Workshop X1C' }]));

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));
    fireEvent.click(await screen.findByRole('button', { name: /Office P1S/ }));
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('openEmbeddedCameras')!)).toEqual([
        { id: 1, name: 'Workshop X1C' },
        { id: 2, name: 'Office P1S' },
      ]),
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));
    fireEvent.click(await screen.findByRole('button', { name: /Office P1S/ }));
    await waitFor(() =>
      expect(JSON.parse(localStorage.getItem('openEmbeddedCameras')!)).toEqual([{ id: 1, name: 'Workshop X1C' }]),
    );
  });

  describe.each([
    ['camera:view', ['printers:read']],
    ['printers:read', ['camera:view']],
  ])('without %s', (_missing, permissions) => {
    beforeEach(() => {
      server.use(
        http.get('*/api/v1/auth/status', () => HttpResponse.json({ auth_enabled: true, requires_setup: false })),
        http.get('*/api/v1/auth/me', () =>
          HttpResponse.json({
            id: 2,
            username: 'viewer',
            role: 'user',
            is_active: true,
            is_admin: false,
            groups: [],
            permissions,
            created_at: '2026-01-01T00:00:00Z',
          }),
        ),
      );
      setAuthToken('test-token', 'session');
    });

    it('shows no overlay and no launcher', async () => {
      localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));

      render(
        <>
          <SomeOtherPage />
          <CameraLauncher placement="side" />
        </>,
      );

      await screen.findByText('Archives');
      // Give the permissions time to arrive, then nothing camera-related is up.
      await new Promise(resolve => setTimeout(resolve, 100));
      expect(overlayCount()).toBe(0);
      expect(screen.queryByRole('button', { name: 'Cameras' })).toBeNull();
    });

    it('does not take a camera window popping in', async () => {
      render(<SomeOtherPage />);
      await screen.findByText('Archives');
      await new Promise(resolve => setTimeout(resolve, 100));

      await expect(requestCameraPopIn(1, 'Workshop X1C', null, { answerMs: 50 })).resolves.toBe(false);
    });
  });
});

describe('CameraLauncher', () => {
  beforeEach(() => {
    backStorage();
    server.use(
      http.get('/api/v1/printers/', () => HttpResponse.json(PRINTERS)),
      http.get('/api/v1/printers/:id', ({ params }) =>
        HttpResponse.json(PRINTERS.find(p => p.id === Number(params.id))),
      ),
      http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
      http.get('/api/v1/printers/:id/camera/status', () => HttpResponse.json({ active: true, stalled: false })),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('lists the printers and opens the picked one as an overlay', async () => {
    render(<CameraLauncher placement="side" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));
    fireEvent.click(await screen.findByRole('button', { name: /Office P1S/ }));

    await waitFor(() => expect(overlayCount()).toBe(1));
    // A quick look: the list gets out of the way.
    expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull();
    expect(JSON.parse(localStorage.getItem('openEmbeddedCameras')!)).toEqual([{ id: 2, name: 'Office P1S' }]);
  });

  it('marks an open camera and closes it from the list', async () => {
    localStorage.setItem('openEmbeddedCameras', JSON.stringify([{ id: 2, name: 'Office P1S' }]));
    render(<CameraLauncher placement="down" />);
    await waitFor(() => expect(overlayCount()).toBe(1));

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));
    const item = await screen.findByRole('button', { name: /Office P1S/ });
    expect(item).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /Workshop X1C/ })).toHaveAttribute('aria-pressed', 'false');

    fireEvent.click(item);

    await waitFor(() => expect(overlayCount()).toBe(0));
    expect(localStorage.getItem('openEmbeddedCameras')).toBeNull();
  });

  it('closes the list on Escape and on a click elsewhere', async () => {
    render(
      <>
        <p>outside</p>
        <CameraLauncher placement="side" />
      </>,
    );
    const button = await screen.findByRole('button', { name: 'Cameras' });

    fireEvent.click(button);
    expect(await screen.findByRole('region', { name: 'Cameras' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull();

    fireEvent.click(button);
    expect(await screen.findByRole('region', { name: 'Cameras' })).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByText('outside'));
    expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull();
  });

  it('draws the list at page level, above the overlays', async () => {
    render(
      <aside className="fixed z-30">
        <CameraLauncher placement="side" />
      </aside>,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));

    const list = await screen.findByRole('region', { name: 'Cameras' });
    // Inside the sidebar it would be capped at the sidebar's z-30 and clipped
    // by its scrolling footer.
    expect(list.closest('aside')).toBeNull();
    expect(list.className).toContain('fixed');
    expect(list.className).toContain('z-50');
  });

  it('stays open for a press inside the list, which is outside the button', async () => {
    render(<CameraLauncher placement="side" />);
    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));

    const item = await screen.findByRole('button', { name: /Office P1S/ });
    fireEvent.pointerDown(item);

    expect(screen.getByRole('region', { name: 'Cameras' })).toBeInTheDocument();
  });

  it('takes keyboard focus into the list and back to the button on Escape', async () => {
    render(<CameraLauncher placement="side" />);
    const button = await screen.findByRole('button', { name: 'Cameras' });

    fireEvent.click(button);

    // The list is drawn at the end of the page, out of the button's Tab order.
    await waitFor(() => expect(screen.getByRole('button', { name: /Workshop X1C/ })).toHaveFocus());
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(button).toHaveFocus();
  });

  it('closes when focus leaves the list', async () => {
    render(
      <>
        <button>elsewhere</button>
        <CameraLauncher placement="side" />
      </>,
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));
    const item = await screen.findByRole('button', { name: /Workshop X1C/ });

    fireEvent.blur(item, { relatedTarget: screen.getByRole('button', { name: 'elsewhere' }) });

    expect(screen.queryByRole('region', { name: 'Cameras' })).toBeNull();
  });

  it('says so when there are no printers', async () => {
    server.use(http.get('/api/v1/printers/', () => HttpResponse.json([])));
    render(<CameraLauncher placement="side" />);

    fireEvent.click(await screen.findByRole('button', { name: 'Cameras' }));

    expect(await screen.findByText('No printers')).toBeInTheDocument();
  });
});
