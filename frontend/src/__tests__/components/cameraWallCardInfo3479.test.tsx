import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { render as rtlRender } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { render } from '../utils';
import { ToastProvider } from '../../contexts/ToastContext';
import { api } from '../../api/client';
import { CamWallPage } from '../../pages/CamWallPage';
import { CameraTile } from '../../components/CameraTile';
import { CameraWall, type CameraWallStatus } from '../../components/CameraWall';

// The camera wall tiles carry what a printer card shows at a glance: the
// heaters and the finish time in the full overlay, and pause/resume, stop and
// the chamber light for a user allowed to control printers (#3479).

let canControl = true;
let signedInUser: { id: number; username: string } | null = null;
vi.mock('../../contexts/AuthContext', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../contexts/AuthContext')>();
  return {
    ...actual,
    useAuth: () => ({
      hasPermission: (p: string) => (p === 'printers:control' ? canControl : true),
      hasAnyPermission: () => true,
      authEnabled: true,
      user: signedInUser,
      loading: false,
    }),
  };
});

const PRINTER = { id: 5, name: 'Farm-5' };
const PRINTING: CameraWallStatus = {
  connected: true,
  state: 'RUNNING',
  progress: 40,
  remaining_time: 90,
  temperatures: { nozzle: 219.6, bed: 60, chamber: 34.4 },
  chamber_light: false,
};

let posted: string[];

beforeEach(() => {
  canControl = true;
  signedInUser = null;
  posted = [];
  vi.spyOn(global, 'fetch');
  server.use(
    http.post('/api/v1/printers/:id/print/:action', ({ request, params }) => {
      posted.push(`${params.action} ${new URL(request.url).pathname}`);
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
    http.post('/api/v1/printers/:id/chamber-light', ({ request }) => {
      posted.push(`light ${new URL(request.url).searchParams.get('on')}`);
      return HttpResponse.json({ success: true, message: 'ok' });
    }),
    http.post('/api/v1/printers/:id/camera/stop', () => HttpResponse.json({ success: true })),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

function renderWall(
  status: CameraWallStatus,
  props: Partial<Parameters<typeof CameraWall>[0]> = { showControls: true },
) {
  return render(
    <CameraWall
      printers={[PRINTER]}
      maxLive={0}
      snapshotIntervalSec={8}
      statusMode="full"
      statuses={new Map([[PRINTER.id, status]])}
      onChangeMaxLive={() => {}}
      onChangeSnapshotIntervalSec={() => {}}
      onChangeStatusMode={() => {}}
      onTileClick={() => {}}
      timeFormat="24h"
      {...props}
    />,
  );
}

describe('tile information', () => {
  function tile(props: Partial<Parameters<typeof CameraTile>[0]>) {
    return render(
      <CameraTile
        printerId={5}
        printerName="Farm-5"
        mode="paused"
        snapshotIntervalMs={8000}
        connected
        statusMode="full"
        printerState="RUNNING"
        remainingMin={90}
        temperatures={{ nozzle: 219.6, nozzle_2: 180, bed: 60, chamber: 34.4 }}
        timeFormat="24h"
        {...props}
      />,
    );
  }

  it('shows every heater, both nozzles of a dual-nozzle printer, rounded', () => {
    tile({});
    expect(screen.getByTitle('Nozzle')).toHaveTextContent('220° / 180°');
    expect(screen.getByTitle('Bed')).toHaveTextContent('60°');
    expect(screen.getByTitle('Chamber')).toHaveTextContent('34°');
  });

  it('leaves out a heater the printer does not report', () => {
    tile({ temperatures: { nozzle: 200, bed: 55 } });
    expect(screen.queryByTitle('Chamber')).not.toBeInTheDocument();
  });

  it('shows the heaters on an idle printer too', () => {
    tile({ printerState: 'IDLE', remainingMin: null });
    expect(screen.getByTitle('Bed')).toHaveTextContent('60°');
  });

  it('shows the finish time while printing', () => {
    tile({});
    expect(screen.getByText(/^ETA \d{2}:\d{2}$|^ETA Tomorrow \d{2}:\d{2}$/)).toBeInTheDocument();
  });

  it('shows no finish time for a paused print, whose end moves with the pause', () => {
    tile({ printerState: 'PAUSE' });
    expect(screen.getByText(/left$/)).toBeInTheDocument();
    expect(screen.queryByText(/^ETA/)).not.toBeInTheDocument();
  });

  it('keeps the compact overlay to the state badge', () => {
    tile({ statusMode: 'compact' });
    expect(screen.queryByTitle('Bed')).not.toBeInTheDocument();
    expect(screen.queryByText(/^ETA/)).not.toBeInTheDocument();
  });

  it('shows no heaters for an offline printer', () => {
    tile({ connected: false });
    expect(screen.queryByTitle('Bed')).not.toBeInTheDocument();
  });
});

describe('tile controls', () => {
  it('pauses a print after the same confirmation as the card', async () => {
    const user = userEvent.setup();
    renderWall(PRINTING);

    await user.click(await screen.findByRole('button', { name: 'Pause' }));
    expect(posted).toEqual([]);
    await screen.findByText(/pause the current print on "Farm-5"/);
    await user.click(screen.getByRole('button', { name: 'Pause Print' }));

    await waitFor(() => expect(posted).toEqual(['pause /api/v1/printers/5/print/pause']));
  });

  it('resumes a paused print', async () => {
    const user = userEvent.setup();
    renderWall({ ...PRINTING, state: 'PAUSE' });

    await user.click(await screen.findByRole('button', { name: 'Resume' }));
    await screen.findByText(/resume the print on "Farm-5"/);
    await user.click(screen.getByRole('button', { name: 'Resume Print' }));

    await waitFor(() => expect(posted).toEqual(['resume /api/v1/printers/5/print/resume']));
  });

  it('stops a print only after it is confirmed', async () => {
    const user = userEvent.setup();
    renderWall(PRINTING);

    await user.click(await screen.findByRole('button', { name: 'Stop' }));
    await screen.findByText(/stop the current print on "Farm-5"/);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(posted).toEqual([]);

    await user.click(screen.getByRole('button', { name: 'Stop' }));
    await screen.findByText(/stop the current print on "Farm-5"/);
    await user.click(screen.getByRole('button', { name: 'Stop Print' }));
    await waitFor(() => expect(posted).toEqual(['stop /api/v1/printers/5/print/stop']));
  });

  it('switches the chamber light', async () => {
    const user = userEvent.setup();
    renderWall(PRINTING);

    await user.click(await screen.findByRole('button', { name: 'Turn on chamber light' }));
    await waitFor(() => expect(posted).toEqual(['light true']));
  });

  it('offers only the light on a printer that is not printing', async () => {
    renderWall({ ...PRINTING, state: 'IDLE' });

    expect(await screen.findByRole('button', { name: 'Turn on chamber light' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
  });

  it('gives a user without printers:control no controls', async () => {
    canControl = false;
    renderWall(PRINTING);

    // The tile itself is there; only its controls are not.
    expect(await screen.findByTitle('Bed')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chamber light/ })).not.toBeInTheDocument();
  });

  it('gives an offline printer no controls', async () => {
    renderWall({ ...PRINTING, connected: false });

    expect(screen.queryByRole('button', { name: /chamber light/ })).not.toBeInTheDocument();
  });

  it('gives a wall without controls (the kiosk) none', async () => {
    renderWall(PRINTING, { showControls: false });

    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chamber light/ })).not.toBeInTheDocument();
  });
});

describe('kiosk wall', () => {
  it('has no controls, also where the browser could control printers', async () => {
    vi.spyOn(api, 'getCamWallPrinters').mockResolvedValue([
      { id: 7, name: 'X1C-Lab', camera_rotation: 0, connected: true, state: 'RUNNING', progress: 42, remaining_time: 33, layer_num: 1, total_layers: 2, hms_errors: [] },
    ]);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    rtlRender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/camwall?token=bblt_abcdefgh_secretsecretsecret']}>
          <ToastProvider>
            <CamWallPage />
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('X1C-Lab')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /chamber light/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pause' })).not.toBeInTheDocument();
  });
});

describe('when the tile controls show', () => {
  const controls = () => screen.getByTestId('camwall-tile-controls');

  it('waits for hover by default', async () => {
    renderWall(PRINTING);
    await screen.findByRole('button', { name: 'Pause' });
    expect(controls().className).toContain('opacity-0');
    expect(controls().className).toContain('group-hover/tile:opacity-100');
    // No hover on a touch screen: shown there all the same.
    expect(controls().className).toContain('[@media(hover:none)]:opacity-100');
  });

  it('shows them all the time when set to always', async () => {
    renderWall(PRINTING, { showControls: true, controlsMode: 'always', onChangeControlsMode: () => {} });
    await screen.findByRole('button', { name: 'Pause' });
    expect(controls().className).not.toContain('opacity-0');
  });

  it('is chosen in the wall settings', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    renderWall(PRINTING, { showControls: true, controlsMode: 'hover', onChangeControlsMode: onChange });

    await user.click(screen.getByRole('button', { name: 'Cam wall settings' }));
    const group = screen.getByRole('radiogroup', { name: 'Tile controls' });
    expect(within(group).getByRole('radio', { name: 'On hover' })).toHaveAttribute('aria-checked', 'true');
    await user.click(within(group).getByRole('radio', { name: 'Always' }));
    expect(onChange).toHaveBeenCalledWith('always');
  });

  it('is not offered on a wall without controls', async () => {
    const user = userEvent.setup();
    renderWall(PRINTING, { showControls: false, onChangeControlsMode: () => {} });

    await user.click(screen.getByRole('button', { name: 'Cam wall settings' }));
    expect(screen.queryByRole('radiogroup', { name: 'Tile controls' })).not.toBeInTheDocument();
  });
});

describe('the /camwall page, signed in', () => {
  it('follows the choice saved in this browser', async () => {
    signedInUser = { id: 1, username: 'farm' };
    vi.mocked(localStorage.getItem).mockImplementation((key: string) => (key === 'camWallControls' ? 'always' : null));
    vi.spyOn(api, 'getPrinters').mockResolvedValue([{ id: 7, name: 'X1C-Lab', camera_rotation: 0 }] as never);
    vi.spyOn(api, 'getPrinterStatus').mockResolvedValue({ ...PRINTING, hms_errors: [] } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    rtlRender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/camwall']}>
          <ToastProvider>
            <CamWallPage />
          </ToastProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await screen.findByRole('button', { name: 'Pause' });
    expect(screen.getByTestId('camwall-tile-controls').className).not.toContain('opacity-0');
  });
});
