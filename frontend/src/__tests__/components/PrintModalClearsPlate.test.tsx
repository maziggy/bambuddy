/**
 * "Clears plate automatically" in the PrintModal (#3576).
 *
 * The pill sends `clears_plate` explicitly once it is decided (seeded from the
 * single file / archive, or touched by the user), is not wiped by the
 * settings-defaults effect, and is omitted for several files so the backend
 * falls back to each file's own flag.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse, delay } from 'msw';
import type { ComponentProps } from 'react';

import { render as renderPlain } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import { render } from '../utils';
import { ThemeProvider } from '../../contexts/ThemeContext';
import { ToastProvider } from '../../contexts/ToastContext';
import { AuthProvider } from '../../contexts/AuthContext';
import { EmbeddedCameraProvider } from '../../contexts/EmbeddedCameraContext';
import { server } from '../mocks/server';
import { PrintModal } from '../../components/PrintModal';
import type { PrintQueueItem } from '../../api/client';

type Body = Record<string, unknown>;

const printers = [
  { id: 1, name: 'X1 Carbon', model: 'X1C', ip_address: '192.168.1.100', enabled: true, is_active: true },
];

let posted: Body[];
let patched: Body[];

function mockBackend({
  libraryClears = false,
  archiveClears = false,
  settingsDelayMs = 0,
  detailsDelayMs = 0,
  slicedFor = {},
}: { libraryClears?: boolean; archiveClears?: boolean; settingsDelayMs?: number; detailsDelayMs?: number; slicedFor?: Record<number, string> } = {}) {
  server.use(
    http.get('/api/v1/printers/', () => HttpResponse.json(printers)),
    http.get('/api/v1/printers/:id/status', () =>
      HttpResponse.json({ connected: true, state: 'IDLE', ams: [], vt_tray: [] }),
    ),
    http.get('/api/v1/settings/', async () => {
      if (settingsDelayMs) await delay(settingsDelayMs);
      return HttpResponse.json({ default_confirm_outcome: false });
    }),
    http.get('/api/v1/library/files/:id', async ({ params }) => {
      if (detailsDelayMs) await delay(detailsDelayMs);
      return HttpResponse.json({
        id: Number(params.id),
        filename: `f${params.id}.gcode.3mf`,
        file_type: 'gcode.3mf',
        sliced_for_model: slicedFor[Number(params.id)] ?? null,
        // Only the first file (id 5) is flagged.
        clears_plate: libraryClears && Number(params.id) === 5,
      });
    }),
    http.get('/api/v1/library/files/:id/plates', () =>
      HttpResponse.json({ is_multi_plate: false, plates: [] }),
    ),
    http.get('/api/v1/library/files/:id/filament-requirements', () => HttpResponse.json({ filaments: [] })),
    http.get('/api/v1/archives/:id', async () => {
      if (detailsDelayMs) await delay(detailsDelayMs);
      return HttpResponse.json({ id: 1, sliced_for_model: null, clears_plate: archiveClears });
    }),
    http.get('/api/v1/archives/:id/plates', () => HttpResponse.json({ is_multi_plate: false, plates: [] })),
    http.get('/api/v1/archives/:id/filament-requirements', () => HttpResponse.json({ filaments: [] })),
    http.post('/api/v1/queue/', async ({ request }) => {
      posted.push((await request.json()) as Body);
      return HttpResponse.json({ id: posted.length, status: 'pending' });
    }),
    http.patch('/api/v1/queue/:id', async ({ request }) => {
      patched.push((await request.json()) as Body);
      return HttpResponse.json({ id: 1, status: 'pending' });
    }),
  );
}

const pill = () => screen.getByRole('button', { name: 'Clears plate automatically' });
const submit = () => document.querySelector('button[type="submit"]') as HTMLElement;

const queueItem = (overrides: Partial<PrintQueueItem> = {}) =>
  ({
    id: 1, printer_id: 1, archive_id: 1, position: 1, scheduled_time: null, require_previous_success: false,
    auto_off_after: false, gcode_injection: false, manual_start: false, ams_mapping: null, plate_id: null,
    bed_levelling: true, flow_cali: false, vibration_cali: true, layer_inspect: false, timelapse: false,
    use_ams: true, status: 'pending', started_at: null, completed_at: null, error_message: null,
    created_at: '2024-01-01T00:00:00Z', archive_name: 'Test Print', archive_thumbnail: null,
    printer_name: 'X1 Carbon', print_time_seconds: 3600, batch_id: null, batch_name: null,
    clears_plate: false, confirm_outcome: false,
    ...overrides,
  }) as PrintQueueItem;

const renderCreate = (props: Partial<ComponentProps<typeof PrintModal>> = {}) =>
  render(
    <PrintModal
      mode="create"
      archiveName="Benchy"
      initialSelectedPrinterIds={[1]}
      onClose={() => {}}
      {...props}
    />,
  );

describe('PrintModal clears-plate pill (#3576)', () => {
  beforeEach(() => {
    posted = [];
    patched = [];
  });

  it('starts off, toggles, and sends clears_plate=true on create', async () => {
    mockBackend();
    const user = userEvent.setup();
    renderCreate({ archiveId: 1 });

    expect(pill()).toHaveAttribute('aria-pressed', 'false');
    await user.click(pill());
    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(true);
  });

  it('sends an explicit clears_plate=false once the unflagged details are in', async () => {
    mockBackend();
    const user = userEvent.setup();
    renderCreate({ archiveId: 1 });

    await screen.findByRole('button', { name: 'Clears plate automatically' });
    await new Promise((r) => setTimeout(r, 150));
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toHaveProperty('clears_plate', false);
  });

  it('omits clears_plate when submitted before the details loaded', async () => {
    mockBackend({ libraryClears: true, detailsDelayMs: 600 });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    await screen.findByRole('button', { name: 'Clears plate automatically' });
    await waitFor(() => expect(submit()).toBeEnabled());
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).not.toHaveProperty('clears_plate');
  });

  it('keeps the user choice made before the details load', async () => {
    mockBackend({ libraryClears: true, detailsDelayMs: 300 });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    // Turn it on, then off, before the flagged file arrives.
    await user.click(await screen.findByRole('button', { name: 'Clears plate automatically' }));
    await user.click(pill());
    await new Promise((r) => setTimeout(r, 600));
    expect(pill()).toHaveAttribute('aria-pressed', 'false');
  });

  it('is prefilled from a flagged library file and can be switched off', async () => {
    mockBackend({ libraryClears: true });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    await user.click(pill());
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(false);
  });

  it('sends clears_plate=true for a flagged library file left as is', async () => {
    mockBackend({ libraryClears: true });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(true);
  });

  it('is prefilled from a flagged archive (reprint)', async () => {
    mockBackend({ archiveClears: true });
    const user = userEvent.setup();
    renderCreate({ archiveId: 1 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(true);
  });

  it('stays off for an unflagged library file', async () => {
    mockBackend({ libraryClears: false });
    renderCreate({ libraryFileId: 5 });

    await screen.findByRole('button', { name: 'Clears plate automatically' });
    await new Promise((r) => setTimeout(r, 50));
    expect(pill()).toHaveAttribute('aria-pressed', 'false');
  });

  it('is not reset when the settings load after the file details', async () => {
    mockBackend({ libraryClears: true, settingsDelayMs: 300 });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    // Let the delayed settings response arrive and run the defaults effect.
    await new Promise((r) => setTimeout(r, 500));
    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(true);
  });

  it('is not reset when the settings load before the file details', async () => {
    mockBackend({ archiveClears: true });
    const user = userEvent.setup();
    renderCreate({ archiveId: 1 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate).toBe(true);
  });

  it('does not re-seed after the user switched it off', async () => {
    mockBackend({ libraryClears: true });
    const user = userEvent.setup();
    renderCreate({ libraryFileId: 5 });

    await waitFor(() => expect(pill()).toHaveAttribute('aria-pressed', 'true'));
    await user.click(pill());
    await new Promise((r) => setTimeout(r, 300));
    expect(pill()).toHaveAttribute('aria-pressed', 'false');
  });

  const filesOf = (modelB: string) => [
    { id: 5, filename: 'a.gcode.3mf', sliced_for_model: 'X1C' },
    { id: 6, filename: 'b.gcode.3mf', sliced_for_model: modelB },
  ];
  const printButton = () => screen.getByRole('button', { name: /^Print$/i });

  it('hides the pill and omits clears_plate across cross-model variants, even if the first file is flagged', async () => {
    mockBackend({ libraryClears: true, slicedFor: { 5: 'X1C', 6: 'P1S' } });
    const user = userEvent.setup();
    renderCreate({
      libraryFileId: 5,
      variantFiles: filesOf('P1S') as never,
      initialSelectedPrinterIds: undefined,
    });

    await screen.findByText('Printer alternatives');
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.queryByRole('button', { name: 'Clears plate automatically' })).not.toBeInTheDocument();
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).not.toHaveProperty('clears_plate');
  });

  it('omits clears_plate from every separate job when the files have mixed flags', async () => {
    mockBackend({ libraryClears: true, slicedFor: { 5: 'X1C', 6: 'X1C' } });
    const user = userEvent.setup();
    renderCreate({
      libraryFileId: 5,
      variantFiles: filesOf('X1C') as never,
      separateJobsAllowed: true,
      initialSelectedPrinterIds: undefined,
    });

    await screen.findByRole('radio', { name: 'Separate jobs' });
    await new Promise((r) => setTimeout(r, 200));
    expect(screen.queryByRole('button', { name: 'Clears plate automatically' })).not.toBeInTheDocument();
    await user.click(await screen.findByText('X1 Carbon'));
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(2));
    for (const body of posted) expect(body).not.toHaveProperty('clears_plate');
  });
});

describe('PrintModal per-alternative clears plate (#3576)', () => {
  beforeEach(() => {
    posted = [];
    patched = [];
  });

  const files = [
    { id: 5, filename: 'a1.gcode.3mf', sliced_for_model: 'A1', clears_plate: true },
    { id: 6, filename: 'p1s.gcode.3mf', sliced_for_model: 'P1S', clears_plate: false },
  ];
  const printButton = () => screen.getByRole('button', { name: /^Print$/i });
  const rowBox = (i: number) => screen.getAllByRole('checkbox', { name: /Clears plate/ })[i] as HTMLInputElement;
  const open = () =>
    renderCreate({ libraryFileId: 5, variantFiles: files as never, initialSelectedPrinterIds: undefined });

  it('prefills each row from its own file', async () => {
    mockBackend({ slicedFor: { 5: 'A1', 6: 'P1S' } });
    open();
    await screen.findByText('Printer alternatives');
    expect(screen.getAllByRole('checkbox', { name: /Clears plate/ })).toHaveLength(2);
    expect(rowBox(0)).toBeChecked();
    expect(rowBox(1)).not.toBeChecked();
  });

  it('sends the displayed clears_plate for every row', async () => {
    mockBackend({ slicedFor: { 5: 'A1', 6: 'P1S' } });
    const user = userEvent.setup();
    open();
    await screen.findByText('Printer alternatives');
    await user.click(rowBox(1));
    expect(rowBox(1)).toBeChecked();
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    const variants = posted[0].variants as Body[];
    expect(variants[0]).toMatchObject({ library_file_id: 5, clears_plate: true });
    expect(variants[1]).toMatchObject({ library_file_id: 6, clears_plate: true });
    expect(posted[0]).not.toHaveProperty('clears_plate');
  });

  it('sends every row\'s prefilled value explicitly when none was toggled', async () => {
    mockBackend({ slicedFor: { 5: 'A1', 6: 'P1S' } });
    const user = userEvent.setup();
    open();
    await screen.findByText('Printer alternatives');
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    const variants = posted[0].variants as Body[];
    expect(variants[0]).toMatchObject({ clears_plate: true });
    expect(variants[1]).toMatchObject({ clears_plate: false });
  });

  it('sends an explicit false when a flagged row is switched off', async () => {
    mockBackend({ slicedFor: { 5: 'A1', 6: 'P1S' } });
    const user = userEvent.setup();
    open();
    await screen.findByText('Printer alternatives');
    await user.click(rowBox(0));
    await waitFor(() => expect(printButton()).toBeEnabled());
    await user.click(printButton());

    await waitFor(() => expect(posted).toHaveLength(1));
    const variants = posted[0].variants as Body[];
    expect(variants[0]).toMatchObject({ clears_plate: false });
    expect(variants[1]).toMatchObject({ clears_plate: false });
  });
});

describe('PrintModal clears-plate pill in edit mode (#3576)', () => {
  beforeEach(() => {
    posted = [];
    patched = [];
  });

  const renderEdit = (item: PrintQueueItem) =>
    render(
      <PrintModal
        mode="edit-queue-item"
        archiveId={1}
        archiveName="Test Print"
        queueItem={item}
        onClose={() => {}}
      />,
    );

  it('is prefilled from the queue item and saved unchanged', async () => {
    // The archive is not flagged: the item's own value wins in edit mode.
    mockBackend({ archiveClears: false });
    const user = userEvent.setup();
    renderEdit(queueItem({ clears_plate: true }));

    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await new Promise((r) => setTimeout(r, 100));
    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].clears_plate).toBe(true);
  });

  it('saves a switched-off value as an explicit false', async () => {
    mockBackend();
    const user = userEvent.setup();
    renderEdit(queueItem({ clears_plate: true }));

    await user.click(pill());
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].clears_plate).toBe(false);
  });

  it('saves a switched-on value', async () => {
    mockBackend();
    const user = userEvent.setup();
    renderEdit(queueItem({ clears_plate: false }));

    expect(pill()).toHaveAttribute('aria-pressed', 'false');
    await user.click(pill());
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0].clears_plate).toBe(true);
  });

  it('hides the pill and never sends clears_plate for an alternatives item', async () => {
    mockBackend();
    const user = userEvent.setup();
    renderEdit(
      queueItem({
        clears_plate: true,
        variants: [
          { library_file_id: 5, filename: 'a1.gcode.3mf', target_model: 'A1', clears_plate: true },
          { library_file_id: 6, filename: 'p1s.gcode.3mf', target_model: 'P1S', clears_plate: false },
        ],
      } as never),
    );
    await new Promise((r) => setTimeout(r, 100));
    expect(screen.queryByRole('button', { name: /Clears plate/i })).toBeNull();
    await user.click(screen.getByRole('button', { name: /save/i }));

    await waitFor(() => expect(patched).toHaveLength(1));
    expect(patched[0]).not.toHaveProperty('clears_plate');
  });

  it('is not overwritten by a flagged archive in edit mode', async () => {
    mockBackend({ archiveClears: true });
    renderEdit(queueItem({ clears_plate: false }));

    await new Promise((r) => setTimeout(r, 200));
    expect(pill()).toHaveAttribute('aria-pressed', 'false');
  });
});

// The app's QueryClient serves cached data for 60 s without a fetch; a file's
// details already in the cache (Reprint from the archive modal, a file just
// viewed) must still show and send the file's flag (#3576).
describe('PrintModal clears-plate pill with fresh cached details (#3576)', () => {
  beforeEach(() => {
    posted = [];
    patched = [];
  });

  const renderCached = (key: unknown[], data: Record<string, unknown>, props: Partial<ComponentProps<typeof PrintModal>>) => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 60_000 }, mutations: { retry: false } },
    });
    client.setQueryData(key, data);
    return renderPlain(
      <QueryClientProvider client={client}>
        <BrowserRouter>
          <AuthProvider>
            <ThemeProvider>
              <ToastProvider>
                <EmbeddedCameraProvider>
                  <PrintModal
                    mode="create"
                    archiveName="Benchy"
                    initialSelectedPrinterIds={[1]}
                    onClose={() => {}}
                    {...props}
                  />
                </EmbeddedCameraProvider>
              </ToastProvider>
            </ThemeProvider>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>,
    );
  };

  it('shows a flagged cached archive as on and one click sends explicit false', async () => {
    mockBackend({ archiveClears: true });
    const user = userEvent.setup();
    renderCached(['archive', 1], { id: 1, sliced_for_model: null, clears_plate: true }, { archiveId: 1 });

    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await user.click(pill());
    expect(pill()).toHaveAttribute('aria-pressed', 'false');
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toHaveProperty('clears_plate', false);
  });

  it('sends true for a flagged cached library file left as is', async () => {
    mockBackend({ libraryClears: true });
    const user = userEvent.setup();
    renderCached(
      ['library-file', 5],
      { id: 5, filename: 'f5.gcode.3mf', file_type: 'gcode.3mf', sliced_for_model: null, clears_plate: true },
      { libraryFileId: 5 },
    );

    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await waitFor(() => expect(submit()).toBeEnabled());
    await new Promise((r) => setTimeout(r, 200));
    expect(pill()).toHaveAttribute('aria-pressed', 'true');
    await user.click(submit());

    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].clears_plate ?? true).toBe(true);
  });
});
